"""Lossless, run-owned immutable state blocks in the existing artifact store.

Run artifact links are the retention index: blocks have no mutable head and stay
referenced until the owning run is deleted. Encoding never commits a transaction.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from sqlalchemy import select

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


def encode_run_state(store, run, token: str, payload: dict, *, kind=None) -> dict:
    version = performance_policy(store, run).get("state_storage_version")
    if version == 3:
        from app.services.document_analysis.incremental_state import encode_incremental_state

        return encode_incremental_state(store, run, token, payload, kind=kind)
    if version == STORAGE_VERSION:
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
    # One fenced inventory query replaces a SELECT for every unchanged block.
    links = dict(db.execute(select(
        DocumentRunArtifact.content_hash, DocumentRunArtifact.artifact_id,
    ).join(DocumentAnalysisArtifact, DocumentAnalysisArtifact.artifact_id ==
           DocumentRunArtifact.artifact_id).where(
        DocumentRunArtifact.recognition_run_id == run.recognition_run_id,
        DocumentAnalysisArtifact.artifact_kind == "state_block",
    )).all())

    def block(node):
        body = {"schema_version": BLOCK_VERSION, "node": node}
        digest = content_hash(body)
        if digest in memo:
            return ["ref", memo[digest]]
        identity = stable_id("run-state-block", [str(run.recognition_run_id), digest])
        ref = {"artifact_id": identity, "content_hash": digest, "schema_version": BLOCK_VERSION}
        existing_id = links.get(digest)
        if existing_id is None:
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
            links[digest] = identity
        elif existing_id != identity:
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
