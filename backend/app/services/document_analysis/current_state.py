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


def _candidate(store, run, token, kind, body, sequence):
    identity = body.get("candidate_id") or body["entity_id"]
    revision = body["revision"]
    existing = store.db.get(DocumentRunCandidate, (run.recognition_run_id, identity, revision))
    digest = content_hash(body)
    refs = [body["proof_ref"]] if body.get("proof_ref") else []
    if existing is not None:
        if existing.payload_hash != digest or existing.kind != kind or existing.proof_refs != refs:
            raise HeadConflict("immutable candidate revision changed")
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


def write_work(store, run, token, changes, *, sequence, expected_version, fingerprint):
    current = lock_current(store, run, token, fingerprint)
    if current.work_version != expected_version:
        raise HeadConflict("current work version changed")
    version = expected_version + 1
    for domain, values in changes.items():
        if domain in {"nodes", "edges", "properties"}:
            kind = {"nodes": "entity", "edges": "relationship", "properties": "property"}[domain]
            values = {
                key: {
                    "key": row["key"],
                    "position": row["position"],
                    "candidate_ref": _candidate(
                        store, current, token, kind, row["value"], sequence
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
    for name in ("nodes", "edges", "properties"):
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
                if state["version"] == 2
                else {}
            ),
        },
        check_paid_prefix=False,  # Changed requests are checked against their persisted rows below.
    )
    if state.get("run_fingerprint") != fingerprint or state.get("recognition_run_id") != str(
        run.recognition_run_id
    ):
        raise HeadConflict("request run identity mismatch")
    with _publication(store.db, store):
        current = lock_current(store, run, token, fingerprint)
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
        reserved_delta = confirmed_delta = 0
        for domain in ("lineage_calls", "protocols"):
            for key, row in state[domain].items():
                prior = get_row(store, current, "calls:" + domain, key, model=DocumentRunRequest)
                if domain == "lineage_calls":
                    if prior and row["value"] < prior["value"]:
                        raise HeadConflict("reserved model costs regressed")
                    reserved_delta += row["value"] - (prior["value"] if prior else 0)
                if domain == "protocols":
                    protocol = row["value"]
                    prior = hydrate_protocol(store, current, prior) if prior else None
                    if (
                        protocol["base_target"]["document_context"]["document_hash"]
                        != run.document_hash
                    ):
                        raise HeadConflict("protocol source mismatch")
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
        if state["version"] == 2:
            progress["model_calls"] = progress.get("model_calls", 0) + confirmed_delta
        progress["model_calls_unresolved"] = max(
            0, progress["model_calls_reserved"] - progress.get("model_calls", 0)
        )
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


def call_request_key(receipt):
    attempt = receipt.get("protocol_attempt")
    return (
        content_hash([receipt["lineage_id"], attempt])
        if attempt is not None
        else str(receipt["sequence"])
    )


def hydrate_protocol(store, run, row):
    row = deepcopy(row)
    protocol = row["value"]
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
    for field in ("nodes", "edges", "properties"):
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
    for field in ("nodes", "edges", "properties"):
        values = {getattr(v, "entity_id", None) or v.candidate_id: v for v in getattr(graph, field)}
        prior = members.get("display:" + field, set())
        touched = (
            {r["key"] for r in changes.get(field, {}).values() if r}
            if changes is not None
            else set(values)
        ) | (set(values) - prior)
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
                    for name in ("nodes", "edges", "properties")
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
    updates["coverage"] = {
        content_hash([v.subject_ref.id, v.subject_ref.revision, v.predicate_iri]): {
            "position": i,
            "value": v.model_dump(mode="json"),
        }
        for i, v in enumerate(graph.coverage)
        if slots is None or (v.subject_ref.id, v.subject_ref.revision, v.predicate_iri) in slots
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
        "snapshot_id": snapshot_id,
        "analysis_id": ir.analysis_id,
        "member_counts": {
            name: len(members.get("display:" + name, ()))
            for name in ("nodes", "edges", "properties")
        },
        "ontology_snapshot_id": ontology.snapshot_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "graph": graph.model_dump(
            mode="json", exclude={"nodes", "edges", "properties", "coverage"}
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

    digest = content_hash(
        {
            "fingerprint": fingerprint,
            "task": batch.task.model_dump(mode="json"),
            "outcome": batch.outcome.model_dump(mode="json"),
            "changes": batch.work_changes,
        }
    )
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
                "task": batch.task.model_dump(mode="json"),
                "outcome": batch.outcome.model_dump(mode="json"),
            },
        )
        write_proofs(store, current, token, batch.outcome, event.sequence)
        current = write_work(
            store,
            current,
            token,
            batch.work_changes,
            sequence=event.sequence,
            expected_version=expected_version,
            fingerprint=fingerprint,
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
    from app.services.document_analysis.state_artifacts import decode_state
    from app.services.extraction.document_ir import DocumentIR
    from app.services.extraction.ontology_guided.contracts import (
        CoverageSummary,
        GraphEdge,
        GraphNode,
        GraphProperty,
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
    for name, model in (("nodes", GraphNode), ("edges", GraphEdge), ("properties", GraphProperty)):
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
        projection="all",
        artifact_status=header["artifact_status"],
        **collections,
    )
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
