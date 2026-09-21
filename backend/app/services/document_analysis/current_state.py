"""Current run state, exact result reuse and per-request accounting (format 4)."""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

from sqlalchemy import select

from app.models.document_analysis import (
    DocumentAnalysisRun,
    DocumentRunCandidate,
    DocumentRunCandidateHead,
    DocumentRunCurrentState,
    DocumentRunRequest,
    DocumentRunResult,
    DocumentVerificationProof,
    DocumentVerificationProofHead,
)
from app.services.document_analysis.run_store import HeadConflict, content_hash
from app.services.document_analysis.state_artifacts import performance_policy
from app.services.extraction.ontology_guided.current_work import (
    TOOL_BATCH_PROTOCOL_VERSION,
    TOOL_PROTOCOL_VERSION,
    call_request_key,
    protocol_result_ref,
    validate_batch_tool_protocol,
    validate_member_result_version,
    validate_protocol_result,
    validate_tool_protocol,
)
from app.services.extraction.ontology_guided.record_discovery import RECORD_PROTOCOL


def enabled(store, run):
    return performance_policy(store, run).get("state_storage_version") == 4


def _key(key):
    # Slot keys include IRIs; retain the original business key in the checked payload.
    return content_hash(key)


def _checked(row):
    if content_hash(row.payload) != row.content_hash:
        raise HeadConflict("current state content hash mismatch")
    return deepcopy(row.payload)


def read_rows(store, run, model, *, prefix=None):
    store.get_owned(run.recognition_run_id, run.owner_id)
    query = select(model).where(model.recognition_run_id == run.recognition_run_id)
    if prefix is not None:
        query = query.where(model.domain.startswith(prefix))
    rows = {}
    for row in store.db.scalars(query):
        payload = _checked(row)
        rows.setdefault(row.domain, {})[payload["storage_key"]] = payload["body"]
    return rows


def put_rows(store, run, model, domain, values, *, work_version=None, immutable=False):
    for key, body in values.items():
        identity = (run.recognition_run_id, domain, _key(key))
        existing = store.db.get(model, identity)
        if body is None:
            if immutable:
                raise HeadConflict("paid result cannot be removed")
            if existing is not None:
                store.db.delete(existing)
            continue
        payload = {"storage_key": key, "body": body}
        digest = content_hash(payload)
        if existing is not None:
            _checked(existing)
            if existing.content_hash == digest:
                continue
            if immutable:
                raise HeadConflict("exact result identity cannot be rewritten")
            existing.payload, existing.content_hash = payload, digest
            if work_version is not None:
                existing.work_version = work_version
        else:
            field = (
                "business_key"
                if model is DocumentRunCurrentState
                else "result_key"
                if model is DocumentRunResult
                else "request_key"
            )
            store.db.add(
                model(
                    recognition_run_id=run.recognition_run_id,
                    domain=domain,
                    **{field: _key(key)},
                    payload=payload,
                    content_hash=digest,
                    **({"work_version": work_version} if work_version is not None else {}),
                )
            )


def get_row(store, run, domain, key="current", *, model=DocumentRunCurrentState):
    store.get_owned(run.recognition_run_id, run.owner_id)
    row = store.db.get(model, (run.recognition_run_id, domain, _key(key)))
    if row is None:
        return None
    return _checked(row)["body"]


def lock_current(store, run, token, fingerprint):
    store.assert_fence(run.recognition_run_id, run.owner_id, token, for_update=True)
    current = store.get_owned(run.recognition_run_id, run.owner_id)
    if current.run_fingerprint != fingerprint:
        raise HeadConflict("current work fingerprint changed")
    return current


def _candidate(store, run, token, kind, body, sequence, *, identity_nodes=()):
    identity = body.get("candidate_id") or body["entity_id"]
    revision = body["revision"]
    existing = store.db.get(DocumentRunCandidate, (run.recognition_run_id, identity, revision))
    digest = content_hash(body)
    refs = [body["proof_ref"]] if body.get("proof_ref") else []
    if existing is not None:
        if existing.payload_hash != digest or existing.kind != kind or existing.proof_refs != refs:
            identity_fields = {"identity_status", "external_provenance", "identity_decision_refs"}
            before = existing.payload
            if not (
                kind == existing.kind == "entity" and body in identity_nodes
                and existing.proof_refs == refs
                and content_hash(before) == existing.payload_hash
                and {key: value for key, value in before.items() if key not in identity_fields}
                == {key: value for key, value in body.items() if key not in identity_fields}
                and body.get("identity_status") == "verified"
                and body.get("external_provenance") and body.get("identity_decision_refs")
                and all(item in body["external_provenance"]
                        for item in before.get("external_provenance", []))
                and all(item in body["identity_decision_refs"]
                        for item in before.get("identity_decision_refs", []))
            ):
                raise HeadConflict("immutable candidate revision changed")
            existing.payload = deepcopy(body)
            existing.payload_hash = digest
    else:
        head = store.db.get(DocumentRunCandidateHead, (run.recognition_run_id, identity))
        store.put_candidate(
            run.recognition_run_id,
            run.owner_id,
            token,
            candidate_id=identity,
            revision=revision,
            kind=kind,
            payload=body,
            payload_hash=digest,
            proof_refs=refs,
            expected_head_revision=head.revision if head else 0,
            event_sequence=sequence,
        )
    return {"id": identity, "revision": revision, "kind": kind}


def write_work(
    store, run, token, changes, *, sequence, expected_version, fingerprint, identity_nodes=(),
):
    current = lock_current(store, run, token, fingerprint)
    if current.work_version != expected_version:
        raise HeadConflict("current work version changed")
    version = expected_version + 1
    for domain, values in changes.items():
        if domain in {"nodes", "edges", "properties", "relationship_groups"}:
            kind = {"nodes": "entity", "edges": "relationship", "properties": "property",
                    "relationship_groups": "relationship_group"}[domain]
            values = {
                key: {
                    "key": row["key"],
                    "position": row["position"],
                    "candidate_ref": _candidate(
                        store, current, token, kind, row["value"], sequence,
                        identity_nodes=identity_nodes,
                    ),
                }
                if row
                else None
                for key, row in values.items()
            }
        put_rows(
            store, current, DocumentRunCurrentState, "work:" + domain, values, work_version=version
        )
    current.work_version = version
    store.db.flush()
    return current


def write_proofs(store, run, token, outcome, sequence):
    decisions = {}
    for decision in outcome.decision_payloads:
        decisions.setdefault(str(decision.get("target_id")), []).append(decision)
    for proof in outcome.proof_payloads:
        identity, revision = str(proof["proof_id"]), int(proof.get("proof_revision", 1))
        body = {
            "predicate_evidence": proof,
            "decisions": decisions.get(str(proof["target_id"]), []),
        }
        existing = store.db.get(
            DocumentVerificationProof, (run.recognition_run_id, identity, revision)
        )
        if existing:
            if existing.payload_hash != content_hash(body):
                raise HeadConflict("immutable proof revision changed")
            continue
        head = store.db.get(DocumentVerificationProofHead, (run.recognition_run_id, identity))
        store.put_proof(
            run.recognition_run_id,
            run.owner_id,
            token,
            proof_id=identity,
            proof_revision=revision,
            target_id=str(proof["target_id"]),
            payload=body,
            payload_hash=content_hash(body),
            event_sequence=sequence,
            expected_head_revision=head.proof_revision if head else 0,
        )
        put_rows(
            store,
            run,
            DocumentRunCurrentState,
            "work:proof_heads",
            {
                identity: {
                    "id": identity,
                    "revision": revision,
                    "content_hash": content_hash(body),
                }
            },
            work_version=run.work_version + 1,
        )


