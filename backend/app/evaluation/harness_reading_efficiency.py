"""Measure the deployed Harness reading loop on frozen input, without editing a live run."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from threading import Lock
from time import monotonic
from uuid import uuid4

from sqlalchemy import text

from app.db import SessionLocal
from app.services.document_harness.continuation import restore_window_plan
from app.services.document_harness.controller import Engine
from app.services.document_harness.lookup_adapter import lookup_current
from app.services.document_harness.model import call_model, freeze_policy, request_size
from app.services.document_harness.ontology import SchemaCatalog
from app.services.document_harness.ranking import CardRanker
from app.services.extraction.document_ir import DocumentIR
from app.services.llm.model_runtime import model_scope
from app.services.ontology_engine import OntologyEngine


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--windows", type=int, default=0, help="0 means all original windows")
    parser.add_argument("--concurrency", type=int, choices=(1, 2), default=2)
    parser.add_argument("--max-calls", type=int, default=160)
    parser.add_argument("--seconds", type=int, default=2400)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    baseline = json.loads(args.input.read_text())
    ir = DocumentIR.model_validate(baseline["ir"])
    catalog = SchemaCatalog.model_validate(baseline["catalog"])
    policy = freeze_policy()
    assert policy["model"] == baseline["policy"]["model"]
    assert policy["model_revision"] == baseline["policy"]["model_revision"]
    policy["execution_policy"]["reading_concurrency"] = args.concurrency
    run_id = "reading-test-" + uuid4().hex
    write(args.output / "manifest.json", {
        "run_id": run_id, "baseline_run": baseline["run_id"], "policy": policy,
        "document_hash": ir.document_hash, "catalog_snapshot": catalog.snapshot_id,
        "root_windows": args.windows or "all", "max_calls": args.max_calls,
        "seconds_limit": args.seconds, "scope": "reading_only",
        "live_document_run_modified": False, "reference_is_input": False,
    })
    world = OntologyEngine(store_path=args.output / "world.sqlite3")
    world.load()
    ranker, calls, queries = CardRanker(policy["card_ranking"]), [], []
    lock = Lock()
    started = monotonic()
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))

        def query(operation, schema, argument):
            begin = monotonic()
            result = lookup_current(db, world, operation, schema, argument)
            queries.append({"operation": operation, "seconds": monotonic() - begin,
                            "issues": result.get("issues", [])})
            return result

        def invoke(stage, payload, schema):
            with lock:
                if len(calls) >= args.max_calls:
                    raise RuntimeError("evaluation_call_budget_exhausted")
                row = {"number": len(calls) + 1, "stage": stage,
                       "mode": payload.get("lookup_mode", "plain"),
                       "started": monotonic() - started,
                       "request_bytes": request_size(stage, payload, schema),
                       "source_characters": sum(len(s["text"]) for s in payload["sources"]),
                       "guidance_bytes": len(json.dumps(
                           payload.get("schema_guidance", {}), ensure_ascii=False).encode())}
                calls.append(row)
            record = {"payload": payload, "schema": schema}
            try:
                with model_scope(stage=stage):
                    result = call_model(stage, payload, schema, policy)
                record["result"] = result
                row.update(error=result["error"], usage=result.get("usage"),
                           provider_status=result["raw_response"].get("response_status"))
                if result["error"]:
                    raise RuntimeError(result["error"])
                return result["output"]
            except Exception as exc:
                row["error"] = str(exc)[:300]
                raise
            finally:
                row["finished"] = monotonic() - started
                write(args.output / f"call-{row['number']:03}.json", {**record, **row})
                print(json.dumps({k: row.get(k) for k in (
                    "number", "mode", "started", "finished", "request_bytes", "error",
                )}), flush=True)

        def save_progress(changes):
            cursor = changes.get("cursor", {}).get("main")
            if cursor and "reading" in cursor:
                write(args.output / "progress.json", {
                    "seconds": round(monotonic() - started, 3), "calls": len(calls),
                    "phase": cursor.get("phase"), "reading": cursor["reading"],
                    "reading_windows": cursor.get("reading_windows"),
                })

        engine = Engine(
            ir=ir, catalog=catalog, state={}, invoke=invoke, save=save_progress,
            rank=ranker.rank, lookup=query, policy=policy,
            should_stop=lambda: monotonic() - started > args.seconds or (
                engine.state.get("cursor", {}).get("main", {}).get("phase") == "entities"
            ),
        )
        roots = sorted((w for w in baseline["windows"].values() if w["parent_id"] is None),
                       key=lambda w: w["order"])
        engine.windows = [restore_window_plan(ir, w["plan"])
                          for w in (roots[:args.windows] if args.windows else roots)]
        error = None
        try:
            with model_scope(run_id=run_id, bind=db.get_bind(), on_harness_event=lambda *_: None):
                engine.run()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            ranker.close()
            if engine._memory_calls:
                engine._memory_calls.close()
            world.close()
        elapsed = monotonic() - started
        intervals = sorted([(r["started"], 1) for r in calls]
                           + [(r["finished"], -1) for r in calls])
        durations, active, previous = defaultdict(float), 0, 0
        for at, delta in intervals:
            durations[active] += at - previous
            active += delta
            previous = at
        durations[active] += elapsed - previous
        summary = {
            "run_id": run_id, "error": error, "seconds": round(elapsed, 3),
            "calls": len(calls), "modes": dict(Counter(r["mode"] for r in calls)),
            "input_tokens": sum((r.get("usage") or {}).get("input_tokens") or 0 for r in calls),
            "output_tokens": sum((r.get("usage") or {}).get("output_tokens") or 0 for r in calls),
            "concurrency_seconds": {k: round(v, 3) for k, v in durations.items()},
            "query_seconds": round(sum(r["seconds"] for r in queries), 3),
            "mean_guidance_bytes": round(sum(r["guidance_bytes"] for r in calls) / len(calls))
            if calls else 0,
            "cursor": engine.state.get("cursor", {}).get("main"),
            "reading_complete": engine.state.get("cursor", {}).get("main", {}).get(
                "reading", {},
            ).get("complete", False),
            "entities": len(engine.state.get("entities", {})) - 1,
            "fields": len(engine.state.get("fields", {})),
            "observation_reasons": dict(Counter(r.get("reason") for r in
                                                engine.state.get("observations", {}).values())),
        }
        write(args.output / "state.json", engine.state)
        write(args.output / "queries.json", queries)
        write(args.output / "summary.json", summary)
        print(json.dumps({k: summary[k] for k in (
            "run_id", "error", "seconds", "calls", "modes", "input_tokens", "output_tokens",
            "concurrency_seconds", "query_seconds", "mean_guidance_bytes", "cursor",
        )}, ensure_ascii=False), flush=True)
        if error or summary["cursor"]["phase"] != "entities":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
