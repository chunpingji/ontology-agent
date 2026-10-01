"""Run one isolated Harness document through the deployed production GPT client."""

import argparse
import json
from pathlib import Path
from time import monotonic

from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.services.document_harness.controller import Engine
from app.services.document_harness.lookup_adapter import lookup_current
from app.services.document_harness.model import call_model, freeze_policy
from app.services.document_harness.ontology import SchemaCatalog
from app.services.document_harness.ranking import CardRanker
from app.services.extraction.document_ir import DocumentIR
from app.services.llm.model_runtime import model_scope
from app.services.ontology_engine import OntologyEngine


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", choices=("members", "parallel_plan"), default="members")
    parser.add_argument("--max-calls", type=int, default=24)
    args = parser.parse_args()
    root = args.output
    root.mkdir(parents=True, exist_ok=False)
    name = args.case
    ir = DocumentIR.model_validate_json((args.source / f"{name}-input.json").read_text())
    catalog = SchemaCatalog.model_validate_json((args.source / f"{name}-ontology.json").read_text())
    old_policy = json.loads((args.source / f"{name}-policy.json").read_text())
    policy = freeze_policy()
    assert policy["card_ranking"] == old_policy["card_ranking"]
    assert settings.local_llm_model == "gpt-6-sol"
    assert settings.local_llm_max_concurrency == 8
    assert settings.document_analysis_dispatch_concurrency == 8
    write(root / "manifest.json", {
        "case": name, "model": policy["model"], "base_url": settings.local_llm_base_url,
        "model_max_concurrency": settings.local_llm_max_concurrency,
        "dispatch_concurrency": settings.document_analysis_dispatch_concurrency,
        "input": str(args.source / f"{name}-input.json"), "max_calls": args.max_calls,
        "isolated_engine": True, "source_query": "live read-only transaction",
    })
    world = OntologyEngine(ontology_dir=args.source / "ontology",
                           store_path=root / "world.sqlite3")
    world.load()
    ranker = CardRanker(policy=policy["card_ranking"])
    started, calls, queries = monotonic(), [], []

    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))

        def query(operation, cards, argument):
            result = lookup_current(db, world, operation, cards, argument)
            queries.append({"operation": operation, "argument": argument, "result": result})
            write(root / "queries.json", queries)
            return result

        def invoke(stage, payload, schema):
            if len(calls) >= args.max_calls:
                raise RuntimeError("deployed_smoke_call_budget_exhausted")
            row = {"number": len(calls) + 1, "stage": stage}
            calls.append(row)
            try:
                with model_scope(on_harness_event=lambda *_: None, stage=stage):
                    result = call_model(stage, payload, schema, policy)
                row.update(error=result["error"], usage={
                    k: result["usage"].get(k) for k in ("input_tokens", "output_tokens")
                }, output=result["output"])
                if result["error"]:
                    raise RuntimeError(result["error"])
                return result["output"]
            except Exception as exc:
                cause = exc.__cause__
                provider_error = getattr(cause, "body", None)
                if isinstance(provider_error, dict):
                    provider_error = provider_error.get("error", provider_error)
                row.update(
                    error=str(exc)[:150],
                    exception_type=type(exc).__name__,
                    cause_type=type(cause).__name__ if cause else None,
                    http_status=getattr(cause, "status_code", None),
                    provider_error={
                        k: provider_error.get(k) for k in ("type", "code", "param", "message")
                    } if isinstance(provider_error, dict) else None,
                )
                raise
            finally:
                write(root / "calls.json", calls)
                print(json.dumps({k: row.get(k) for k in ("number", "stage", "error")}),
                      flush=True)

        engine = Engine(ir=ir, catalog=catalog, state={}, invoke=invoke,
                        save=lambda _: None, should_stop=lambda: False,
                        max_request_bytes=policy["execution_policy"]["wire_bytes_per_call"],
                        rank=ranker.rank, lookup=query, policy=policy)
        error = None
        try:
            engine.run()
        except Exception as exc:
            error = f"{type(exc).__name__}:{exc}"[:300]
        finally:
            ranker.close()
            world.close()
            write(root / "state.json", engine.state)
        facility = "https://ontology.pharma-gmp.cn/slpra/facility/"
        accepted = [e for e in engine.state.get("entities", {}).values()
                    if e.get("class_iri") == facility + "ProductionArea"
                    and e.get("state") == "accepted"]
        properties = [p for p in engine.state.get("properties", {}).values()
                      if p.get("predicate_iri") == facility + "areaIdentifier"
                      and p.get("state") == "accepted"]
        summary = {"error": error, "calls": len(calls),
                   "seconds": round(monotonic() - started, 3),
                   "scope_complete": engine.state.get("cursor", {}).get("main", {}).get(
                       "scope_complete"),
                   "accepted_area_labels": sorted(e["label"] for e in accepted),
                   "accepted_identifiers": sorted(p["value"] for p in properties),
                   "relation_groups": [{k: g.get(k) for k in (
                       "participation", "timing", "state", "timing_state",
                   )} for g in engine.state.get("relation_groups", {}).values()]}
        write(root / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        if error or not summary["scope_complete"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