def lock_read(store, run):
    # Same order as every writer: execution then run. Component reads share this view.
    store._execution_for_update(run.recognition_run_id)
    current = store.db.scalar(
        select(DocumentAnalysisRun)
        .where(
            DocumentAnalysisRun.recognition_run_id == run.recognition_run_id,
            DocumentAnalysisRun.owner_id == run.owner_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if current is None:
        raise HeadConflict("current restore identity mismatch")
    return current


def restore_work(store, run, fingerprint):
    current = lock_read(store, run)
    if current.run_fingerprint != fingerprint:
        raise HeadConflict("current restore identity mismatch")
    if not current.work_version:
        return None
    rows = {
        name.removeprefix("work:"): values
        for name, values in read_rows(
            store, current, DocumentRunCurrentState, prefix="work:"
        ).items()
    }
    if "control" not in rows:
        raise HeadConflict("current work control is missing")
    control = rows["control"]["current"]
    if control["run_fingerprint"] != fingerprint:
        raise HeadConflict("current work fingerprint mismatch")
    for name in ("nodes", "edges", "properties", "relationship_groups"):
        for row in sorted(rows.get(name, {}).values(), key=lambda r: r.get("position", 0)):
            ref = row.pop("candidate_ref")
            candidate = store.db.get(
                DocumentRunCandidate, (run.recognition_run_id, ref["id"], ref["revision"])
            )
            head = store.db.get(DocumentRunCandidateHead, (run.recognition_run_id, ref["id"]))
            if (
                candidate is None
                or head is None
                or head.revision != ref["revision"]
                or candidate.kind != ref["kind"]
                or content_hash(candidate.payload) != candidate.payload_hash
            ):
                raise HeadConflict("current candidate reference mismatch")
            row["value"] = deepcopy(candidate.payload)
    for ref in rows.get("proof_heads", {}).values():
        proof = store.db.get(
            DocumentVerificationProof, (run.recognition_run_id, ref["id"], ref["revision"])
        )
        head = store.db.get(DocumentVerificationProofHead, (run.recognition_run_id, ref["id"]))
        if (
            proof is None
            or head is None
            or head.proof_revision != ref["revision"]
            or proof.payload_hash != ref["content_hash"]
            or content_hash(proof.payload) != ref["content_hash"]
        ):
            raise HeadConflict("current proof reference mismatch")
    from app.services.extraction.ontology_guided.dependencies import DependencyIndex

    dependencies = DependencyIndex.from_current(rows).snapshot()
    display = None
    return SimpleNamespace(
        work_state=rows,
        work_version=current.work_version,
        frontier=control["frontier_policy"],
        diagnostics=control["diagnostics"],
        task_outcomes=[],
        recall_ledger={},
        dependency_index=dependencies,
        ranking_state={},
        model_call_state={},
        graph_state=display["graph"] if display else None,
    )


def load_protocol_record(
    store, run, lineage_id, result_ref, field, *, member_task_id=None, result_version=None,
):
    """Load an exact owned result and verify the immutable wrapper's own version."""
    result = get_row(store, run, "calls:results", result_ref, model=DocumentRunResult)
    required = {"lineage_id", "field", "value"}
    if member_task_id is not None:
        required |= {"member_task_id", "result_version"}
    if (result is None or set(result) != required
            or result["lineage_id"] != lineage_id or result["field"] != field
            or (member_task_id is not None and result["member_task_id"] != member_task_id)
            or (result_version is not None and result.get("result_version") != result_version)):
        raise HeadConflict("tool protocol result reference mismatch")
    try:
        validate_protocol_result(field, result["value"])
        if member_task_id is not None:
            validate_member_result_version(result["result_version"])
        expected = protocol_result_ref(
            lineage_id, field, result["value"], member_task_id=member_task_id,
            result_version=result.get("result_version"),
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise HeadConflict("tool protocol result is invalid") from exc
    if result_ref != expected:
        raise HeadConflict("tool protocol result identity mismatch")
    return result


def load_protocol_result(
    store, run, lineage_id, result_ref, field, *, member_task_id=None, result_version=None,
):
    return load_protocol_record(
        store, run, lineage_id, result_ref, field, member_task_id=member_task_id,
        result_version=result_version,
    )["value"]


def check_record_receipt(protocol, receipt, fingerprint, *, pending=True):
    """A record request has one exact task owner, without batch participants."""
    task = protocol["task"]
    if (protocol.get("version") != RECORD_PROTOCOL
            or receipt.get("lineage_id") != protocol["lineage_id"]
            or receipt.get("record_task_id") != task["task_id"]
            or receipt.get("task_id") != task["task_id"]
            or receipt.get("record_id") != task["record_id"]
            or receipt.get("schema_card_id") != task["schema_card_id"]
            or receipt.get("run_fingerprint") != fingerprint
            or any(key in receipt for key in (
                "subject_ref", "member_task_ids", "member_lineage_ids", "stage_group_seq",
            ))):
        raise HeadConflict("record reservation owner differs from its task")
    if pending:
        request = protocol["pending_request"]
        if (request is None or receipt.get("protocol_attempt") != request["attempt"]
                or receipt.get("ordinal") != request["attempt"]
                or receipt.get("input_hash") != request["request_hash"]
                or receipt["stage"].removeprefix("ontology_guided_") != request["stage"]
                or call_request_key(receipt) != request["reservation_key"]):
            raise HeadConflict("record reservation differs from its pending request")


def _tool_protocol_results(store, run, protocol, *, unconfirmed_attempts=()):
    from app.services.extraction.ontology_guided.tool_contracts import (
        TOOL_DEFINITIONS,
        ToolErrorResult,
    )

    lineage = protocol["lineage_id"]
    turns, previous_attempt = {}, 0
    for result_ref in protocol["turn_refs"]:
        result = load_protocol_result(store, run, lineage, result_ref, "model_turn")
        attempt = result["attempt"]
        if (attempt <= previous_attempt or attempt not in protocol["completed_attempts"]
                or result["stage"] != protocol["stage"]):
            raise HeadConflict("tool protocol turns are not the ordered current stage")
        request = get_row(
            store, run, "calls:requests",
            call_request_key({"lineage_id": lineage, "protocol_attempt": attempt}),
            model=DocumentRunRequest,
        )
        if request is None:
            raise HeadConflict("model turn has no reserved request")
        reservation = request["reservation"]
        if protocol.get("version") == RECORD_PROTOCOL:
            check_record_receipt(protocol, reservation, run.run_fingerprint, pending=False)
        if (reservation.get("input_hash") != result["input_hash"]
                or reservation.get("run_fingerprint") != run.run_fingerprint
                or reservation["stage"].removeprefix("ontology_guided_") != result["stage"]
                or (request["result_ref"] != result_ref
                    and not (attempt in unconfirmed_attempts and request["result_ref"] is None))):
            raise HeadConflict("model turn does not match its reserved request")
        turns[attempt] = (result_ref, result)
        previous_attempt = attempt
    tools = {}
    for result_ref in protocol["completed_tool_results"]:
        result = load_protocol_result(store, run, lineage, result_ref, "tool_result")
        key = result["attempt"], result["call_id"]
        if key in tools or key[0] not in turns:
            raise HeadConflict("tool result has no unique current response call")
        turn = turns[key[0]][1]
        calls = [item for item in turn["output_items"] if item.get("type") == "function_call"]
        ids = [item.get("call_id") for item in calls]
        refused = any(
            part.get("type") == "refusal"
            for item in turn["output_items"] if item.get("type") == "message"
            for part in item.get("content", []) if isinstance(part, dict)
        )
        if (turn["response_status"] != "completed" or turn["error"] is not None
                or "reference_error" in turn
                or turn["incomplete_details"] is not None or refused
                or any(not isinstance(call_id, str) or not call_id for call_id in ids)
                or len(set(ids)) != len(ids) or key[1] not in ids):
            raise HeadConflict("tool result cannot be attached to an unconsumable response")
        call = next(item for item in calls if item["call_id"] == key[1])
        if not turn["allowed_tool_names"]:
            raise HeadConflict("answer-only response cannot have tool results")
        try:
            envelope = result["result"]
            if call.get("name") not in turn["allowed_tool_names"] and (
                envelope.get("status") not in {"blocked", "error"}
                or envelope.get("data") is not None
            ):
                raise ValueError("tool result exceeds original request permissions")
            result_type = (
                ToolErrorResult if envelope.get("data") is None
                else TOOL_DEFINITIONS[call["name"]].result_type
            )
            result_type.model_validate(envelope, strict=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise HeadConflict("stored tool result does not match its result contract") from exc
        tools[key] = result_ref
    for field in ("discovery", "verification", "outcome"):
        result_ref = protocol[field + "_ref"]
        if result_ref is not None:
            load_protocol_result(store, run, lineage, result_ref, field)
    if protocol.get("verification_batches") is not None:
        from app.services.extraction.ontology_guided.claim_protocol import VerifiedClaimSet

        for batch in protocol["verification_batches"]["batches"]:
            if batch["result_ref"] is None:
                continue
            value = load_protocol_result(
                store, run, lineage, batch["result_ref"], "verification",
            )
            try:
                checked = VerifiedClaimSet.model_validate(value, strict=True)
            except (TypeError, ValueError) as exc:
                raise HeadConflict("stored verification batch result is invalid") from exc
            if {target.target_id for target in checked.targets} != set(batch["target_ids"]):
                raise HeadConflict("stored verification batch targets changed")
    for value in protocol["materialized_refs"].values():
        if not isinstance(value.get("result_ref"), str):
            raise HeadConflict("materialized reference has no exact tool result")
        load_protocol_result(store, run, lineage, value["result_ref"], "tool_result")
    return turns


def _record_source_records(store, run, task):
    """Resolve exactly the frozen source members against this run's owned IR."""
    from app.models.document_analysis import DocumentAnalysisArtifact
    from app.services.extraction.document_ir import DocumentIR
    from app.services.extraction.ontology_guided.records import RecordIndex

    source_ref = store.get_artifact(run.recognition_run_id, run.owner_id, "metadata")
    source = store.db.get(DocumentAnalysisArtifact, source_ref.artifact_id) if source_ref else None
    if (source is None or not source.payload or source.content_hash != source_ref.content_hash
            or content_hash(source.payload) != source.content_hash):
        raise HeadConflict("record feedback has no authoritative original source")
    try:
        ir = DocumentIR.model_validate(source.payload["analysis"], strict=True)
        index = RecordIndex(ir)
        identities = task.get("source_record_ids") or [task["record_id"]]
        sections = task.get("reading_section_ids", [])
        if (ir.document_hash != run.document_hash or task["record_id"] not in identities
                or len(identities) != len(set(identities))
                or len(sections) != len(set(sections))
                or any(section not in index.nodes_by_id for section in sections)):
            raise ValueError("record source group does not match the frozen task")
        records = {identity: index.by_id[identity] for identity in identities}
    except (KeyError, TypeError, ValueError) as exc:
        raise HeadConflict("record source is outside its original document or group") from exc
    return ir, records


def _check_record_authorization(store, run, task, authorization):
    """A group grants facts only from each explicitly named record's own source."""
    ir, records = _record_source_records(store, run, task)
    if (set(authorization.record_ids) != set(records)
            or authorization.ir_identity.parser_version != ir.parser_version
            or authorization.ir_identity.structure_hash != ir.structure_hash):
        raise HeadConflict("record source authorization differs from its frozen source group")
    try:
        for fragment in authorization.fragments:
            ir.anchor(fragment.evidence_id, fragment.span_start, fragment.span_end)
            if fragment.record_id is None:
                if fragment.fact_eligible:
                    raise ValueError("unassigned source cannot grant facts")
                continue
            record = records[fragment.record_id]
            units = (record.source_units if fragment.fact_eligible else (
                *record.source_units, *record.header_units,
                *record.parent_units, *record.note_units,
            ))
            if fragment.evidence_id not in {unit.evidence_id for unit in units}:
                raise ValueError("fragment source does not belong to its authorized record")
    except (KeyError, TypeError, ValueError) as exc:
        raise HeadConflict("record authorization source is outside its original group") from exc


def _check_record_reopening(store, run, protocol, old, authority):
    """Authorize a new record generation only from its published pending feedback."""
    from app.schemas.evidence import EvidenceAnchor
    from app.services.extraction.ontology_guided.contracts import VersionedRef

    lineage = protocol["lineage_id"]
    row = get_row(store, run, "work:record_discovery",
                  json.dumps(lineage, ensure_ascii=False, separators=(",", ":")))
    current = (row or {}).get("value", {})
    prior_work = (authority or {}).get("value", {})
    feedback_hash = protocol.get("record_feedback_hash")
    if (not isinstance(feedback_hash, str) or len(feedback_hash) != 64
            or any(char not in "0123456789abcdef" for char in feedback_hash)
            or feedback_hash == old.get("record_feedback_hash")
            or current.get("task") != old["task"]
            or current.get("status") != "active"
            or current.get("feedback_hash") != feedback_hash
            or current.get("consumed_feedback_hash") != feedback_hash
            or current.get("generation") != protocol["assertion_generation"]
            or prior_work.get("status") != "pending"
            or prior_work.get("consumed_feedback_hash") == feedback_hash
            or any(prior_work.get(key) != current.get(key) for key in (
                "task", "outcome_ref", "feedback_hash", "feedback_refs", "feedback_source_refs",
            ))
            or old["outcome_ref"] is None or current.get("outcome_ref") != old["outcome_ref"]
            or protocol["assertion_generation"] != old["assertion_generation"] + 1
            or protocol["evidence_revision"] != old["evidence_revision"] + 1
            or old["pending_request"] is not None or protocol["pending_request"] is not None
            or protocol["request_attempt"] != old["request_attempt"]
            or protocol["completed_attempts"] != old["completed_attempts"]
            or protocol["stage"] != "discovery"
            or any(protocol[field] is not None
                   for field in ("discovery_ref", "verification_ref", "outcome_ref"))
            or protocol.get("verification_batches") is not None
            or any(protocol[field] for field in (
                "turn_refs", "completed_tool_results", "stage_input_items", "materialized_refs",
            ))):
        raise HeadConflict("record reopening has no exact published feedback authority")
    maximum = performance_policy(store, run).get("max_lineage_calls", 4)
    if maximum - old["request_attempt"] < 2:
        raise HeadConflict("record reopening has no remaining discovery and verification budget")
    allowed_target_changes = {"target_id", "source_scope_hash", "context_hash"}
    if ({key: value for key, value in protocol["base_target"].items()
         if key not in allowed_target_changes}
            != {key: value for key, value in old["base_target"].items()
                if key not in allowed_target_changes}):
        raise HeadConflict("record reopening changed frozen source or task identity")
    try:
        refs = [VersionedRef.model_validate(value, strict=True)
                for value in current.get("feedback_refs", [])]
        anchors = [EvidenceAnchor.model_validate(value, strict=True)
                   for value in current.get("feedback_source_refs", [])]
    except (TypeError, ValueError) as exc:
        raise HeadConflict("record feedback references are invalid") from exc
    before_refs = {(value["id"], value["revision"])
                   for value in (old.get("reference_context") or {}).get("entity_refs", [])}
    after_refs = {(value["id"], value["revision"])
                  for value in (protocol.get("reference_context") or {}).get("entity_refs", [])}
    authorized_refs = {(ref.id, ref.revision) for ref in refs}
    if protocol["task"].get("purpose") == "property_disambiguation":
        permitted_references = after_refs == authorized_refs
    else:
        replaced = {(identity, revision) for identity, revision in before_refs - after_refs
                    if any(new_id == identity and new_revision > revision
                           for new_id, new_revision in after_refs & authorized_refs)}
        permitted_references = (before_refs - after_refs == replaced
                                and after_refs - before_refs <= authorized_refs)
    if not permitted_references:
        raise HeadConflict("record reopening changed unauthorized entity references")
    for ref in refs:
        candidate = store.db.get(
            DocumentRunCandidate, (run.recognition_run_id, ref.id, ref.revision),
        )
        head = store.db.get(DocumentRunCandidateHead, (run.recognition_run_id, ref.id))
        if (candidate is None or head is None or head.revision != ref.revision
                or candidate.kind != "entity"
                or content_hash(candidate.payload) != candidate.payload_hash):
            raise HeadConflict("record feedback entity is not a current owned candidate")
    if not anchors:
        raise HeadConflict("record feedback has no authoritative original source")
    ir, records = _record_source_records(store, run, protocol["task"])
    try:
        evidence = {unit.evidence_id: unit for record in records.values()
                    for unit in record.source_units}
        for anchor in anchors:
            unit = evidence[anchor.evidence_id]
            start, end = anchor.span_start, anchor.span_end
            if (ir.document_hash != run.document_hash or start is None or end is None
                    or end > len(unit.text)
                    or anchor != ir.anchor(anchor.evidence_id, start, end)):
                raise ValueError("feedback anchor differs from original record source")
    except (KeyError, TypeError, ValueError) as exc:
        raise HeadConflict("record feedback source is outside its original record") from exc


def _check_record_answer_correction(store, run, protocol, old, result_changes):
    """Allow one correction input while retaining the exact paid answer and source scope."""
    from app.services.extraction.ontology_guided.claim_protocol import (
        DiscoveryEnvelope,
        VerificationEnvelope,
    )
    from app.services.extraction.ontology_guided.tool_model_adapter import (
        ToolModelRecognitionAdapter,
        parse_stage_answer,
    )

    changed = {"stage_input_items", "turn_refs", "completed_tool_results"}
    stage = protocol["stage"]
    if (stage not in {"discovery", "verification"}
            or old[stage + "_ref"] is not None or old["outcome_ref"] is not None
            or old["pending_request"] is not None or not old["turn_refs"]
            or protocol["turn_refs"] or protocol["completed_tool_results"] or result_changes
            or {key: value for key, value in protocol.items() if key not in changed}
            != {key: value for key, value in old.items() if key not in changed}):
        raise HeadConflict("record correction must preserve its committed state")
    try:
        before_items, after_items = deepcopy(old["stage_input_items"]), deepcopy(
            protocol["stage_input_items"],
        )
        before = json.loads(before_items[0]["content"][0].pop("text"))
        after = json.loads(after_items[0]["content"][0].pop("text"))
        correction = after.pop("answer_correction")
        turn = after.pop("turn")
        before.pop("turn")
        reserve = 1 if stage == "discovery" else 0
        if (before_items != after_items or before != after
                or "answer_correction" in before
                or set(correction) != {"previous_answer", "issues"}
                or not isinstance(correction["issues"], list) or not correction["issues"]
                or turn != {
                    "stage": stage, "mode": "answer", "allowed_tool_names": [],
                    "model_calls_remaining": turn.get("model_calls_remaining"),
                    "reserved_model_calls": reserve, "reason_code": "answer_correction",
                }
                or type(turn["model_calls_remaining"]) is not int
                or turn["model_calls_remaining"] < reserve + 1):
            raise ValueError("correction changed the frozen input or repeated a correction")
        turns = _tool_protocol_results(store, run, old)
        last = turns[old["completed_attempts"][-1]][1]
        answer = parse_stage_answer(
            ToolModelRecognitionAdapter._turn(last),
            DiscoveryEnvelope if stage == "discovery" else VerificationEnvelope,
        )
        if correction["previous_answer"] != answer.model_dump(mode="json"):
            raise ValueError("correction does not reference the last paid answer")
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        raise HeadConflict("record correction has no exact answer and source authority") from exc


def _check_record_verification_batch_transition(protocol, old, result_changes):
    """Validate the one mutable verifier-batch step and its optional result."""
    from app.services.extraction.ontology_guided.claim_protocol import VerifiedClaimSet

    before, after = old.get("verification_batches"), protocol.get("verification_batches")
    if before == after:
        return False
    if before is None:
        # The full frozen verifier input is compiled once after discovery.
        return False
    if (
        after is None
        or before["version"] != after["version"]
        or before["verification_input_hash"] != after["verification_input_hash"]
    ):
        raise HeadConflict("verification batch input changed")
    old_batches = {item["batch_id"]: item for item in before["batches"]}
    new_batches = {item["batch_id"]: item for item in after["batches"]}
    if not set(old_batches) <= set(new_batches):
        raise HeadConflict("verification batch history regressed")
    transitions = []
    for batch_id, previous in old_batches.items():
        current = new_batches[batch_id]
        frozen = {"batch_id", "parent_batch_id", "target_ids"}
        if any(previous[name] != current[name] for name in frozen):
            raise HeadConflict("verification batch identity changed")
        change = (previous["status"], current["status"])
        allowed = {
            ("pending", "pending"), ("pending", "running"), ("pending", "split"),
            ("running", "running"), ("running", "completed"),
            ("running", "split"), ("running", "failed"),
            ("completed", "completed"), ("split", "split"),
            ("failed", "failed"), ("failed", "pending"),
        }
        if change not in allowed:
            raise HeadConflict("verification batch status changed illegally")
        expected_attempts = previous["attempts"] + int(change == ("pending", "running"))
        if current["attempts"] != expected_attempts:
            raise HeadConflict("verification batch attempts changed illegally")
        if change[0] != change[1]:
            transitions.append((previous, current))
        if previous["status"] in {"completed", "split"} and previous != current:
            raise HeadConflict("completed verification batch changed")
        if current["status"] == "completed":
            result = result_changes.get(current["result_ref"])
            if previous["status"] != "completed":
                if result is None or result.get("field") != "verification":
                    raise HeadConflict("verification batch result is missing")
                try:
                    checked = VerifiedClaimSet.model_validate(result["value"], strict=True)
                except (TypeError, ValueError) as exc:
                    raise HeadConflict("verification batch result is invalid") from exc
                if {item.target_id for item in checked.targets} != set(current["target_ids"]):
                    raise HeadConflict("verification batch result targets changed")
        elif current["result_ref"] is not None:
            raise HeadConflict("unfinished verification batch has a result")
    if len(transitions) != 1:
        raise HeadConflict("verification batches must advance one at a time")
    previous, current = transitions[0]
    added = [item for key, item in new_batches.items() if key not in old_batches]
    if current["status"] == "split":
        if (
            len(added) != 2
            or any(item["parent_batch_id"] != current["batch_id"]
                   or item["status"] != "pending" for item in added)
            or [target for item in added for target in item["target_ids"]]
            != current["target_ids"]
        ):
            raise HeadConflict("verification batch split changed targets")
    elif added:
        raise HeadConflict("verification batch added without a split")
    reset = (
        previous["status"] == "running"
        and current["status"] in {"completed", "split", "failed"}
    )
    if reset and (
        old["pending_request"] is not None
        or protocol["pending_request"] is not None
        or protocol["stage_input_items"]
        or protocol["turn_refs"]
        or protocol["completed_tool_results"]
    ):
        raise HeadConflict("verification batch reset kept an active exchange")
    return reset


def _pending_review_completion(store, run, protocol, old, result_changes):
    """Replace only an unproved pending view after its frozen review has been saved."""
    if (protocol["version"] != TOOL_PROTOCOL_VERSION
            or performance_policy(store, run).get("record_discovery", {}).get("graph_phase")
            != "evidence_review"
            or old.get("pending_request") is not None
            or protocol.get("pending_request") is not None
            or protocol["stage"] != "finalize" or not protocol.get("verification_ref")
            or not protocol.get("outcome_ref")
            or any(protocol[field] != old[field] for field in (
                "assertion_generation", "evidence_revision", "discovery_ref",
            ))):
        return False
    pending = load_protocol_result(store, run, old["lineage_id"], old["outcome_ref"], "outcome")
    if (pending.get("complete") is not False or pending.get("semantic_outcome") != "not_checked"
            or any(pending.get(field) for field in (
                "nodes", "proof_payloads", "decision_payloads", "reference_resolutions",
            ))):
        return False
    candidates = [item for field in ("properties", "edges", "relationship_groups")
                  for item in pending.get(field, [])]
    if any(item.get("decision_status") != "not_checked" or item.get("proof_ref")
           or item.get("decision_refs") or item.get("policy_eligible") for item in candidates):
        return False
    replacement = result_changes.get(protocol["outcome_ref"], {})
    if (replacement.get("lineage_id") != protocol["lineage_id"]
            or replacement.get("field") != "outcome"
            or replacement.get("value", {}).get("complete") is not True):
        return False
    verification = load_protocol_result(
        store, run, protocol["lineage_id"], protocol["verification_ref"], "verification",
    )
    return verification.get("context_hash") == protocol["context_hash"]


def _persist_tool_protocol(store, run, protocol, old, result_changes, *, record_authority=None):
    lineage = protocol["lineage_id"]
    record_reopening = False
    if old is not None:
        if (protocol.get("version") not in {TOOL_PROTOCOL_VERSION, RECORD_PROTOCOL}
                or old.get("version") != protocol["version"]):
            raise HeadConflict("existing protocol cannot switch to Responses")
        if protocol["version"] == RECORD_PROTOCOL:
            record_reopening = protocol["assertion_generation"] != old["assertion_generation"]
            if record_reopening:
                _check_record_reopening(store, run, protocol, old, record_authority)
            elif (protocol.get("record_feedback_hash") != old.get("record_feedback_hash")
                  or protocol["evidence_revision"] != old["evidence_revision"]
                  or protocol["base_target"] != old["base_target"]):
                raise HeadConflict("record authority changed without a new authorized generation")
        for field in ("scope_id", "api_protocol", "reference_context"):
            if field == "reference_context" and record_reopening:
                continue
            if old.get(field) != protocol.get(field):
                raise HeadConflict("tool protocol frozen identity changed")
        if protocol["version"] == RECORD_PROTOCOL and old.get("task") != protocol["task"]:
            raise HeadConflict("record protocol frozen task changed")
        for field in (
            "request_attempt", "assertion_generation", "evidence_revision", "tool_calls_used",
        ):
            if protocol[field] < old[field]:
                raise HeadConflict("tool protocol counters regressed")
        if protocol["completed_attempts"][:len(old["completed_attempts"])] != old[
            "completed_attempts"
        ]:
            raise HeadConflict("tool protocol response receipts regressed")
        if old["recovery_used"] and not protocol["recovery_used"]:
            raise HeadConflict("tool protocol recovery budget regressed")
        if protocol["evidence_revision"] == old["evidence_revision"]:
            for field in ("evidence_hash", "context_hash", "context_authorization"):
                if protocol[field] != old[field]:
                    raise HeadConflict("current authorization changed without evidence revision")
        same_stage = (protocol["stage"] == old["stage"]
                      and protocol["assertion_generation"] == old["assertion_generation"]
                      and protocol["evidence_revision"] == old["evidence_revision"])
        batch_reset = False
        if protocol["version"] == RECORD_PROTOCOL and not record_reopening:
            batch_reset = _check_record_verification_batch_transition(
                protocol, old, result_changes,
            )
        correcting = (same_stage and protocol["version"] == RECORD_PROTOCOL
                      and old["stage_input_items"]
                      and protocol["stage_input_items"] != old["stage_input_items"]
                      and not batch_reset)
        if correcting:
            _check_record_answer_correction(store, run, protocol, old, result_changes)
        if same_stage and not correcting and not batch_reset:
            for field in ("base_target", "active_instructions", "stage_input_items"):
                if (field == "stage_input_items" and not old[field] and not old["turn_refs"]
                        and old["pending_request"] is None):
                    continue
                if protocol[field] != old[field]:
                    raise HeadConflict("current stage initial input changed")
            for field in ("turn_refs", "completed_tool_results"):
                if protocol[field][:len(old[field])] != old[field]:
                    raise HeadConflict("confirmed current stage results changed")
        if protocol["assertion_generation"] == old["assertion_generation"]:
            fields = ["discovery_ref"]
            if protocol["evidence_revision"] == old["evidence_revision"]:
                fields.extend(["verification_ref", "outcome_ref"])
            for field in fields:
                if old[field] is not None and old[field] != protocol[field]:
                    if field == "outcome_ref" and _pending_review_completion(
                        store, run, protocol, old, result_changes,
                    ):
                        continue
                    raise HeadConflict("committed tool protocol stage changed")
        pending = old["pending_request"]
        if pending and pending["attempt"] not in protocol["completed_attempts"]:
            if protocol["pending_request"] != pending:
                raise HeadConflict("unknown model request cannot be replaced or refunded")

    references = set(protocol["turn_refs"] + protocol["completed_tool_results"])
    references.update(protocol[field] for field in (
        "discovery_ref", "verification_ref", "outcome_ref",
    ) if protocol[field] is not None)
    references.update(value.get("result_ref") for value in protocol["materialized_refs"].values())
    if protocol.get("verification_batches") is not None:
        references.update(
            batch["result_ref"] for batch in protocol["verification_batches"]["batches"]
            if batch["result_ref"] is not None
        )
    changes = {
        key: value for key, value in result_changes.items() if value["lineage_id"] == lineage
    }
    if not set(changes) <= references:
        raise HeadConflict("result changes must be referenced by the current protocol")
    put_rows(store, run, DocumentRunResult, "calls:results", changes, immutable=True)
    store.db.flush()
    old_completed = old["completed_attempts"] if old else []
    newly_completed = protocol["completed_attempts"][len(old_completed):]
    turns = _tool_protocol_results(
        store, run, protocol, unconfirmed_attempts=newly_completed,
    )
    if newly_completed:
        pending = (old or {}).get("pending_request")
        if (pending is None or newly_completed != [pending["attempt"]]
                or pending["attempt"] not in turns):
            raise HeadConflict("new response receipt has no exact pending request")
        result = turns[pending["attempt"]][1]
        if result["allowed_tool_names"] != pending["allowed_tool_names"]:
            raise HeadConflict("response tool permissions differ from its pending request")
    authorization = protocol["context_authorization"]
    if authorization != (old or {}).get("context_authorization"):
        from app.services.extraction.ontology_guided.context import ContextAuthorization

        try:
            checked = ContextAuthorization.model_validate(authorization, strict=True)
        except (TypeError, ValueError) as exc:
            raise HeadConflict("current authorization has an invalid source contract") from exc
        if (checked.task_id != protocol["base_target"]["task_id"]
                or checked.ir_identity.document_hash != run.document_hash):
            raise HeadConflict("current authorization belongs to another task or document")
        if protocol["version"] == RECORD_PROTOCOL:
            if old is not None and not record_reopening:
                raise HeadConflict("record source authorization cannot change after registration")
            _check_record_authorization(store, run, protocol["task"], checked)
            # The coordinator registers the initial record authority before any request.
            if not record_reopening and (protocol["request_attempt"]
                                         or protocol["completed_attempts"]):
                raise HeadConflict("record source authority must precede model requests")
            retrieved = True
        else:
            retrieved = False
        for change in changes.values():
            if change["field"] != "tool_result":
                continue
            record = change["value"]
            result = record["result"]
            data = result.get("data")
            calls = turns.get(record["attempt"], (None, {}))[1].get("output_items", [])
            if (result.get("status") == "ok" and isinstance(data, dict)
                    and data.get("new_evidence") is True
                    and data.get("context_hash") == protocol["context_hash"]
                    and any(item.get("type") == "function_call"
                            and item.get("call_id") == record["call_id"]
                            and item.get("name") == "retrieve_evidence" for item in calls)):
                retrieved = True
        if not retrieved:
            raise HeadConflict("authorization change requires confirmed new retrieval evidence")
    for attempt in newly_completed:
        if attempt not in turns:
            raise HeadConflict("new response receipt has no exact current turn result")
        result_ref, result = turns[attempt]
        request_key = call_request_key({"lineage_id": lineage, "protocol_attempt": attempt})
        request = get_row(store, run, "calls:requests", request_key, model=DocumentRunRequest)
        if request is None:
            raise HeadConflict("completed response has no reserved request")
        reservation = request["reservation"]
        if (reservation["stage"].removeprefix("ontology_guided_") != result["stage"]
                or reservation.get("input_hash") != result["input_hash"]
                or reservation.get("run_fingerprint") != run.run_fingerprint):
            raise HeadConflict("response does not match its reserved request")
        put_rows(store, run, DocumentRunRequest, "calls:requests", {
            request_key: {
                **request, "dispatch_state": "completed", "actual_cost": 1,
                "cost_status": "measured", "result_ref": result_ref,
            },
        })
    return len(newly_completed)



def _validate_result_changes(protocols, changes):
    try:
        if not isinstance(changes, dict):
            raise ValueError("result changes must be a mapping")
        for ref, result in changes.items():
            required = {"lineage_id", "field", "value"}
            protocol = protocols.get(result["lineage_id"], {})
            batch = protocol.get("version") == TOOL_BATCH_PROTOCOL_VERSION
            owner = result.get("member_task_id")
            if batch and result["field"] in {"discovery", "verification", "outcome"}:
                required |= {"member_task_id", "result_version"}
                if owner not in protocol["member_states"]:
                    raise ValueError("batch result owner is not a member")
                validate_member_result_version(result.get("result_version"))
            if (not isinstance(result, dict) or set(result) != required
                    or protocol.get("version") not in {TOOL_PROTOCOL_VERSION,
                                                       TOOL_BATCH_PROTOCOL_VERSION,
                                                       RECORD_PROTOCOL}):
                raise ValueError("result change has no current Responses protocol")
            validate_protocol_result(result["field"], result["value"])
            if ref != protocol_result_ref(
                result["lineage_id"], result["field"], result["value"], member_task_id=owner,
                result_version=result.get("result_version"),
            ):
                raise ValueError("result change identity is invalid")
    except (ValueError, TypeError, KeyError) as exc:
        raise HeadConflict("invalid protocol result changes") from exc


def persist_calls(store, run, token, fingerprint, state):
    from app.services.document_analysis.execution import _publication, _validate_model_call_state

    _validate_model_call_state(
        {
            "version": state["version"],
            "recognition_run_id": state["recognition_run_id"],
            "run_fingerprint": state["run_fingerprint"],
            "reservations": [],
            "lineage_calls": {r["key"]: r["value"] for r in state["lineage_calls"].values()},
            **(
                {"protocols": {r["key"]: r["value"] for r in state["protocols"].values()}}
                if state["version"] in (2, 3)
                else {}
            ),
        },
        check_paid_prefix=False,  # Changed requests are checked against their persisted rows below.
    )
    if state.get("run_fingerprint") != fingerprint or state.get("recognition_run_id") != str(
        run.recognition_run_id
    ):
        raise HeadConflict("request run identity mismatch")
    result_changes = state.get("result_changes", {})
    changed_protocols = {row["key"]: row["value"] for row in state["protocols"].values()}
    _validate_result_changes(changed_protocols, result_changes)
    with _publication(store.db, store):
        current = lock_current(store, run, token, fingerprint)
        record_authorities = {
            lineage: get_row(store, current, "work:record_discovery",
                             json.dumps(lineage, ensure_ascii=False, separators=(",", ":")))
            for lineage, protocol in changed_protocols.items()
            if protocol.get("version") == RECORD_PROTOCOL
            and protocol.get("record_feedback_hash") is not None
        }
        if state.get("work_changes"):
            if state["reservations"] or state.get("result_changes"):
                raise HeadConflict("initial work-unit boundary cannot contain paid results")
            current = write_work(
                store, current, token, state["work_changes"], sequence=current.event_head or None,
                expected_version=state["expected_work_version"], fingerprint=fingerprint,
            )
        prior_control = get_row(store, current, "calls:control") or {}
        sequence = prior_control.get("reservation_sequence", 0)
        if state["reservation_sequence"] < sequence:
            raise HeadConflict("request watermark regressed")
        fresh = [r for r in state["reservations"] if r["sequence"] > sequence]
        if [r["sequence"] for r in fresh] != list(
            range(sequence + 1, state["reservation_sequence"] + 1)
        ):
            raise HeadConflict("request reservation gap")
        for receipt in state["reservations"]:
            request_key = call_request_key(receipt)
            prior = get_row(store, current, "calls:requests", request_key, model=DocumentRunRequest)
            if prior is not None:
                if prior["reservation"] != receipt:
                    raise HeadConflict("request reservation identity changed")
                continue
            if receipt["sequence"] <= sequence:
                raise HeadConflict("committed request reservation is missing")
            protocol = changed_protocols.get(receipt["lineage_id"])
            if protocol is None:
                saved = get_row(
                    store, current, "calls:protocols", receipt["lineage_id"],
                    model=DocumentRunRequest,
                )
                protocol = saved["value"] if saved else None
            if protocol and protocol.get("version") == TOOL_BATCH_PROTOCOL_VERSION:
                from app.services.document_analysis.batch_current_state import check_receipt

                check_receipt(protocol, receipt, fingerprint)
            if "record_task_id" in receipt:
                if protocol is None or protocol.get("version") != RECORD_PROTOCOL:
                    raise HeadConflict("record reservation has no record protocol")
                check_record_receipt(protocol, receipt, fingerprint)
            if protocol and protocol.get("version") == TOOL_PROTOCOL_VERSION:
                pending = protocol["pending_request"]
                if (pending is None or pending["reservation_key"] != request_key
                        or receipt.get("input_hash") != pending["request_hash"]
                        or receipt["stage"].removeprefix("ontology_guided_") != pending["stage"]
                        or receipt.get("run_fingerprint") != fingerprint):
                    raise HeadConflict("Responses reservation does not match its pending request")
            put_rows(
                store,
                current,
                DocumentRunRequest,
                "calls:requests",
                {
                    request_key: {
                        "reservation": receipt,
                        "dispatch_state": "dispatch_claimed",
                        "reserved_cost": 1,
                        "actual_cost": None,
                        "cost_status": "unknown",
                        "result_ref": None,
                    },
                },
            )
        store.db.flush()
        progress = dict(current.progress or {})
        reserved_delta = len(fresh) if state["version"] == 3 else 0
        confirmed_delta = 0
        for domain in (("protocols",) if state["version"] == 3 else ("lineage_calls", "protocols")):
            for key, row in state[domain].items():
                prior = get_row(store, current, "calls:" + domain, key, model=DocumentRunRequest)
                if domain == "lineage_calls":
                    if prior and row["value"] < prior["value"]:
                        raise HeadConflict("reserved model costs regressed")
                    reserved_delta += row["value"] - (prior["value"] if prior else 0)
                if domain == "protocols":
                    protocol = row["value"]
                    prior = hydrate_protocol(store, current, prior) if prior else None
                    targets = ([member["base_target"]
                                for member in protocol["member_states"].values()]
                               if protocol.get("version") == TOOL_BATCH_PROTOCOL_VERSION
                               else [protocol["base_target"]])
                    if any(target["document_context"]["document_hash"] != run.document_hash
                           for target in targets):
                        raise HeadConflict("protocol source mismatch")
                    if prior and protocol.get("version") != prior["value"].get("version"):
                        raise HeadConflict("frozen protocol version cannot change")
                    if protocol.get("version") == TOOL_BATCH_PROTOCOL_VERSION:
                        from app.services.document_analysis.batch_current_state import (
                            check_receipt,
                            persist_protocol,
                        )

                        pending = protocol["pending_request"]
                        if pending is not None:
                            request = get_row(store, current, "calls:requests",
                                              pending["reservation_key"], model=DocumentRunRequest)
                            if request is None:
                                raise HeadConflict("pending batch request has no reservation")
                            check_receipt(protocol, request["reservation"], fingerprint)
                        confirmed_delta += persist_protocol(
                            store, current, protocol, prior["value"] if prior else None,
                            result_changes,
                        )
                        put_rows(store, current, DocumentRunRequest, "calls:" + domain, {key: row})
                        continue
                    if protocol.get("version") in {TOOL_PROTOCOL_VERSION, RECORD_PROTOCOL}:
                        pending = protocol["pending_request"]
                        if pending is not None:
                            request = get_row(
                                store, current, "calls:requests", pending["reservation_key"],
                                model=DocumentRunRequest,
                            )
                            receipt = request["reservation"] if request else None
                            if (receipt is None or receipt["lineage_id"] != row["key"]
                                    or receipt.get("protocol_attempt") != pending["attempt"]
                                    or receipt.get("input_hash") != pending["request_hash"]
                                    or receipt["stage"].removeprefix("ontology_guided_")
                                    != pending["stage"]):
                                raise HeadConflict("pending Responses request has no reservation")
                            if protocol["version"] == RECORD_PROTOCOL:
                                check_record_receipt(protocol, receipt, fingerprint)
                        confirmed_delta += _persist_tool_protocol(
                            store, current, protocol, prior["value"] if prior else None,
                            result_changes, record_authority=record_authorities.get(row["key"]),
                        )
                        put_rows(store, current, DocumentRunRequest, "calls:" + domain, {key: row})
                        continue
                    if prior and (
                        protocol.get("request_attempt", 0)
                        < prior["value"].get("request_attempt", 0)
                    ):
                        raise HeadConflict("protocol attempt regressed")
                    if prior:
                        old = prior["value"]
                        if protocol.get("evidence_revision", 0) < old.get(
                            "evidence_revision", 0
                        ) or protocol.get("completed_attempts", [])[
                            : len(old.get("completed_attempts", []))
                        ] != old.get("completed_attempts", []):
                            raise HeadConflict("protocol evidence or results regressed")
                        if protocol["evidence_revision"] == old[
                            "evidence_revision"
                        ] and protocol.get("assertion_generation", 0) == old.get(
                            "assertion_generation", 0
                        ):
                            for field in (
                                "evidence_hash",
                                "base_target",
                                "discovery",
                                "verification",
                            ):
                                if old.get(field) is not None and protocol.get(field) != old[field]:
                                    raise HeadConflict("committed protocol response changed")
                    confirmed_delta += len(protocol.get("completed_attempts", [])) - len(
                        (prior or {}).get("value", {}).get("completed_attempts", [])
                    )
                    compact = dict(protocol)
                    for field in ("base_target", "discovery", "verification", "outcome"):
                        value = compact.pop(field, None)
                        if value is not None:
                            result_key = content_hash([row["key"], field, value])
                            put_rows(
                                store,
                                current,
                                DocumentRunResult,
                                "calls:results",
                                {
                                    result_key: {
                                        "lineage_id": row["key"],
                                        "field": field,
                                        "value": value,
                                    }
                                },
                                immutable=True,
                            )
                            compact[field + "_ref"] = result_key
                    completed = protocol.get("completed_attempts", [])
                    old_completed = (prior or {}).get("value", {}).get("completed_attempts", [])
                    for attempt in completed[len(old_completed) :]:
                        request_key = call_request_key(
                            {
                                "lineage_id": row["key"],
                                "protocol_attempt": attempt,
                            }
                        )
                        request = get_row(
                            store, current, "calls:requests", request_key, model=DocumentRunRequest
                        )
                        if request is None:
                            raise HeadConflict("completed response has no reserved request")
                        stage = request["reservation"]["stage"].removeprefix("ontology_guided_")
                        result_ref = compact.get(stage + "_ref")
                        if result_ref is None:
                            raise HeadConflict("completed request has no exact result")
                        put_rows(
                            store,
                            current,
                            DocumentRunRequest,
                            "calls:requests",
                            {
                                request_key: {
                                    **request,
                                    "dispatch_state": "completed",
                                    "actual_cost": 1,
                                    "cost_status": "measured",
                                    "result_ref": result_ref,
                                },
                            },
                        )
                    row = {**row, "value": compact}
                put_rows(store, current, DocumentRunRequest, "calls:" + domain, {key: row})
        progress["model_calls_reserved"] = progress.get("model_calls_reserved", 0) + reserved_delta
        if state["version"] in (2, 3):
            progress["model_calls"] = progress.get("model_calls", 0) + confirmed_delta
        progress["model_calls_unresolved"] = max(
            0, progress["model_calls_reserved"] - progress.get("model_calls", 0)
        )
        if confirmed_delta:
            # Split verification can save several paid answers before any graph
            # event is published. Each new durable answer is execution progress;
            # reservations, heartbeats and repeated receipts are not.
            execution = store.assert_fence(current.recognition_run_id, current.owner_id, token)
            execution.last_progress_at = store._clock()
            execution.recovery_event_head = current.event_head
            execution.recovery_attempts = 0
        current.progress = progress
        current.revision += 1
        current.request_version += 1
        put_rows(
            store,
            current,
            DocumentRunCurrentState,
            "calls:control",
            {
                "current": {
                    "version": state["version"],
                    "recognition_run_id": str(run.recognition_run_id),
                    "run_fingerprint": fingerprint,
                    "reservation_sequence": state["reservation_sequence"],
                }
            },
            work_version=current.work_version,
        )
    return current.work_version


def hydrate_protocol(store, run, row):
    row = deepcopy(row)
    protocol = row["value"]
    if protocol.get("version") == TOOL_BATCH_PROTOCOL_VERSION:
        from app.services.document_analysis.batch_current_state import protocol_results

        try:
            validate_batch_tool_protocol(protocol)
        except (TypeError, ValueError, KeyError) as exc:
            raise HeadConflict("stored current batch protocol is invalid") from exc
        protocol_results(store, run, protocol)
        return row
    if protocol.get("version") in {TOOL_PROTOCOL_VERSION, RECORD_PROTOCOL}:
        try:
            validate_tool_protocol(protocol)
        except (TypeError, ValueError) as exc:
            raise HeadConflict("stored current tool protocol is invalid") from exc
        _tool_protocol_results(store, run, protocol)
        return row
    for field in ("base_target", "discovery", "verification", "outcome"):
        ref = protocol.pop(field + "_ref", None)
        if ref is not None:
            result = get_row(store, run, "calls:results", ref, model=DocumentRunResult)
            if result is None or result["lineage_id"] != row["key"] or result["field"] != field:
                raise HeadConflict("protocol result reference mismatch")
            protocol[field] = result["value"]
    return row


def restore_calls(store, run, fingerprint):
    control = get_row(store, run, "calls:control")
    if control is None:
        return {}
    if control["run_fingerprint"] != fingerprint:
        raise HeadConflict("request fingerprint mismatch")
    result = {**control, "reservations": [], "lineage_calls": {}, "protocols": {}}
    for domain in ("lineage_calls", "protocols"):
        rows = read_rows(store, run, DocumentRunRequest, prefix="calls:" + domain)
        result[domain] = {
            row["key"]: (
                hydrate_protocol(store, run, row)["value"]
                if domain == "protocols"
                else row["value"]
            )
            for row in rows.get("calls:" + domain, {}).values()
        }
    if result["version"] == 3:
        requests = read_rows(store, run, DocumentRunRequest, prefix="calls:requests")
        receipts = sorted(
            (row["reservation"] for row in requests.get("calls:requests", {}).values()),
            key=lambda receipt: receipt["sequence"],
        )
        if [receipt["sequence"] for receipt in receipts] != list(
            range(1, result["reservation_sequence"] + 1)
        ):
            raise HeadConflict("stored physical reservation sequence is invalid")
        result["reservations"] = receipts
        result["lineage_calls"] = {}
        for receipt in receipts:
            lineages = ([receipt["lineage_id"]] if "record_task_id" in receipt
                        else receipt["member_lineage_ids"])
            for lineage in lineages:
                result["lineage_calls"][lineage] = result["lineage_calls"].get(lineage, 0) + 1
        from app.services.document_analysis.execution import _validate_model_call_state

        _validate_model_call_state({key: result[key] for key in (
            "version", "recognition_run_id", "run_fingerprint", "reservations",
            "lineage_calls", "protocols",
        )})
    return result


RANKING_RESULTS = {
    "epochs",
    "paused_attempts",
    "cache",
    "score_cache",
    "gate_evaluations",
    "admission_decisions",
    "adaptive_inputs",
    "model_observations",
}
RANKING_REQUESTS = {
    "slot_costs",
    "record_call_counts",
    "record_intent_call_counts",
    "request_attempts",
    "dispatch_receipts",
    "adaptive_requests",
}


def persist_ranking(store, run, token, fingerprint, state):
    from app.services.document_analysis.execution import _publication

    if state.get("run_fingerprint") != fingerprint or state.get("recognition_run_id") != str(
        run.recognition_run_id
    ):
        raise HeadConflict("ranking run identity mismatch")
    with _publication(store.db, store):
        current = lock_current(store, run, token, fingerprint)
        for name, values in {
            **state["changes"],
            "committed_at": state["committed_at"],
            "discarded_epochs": state["discarded_epochs"],
        }.items():
            model = (
                DocumentRunResult
                if name in RANKING_RESULTS
                else DocumentRunRequest
                if name in RANKING_REQUESTS
                else DocumentRunCurrentState
            )
            if name == "control":
                prior = get_row(store, current, "ranking:control")
                control = values["current"]
                if prior and (
                    any(prior[k] != control[k] for k in ("policy", "model_identity"))
                    or any(control["costs"].get(k, 0) < v for k, v in prior["costs"].items())
                ):
                    raise HeadConflict("ranking identity or costs regressed")
            put_rows(
                store,
                current,
                model,
                "ranking:" + name,
                values,
                immutable=model is DocumentRunResult,
                work_version=current.work_version if model is DocumentRunCurrentState else None,
            )

        publish_ranking_summary(store, current, state)
        current.ranking_version += 1
        current.revision += 1


def publish_ranking_summary(store, run, state):
    from app.services.document_analysis.public_projection import public_ranking_payload

    control = state["changes"]["control"]["current"]
    epochs = [r["value"] for r in state["changes"].get("epochs", {}).values()]
    pending = control.get("pending_epochs", [])
    observations = [r["value"] for r in state["changes"].get("model_observations", {}).values()]
    summary = public_ranking_payload(
        {"service": {**control, "epochs": epochs, "model_observations": observations}}
    )
    prior = get_row(store, run, "display:ranking_summary") or {}
    metrics = get_row(store, run, "ranking:summary_metrics") or {"measured": 0, "pending": []}
    measured = sum(
        r.get("operation") in {"embed", "score_pairs"} and r.get("input_tokens") is not None
        for r in observations
    )
    values = {item["epoch_id"]: item for item in summary["epochs"]}
    for identity in metrics["pending"]:
        if identity not in values:
            values[identity] = None
    put_rows(
        store,
        run,
        DocumentRunCurrentState,
        "display:ranking_epochs",
        values,
        work_version=run.work_version,
    )
    for field in ("observed_requests", "measured_input_tokens", "queue_seconds"):
        summary["cost"][field] += prior.get("cost", {}).get(field, 0)
    summary["cost"]["unknown_request_count"] = max(
        0, control["costs"].get("model_calls", 0) - metrics["measured"] - measured
    )
    summary.pop("epochs")
    put_rows(
        store,
        run,
        DocumentRunCurrentState,
        "display:ranking_summary",
        {"current": summary},
        work_version=run.work_version,
    )
    put_rows(
        store,
        run,
        DocumentRunCurrentState,
        "ranking:summary_metrics",
        {
            "current": {
                "measured": metrics["measured"] + measured,
                "pending": [p["epoch_id"] for p in pending if p.get("status") == "paused"],
            }
        },
        work_version=run.work_version,
    )
    manifest = dict(run.artifact_manifest or {})
    manifest["ranking_summary"] = {
        "artifact_id": content_hash(summary),
        "content_hash": content_hash(summary),
        "revision": run.revision,
        "status": "ready",
    }
    run.artifact_manifest = manifest


def read_ranking_summary(store, run):
    run = lock_read(store, run)
    try:
        summary = get_row(store, run, "display:ranking_summary")
        epochs = list(
            read_rows(store, run, DocumentRunCurrentState, prefix="display:ranking_epochs")
            .get("display:ranking_epochs", {})
            .values()
        )
    except HeadConflict:
        summary = None
    if summary is None:
        from app.services.document_analysis.public_projection import public_ranking_payload

        return public_ranking_payload(restore_ranking(store, run, run.run_fingerprint))
    return {
        **summary,
        "epochs": epochs,
        "committed_epochs": sum(e["status"] == "committed" for e in epochs),
        "actual_modes": sorted({e["actual_mode"] for e in epochs if e["status"] == "committed"}),
        "degraded": any(e["degraded"] for e in epochs),
        "paused": any(e["status"] == "paused" for e in epochs),
        "reasons": sorted({e["reason"] for e in epochs if e["reason"]}),
    }


def restore_ranking(store, run, fingerprint):
    control = get_row(store, run, "ranking:control")
    if control is None:
        return {}
    parts = {}
    for model in (DocumentRunCurrentState, DocumentRunResult, DocumentRunRequest):
        parts.update(read_rows(store, run, model, prefix="ranking:"))
    service = dict(control)
    lists = {"epochs", "paused_attempts", "model_observations", "dispatch_receipts"}
    for name in RANKING_RESULTS | RANKING_REQUESTS | {"token_cache", "preparation_results"}:
        service[name] = [] if name in lists else {}
    for domain, values in parts.items():
        name = domain.removeprefix("ranking:")
        if name in {"control", "committed_at", "discarded_epochs", "summary_metrics"}:
            continue
        service[name] = (
            [row["value"] for _, row in sorted(values.items(), key=lambda i: int(i[0]))]
            if name in lists
            else {row["key"]: row["value"] for row in values.values()}
        )
    return {
        "recognition_run_id": str(run.recognition_run_id),
        "run_fingerprint": fingerprint,
        "service": service,
        "committed_at": {
            r["key"]: r["value"] for r in parts.get("ranking:committed_at", {}).values()
        },
        "discarded_epochs": [
            r["value"]
            for _, r in sorted(
                parts.get("ranking:discarded_epochs", {}).items(), key=lambda i: int(i[0])
            )
        ],
    }


def read_display(store, run, kind="public_graph"):
    run = lock_read(store, run)
    try:
        header = get_row(store, run, "display:public_graph")
    except HeadConflict:
        return rebuild_display(store, run)
    if header is None:
        return rebuild_display(store, run)
    if header.get("partitioned") != 1:
        return header
    try:
        rows = read_rows(store, run, DocumentRunCurrentState, prefix="display:")
    except HeadConflict:
        return rebuild_display(store, run)
    if any(
        len(rows.get("display:" + name, {})) != count
        for name, count in header.get("member_counts", {}).items()
    ):
        return rebuild_display(store, run)
    graph = dict(header["graph"])
    fields = ("nodes", "edges", "properties", "attribute_candidates") + (
        ("relationship_groups",) if "relationship_groups" in header.get("member_counts", {}) else ()
    )
    for field in fields:
        graph[field] = [
            r["value"]
            for r in sorted(rows.get("display:" + field, {}).values(), key=lambda r: r["position"])
        ]
    graph["coverage"] = [
        r["value"]
        for r in sorted(
            read_rows(store, run, DocumentRunCurrentState, prefix="work:coverage")
            .get("work:coverage", {})
            .values(),
            key=lambda r: r["position"],
        )
    ]
    selections, menus = {}, {}
    for row in rows.get("display:selections", {}).values():
        selections.update(row)
    menus.update(rows.get("display:menus", {}))
    from app.services.extraction.ontology_guided.dependencies import DependencyIndex

    work = {
        name.removeprefix("work:"): values
        for name, values in read_rows(
            store, run, DocumentRunCurrentState, prefix="work:dependencies:"
        ).items()
    }
    return {
        k: v
        for k, v in {
            **header,
            "graph": graph,
            "selection_registry": selections,
            "predicate_menus": menus,
            "dependency_index": DependencyIndex.from_current(work).snapshot(),
        }.items()
        if k not in {"partitioned", "member_counts"}
    }


def display_payload(
    store, run, *, ontology, ir, metadata, index, graph, dependencies, summary, changes=None
):
    from datetime import UTC, datetime

    from app.services.document_analysis.public_projection import build_selection_registry
    from app.services.extraction.evidence_identity import stable_id
    from app.services.extraction.ontology_guided.contracts import SubjectRef
    from app.services.extraction.ontology_guided.ontology_plan import compile_local_menu
    from app.services.extraction.ontology_guided.projection import (
        TOOL_EXTRACTION_PROTOCOL,
        TOOL_PROJECTION_POLICY,
    )

    snapshot_id = stable_id(
        "current-graph", [str(run.recognition_run_id), graph.generated_from_hash, graph.event_head]
    )
    cache = getattr(store, "_current_display_members", None)
    if cache is None or cache[0] != str(run.recognition_run_id):
        cache = (
            str(run.recognition_run_id),
            {
                name: set(values)
                for name, values in read_rows(
                    store, run, DocumentRunCurrentState, prefix="display:"
                ).items()
            },
        )
    members = deepcopy(cache[1])
    updates = {}
    tool_protocol = graph.projection_policy == TOOL_PROJECTION_POLICY
    fields = ("nodes", "edges", "properties", "attribute_candidates") + (
        ("relationship_groups",) if tool_protocol else ()
    )
    for field in fields:
        values = {getattr(v, "entity_id", None) or v.candidate_id: v for v in getattr(graph, field)}
        prior = members.get("display:" + field, set())
        if field == "attribute_candidates" and changes is not None:
            touched = {
                candidate["candidate_id"]
                for row in changes.get("record_discovery", {}).values() if row
                for candidate in row["value"].get("attribute_candidates", [])
            }
        else:
            touched = (
                {r["key"] for r in changes.get(field, {}).values() if r}
                if changes is not None else set(values)
            )
        touched |= set(values) - prior
        selected = {key: values[key] for key in touched if key in values}
        positions = {key: i for i, key in enumerate(values)}
        updates[field] = {
            key: {"position": positions[key], "value": value.model_dump(mode="json")}
            for key, value in selected.items()
        }
        updates[field].update({key: None for key in prior - set(values)})
        members["display:" + field] = set(values)
        selections = updates.setdefault("selections", {})
        for key, value in selected.items():
            partial = graph.model_copy(
                update={
                    name: [value] if name == field else []
                    for name in fields
                }
            )
            selections[field + ":" + key] = build_selection_registry(
                recognition_run_id=str(run.recognition_run_id),
                analysis_id=ir.analysis_id,
                graph=partial,
                index=index,
            )
        for key in prior - set(values):
            selections[field + ":" + key] = None
        if field == "nodes":
            menus = updates.setdefault("menus", {})
            class_menus = getattr(store, "_current_class_menus", {})
            for key, value in selected.items():
                menu_key = (ontology.snapshot_id, value.class_iri, value.root)
                if value.class_iri not in ontology.classes:
                    continue
                if menu_key not in class_menus:
                    menu = compile_local_menu(
                        ontology,
                        SubjectRef(
                            entity_id=value.entity_id,
                            revision=value.revision,
                            class_iri=value.class_iri,
                            is_document_root=value.root,
                        ),
                    )
                    class_menus[menu_key] = [
                        {"predicate_iri": p.iri, "predicate_label": p.label, "kind": p.kind}
                        for p in [*menu.relationships, *menu.properties]
                    ]
                menus[key] = class_menus[menu_key]
            store._current_class_menus = class_menus
            menus.update({key: None for key in prior - set(values)})
    slots = (
        {
            tuple(r["key"])
            for name in ("plans", "searches")
            for r in changes.get(name, {}).values()
            if r
        }
        if changes is not None
        else None
    )
    def coverage_key(value):
        return (value.subject_ref.id, value.subject_ref.revision,
                *((value.scope.scope_id,) if value.scope is not None else ()), value.predicate_iri)

    updates["coverage"] = {
        content_hash(list(coverage_key(v))): {
            "position": i,
            "value": v.model_dump(mode="json"),
        }
        for i, v in enumerate(graph.coverage)
        if slots is None or coverage_key(v) in slots
    }
    if changes is not None:
        updates["coverage"].update(
            {
                content_hash(json.loads(key)): None
                for key, row in changes.get("plans", {}).items()
                if row is None
            }
        )
    header = {
        "partitioned": 1,
        **({"extraction_protocol": TOOL_EXTRACTION_PROTOCOL} if tool_protocol else {}),
        "snapshot_id": snapshot_id,
        "analysis_id": ir.analysis_id,
        "member_counts": {
            name: len(members.get("display:" + name, ()))
            for name in fields
        },
        "ontology_snapshot_id": ontology.snapshot_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "graph": graph.model_dump(
            mode="json", exclude={*fields, "coverage"}
        ),
        "evidence_repair": summary,
    }
    updates["public_graph"] = {"current": header}
    return snapshot_id, (updates, members)


def publish_display(store, run, prepared, snapshot_id):
    updates, members = prepared
    for kind, values in updates.items():
        put_rows(
            store,
            run,
            DocumentRunCurrentState,
            ("work:" if kind == "coverage" else "display:") + kind,
            values,
            work_version=run.work_version,
        )
    put_rows(
        store,
        run,
        DocumentRunCurrentState,
        "work:projection",
        {"current": updates["public_graph"]["current"]},
        work_version=run.work_version,
    )
    run.graph_snapshot_id = snapshot_id
    manifest = dict(run.artifact_manifest or {})
    manifest["public_graph"] = {
        "artifact_id": snapshot_id,
        "content_hash": content_hash(updates["public_graph"]),
        "revision": run.work_version,
        "status": "ready",
        "event_head": run.event_head,
    }
    manifest["graph"] = dict(manifest["public_graph"])
    manifest["graph"]["status"] = updates["public_graph"]["current"]["graph"]["artifact_status"]
    manifest["source_selections"] = dict(manifest["public_graph"])
    if "ranking_summary" not in manifest:
        from app.services.document_analysis.public_projection import public_ranking_payload

        summary = public_ranking_payload({})
        put_rows(
            store,
            run,
            DocumentRunCurrentState,
            "display:ranking_summary",
            {"current": summary},
            work_version=run.work_version,
        )
        manifest["ranking_summary"] = {
            "artifact_id": content_hash(summary),
            "content_hash": content_hash(summary),
            "status": "ready",
        }
    run.artifact_manifest = manifest
    store._current_display_members = (str(run.recognition_run_id), members)


def persist_boundary(store, run, token, *, changes, fingerprint, expected_version):
    from app.services.document_analysis.execution import _publication

    with _publication(store.db, store):
        current = write_work(
            store,
            run,
            token,
            changes,
            sequence=run.event_head or None,
            expected_version=expected_version,
            fingerprint=fingerprint,
        )
    return current.work_version


def persist_batch(
    store, run, token, *, batch, fingerprint, ontology, ir, metadata, index, expected_version
):
    from app.services.document_analysis.execution import _publication
    from app.services.document_analysis.reviews import mark_repair_operation

    unit = getattr(batch, "work_unit", None)
    record_task = getattr(batch.task, "kind", None) == "record_discovery"
    member_outcomes = getattr(batch, "member_outcomes", {})
    protocols = getattr(batch, "protocol_state_changes", {})
    results = getattr(batch, "protocol_result_changes", {})
    record_review_boundary = (
        record_task and batch.outcome.reason_code == "expert_review_boundary"
    )
    if record_review_boundary and (
        unit is not None or member_outcomes or protocols or results
        or getattr(batch, "model_call_state", {}) or getattr(batch, "task_outcomes", [])
        or batch.outcome.semantic_outcome != "not_checked" or batch.outcome.complete
        or any(getattr(batch.outcome, field) for field in (
            "model_calls", "controller_checks", "nodes", "edges", "properties",
            "relationship_groups", "proof_payloads", "decision_payloads", "reference_resolutions",
        ))
    ):
        raise HeadConflict("record review boundary cannot carry recognition changes")
    if unit is not None:
        tasks = {task.task_id: task for task in unit.members}
        if not member_outcomes or set(member_outcomes) - set(tasks):
            raise HeadConflict("batch publication requires explicit member outcomes")
        event_results = {
            "work_unit": unit.model_dump(mode="json"),
            "member_outcomes": {key: outcome.model_dump(mode="json")
                                for key, outcome in member_outcomes.items()},
        }
        outcomes = list(member_outcomes.values())
    else:
        event_results = {"task": batch.task.model_dump(mode="json"),
                         "outcome": batch.outcome.model_dump(mode="json")}
        outcomes = [batch.outcome]
    digest = content_hash({"fingerprint": fingerprint, **event_results,
                           "changes": batch.work_changes,
                           **({"protocols": protocols, "results": results}
                              if unit is not None or record_task else {})})
    receipt = store.event_batch_receipt(
        run.recognition_run_id, run.owner_id, token, batch_id=batch.batch_id, batch_hash=digest
    )
    if receipt:
        return receipt.committed_work_version
    # Prepare the projection outside the lock. Public revision changes are merged below.
    graph = batch.graph.model_copy(
        update={"run_revision": run.revision, "event_head": run.event_head + 1}
    )
    snapshot_id, reads = display_payload(
        store,
        run,
        ontology=ontology,
        ir=ir,
        metadata=metadata,
        index=index,
        graph=graph,
        dependencies=batch.dependency_index,
        summary=batch.evidence_repair_summary,
        changes=batch.work_changes,
    )
    with _publication(store.db, store):
        current = lock_current(store, run, token, fingerprint)
        if current.work_version != expected_version:
            raise HeadConflict("current work version changed during batch preparation")
        from app.services.extraction.ontology_guided.projection import TOOL_PROJECTION_POLICY

        identity_nodes = []
        if unit is not None:
            from app.services.document_analysis.batch_current_state import persist_protocol

            _validate_result_changes(protocols, results)
            for unit_id, protocol in protocols.items():
                if (unit_id != unit.work_unit_id
                        or protocol["work_unit"] != unit.model_dump(mode="json")):
                    raise HeadConflict("publication protocol belongs to another work unit")
                key = json.dumps(unit_id, ensure_ascii=False, separators=(",", ":"))
                saved = get_row(store, current, "calls:protocols", key, model=DocumentRunRequest)
                if saved is None:
                    raise HeadConflict("batch publication has no current unit")
                prior = hydrate_protocol(store, current, saved)["value"]
                if (protocol["request_attempt"] != prior["request_attempt"]
                        or protocol["completed_attempts"] != prior["completed_attempts"]
                        or protocol["pending_request"] != prior["pending_request"]):
                    raise HeadConflict("publication cannot reserve or confirm model requests")
                persist_protocol(store, current, protocol, prior, results)
                put_rows(store, current, DocumentRunRequest, "calls:protocols",
                         {key: {**saved, "value": protocol}})
            store.db.flush()
            unit_key = json.dumps(unit.work_unit_id, ensure_ascii=False, separators=(",", ":"))
            saved = get_row(store, current, "calls:protocols", unit_key, model=DocumentRunRequest)
            protocol = saved["value"] if saved else {}
            for task_id, outcome in member_outcomes.items():
                ref = protocol.get("outcome_refs", {}).get(task_id)
                if ref is None:
                    raise HeadConflict("published batch member has no finalized owned result")
                finalized = load_protocol_result(store, current, unit.work_unit_id, ref, "outcome",
                                                 member_task_id=task_id)
                if finalized != outcome.model_dump(mode="json"):
                    raise HeadConflict("published batch outcome differs from its finalized result")
                identity_nodes.extend(node for node in finalized["nodes"]
                                      if node.get("identity_status") == "verified")
        elif record_task:
            lineage = batch.task.claim_lineage_id
            task_value = batch.task.model_dump(mode="json")
            _validate_result_changes(protocols, results)
            if set(protocols) - {lineage}:
                raise HeadConflict("record publication has a foreign protocol")
            key = json.dumps(lineage, ensure_ascii=False, separators=(",", ":"))
            saved = get_row(store, current, "calls:protocols", key, model=DocumentRunRequest)
            if saved is None:
                raise HeadConflict("record publication has no current protocol")
            prior = hydrate_protocol(store, current, saved)["value"]
            if prior.get("version") != RECORD_PROTOCOL or prior.get("task") != task_value:
                raise HeadConflict("record publication protocol belongs to another task")
            protocol = protocols.get(lineage, prior)
            try:
                validate_tool_protocol(protocol)
            except (TypeError, ValueError, KeyError) as exc:
                raise HeadConflict("record publication protocol is invalid") from exc
            if (protocol.get("version") != RECORD_PROTOCOL or protocol.get("task") != task_value
                    or any(protocol[field] != prior[field] for field in (
                        "request_attempt", "completed_attempts", "pending_request",
                    ))):
                raise HeadConflict("record publication cannot change request ownership or receipts")
            _persist_tool_protocol(store, current, protocol, prior, results)
            put_rows(store, current, DocumentRunRequest, "calls:protocols",
                     {key: {**saved, "value": protocol}})
            ref = protocol.get("outcome_ref")
            if ref is None:
                raise HeadConflict("published record has no finalized owned result")
            finalized = load_protocol_result(store, current, lineage, ref, "outcome")
            if record_review_boundary and protocol["pending_request"] is not None:
                raise HeadConflict("record review boundary has an unresolved model request")
            if not record_review_boundary and finalized != batch.outcome.model_dump(mode="json"):
                raise HeadConflict("published record outcome differs from its finalized result")
            if not record_review_boundary:
                identity_nodes = [node for node in finalized["nodes"]
                                  if node.get("identity_status") == "verified"]
        elif (graph.projection_policy == TOOL_PROJECTION_POLICY
                and any(node.identity_status == "verified" for node in batch.outcome.nodes)):
            saved = get_row(
                store, current, "calls:protocols",
                json.dumps(batch.task.claim_lineage_id, ensure_ascii=False, separators=(",", ":")),
                model=DocumentRunRequest,
            )
            protocol = saved["value"] if saved else {}
            if protocol.get("version") == TOOL_PROTOCOL_VERSION and protocol.get("outcome_ref"):
                finalized = load_protocol_result(
                    store, current, batch.task.claim_lineage_id, protocol["outcome_ref"], "outcome",
                )
                accepted = [node.model_dump(mode="json") for node in batch.outcome.nodes
                            if node.identity_status == "verified"]
                identity_nodes = [node for node in finalized["nodes"] if node in accepted]
        event = store.append_event(
            current.recognition_run_id,
            current.owner_id,
            token,
            expected_head=current.event_head,
            event_key="recognition-batch:" + batch.batch_id,
            event_type="progress",
            payload={
                "contract_version": current.contract_version,
                "recognition_run_id": str(current.recognition_run_id),
                "run_revision": current.revision + 1,
                "event_head": current.event_head + 1,
                "artifact_revision": current.artifact_revision,
                "status": "running",
                "stage": "extracting",
                "progress": graph.progress.model_dump(mode="json"),
                **event_results,
            },
        )
        for outcome in outcomes:
            write_proofs(store, current, token, outcome, event.sequence)
        current = write_work(
            store,
            current,
            token,
            batch.work_changes,
            sequence=event.sequence,
            expected_version=expected_version,
            fingerprint=fingerprint,
            identity_nodes=identity_nodes,
        )
        body = reads[0]["public_graph"]["current"]
        body["graph"]["run_revision"] = current.revision
        body["graph"]["event_head"] = event.sequence
        publish_display(store, current, reads, snapshot_id)
        current.progress = graph.progress.model_dump(mode="json")
        current.work_batch_id = batch.batch_id
        store.db.flush()
        for operation_id, operation in (
            batch.evidence_repair_summary.get("expert_review", {}).get("operations", {}).items()
        ):
            mark_repair_operation(
                store.db,
                current.recognition_run_id,
                current.owner_id,
                token,
                operation_id,
                operation["status"],
                operation["result"],
            )
        store.put_event_batch(
            current.recognition_run_id,
            current.owner_id,
            token,
            batch_id=batch.batch_id,
            batch_hash=digest,
            first_sequence=event.sequence,
            last_sequence=event.sequence,
            committed_work_version=current.work_version,
        )
    return current.work_version


def rebuild_display(store, run):
    from app.models.document_analysis import DocumentAnalysisArtifact
    from app.schemas.attribute_calibration import AttributeCalibrationCandidate
    from app.services.document_analysis.state_artifacts import decode_state
    from app.services.extraction.document_ir import DocumentIR
    from app.services.extraction.ontology_guided.contracts import (
        CoverageSummary,
        GraphEdge,
        GraphNode,
        GraphProperty,
        GraphRelationshipGroup,
        OntologySnapshot,
        RunProgress,
        VersionedRef,
    )
    from app.services.extraction.ontology_guided.dependencies import DependencyIndex
    from app.services.extraction.ontology_guided.projection import project_graph
    from app.services.extraction.ontology_guided.records import RecordIndex

    context = get_row(store, run, "work:projection")
    if context is None:
        return None
    rows = {
        name.removeprefix("work:"): values
        for name, values in read_rows(store, run, DocumentRunCurrentState, prefix="work:").items()
    }
    collections = {}
    models = [("nodes", GraphNode), ("edges", GraphEdge), ("properties", GraphProperty)]
    if context.get("extraction_protocol"):
        models.append(("relationship_groups", GraphRelationshipGroup))
    for name, model in models:
        values = []
        for row in sorted(rows.get(name, {}).values(), key=lambda r: r.get("position", 0)):
            ref = row["candidate_ref"]
            candidate = store.db.get(
                DocumentRunCandidate, (run.recognition_run_id, ref["id"], ref["revision"])
            )
            if candidate is None or content_hash(candidate.payload) != candidate.payload_hash:
                raise HeadConflict("authoritative candidate is missing or invalid")
            values.append(model.model_validate(candidate.payload))
        collections[name] = values
    header = context["graph"]
    dependencies = DependencyIndex.from_current(rows)
    graph = project_graph(
        recognition_run_id=str(run.recognition_run_id),
        run_revision=header["run_revision"],
        event_head=header["event_head"],
        metadata_snapshot_id=run.metadata_snapshot_id,
        root_ref=VersionedRef.model_validate(header["root_ref"]),
        coverage=[
            CoverageSummary.model_validate(v["value"])
            for v in sorted(rows.get("coverage", {}).values(), key=lambda r: r["position"])
        ],
        progress=RunProgress.model_validate(header["progress"]),
        dependency_index=dependencies,
        projection=header["projection"] if context.get("extraction_protocol") else "all",
        extraction_protocol=context.get("extraction_protocol"),
        artifact_status=header["artifact_status"],
        **collections,
    )
    graph.attribute_candidates = [
        AttributeCalibrationCandidate.model_validate(candidate)
        for row in rows.get("record_discovery", {}).values()
        for candidate in row["value"].get("attribute_candidates", [])
    ]
    ontology_ref = store.get_artifact(run.recognition_run_id, run.owner_id, "ontology_snapshot")
    source_ref = store.get_artifact(run.recognition_run_id, run.owner_id, "structure")
    ontology = OntologySnapshot.model_validate(
        store.db.get(DocumentAnalysisArtifact, ontology_ref.artifact_id).payload
    )
    source = store.db.get(DocumentAnalysisArtifact, source_ref.artifact_id)
    ir = DocumentIR.model_validate(decode_state(store, run, source.payload)["analysis"])
    # Avoid consulting the invalid cache when preparing the replacement values.
    store._current_display_members = (str(run.recognition_run_id), {})
    try:
        _, (updates, _) = display_payload(
            store,
            run,
            ontology=ontology,
            ir=ir,
            metadata=None,
            index=RecordIndex(ir),
            graph=graph,
            dependencies=dependencies.snapshot(),
            summary=context.get("evidence_repair", {}),
        )
    finally:
        store._current_display_members = None
    selections = {}
    for value in updates["selections"].values():
        selections.update(value)
    graph = graph.model_copy(update={"generated_from_hash": header["generated_from_hash"]})
    return {
        **{k: v for k, v in context.items() if k not in {"partitioned", "member_counts"}},
        "graph": graph.model_dump(mode="json"),
        "selection_registry": selections,
        "predicate_menus": updates["menus"],
        "dependency_index": dependencies.snapshot(),
    }
