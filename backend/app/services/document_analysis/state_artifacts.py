"""Lossless, run-owned immutable state blocks in the existing artifact store.

Run artifact links are the retention index: blocks have no mutable head and stay
referenced until the owning run is deleted. Encoding never commits a transaction.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from app.models.document_analysis import DocumentAnalysisArtifact, DocumentRunArtifact
from app.services.document_analysis.run_store import (
    ArtifactConflict,
    DocumentAnalysisRunStore,
    content_hash,
)
from app.services.extraction.evidence_identity import stable_id
from app.services.extraction.ontology_guided.state_delta import freeze_json, thaw_json

STORAGE_VERSION = 2
BLOCK_VERSION = 1
BLOCK_BYTES = 8192
CHUNK_ITEMS = 64


class StateIntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedRunState:
    payload: dict
    blocks: dict = field(default_factory=dict)
    kind: str | None = None
    expected_head: tuple | None = None
    cache_entry: tuple | None = None
    run_identity: tuple | None = None

    def __post_init__(self):
        object.__setattr__(self, "payload", freeze_json(self.payload))
        object.__setattr__(self, "blocks", freeze_json(self.blocks))


def state_owner(run):
    return (str(run.recognition_run_id), run.owner_id, run.run_fingerprint)


def state_head(store, run, kind):
    head = store.get_artifact_head(run.recognition_run_id, run.owner_id, kind) if kind else None
    if head is not None:
        store.db.refresh(head)
        return (head.revision, head.artifact_id, head.content_hash)
    return (0, None, None)


def check_preparation(store):
    check = getattr(store, "interruption_check", None)
    if check:
        check()


def performance_policy(store, run) -> dict:
    ref = store.get_artifact(run.recognition_run_id, run.owner_id, "source")
    artifact = store.db.get(DocumentAnalysisArtifact, ref.artifact_id) if ref else None
    return dict((artifact.payload or {}).get("performance_policy") or {}) if artifact else {}


def encode_run_state(store, run, token: str, payload: dict, *, kind=None) -> dict:
    prepared = prepare_run_state(store, run, payload, kind=kind)
    return thaw_json(publish_prepared_state(store, run, token, prepared))


def prepare_run_state(store, run, payload: dict, *, kind=None) -> PreparedRunState:
    """Compute immutable state before acquiring a worker's publication locks."""
    check_preparation(store)
    store.get_owned(run.recognition_run_id, run.owner_id)
    expected = state_head(store, run, kind)
    if kind == "graph" and isinstance(payload.get("retrieval_plans"), list):
        plans = payload["retrieval_plans"]
        identities = [plan["plan_id"] for plan in plans]
        if len(set(identities)) != len(identities):
            raise StateIntegrityError("duplicate retrieval plan identity")
        payload = {**payload, "retrieval_plans": dict(zip(identities, plans, strict=True)),
                   "retrieval_plan_order": identities}
    version = performance_policy(store, run).get("state_storage_version")
    if version == 3:
        from app.services.document_analysis.incremental_state import prepare_incremental_state

        return prepare_incremental_state(store, run, payload, kind=kind)
    if version == STORAGE_VERSION:
        prepared = prepare_state_blocks(store, run, payload)
        return PreparedRunState(prepared.payload, prepared.blocks, kind, expected,
                                run_identity=state_owner(run))
    return PreparedRunState(freeze_json(payload), kind=kind, expected_head=expected,
                            run_identity=state_owner(run))


