"""Physical field reading aids; these never assign types, owners or global identity."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from app.services.extraction.ontology_guided.claim_protocol import ObservationProposal, Quote
from app.services.extraction.ontology_guided.source_citations import resolve_fragment_quote


def field_reading_groups(fields):
    groups = defaultdict(list)
    seen = set()
    for field in fields:
        if field["field_id"] in seen:
            continue
        seen.add(field["field_id"])
        groups[field["record_id"]].append(field)
    return [
        {"record_id": record_id, "fields": values, "shared_subject_established": False}
        for record_id, values in groups.items()
    ]


def physical_entity_issues(proposal, context):
    """Flag duplicate physical referents, without merging by name or across types."""
    from .claim_protocol import iter_quotes

    seen, issues = set(), {}
    for entity in proposal.entities:
        quotes = (entity.mentions if entity.representation == "mention"
                  else list(iter_quotes(entity.record_components)))
        anchors = []
        try:
            for quote in quotes:
                anchor, _ = resolve_fragment_quote(
                    quote.evidence_id, quote.text, context.fragments,
                    context_text=quote.context_text,
                )
                anchors.append((anchor.evidence_id, anchor.span_start, anchor.span_end))
        except ValueError:
            continue
        signature = (entity.class_iri, entity.representation, tuple(sorted(anchors)),
                     tuple(c.role for c in entity.record_components))
        if signature in seen:
            issues[entity.local_id] = ["duplicate_physical_referent"]
        seen.add(signature)
        if entity.representation != "mention":
            continue
        for group in field_reading_groups(context.tool_inputs.get("property_fields", [])):
            for field in group["fields"]:
                refs = [*field["label_refs"], *field["value_refs"]]
                if field["value_refs"] and any(all(
                    eid == ref["evidence_id"] and start <= ref["span_start"]
                    and end >= ref["span_end"] for ref in refs
                ) for eid, start, end in anchors):
                    issues.setdefault(entity.local_id, []).append("entity_field_as_name")
    return issues


def retain_unbound_fields(proposal, context):
    """Keep unmapped physical fields as observations, without guessing a legal predicate."""
    from .candidate_semantics import missing_value_kind
    from .claim_protocol import iter_quotes

    covered = set()
    for item in [*proposal.properties, *proposal.observations]:
        for quote in iter_quotes(item):
            try:
                anchor, _ = resolve_fragment_quote(
                    quote.evidence_id, quote.text, context.fragments,
                    context_text=quote.context_text,
                )
                covered.add((anchor.evidence_id, anchor.span_start, anchor.span_end))
            except ValueError:
                pass  # Invalid claims retain their own source failures at freeze.
    observations = list(proposal.observations)
    for group in field_reading_groups(context.tool_inputs.get("property_fields", [])):
        for field in group["fields"]:
            refs = field["value_refs"] or field["label_refs"]
            if not refs or all(
                any(eid == ref["evidence_id"] and start <= ref["span_start"]
                    and end >= ref["span_end"] for eid, start, end in covered)
                for ref in refs
            ):
                continue
            # The scalar-field parser binds a single exact physical value/label.
            text = field["value"] or field["label"]
            source = refs[0]
            nearby = next((fragment for fragment in context.fragments
                           if fragment.anchor.evidence_id == source["evidence_id"]
                           and (fragment.anchor.span_start or 0) <= source["span_start"]
                           and fragment.bounded_anchor().span_end >= source["span_end"]), None)
            context_text = None
            if nearby is not None and nearby.text.count(text) > 1:
                start = min([source["span_start"], *[
                    ref["span_start"] for ref in field["label_refs"]
                    if ref["evidence_id"] == source["evidence_id"]
                ]]) - (nearby.anchor.span_start or 0)
                end = source["span_end"] - (nearby.anchor.span_start or 0)
                context_text = nearby.text[max(0, start):end]
            quote = Quote(evidence_id=source["evidence_id"], text=text, context_text=context_text)
            try:
                anchor, _ = resolve_fragment_quote(
                    quote.evidence_id, quote.text, context.fragments, fact_required=True,
                    context_text=quote.context_text,
                )
            except ValueError:
                continue
            if (anchor.span_start, anchor.span_end) != (refs[0]["span_start"], refs[0]["span_end"]):
                continue
            missing = "missing" if not field["value"] else missing_value_kind(field["value"])
            observations.append(ObservationProposal(
                subject_id=None, predicate_iri=None, quote=quote,
                kind=missing or "unbound",
                reason=f"原字段“{field['label']}”保留为{'缺失标记' if missing else '待对齐观察'}；"
                       "尚未建立主体归属与合法属性语义匹配。",
            ))
    return proposal.model_copy(update={"observations": observations})


def discovery_limits(output_tokens):
    # Reserve space for syntax and observations. These are conservative planning
    # weights, not tokenizer measurements or permission to shorten evidence.
    return {
        "entities": max(1, (output_tokens + 4095) // 4096),
        "properties": max(1, (output_tokens + 2047) // 2048),
        "relations": max(1, (output_tokens + 6143) // 6144),
        "observations": max(1, (output_tokens + 2047) // 2048),
        "reference_bindings": max(1, output_tokens // 8192),
        "external_links": max(1, output_tokens // 8192),
    }


def bound_discovery_schema(schema, output_tokens, *, batch=False):
    schema = deepcopy(schema)
    root = schema["$defs"]["BatchMemberResult"] if batch else schema
    for name, maximum in discovery_limits(output_tokens).items():
        if name in root["properties"]:
            field = root["properties"][name]
            field["maxItems"] = min(maximum, field.get("maxItems", maximum))
    entity = schema["$defs"]["EntityProposal"]
    branches = []
    for representation, active, inactive in (
        ("mention", "mentions", "record_components"),
        ("record", "record_components", "mentions"),
    ):
        branch = deepcopy(entity)
        branch["properties"]["representation"] = {"type": "string", "const": representation}
        branch["properties"][active]["minItems"] = 1
        branch["properties"][inactive]["maxItems"] = 0
        branches.append(branch)
    schema["$defs"]["EntityProposal"] = {"anyOf": branches}
    return schema


def mark_candidate_budget(outcome, frozen, output_tokens):
    limits = discovery_limits(output_tokens)
    if any(len(getattr(frozen, name)) >= limits[name]
           for name in ("entities", "properties", "relations")):
        outcome.complete = False
        outcome.reason_code = "candidate_output_budget_reached"
        outcome.reason = "候选数量已到本轮输出预算边界，保留已核对结果；区域范围未确认穷尽。"


def discovery_budget_instructions(output_tokens):
    return (
        f"\n本轮发现最大输出为{output_tokens} token，这是上限不是长度目标。候选数组上限："
        f"{discovery_limits(output_tokens)}。先组织真实指称，实体只定义一次，再引用其local_id"
        "提出属性和关系；优先保留有来源的关系线索，不用重复属性挤占关系空间。"
        "引文只保留定位所需原文，context_text仅消歧时填写；不重复整段原文或实体组成。"
        "超过容量时保留本轮最明确的候选，不声称已覆盖全文；可在observations说明未覆盖范围。"
    )
