"""Fixed-input identifier ablation and one bounded locator-feedback experiment.

Reference expectations are used only by score_discovery, never by model requests.
This is an offline evaluation entrypoint; online code does not import it.
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from docx import Document
from rdflib import Graph

from app.services.document_harness.controller import Engine
from app.services.document_harness.model import call_model, freeze_policy
from app.services.document_harness.ontology import catalog_from_graph, freeze_catalog
from app.services.document_harness.protocols import INSTRUCTIONS, Discovery, stage_schema
from app.services.document_harness.ranking import GUIDANCE_RULE, reading_card
from app.services.document_harness.source import Window, build_windows, reference
from app.services.extraction.document_ir import DocumentIR, build_document_ir
from app.services.extraction.docx_structure import parse_docx_structure
from app.services.llm.model_runtime import model_scope
from app.services.ontology_engine import OntologyEngine

FAC = "https://ontology.pharma-gmp.cn/slpra/facility/"
EQ = "https://ontology.pharma-gmp.cn/slpra/equipment/"
DEV = "https://ontology.pharma-gmp.cn/slpra/drug-development/"
DRUG = "https://ontology.pharma-gmp.cn/slpra/drug/"
NS = "urn:identifier-probe:"


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def neutral_catalog(*, group=False, renamed=False):
    namespace = "urn:opaque:Z:" if renamed else NS
    definition = (
        "登记对象是一个集合条目，其完整登记值标识整个集合，成员不是另一个登记对象。"
        if group
        else "登记对象是逐件管理的物件；并列登记值分别指称各个物件。"
    )
    graph = Graph().parse(
        data=f'''
    @prefix : <{namespace}> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
    :Report a owl:Class; rdfs:label "登记报告" .
    :Object a owl:Class; rdfs:label "登记对象";
        rdfs:comment "管理系统中按其登记值指称的具体对象。" .
    :Record a owl:Class; rdfs:label "登记记录"; rdfs:comment "管理台账中的具体记录。";
        owl:hasKey (:registry :serial) .
    :identifier a owl:DatatypeProperty; rdfs:domain :Object; rdfs:range xsd:string;
        rdfs:label "登记值"; rdfs:comment "{definition}编号在所在登记机构及体系内解释。" .
    :registry a owl:DatatypeProperty; rdfs:domain :Record; rdfs:range xsd:string;
        rdfs:label "登记机构"; rdfs:comment "复合键的机构组件，单独不能确定记录身份。" .
    :serial a owl:DatatypeProperty; rdfs:domain :Record; rdfs:range xsd:string;
        rdfs:label "记录编号"; rdfs:comment "编号需与登记机构共同确定身份。" .
    :describes a owl:ObjectProperty; rdfs:domain :Report;
        rdfs:range [owl:unionOf (:Object :Record)] .
    ''',
        format="turtle",
    )
    return catalog_from_graph(
        graph, namespace + "Report", identity_property_iris=[namespace + "identifier"]
    )


# Each expected group is the identifier values attached to one physical mention.
# These are developer-authored regression references, not expert-curated gold.
CASES = [
    (
        "area",
        "计划于611/642/646车间完成临床样品生产。",
        FAC + "ProductionArea",
        [["611"], ["642"], ["646"]],
    ),
    (
        "equipment_en",
        "Inspect pumps P-07, P-08 and P-09 at the same site.",
        EQ + "Equipment",
        [["P-07"], ["P-08"], ["P-09"]],
    ),
    ("equipment_space", "本次使用 EQ01 ／ EQ02 两台设备。", EQ + "Equipment", [["EQ01"], ["EQ02"]]),
    (
        "compound",
        "唯一一台设备的完整设备编号为A/01-B，斜杠属于编号。",
        EQ + "Equipment",
        [["A/01-B"]],
    ),
    ("renumber", "同一台设备原编号EQ-01，现改为EQ-02。", EQ + "Equipment", [["EQ-01", "EQ-02"]]),
    (
        "dual_system",
        "这台泵在资产系统编号AS-7，在维护系统编号MT-9。",
        EQ + "Equipment",
        [["AS-7", "MT-9"]],
    ),
    (
        "date_ratio",
        "设备EQ-03的型号为M/8，检查日期2026/08/01，配比1/2。",
        EQ + "Equipment",
        [["EQ-03"]],
    ),
    (
        "zero_case",
        "分别检查编号为007、aB-09的两台设备。编号N/A表示未提供。",
        EQ + "Equipment",
        [["007"], ["aB-09"]],
    ),
    (
        "alternatives",
        "拟选611或642车间之一；生产总量5 kg，不是在两处各生产5 kg。",
        FAC + "ProductionArea",
        [["611"], ["642"]],
    ),
    (
        "negative",
        "611车间本次不生产；642车间承担生产。",
        FAC + "ProductionArea",
        [["611"], ["642"]],
    ),
    (
        "composite",
        "登记记录：机构甲，记录编号01；另有一条记录：机构乙，记录编号02。",
        NS + "Record",
        [["甲", "01"], ["乙", "02"]],
    ),
    ("missing_component", "登记记录的编号是01，登记机构未提供。", NS + "Record", [["01"]]),
    ("semantic_members", "登记对象：A7、B9。", NS + "Object", [["A7"], ["B9"]]),
    ("semantic_group", "登记对象：A7、B9。", NS + "Object", [["A7、B9"]]),
    ("renamed_iri", "登记对象：A7、B9。", "urn:opaque:Z:Object", [["A7"], ["B9"]]),
]


def fixture(root, name, texts):
    doc = Document()
    for text in texts:
        doc.add_paragraph(text)
    path = root / f"{name}.docx"
    doc.save(path)
    ir = build_document_ir(path, parse_docx_structure(path))
    window = Window(
        name,
        [
            {
                "source_id": f"S{i + 1}",
                "evidence_id": unit.evidence_id,
                "offset": 0,
                "text": text,
                "section": unit.section_node_id,
                "kind": unit.kind,
                "table": None,
                "row": None,
                "column": None,
            }
            for i, text in enumerate(texts)
            if (unit := next(u for u in ir.evidence_units if u.text == text))
        ],
        [],
        [],
    )
    return ir, window


def variant(card, condition):
    result = deepcopy(card)
    if condition == "A":
        for key in (
            "identity_properties",
            "identity_key_groups",
            "identity_rule",
            "unavailable_identity_components",
            "annotation_contracts",
        ):
            result.pop(key, None)
    elif condition == "B":
        result["identity_properties"] = [
            {key: prop[key] for key in ("iri", "label", "identity_key") if key in prop}
            for prop in result["identity_properties"]
        ]
        # This ablation intentionally omits scope/definition/group semantics.
        result["identity_key_groups"] = []
        result["annotation_contracts"] = []
        result["unavailable_identity_components"] = []
    return result


def locator_feedback(ir, window, output):
    """Only factual source lookup and span overlap; no expected counts/semantic verdicts."""
    result = []
    anchors = []
    for mention in Discovery.model_validate(output).entities:
        row = {"local_id": mention.local_id, "field_locations": []}
        try:
            anchor = window.resolve_anchor(ir, mention.anchor)
            row["anchor_location"] = {
                "source_id": mention.anchor.source_id,
                "start": anchor["start"],
                "end": anchor["end"],
            }
            anchors.append((mention.local_id, anchor))
        except ValueError as exc:
            row["anchor_error"] = str(exc)
        for field in mention.source_fields:
            try:
                location = window.resolve(ir, field.value)
                row["field_locations"].append(
                    {
                        "source_id": field.value.source_id,
                        "start": location["start"],
                        "end": location["end"],
                    }
                )
            except ValueError as exc:
                row["field_locations"].append({"error": str(exc)})
        result.append(row)
    overlaps = [
        [a, b]
        for i, (a, x) in enumerate(anchors)
        for b, y in anchors[i + 1 :]
        if x["source_id"] == y["source_id"]
        and max(x["start"], y["start"]) < min(x["end"], y["end"])
    ]
    return {
        "method": "existing exact source locator",
        "mentions": result,
        "overlaps": overlaps,
        "scope": "位置及重叠检查不证明数量、归属或身份正确；结合本体和原文自行核对。",
    }


def score_discovery(output, cases, ir, window):
    answer = Discovery.model_validate(output)
    scores = {}
    for i, (name, _, _, expected) in enumerate(cases, 1):
        entities = [e for e in answer.entities if e.anchor.source_id == f"S{i}"]
        values, errors, spans = [], [], []
        for entity in entities:
            try:
                spans.append(window.resolve_anchor(ir, entity.anchor))
                if entity.name:
                    window.resolve(ir, entity.name)
                window.quotes(ir, entity.evidence)
                fields = []
                for field in entity.source_fields:
                    if field.label:
                        window.resolve(ir, field.label)
                    value = window.resolve(ir, field.value)
                    if field.value.source_id != entity.anchor.source_id:
                        raise ValueError("fixture_field_assigned_across_independent_sources")
                    fields.append(value["text"])
                # Count all identifier-like reference values; keep all raw fields for review.
                universe = {value for group in expected for value in group}
                values.append(sorted(v for v in fields if v in universe))
            except ValueError as exc:
                errors.append(str(exc))
        overlap = any(
            a["source_id"] == b["source_id"]
            and max(a["start"], b["start"]) < min(a["end"], b["end"])
            for j, a in enumerate(spans)
            for b in spans[j + 1 :]
        )
        owned = sorted(values) == sorted(sorted(group) for group in expected)
        scores[name] = {
            "expected_count": len(expected),
            "count": len(entities),
            "count_ok": len(entities) == len(expected),
            "identifier_ownership_ok": owned,
            "locatable_nonoverlapping": bool(entities) and not errors and not overlap,
            "raw_mentions": [e.model_dump(mode="json") for e in entities],
            "errors": errors,
            "complete": answer.complete,
            "passed": len(entities) == len(expected)
            and owned
            and not errors
            and not overlap
            and answer.complete,
        }
    return scores


def assess_ablation(baseline, output):
    """Rescore immutable model outputs, including every label/name/value quote."""
    groups = [CASES[i : i + 3] for i in range(0, 12, 3)] + [[case] for case in CASES[12:]]
    reference_values = json.loads((baseline / "reference.json").read_text())
    results = {}
    for batch, cases in enumerate(groups):
        path = baseline / f"batch{batch}.docx"
        ir = build_document_ir(path, parse_docx_structure(path))
        cases = [(name, text, iri, reference_values[name]) for name, text, iri, _ in cases]
        for condition in ("A", "B", "C", "C_tool"):
            paths = list(baseline.glob(f"*-batch{batch}-{condition}-discover.json"))
            if len(paths) != 1:
                raise ValueError(f"incomplete_benchmark_outputs: batch{batch}-{condition}")
            record = json.loads(paths[0].read_text())
            sources = record["payload"]["sources"]
            window = Window(
                "rescore",
                [
                    {
                        **source,
                        "offset": 0,
                        "evidence_id": next(
                            u.evidence_id for u in ir.evidence_units if u.text == source["text"]
                        ),
                    }
                    for source in sources
                ],
                [],
                [],
            )
            response = record.get("result", {})
            error = response.get("error") or record.get("exception")
            if not response and not error:
                raise ValueError("benchmark_call_still_running")
            if error:
                scores = {name: {"passed": False, "error": error} for name, *_ in cases}
            else:
                scores = score_discovery(response["output"], cases, ir, window)
            results.setdefault(condition, {}).update(scores)
    costs = json.loads((baseline / "costs.json").read_text())
    aggregates = {}
    for condition, scores in results.items():
        calls = [c for c in costs if c["case"].endswith("-" + condition)]
        aggregates[condition] = {
            "cases": len(scores),
            "passed": sum(bool(s.get("passed")) for s in scores.values()),
            "count_ok": sum(bool(s.get("count_ok")) for s in scores.values()),
            "identifier_ownership_ok": sum(
                bool(s.get("identifier_ownership_ok")) for s in scores.values()
            ),
            "locatable_nonoverlapping": sum(
                bool(s.get("locatable_nonoverlapping")) for s in scores.values()
            ),
            "under_count": sum(
                s.get("count", 0) < s.get("expected_count", 0) for s in scores.values()
            ),
            "over_count": sum(
                s.get("count", 0) > s.get("expected_count", 0) for s in scores.values()
            ),
            "call_errors": sum(bool(c.get("error")) for c in calls),
            "calls": len(calls),
            "seconds_including_queue": sum(c.get("seconds", 0) for c in calls),
            "input_tokens": sum(c.get("usage", {}).get("input_tokens", 0) for c in calls),
            "output_tokens": sum(c.get("usage", {}).get("output_tokens", 0) for c in calls),
        }
    output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(__file__, output / "scorer.py")
    write(
        output / "assessment.json",
        {
            "baseline": str(baseline),
            "results": results,
            "aggregates": aggregates,
            "note": "C_tool reports additional calls; add C for total cost."
            " All label/name/value quotes are checked.",
        },
    )
    print(json.dumps(aggregates, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-ir", type=Path, required=True)
    parser.add_argument("--ontology-dir", type=Path, default=Path("/app/ontology/slpra"))
    parser.add_argument(
        "--part",
        choices=[
            "ablation",
            "pipeline",
            "identity",
            "self-review",
            "real-feedback",
            "review",
            "assess",
            "all",
        ],
        default="all",
    )
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--identity-cases", nargs="+")
    parser.add_argument(
        "--feedback-conditions",
        nargs="+",
        default=["C_repeat", "C_tool"],
        choices=["C_repeat", "C_tool"],
    )
    args = parser.parse_args()
    if args.part == "assess":
        if args.baseline is None:
            parser.error("--baseline is required for assess")
        assess_ablation(args.baseline, args.output)
        return
    root = args.output
    root.mkdir(parents=True, exist_ok=False)
    shutil.copytree(
        Path(__file__).resolve().parents[1] / "services/document_harness",
        root / "runtime",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copy2(__file__, root / "probe.py")
    shutil.copytree(args.ontology_dir, root / "ontology")
    engine = OntologyEngine(ontology_dir=args.ontology_dir, store_path=root / "ontology.sqlite3")
    try:
        engine.load()
        catalog = freeze_catalog(engine, DEV + "CMCReport")
    finally:
        engine.close()
    policy = freeze_policy()
    write(root / "catalog.json", catalog.model_dump(mode="json"))
    write(
        root / "manifest.json",
        {
            "created_at": datetime.now(UTC),
            "policy": policy,
            "instructions": INSTRUCTIONS,
            "call_limit": 100,
            "reference_is_model_input": False,
            "reference_status": "developer-authored regression expectations",
            "feedback": "one forced locator-feedback turn; no semantic oracle",
        },
    )
    write(root / "reference.json", {name: expected for name, _, _, expected in CASES})
    calls, summary = [], {}
    current = ""

    def invoke(stage, payload, schema):
        number = len(calls) + 1
        if number > 100:
            raise RuntimeError("evaluation_call_limit")
        path = root / f"{number:03}-{current}-{stage}.json"
        record = {"case": current, "stage": stage, "payload": payload, "schema": schema}
        write(path, record)
        print("CALL", number, current, stage, flush=True)
        try:
            with model_scope(run_id=root.name, task_id=str(number), stage=stage):
                result = call_model(stage, payload, schema, policy)
        except Exception as exc:
            write(path, {**record, "exception": repr(exc)})
            calls.append({"case": current, "stage": stage, "error": repr(exc)})
            write(root / "costs.json", calls)
            raise
        write(path, {**record, "result": result})
        calls.append(
            {
                "case": current,
                "stage": stage,
                "seconds": result["seconds"],
                "usage": result["usage"],
                "error": result["error"],
            }
        )
        write(root / "costs.json", calls)
        if result["error"]:
            raise RuntimeError(result["error"])
        return result["output"]

    if args.part in {"ablation", "all"}:
        groups = [CASES[i : i + 3] for i in range(0, 12, 3)] + [[case] for case in CASES[12:]]
        for batch, cases in enumerate(groups):
            ir, window = fixture(root, f"batch{batch}", [c[1] for c in cases])
            cards = []
            for name, _, iri, _ in cases:
                source = (
                    neutral_catalog(group=name == "semantic_group", renamed=name == "renamed_iri")
                    if iri.startswith("urn:")
                    else catalog
                )
                card = reading_card(
                    source.classes[iri], annotation_contracts=source.annotation_contracts
                )
                if card not in cards:
                    cards.append(card)
            base = {
                **window.payload(),
                "document": {"label": "对象记录", "type": "报告", "property_guidance": []},
                "known_mentions": [],
            }
            schema = stage_schema("discover", source_ids=[s["source_id"] for s in window.sources])
            previous = None
            for condition in ("A", "B", "C", "C_tool"):
                current = f"batch{batch}-{condition}"
                payload = {
                    **base,
                    "schema_guidance": {
                        "rule": GUIDANCE_RULE,
                        "classes": [variant(c, condition) for c in cards],
                    },
                }
                if condition == "C_tool":
                    payload["previous_proposal"] = previous
                    payload["locator_feedback"] = locator_feedback(ir, window, previous)
                    payload["review_task"] = (
                        "根据工具返回的位置事实重新核对前次提议，输出完整发现结果。"
                    )
                try:
                    output = invoke("discover", payload, schema)
                    summary[current] = score_discovery(output, cases, ir, window)
                    if condition == "C":
                        previous = output
                except Exception as exc:
                    summary[current] = {"error": repr(exc)}
                    if condition == "C":
                        break
                write(root / "summary.json", summary)
                print(
                    "RESULT",
                    current,
                    {
                        k: v.get("passed")
                        for k, v in summary[current].items()
                        if isinstance(v, dict)
                    },
                    flush=True,
                )

    if args.part == "self-review":
        if args.baseline is None:
            parser.error("--baseline is required for self-review")
        baseline_manifest = json.loads((args.baseline / "manifest.json").read_text())
        if (
            baseline_manifest["policy"] != policy
            or baseline_manifest["instructions"] != INSTRUCTIONS
        ):
            raise ValueError("baseline_runtime_policy_mismatch")
        groups = [CASES[i : i + 3] for i in range(0, 12, 3)] + [[case] for case in CASES[12:]]
        for path in sorted(args.baseline.glob("*-C-discover.json")):
            recorded = json.loads(path.read_text())
            if not recorded.get("result", {}).get("output"):
                continue
            current = recorded["case"].replace("-C", "-C_repeat")
            batch = int(recorded["case"].split("-")[0].removeprefix("batch"))
            cases = groups[batch]
            # Load the frozen source document, not newly paraphrased evidence.
            source_doc = args.baseline / f"batch{batch}.docx"
            ir = build_document_ir(source_doc, parse_docx_structure(source_doc))
            sources = recorded["payload"]["sources"]
            window = Window(
                current,
                [
                    {
                        **source,
                        "offset": 0,
                        "evidence_id": next(
                            u.evidence_id for u in ir.evidence_units if u.text == source["text"]
                        ),
                    }
                    for source in sources
                ],
                [],
                [],
            )
            payload = deepcopy(recorded["payload"])
            payload["previous_proposal"] = recorded["result"]["output"]
            payload["review_task"] = "结合本体和原文重新核对前次提议，输出完整发现结果。"
            try:
                output = invoke("discover", payload, recorded["schema"])
                summary[current] = score_discovery(output, cases, ir, window)
            except Exception as exc:
                summary[current] = {"error": repr(exc)}
            write(root / "summary.json", summary)
            print("RESULT", current, flush=True)

    if args.part == "real-feedback":
        if args.baseline is None:
            parser.error("--baseline is required for real-feedback")
        path = next(args.baseline.glob("*-real-pipeline-discover.json"))
        recorded = json.loads(path.read_text())
        baseline_manifest = json.loads((args.baseline / "manifest.json").read_text())
        if (
            baseline_manifest["policy"] != policy
            or baseline_manifest["instructions"] != INSTRUCTIONS
        ):
            raise ValueError("baseline_runtime_policy_mismatch")
        ir = DocumentIR.model_validate_json(args.source_ir.read_text())
        write(root / "ir.json", ir.model_dump(mode="json"))
        sources = recorded["payload"]["sources"]
        window = Window(
            "real-feedback",
            [
                {
                    **source,
                    "offset": 0,
                    "evidence_id": next(
                        u.evidence_id for u in ir.evidence_units if u.text == source["text"]
                    ),
                }
                for source in sources
            ],
            [],
            [],
        )
        for condition in args.feedback_conditions:
            current = "real-" + condition
            payload = deepcopy(recorded["payload"])
            previous = recorded["result"]["output"]
            payload["previous_proposal"] = previous
            payload["review_task"] = "结合本体和原文重新核对前次提议，输出完整发现结果。"
            if condition == "C_tool":
                payload["locator_feedback"] = locator_feedback(ir, window, previous)
            try:
                output = invoke("discover", payload, recorded["schema"])
                answer = Discovery.model_validate(output)
                text = sources[0]["text"]
                start = text.index("611/642/646车间")
                end = start + len("611/642/646车间")
                selected = []
                for entity in answer.entities:
                    anchor = window.resolve_anchor(ir, entity.anchor)
                    if start <= anchor["start"] < anchor["end"] <= end:
                        selected.append(entity.model_dump(mode="json"))
                focused = {**output, "entities": selected}
                reference_cases = [
                    ("real_members", text, FAC + "ProductionArea", [["611"], ["642"], ["646"]])
                ]
                summary[current] = {
                    **score_discovery(focused, reference_cases, ir, window),
                    "all_mentions": output,
                }
            except Exception as exc:
                summary[current] = {"error": repr(exc)}
            write(root / "summary.json", summary)
            print("RESULT", current, flush=True)

    if args.part in {"pipeline", "all"}:
        current = "real-pipeline"
        ir = DocumentIR.model_validate_json(args.source_ir.read_text())
        write(root / "ir.json", ir.model_dump(mode="json"))
        unit = next(u for u in ir.evidence_units if "611/642/646车间" in u.text)
        window = Window(
            "real-paragraph",
            [
                {
                    "source_id": "S1",
                    "evidence_id": unit.evidence_id,
                    "offset": 0,
                    "text": unit.text,
                    "section": unit.section_node_id,
                    "kind": unit.kind,
                    "table": None,
                    "row": None,
                    "column": None,
                }
            ],
            [],
            [unit.evidence_id],
        )
        write(root / "real-source.json", reference(ir, unit.evidence_id, 0, len(unit.text)))
        saved = {}

        def save(changes):
            for domain, rows in changes.items():
                saved.setdefault(domain, {}).update(deepcopy(rows))
            write(root / "pipeline-state.json", saved)

        runtime = Engine(
            ir=ir,
            catalog=catalog,
            state={},
            invoke=invoke,
            save=save,
            should_stop=lambda: False,
            max_input_tokens=policy["max_input_tokens"],
        )
        runtime.windows = [window]
        try:
            runtime.run()
            areas = [
                e
                for e in runtime.state["entities"].values()
                if e.get("class_iri") == FAC + "ProductionArea"
            ]
            properties = [
                p
                for p in runtime.state.get("properties", {}).values()
                if p["predicate_iri"] == FAC + "areaIdentifier"
            ]
            summary[current] = {
                "areas": areas,
                "identifier_properties": properties,
                "relations": list(runtime.state.get("relations", {}).values()),
                "coreferences": list(runtime.state.get("coreferences", {}).values()),
                "cursor": runtime.state["cursor"],
                "entity_states": dict(Counter(e["state"] for e in areas)),
                "count_ok": len(areas) == 3 and all(e["state"] == "accepted" for e in areas),
                "attribute_values_ok": sorted(
                    str(p["value"]) for p in properties if p["state"] == "accepted"
                )
                == ["611", "642", "646"],
            }
        except Exception as exc:
            summary[current] = {"error": repr(exc)}
        write(root / "summary.json", summary)
        print(
            "RESULT",
            current,
            {k: v for k, v in summary[current].items() if k.endswith("ok") or k == "error"},
            flush=True,
        )

    if args.part == "review":
        ir = DocumentIR.model_validate_json(args.source_ir.read_text())
        unit = next(u for u in ir.evidence_units if "611/642/646车间" in u.text)
        whole = reference(ir, unit.evidence_id, 0, len(unit.text))
        window = Window(
            "independent-review",
            [
                {
                    "source_id": "S1",
                    "evidence_id": unit.evidence_id,
                    "offset": 0,
                    "text": unit.text,
                    "section": unit.section_node_id,
                    "kind": unit.kind,
                    "table": None,
                    "row": None,
                    "column": None,
                }
            ],
            [],
            [unit.evidence_id],
        )
        for name in ("members", "bundle", "matching_attributes", "swapped_attributes"):
            current = name
            runtime = Engine(
                ir=ir,
                catalog=catalog,
                state={},
                invoke=invoke,
                save=lambda _: None,
                should_stop=lambda: True,
                max_input_tokens=policy["max_input_tokens"],
            )
            runtime.windows = [window]
            runtime.run()  # Initialise only; supplied candidates below are test hypotheses.
            entities, fields, properties = {}, {}, {}
            texts = ["611/642/646车间"] if name == "bundle" else ["611", "642", "646"]
            attributes = name.endswith("attributes")
            for i, text in enumerate(texts):
                start = unit.text.index(text)
                anchor = reference(ir, unit.evidence_id, start, start + len(text))
                key = f"e{i}"
                entities[key] = {
                    "id": key,
                    "label": text,
                    "name": None,
                    "role": "生产区域提及",
                    "class_iri": FAC + "ProductionArea",
                    "class_label": "生产区域",
                    "state": "accepted" if attributes else "candidate",
                    "reason": "测试提供的候选假设",
                    "evidence": [anchor, whole],
                    "referent": anchor,
                    "field_ids": [],
                    "window_id": window.id,
                }
                field_id = f"f{i}"
                fields[field_id] = {
                    "id": field_id,
                    "alias": "",
                    "label": "",
                    "value": text,
                    "missing": False,
                    "evidence": [anchor],
                    "value_evidence": [anchor],
                    "source_aliases": [],
                    "row": None,
                }
            for i, entity in enumerate(entities.values()):
                owner_field = f"f{(i + 1) % 3 if name == 'swapped_attributes' else i}"
                entity["field_ids"] = [owner_field]
                field = fields[owner_field]
                if attributes:
                    key = f"p{i}"
                    properties[key] = {
                        "id": key,
                        "subject_id": entity["id"],
                        "predicate_iri": FAC + "areaIdentifier",
                        "value": field["value"],
                        "source_value": field["value"],
                        "source_unit": None,
                        "value_component": "whole",
                        "value_evidence": field["value_evidence"],
                        "field_id": owner_field,
                        "state": "candidate",
                        "reason": "测试提供的编号对应候选",
                        "evidence": [whole],
                        "window_id": window.id,
                    }
            runtime.commit(
                {
                    "entities": entities,
                    "fields": fields,
                    "properties": properties,
                    "window_entities": {window.id: {"ids": list(entities)}},
                }
            )
            runtime.should_stop = lambda: False
            try:
                if attributes:
                    runtime.evidence_review(window)
                    rows = list(runtime.state["properties"].values())
                else:
                    runtime.entity_review(window)
                    rows = [runtime.state["entities"][key] for key in entities]
                should_accept = name in {"members", "matching_attributes"}
                summary[name] = {
                    "candidate_hypotheses_are_fixture_premises": True,
                    "rows": rows,
                    "passed": all((row["state"] == "accepted") == should_accept for row in rows),
                }
            except Exception as exc:
                summary[name] = {"error": repr(exc), "passed": False}
            write(root / f"{name}-state.json", runtime.state)
            write(root / "summary.json", summary)
            print("RESULT", name, summary[name]["passed"], flush=True)

    if args.part in {"identity", "all"}:
        from app.evaluation.document_coreference_probe import fixture as pair_fixture

        controls = {
            "scoped_same": (["甲机构登记记录的编号为01。", "甲机构复查登记记录的编号01。"], "same"),
            "different_scope": (
                ["甲机构的登记记录编号01。", "乙机构的另一条登记记录编号01。"],
                "different",
            ),
            "missing_scope": (
                ["本节登记记录编号01，机构未提供。", "另页登记记录编号01，机构未提供。"],
                "unresolved",
            ),
            "different_values": (
                ["登记记录编号01，所属机构未说明。", "登记记录编号02，所属机构未说明。"],
                "unresolved",
            ),
            "explicit_renumber": (
                ["登记物件旧编号A7。", "上节的同一个登记物件已改编号B9。"],
                "same",
            ),
        }
        cards = neutral_catalog()
        for name, (texts, expected) in controls.items():
            if args.identity_cases and name not in args.identity_cases:
                continue
            current = name
            ir, state = pair_fixture(root, name, texts, cards)
            locations = {
                (row["referent"]["source_id"], row["referent"]["start"], row["referent"]["end"])
                for row in state["entities"].values()
                if row["id"] != "document"
            }
            if len(locations) != len(texts):
                raise ValueError("identity_fixture_reuses_physical_mention")
            for e in state["entities"].values():
                if e["id"] != "document":
                    e.update(
                        class_iri=NS + ("Object" if name == "explicit_renumber" else "Record"),
                        class_label="登记对象" if name == "explicit_renumber" else "登记记录",
                        role="原文登记对象提及",
                    )
            runtime = Engine(
                ir=ir,
                catalog=cards,
                state=state,
                invoke=invoke,
                save=lambda _: None,
                should_stop=lambda: False,
                max_input_tokens=policy["max_input_tokens"],
            )
            runtime.state["cursor"] = {
                "main": {"window_index": len(build_windows(ir)), "stage": "coreference_review"}
            }
            try:
                runtime.run()
                decisions = list(runtime.state.get("coreferences", {}).values())
                summary[name] = {
                    "expected": expected,
                    "decisions": decisions,
                    "passed": len(decisions) == 1 and decisions[0]["verdict"] == expected,
                    "types_are_fixture_premises": True,
                }
            except Exception as exc:
                summary[name] = {"error": repr(exc), "expected": expected, "passed": False}
            write(root / f"{name}-state.json", runtime.state)
            write(root / "summary.json", summary)
            print("RESULT", name, summary[name].get("passed"), flush=True)
    print("OUTPUT_DIR", root, flush=True)


if __name__ == "__main__":
    main()
