"""Current GPT selection contract on fixed proposals, using production decision checks."""

import argparse
import getpass
import json
import shutil
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from docx import Document

from app.evaluation.harness_model_comparison import (
    gateway_call,
    request_body,
    validate_output,
    write,
)
from app.services.document_harness.ontology import SchemaCatalog, identity_guidance, legal_property
from app.services.document_harness.protocols import (
    INSTRUCTIONS,
    PROTOCOL,
    ReferentCandidates,
    stage_schema,
)
from app.services.document_harness.referents import (
    register_span,
    resolve_selection,
    span_input,
    validate_partitions,
)
from app.services.document_harness.source import build_windows
from app.services.extraction.document_ir import DocumentIR, build_document_ir
from app.services.extraction.docx_structure import parse_docx_structure


def saved_case(source, case, number):
    record = json.loads((source / f"call-{number:03d}.json").read_text())
    state = json.loads((source / f"{case}-state.json").read_text())
    payload = record["payload"]
    work = next(w for w in state["referent_work"].values()
                if w.get("proposal") == payload["proposal"])
    ir = DocumentIR.model_validate_json((source / f"{case}-input.json").read_text())
    catalog = SchemaCatalog.model_validate_json((source / f"{case}-ontology.json").read_text())
    spans = {}
    for key, ref in work["span_catalog"].items():
        assert register_span(ir, spans, ref) == key
        assert spans[key] == ref
    return {"ir": ir, "catalog": catalog, "payload": payload, "spans": spans,
            "identifiers": work["identifier_span_ids"]}


def synthetic_case(root, base, name, text):
    doc = Document()
    doc.add_paragraph(text)
    path = root / f"{name}.docx"
    doc.save(path)
    ir = build_document_ir(path, parse_docx_structure(path))
    window = build_windows(ir)[0]
    payload, spans = deepcopy(base["payload"]), {}
    keys = {}
    for value in (text, "644/642", "644", "642"):
        ref = window.resolve(ir, {"source_id": "S1", "text": value, "occurrence": 0})
        keys[value] = register_span(ir, spans, ref)
    identifiers = [keys[v] for v in ("644/642", "644", "642")]
    payload["sources"] = window.payload()["sources"]
    payload["mentions"] = [{"anchor": {"source_id": "S1", "text": "644/642", "occurrence": 0},
                            "label": "644/642"}]
    payload["evidence_spans"] = span_input(window, spans, identifiers)
    # The same two competing proposals are supplied for both diagnostic inputs.
    for expression in payload["proposal"]["expressions"]:
        value = base["spans"][expression["span_id"]]["text"]
        expression["span_id"] = keys[value]
    return {"ir": ir, "catalog": base["catalog"], "payload": payload, "spans": spans,
            "identifiers": identifiers}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:31080/v1")
    parser.add_argument("--model", default="gpt-6-sol")
    args = parser.parse_args()
    root = args.output
    root.mkdir(parents=True, exist_ok=False)
    shutil.copy2(__file__, root / "probe.py")
    shutil.copy2(Path(__file__).with_name("harness_model_comparison.py"), root / "transport.py")
    shutil.copytree(Path(__file__).parents[1] / "services/document_harness", root / "runtime",
                    ignore=shutil.ignore_patterns("__pycache__"))
    cases = {"members": saved_case(args.source, "members", 27),
             "parallel_plan": saved_case(args.source, "parallel_plan", 36)}
    cases["compound"] = synthetic_case(
        root, cases["members"], "compound",
        "该区域的完整管理编号为644/642，斜杠属于编号，指一个车间。",
    )
    cases["explicit_ambiguity"] = synthetic_case(
        root, cases["members"], "explicit_ambiguity",
        "记录中的644/642车间可能指一个复合编号区域，也可能指两个区域，当前资料无法确定。",
    )
    for name, case in cases.items():
        write(root / f"{name}-input.json", case["ir"].model_dump(mode="json"))
        write(root / f"{name}-ontology.json", case["catalog"].model_dump(mode="json"))
        write(root / f"{name}-payload.json", case["payload"])
    write(root / "manifest.json", {
        "created_at": datetime.now(UTC), "protocol": PROTOCOL, "model": args.model,
        "base_url": args.base_url, "source": str(args.source),
        "method": "fixed selection-stage inputs; fresh GPT verdicts and production checks; "
                  "no discovery/type/property/relation run or live source query",
        "instructions": INSTRUCTIONS["referent_selection"], "call_limit": 8,
        "seconds_limit": 600, "reference_is_model_input": False,
        "production_service_restarted": False,
    })
    # Expected member values score the downstream result only, never the request.
    expected = {"members": [["642"], ["644"]], "parallel_plan": [["642"], ["644"]],
                "compound": [["644/642"]], "explicit_ambiguity": []}
    write(root / "reference.json", {"status": "developer_regression", "members": expected})
    secret = getpass.getpass("API key (memory only): ")
    started, summaries, calls = monotonic(), [], []
    try:
        for name, case in cases.items():
            payload, ir = case["payload"], case["ir"]
            answer = ReferentCandidates.model_validate(payload["proposal"])
            card = case["catalog"].classes[payload["class_definition"]["iri"]]
            properties = [p["iri"] for p in identity_guidance(card)["identity_properties"]
                          if legal_property(case["catalog"], card.iri, p["iri"])]
            refs = validate_partitions(answer, case["spans"], ir, properties, case["identifiers"])
            schema = stage_schema("referent_selection", source_ids=["S1"],
                                  partition_ids=[p.id for p in answer.partitions],
                                  span_ids=case["spans"])
            for repeat in range(3 if name in {"members", "parallel_plan"} else 1):
                if len(calls) >= 8 or monotonic() - started > 600:
                    raise RuntimeError("selection_probe_budget_exhausted")
                number = len(calls) + 1
                body = request_body("referent_selection", payload, schema, args.model)
                call = {"number": number, "case": name, "repeat": repeat + 1,
                        "schema": schema, "request_body": body}
                calls.append(call)
                row = {"number": number, "case": name, "repeat": repeat + 1,
                       "target_met": False, "error": None}
                try:
                    result = gateway_call(body, base_url=args.base_url, secret=secret)
                    call["result"] = result
                    if result["error"]:
                        raise RuntimeError(result["error"])
                    selection = validate_output("referent_selection", payload, schema,
                                                result["output"])
                    selected, _, issue = resolve_selection(
                        selection, answer, refs, case["spans"], ir,
                    )
                    values = sorted(sorted(refs[e]["text"] for e in m.expression_ids)
                                    for m in selected.members) if selected else []
                    row.update(verdict=selection.verdict, confidence=selection.confidence,
                               selected_partition_id=selected.id if selected else None,
                               selection_issue=issue, member_values=values,
                               reason=selection.reason,
                               target_met=values == expected[name] and (
                                   selection.verdict == "unresolved" if name == "explicit_ambiguity"
                                   else selection.verdict == "supported" and issue is None))
                except Exception as exc:
                    row["error"] = str(exc).replace(secret, "[REDACTED]")[:1000]
                finally:
                    summaries.append(row)
                    write(root / f"call-{number:03d}.json", call)
                    write(root / "calls.json", calls)
                    write(root / "summary.json", summaries)
                    print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        secret = ""


if __name__ == "__main__":
    main()
