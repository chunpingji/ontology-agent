"""Replay frozen JSON through v2/v3 storage in a new isolated SQLite database.

No model calls, online database access or changes to the input snapshot. This
measures storage CPU/SQL/bytes, not end-to-end recognition or semantic quality.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from hashlib import sha256
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.db import Base
from app.services.document_analysis.incremental_state import encode_incremental_state
from app.services.document_analysis.run_store import DocumentAnalysisRunStore, content_hash
from app.services.document_analysis.state_artifacts import decode_state, encode_state
from app.services.extraction.ontology_guided.state_delta import freeze_json


def measure(input_path, output_dir, repeats=3, scales=(1, 2), *, immutable=False):
    output_dir.mkdir(parents=True, exist_ok=False)
    raw = input_path.read_bytes()
    states = json.loads(raw)
    engine = create_engine(f"sqlite:///{output_dir / 'isolated-replay.sqlite'}")
    Base.metadata.create_all(engine)
    sql = {"select": 0, "write": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def count_sql(connection, cursor, statement, parameters, context, executemany):
        name = "select" if statement.lstrip().upper().startswith("SELECT") else "write"
        sql[name] += 1

    result = {
        "input_sha256": sha256(raw).hexdigest(),
        "input_bytes": len(raw),
        "database": "isolated SQLite",
        "model_calls": 0,
        "snapshot_mode": "immutable" if immutable else "mutable",
        "scope": "storage only; fixed scalar change around frozen real state",
        "cases": [],
    }
    for kind, state in states.items():
        for scale in scales:
            # Repeated history is a storage stress case, never a valid semantic
            # checkpoint or a claimed larger-document recognition experiment.
            frozen = state if scale == 1 else [state for _ in range(scale)]
            freeze_started = time.perf_counter()
            if immutable:
                frozen = freeze_json(frozen)
            freeze_seconds = time.perf_counter() - freeze_started
            initial = {"frozen_state": frozen, "replay_tick": 0}
            for version in ((3,) if immutable else (2, 3)):
                with Session(engine) as db:
                    store = DocumentAnalysisRunStore(db)
                    run, _ = store.create_run(
                        owner_id="isolated-state-benchmark",
                        request_key=f"{kind}:{scale}:{version}",
                        filename="frozen-state.json",
                        document_hash=sha256(raw).hexdigest(),
                        root_class_iri="urn:StorageReplay",
                    )
                    db.commit()
                    token = store.claim(
                        run.recognition_run_id,
                        run.owner_id,
                        actor="benchmark",
                        worker_id="isolated-replay",
                        lease_seconds=3600,
                    )
                    db.commit()
                    run.run_fingerprint = content_hash([kind, scale, version])
                    db.commit()
                    samples = []
                    # v3 includes its periodic baseline at revision 33. v2 hot
                    # samples suffice to characterize its repeated full traversal.
                    count = 33 if version == 3 and scale == 1 else repeats + 1
                    for index in range(count):
                        value = {**initial, "replay_tick": index}
                        before = dict(sql)
                        start = time.perf_counter()
                        encoded = (
                            encode_state(store, run, token, value)
                            if version == 2
                            else encode_incremental_state(store, run, token, value, kind=kind)
                        )
                        digest = content_hash(encoded)
                        store.update_artifact(
                            run.recognition_run_id,
                            run.owner_id,
                            token,
                            artifact_kind=kind,
                            expected_revision=index,
                            artifact_hash=digest,
                            status="ready",
                            artifact_id=f"{run.recognition_run_id}:{kind}:{index + 1}:{digest}",
                            payload=encoded,
                            event_head=run.event_head,
                        )
                        db.commit()
                        elapsed = time.perf_counter() - start
                        sample = {
                            "revision": index + 1,
                            "seconds": elapsed,
                            "envelope_bytes": len(
                                json.dumps(
                                    encoded, ensure_ascii=False, separators=(",", ":")
                                ).encode()
                            ),
                            "sql": {k: sql[k] - before[k] for k in sql},
                            "baseline": version == 3 and index % 32 == 0,
                        }
                        samples.append(sample)
                        if index in (0, repeats, 31, 32):
                            print(
                                json.dumps(
                                    {"kind": kind, "scale": scale, "version": version, **sample},
                                    ensure_ascii=False,
                                ),
                                flush=True,
                            )
                        if index == count - 1 or index == 31:
                            cold = DocumentAnalysisRunStore(db)
                            start = time.perf_counter()
                            restored = decode_state(cold, run, encoded)
                            sample["cold_recovery_seconds"] = time.perf_counter() - start
                            sample["equal"] = restored == value
                            if not sample["equal"]:
                                raise AssertionError("storage replay changed frozen content")
                    hot = [s for s in samples[1:] if not s["baseline"]][:repeats]
                    result["cases"].append(
                        {
                            "kind": kind,
                            "immutable_export_seconds": freeze_seconds,
                            "scale": scale,
                            "version": version,
                            "logical_bytes": len(
                                json.dumps(
                                    initial, ensure_ascii=False, separators=(",", ":")
                                ).encode()
                            ),
                            "hot_median_seconds": statistics.median(s["seconds"] for s in hot),
                            "hot_max_seconds": max(s["seconds"] for s in hot),
                            "samples": samples,
                        }
                    )
                    (output_dir / "result.json").write_text(
                        json.dumps(
                            result,
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
    engine.dispose()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--immutable", action="store_true")
    args = parser.parse_args()
    measure(args.input, args.output, immutable=args.immutable)
