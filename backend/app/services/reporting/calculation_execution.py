"""Frozen calculation checks shared by report inputs, citations and conclusion gates."""

from app.schemas.evidence import DerivedProvenance
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.reasoning.pde_calculation import apply_decisions
from app.services.reasoning.rule_service import evaluate as evaluate_candidates
from app.services.reporting.input_resolver import Discovery, ResolvedValue, issue, typed_value
from app.services.reporting.output_ast import OutputNode
from app.services.reporting.template_v2 import ReportingError


def execute_checks(template, bundle):
    output = {}
    for check in template.calculation_checks:
        contract = bundle.get("contracts", {}).get(check.contract_ref)
        if not contract:
            raise ReportingError("CALCULATION_CONTRACT_UNAVAILABLE")
        source = bundle.get("sources", {}).get(check.source_slot, {})
        snapshot = source.get("snapshot") or {}
        candidates = [r["candidate"] for r in snapshot.get("assertions", [])]
        rows = apply_decisions(
            evaluate_candidates(
                candidates, contract=contract, root_entity_id=source.get("root_entity_id")
            ),
            source.get("calculation_decisions", []),
        )
        current = evaluate_candidates(
            source.get("candidates", candidates),
            contract=contract,
            root_entity_id=source.get("root_entity_id"),
        )
        issues = []

        def problem(state, code, message):
            issues.append(
                issue(state, code, check.check_id, blocks=True, message=message).model_dump(
                    mode="json"
                )
            )

        for comparison in source.get("model_compatibility", []):
            if comparison["status"] in {"unknown", "incompatible"}:
                problem("incomplete", comparison["code"], "计算来源的模型版本需要核对")

        if not rows:
            problem(
                "incomplete",
                "CALCULATION_SUBJECT_MISSING",
                "尚无已发布的计算实体及参数，无法完成 PDE 校验",
            )

        def input_bindings(results):
            return {
                (r["calculation_id"], r["linked_to_source"], tuple(sorted(r["relation_refs"])))
                for r in results
            }

        if input_bindings(rows) != input_bindings(current):
            problem(
                "incomplete",
                "CALCULATION_INPUTS_NOT_PUBLISHED",
                "计算输入或源文档关系已变化，请发布最新证据后重新生成报告",
            )
        if source.get("extraction_completion") != "complete":
            problem(
                "incomplete",
                "CALCULATION_DISCOVERY_INCOMPLETE",
                "源文档识别尚未完成，计算实体可能未全部列出",
            )
        for row in rows:
            if row["blocks_conclusion"]:
                state = (
                    "conflict"
                    if row["status"] == "conflict" or row["review_status"] == "rejected"
                    else "incomplete"
                )
                problem(
                    state,
                    "CALCULATION_REVIEW_REQUIRED",
                    row["subject_label"] + "：PDE 校验或异议处理未完成",
                )
        output[check.check_id] = {
            "check_id": check.check_id,
            "contract_ref": check.contract_ref,
            "method_hash": contract["definition_hash"],
            "source_slot": check.source_slot,
            "snapshot_id": snapshot.get("snapshot_id"),
            "rows": rows,
            "issues": issues,
            "blocks_conclusion": bool(issues),
        }
    return output


def calculation_value(binding, checks, typ, scope_id):
    check = checks[binding.check_ref]
    result = ResolvedValue(
        node_id=binding.binding_id,
        resolved_type=typ,
        execution_scope_id=scope_id,
        discovery=Discovery(status="complete" if not check["blocks_conclusion"] else "open"),
    )
    for row in check["rows"]:
        evidence = [e for es in row["input_evidence"].values() for e in es]
        refs = list(
            dict.fromkeys(
                [
                    stable_id("assertion", [row["subject_candidate_id"], row["subject_revision"]]),
                    *row["relation_refs"],
                    *(e["fact_ref"] for e in evidence),
                ]
            )
        )
        values = {
            name: {"value": row[name + "_mg_day"], "unit": "mg/day", "dimension": "mass/time"}
            if row[name + "_mg_day"]
            else None
            for name in ("asserted_pde", "derived_pde", "effective_pde")
        }
        values["status"] = row["review_status"]
        provenance = DerivedProvenance(
            rule_id=row["contract_ref"],
            rule_version=row["method_version"],
            snapshot_id=check["snapshot_id"],
            input_fact_ids=refs,
            parameters={
                "calculation_id": row["calculation_id"],
                "method_hash": row["method_hash"],
                "inputs": row["inputs"],
                "factors": row["factors"],
                "decision": row["decision"],
            },
            value=values,
        ).model_dump(mode="json")
        value = typed_value(
            row["calculation_id"],
            typ.item_type,
            values,
            refs=refs,
            provenance=[provenance, *(p for e in evidence for p in e["provenance"])],
            subject=[row["subject_iri"]],
            scope=scope_id,
            identity=row["subject_iri"],
        )
        if row["blocks_conclusion"]:
            value.fields["effective_pde"].issues.append(
                issue("conflict", "CALCULATION_REVIEW_REQUIRED", row["calculation_id"], blocks=True)
            )
        result.items.append(value)
    if check["blocks_conclusion"]:
        result.issues.append(
            issue("conflict", "CALCULATION_REVIEW_REQUIRED", binding.binding_id, blocks=True)
        )
    result.state = "conflict" if check["blocks_conclusion"] else "ready"
    return result


