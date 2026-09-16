"""Scope-limited tool adapters for source, owner and field binding checks.

The private anchors below are deliberately local to the supplied short-ref
catalog. They are never persisted as document EvidenceAnchors or fact proofs.
No tool in this module decides semantic support or normalizes a literal.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from types import SimpleNamespace

from app.services.extraction.literal_normalizer import (
    LiteralNormalizationError,
    canonical_unit,
)
from app.services.extraction.literal_normalizer import normalize_literal as parse_quantity
from app.services.extraction.ontology_guided.field_bindings import (
    validate_field_binding,
    validate_local_owner,
)
from app.services.extraction.ontology_guided.source_citations import (
    SpanProposal,
    resolve_source_anchor,
)

EMPTY = {"ref": "", "quote": ""}
_NUMBER = re.compile(r"[+＋\-−－]?\d+(?:[.．]\d+)?(?:[eE][+\-]?\d+)?")
_CONDITION = re.compile(r"(?:如果|否则|若|当.{1,60}时|在.{1,60}条件下|如不符合|满足.{1,30}后)")
_LEGEND = re.compile(r"[—×√].{0,4}(?:代表|表示|意味着|=|：|:)")
_INCOMPLETE = {"unit_source_missing", "field_role_source_missing", "condition_role_unproven",
               "status_marker_is_not_literal", "owner_identity_unproven",
               "owner_record_ambiguous"}


@dataclass(frozen=True)
class _Anchor:
    evidence_id: str
    table_path: tuple | None
    span_start: int
    span_end: int

    def model_copy(self, *, update):
        return replace(self, **update)


class _Scope:
    def __init__(self, sources):
        self.sources = sources

    def unit(self, ref):
        return SimpleNamespace(text=self.sources[ref]["text"])

    def anchor(self, ref, start=None, end=None):
        source = self.sources[ref]
        start = 0 if start is None else start
        end = len(source["text"]) if end is None else end
        if not 0 <= start < end <= len(source["text"]):
            raise ValueError("citation_span_outside_source")
        return _Anchor(ref, tuple(source.get("table_path") or []) or None, start, end)

    def resolve(self, anchor):
        return self.sources[anchor.evidence_id]["text"][anchor.span_start:anchor.span_end]


def resolve_citation(sources: dict, citation: dict) -> dict:
    """Resolve a verbatim quote, retaining explicit half-open coordinates."""
    if not isinstance(citation, Mapping) or citation.get("ref") not in sources:
        raise ValueError("citation_ref_missing")
    quote = citation.get("quote")
    if not isinstance(quote, str) or not quote.strip():
        raise ValueError("citation_quote_empty")
    if any(citation.get(key) is not None and type(citation[key]) is not int
           for key in ("start", "end")):
        raise ValueError("citation_coordinates_invalid")
    scope = _Scope(sources)
    try:
        span = SpanProposal(evidence_id=citation["ref"], text=quote,
                            start=citation.get("start"), end=citation.get("end"))
        anchor = resolve_source_anchor(span, scope, [scope.anchor(citation["ref"])])
    except ValueError as exc:
        reasons = {"ambiguous_source_quote": "citation_quote_ambiguous",
                   "source_excerpt_mismatch": "citation_quote_not_in_source"}
        raise ValueError(reasons.get(str(exc), "citation_coordinates_invalid")) from exc
    return {"ref": citation["ref"], "quote": quote,
            "start": anchor.span_start, "end": anchor.span_end}


def get_schema_card(catalog: dict, class_iri: str, allowed_classes=None) -> dict:
    if class_iri not in catalog or (
        allowed_classes is not None and class_iri not in allowed_classes
    ):
        return {"execution_status": "completed", "validation_status": "failed",
                "issues": ["schema_class_outside_scope"], "card": None}
    return {"execution_status": "completed", "validation_status": "passed", "issues": [],
            "card": deepcopy(catalog[class_iri]), "is_source_evidence": False}


def inspect_evidence(sources: dict, refs: list[str]) -> dict:
    """Expand only registered rows/headers; expose exact source coordinates."""
    unknown = [ref for ref in refs if ref not in sources]
    selected = set(refs) & sources.keys()
    for ref in list(selected):
        source = sources[ref]
        selected.update(r for r in source.get("column_header_refs", []) if r in sources)
        if source.get("table_path") and source.get("cell_structure_valid"):
            selected.update(r for r, other in sources.items()
                            if other.get("table_path") == source["table_path"]
                            and set(source.get("logical_rows", []))
                            & set(other.get("logical_rows", [])))
    units = []
    for ref, source in sources.items():
        if ref not in selected:
            continue
        text = source["text"]
        spans = [{"ref": ref, "quote": text, "start": 0, "end": len(text)}] if text else []
        # Positions are a convenience, not an exhaustive candidate restriction.
        for match in re.finditer(
            r"[+\-−]?\d+(?:\.\d+)?|[A-Za-zµμ°℃%％]+(?:[/／][A-Za-zµμ天]+)*", text,
        ):
            spans.append({"ref": ref, "quote": match[0],
                          "start": match.start(), "end": match.end()})
        units.append({**deepcopy(source), "ref": ref, "spans": spans,
                      "legend_candidate": bool(_LEGEND.search(text)),
                      "condition_candidate": bool(_CONDITION.search(text))})
    return {"execution_status": "completed",
            "validation_status": "failed" if unknown else "passed",
            "issues": ["citation_ref_missing:" + str(ref) for ref in unknown], "units": units,
            "coordinate_system": "zero_based_half_open_characters",
            "semantic_status": "not_checked", "fact_eligible": False}


def _same_row(left, right):
    return (left.get("table_path") == right.get("table_path")
            and bool(set(left.get("logical_rows", [])) & set(right.get("logical_rows", []))))


def _binding_context(sources, value_ref, owner=None):
    scope = _Scope(sources)
    value = sources[value_ref]
    fragments = [SimpleNamespace(anchor=scope.anchor(ref), text=unit["text"],
                                 fact_eligible=not unit.get("is_header"), purpose="source")
                 for ref, unit in sources.items() if unit["text"]]
    headers = [scope.anchor(ref) for ref in value.get("column_header_refs", [])
               if ref in sources and sources[ref]["text"]]
    binding = None
    if value.get("table_path"):
        binding = SimpleNamespace(
            field_binding_id="field:" + value_ref, kind="table",
            target_value_refs=[scope.anchor(value_ref)], label_refs=headers,
            owner_candidate_refs=[scope.anchor(ref) for ref, unit in sources.items()
                                  if unit["text"] and not unit.get("is_header")
                                  and _same_row(value, unit)],
            mapping_status="structural_candidate" if headers else "incomplete",
        )
    context = SimpleNamespace(fragments=fragments, field_bindings=[binding] if binding else [],
                              subject_evidence_refs=[owner] if owner else [],
                              owner_field_refs=[], subject_label="")
    return scope, context, binding


def _owner_issue(sources, owner, value):
    first, second = sources[owner["ref"]], sources[value["ref"]]
    if first.get("is_header"):
        return "owner_is_header_candidate"
    if first.get("table_path") and second.get("table_path"):
        if not first.get("cell_structure_valid") or not second.get("cell_structure_valid"):
            return "owner_cell_structure_missing"
        if first["table_path"] != second["table_path"]:
            return "owner_table_mismatch"
        if not _same_row(first, second):
            return "owner_row_mismatch"
        owner_rows = set(first.get("logical_rows", []))
        value_rows = set(second.get("logical_rows", []))
        same_cell = (first.get("source_cell_id") is not None
                     and first["source_cell_id"] == second.get("source_cell_id"))
        if (owner["ref"] != value["ref"] and not same_cell and len(owner_rows) > 1
                and value_rows < owner_rows):
            # A shared name spanning several records does not identify which
            # row's record owns a value. Matching any row is insufficient.
            return "owner_record_ambiguous"
        scope = _Scope(sources)
        anchor = scope.anchor(owner["ref"], owner["start"], owner["end"])
        _, context, binding = _binding_context(sources, value["ref"], anchor)
        return validate_local_owner(context, binding, [anchor], [anchor], [], "role_mapped_table")
    # Narrative-to-table ownership cannot be established by physical structure.
    # It remains explicitly unverified in the external semantic dimensions.
    return None


def _raw_span(raw, source, field, sources):
    if not isinstance(raw, str) or not raw:
        raise ValueError("raw_value_not_in_quote")
    positions = [match.start() for match in re.finditer(re.escape(raw), source["quote"])]
    if len(positions) > 1 and raw in {"是", "否"} and field.get("ref") == source["ref"]:
        positions = [p for p in positions if source["start"] + p >= field["end"]]
    if not positions:
        raise ValueError("raw_value_not_in_quote")
    if len(positions) != 1:
        raise ValueError("raw_value_ambiguous")
    start = source["start"] + positions[0]
    end = start + len(raw)
    text = sources[source["ref"]]["text"]
    try:
        scalar = parse_quantity(raw, datatype="decimal").kind == "number"
    except LiteralNormalizationError:
        scalar = bool(_NUMBER.fullmatch(raw))
    if scalar:
        # Registered scalar quantities include units (25℃), so adding a unit
        # cannot bypass the same boundary checks as a bare number (25).
        scalar_start = start + len(raw) - len(raw.lstrip())
        before, after = text[:scalar_start], text[end:]
        if re.search(r"[\d.eE+−－-]$", before) or re.match(r"[\d.eE]", after):
            raise ValueError("numeric_substring_not_full_value")
        if (re.search(r"(?:不超过|不低于|不得过|小于|大于|至少|最多|[<>≤≥约])\s*$", before)
                or re.match(r"\s*[-–—~～至到]\s*\d", after)
                or re.search(r"\d\s*[-–—~～至到]\s*$", before)):
            raise ValueError("scalar_value_required")
    return {"ref": source["ref"], "quote": raw, "start": start, "end": end}


def _unit_binding(raw, unit, sources):
    """Coordinate-aware equivalent of the existing unit replay physical gates.

    replay_unit_binding accepts quote-only citations, so cannot replay the
    second of two identical unit symbols. Keep this adapter narrow: alias and
    conversion policies still belong to the shared literal normalizer.
    """
    if unit == EMPTY:
        return None, "unit_source_missing"
    text = sources[unit["ref"]]["text"]
    value = unit["quote"].strip()
    if len(value) >= 2 and (value[0], value[-1]) in {("(", ")"), ("（", "）")}:
        value = value[1:-1].strip()
    for adjacent in (text[max(0, unit["start"] - 1):unit["start"]],
                     text[unit["end"]:].lstrip()[:1]):
        if adjacent and re.fullmatch(r"[A-Za-zａ-ｚＡ-Ｚµμ/／²³^力]", adjacent):
            return value, "unit_quote_partial"
    if text[unit["end"]:unit["end"] + 1].isdigit():
        return value, "unit_quote_partial"
    source = sources[raw["ref"]]
    if unit["ref"] == raw["ref"]:
        # A string quantity may include its cited unit already.
        contained = raw["start"] <= unit["start"] < unit["end"] <= raw["end"]
        if not contained and (unit["start"] < raw["end"]
                              or source["text"][raw["end"]:unit["start"]].strip()):
            return value, "unit_not_bound_to_value"
    elif unit["ref"] not in source.get("column_header_refs", []):
        return value, "unit_column_mismatch"
    suffix = re.match(r"\s*([A-Za-zµμ%％℃°]+(?:[/／][A-Za-zµμ天]+)*)",
                      source["text"][raw["end"]:])
    if suffix and canonical_unit(suffix[1]) != canonical_unit(value):
        return value, "source_unit_conflict"
    return value, None


def check_claim_binding(candidate: dict, subjects: dict, bindings: dict, sources: dict) -> dict:
    """Check frozen candidate mechanics; a passed result is never a fact."""
    output = {"candidate_id": candidate.get("id"), "execution_status": "completed",
              "validation_status": "failed", "issues": [], "fact": None,
              "source_unit": None, "semantic_status": "not_checked", "fact_eligible": False}
    issues = output["issues"]
    try:
        subject_id, field_key = candidate["subject_id"], candidate["field"]
        subject = subjects.get(subject_id)
        prop = bindings.get(subject_id, {}).get(field_key)
        if subject is None or prop is None:
            raise ValueError("subject_or_predicate_outside_scope")
        kind = candidate["kind"]
        if kind not in {"property", "relation"}:
            raise ValueError("candidate_kind_invalid")
        expected_kind = "property" if kind == "property" else "relationship"
        if prop.get("kind") != expected_kind or prop.get("constraint_status") != "resolved":
            raise ValueError("predicate_kind_or_constraint_mismatch")
        proposal = candidate["proposal"]
        roles = ("source", "field", "unit", "condition") if kind == "property" else (
            "evidence", "condition")
        citations = {}
        for role in roles:
            cite = proposal.get(role, EMPTY)
            empty = (isinstance(cite, Mapping) and cite.get("ref") == ""
                     and cite.get("quote") == "" and cite.get("start") is None
                     and cite.get("end") is None)
            citations[role] = EMPTY if empty else resolve_citation(sources, cite)
        evidence = citations["source" if kind == "property" else "evidence"]
        if evidence == EMPTY:
            raise ValueError("citation_ref_missing")
        owner = (resolve_citation(sources, subject["anchor"])
                 if subject_id != "document" else EMPTY)
        if owner != EMPTY:
            issue = _owner_issue(sources, owner, evidence)
            if issue:
                issues.append(issue)
        condition = citations["condition"]
        if condition != EMPTY:
            if sources[condition["ref"]].get("is_header") or not _CONDITION.search(
                condition["quote"]
            ):
                issues.append("condition_role_unproven")
            if owner != EMPTY:
                issue = _owner_issue(sources, owner, condition)
                if issue:
                    issues.append(issue)
        fact = {"kind": kind, "subject_id": subject_id,
                "subject_class_iri": subject["class_iri"],
                "subject_text": subject["anchor"]["quote"], "predicate_iri": prop["iri"],
                "evidence": evidence, "condition": condition}
        output["fact"] = fact
        if kind == "property":
            raw = _raw_span(proposal["raw"], evidence, citations["field"], sources)
            fact.update(raw_value=proposal["raw"], field=citations["field"], unit=citations["unit"],
                        value_span=raw)
            if proposal["raw"].strip().casefold() in {"—", "×", "√", "n/a", "不适用"}:
                issues.append("status_marker_is_not_literal")
            if sources[evidence["ref"]].get("is_header"):
                issues.append("header_is_not_value")
            scope, context, binding = _binding_context(sources, evidence["ref"])
            endpoint = scope.anchor(raw["ref"], raw["start"], raw["end"])
            role_refs = ([scope.anchor(citations["field"]["ref"], citations["field"]["start"],
                                      citations["field"]["end"])]
                         if citations["field"] != EMPTY else [])
            # A selected header can contain both a label and a separate unit
            # paragraph. Only require the field citation's physical header;
            # all same-column headers remain available to unit validation.
            if binding is not None:
                selected = [r for r in role_refs if any(
                    r.evidence_id == h.evidence_id for h in binding.label_refs)]
                if not binding.label_refs:
                    # A procedural paragraph in a cell is still narrative.
                    # Its own exact field context is usable without inventing
                    # a column header (the parser may mark row zero by default).
                    selected = [r for r in role_refs if r.evidence_id == endpoint.evidence_id]
                binding.label_refs = selected
                binding.mapping_status = "structural_candidate" if selected else "incomplete"
            issue = validate_field_binding(
                context, SimpleNamespace(label=prop["label"]), endpoint,
                binding.field_binding_id if binding else None, role_refs,
                "role_mapped_table" if binding else "explicit_assertion",
            )
            if issue:
                issues.append(issue)
            if prop.get("canonical_unit") or citations["unit"] != EMPTY:
                output["source_unit"], issue = _unit_binding(raw, citations["unit"], sources)
                if issue:
                    issues.append(issue)
        else:
            target_id = proposal["target_id"]
            target = subjects.get(target_id)
            if target is None or target_id == subject_id:
                raise ValueError("relation_target_outside_scope")
            if target["class_iri"] not in prop.get("range_class_iris", []):
                issues.append("range_mismatch")
            if proposal["polarity"] not in {"affirmed", "negated"}:
                issues.append("relation_polarity_invalid")
            if target_id != "document":
                target_anchor = resolve_citation(sources, target["anchor"])
                issue = _owner_issue(sources, target_anchor, evidence)
                if issue:
                    issues.append(issue)
            fact.update(object_id=target_id, object_text=target["anchor"]["quote"],
                        object_class_iri=target["class_iri"], polarity=proposal["polarity"])
    except ValueError as exc:
        issues.append(str(exc))
    except (KeyError, TypeError, AttributeError):
        issues.append("candidate_contract_invalid")
    output["issues"] = list(dict.fromkeys(issues))
    output["validation_status"] = ("passed" if not issues else "incomplete"
                                   if all(issue in _INCOMPLETE for issue in issues) else "failed")
    return output
