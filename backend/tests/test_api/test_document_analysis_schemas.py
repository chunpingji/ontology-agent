"""Pure contract tests for the public document-analysis runs v1 schemas."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas import document_analysis as schema

RUN_ID = "a4f9885d-1b70-4718-9a97-2eb03c3ef8c4"
ROOT_IRI = "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport"
PRODUCT_IRI = "https://ontology.pharma-gmp.cn/slpra/drug-development/DrugProduct"
API_IRI = "https://ontology.pharma-gmp.cn/slpra/drug-development/API"
DESCRIBES_IRI = "https://ontology.pharma-gmp.cn/slpra/drug-development/describes"
HAS_API_IRI = "https://ontology.pharma-gmp.cn/slpra/drug-development/hasAPI"
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def _progress(*, event_head: int = 35, artifact_revision: int = 4) -> dict:
    return {
        "tasks_attempted": 12,
        "model_calls": 20,
        "records_planned": 117,
        "records_examined": 8,
        "records_incomplete": 1,
        "records_unattempted": 108,
        "phase_counts": {"phase1": 8, "phase2": 0},
        "decisions": {
            "supported": 3,
            "unsupported": 4,
            "undetermined": 2,
            "not_checked": 1,
        },
        "pending_frontiers": 5,
        "stop_reason": None,
        "contract_version": schema.CONTRACT_VERSION,
        "event_head": event_head,
        "artifact_revision": artifact_revision,
    }


def _run_response() -> dict:
    return {
        "contract_version": schema.CONTRACT_VERSION,
        "recognition_run_id": RUN_ID,
        "run_revision": 8,
        "event_head": 35,
        "artifact_revision": 4,
        "status": "running",
        "stage": "extracting",
        "input": {
            "filename": "CMCReport.docx",
            "root_class_iri": ROOT_IRI,
            "root_class_label": "CMC 报告",
            "metadata_mode": "generate_summary",
            "scope_mode": "document_graph",
        },
        "identities": {
            "analysis_id": "analysis:one",
            "ontology_snapshot_id": "ontology-snapshot:one",
            "metadata_snapshot_id": "metadata-snapshot:one",
            "graph_snapshot_id": "graph-snapshot:one",
            "fingerprint_status": "frozen",
        },
        "artifacts": {
            "source": "ready",
            "structure": "ready",
            "metadata": "ready",
            "graph": "partial",
        },
        "progress": _progress(),
        "error": None,
        "available_actions": ["pause", "cancel"],
        "created_at": NOW,
        "started_at": NOW,
        "paused_at": None,
        "finished_at": None,
        "expires_at": None,
    }


def _snapshot_header() -> dict:
    return {
        "snapshot_id": "graph-snapshot:one",
        "analysis_id": "analysis:one",
        "metadata_snapshot_id": "metadata-snapshot:one",
        "ontology_snapshot_id": "ontology-snapshot:one",
        "root_ref": {"entity_id": "entity:root", "revision": 1},
        "projection_policy": "proof-gate-v1",
        "generated_at": NOW,
    }


def _source_roles() -> dict:
    return {
        "subject": ["selection:subject"],
        "object": ["selection:object"],
        "value": [],
        "predicate_bridge": ["selection:bridge"],
        "condition": [],
        "counterevidence": [],
    }


def _relationship() -> dict:
    return {
        "candidate_id": "candidate:describes",
        "revision": 2,
        "subject_ref": {"entity_id": "entity:root", "revision": 1},
        "object_ref": {"entity_id": "entity:product", "revision": 2},
        "predicate_iri": DESCRIBES_IRI,
        "predicate_label": "描述",
        "direction": "subject_to_object",
        "polarity": "affirmed",
        "conditions": [],
        "applicability": {},
        "structural_valid": True,
        "model_supported": True,
        "policy_eligible": True,
        "independent_review": "unreviewed",
        "proof_ref": {"id": "proof:describes", "revision": 4},
        "decision_refs": [{"id": "decision:describes", "revision": 3}],
        "dependency_refs": [{"id": "entity:product", "revision": 2}],
        "invalidated": False,
        "reason_code": None,
        "reason": None,
        "source_selection_refs": _source_roles(),
    }


def _property() -> dict:
    roles = _source_roles()
    roles["object"] = []
    roles["value"] = ["selection:value"]
    return {
        "candidate_id": "candidate:name",
        "revision": 7,
        "subject_ref": {"entity_id": "entity:product", "revision": 2},
        "predicate_iri": "https://ontology.example/name",
        "predicate_label": "名称",
        "direction": "subject_to_value",
        "raw_value": "产品 A",
        "normalized_value": "产品 A",
        "datatype_iri": "http://www.w3.org/2001/XMLSchema#string",
        "unit": None,
        "polarity": "affirmed",
        "conditions": [],
        "applicability": {},
        "structural_valid": True,
        "model_supported": True,
        "policy_eligible": True,
        "independent_review": "unreviewed",
        "proof_ref": {"id": "proof:name", "revision": 9},
        "decision_refs": [{"id": "decision:name", "revision": 6}],
        "dependency_refs": [{"id": "entity:product", "revision": 2}],
        "invalidated": False,
        "reason_code": None,
        "reason": None,
        "source_selection_refs": roles,
    }


def _graph_response(*, projection: str = "effective_affirmed") -> dict:
    return {
        "contract_version": schema.CONTRACT_VERSION,
        "recognition_run_id": RUN_ID,
        "run_revision": 8,
        "event_head": 35,
        "artifact_revision": 4,
        "availability": "partial",
        "projection": projection,
        "graph_snapshot": _snapshot_header(),
        "entities": [
            {
                "entity_id": "entity:root",
                "revision": 1,
                "class_iri": ROOT_IRI,
                "class_label": "CMC 报告",
                "label": "CMCReport.docx",
                "seed_origin": "user_selected",
                "identity_state": "document_local",
                "independent_review": "unreviewed",
                "source_selection_refs": [],
            },
            {
                "entity_id": "entity:product",
                "revision": 2,
                "class_iri": PRODUCT_IRI,
                "class_label": "制剂",
                "label": "产品 A",
                "seed_origin": "recognized",
                "identity_state": "document_local",
                "independent_review": "unreviewed",
                "source_selection_refs": ["selection:product"],
            },
        ],
        "properties": [_property()],
        "relationships": [_relationship()],
        "invalidated_refs": [],
        "coverage": {
            "subjects": [],
            "records_planned": 117,
            "records_examined": 8,
            "records_incomplete": 1,
            "records_unattempted": 108,
            "phase2_started": False,
            "pending_frontiers": 5,
            "stop_reason": None,
        },
        "unresolved": {
            "unsupported": 4,
            "undetermined": 2,
            "not_checked": 1,
            "unassociated_entities": 0,
        },
        "error": None,
    }


def _anchor(**overrides) -> dict:
    values = {
        "document_hash": "a" * 64,
        "parser_version": "2",
        "structure_hash": "b" * 64,
        "evidence_id": "paragraph:1:0",
        "section_node_id": "section:1",
        "block_id": "paragraph:1:0",
        "paragraph_index": 1,
        "span_start": 0,
        "span_end": 4,
    }
    return values | overrides


def test_create_form_and_accepted_response_are_contract_exact():
    request = schema.CreateRunRequest(
        root_class_iri=ROOT_IRI,
        request_key="browser-request-1",
    )
    assert request.metadata_mode == "generate_summary"

    response = schema.CreateRunResponse(
        recognition_run_id=RUN_ID,
        input={
            "filename": "CMCReport.docx",
            "root_class_iri": ROOT_IRI,
            "root_class_label": "CMC 报告",
            "metadata_mode": "generate_summary",
        },
        created_at=NOW,
        links={
            "self": f"/api/document-analysis/runs/{RUN_ID}",
            "metadata": f"/api/document-analysis/runs/{RUN_ID}/metadata",
            "graph": f"/api/document-analysis/runs/{RUN_ID}/graph",
            "source": f"/api/document-analysis/runs/{RUN_ID}/source",
            "events": f"/api/document-analysis/runs/{RUN_ID}/events",
        },
    )
    payload = response.model_dump(mode="json")
    assert payload["status"] == "queued"
    assert payload["stage"] == "accepted"
    assert payload["run_revision"] == payload["event_head"] == 1
    assert payload["artifact_revision"] == 1
    assert "execution_token" not in payload
    assert "checkpoint" not in payload


@pytest.mark.parametrize(
    "invalid",
    [
        {"root_class_iri": "CMCReport", "request_key": "key"},
        {"root_class_iri": ROOT_IRI, "request_key": ""},
        {"root_class_iri": ROOT_IRI, "request_key": "key", "scope_mode": "focus_path"},
        {
            "root_class_iri": ROOT_IRI,
            "request_key": "key",
            "metadata_mode": "legacy_summary",
        },
    ],
)
def test_create_form_rejects_invalid_or_non_public_fields(invalid):
    with pytest.raises(ValidationError):
        schema.CreateRunRequest.model_validate(invalid)


def test_status_response_uses_public_names_and_closed_watermarks():
    response = schema.DocumentAnalysisRunResponse.model_validate(_run_response())
    restored = schema.DocumentAnalysisRunResponse.model_validate_json(response.model_dump_json())
    payload = restored.model_dump(mode="json")

    assert payload["status"] == "running"
    assert payload["run_revision"] == 8
    assert payload["available_actions"] == ["pause", "cancel"]
    assert "revision" not in payload
    assert "execution_status" not in payload
    assert "allowed_actions" not in payload
    assert "owner_id" not in payload

    mismatch = _run_response()
    mismatch["progress"]["event_head"] = 34
    with pytest.raises(ValidationError, match="progress.event_head"):
        schema.DocumentAnalysisRunResponse.model_validate(mismatch)


def test_status_rejects_old_status_stage_and_duplicate_actions():
    for field, value in (
        ("status", "pausing"),
        ("status", "failed"),
        ("stage", "recognition"),
    ):
        payload = _run_response()
        payload[field] = value
        with pytest.raises(ValidationError):
            schema.DocumentAnalysisRunResponse.model_validate(payload)

    payload = _run_response()
    payload["available_actions"] = ["cancel", "cancel"]
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        schema.DocumentAnalysisRunResponse.model_validate(payload)


def test_metadata_pending_and_ready_shapes_are_distinct():
    pending = schema.MetadataArtifactResponse(
        recognition_run_id=RUN_ID,
        run_revision=2,
        event_head=3,
        artifact_revision=0,
        availability="pending",
        stage="parsing",
        retry_after_ms=1000,
    )
    assert pending.metadata is None
    assert pending.analysis is None

    ready = schema.MetadataArtifactResponse(
        recognition_run_id=RUN_ID,
        run_revision=6,
        event_head=20,
        artifact_revision=2,
        availability="ready",
        stage="extracting",
        analysis={
            "analysis_id": "analysis:one",
            "document_hash": "a" * 64,
            "structure_hash": "b" * 64,
            "parser_version": "2",
            "structure_policy_version": "word-structure-v1",
        },
        metadata_snapshot={
            "snapshot_id": "metadata-snapshot:one",
            "generation_source": "model_summary",
            "summary_version": "word-tree-summary-v1",
            "summary_model_identity": "local-model@sha256:one",
            "dependency_hash": "c" * 64,
            "frozen": True,
        },
        filename="CMCReport.docx",
        content={"type": "doc", "content": []},
        section_tree={"node_id": "document", "children": []},
        pagination={
            "mode": "single_page_fallback",
            "physical_page_numbers_available": False,
            "is_estimated": True,
            "warning": "Word 未保存可靠分页标记",
        },
    )
    assert ready.metadata_snapshot is not None
    assert ready.metadata_snapshot.frozen is True

    invalid = ready.model_dump(mode="python")
    invalid["metadata_snapshot"] = None
    with pytest.raises(ValidationError, match="frozen metadata snapshot"):
        schema.MetadataArtifactResponse.model_validate(invalid)


def test_graph_response_is_flat_and_does_not_expose_internal_snapshot():
    response = schema.GraphArtifactResponse.model_validate(_graph_response())
    payload = response.model_dump(mode="json")

    assert set(("entities", "properties", "relationships", "coverage")) <= set(payload)
    assert "snapshot" not in payload
    assert "nodes" not in payload
    assert "edges" not in payload
    assert set(payload["graph_snapshot"]) == {
        "snapshot_id",
        "analysis_id",
        "metadata_snapshot_id",
        "ontology_snapshot_id",
        "root_ref",
        "projection_policy",
        "generated_at",
    }
    assert "nodes" not in payload["graph_snapshot"]
    assert payload["graph_snapshot"]["root_ref"] == {
        "entity_id": "entity:root",
        "revision": 1,
    }
    assert payload["entities"][1]["source_selection_refs"] == ["selection:product"]
    assert payload["relationships"][0]["source_selection_refs"] == _source_roles()
    assert payload["relationships"][0]["proof_ref"] == {
        "id": "proof:describes",
        "revision": 4,
    }
    assert payload["relationships"][0]["decision_refs"] == [
        {"id": "decision:describes", "revision": 3}
    ]

    json_schema = schema.GraphArtifactResponse.model_json_schema()
    assert {"entities", "properties", "relationships", "coverage"} <= set(json_schema["properties"])
    assert "GraphSnapshot" not in json_schema.get("$defs", {})


def test_graph_rejects_internal_projection_and_internal_payload_fields():
    payload = _graph_response()
    payload["projection"] = "effective"
    with pytest.raises(ValidationError):
        schema.GraphArtifactResponse.model_validate(payload)

    payload = _graph_response()
    payload["snapshot"] = {"nodes": [], "edges": []}
    with pytest.raises(ValidationError):
        schema.GraphArtifactResponse.model_validate(payload)

    payload = _graph_response()
    payload["relationships"][0]["source_selection_refs"] = ["selection:bridge"]
    with pytest.raises(ValidationError):
        schema.GraphArtifactResponse.model_validate(payload)


def test_effective_projection_fails_closed_but_all_candidates_preserves_audit_state():
    invalid = _graph_response()
    invalid["relationships"][0]["polarity"] = "negated"
    invalid["relationships"][0]["policy_eligible"] = False
    invalid["relationships"][0]["reason_code"] = "predicate_not_supported"
    with pytest.raises(ValidationError, match="effective_affirmed"):
        schema.GraphArtifactResponse.model_validate(invalid)

    audit = deepcopy(invalid)
    audit["projection"] = "all_candidates"
    response = schema.GraphArtifactResponse.model_validate(audit)
    assert response.relationships[0].polarity == "negated"
    assert response.relationships[0].policy_eligible is False

    for missing_proof_field in ("proof_ref", "decision_refs"):
        incomplete = _graph_response()
        incomplete["relationships"][0][missing_proof_field] = (
            None if missing_proof_field == "proof_ref" else []
        )
        with pytest.raises(ValidationError, match="effective_affirmed"):
            schema.GraphArtifactResponse.model_validate(incomplete)


def test_pending_graph_cannot_claim_an_uncommitted_projection():
    pending = {
        "contract_version": schema.CONTRACT_VERSION,
        "recognition_run_id": RUN_ID,
        "run_revision": 1,
        "event_head": 1,
        "artifact_revision": 0,
        "availability": "pending",
        "projection": "effective_affirmed",
        "graph_snapshot": None,
        "entities": [],
        "properties": [],
        "relationships": [],
        "invalidated_refs": [],
        "coverage": {},
        "unresolved": {},
        "error": None,
    }
    assert schema.GraphArtifactResponse.model_validate(pending).entities == []

    pending["entities"] = _graph_response()["entities"]
    with pytest.raises(ValidationError, match="pending graph"):
        schema.GraphArtifactResponse.model_validate(pending)


def test_source_query_accepts_only_opaque_selection_or_original_mode():
    selection = schema.SourceQuery(selection_ref="selection:bridge")
    assert selection.selection_ref == "selection:bridge"
    assert schema.SourceQuery(format="original").format == "original"

    with pytest.raises(ValidationError):
        schema.SourceQuery(selection_ref="selection:bridge", format="original")
    for forbidden in (
        {"selection_ref": "selection:bridge", "path": "/etc/passwd"},
        {"anchor": _anchor()},
        {"span_start": 0, "span_end": 5},
        {"url": "file:///etc/passwd"},
    ):
        with pytest.raises(ValidationError):
            schema.SourceQuery.model_validate(forbidden)


def test_source_response_returns_selection_and_authorized_word_viewer_anchors():
    response = schema.SourceArtifactResponse(
        recognition_run_id=RUN_ID,
        analysis_id="analysis:one",
        document_hash="a" * 64,
        structure_hash="b" * 64,
        filename="CMCReport.docx",
        content={"type": "doc", "content": []},
        selection={
            "selection_ref": "selection:bridge",
            "section_node_id": "section:1",
            "source_record_ref": "record:1",
            "record_view_ref": "record-view:1",
            "source_cell_id": "cell:1",
            "span_refs": ["span:1"],
            "selection_role": "predicate_bridge",
        },
        anchors=[_anchor()],
    )
    payload = response.model_dump(mode="json")
    assert set(payload["selection"]) == {
        "selection_ref",
        "section_node_id",
        "source_record_ref",
        "record_view_ref",
        "source_cell_id",
        "span_refs",
        "selection_role",
    }
    assert payload["anchors"][0]["evidence_id"] == "paragraph:1:0"

    invalid = response.model_dump(mode="python")
    invalid["anchors"][0]["document_hash"] = "c" * 64
    with pytest.raises(ValidationError, match="another document"):
        schema.SourceArtifactResponse.model_validate(invalid)


def test_control_request_delete_query_and_response_use_exact_fields():
    request = schema.RunControlRequest(
        expected_revision=8,
        request_key="pause-op-key",
        reason="用户请求暂停以检查当前结果",
    )
    assert set(request.model_dump()) == {"expected_revision", "request_key", "reason"}
    delete = schema.DeleteRunRequest(expected_revision=9, request_key="delete-op-key")
    assert set(delete.model_dump()) == {"expected_revision", "request_key"}

    with pytest.raises(ValidationError):
        schema.RunControlRequest(
            expected_version=8,
            request_key="pause-op-key",
            reason="wrong field",
        )
    with pytest.raises(ValidationError):
        schema.DeleteRunRequest(
            expected_revision=9,
            request_key="delete-op-key",
            reason="DELETE contract has no body reason",
        )

    response = schema.RunControlResponse(
        recognition_run_id=RUN_ID,
        run_revision=9,
        event_head=36,
        artifact_revision=4,
        status="paused",
        stage="extracting",
        operation="pause",
        operation_status="accepted",
        available_actions=["resume", "cancel", "delete"],
    )
    payload = response.model_dump(mode="json")
    assert payload["operation"] == "pause"
    assert "control_version" not in payload
    assert "execution_status" not in payload


def test_sse_uses_numeric_sequence_and_safe_typed_data():
    data = {
        "contract_version": schema.CONTRACT_VERSION,
        "event_id": "event:graph-ready",
        "recognition_run_id": RUN_ID,
        "run_revision": 8,
        "event_head": 35,
        "artifact_revision": 4,
        "status": "running",
        "stage": "extracting",
        "artifact_kind": "graph",
        "availability": "partial",
    }
    event = schema.SseEvent(id=35, event="artifact", data=data)
    assert event.id == event.data.event_head
    assert event.data.event_id == "event:graph-ready"
    assert "payload" not in event.data.model_dump()

    with pytest.raises(ValidationError, match="durable event sequence"):
        schema.SseEvent(id=34, event="artifact", data=data)
    invalid = data | {"availability": None}
    with pytest.raises(ValidationError, match="supplied together"):
        schema.RunEventResponse.model_validate(invalid)


def test_error_contract_rejects_unknown_codes_and_unsafe_extras():
    response = schema.ApiErrorResponse(
        error={
            "code": "RUN_REVISION_CONFLICT",
            "message": "运行状态已变化，请刷新后重试",
            "retryable": True,
            "current_revision": 8,
        }
    )
    assert response.error.current_revision == 8

    with pytest.raises(ValidationError):
        schema.ApiErrorResponse(
            error={
                "code": "INTERNAL_TRACEBACK",
                "message": "unsafe",
                "retryable": False,
            }
        )
    with pytest.raises(ValidationError):
        schema.ApiErrorResponse(
            error={
                "code": "RUN_NOT_FOUND",
                "message": "not found",
                "retryable": False,
                "prompt": "must never cross the API boundary",
            }
        )


def test_public_schema_never_imports_internal_graph_snapshot():
    assert "GraphSnapshot" not in vars(schema)
    annotation = schema.GraphArtifactResponse.model_fields["graph_snapshot"].annotation
    assert "GraphSnapshotHeader" in str(annotation)
    assert "ontology_guided" not in str(annotation)


def test_timestamps_must_be_timezone_aware():
    payload = _run_response()
    payload["created_at"] = datetime(2026, 9, 8, 12, 0)
    with pytest.raises(ValidationError):
        schema.DocumentAnalysisRunResponse.model_validate(payload)
