"""One-off, dependency-aware pruning. Never schedule this implicitly.

Keep three revisions per run/kind, plus their exact restoration dependencies.
State blocks have no independent version sequence. Old checkpoint payloads can
be removed while their identities remain for durable batch idempotency receipts.
Run against a quiescent database; --apply locks publication tables and rechecks.
"""

from __future__ import annotations

import argparse
import gzip
import json
import resource
import signal
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, null, select, text, tuple_, update

from app.db import SessionLocal
from app.models.document_analysis import (
    DocumentAnalysisArtifact as Artifact,
)
from app.models.document_analysis import (
    DocumentAnalysisRun as Run,
)
from app.models.document_analysis import (
    DocumentRecognitionEventBatch as Batch,
)
from app.models.document_analysis import (
    DocumentRunArtifact as Ref,
)
from app.models.document_analysis import (
    DocumentRunArtifactHead as Head,
)
from app.models.document_analysis_review import DocumentPropertyRepair as Repair
from app.services.document_analysis.run_store import DocumentAnalysisRunStore, content_hash
from app.services.document_analysis.state_artifacts import decode_state

TARGETS = {"recognition_checkpoint", "graph", "public_graph", "ranking_state"}
ENCODED = {"recognition_checkpoint", "graph", "ranking_state", "recognition-model-calls"}


def block_references(node):
    if not isinstance(node, list) or len(node) != 2:
        raise ValueError("invalid state tree")
    tag, value = node
    if tag == "ref":
        yield value
    elif tag == "dict":
        for child in value.values():
            yield from block_references(child)
    elif tag in {"items", "chunks"}:
        for child in value:
            yield from block_references(child)
    elif tag != "value":
        raise ValueError("unknown state tree tag")


def dependencies(body, kind):
    if kind == "state_block":
        yield from block_references(body["node"])
    elif body.get("storage_schema_version") == 3:
        if body["base"]:
            yield body["base"]
        if body["baseline"]:
            yield from block_references(body["baseline"]["root"])
    elif body.get("storage_schema_version") == 2:
        yield from block_references(body["root"])
    elif "storage_schema_version" in body:
        raise ValueError("unsupported state format")


def key(ref):
    return (ref.recognition_run_id, ref.artifact_kind, ref.revision)


@dataclass
class Plan:
    roots: list
    remove_refs: list
    erase_payload: list[str]
    delete_artifacts: list[str]
    dependency_versions: dict
    artifact_kinds: dict
    verified_hashes: dict
    dependency_edges: dict

    def summary(self):
        return {
            "policy": "latest 3 per run/kind plus exact dependencies",
            "root_versions": len(self.roots),
            "extra_dependency_versions": self.dependency_versions,
            "remove_references": len(self.remove_refs),
            "erase_checkpoint_payloads_keep_receipts": len(self.erase_payload),
            "delete_artifacts": dict(Counter(
                self.artifact_kinds[identity] for identity in self.delete_artifacts
            )),
        }


