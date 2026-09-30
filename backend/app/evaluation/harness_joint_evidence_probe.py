"""Joint local-reference evidence: paired GPT selection, counterexamples, fresh Engines."""

import argparse
import getpass
import json
import shutil
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from app.evaluation.harness_model_comparison import (
    gateway_call,
    request_body,
    score_state,
    validate_output,
    write,
)
from app.evaluation.harness_referent_decision_probe import saved_case, synthetic_case
from app.services.document_harness.ontology import identity_guidance, legal_property
from app.services.document_harness.protocols import (
    INSTRUCTIONS,
    PROTOCOL,
    ReferentCandidates,
    stage_schema,
)
from app.services.document_harness.referents import resolve_selection, validate_partitions


def build_cases(root, source):
    cases = {"members": saved_case(source, "members", 27),
             "parallel_plan": saved_case(source, "parallel_plan", 36)}
    texts = {
        "compound": "该区域的完整管理编号为644/642，斜杠属于编号，指一个车间。",
        "renamed": "644/642车间中，644为旧编号，642为现编号，两个编号指同一个车间。",
        "explicit_ambiguity": (
            "记录中的644/642车间可能指一个复合编号区域，也可能指两个区域，当前资料无法确定。"
        ),
        "substring": "计划于1644/6420车间完成本次临床样品的生产。",
        "wrong_level": "产品批号为644/642，该数字组不指车间。计划在制剂车间生产。",
    }
    for name, text in texts.items():
        cases[name] = synthetic_case(root, cases["members"], name, text)
    cases["renamed"]["payload"]["proposal"]["partitions"].append({
        "id": "P3", "members": [{"id": "M4", "expression_ids": ["E2", "E3"]}],
        "reason": "两个编号表达也可能属于同一个对象。",
    })
    # Explicitly coordinated source text must not become negative evidence when records lack.
    for name in ("no_source", "partial_source"):
        cases[name] = deepcopy(cases["parallel_plan"])
        payload = cases[name]["payload"]
        if name == "no_source":
            payload.update(key_candidates=[], lookup_capabilities=[], source_status=[],
                           issues=["no_queryable_mapping"],
                           lookup_feedback={"results": [], "issues": ["no_queryable_mapping"]})
        else:
            payload["key_candidates"] = [c for c in payload["key_candidates"] if not any(
                k["value"] == "642" for k in c["key_occurrences"]
            )]
            expressions = {e["id"]: cases[name]["spans"][e["span_id"]]["text"]
                           for e in payload["proposal"]["expressions"]}
            for row in payload["lookup_feedback"]["results"]:
                if expressions[row["query_id"]] == "642":
                    row.update(outcome="no_match", candidates=[], total=0)
    expected = {name: [["642"], ["644"]]
                for name in ("members", "parallel_plan", "no_source", "partial_source")}
    expected.update(compound=[["644/642"]], renamed=[["642", "644"]],
                    explicit_ambiguity=[], substring=[], wrong_level=[])
    return cases, expected


def selection_inputs(case):
    payload = case["payload"]
    answer = ReferentCandidates.model_validate(payload["proposal"])
    card = case["catalog"].classes[payload["class_definition"]["iri"]]
    properties = [p["iri"] for p in identity_guidance(card)["identity_properties"]
                  if legal_property(case["catalog"], card.iri, p["iri"])]
    refs = validate_partitions(answer, case["spans"], case["ir"], properties, case["identifiers"])
    schema = stage_schema("referent_selection", source_ids=[s["source_id"]
                          for s in payload["sources"]],
                          partition_ids=[p.id for p in answer.partitions], span_ids=case["spans"])
    return answer, refs, schema