def publish_prepared_state(store, run, token, prepared):
    """Validate the exact owned predecessor, then publish prepared blocks atomically."""
    store.assert_fence(run.recognition_run_id, run.owner_id, token, for_update=True)
    if prepared.run_identity != state_owner(run):
        raise StateIntegrityError("prepared state belongs to another run or dependency")
    if prepared.kind and state_head(store, run, prepared.kind) != prepared.expected_head:
        raise ArtifactConflict("prepared state predecessor changed")
    blocks = prepared.blocks
    identities = list(blocks)
    existing = {}
    for offset in range(0, len(identities), 256):
        store.check_publication_deadline()
        existing.update(store.db.execute(select(
            DocumentRunArtifact.content_hash, DocumentRunArtifact.artifact_id,
        ).where(
            DocumentRunArtifact.recognition_run_id == run.recognition_run_id,
            DocumentRunArtifact.content_hash.in_(identities[offset:offset + 256]),
        )).all())
    pending = []
    for digest, (identity, body, size) in blocks.items():
        store.check_publication_deadline()
        if digest in existing:
            if existing[digest] != identity:
                raise StateIntegrityError("immutable state block link changed")
            continue
        pending.append((digest, identity))
        store.db.add(DocumentAnalysisArtifact(
            artifact_id=identity, artifact_kind="state_block", content_hash=digest,
            media_type="application/vnd.slpra.state-block+json", payload=body, size_bytes=size,
        ))
    store.db.flush()
    store.db.add_all([DocumentRunArtifact(
        recognition_run_id=run.recognition_run_id, artifact_kind=digest, revision=1,
        artifact_id=identity, content_hash=digest, status="ready", event_head=run.event_head,
        is_exclusive=True,
    ) for digest, identity in pending])
    store.db.flush()
    if prepared.cache_entry is not None:
        key, entry = prepared.cache_entry
        cache = getattr(store, "_incremental_states", {})
        cache[key] = entry
        store._incremental_states = cache
    store.assert_fence(run.recognition_run_id, run.owner_id, token)
    return prepared.payload


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def encode_state(store: DocumentAnalysisRunStore, run, token: str, payload: dict) -> dict:
    """Create a Merkle tree; only new content is written, under the caller's fence."""
    return thaw_json(publish_prepared_state(
        store, run, token, prepare_state_blocks(store, run, payload),
    ))


def prepare_state_blocks(store, run, payload):
    memo: dict[str, dict] = {}
    blocks = {}

    def block(node):
        check_preparation(store)
        body = {"schema_version": BLOCK_VERSION, "node": node}
        digest = content_hash(body)
        if digest in memo:
            return ["ref", memo[digest]]
        identity = stable_id("run-state-block", [str(run.recognition_run_id), digest])
        ref = {"artifact_id": identity, "content_hash": digest, "schema_version": BLOCK_VERSION}
        blocks[digest] = (identity, freeze_json(body), _size(body))
        memo[digest] = ref
        return ["ref", ref]

    def encode(value):
        if _size(value) <= BLOCK_BYTES or not isinstance(value, (dict, list)):
            node = ["value", value]
        elif isinstance(value, dict):
            node = ["dict", {key: encode(item) for key, item in value.items()}]
        else:
            # Fixed groups share complete historical prefixes across checkpoints.
            node = (
                ["chunks", [encode(value[i:i + CHUNK_ITEMS])
                            for i in range(0, len(value), CHUNK_ITEMS)]]
                if len(value) > CHUNK_ITEMS else ["items", [encode(v) for v in value]]
            )
        return block(node) if _size(value) > 512 else node

    return PreparedRunState(freeze_json({"storage_schema_version": STORAGE_VERSION,
                                        "root": encode(payload)}), blocks,
                            run_identity=state_owner(run))


def decode_state(store: DocumentAnalysisRunStore, run, payload: dict) -> dict:
    result = _decode_state(store, run, payload)
    if isinstance(result.get("retrieval_plans"), dict) and "retrieval_plan_order" in result:
        result = {**result, "retrieval_plans": [result["retrieval_plans"][identity]
                                              for identity in result["retrieval_plan_order"]]}
        result.pop("retrieval_plan_order")
    return result


