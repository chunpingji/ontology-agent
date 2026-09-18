"""Replay exported run artifacts without models or access to the source database.

Input is gzip JSONL of artifact_kind/revision/event_head/payload rows. Output is a
new directory containing a private SQLite replay and a metrics-only report.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import platform
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import app.models  # noqa: E402,F401
from app.db import Base  # noqa: E402
from app.models.document_analysis import DocumentAnalysisArtifact  # noqa: E402
from app.services.document_analysis.application import DocumentAnalysisApplication  # noqa: E402
from app.services.document_analysis.read_artifacts import publish_read_artifacts  # noqa: E402
from app.services.document_analysis.run_store import (  # noqa: E402
    DocumentAnalysisRunStore,
    content_hash,
)
from app.services.document_analysis.state_artifacts import (  # noqa: E402
    decode_state,
    encode_state,
)


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def write_head(store, run, token, kind, payload):
    head = store.get_artifact_head(run.recognition_run_id, run.owner_id, kind)
    return store.update_artifact(
        run.recognition_run_id, run.owner_id, token, artifact_kind=kind,
        expected_revision=head.revision if head else 0, artifact_hash=content_hash(payload),
        payload=payload, media_type="application/json", status="partial",
    )


def timing(engine, run_id, samples):
    measured, response = [], None
    for _ in range(samples):
        with Session(engine) as reader:
            application = DocumentAnalysisApplication(reader, ontology_engine=object())
            start = time.perf_counter()
            run = application.get_run(run_id, "performance-replay")
            response = application.graph_response(run, projection="effective_affirmed")
            measured.append((time.perf_counter() - start) * 1000)
    return {"samples": samples, "median_ms": statistics.median(measured),
            "p95_ms": sorted(measured)[max(0, int(samples * .95) - 1)]}, response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=30)
    args = parser.parse_args()
    if args.samples < 20:
        parser.error("at least 20 samples are required")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    engine = create_engine(f"sqlite:///{args.output_dir / 'replay.sqlite3'}")
    Base.metadata.create_all(engine)
    with args.input.open("rb") as source:
        input_digest = hashlib.file_digest(source, "sha256").hexdigest()
    encoded_head_bytes, versions = 0, Counter()
    latest = {}
    encode_seconds, restore_seconds = 0., 0.
    with Session(engine, expire_on_commit=False) as db:
        store = DocumentAnalysisRunStore(db)
        run, _ = store.create_run(owner_id="performance-replay", request_key="replay",
                                  filename="frozen-replay.docx", document_hash="d" * 64,
                                  root_class_iri="urn:Report")
        db.commit()
        token = store.claim(
            run.recognition_run_id, run.owner_id, actor="replay", worker_id="replay",
            lease_seconds=3600,
        )
        db.commit()
        with gzip.open(args.input, "rt") as source:
            for line in source:
                row = json.loads(line)
                kind, payload = row["artifact_kind"], row["payload"]
                latest[kind] = payload
                if kind not in {"ranking_state", "graph", "recognition_checkpoint"}:
                    continue
                versions[kind] += 1
                start = time.perf_counter()
                encoded = encode_state(store, run, token, payload)
                encode_seconds += time.perf_counter() - start
                start = time.perf_counter()
                restored = decode_state(store, run, encoded)
                restore_seconds += time.perf_counter() - start
                if content_hash(restored) != content_hash(payload):
                    raise ValueError(f"non-equivalent replay: {kind} revision {row['revision']}")
                encoded_head_bytes += size(encoded)
                write_head(store, run, token, kind, encoded)
                db.commit()
                print(json.dumps({"verified_versions": sum(versions.values()), "kind": kind}),
                      flush=True)
        blocks = list(db.scalars(select(DocumentAnalysisArtifact).where(
            DocumentAnalysisArtifact.artifact_kind == "state_block"
        )))
        block_bytes = sum(size(block.payload) for block in blocks)
        # Measure the same service/projection using only the exported final boundary.
        for kind in ("ontology_snapshot", "graph", "ranking_state"):
            write_head(store, run, token, kind, latest[kind])
        run.graph_snapshot_id = latest["graph"]["snapshot_id"]
        db.commit()
        run_id = run.recognition_run_id
        # One compatibility read supplies the expected projection, without
        # benchmarking the old implementation or assessing performance uplift.
        with Session(engine) as reader:
            application = DocumentAnalysisApplication(reader, ontology_engine=object())
            expected_response = application.graph_response(
                application.get_run(run_id, "performance-replay"),
                projection="effective_affirmed",
            )
        for kind in ("graph", "ranking_state"):
            publish_read_artifacts(store, run, token, kind=kind, payload=latest[kind],
                                   status="partial", event_head=run.event_head)
        db.commit()
        compact_timing, new_response = timing(engine, run_id, args.samples)
        for value in (expected_response, new_response):
            for key in ("run_revision", "artifact_revision", "event_head"):
                value.pop(key, None)
        if expected_response != new_response:
            raise ValueError("public graph projection changed")
        compact_bytes = sum(size(db.get(DocumentAnalysisArtifact, store.get_artifact(
            run_id, run.owner_id, kind).artifact_id).payload)
            for kind in ("public_graph", "ranking_summary"))
    report = {
        "input_sha256": input_digest, "python": platform.python_version(),
        "platform": platform.platform(), "versions": dict(versions),
        "models_called": 0, "all_restored_payloads_equal": True,
        "public_projection_equal": True,
        "new_json_bytes": block_bytes + encoded_head_bytes, "immutable_block_count": len(blocks),
        "immutable_block_json_bytes": block_bytes, "encoded_head_json_bytes": encoded_head_bytes,
        "new_internal_read_json_bytes": compact_bytes,
        "compact_service": compact_timing,
        "encode_total_seconds": encode_seconds, "restore_total_seconds": restore_seconds,
        "limitations": ["JSON logical bytes, not disk or WAL",
                        "SQLite isolated service replay, not production PostgreSQL/HTTP/browser",
                        "saved partial run, no new recognition or expert quality measurement",
                        "new format diagnostics only; no old-implementation speed comparison"],
    }
    (args.output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