def run_engines(args, cases, invoke, summaries, started):
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.services.document_analysis.run_store import content_hash
    from app.services.document_harness.controller import Engine
    from app.services.document_harness.lookup_adapter import lookup_current
    from app.services.document_harness.ontology import freeze_catalog
    from app.services.ontology_engine import OntologyEngine

    root = args.output
    shutil.copytree(args.source / "ontology", root / "ontology")
    world = OntologyEngine(ontology_dir=root / "ontology", store_path=root / "world.sqlite3")
    world.load()
    try:
        # Only access the non-sensitive recognition budget/ranking policy from prior artifacts.
        previous = json.loads((args.source / "manifest.json").read_text())
        policy_source = Path(previous["source"])
        with SessionLocal(autoflush=False) as db:
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            for name in ("members", "parallel_plan"):
                case = cases[name]
                catalog, ir = case["catalog"], case["ir"]
                if freeze_catalog(world, catalog.root_class_iri).snapshot_id != catalog.snapshot_id:
                    raise ValueError("frozen_ontology_mismatch")
                policy = json.loads((policy_source / f"baseline-{name}-input.json").read_text())[
                    "policy"]
                rankings = json.loads((args.source / f"{name}-rankings.json").read_text())
                write(root / f"{name}-rankings.json", rankings)
                write(root / f"{name}-policy.json", policy)
                queries, call_count = [], 0

                def rank(cards, payload, budget):
                    key = content_hash({"snapshot_id": cards.snapshot_id, "input": payload,
                                        "card_budget": budget, "policy": policy["card_ranking"]})
                    if key not in rankings:
                        raise ValueError("frozen_ranking_input_changed")
                    return deepcopy(rankings[key])

                def query(operation, cards, argument):
                    result = lookup_current(db, world, operation, cards, argument)
                    queries.append({"operation": operation, "argument": argument,
                                    "result": deepcopy(result)})
                    write(root / f"{name}-queries.json", queries)
                    return result

                def ask(stage, payload, schema):
                    nonlocal call_count
                    call_count += 1
                    if call_count > 22:
                        raise RuntimeError("full_engine_call_budget_exhausted")
                    return invoke(name, "full_engine", stage, payload, schema)

                engine = Engine(
                    ir=ir, catalog=catalog, state={}, save=lambda _: None, invoke=ask,
                    should_stop=lambda: monotonic() - started > args.seconds_limit,
                    max_input_tokens=policy["max_input_tokens"], rank=rank, lookup=query,
                )
                row = {"case": name, "phase": "full_engine", "error": None}
                try:
                    engine.run()
                except Exception as exc:
                    # Model errors have already been sanitized by invoke.
                    row["error"] = str(exc)[:1000]
                write(root / f"{name}-state.json", engine.state)
                row.update(score_state(engine.state, catalog, name))
                row.update(calls=call_count, selections=[{
                    k: w.get(k) for k in ("selection", "selected_partition_id", "selection_issue")
                } for w in engine.state.get("referent_work", {}).values()],
                    source_identity_statuses=sorted({
                        b["identity_status"] for e in engine.state["entities"].values()
                        for b in e.get("identity_binding", {}).get("identifiers", [])
                    }))
                # Full target requires a body plan, not merely a supplied document root.
                row["full_target_ok"] = row["full_target_ok"] and row["body_plan_found"]
                summaries.append(row)
                write(root / "summary.json", summaries)
                print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        world.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://172.22.0.1:31080/v1")
    parser.add_argument("--model", default="gpt-6-sol")
    parser.add_argument("--call-limit", type=int, default=60)
    parser.add_argument("--seconds-limit", type=int, default=1800)
    args = parser.parse_args()
    root = args.output
    root.mkdir(parents=True, exist_ok=False)
    for name in (Path(__file__).name, "harness_model_comparison.py",
                 "harness_referent_decision_probe.py"):
        shutil.copy2(Path(__file__).with_name(name), root / name)
    shutil.copytree(Path(__file__).parents[1] / "services/document_harness", root / "runtime",
                    ignore=shutil.ignore_patterns("__pycache__"))
    cases, expected = build_cases(root, args.source)
    for name, case in cases.items():
        selection_inputs(case)
        write(root / f"{name}-input.json", case["ir"].model_dump(mode="json"))
        write(root / f"{name}-ontology.json", case["catalog"].model_dump(mode="json"))
        write(root / f"{name}-payload.json", case["payload"])
    baseline = json.loads((args.baseline / "call-001.json").read_text())
    old_instructions = baseline["request_body"]["instructions"]
    assert baseline["schema"] == selection_inputs(cases["members"])[2]
    old_input = json.loads(baseline["request_body"]["input"][0]["content"][0]["text"])["input"]
    assert old_input == cases["members"]["payload"]
    write(root / "manifest.json", {
        "created_at": datetime.now(UTC), "protocol": PROTOCOL, "model": args.model,
        "base_url": args.base_url, "source": str(args.source), "baseline": str(args.baseline),
        "instructions": INSTRUCTIONS, "old_instructions": old_instructions,
        "call_limit": args.call_limit, "seconds_limit": args.seconds_limit,
        "method": "same-input/schema paired selection; synthetic counterexamples; "
                  "two empty Engines with live read-only Mock and frozen ontology/ranking",
        "reference_is_model_input": False, "reference_status": "developer_regression",
        "production_service_restarted": False, "production_model_changed": False,
    })
    write(root / "reference.json", {"selection_member_values": expected,
                                    "full_engine": "two identifiers; body plan; "
                                    "slash participation unresolved; explicit parallel accepted"})
    secret = getpass.getpass("API key (memory only): ")
    calls, summaries, started = [], [], monotonic()

    def invoke(name, phase, stage, payload, schema, instructions=None):
        if len(calls) >= args.call_limit or monotonic() - started > args.seconds_limit:
            raise RuntimeError("joint_evidence_probe_budget_exhausted")
        body = request_body(stage, payload, schema, args.model)
        if instructions is not None:
            body["instructions"] = instructions
        row = {"number": len(calls) + 1, "case": name, "phase": phase, "stage": stage,
               "payload": deepcopy(payload), "schema": schema, "request_body": body}
        calls.append(row)
        try:
            result = gateway_call(body, base_url=args.base_url, secret=secret)
            row["result"] = result
            if result["error"]:
                raise RuntimeError(result["error"])
            validate_output(stage, payload, schema, result["output"])
            return result["output"]
        except Exception as exc:
            row["error"] = str(exc).replace(secret, "[REDACTED]")[:1000]
            raise RuntimeError(row["error"]) from None
        finally:
            write(root / f"call-{row['number']:03d}.json", row)
            write(root / "calls.json", [{k: v for k, v in c.items()
                                         if k not in {"payload", "schema", "request_body"}}
                                        for c in calls])
            print(json.dumps({k: row.get(k) for k in (
                "number", "case", "phase", "stage", "error",
            )}), flush=True)

    schedule = [("members", f"{arm}_{repeat + 1}", arm == "old") for repeat in range(3)
                for arm in (("old", "new") if repeat % 2 == 0 else ("new", "old"))]
    schedule += [("parallel_plan", f"new_{repeat + 1}", False) for repeat in range(3)]
    schedule += [(name, "boundary", False) for name in cases
                 if name not in {"members", "parallel_plan"}]
    try:
        for name, phase, old in schedule:
            case = cases[name]
            answer, refs, schema = selection_inputs(case)
            row = {"case": name, "phase": phase, "target_met": False, "error": None}
            try:
                output = invoke(name, phase, "referent_selection", case["payload"], schema,
                                old_instructions if old else None)
                decision = validate_output("referent_selection", case["payload"], schema, output)
                selected, _, issue = resolve_selection(
                    decision, answer, refs, case["spans"], case["ir"],
                )
                values = sorted(sorted(refs[e]["text"] for e in m.expression_ids)
                                for m in selected.members) if selected else []
                row.update(verdict=decision.verdict, confidence=decision.confidence,
                           selected_partition_id=selected.id if selected else None,
                           selection_issue=issue, member_values=values, reason=decision.reason,
                           target_met=values == expected[name] and (
                               decision.verdict == "supported" and issue is None
                               if expected[name] else decision.verdict == "unresolved"))
            except Exception as exc:
                row["error"] = str(exc).replace(secret, "[REDACTED]")[:1000]
            row["call_number"] = len(calls)
            summaries.append(row)
            write(root / "summary.json", summaries)
            print(json.dumps(row, ensure_ascii=False), flush=True)
        run_engines(args, cases, invoke, summaries, started)
    finally:
        secret = ""
        write(root / "summary.json", summaries)


if __name__ == "__main__":
    main()
