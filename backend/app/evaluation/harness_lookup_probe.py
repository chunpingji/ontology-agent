"""Real-model discovery ablation using production Harness and isolated mapped Mock rows."""

from __future__ import annotations

import argparse
import json
import shutil
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from docx import Document
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.db import Base
from app.models.mock_data import MockProductionArea
from app.models.ontology_meta import OntologyClass, OntologyClassMapping, OntologyPropertyBinding
from app.services.document_harness.controller import Engine
from app.services.document_harness.lookup_adapter import lookup_current
from app.services.document_harness.model import call_model, freeze_policy, request_size
from app.services.document_harness.ontology import freeze_catalog
from app.services.document_harness.protocols import INSTRUCTIONS
from app.services.document_harness.ranking import CardRanker
from app.services.document_harness.source import build_windows
from app.services.extraction.document_ir import build_document_ir
from app.services.extraction.docx_structure import parse_docx_structure
from app.services.llm.model_runtime import model_scope
from app.services.ontology_engine import OntologyEngine

FAC = "https://ontology.pharma-gmp.cn/slpra/facility/"
DEV = "https://ontology.pharma-gmp.cn/slpra/drug-development/"
CASES = {
    "members": "计划于644/642车间完成本次临床样品的生产。",
    "composite": "本次仅使用一个车间，其完整编号为644/642，不代表两个车间。",
    "missing": "计划于644/642车间完成本次临床样品的生产。",
    "alternative": "本次计划选择644车间或642车间之一生产，尚未确定具体车间。",
}
# Evaluation-only expectations, never supplied to discovery, ranking or lookup.
REFERENCE = {
    "members": [["644"], ["642"]], "composite": [["644/642"]],
    "missing": [["644"], ["642"]], "alternative": [["644"], ["642"]],
}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def score(state, expected):
    fields = state.get("fields", {})
    identifiers = {v for group in expected for v in group}
    entities = [e for e in state.get("entities", {}).values() if e["id"] != "document"]
    rows = []
    for entity in entities:
        values = [fields[f]["value"] for f in entity["field_ids"] if f in fields]
        ref = entity["referent"]
        if any(value in values or value in ref["text"] for value in identifiers):
            # The parallel-member fixture needs member-local value evidence to
            # detect swapped ownership. A single explicit object may instead
            # have a short name anchor and a separate field in the same source.
            owned = all(
                any(v["source_id"] == ref["source_id"] and (len(expected) == 1 or
                    ref["start"] <= v["start"] and v["end"] <= ref["end"])
                    for v in fields[f].get("value_evidence", []))
                for f in entity["field_ids"] if f in fields and fields[f]["value"] in identifiers
            )
            rows.append({"id": entity["id"], "label": entity["label"], "anchor": ref,
                         "values": values, "identifier_values": sorted(set(values) & identifiers),
                         "identifier_evidence_ownership_ok": owned})
    found = sorted(row["identifier_values"] for row in rows)
    spans = [row["anchor"] for row in rows]
    separate = all(a["source_id"] != b["source_id"] or
                   a["end"] <= b["start"] or b["end"] <= a["start"]
                   for i, a in enumerate(spans) for b in spans[i + 1:])
    return {
        "count_ok": len(rows) == len(expected),
        "identifier_ownership_ok": (
            found == sorted(sorted(g) for g in expected)
            and all(row["identifier_evidence_ownership_ok"] for row in rows)
        ),
        "nonoverlapping_anchors": bool(rows) and separate,
        "rows": rows, "all_mentions": len(entities),
        "source_suggestions": list(state.get("source_candidates", {}).values()),
        "lookup": [w.get("lookup") for w in state.get("windows", {}).values()],
        "accepted_facts": sum(e["state"] == "accepted" for e in entities),
        "scope": "discovery_only; no final type/property/identity/relation acceptance",
    }


def seed(db, template, codes):
    cls = OntologyClass(slpra_iri=template["class_iri"], label="生产区域")
    db.add(cls)
    db.flush()
    mapping = OntologyClassMapping(
        class_id=cls.id, mapping_type=template["mapping_type"],
        target=template["target"], source_system=template["source_system"],
        query_config=template["query_config"],
    )
    db.add(mapping)
    db.flush()
    for binding in template["property_bindings"]:
        db.add(OntologyPropertyBinding(class_mapping_id=mapping.id, **binding))
    replace_rows(db, codes)


