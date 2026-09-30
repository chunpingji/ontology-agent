"""Isolated comparison of current Harness prompts and complete controller runs.

Credentials are read from a hidden terminal prompt and never serialized. Both models
receive the same schema adaptation; the original production schema is still validated.
"""

from __future__ import annotations

import argparse
import getpass
import json
import shutil
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

import httpx
import jsonschema

from app.services.document_harness.protocols import INSTRUCTIONS, PROTOCOL, STAGES, discovery_model


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")


def wire_schema(schema):
    """Make defaults explicit and inline annotated refs for the gateway's strict subset.

    Required default-valued fields are a shared transport constraint, not a semantic
    repair. The original schema is saved and still used for local validation.
"""
    definitions = schema.get("$defs", {})

    def visit(value):
        if isinstance(value, list):
            return [visit(v) for v in value]
        if not isinstance(value, dict):
            return value
        value = deepcopy(value)
        if "$ref" in value and len(value) > 1:
            ref = value.pop("$ref")
            if not ref.startswith("#/$defs/"):
                raise ValueError("unsupported_nonlocal_schema_reference")
            value = {**deepcopy(definitions[ref.rsplit("/", 1)[1]]), **value}
        result = {k: visit(v) for k, v in value.items() if k != "default"}
        if result.get("type") == "object" and "properties" in result:
            result["required"] = list(result["properties"])
            result["additionalProperties"] = False
        if "type" not in result and "enum" in result:
            values = result["enum"]
            if values and all(isinstance(v, str) for v in values):
                result["type"] = "string"
            elif values and all(v is None or isinstance(v, str) for v in values):
                result["type"] = ["string", "null"]
        if "type" not in result and isinstance(result.get("const"), str):
            result["type"] = "string"
        elif "type" not in result and "const" in result and result["const"] is None:
            result["type"] = "null"
        return result

    return visit(schema)


def request_body(stage, payload, schema, model):
    adapted = wire_schema(schema)
    return {
        "model": model, "instructions": INSTRUCTIONS[stage],
        "input": [{"role": "user", "content": [{
            "type": "input_text", "text": json.dumps(
                {"input": payload, "schema": adapted},
                ensure_ascii=False, separators=(",", ":"),
            ),
        }]}],
        "stream": True, "store": False, "max_output_tokens": 16384,
        "reasoning": {"effort": "none"}, "temperature": 0.1,
        "text": {"format": {"type": "json_schema", "name": "harness_" + stage,
                            "strict": True, "schema": adapted}},
    }


def gateway_call(body, *, base_url, secret, timeout=240):
    started, events, response_data = monotonic(), [], {}
    with httpx.Client(trust_env=False, timeout=timeout, follow_redirects=False) as client:
        with client.stream("POST", base_url.rstrip("/") + "/responses", json=body,
                           headers={"Authorization": "Bearer " + secret}) as response:
            if response.status_code != 200:
                raw = response.read().decode().replace(secret, "[REDACTED]")
                try:
                    detail = json.loads(raw)
                except ValueError:
                    detail = {"non_json_error": raw[:1000]}
                return {"error": "http_" + str(response.status_code), "detail": detail,
                        "seconds": monotonic() - started, "usage": {}, "output": None}
            for line in response.iter_lines():
                if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                    continue
                event = json.loads(line[5:].strip().replace(secret, "[REDACTED]"))
                # Raw terminal output carries the text; no need to duplicate each token delta.
                if not event.get("type", "").endswith(".delta"):
                    events.append(event)
                if event.get("type") in (
                    "response.completed", "response.failed", "response.incomplete",
                ):
                    response_data = event.get("response", event)
                elif event.get("type") == "error":
                    response_data = event
    result = {"seconds": monotonic() - started, "raw_response": response_data,
              "events": events, "usage": response_data.get("usage") or {}, "output": None,
              "error": None}
    if response_data.get("status") != "completed":
        result["error"] = "gateway_response_not_completed"
        return result
    # This gateway leaves terminal output empty and sends complete items in SSE.
    items = response_data.get("output") or [
        e["item"] for e in sorted(events, key=lambda e: e.get("output_index", 0))
        if e.get("type") == "response.output_item.done"
    ]
    text = "".join(
        part.get("text", "") for item in items
        if item.get("type") == "message" for part in item.get("content", [])
        if part.get("type") == "output_text"
    )
    try:
        from app.services.document_harness.model import _unique_object
        result["output"] = json.loads(text, object_pairs_hook=_unique_object)
    except (ValueError, TypeError):
        result["error"] = "gateway_invalid_json"
    return result