def _decode_state(store: DocumentAnalysisRunStore, run, payload: dict) -> dict:
    """Read v1 inline or v2 exact references. Never repair or select latest blocks."""
    store.get_owned(run.recognition_run_id, run.owner_id)
    if payload.get("storage_schema_version") == 3:
        from app.services.document_analysis.incremental_state import decode_incremental_state

        return decode_incremental_state(store, run, payload)
    if "storage_schema_version" not in payload:
        return payload
    if payload.get("storage_schema_version") != STORAGE_VERSION or set(payload) != {
        "storage_schema_version", "root"
    }:
        raise StateIntegrityError("unsupported state storage format")
    cache: dict[str, Any] = {}
    visiting: set[str] = set()
    blocks: dict[str, dict] = {}

    def refs(node):
        if not isinstance(node, list) or len(node) != 2:
            raise StateIntegrityError("invalid state reference tree")
        kind, value = node
        if kind == "ref":
            return [value]
        if kind == "dict" and isinstance(value, dict):
            return [ref for child in value.values() for ref in refs(child)]
        if kind in {"items", "chunks"} and isinstance(value, list):
            return [ref for child in value for ref in refs(child)]
        if kind == "value":
            return []
        raise StateIntegrityError("invalid state reference tree")

    pending = refs(payload["root"])
    depth = 0
    while pending:
        check_preparation(store)
        depth += 1
        if depth > 100:
            raise StateIntegrityError("invalid state reference depth")
        requested = {}
        for ref in pending:
            if (not isinstance(ref, dict) or set(ref) != {
                "artifact_id", "content_hash", "schema_version"
            } or ref.get("schema_version") != BLOCK_VERSION
                    or ref["artifact_id"] != stable_id(
                        "run-state-block", [str(run.recognition_run_id), ref["content_hash"]]
                    )):
                raise StateIntegrityError("state block belongs to another run or format")
            if ref["artifact_id"] not in blocks:
                requested[ref["artifact_id"]] = ref["content_hash"]
        pending = []
        identities = list(requested)
        for offset in range(0, len(identities), 256):
            batch = identities[offset:offset + 256]
            rows = store.db.execute(select(
                DocumentAnalysisArtifact.artifact_id,
                DocumentAnalysisArtifact.content_hash,
                DocumentAnalysisArtifact.payload,
                DocumentRunArtifact.content_hash.label("link_hash"),
                DocumentRunArtifact.artifact_kind.label("link_kind"),
                DocumentRunArtifact.revision,
                DocumentRunArtifact.event_head,
            ).join(DocumentRunArtifact, DocumentRunArtifact.artifact_id ==
                   DocumentAnalysisArtifact.artifact_id).where(
                DocumentRunArtifact.recognition_run_id == run.recognition_run_id,
                DocumentAnalysisArtifact.artifact_kind == "state_block",
                DocumentAnalysisArtifact.artifact_id.in_(batch),
            )).mappings().all()
            found = set()
            for row in rows:
                identity, digest, body = row["artifact_id"], row["content_hash"], row["payload"]
                if (digest != requested[identity] or row["link_hash"] != digest
                        or row["link_kind"] != digest or row["revision"] != 1
                        or row["event_head"] > run.event_head or not isinstance(body, dict)
                        or content_hash(body) != digest
                        or body.get("schema_version") != BLOCK_VERSION):
                    raise StateIntegrityError("state block missing, unowned or corrupt")
                found.add(identity)
                blocks[identity] = body
                pending.extend(refs(body.get("node")))
            if found != set(batch):
                raise StateIntegrityError("state block missing, unowned or corrupt")

    def decode(node, depth=0):
        check_preparation(store)
        if depth > 100 or not isinstance(node, list) or len(node) != 2:
            raise StateIntegrityError("invalid state reference tree")
        kind, value = node
        if kind == "value":
            return value
        if kind == "dict" and isinstance(value, dict):
            return {key: decode(item, depth + 1) for key, item in value.items()}
        if kind in {"items", "chunks"} and isinstance(value, list):
            values = [decode(item, depth + 1) for item in value]
            return values if kind == "items" else [item for chunk in values for item in chunk]
        if kind != "ref" or not isinstance(value, dict) or set(value) != {
            "artifact_id", "content_hash", "schema_version"
        } or value.get("schema_version") != BLOCK_VERSION:
            raise StateIntegrityError("invalid state block reference")
        identity, digest = value["artifact_id"], value["content_hash"]
        if identity != stable_id("run-state-block", [str(run.recognition_run_id), digest]):
            raise StateIntegrityError("state block belongs to another run")
        if identity in visiting:
            raise StateIntegrityError("cyclic state block reference")
        if identity in cache:
            return deepcopy(cache[identity])
        body = blocks.get(identity)
        if body is None:
            raise StateIntegrityError("state block missing, unowned or corrupt")
        visiting.add(identity)
        result = decode(body.get("node"), depth + 1)
        visiting.remove(identity)
        cache[identity] = result
        return result

    result = decode(payload.get("root"))
    if not isinstance(result, dict):
        raise StateIntegrityError("state root must be an object")
    return result