def replace_rows(db, codes):
    db.execute(delete(MockProductionArea))
    db.add_all([MockProductionArea(code=code, label=code + "车间",
                                   iri="urn:probe:production-area:" + code) for code in codes])
    db.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ontology-dir", type=Path, default=Path("/app/ontology/slpra"))
    parser.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    parser.add_argument("--repeats", type=int, default=1,
                        help="Independent repetitions of the core members case; controls run once")
    parser.add_argument("--call-limit", type=int, default=40)
    parser.add_argument("--seconds-limit", type=int, default=3600)
    args = parser.parse_args()
    root = args.output
    root.mkdir(parents=True, exist_ok=False)
    shutil.copytree(Path(__file__).parents[1] / "services/document_harness", root / "runtime",
                    ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("entity_query.py", "entity_query_schema.py", "entity_query_mapping.py"):
        shutil.copy2(Path(__file__).parents[1] / "services" / name, root / name)
    shutil.copy2(__file__, root / "probe.py")
    shutil.copytree(args.ontology_dir, root / "ontology")
    templates = json.loads((Path(__file__).parents[1] / "resources/mock_entity_mappings.json")
                           .read_text())
    template = next(t for t in templates if t["class_iri"] == FAC + "ProductionArea")
    write(root / "mapping.json", template)
    write(root / "reference.json", {k: REFERENCE[k] for k in args.cases})
    world = OntologyEngine(ontology_dir=root / "ontology", store_path=root / "world.sqlite3")
    world.load()
    catalog = freeze_catalog(world, DEV + "CMCReport")
    policy = freeze_policy()
    ranker = CardRanker(policy["card_ranking"])
    bind = create_engine("sqlite:///" + str((root / "sources.sqlite3").resolve()))
    Base.metadata.create_all(bind)
    with Session(bind) as db:
        seed(db, template, ["644", "642"])
    write(root / "catalog.json", catalog.model_dump(mode="json"))
    write(root / "manifest.json", {
        "created_at": datetime.now(UTC), "policy": policy, "instructions": INSTRUCTIONS,
        "call_limit": args.call_limit, "seconds_limit": args.seconds_limit,
        "reference_is_model_input": False, "reference_status": "developer_regression",
        "ranking": "production ranker; same source-selected cards reused by A/B/C",
        "source_kind": "isolated SQLite Mock tables with production mapping and query service",
        "scope": "discovery_only", "repeats": args.repeats,
    })
    calls, summary = [], []
    started = monotonic()
    current = ""

    def invoke(stage, payload, schema):
        if len(calls) >= args.call_limit or monotonic() - started >= args.seconds_limit:
            raise RuntimeError("evaluation_budget_exhausted")
        number = len(calls) + 1
        path = root / f"{number:03}-{current}.json"
        record = {"case": current, "stage": stage, "payload": payload, "schema": schema,
                  "input_bytes_bound": request_size(stage, payload, schema)}
        write(path, record)
        print("CALL", number, current, payload.get("lookup_mode", "baseline"), flush=True)
        try:
            with model_scope(run_id=root.name, task_id=str(number), stage=stage):
                result = call_model(stage, payload, schema, policy)
        except Exception as exc:
            calls.append({"case": current, "error": type(exc).__name__})
            write(path, {**record, "error": str(exc)})
            write(root / "costs.json", calls)
            raise
        write(path, {**record, "result": result})
        calls.append({"case": current, "mode": payload.get("lookup_mode", "baseline"),
                      "seconds": result["seconds"], "usage": result["usage"],
                      "error": result["error"]})
        write(root / "costs.json", calls)
        if result["error"]:
            raise RuntimeError(result["error"])
        return result["output"]

    try:
        for case in args.cases:
            codes = ["642"] if case == "missing" else ["644", "642"]
            if case == "composite":
                codes.append("644/642")
            with Session(bind) as db:
                replace_rows(db, codes)
                write(root / f"{case}-source-records.json", [
                    {"record_id": str(r.id), "code": r.code, "label": r.label, "iri": r.iri}
                    for r in db.query(MockProductionArea).all()
                ])
            doc = Document()
            doc.add_paragraph(CASES[case])
            source = root / f"{case}.docx"
            doc.save(source)
            ir = build_document_ir(source, parse_docx_structure(source))
            write(root / f"{case}-ir.json", ir.model_dump(mode="json"))
            for repeat in range(args.repeats if case == "members" else 1):
                ranking, saved_draft = {}, {}

                def rank(cards, payload, budget):
                    if not ranking:
                        ranking.update(ranker.rank(cards, payload, budget))
                    return deepcopy(ranking)

                def lookup(operation, cards, argument):
                    with Session(bind, autoflush=False) as db:
                        return lookup_current(db, world, operation, cards, argument)

                for condition in ("B", "A", "C"):
                    current = f"{case}-{repeat + 1}-{condition}"
                    state = {}
                    def save(changes):
                        for domain, rows in changes.items():
                            state.setdefault(domain, {}).update(deepcopy(rows))

                    def model(stage, payload, schema):
                        if condition == "C" and payload.get("lookup_mode") == "draft":
                            if not saved_draft:
                                raise RuntimeError("no_draft_for_repeat_control")
                            return deepcopy(saved_draft)
                        result = invoke(stage, payload, schema)
                        if condition == "B" and payload.get("lookup_mode") == "draft":
                            saved_draft.update(deepcopy(result))
                        return result

                    def query_port(operation, cards, argument):
                        if condition == "C" and operation == "query":
                            return {"results": [], "issues": ["query_not_executed_control"]}
                        return lookup(operation, cards, argument)

                    engine = Engine(
                        ir=ir, catalog=catalog, state={}, invoke=model, save=save,
                        should_stop=lambda: state.get("cursor", {}).get("main", {}).get("stage")
                        not in (None, "discover"), max_input_tokens=policy["max_input_tokens"],
                        rank=rank, lookup=query_port if condition != "A" else None,
                    )
                    engine.windows = [build_windows(ir)[0]]
                    error = None
                    try:
                        engine.run()
                    except Exception as exc:
                        error = str(exc)
                    write(root / f"{current}-state.json", state)
                    write(root / f"{case}-{repeat + 1}-ranking.json", ranking)
                    result = {"case": case, "repeat": repeat + 1, "condition": condition,
                              "error": error, **score(state, REFERENCE[case])}
                    summary.append(result)
                    write(root / "summary.json", summary)
                    print("RESULT", current, json.dumps({k: result[k] for k in (
                        "error", "count_ok", "identifier_ownership_ok", "nonoverlapping_anchors",
                    )}), flush=True)
    finally:
        ranker.close()
        world.close()
        bind.dispose()


if __name__ == "__main__":
    main()