def validate_output(stage, payload, schema, output):
    jsonschema.validate(output, schema)
    model = discovery_model(payload.get("lookup_mode")) if stage == "discover" else STAGES[stage]
    return model.model_validate(output)


def saved_rows(source, case, kind, domain):
    return {r["payload"]["key"]: r["payload"]["value"]
            for r in json.loads((source / f"{case}-{kind}.json").read_text())
            if r["domain"] == "harness:" + domain}


def preflight(args):
    args.output.mkdir(parents=True, exist_ok=False)
    request = next(v for v in saved_rows(args.source, "parallel_plan", "requests", "calls").values()
                   if v["stage"] == "referent_candidates")
    if "evidence_spans" not in request["payload"]:
        raise ValueError("current_protocol_baseline_required")
    body = request_body(request["stage"], request["payload"], request["schema"], args.model)
    write(args.output / "request.json", body)
    secret = getpass.getpass("API key (memory only): ")
    try:
        result = gateway_call(body, base_url=args.base_url, secret=secret)
        write(args.output / "response.json", result)
        error = result["error"]
        if not error:
            validate_output(
                request["stage"], request["payload"], request["schema"], result["output"],
            )
        print(json.dumps({"error": error, "detail": result.get("detail"),
                          "seconds": result["seconds"], "usage": result["usage"],
                          "model": result.get("raw_response", {}).get("model")},
                         ensure_ascii=False), flush=True)
    finally:
        secret = ""


def score_candidates(output, ir, payload, case):
    from app.services.document_harness.referents import validate_partitions
    from app.services.document_harness.source import build_windows, reference

    answer = STAGES["referent_candidates"].model_validate(output)
    properties = [p["iri"] for p in payload["class_definition"]["identity_properties"]]
    window = build_windows(ir)[0]
    sources = {s["source_id"]: s for s in window.sources}
    spans = {s["span_id"]: reference(ir, sources[s["source_id"]]["evidence_id"],
                                    s["start"], s["end"]) for s in payload["evidence_spans"]}
    refs = validate_partitions(answer, spans, ir, properties,
                              [s["span_id"] for s in payload["evidence_spans"]
                               if s["identifier_candidate"]])
    expressions = {key: ref["text"] for key, ref in refs.items()}
    partitions = [sorted(sorted(expressions[key] for key in member.expression_ids)
                         for member in p.members) for p in answer.partitions]
    members = [["642"], ["644"]] in partitions
    compound = [["644/642"]] in partitions
    allowed = {"644", "642", "644/642"} if case == "members" else {"644", "642"}
    return {"grounded": True, "expressions": list(expressions.values()),
            "partitions": partitions, "members_partition": members,
            "compound_partition": compound,
            "pass": members and (compound or case != "members")
            and set(expressions.values()) <= allowed}


def score_state(state, catalog, case):
    from app.services.document_harness.coreference import project_coreferences
    from app.services.document_harness.source import references_cover

    result = {key: deepcopy(list(state.get(key, {}).values())) for key in (
        "entities", "properties", "relations", "relation_groups",
    )}
    result["observations"] = []
    project_coreferences(result, list(state.get("coreferences", {}).values()),
                        catalog.model_dump(mode="json")["classes"])
    facility = "https://ontology.pharma-gmp.cn/slpra/facility/"
    development = "https://ontology.pharma-gmp.cn/slpra/drug-development/"
    areas = [e for e in result["entities"] if e.get("class_iri") == facility + "ProductionArea"
             and e["state"] == "accepted"]
    props = [p for p in result["properties"]
             if p["predicate_iri"] == facility + "areaIdentifier" and p["state"] == "accepted"]
    values = {e["id"]: sorted(p["value"] for p in props if p["subject_id"] == e["id"])
              for e in areas}
    ownership = bool(props) and all(
        any(e["id"] == p["subject_id"] and e.get("referent") and
            references_cover(r, [e["referent"]]) for e in areas)
        for p in props for r in p.get("value_evidence", [])
    ) and all(p.get("value_evidence") for p in props)
    identifiers_ok = (len(areas) == 2 and len(props) == 2 and ownership
                      and sorted(values.values()) == [["642"], ["644"]])
    plans = [e for e in result["entities"]
             if e.get("class_iri") == development + "ClinicalSampleProductionPlan"
             and e["state"] == "accepted"]
    groups = [{k: g.get(k) for k in (
        "subject_id", "object_ids", "predicate_iri", "participation", "selection", "timing",
        "state", "timing_state", "reason", "timing_reason",
    )} for g in result["relation_groups"]]
    relevant = [g for g in groups if g["subject_id"] in {p["id"] for p in plans}
                and set(g["object_ids"]) == {e["id"] for e in areas}
                and g["predicate_iri"] == development + "producedInArea"]
    if case == "parallel_plan":
        relation_ok = any(g["participation"] == "all" and g["timing"] == "parallel"
                          and g["state"] == g["timing_state"] == "accepted" for g in relevant)
    else:
        relation_ok = bool(relevant) and all(g["state"] != "accepted" for g in relevant)
    return {"scope_complete": state.get("cursor", {}).get("main", {}).get("scope_complete", False),
            "areas": [{"id": e["id"], "label": e["label"]} for e in areas],
            "identifiers": values, "identifier_ownership": bool(ownership),
            "identifiers_ok": identifiers_ok, "body_plan_found": any(
                e["role"] != "document_root" for e in plans),
            "plan_root_provided": any(e["role"] == "document_root" for e in plans),
            "relation_groups": groups, "relations": result["relations"],
            "relation_ok": relation_ok, "full_target_ok": identifiers_ok and bool(plans)
            and relation_ok and state.get("cursor", {}).get("main", {}).get(
                "scope_complete", False),
            "coreferences": result.get("coreferences", [])}


