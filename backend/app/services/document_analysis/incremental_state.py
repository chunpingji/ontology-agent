"""Fenced per-artifact JSON deltas with bounded, independently restorable baselines."""

from __future__ import annotations

from sqlalchemy import select

from app.models.document_analysis import DocumentAnalysisArtifact, DocumentRunArtifact
from app.services.document_analysis.run_store import content_hash
from app.services.extraction.ontology_guided.state_delta import (
    apply_delta,
    freeze_json,
    snapshot_delta,
    thaw_json,
)

STORAGE_VERSION = 3
BASELINE_INTERVAL = 32


def _key(run, kind):
    return (str(run.recognition_run_id), run.owner_id, run.run_fingerprint, kind)


def structural_digest(store, run, name, value):
    """Pure hash memoization: every supplied value is compared, including after rollback."""
    return structural_snapshot(store, run, name, value).digest


def structural_snapshot(store, run, name, value):
    """Detached copy-on-write snapshot, including reusable identities for old subtrees."""
    from app.services.document_analysis.state_artifacts import check_preparation

    cache = getattr(store, "_structural_hashes", {})
    key = _key(run, name)
    tree = snapshot_delta(value, cache.get(key), check=lambda: check_preparation(store))[0]
    cache[key] = tree
    store._structural_hashes = cache
    return tree


def encode_incremental_state(store, run, token, payload, *, kind):
    from app.services.document_analysis.state_artifacts import publish_prepared_state

    return thaw_json(publish_prepared_state(
        store, run, token, prepare_incremental_state(store, run, payload, kind=kind),
    ))


def prepare_incremental_state(store, run, payload, *, kind):
    from app.services.document_analysis.state_artifacts import (
        PreparedRunState,
        StateIntegrityError,
        check_preparation,
        prepare_state_blocks,
        state_head,
        state_owner,
    )

    if not kind:
        raise StateIntegrityError("incremental state requires an artifact kind")
    check_preparation(store)
    expected = state_head(store, run, kind)
    head = store.get_artifact_head(run.recognition_run_id, run.owner_id, kind)
    if head is not None:
        store.db.refresh(head)
    previous = None
    cache = getattr(store, "_incremental_states", {})
    prior = cache.get(_key(run, kind))
    revision = head.revision if head else 0
    if head:
        # A proposed cached version becomes usable only after its exact hash and
        # revision actually appear in this owned, fenced durable head. Rollback
        # or another head always forces a cold read; no uncommitted state leaks.
        if prior and prior[0] == (revision, head.content_hash):
            previous = prior[1]
        else:
            artifact = store.db.get(DocumentAnalysisArtifact, head.artifact_id)
            if artifact is None or content_hash(artifact.payload) != head.content_hash:
                raise StateIntegrityError("incremental predecessor is corrupt")
            raw = decode_incremental_state(store, run, artifact.payload, expected_kind=kind)
            previous = snapshot_delta(raw, check=lambda: check_preparation(store))[0]
    tree, operations = snapshot_delta(payload, previous, check=lambda: check_preparation(store))
    baseline = revision % BASELINE_INTERVAL == 0
    baseline_state = prepare_state_blocks(store, run, tree.value) if baseline else None
    result = {
        "storage_schema_version": STORAGE_VERSION,
        "recognition_run_id": str(run.recognition_run_id),
        "run_fingerprint": run.run_fingerprint,
        "artifact_kind": kind,
        "sequence": revision + 1,
        "depth": revision % BASELINE_INTERVAL,
        "value_hash": tree.digest,
        "base": None if baseline else {
            "artifact_id": head.artifact_id, "content_hash": head.content_hash,
            "revision": revision,
        },
        "baseline": baseline_state.payload if baseline_state else None,
        "operations": [] if baseline else operations,
    }
    return PreparedRunState(
        freeze_json(result), baseline_state.blocks if baseline_state else {}, kind, expected,
        (_key(run, kind), ((revision + 1, content_hash(result)), tree)),
        state_owner(run),
    )


def decode_incremental_state(store, run, payload, *, expected_kind=None):
    from app.services.document_analysis.state_artifacts import (
        StateIntegrityError,
        _decode_state,
        check_preparation,
    )

    store.get_owned(run.recognition_run_id, run.owner_id)
    required = {"storage_schema_version", "recognition_run_id", "run_fingerprint",
                "artifact_kind", "sequence", "depth", "value_hash", "base", "baseline",
                "operations"}
    chain = []
    current = payload
    kind = expected_kind or payload.get("artifact_kind")
    try:
        while True:
            check_preparation(store)
            if (not isinstance(current, dict) or set(current) != required
                    or current["storage_schema_version"] != STORAGE_VERSION
                    or current["recognition_run_id"] != str(run.recognition_run_id)
                    or current["run_fingerprint"] != run.run_fingerprint
                    or current["artifact_kind"] != kind
                    or type(current["sequence"]) is not int or current["sequence"] < 1
                    or type(current["depth"]) is not int
                    or current["depth"] != (current["sequence"] - 1) % BASELINE_INTERVAL
                    or len(chain) >= BASELINE_INTERVAL
                    or not isinstance(current["operations"], list)):
                raise StateIntegrityError("invalid incremental state identity or chain")
            chain.append(current)
            if current["depth"] == 0:
                if current["base"] is not None or current["operations"] or not isinstance(
                    current["baseline"], dict,
                ) or current["baseline"].get("storage_schema_version") != 2:
                    raise StateIntegrityError("invalid incremental state baseline")
                value = _decode_state(store, run, current["baseline"])
                tree = snapshot_delta(value, check=lambda: check_preparation(store))[0]
                break
            base = current["base"]
            if (not isinstance(base, dict) or set(base) != {
                "artifact_id", "content_hash", "revision"
            } or current["baseline"] is not None
                    or base["revision"] != current["sequence"] - 1):
                raise StateIntegrityError("invalid incremental state predecessor")
            row = store.db.execute(select(
                DocumentAnalysisArtifact.payload, DocumentAnalysisArtifact.content_hash,
                DocumentRunArtifact.content_hash.label("link_hash"),
                DocumentRunArtifact.event_head,
            ).join(DocumentRunArtifact, DocumentRunArtifact.artifact_id ==
                   DocumentAnalysisArtifact.artifact_id).where(
                DocumentRunArtifact.recognition_run_id == run.recognition_run_id,
                DocumentRunArtifact.artifact_kind == kind,
                DocumentRunArtifact.revision == base["revision"],
                DocumentRunArtifact.artifact_id == base["artifact_id"],
                DocumentAnalysisArtifact.artifact_kind == kind,
            )).mappings().one_or_none()
            if (row is None or row["content_hash"] != base["content_hash"]
                    or row["link_hash"] != base["content_hash"]
                    or content_hash(row["payload"]) != base["content_hash"]
                    or row["event_head"] > run.event_head):
                raise StateIntegrityError("incremental predecessor missing, unowned or corrupt")
            current = row["payload"]
            if current.get("sequence") != base["revision"]:
                raise StateIntegrityError("incremental predecessor revision mismatch")
        for item in reversed(chain):
            check_preparation(store)
            if item["depth"]:
                value = apply_delta(tree.value, item["operations"],
                                    check=lambda: check_preparation(store))
                tree = snapshot_delta(value, tree, check=lambda: check_preparation(store))[0]
            if tree.digest != item["value_hash"]:
                raise StateIntegrityError("incremental state content hash mismatch")
        return thaw_json(tree.value)
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, StateIntegrityError):
            raise
        raise StateIntegrityError("invalid incremental state content") from exc