def build_plan(db, keep=3, progress=None):
    if keep < 1:
        raise ValueError("keep must be positive")
    if db.scalar(select(Run.recognition_run_id).where(
        Run.execution_status.in_(["running", "queued", "pausing"]),
    ).limit(1)):
        raise ValueError("pause active runs before pruning")
    refs = list(db.scalars(select(Ref)))
    grouped = defaultdict(list)
    by_identity = defaultdict(list)
    for ref in refs:
        grouped[(ref.recognition_run_id, ref.artifact_kind)].append(ref)
        by_identity[(ref.recognition_run_id, ref.artifact_id)].append(ref)
    kinds = dict(db.execute(select(Artifact.artifact_id, Artifact.artifact_kind)).all())
    roots = [ref for (_, kind), values in grouped.items() if kind in TARGETS
             for ref in sorted(values, key=lambda item: item.revision, reverse=True)[:keep]]
    protected = {key(ref) for ref in refs if kinds[ref.artifact_id] not in TARGETS | {
        "state_block",
    }}
    seeds = list(roots)
    # Every historical model reservation remains in scope and may share blocks.
    seeds.extend(ref for ref in refs if ref.artifact_kind == "recognition-model-calls")
    by_key = {key(ref): ref for ref in refs}
    for head in db.scalars(select(Head)):
        ref = by_key[key(head)]
        if ref.artifact_id != head.artifact_id or ref.content_hash != head.content_hash:
            raise ValueError("artifact head identity mismatch")
        protected.add(key(ref))
        if ref.artifact_kind in ENCODED:
            seeds.append(ref)
    for repair in db.scalars(select(Repair)):
        links = by_identity[(repair.recognition_run_id, repair.base_checkpoint_artifact_id)]
        if not links:
            raise ValueError("repair checkpoint reference missing")
        seeds.extend(links)
    pending = {key(ref): ref for ref in seeds}
    visited = set()
    verified_hashes = {}
    dependency_edges = defaultdict(set)
    reported_at = time.monotonic()
    while pending:
        work = list(pending.values())[:64]
        rows = {row.artifact_id: row for row in db.execute(select(
            Artifact.artifact_id, Artifact.payload, Artifact.content_hash,
        ).where(Artifact.artifact_id.in_({ref.artifact_id for ref in work})))}
        for ref in work:
            ref_key = key(ref)
            pending.pop(ref_key, None)
            if ref_key in visited:
                continue
            visited.add(ref_key)
            protected.add(ref_key)
            row = rows.get(ref.artifact_id)
            if (row is None or row.payload is None or row.content_hash != ref.content_hash
                    or content_hash(row.payload) != ref.content_hash):
                raise ValueError("retained artifact missing or corrupt")
            kind = kinds[ref.artifact_id]
            verified_hashes[ref.artifact_id] = row.content_hash
            if kind not in ENCODED | {"state_block"}:
                continue
            body = row.payload
            if body.get("storage_schema_version") == 3 and (
                body["recognition_run_id"] != str(ref.recognition_run_id)
                or body["artifact_kind"] != ref.artifact_kind
                or body["sequence"] != ref.revision
            ):
                raise ValueError("incremental identity mismatch")
            for dep in dependencies(body, kind):
                links = by_identity[(ref.recognition_run_id, dep["artifact_id"])]
                matches = [link for link in links if link.content_hash == dep["content_hash"]
                           and (link.artifact_kind == ref.artifact_kind
                                and link.revision == dep["revision"] if "revision" in dep
                                else kinds[link.artifact_id] == "state_block"
                                and link.artifact_kind == dep["content_hash"]
                                and link.revision == 1)]
                if len(matches) != 1:
                    raise ValueError("missing or unowned restoration dependency")
                link = matches[0]
                dependency_edges[ref.artifact_id].add(link.artifact_id)
                if key(link) not in visited:
                    pending[key(link)] = link
        if progress and time.monotonic() - reported_at > 10:
            progress({"verified_artifacts": len(visited), "pending": len(pending)})
            reported_at = time.monotonic()
    remove_refs = [ref for ref in refs if key(ref) not in protected]
    retained_ids = {ref.artifact_id for ref in refs if key(ref) in protected}
    obsolete = {identity for identity, kind in kinds.items()
                if kind in TARGETS | {"state_block"} and identity not in retained_ids}
    receipt_ids = set(db.scalars(select(Batch.checkpoint_artifact_id)))
    repair_ids = set(db.scalars(select(Repair.base_checkpoint_artifact_id)))
    if obsolete & repair_ids:
        raise ValueError("pruning would remove a repair checkpoint")
    root_keys = {key(ref) for ref in roots}
    dependency_keys = protected - root_keys
    populated = set(db.scalars(select(Artifact.artifact_id).where(
        Artifact.artifact_kind == "recognition_checkpoint", Artifact.payload.is_not(None),
    )))
    return Plan(
        roots, remove_refs, sorted(obsolete & receipt_ids & populated),
        sorted(obsolete - receipt_ids),
        dict(Counter(ref.artifact_kind for ref in refs if key(ref) in dependency_keys
                     and ref.artifact_kind in TARGETS)), kinds, verified_hashes, dependency_edges,
    )


def root_digests(db, plan, backup=None):
    store = DocumentAnalysisRunStore(db)
    runs = {run.recognition_run_id: run for run in db.scalars(select(Run))}
    result = {}
    for ref in plan.roots:
        body = db.scalar(select(Artifact.payload).where(Artifact.artifact_id == ref.artifact_id))
        decoded = decode_state(store, runs[ref.recognition_run_id], body)
        result[ref.artifact_id] = content_hash(decoded)
        if backup:
            backup.write(json.dumps({
                "run_id": str(ref.recognition_run_id), "kind": ref.artifact_kind,
                "revision": ref.revision, "artifact_id": ref.artifact_id,
                "encoded_hash": ref.content_hash, "decoded_hash": result[ref.artifact_id],
                "decoded_payload": decoded,
            }, ensure_ascii=False) + "\n")
    return result