def compare(args):
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.services.document_analysis.run_store import content_hash
    from app.services.document_harness.controller import Engine
    from app.services.document_harness.lookup_adapter import lookup_current
    from app.services.document_harness.model import call_model
    from app.services.document_harness.ontology import SchemaCatalog, freeze_catalog
    from app.services.extraction.document_ir import DocumentIR
    from app.services.llm.model_runtime import model_scope
    from app.services.ontology_engine import OntologyEngine

    root = args.output
    root.mkdir(parents=True, exist_ok=False)
    shutil.copytree(args.ontology_dir, root / "ontology")
    shutil.copytree(Path(__file__).parents[1] / "services/document_harness", root / "runtime",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(__file__, root / "probe.py")
    world = OntologyEngine(ontology_dir=root / "ontology", store_path=root / "world.sqlite3")
    world.load()
    inputs = {}
    for case in ("members", "parallel_plan"):
        saved = saved_rows(args.source, case, "state", "input")["document"]
        catalog = SchemaCatalog.model_validate_json(
            (args.source / f"{case}-ontology.json").read_text(),
        )
        if freeze_catalog(world, catalog.root_class_iri).snapshot_id != catalog.snapshot_id:
            raise ValueError("baseline_ontology_changed")
        policy = json.loads((args.source / f"{case}-input.json").read_text())["policy"]
        if policy.get("protocol") != PROTOCOL:
            raise ValueError("current_protocol_baseline_required")
        inputs[case] = (DocumentIR.model_validate(saved), catalog, policy)
        for suffix in ("input", "ontology", "state", "requests", "results", "graph"):
            shutil.copy2(args.source / f"{case}-{suffix}.json",
                         root / f"baseline-{case}-{suffix}.json")
    write(root / "manifest.json", {
        "created_at": datetime.now(UTC), "model": args.model, "base_url": args.base_url,
        "original_user_base_url": "http://0.0.0.0:31080/v1", "baseline": str(args.source),
        "scope": "paired referent prompts, then fresh complete Engine with frozen card ranking",
        "stage_repeats": args.repeats, "full_runs_per_model_per_case": 1,
        "call_limit": args.call_limit, "seconds_limit": args.seconds_limit,
        "wire_schema": "both models: explicit defaults; inline refs with siblings; typed enums",
        "instructions": INSTRUCTIONS, "qwen_policy": inputs["members"][2],
        "sol_policy": {"reasoning": "none", "temperature": 0.1, "max_output_tokens": 16384},
        "reference_is_model_input": False, "reference_status": "developer_regression",
        "production_default_model_changed": False,
    })
    write(root / "reference.json", {
        "members": {"identifiers": [["644"], ["642"]], "body_plan": True,
                    "relation": "group required, participation unresolved; slash proves no AND/OR"},
        "parallel_plan": {"identifiers": [["644"], ["642"]], "plan_root": True,
                          "participation": "all", "timing": "parallel"},
    })
    secret = getpass.getpass("API key (memory only): ")
    calls, summaries = [], []
    started = monotonic()

    def invoke(model, case, phase, stage, payload, schema):
        if len(calls) >= args.call_limit or monotonic() - started > args.seconds_limit:
            raise RuntimeError("comparison_budget_exhausted")
        number = len(calls) + 1
        entry = {"number": number, "model": model, "case": case, "phase": phase, "stage": stage}
        calls.append(entry)
        path = root / f"call-{number:03}.json"
        record = {**entry, "payload": payload, "schema": schema, "wire_schema": wire_schema(schema)}
        write(path, record)
        print("CALL", number, model, case, phase, stage, flush=True)
        start = monotonic()
        try:
            if model == "qwen":
                with model_scope(run_id=root.name, task_id=str(number), stage=stage):
                    result = call_model(stage, payload, wire_schema(schema), inputs[case][2])
            else:
                body = request_body(stage, payload, schema, args.model)
                record["request_body"] = body
                result = gateway_call(body, base_url=args.base_url, secret=secret)
            record["result"] = result
            entry.update(seconds=result["seconds"], usage=result["usage"], error=result["error"])
            if result["error"]:
                raise RuntimeError(result["error"])
            validate_output(stage, payload, schema, result["output"])
            return result["output"]
        except Exception as exc:
            entry.update(error=str(exc).replace(secret, "[REDACTED]")[:1000])
            entry.setdefault("seconds", monotonic() - start)
            record["exception"] = entry["error"]
            raise
        finally:
            write(path, record)
            write(root / "calls.json", calls)

    try:
        for case, (ir, catalog, policy) in inputs.items():
            request = next(v for v in saved_rows(args.source, case, "requests", "calls").values()
                           if v["stage"] == "referent_candidates")
            for repeat in range(args.repeats):
                for model in (("qwen", "sol") if repeat % 2 == 0 else ("sol", "qwen")):
                    row = {"case": case, "model": model, "phase": "fixed_referent",
                           "repeat": repeat + 1, "pass": False}
                    try:
                        output = invoke(model, case, "fixed_referent", "referent_candidates",
                                        request["payload"], request["schema"])
                        row.update(score_candidates(output, ir, request["payload"], case))
                    except Exception as exc:
                        row["error"] = str(exc).replace(secret, "[REDACTED]")[:1000]
                    summaries.append(row)
                    write(root / "summary.json", summaries)
                    print("RESULT", json.dumps(row, ensure_ascii=False), flush=True)

        # One database snapshot keeps mapped sources identical across both model runs.
        with SessionLocal(autoflush=False) as db:
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            for case, (ir, catalog, policy) in inputs.items():
                rankings = saved_rows(args.source, case, "results", "rankings")

                def rank(cards, payload, budget):
                    key = content_hash({"snapshot_id": cards.snapshot_id, "input": payload,
                                        "card_budget": budget, "policy": policy["card_ranking"]})
                    if key not in rankings:
                        raise ValueError("frozen_ranking_input_changed")
                    return deepcopy(rankings[key])

                for model in (("sol", "qwen") if case == "members" else ("qwen", "sol")):
                    row = {"case": case, "model": model, "phase": "full_engine", "error": None}
                    changes, queries = [], []
                    initial_calls = len(calls)

                    def query(operation, cards, argument):
                        result = lookup_current(db, world, operation, cards, argument)
                        queries.append({"operation": operation, "argument": argument,
                                        "result": result})
                        write(root / f"{case}-{model}-queries.json", queries)
                        return result

                    def model_call(stage, payload, schema):
                        if len(calls) - initial_calls >= 30:
                            raise RuntimeError("per_run_call_limit")
                        return invoke(model, case, "full_engine", stage, payload, schema)

                    engine = Engine(
                        ir=ir, catalog=catalog, state={}, invoke=model_call,
                        save=lambda change: changes.append(deepcopy(change)),
                        should_stop=lambda: monotonic() - started > args.seconds_limit,
                        max_input_tokens=policy["max_input_tokens"], rank=rank, lookup=query,
                    )
                    try:
                        engine.run()
                    except Exception as exc:
                        row["error"] = str(exc).replace(secret, "[REDACTED]")[:1000]
                    write(root / f"{case}-{model}-state.json", engine.state)
                    row.update(score_state(engine.state, catalog, case))
                    row["calls"] = len(calls) - initial_calls
                    summaries.append(row)
                    write(root / "summary.json", summaries)
                    print("RESULT", json.dumps({k: row[k] for k in (
                        "case", "model", "phase", "error", "identifiers_ok", "relation_ok",
                        "body_plan_found", "scope_complete", "calls",
                    )}, ensure_ascii=False), flush=True)
    finally:
        secret = ""
        world.close()
        write(root / "summary.json", summaries)
        write(root / "calls.json", calls)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["preflight", "compare"])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:31080/v1")
    parser.add_argument("--model", default="gpt-6-sol")
    parser.add_argument("--ontology-dir", type=Path, default=Path("/app/ontology/slpra"))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--call-limit", type=int, default=90)
    parser.add_argument("--seconds-limit", type=int, default=2400)
    args = parser.parse_args()
    (preflight if args.mode == "preflight" else compare)(args)


if __name__ == "__main__":
    main()
