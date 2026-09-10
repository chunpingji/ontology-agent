"""Lossless, run-owned immutable state blocks in the existing artifact store.

Run artifact links are the retention index: blocks have no mutable head and stay
referenced until the owning run is deleted. Encoding never commits a transaction.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from app.models.document_analysis import DocumentAnalysisArtifact, DocumentRunArtifact
from app.services.document_analysis.run_store import DocumentAnalysisRunStore, content_hash
from app.services.extraction.evidence_identity import stable_id

STORAGE_VERSION = 2
BLOCK_VERSION = 1
BLOCK_BYTES = 8192
CHUNK_ITEMS = 64


class StateIntegrityError(ValueError):
    pass


def performance_policy(store, run) -> dict:
    ref = store.get_artifact(run.recognition_run_id, run.owner_id, "source")
    artifact = store.db.get(DocumentAnalysisArtifact, ref.artifact_id) if ref else None
    return dict((artifact.payload or {}).get("performance_policy") or {}) if artifact else {}


def encode_run_state(store, run, token: str, payload: dict) -> dict:
    if performance_policy(store, run).get("state_storage_version") == STORAGE_VERSION:
        return encode_state(store, run, token, payload)
    return payload


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def encode_state(store: DocumentAnalysisRunStore, run, token: str, payload: dict) -> dict:
    """Create a Merkle tree; only new content is written, under the caller's fence."""
    # Serialize before inserting shared blocks; a read-only fence check would
    # let duplicate workers race on the immutable block's unique identity.
    store.assert_fence(run.recognition_run_id, run.owner_id, token, for_update=True)
    db = store.db
    memo: dict[str, dict] = {}

    def block(node):
        body = {"schema_version": BLOCK_VERSION, "node": node}
        digest = content_hash(body)
        if digest in memo:
            return ["ref", memo[digest]]
        identity = stable_id("run-state-block", [str(run.recognition_run_id), digest])
        ref = {"artifact_id": identity, "content_hash": digest, "schema_version": BLOCK_VERSION}
        link = db.get(DocumentRunArtifact, (run.recognition_run_id, digest, 1))
        if link is None:
            db.add(DocumentAnalysisArtifact(
                artifact_id=identity, artifact_kind="state_block", content_hash=digest,
                media_type="application/vnd.slpra.state-block+json", payload=body,
                size_bytes=_size(body),
            ))
            db.flush()
            db.add(DocumentRunArtifact(
                recognition_run_id=run.recognition_run_id, artifact_kind=digest, revision=1,
                artifact_id=identity, content_hash=digest, status="ready",
                event_head=run.event_head,
                is_exclusive=True,
            ))
            db.flush()
        elif link.artifact_id != identity or link.content_hash != digest:
            raise StateIntegrityError("immutable state block link changed")
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

    return {"storage_schema_version": STORAGE_VERSION, "root": encode(payload)}


def decode_state(store: DocumentAnalysisRunStore, run, payload: dict) -> dict:
    """Read v1 inline or v2 exact references. Never repair or select latest blocks."""
    store.get_owned(run.recognition_run_id, run.owner_id)
    if "storage_schema_version" not in payload:
        return payload
    if payload.get("storage_schema_version") != STORAGE_VERSION or set(payload) != {
        "storage_schema_version", "root"
    }:
        raise StateIntegrityError("unsupported state storage format")
    cache: dict[str, Any] = {}
    visiting: set[str] = set()

    def decode(node, depth=0):
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
        link = store.db.get(DocumentRunArtifact, (run.recognition_run_id, digest, 1))
        artifact = store.db.get(DocumentAnalysisArtifact, identity)
        if (
            link is None or link.artifact_id != identity or link.content_hash != digest
            or link.event_head > run.event_head or artifact is None
            or artifact.artifact_kind != "state_block" or artifact.content_hash != digest
            or not isinstance(artifact.payload, dict) or content_hash(artifact.payload) != digest
            or artifact.payload.get("schema_version") != BLOCK_VERSION
        ):
            raise StateIntegrityError("state block missing, unowned or corrupt")
        visiting.add(identity)
        result = decode(artifact.payload.get("node"), depth + 1)
        visiting.remove(identity)
        cache[identity] = result
        return result

    result = decode(payload.get("root"))
    if not isinstance(result, dict):
        raise StateIntegrityError("state root must be an object")
    return result