def _evidence_lines(row):
    labels = {
        "noael": "NOAEL",
        "asserted_pde": "原文 PDE",
        "species": "种属",
        "duration": "试验周期",
        "asserted_oeb": "原文明确 OEB",
    }
    lines = []
    for name, records in row["input_evidence"].items():
        for record in records:
            locations, excerpts = [], []
            for provenance in record["provenance"]:
                excerpts.extend(provenance.get("excerpts", []))
                for anchor in provenance.get("anchors", []):
                    page = anchor.get("physical_page_number")
                    locations.append(
                        (f"第 {page} 页，" if page else "")
                        + (anchor.get("block_id") or anchor["evidence_id"])
                    )
            lines.append(
                f"输入引用 · {labels.get(name, name.upper())}：{record['literal']['raw_value']}；"
                f"证据 {record['fact_ref']}（修订 {record['revision']}）"
                + ("；位置：" + "、".join(dict.fromkeys(locations)) if locations else "")
                + ("；摘录：" + " / ".join(dict.fromkeys(excerpts)) if excerpts else "")
            )
    return lines


def calculation_sections(checks):
    """Only serialise already frozen results; this renderer never looks up business data."""
    sections = []
    for check in checks.values():
        children = []
        for index, problem in enumerate(check["issues"]):
            children.append(
                OutputNode(
                    node_id=evidence_hash([check["check_id"], index]),
                    kind="paragraph",
                    text=problem["message"],
                    state=problem["state"],
                )
            )
        for row in check["rows"]:
            decision = row["decision"]
            labels = {
                "document": "原文",
                "method_default": "方法默认值",
                "species_table": "按种属计算",
                "duration_table": "按周期计算",
            }
            params = "；".join(
                f"{k.upper()}={v['value']}（{labels[v['source']]}）"
                for k, v in row["factors"].items()
            )
            parts = [
                f"{row['subject_label']} · 方法 {row['method_version']}",
                row["formula"],
                "NOAEL=" + row["inputs"].get("noael", "缺失") + " mg/kg/day；" + params,
                f"原文 PDE：{row['asserted_pde_mg_day'] or '缺失'} mg/day；"
                f"计算 PDE：{row['derived_pde_mg_day'] or '未完成'} mg/day；"
                f"比值：{row['ratio'] or '未计算'}（阈值 > {row['ratio_threshold']}）",
                f"按原文 PDE 换算 OEB：{row['asserted_band'] or '未完成'}；"
                f"按计算 PDE 换算 OEB：{row['derived_band'] or '未完成'}",
                "报告采用 PDE："
                + (
                    row["effective_pde_mg_day"] + " mg/day"
                    if row["effective_pde_mg_day"]
                    else "待处理，不能形成可接受结论"
                ),
                *[p["parameter"] + "：" + p["message"] for p in row["issues"]],
                row["limitations"],
                f"计算记录：{row['calculation_id']}；方法校验码：{row['method_hash']}",
            ]
            if row["inputs"].get("asserted_oeb"):
                parts.append("原文明确 OEB：" + row["inputs"]["asserted_oeb"])
            differences = {
                "pde_ratio": "PDE 数值比值超过阈值",
                "oeb_band": "原文与计算 PDE 换算的 OEB 分档不同",
                "oeb_assertion": "原文明确给出的 OEB 与计算分档不同",
            }
            parts.extend("差异：" + differences[d] for d in row["differences"])
            if decision:
                choices = {
                    "derived": "采纳计算值",
                    "asserted": "保留原文值",
                    "rejected": "提出异议",
                    "pending": "撤回取值决定",
                }
                parts.append(
                    f"取值决定：{choices[decision['choice']]}；处理人：{decision['actor']}；时间：{decision['decided_at']}；版本：{decision['revision']}；理由：{decision['reason']}"
                )
            elif row["stale_decision"]:
                parts.append("旧取值决定已失效，需按当前输入重新处理。")
            else:
                parts.append(
                    "审核：自动通过" if row["review_status"] == "automatic" else "审核：待处理"
                )
            parts.extend(_evidence_lines(row))
            evidence = [e for es in row["input_evidence"].values() for e in es]
            children.append(
                OutputNode(
                    node_id=row["calculation_id"],
                    kind="group",
                    text=row["subject_label"],
                    children=[
                        OutputNode(
                            node_id=evidence_hash([row["calculation_id"], i]),
                            kind="paragraph",
                            children=[
                                OutputNode(
                                    node_id=evidence_hash([row["calculation_id"], i, "value"]),
                                    kind="value",
                                    text=part,
                                    input_ref={
                                        "input_id": check["check_id"],
                                        "record_id": row["subject_iri"],
                                        "field_path": ["calculation"],
                                        "calculation_id": row["calculation_id"],
                                        "check_id": check["check_id"],
                                    },
                                    fact_refs=[e["fact_ref"] for e in evidence],
                                    provenance_refs=[p for e in evidence for p in e["provenance"]],
                                )
                            ],
                        )
                        for i, part in enumerate(parts)
                    ],
                )
            )
        sections.append(
            OutputNode(
                node_id=evidence_hash([check["check_id"], "section"]),
                kind="section",
                text="PDE 计算校验与取值依据",
                children=children,
            )
        )
    return sections
