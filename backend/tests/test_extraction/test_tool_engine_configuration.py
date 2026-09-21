"""The new online domain freezes Responses capabilities and explicit local tools."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services.document_analysis import execution
from app.services.extraction.ontology_guided.claim_protocol import ExtractionProfile
from app.services.extraction.ontology_guided.contracts import (
    MetadataSnapshot,
    OntologyClassDefinition,
    OntologySnapshot,
)
from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
from app.services.extraction.ontology_guided.record_discovery import RECORD_PIPELINE
from app.services.extraction.ontology_guided.tool_model_adapter import ToolModelRecognitionAdapter
from app.services.extraction.ontology_guided.tool_runtime import ToolLimits

pytest_plugins = ["tests.test_extraction.test_tool_engine_freeze"]


@pytest.fixture
def source(tool_source):
    return tool_source


def test_new_factory_uses_frozen_config_without_legacy_domain_policies(source, monkeypatch):
    from app.services.llm import local_client

    monkeypatch.setattr(settings, "local_llm_model", "qwen-configured")
    monkeypatch.setattr(settings, "local_llm_model_revision", "fixed-model-revision")
    monkeypatch.setattr(settings, "evidence_max_output_tokens", 20480)
    options = {"responses": {"strict_tools": False, "strict_answers": False,
                             "reasoning": {"effort": "none"}}}
    monkeypatch.setattr(settings, "ontology_extraction_options", options)
    monkeypatch.setattr(local_client, "get_local_llm", lambda: object())
    frozen = execution.freeze_tool_engine_policy()
    options["responses"]["strict_tools"] = True
    options["responses"]["reasoning"]["effort"] = "high"
    assert frozen["responses"]["strict_tools"] is False
    assert frozen["max_lineage_calls"] == 6
    assert "cmc_describes_type_scope" not in frozen
    assert frozen["recognition_pipeline"] == RECORD_PIPELINE
    assert frozen["record_discovery"] == {
        "version": "record-discovery-v2", "max_classes_per_card": 4, "endpoint_page_size": 8,
        "graph_phase": "evidence_review",
        "candidate_cards_per_record": 2, "minimum_similarity": 0.25,
        "max_feedback_reopens": 1,
        "attribute_calibration": "source-observations-v1",
        "table_reading": "bounded-table-rows-v2",
        "contextual": {
            "version": "contextual-discovery-v2", "max_section_chars": 240,
            "max_group_chars": 1200, "max_group_records": 8, "max_table_rows_per_group": 4,
            "max_attribute_candidates": 8, "max_disambiguation_attempts": 2,
        },
            "schema_region_routing": {
                "version": "schema-region-routing-v1", "max_regions_per_card": 2,
                "minimum_similarity": 0.25, "max_region_records": 32,
                "max_group_chars": 1200, "max_group_records": 8,
                "execution_mode": "region_batch", "property_field_mode": "region_batch",
            },
    }
    assert frozen["candidate_planning"] == "sparse-candidates-v1"
    assert frozen["heuristic_policy"]["query_rules_version"] == "ontology-controlled-labels-v1"
    frozen_budget = dict(frozen["request_budget"])
    monkeypatch.setattr(settings, "evidence_max_input_tokens", 999)
    assert not {"evidence_repair", "source_object_recognition"} & frozen.keys()
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
    assert adapter.chat_template_kwargs == {"enable_thinking": False}
    assert frozen["responses"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert frozen["request_budget"]["stage_output_tokens"] == {
        "discovery": min(8192, frozen_budget["max_output_tokens"]),
        "verification": 16384,
    }
    assert adapter.instance_reader is adapter.mention_extractor is None
    assert adapter.max_input_tokens == frozen_budget["max_input_tokens"]
    assert adapter.max_output_tokens == frozen_budget["max_output_tokens"]
    assert adapter.stage_output_tokens == frozen_budget["stage_output_tokens"]
    assert adapter.tool_limits.max_result_tokens == min(8192, frozen_budget["max_input_tokens"])
    assert adapter.index.ir.document_hash == ir.document_hash
    assert adapter.recognition_batching.max_members == 4
    assert adapter.recognition_pipeline == RECORD_PIPELINE
    assert adapter.record_discovery.max_classes_per_card == 4
    assert adapter.record_discovery.endpoint_page_size == 8
    assert adapter.record_discovery.contextual.max_disambiguation_attempts == 2
    assert adapter.record_discovery.candidate_cards_per_record == 2
    assert adapter.record_discovery.minimum_similarity == 0.25
    assert adapter.record_discovery.schema_region_routing.max_group_chars == 1200
    assert adapter.record_discovery.schema_region_routing.max_group_records == 8
    assert adapter.record_discovery.schema_region_routing.execution_mode == "region_batch"
    assert adapter.record_discovery.schema_region_routing.property_field_mode == "region_batch"
    executor = OntologyGuidedExecutor(
        ontology=ontology, engine=None, adapter=adapter, current_state=True,
        candidate_policy="sparse-candidates-v1",
        incremental_performance=True,
        heuristic_policy=execution.HeuristicSearchPolicy.generic(),
    )
    assert not hasattr(executor, "cmc_describes_type_scope")
    old_record = deepcopy(frozen)
    old_record["record_discovery"].pop("contextual")
    old_record_adapter = execution._configured_recognition_adapter(
        old_record, ir=ir, ontology=ontology, metadata=metadata,
    )
    assert old_record_adapter.record_discovery.contextual is None
    assert old_record_adapter.record_discovery.model_dump(mode="json") == (
        old_record["record_discovery"]
    )
    # A changed default cannot upgrade a saved single-task run during restoration.
    legacy = deepcopy(frozen)
    legacy.pop("recognition_pipeline")
    legacy.pop("record_discovery")
    legacy_batch_adapter = execution._configured_recognition_adapter(
        legacy, ir=ir, ontology=ontology, metadata=metadata,
    )
    assert legacy_batch_adapter.recognition_batching.max_members == 4
    assert legacy_batch_adapter.recognition_pipeline is None
    legacy.pop("recognition_batching")
    legacy["model_call_state_version"] = 2
    legacy_adapter = execution._configured_recognition_adapter(
        legacy, ir=ir, ontology=ontology, metadata=metadata,
    )
    assert legacy_adapter.recognition_batching is None
    assert legacy_adapter.recognition_pipeline is None
    assert legacy_adapter.record_discovery is None
    monkeypatch.setattr(settings, "local_llm_model_revision", "changed-revision")
    with pytest.raises(execution.CheckpointMismatch):
        execution._configured_recognition_adapter(
            frozen, ir=ir, ontology=ontology, metadata=metadata,
        )


def test_new_record_run_does_not_freeze_boundary_model(monkeypatch):
    monkeypatch.setattr(settings, "ontology_extraction_options", {
        "gliner2": {"model_path": "/unused", "device": "cpu", "manifest": {}},
    })
    frozen = execution.freeze_tool_engine_policy()
    assert frozen["recognition_pipeline"] == RECORD_PIPELINE
    assert "gliner2" not in frozen["extraction_options"]


def test_record_adapter_rejects_boundary_ner_injection():
    with pytest.raises(
        ValueError, match="^record_pipeline_does_not_support_mention_extractor$"
    ):
        ToolModelRecognitionAdapter(
            object(), index=object(), ontology=object(), metadata=object(),
            profile=ExtractionProfile(), token_counter=len, model_identity="qwen-test",
            max_input_tokens=1000, max_output_tokens=1000,
            tool_limits=ToolLimits(max_result_tokens=1000),
            recognition_pipeline=RECORD_PIPELINE, mention_extractor=object(),
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
    {"responses": {"chat_template_kwargs": {"enable_thinking": "no"}}},
    {"responses": {"chat_template_kwargs": {"enable_thinking": False, "other": 1}}},
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


@pytest.mark.parametrize("pipeline,policy", [
    ("unknown", {"version": "record-discovery-v2", "max_classes_per_card": 4}),
    ([], {}), (RECORD_PIPELINE, None),
    (RECORD_PIPELINE, {"version": "record-discovery-v2", "max_classes_per_card": 0}),
    (None, {"version": "record-discovery-v2", "max_classes_per_card": 4}),
])
def test_invalid_record_policy_stops_before_loading_model(monkeypatch, pipeline, policy):
    from app.services.llm import local_client

    def no_client():
        pytest.fail("invalid record pipeline must not acquire a model client")

    monkeypatch.setattr(settings, "ontology_extraction_options", {})
    monkeypatch.setattr(local_client, "get_local_llm", no_client)
    frozen = execution.freeze_tool_engine_policy()
    frozen.update(recognition_pipeline=pipeline, record_discovery=policy)
    with pytest.raises(execution.CheckpointMismatch, match="invalid frozen record discovery"):
        execution._configured_recognition_adapter(frozen)



def test_fingerprint_keeps_old_executor_identity_and_freezes_new_pipeline(source, monkeypatch):
    captured = []
    monkeypatch.setattr(
        execution, "evidence_hash", lambda value: captured.append(value) or "digest",
    )
    run = SimpleNamespace(
        document_hash="d" * 64, root_class_iri="urn:Source", metadata_mode="structure_only",
        scope_mode="document_graph", focus_path=[],
    )
    metadata = SimpleNamespace(dependency_hash="metadata")
    ontology = SimpleNamespace(ontology_hash="ontology", version="v1")
    legacy = {"state_storage_version": 4}
    execution._recognition_fingerprint(
        run, source["index"].ir, metadata, ontology, None, performance=legacy,
    )
    execution._recognition_fingerprint(
        run, source["index"].ir, metadata, ontology, None,
        performance={**legacy, "recognition_pipeline": RECORD_PIPELINE},
    )
    assert captured[0]["protocol"]["executor"] == OntologyGuidedExecutor.version
    assert captured[1]["protocol"]["executor"] == (
        OntologyGuidedExecutor.version + "+" + RECORD_PIPELINE
    )
    assert legacy == {"state_storage_version": 4}
