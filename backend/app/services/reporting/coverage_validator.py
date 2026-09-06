"""Material coverage validator for risk assessment reports (010, AST-2).

Traverses a declarative :class:`ReportTemplate`, resolves each slot against the
actual extraction edges and decision-rule evaluations, and produces a
:class:`CoverageManifest`. This is the proof of "无遗漏" (no-omission): every
required slot in the template is accounted for as either filled / inferred or
**explicitly flagged missing** — there is no silent third state.

Coverage status per slot
-------------------------
- ``filled``           : a concrete value was found (extraction / constant), or a
                         rule's antecedent is determinately FALSE (low risk).
- ``inferred``         : a rule's antecedent evaluated TRUE — its row is meaningful.
- ``manual``           : a human-filled slot (team / review / approvals).
- ``blank_optional``   : an optional slot with no value — left blank, no flag.
- ``missing_required`` : a REQUIRED slot whose value/evaluation is absent
                         (extraction miss, or rule antecedent UNKNOWN). This is the
                         G1 signal: distinguishes "确认低风险" from "无数据".

Rule slots intentionally expand per-rule (one HazID dimension each), because a
missing dimension is a real omission. Equipment columns are evaluated per-column
across all equipment rows (homogeneous rows — a missing cell is not structural).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from app.services.reasoning.interpreter import FALSE, TRUE, evaluate

from app.services.reporting.ast_template import (
    ExtractionSource,
    Group,
    ReportTemplate,
    Slot,
    coverage_key,
)
from app.services.reporting.source_ref import format_source_ref

# Status constants
FILLED = "filled"
INFERRED = "inferred"
MANUAL = "manual"
BLANK_OPTIONAL = "blank_optional"
MISSING_REQUIRED = "missing_required"
DISMISSED = "dismissed"


def _short(iri: str) -> str:
    for sep in ("#", "/"):
        idx = iri.rfind(sep)
        if idx >= 0:
            return iri[idx + 1 :]
    return iri


@dataclass
class SlotCoverage:
    slot_id: str  # may be an instance id, e.g. "assessment.pre_control_level[R-RA2]"
    label: str
    status: str
    source_kind: str  # extraction | rule | manual | constant
    value: str | None = None
    source_ref: str | None = None
    rule_key: str | None = None
    hazid: str | None = None
    note: str | None = None
    source_span: str | None = None
    is_llm_sourced: bool = False


@dataclass
class CoverageManifest:
    template_id: str
    slots: list[SlotCoverage] = field(default_factory=list)
    snapshot_id: str | None = None
    manifest_id: str | None = None
    discovery_revision: str | None = None
    selector_version: str | None = None
    instance_manifest: dict | None = None

    @property
    def total_slots(self) -> int:
        return len(self.slots)

    @property
    def _counts(self) -> Counter:
        return Counter(s.status for s in self.slots)

    @property
    def filled(self) -> int:
        return self._counts[FILLED]

    @property
    def inferred(self) -> int:
        return self._counts[INFERRED]

    @property
    def manual(self) -> int:
        return self._counts[MANUAL]

    @property
    def blank_optional(self) -> int:
        return self._counts[BLANK_OPTIONAL]

    @property
    def missing_required(self) -> int:
        return self._counts[MISSING_REQUIRED]

    @property
    def dismissed(self) -> int:
        return self._counts[DISMISSED]

    @property
    def missing_required_slots(self) -> list[SlotCoverage]:
        return [s for s in self.slots if s.status == MISSING_REQUIRED]

    @property
    def dismissed_slots(self) -> list[SlotCoverage]:
        return [s for s in self.slots if s.status == DISMISSED]

    @property
    def has_omissions(self) -> bool:
        return self.missing_required > 0

    def summary(self) -> dict[str, Any]:
        """Compact summary for audit ``details`` (FR-012)."""
        return {
            "template_id": self.template_id,
            "total_slots": self.total_slots,
            "filled": self.filled,
            "inferred": self.inferred,
            "manual": self.manual,
            "blank_optional": self.blank_optional,
            "missing_required": self.missing_required,
            "dismissed": self.dismissed,
            "missing_slot_ids": [s.slot_id for s in self.missing_required_slots],
        }

    def to_dict(self) -> dict[str, Any]:
        """Full manifest for persistence in ``GeneratedReport.rules_summary``."""
        result = {**self.summary(), "slots": [asdict(s) for s in self.slots]}
        if self.snapshot_id:
            result.update(snapshot_id=self.snapshot_id, manifest_id=self.manifest_id,
                          discovery_revision=self.discovery_revision,
                          selector_version=self.selector_version, instance_manifest=self.instance_manifest)
        return result


# --------------------------------------------------------------------------- #
# Resolution helpers
# --------------------------------------------------------------------------- #


def _matching_edges(edges: Sequence[dict], class_contains: str) -> list[dict]:
    return [e for e in edges if class_contains in e.get("object_class_iri", "")]


# 设备列的标签别名：外部档案用「规格型号/主体材质/equipmentName」，文档用「设备规格/材质/
# 设备名称」——覆盖判定须同时认这些，否则设备表已有值而覆盖清单仍报缺失。富化阶段已把档案
# 值写入文档规范标签，别名在此作为双保险（同口径识别 FILLED）。
_EQUIPMENT_LABEL_ALIASES: dict[str, tuple[str, ...]] = {
    "设备名称": ("设备名称", "equipmentName"),
    "设备规格": ("设备规格", "规格型号", "modelSpecification"),
    "规格型号": ("规格型号", "设备规格", "modelSpecification"),
    "材质": ("材质", "主体材质", "constructedOf"),
    "主体材质": ("主体材质", "材质", "constructedOf"),
}


def _collect_equipment_edges(edges: Sequence[dict]) -> list[dict]:
    """递归收集设备 edge（顶层 + ``sub_relationships``，含 Reactor/Centrifuge 等子类），按设备
    编号去重。识别/遍历复用 :func:`iter_equipment_edges`（谓词权威判定，绝不按命名空间前缀
    泛判），与设备表/富化**同口径**，避免「表有值、覆盖报缺失」，也不误纳 ConstructionMaterial
    等非设备类。"""
    from app.services.extraction.equipment_source import iter_equipment_edges

    seen: set[str] = set()
    out: list[dict] = []
    for edge in iter_equipment_edges(edges):
        code = str(edge.get("object_text") or "").strip()
        key = code or f"__anon__{id(edge)}"
        if key not in seen:
            seen.add(key)
            out.append(edge)
    return out


def _resolve_extraction(slot: Slot, edges: Sequence[dict]) -> SlotCoverage:
    src: ExtractionSource = slot.source  # type: ignore[assignment]
    value: str | None = None
    source_ref: str | None = None

    if src.relation:
        for e in edges:
            if (
                _short(e.get("predicate_iri", "")) == src.relation
                and src.object_class_iri_contains in e.get("object_class_iri", "")
            ):
                value = "存在"
                source_ref = format_source_ref(e.get("source_ref")) or None
                break
    else:
        matching = _matching_edges(edges, src.object_class_iri_contains)
        if src.text:
            for e in matching:
                t = e.get("object_text")
                if t:
                    value = str(t)
                    source_ref = format_source_ref(e.get("source_ref")) or None
                    break
        else:  # data_property or label
            for e in matching:
                for dp in e.get("object_data_properties") or []:
                    val = dp.get("value")
                    if val in (None, ""):
                        continue
                    hit = (
                        src.data_property
                        and dp.get("iri")
                        and _short(dp["iri"]) == src.data_property
                    ) or (src.label and dp.get("label") == src.label)
                    if hit:
                        value = str(val)
                        source_ref = format_source_ref(e.get("source_ref")) or None
                        break
                if value is not None:
                    break

    if value is not None:
        status = FILLED
    else:
        status = MISSING_REQUIRED if slot.required else BLANK_OPTIONAL
    return SlotCoverage(
        slot_id=slot.slot_id,
        label=slot.label,
        status=status,
        source_kind="extraction",
        value=value,
        source_ref=source_ref,
    )


def _resolve_value_slot(slot: Slot, edges: Sequence[dict]) -> SlotCoverage:
    """Resolve a singular (non-table) slot by its source kind."""
    kind = slot.source.kind
    if kind == "extraction":
        return _resolve_extraction(slot, edges)
    if kind == "constant":
        return SlotCoverage(
            slot_id=slot.slot_id, label=slot.label, status=FILLED,
            source_kind="constant", value=slot.source.value,  # type: ignore[attr-defined]
        )
    if kind == "semantic":
        # 016+: a semantic slot is an LLM-synthesized content PROJECTION of its
        # section's coverage + prompt. Omission accounting already happened when the
        # section's ``coverage`` bindings expanded at the top of the section loop, so
        # this position is descriptive and NON-COUNTING (FILLED + is_llm_sourced —
        # no new status enum, no counter change → golden-master parity preserved).
        return SlotCoverage(
            slot_id=slot.slot_id, label=slot.label, status=FILLED,
            source_kind="semantic", is_llm_sourced=True,
            note="语义化插槽：报告生成时由 LLM 依据本节关联本体+事实源合成",
        )
    # manual
    return SlotCoverage(
        slot_id=slot.slot_id, label=slot.label, status=MANUAL, source_kind="manual",
    )


def _resolve_equipment(group: Group, edges: Sequence[dict]) -> list[SlotCoverage]:
    records: list[SlotCoverage] = []
    for slot in group.slots:
        src: ExtractionSource = slot.source  # type: ignore[assignment]
        # 递归 + 命名空间口径收集设备（含子类与嵌套），与设备表一致
        matching = _collect_equipment_edges(edges)
        if src.text:  # the id column → "does any equipment exist?"
            count = sum(1 for e in matching if e.get("object_text"))
            if count:
                status, value = FILLED, f"{count} 台设备"
            else:
                status, value = (
                    (MISSING_REQUIRED if slot.required else BLANK_OPTIONAL),
                    None,
                )
        else:  # an optional column → filled if any row populates it
            wanted = _EQUIPMENT_LABEL_ALIASES.get(src.label, (src.label,))
            has = any(
                dp.get("label") in wanted and dp.get("value") not in (None, "")
                for e in matching
                for dp in (e.get("object_data_properties") or [])
            )
            if has:
                status, value = FILLED, None
            else:
                status, value = (
                    (MISSING_REQUIRED if slot.required else BLANK_OPTIONAL),
                    None,
                )
        records.append(SlotCoverage(
            slot_id=slot.slot_id, label=slot.label, status=status,
            source_kind="extraction", value=value,
        ))
    return records


def _resolve_assessment(
    group: Group, rules: Sequence[Any], facts: Any
) -> list[SlotCoverage]:
    if not rules:
        return [SlotCoverage(
            slot_id=f"{group.group_id}.__no_rules__",
            label="风险评估维度",
            status=MISSING_REQUIRED,
            source_kind="rule",
            note="未加载任何 risk_assessment 规则，全部风险维度缺失",
        )]

    records: list[SlotCoverage] = []
    for rule in rules:
        result = evaluate(rule.antecedent, facts)
        consequent = rule.consequent or {}
        hazid = consequent.get("category", "")
        rule_key = getattr(rule, "rule_key", None)
        for slot in group.slots:
            if result is TRUE:
                status = INFERRED
            elif result is FALSE:
                status = FILLED
            else:  # UNKNOWN — dimension can't be evaluated (G1)
                status = MISSING_REQUIRED if slot.required else BLANK_OPTIONAL
            records.append(SlotCoverage(
                slot_id=f"{slot.slot_id}[{rule_key}]" if rule_key else slot.slot_id,
                label=f"{hazid}·{slot.label}" if hazid else slot.label,
                status=status,
                source_kind="rule",
                rule_key=rule_key,
                hazid=hazid,
            ))
    return records


# --------------------------------------------------------------------------- #
# 016: section-level ontology coverage expansion
# --------------------------------------------------------------------------- #


def _target_types(range_iri: str, engine: Any | None) -> set[str]:
    """The range type plus every (recursive) subclass — the set that satisfies a
    relationship's range (D7). Read-only; any engine error degrades to the bare
    range type (V10/V11)."""
    types = {range_iri}
    if engine is not None:
        try:
            for sub in engine.get_subclasses(range_iri, recursive=True) or []:
                iri = sub.get("iri") if isinstance(sub, dict) else getattr(sub, "iri", None)
                if iri:
                    types.add(iri)
        except Exception:  # pragma: no cover - defensive (V11)
            pass
    return types


def coverage_scoped_edges(
    binding: Any, edges: Sequence[dict], engine: Any | None = None
) -> list[dict]:
    """The subset of ``edges`` whose object type satisfies an ``ontology_relation``
    binding's range — the range type ∪ its subclasses when an engine is loaded, or a
    local-name substring fallback offline (mirrors :func:`_relationship_present`).
    Non-``ontology_relation`` bindings scope nothing.

    Read-only. The semantic-slot generator uses this to gather the *associated
    ontology* facts for one declared coverage relationship (016+)."""
    if getattr(binding, "kind", None) != "ontology_relation":
        return []
    range_iri = binding.range_class_iri
    if engine is not None:
        target_types = _target_types(range_iri, engine)
        return [e for e in edges if (e.get("object_class_iri") or "") in target_types]
    needle = _short(range_iri)
    return [e for e in edges if needle in (e.get("object_class_iri") or "")]


def _relationship_present(
    edges: Sequence[dict], range_iri: str, target_types: set[str], engine: Any | None
) -> bool:
    """Is the declared relationship's target type present among the extraction edges?

    Engine present → exact match against the type set (range ∪ subclasses).
    Engine None → offline local-name substring fallback (still flags omissions, D7/FR-012).
    """
    if engine is not None:
        return any((e.get("object_class_iri") or "") in target_types for e in edges)
    needle = _short(range_iri)
    return any(needle in (e.get("object_class_iri") or "") for e in edges)


def _range_data_properties(
    binding: Any, engine: Any, schema_cache: dict[str, list[dict]]
) -> list[dict]:
    """The target type's own data-property checklist, read from the memoized
    ``get_relation_schema(doc_class_iri)`` view (V7). A missing edge ⇒ no expansion.

    D8 (accepted constraint, not a bug): the engine's ``get_relation_schema`` applies
    a *global first-discovery* dedup (``visited_ranges``) — each range type is emitted
    on its FIRST discovered predicate only. So if two distinct predicates both target
    the same range type, only the first-discovered predicate's edge carries that type's
    property list; a second predicate binding to the same range finds no edge here and
    expands no properties. This is an authoring single-hop limitation we accept.
    """
    doc_iri = binding.doc_class_iri
    if doc_iri not in schema_cache:
        try:
            schema_cache[doc_iri] = list(engine.get_relation_schema(doc_iri) or [])
        except Exception:  # pragma: no cover - defensive (V11)
            schema_cache[doc_iri] = []  # memo the failure so V7 (≤1 call) still holds
    edge = next(
        (
            e
            for e in schema_cache[doc_iri]
            if e.get("predicate_iri") == binding.predicate_iri
            and e.get("range_class_iri") == binding.range_class_iri
        ),
        None,
    )
    return list(edge.get("range_data_properties") or []) if edge else []


def _is_promoted(prop: dict, required_properties: Sequence[str]) -> bool:
    """Was this range data property promoted to required via ``required_properties``?
    Matches by full IRI, local-name, ``name`` or ``label`` so authors can promote by
    whichever handle they hold (FR-007)."""
    if not required_properties:
        return False
    prop_iri = prop.get("iri")
    candidates = {
        c
        for c in (
            prop_iri,
            _short(prop_iri) if prop_iri else None,
            prop.get("name"),
            prop.get("label"),
        )
        if c
    }
    return any(c in required_properties for c in candidates)


def _prop_present(prop: dict, edges: Sequence[dict], target_types: set[str]) -> bool:
    """Does some target-type edge carry a non-empty value for this data property?
    Matches the schema property (iri/label) against the edge's ``object_data_properties``
    by IRI equality, local-name, or label."""
    prop_iri = prop.get("iri")
    prop_local = _short(prop_iri) if prop_iri else None
    prop_label = prop.get("label")
    for e in edges:
        if (e.get("object_class_iri") or "") not in target_types:
            continue
        for dp in e.get("object_data_properties") or []:
            if dp.get("value") in (None, ""):
                continue
            dp_iri = dp.get("iri")
            if (
                (prop_iri and dp_iri and dp_iri == prop_iri)
                or (prop_local and dp_iri and _short(dp_iri) == prop_local)
                or (prop_label and dp.get("label") == prop_label)
            ):
                return True
    return False


def _resolve_coverage_binding(
    binding: Any,
    edges: Sequence[dict],
    engine: Any | None,
    seen_keys: set[tuple[str, str, str]],
    schema_cache: dict[str, list[dict]],
) -> list[SlotCoverage]:
    """Expand one ``section.coverage`` binding into synthetic :class:`SlotCoverage`
    positions, reusing the existing status vocabulary (D12). See
    ``contracts/coverage-validation.md`` for the full guarantee matrix."""
    kind = getattr(binding, "kind", None)

    if kind == "fact_source":
        # D11 / V8: an explicitly human-sourced fact — one non-counting MANUAL
        # position (surfaces the binding, never an omission).
        source = getattr(binding, "source", "") or ""
        return [
            SlotCoverage(
                slot_id=coverage_key(binding),
                label=getattr(binding, "label", None) or _short(source),
                status=MANUAL,
                source_kind="fact_source",
                source_ref=source or None,
            )
        ]

    # ontology_relation
    pred_iri = binding.predicate_iri
    range_iri = binding.range_class_iri
    key = (binding.doc_class_iri, pred_iri, range_iri)
    is_reference = key in seen_keys  # D3 / V5: the same relationship in a later section
    seen_keys.add(key)

    target_types = _target_types(range_iri, engine)
    present = _relationship_present(edges, range_iri, target_types, engine)

    rel_slot_id = coverage_key(binding)
    label = getattr(binding, "label", None) or _short(pred_iri)
    source_ref = f"{pred_iri} → {range_iri}"

    if is_reference:
        # Narrative-only reference: surfaces the binding (FR-011a) but is excluded
        # from the omission Counter — FILLED/BLANK_OPTIONAL never increment
        # missing_required, so the omission is counted once at its first section.
        return [
            SlotCoverage(
                slot_id=rel_slot_id,
                label=label,
                status=FILLED if present else BLANK_OPTIONAL,
                source_kind="ontology_relation",
                source_ref=source_ref,
                note="重复关系（其它章节已计入无遗漏），此处仅作叙述引用",
            )
        ]

    rel_status = (
        FILLED if present else (MISSING_REQUIRED if binding.required else BLANK_OPTIONAL)
    )
    positions = [
        SlotCoverage(
            slot_id=rel_slot_id,
            label=label,
            status=rel_status,
            source_kind="ontology_relation",
            source_ref=source_ref,
        )
    ]

    # Property expansion only when the relationship is present AND an engine is
    # available (offline degrades to the relationship signal alone, V6).
    if present and engine is not None:
        required_props = getattr(binding, "required_properties", None) or []
        for prop in _range_data_properties(binding, engine, schema_cache):
            prop_iri = prop.get("iri")
            blank = not _prop_present(prop, edges, target_types)
            if not blank:
                pstatus = FILLED
            elif _is_promoted(prop, required_props):
                pstatus = MISSING_REQUIRED  # V4: promoted + blank ⇒ omission
            else:
                pstatus = BLANK_OPTIONAL  # V3: informational, not an omission
            positions.append(
                SlotCoverage(
                    slot_id=(
                        f"{rel_slot_id}__{_short(prop_iri)}" if prop_iri else f"{rel_slot_id}__prop"
                    ),
                    label=prop.get("label") or _short(prop_iri or ""),
                    status=pstatus,
                    source_kind="ontology_relation",
                    source_ref=prop_iri,
                )
            )
    return positions


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def validate_coverage(
    template: ReportTemplate,
    edges: Sequence[dict],
    rules: Sequence[Any],
    facts: Any | None = None,
    dismissed_slot_ids: set[str] | None = None,
    engine: Any | None = None,
) -> CoverageManifest:
    """Produce a :class:`CoverageManifest` for ``edges`` + ``rules`` against ``template``.

    ``facts`` may be supplied to reuse an already-built Facts view; otherwise it is
    derived from ``edges``.

    ``dismissed_slot_ids``, when provided, flips ``missing_required`` slots whose
    base slot_id is in the set to ``dismissed`` status (011 FR-API-006).

    ``engine`` (016) is a read-only :class:`OntologyEngine`; when supplied it lets
    each section's ``coverage`` declaration expand into relationship- and
    property-level positions. ``None`` ⇒ legacy behaviour is a strict no-op and the
    ontology path degrades gracefully offline (FR-012 / FR-013).
    """
    if facts is None:
        from app.services.ontology_engine import get_loaded_engine
        from app.services.reasoning.fact_bridge import edges_to_facts

        # Ontology-aware fact building (014 US3); None when unloaded → legacy path.
        facts = edges_to_facts(list(edges), get_loaded_engine())

    manifest = CoverageManifest(template_id=template.template_id)
    # 016: cross-section state for the coverage seam — the omission of a given
    # (doc, predicate, range) relationship counts once (D3); the per-doc-class
    # relation schema is fetched at most once (V7 / D9).
    seen_keys: set[tuple[str, str, str]] = set()
    schema_cache: dict[str, list[dict]] = {}
    for section in template.sections:
        # 016 seam: expand section-level ontology coverage BEFORE the group loop.
        # getattr keeps this safe on any legacy Section object lacking the field.
        for binding in getattr(section, "coverage", []) or []:
            manifest.slots.extend(
                _resolve_coverage_binding(binding, edges, engine, seen_keys, schema_cache)
            )
        for group in section.groups:
            if group.kind == "equipment_table":
                manifest.slots.extend(_resolve_equipment(group, edges))
            elif group.kind == "assessment_table":
                manifest.slots.extend(_resolve_assessment(group, rules, facts))
            else:  # fields | manual
                for slot in group.slots:
                    manifest.slots.append(_resolve_value_slot(slot, edges))

    if dismissed_slot_ids:
        for sc in manifest.slots:
            if sc.status == MISSING_REQUIRED:
                base_id = sc.slot_id.split("[")[0]
                if base_id in dismissed_slot_ids or sc.slot_id in dismissed_slot_ids:
                    sc.status = DISMISSED

    return manifest