def apply_plan(db, plan):
    # Keep batch receipt identities: deleting them would weaken replay protection.
    for offset in range(0, len(plan.remove_refs), 500):
        batch = plan.remove_refs[offset:offset + 500]
        db.execute(delete(Ref).where(tuple_(
            Ref.recognition_run_id, Ref.artifact_kind, Ref.revision,
        ).in_([key(ref) for ref in batch])).execution_options(synchronize_session=False))
    for offset in range(0, len(plan.erase_payload), 500):
        db.execute(update(Artifact).where(Artifact.artifact_id.in_(
            plan.erase_payload[offset:offset + 500],
        )).values(payload=null(), size_bytes=None).execution_options(synchronize_session=False))
    for offset in range(0, len(plan.delete_artifacts), 500):
        db.execute(delete(Artifact).where(Artifact.artifact_id.in_(
            plan.delete_artifacts[offset:offset + 500],
        )).execution_options(synchronize_session=False))
    db.flush()
    db.expire_all()


def backup_retained(db, plan, destination):
    """Copy each encoded recovery object once, without rebuilding every delta chain."""
    identities = {ref.artifact_id for ref in plan.roots}
    pending = list(identities)
    while pending:
        for identity in plan.dependency_edges.get(pending.pop(), ()):
            if identity not in identities:
                identities.add(identity)
                pending.append(identity)
    ordered = sorted(identities)
    with gzip.open(destination, "wt", compresslevel=1) as out:
        for offset in range(0, len(ordered), 64):
            for row in db.execute(select(Artifact.__table__).where(
                Artifact.artifact_id.in_(ordered[offset:offset + 64]),
            )).mappings():
                out.write(json.dumps(dict(row), ensure_ascii=False, default=str) + "\n")
    return len(identities)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    # Bound maintenance independently of the long-lived production web process.
    resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (300, 330))
    signal.alarm(600)
    args.record_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with SessionLocal() as db:
        if db.bind.dialect.name != "postgresql":
            raise ValueError("operational entry point requires PostgreSQL")
        db.execute(text("SET LOCAL lock_timeout = '5s'"))
        db.execute(text("SET LOCAL statement_timeout = '10min'"))
        if args.apply:
            db.execute(text("LOCK TABLE document_analysis_executions, document_analysis_runs, "
                            "document_analysis_run_artifacts, document_analysis_artifact_heads, "
                            "document_analysis_artifacts, document_analysis_event_batches, "
                            "document_analysis_property_reviews, "
                            "document_analysis_property_repairs "
                            "IN SHARE ROW EXCLUSIVE MODE"))
        plan = build_plan(db, progress=lambda item: print(json.dumps(item), flush=True))
        print(json.dumps(plan.summary()), flush=True)
        (args.record_dir / "plan.json").write_text(json.dumps(plan.summary(), indent=2))
        if not args.apply:
            db.rollback()
            return
        count = backup_retained(db, plan, args.record_dir / "retained-artifacts.jsonl.gz")
        print(json.dumps({"retained_recovery_objects_backed_up": count}), flush=True)
        manifest = {**plan.summary(), "verified_hashes": plan.verified_hashes,
                    "remove_refs": [{"run_id": str(ref.recognition_run_id),
                                     "kind": ref.artifact_kind, "revision": ref.revision,
                                     "artifact_id": ref.artifact_id,
                                     "content_hash": ref.content_hash}
                                    for ref in plan.remove_refs],
                    "erase_payload": plan.erase_payload,
                    "delete_artifacts": plan.delete_artifacts}
        with gzip.open(args.record_dir / "deletion-manifest.json.gz", "wt") as out:
            json.dump(manifest, out)
        apply_plan(db, plan)
        print(json.dumps({"deletions_done_in_transaction": True}), flush=True)
        after = build_plan(db, progress=lambda item: print(json.dumps(item), flush=True))
        if (plan.verified_hashes != after.verified_hashes
                or plan.dependency_edges != after.dependency_edges
                or after.remove_refs or after.delete_artifacts or after.erase_payload):
            raise ValueError("retained dependency closure changed; rolling back")
        db.commit()
        report = {**plan.summary(), "committed": True,
                  "verified_recovery_objects": len(after.verified_hashes),
                  "verification": "all retained encoded hashes and dependency edges unchanged"}
        (args.record_dir / "result.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
