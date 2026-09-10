"""Bounded model feedback; never silently rewrite a proposed source anchor."""

QUOTE_ERRORS = frozenset({
    "source_excerpt_mismatch", "ambiguous_source_quote", "source_quote_outside_scope",
})
REPAIR_VERSION = "citation-feedback-v2-domains"


def feedback_domain(event, task_kind):
    path = event.get("field_path", "")
    field = path.rsplit(".", 1)[-1].split("[", 1)[0]
    if "identifier." in path or field in {"conditions", "source_unit", "boolean_legend"}:
        return "binding"
    if field == "assertion_spans":
        return ("fact" if task_kind == "relationship"
                and event.get("stage") == "relationship_recall" else "binding")
    if not path and event.get("stage") in {
        "property_binding", "relationship_binding", "reference_verification", "normalization",
    }:
        return "binding"
    # Old events without a field path cannot authorize the broader binding domain.
    return "fact"


def repair_feedback(events, ir, fact_regions, binding_regions, *, task_kind):
    errors = []
    for event in events:
        if event.get("code") not in QUOTE_ERRORS:
            continue
        quote = event.get("quote", "")
        domain = feedback_domain(event, task_kind)
        allowed = fact_regions if domain == "fact" else binding_regions
        matches = []
        if quote and not event.get("quote_truncated"):
            for region in allowed:
                original = ir.unit(region.evidence_id).text
                left, right = region.span_start or 0, region.span_end
                if quote in original[left:right]:
                    match = {"evidence_id": region.evidence_id, "text": quote}
                    if match not in matches:
                        matches.append(match)
        errors.append({
            "code": event["code"], "evidence_id": event.get("evidence_id"),
            "quote": quote, "exact_matches": matches[:3],
            "field_path": event.get("field_path"), "citation_domain": domain,
        })
        if len(errors) >= 8:
            break
    return {
        "version": REPAIR_VERSION,
        "instruction": (
            "上一次响应的原文引用校验失败。重新完整回答本任务，保留有效提议，"
            "修正 evidence_id 与逐字原文的对应；不要使用省略号、改写或拼接。"
            "exact_matches 仅为定位提示，不证明类型、归属或关系；仍须核对 target 原文。"
            "提示仅适用于标注的字段和权限域，binding 提示不得移作 fact 引用。"
            "同一来源多处出现时重新选择完整且唯一的逐字引文，不要猜坐标或挂接其他来源。"
        ),
        "errors": errors,
    }
