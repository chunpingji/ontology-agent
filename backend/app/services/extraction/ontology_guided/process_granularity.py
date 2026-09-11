"""Complete cleaning method fields, scoped to physical cells and ontology types."""

import re

from app.schemas.evidence import EvidenceAnchor
from app.services.extraction.evidence_identity import stable_id
from app.services.extraction.ontology_guided.field_bindings import contains

VERSION = "whole-method-field-v1"
CLEANING_PROCESS = "https://ontology.pharma-gmp.cn/slpra/cleaning/CleaningProcess"
_METHOD_LABELS = {"清洗方法", "清洁方法", "清洗流程", "清洁流程", "cleaningmethod"}
_ACTION = re.compile(r"冲洗|洗涤|清洗|清洁|循环|浸泡|擦拭|吹干|排[净空放]|加入|加水|搅拌|烘干")


def _text(context, anchor):
    fragment = next((f for f in context.fragments if contains(f.anchor, anchor)), None)
    if fragment is None:
        return ""
    offset = fragment.anchor.span_start or 0
    return fragment.text[(anchor.span_start or 0) - offset:
                         (anchor.span_end or (offset + len(fragment.text))) - offset]


def method_fields(context):
    cells = {}
    for binding in context.field_bindings:
        if (binding.kind != "table" or not binding.source_cell_id
                or binding.mapping_status != "structural_candidate"
                or not any(re.sub(r"\s+", "", _text(context, ref)).strip("：:").casefold()
                           in _METHOD_LABELS for ref in binding.label_refs)):
            continue
        key = (tuple(binding.table_path or []), binding.row_index, binding.source_cell_id)
        refs = cells.setdefault(key, [])
        for ref in binding.target_value_refs:
            if ref not in refs:
                refs.append(ref)
    groups = []
    for key, refs in cells.items():
        texts = [_text(context, ref) for ref in refs]
        steps = [part for text in texts for part in re.split(r"[；;。\n]+", text)
                 if _ACTION.search(part)]
        if len(steps) < 2:
            continue
        groups.append({
            "group_id": stable_id("whole-cleaning-method", [VERSION, key, refs]),
            "source_refs": [ref.model_dump(mode="json") for ref in refs],
            "text": "\n".join(texts), "authority": "source_scope_only",
        })
    return groups


def _cleaning_type(predicate, class_iri):
    classes = {item.iri: item for item in getattr(predicate, "range_classes", [])}
    pending, seen = [class_iri], set()
    while pending:
        iri = pending.pop()
        if iri == CLEANING_PROCESS:
            return True
        if iri in seen:
            continue
        seen.add(iri)
        if iri in classes:
            pending.extend(classes[iri].parent_iris)
    return False


def method_scope(context, predicate, class_iri, endpoint):
    """Only a complete first paragraph locates a multi-paragraph method field.

    All remaining paragraphs become part of the frozen assertion; a review
    must cite the entire group. An arbitrary later action never names the group.
    """
    if not context.incremental_performance or not _cleaning_type(predicate, class_iri):
        return None, None
    for group in method_fields(context):
        refs = [EvidenceAnchor.model_validate(r) for r in group["source_refs"]]
        if not any(contains(ref, endpoint) for ref in refs):
            continue
        named = re.fullmatch(
            r"(?:清洗|清洁)?(?:方法|规程|程序|过程)(?:名称|编号|代号)\s*[:：]\s*(.{1,80})",
            _text(context, refs[0]).strip(),
        )
        if (named and contains(refs[0], endpoint)
                and named[1].strip() == _text(context, endpoint).strip()
                and not _ACTION.search(named[1])):
            # A source explicitly names a process; its name need not be the
            # full field text. This does not exempt its relation/owner proof.
            return None, None
        if endpoint != refs[0]:
            return None, "partial_cleaning_method"
        return group, None
    return None, None


def validate_method_scope(context, predicate, class_iri, endpoint, bridge_refs):
    group, issue = method_scope(context, predicate, class_iri, endpoint)
    if group and not all(any(contains(bridge, EvidenceAnchor.model_validate(ref))
                             for bridge in bridge_refs) for ref in group["source_refs"]):
        return "cleaning_method_scope_incomplete"
    return issue
