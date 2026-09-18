"""The new online domain freezes Responses capabilities and explicit local tools."""

import pytest

from app.config import settings
from app.services.document_analysis import execution
from app.services.extraction.ontology_guided.contracts import (
    MetadataSnapshot,
    OntologyClassDefinition,
    OntologySnapshot,
)
from app.services.extraction.ontology_guided.tool_model_adapter import ToolModelRecognitionAdapter

pytest_plugins = ["tests.test_extraction.test_tool_engine_freeze"]


def test_new_factory_uses_frozen_config_without_legacy_domain_policies(source, monkeypatch):
    from app.services.llm import local_client

    monkeypatch.setattr(settings, "local_llm_model", "qwen-configured")
    monkeypatch.setattr(settings, "local_llm_model_revision", "fixed-model-revision")
    options = {"responses": {"strict_tools": False, "strict_answers": False,
                             "reasoning": {"effort": "none"}}}
    monkeypatch.setattr(settings, "ontology_extraction_options", options)
    monkeypatch.setattr(local_client, "get_local_llm", lambda: object())
    frozen = execution.freeze_tool_engine_policy()
    options["responses"]["strict_tools"] = True
    options["responses"]["reasoning"]["effort"] = "high"
    assert frozen["responses"]["strict_tools"] is False
    assert frozen["max_lineage_calls"] == 4
    assert frozen["candidate_planning"] == "sparse-candidates-v1"
    assert frozen["heuristic_policy"]["query_rules_version"] == "ontology-controlled-labels-v1"
    frozen_budget = dict(frozen["request_budget"])
    monkeypatch.setattr(settings, "evidence_max_input_tokens", 999)
    assert not {"evidence_repair", "cmc_describes_type_scope",
                "source_object_recognition"} & frozen.keys()
    ir = source["index"].ir
    ontology = OntologySnapshot(
        snapshot_id="ontology", ontology_hash="a" * 64,
        classes={"urn:Source": OntologyClassDefinition(
            iri="urn:Source", label="Source", source_hash="b" * 64,
            declared_relationships=[source["predicate"]],
        )},
    )
    metadata = MetadataSnapshot(
        snapshot_id="metadata", analysis_id=ir.analysis_id,
        document_hash=ir.document_hash, structure_hash=ir.structure_hash,
        summary_version="v1", generation_source="structure_only", dependency_hash="c" * 64,
    )
    adapter = execution._configured_recognition_adapter(
        frozen, ir=ir, ontology=ontology, metadata=metadata,
    )
    assert isinstance(adapter, ToolModelRecognitionAdapter)
    assert adapter.model_identity == "qwen-configured"
    assert not adapter.strict_tools and not adapter.strict_answers and adapter.include is None
    assert adapter.reasoning == {"effort": "none"}
    assert adapter.instance_reader is adapter.mention_extractor is None
    assert adapter.max_input_tokens == frozen_budget["max_input_tokens"]
    assert adapter.max_output_tokens == frozen_budget["max_output_tokens"]
    assert adapter.index.ir.document_hash == ir.document_hash
    monkeypatch.setattr(settings, "local_llm_model_revision", "changed-revision")
    with pytest.raises(execution.CheckpointMismatch):
        execution._configured_recognition_adapter(
            frozen, ir=ir, ontology=ontology, metadata=metadata,
        )


def test_required_qwen_absence_is_not_success_or_chat_fallback(monkeypatch):
    from app.services.llm import local_client

    monkeypatch.setattr(settings, "ontology_extraction_options", {})
    monkeypatch.setattr(local_client, "get_local_llm", lambda: None)
    with pytest.raises(RuntimeError, match="required_qwen_responses_unavailable"):
        execution._configured_recognition_adapter(execution.freeze_tool_engine_policy())


@pytest.mark.parametrize("options", [
    {"domain_heuristics": True}, {"responses": {"strict_tools": "yes"}},
    {"responses": {"include": ["unknown-field"]}},
    {"responses": {"reasoning": {"effort": "invented"}}},
    {"responses": {"reasoning": {"effort": "none", "budget": 100}}},
    {"responses": {"reasoning": "none"}},
])
def test_unknown_or_ambiguous_capabilities_cannot_be_frozen(monkeypatch, options):
    monkeypatch.setattr(settings, "ontology_extraction_options", options)
    with pytest.raises(ValueError):
        execution.freeze_tool_engine_policy()


@pytest.mark.parametrize("version", [True, False, 1.0, "1", 0, 2, [], {}])
def test_invalid_reference_policy_stops_before_loading_model(monkeypatch, version):
    from app.services.llm import local_client

    def model_must_not_be_loaded():
        pytest.fail("invalid frozen reference policy must not acquire a model client")

    monkeypatch.setattr(settings, "ontology_extraction_options", {})
    monkeypatch.setattr(local_client, "get_local_llm", model_must_not_be_loaded)
    frozen = execution.freeze_tool_engine_policy()
    frozen["reference_resolution_version"] = version
    with pytest.raises(execution.CheckpointMismatch,
                       match="^tool engine frozen configuration mismatch$"):
        execution._configured_recognition_adapter(frozen)
