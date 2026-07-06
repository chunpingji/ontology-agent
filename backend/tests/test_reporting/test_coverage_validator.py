"""Unit tests for the material coverage validator (010, AST-2; 016 AST-2/AST-3)."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.reporting.ast_template import (
    FactSourceBinding,
    Group,
    OntologyRelationBinding,
    ReportTemplate,
    Section,
    SemanticSource,
    Slot,
    load_default_template,
)
from app.services.reporting.coverage_validator import (
    BLANK_OPTIONAL,
    FILLED,
    MANUAL,
    MISSING_REQUIRED,
    coverage_scoped_edges,
    validate_coverage,
)

from tests.fixtures.ontology import (
    DRUG_NS,
    DRUG_PRODUCT,
    MANUFACTURED_BY,
    MANUFACTURER,
    MFR_NAME,
    build_drug_ontology,
)

# --- edge fixtures (shapes mirror test_risk_report_generator.py) ------------ #


def _drug_edge() -> dict:
    return {
        "predicate_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/describes",
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug/DrugProduct",
        "object_text": "HRS-1234",
        "object_data_properties": [
            {"iri": "https://ontology.pharma-gmp.cn/slpra/drug/pde_mg_per_day",
             "label": "PDE", "value": "1.80"},
            {"iri": None, "label": "分类", "value": "化学药品"},
        ],
        "source_ref": "§ 产品信息",
    }


def _shared_line_edge() -> dict:
    return {
        "predicate_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/hasSharedLineData",
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/SharedLineAssessmentData",
        "object_text": "共线评估",
        "object_data_properties": [],
        "source_ref": "§ 共线评估",
    }


def _equipment_edge(code: str = "RE001") -> dict:
    return {
        "predicate_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/usesEquipment",
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/equipment/Equipment",
        "object_text": code,
        "object_data_properties": [
            {"iri": None, "label": "设备名称", "value": f"设备-{code}"},
            {"iri": None, "label": "设备规格", "value": "搅拌釜 500L"},
            {"iri": None, "label": "材质", "value": "316L"},
        ],
        "source_ref": "642车间 设备需求",
    }


def _shared_line_rule(key: str, category: str) -> SimpleNamespace:
    return SimpleNamespace(
        rule_key=key,
        antecedent={"op": "some_values_from", "property": "hasSharedLineData",
                    "filler_class": "SharedLineAssessmentData"},
        consequent={"risk_level": "HighRisk", "category": category},
    )


def _full_rules() -> list[SimpleNamespace]:
    return [
        _shared_line_rule("R-RA1", "人员"),
        _shared_line_rule("R-RA2", "生产设备"),
    ]


# --- tests ------------------------------------------------------------------ #


class TestCoverageValidator:
    def test_full_coverage_has_no_omissions(self):
        tpl = load_default_template()
        edges = [_drug_edge(), _shared_line_edge(), _equipment_edge()]
        m = validate_coverage(tpl, edges, _full_rules())
        assert not m.has_omissions
        assert m.missing_required == 0
        # extraction slots filled
        by_id = {s.slot_id: s for s in m.slots}
        assert by_id["subject.name"].status == "filled"
        assert by_id["subject.pde"].value == "1.80"
        assert by_id["prereq.shared_line"].status == "filled"
        # rule slots inferred (antecedent TRUE because shared-line present)
        assert by_id["assessment.pre_control_level[R-RA2]"].status == "inferred"

    def test_missing_shared_line_flags_prereq_and_dimensions(self):
        tpl = load_default_template()
        edges = [_drug_edge(), _equipment_edge()]  # no shared-line edge
        m = validate_coverage(tpl, edges, _full_rules())
        assert m.has_omissions
        missing = {s.slot_id for s in m.missing_required_slots}
        # prerequisite surfaces the missing extraction input
        assert "prereq.shared_line" in missing
        # and the rule dimensions become UNKNOWN → missing (G1: not silent "低")
        assert "assessment.pre_control_level[R-RA1]" in missing
        assert "assessment.pre_control_level[R-RA2]" in missing

    def test_missing_equipment_flags_required_id(self):
        tpl = load_default_template()
        edges = [_drug_edge(), _shared_line_edge()]  # no equipment
        m = validate_coverage(tpl, edges, _full_rules())
        by_id = {s.slot_id: s for s in m.slots}
        assert by_id["equipment.id"].status == MISSING_REQUIRED

    def test_no_rules_flags_all_dimensions(self):
        tpl = load_default_template()
        edges = [_drug_edge(), _shared_line_edge(), _equipment_edge()]
        m = validate_coverage(tpl, edges, [])
        missing = {s.slot_id for s in m.missing_required_slots}
        assert any(sid.endswith("__no_rules__") for sid in missing)

    def test_manual_slots_not_counted_missing(self):
        tpl = load_default_template()
        edges = [_drug_edge(), _shared_line_edge(), _equipment_edge()]
        m = validate_coverage(tpl, edges, _full_rules())
        by_id = {s.slot_id: s for s in m.slots}
        assert by_id["team.members"].status == "manual"
        assert by_id["conclusion.text"].status == "manual"
        assert m.manual >= 4  # team, review, conclusion, approvals

    def test_optional_missing_is_blank_not_required(self):
        tpl = load_default_template()
        edges = [_drug_edge(), _shared_line_edge(), _equipment_edge()]
        m = validate_coverage(tpl, edges, _full_rules())
        by_id = {s.slot_id: s for s in m.slots}
        # subject.dosage (剂型) is optional and absent from the drug edge
        assert by_id["subject.dosage"].status == "blank_optional"

    def test_summary_and_to_dict_shapes(self):
        tpl = load_default_template()
        edges = [_drug_edge(), _equipment_edge()]  # missing shared-line
        m = validate_coverage(tpl, edges, _full_rules())
        summ = m.summary()
        assert summ["template_id"] == tpl.template_id
        assert summ["missing_required"] == len(summ["missing_slot_ids"])
        assert summ["total_slots"] == m.total_slots
        full = m.to_dict()
        assert len(full["slots"]) == m.total_slots
        assert "prereq.shared_line" in summ["missing_slot_ids"]


# --- 016 US2: section-level ontology coverage expansion --------------------- #

_REL_ID = "coverage.manufacturedBy__Manufacturer"
_PROP_ID = _REL_ID + "__manufacturerName"


def _mfr_edge(name: str = "Acme 制药", *, blank: bool = False) -> dict:
    """An edge whose object is a Manufacturer individual (the range type)."""
    props = [] if blank else [{"iri": MFR_NAME, "label": "企业名称", "value": name}]
    return {
        "predicate_iri": MANUFACTURED_BY,
        "object_class_iri": MANUFACTURER,
        "object_text": None,
        "object_data_properties": props,
        "source_ref": "§ 生产信息",
    }


def _rel(required: bool = True, required_properties: list[str] | None = None) -> OntologyRelationBinding:
    return OntologyRelationBinding(
        doc_class_iri=DRUG_PRODUCT,
        predicate_iri=MANUFACTURED_BY,
        range_class_iri=MANUFACTURER,
        required=required,
        required_properties=required_properties or [],
    )


def _coverage_template(*section_bindings: list) -> ReportTemplate:
    """One section per binding list; all groups empty (coverage-only)."""
    sections = [
        Section(section_id=f"s{i}", title=f"节 {i}", groups=[], coverage=list(bindings))
        for i, bindings in enumerate(section_bindings)
    ]
    return ReportTemplate(template_id="t-cov", sections=sections)


class _CountingEngine:
    """Wrap the fake engine to count ``get_relation_schema`` invocations (V7)."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.schema_calls = 0

    def get_relation_schema(self, iri, max_hops: int = 4):
        self.schema_calls += 1
        return self._inner.get_relation_schema(iri, max_hops=max_hops)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class TestSectionCoverageExpansion:
    """016 AST-2/AST-3: ``section.coverage`` → synthetic ``SlotCoverage`` positions.

    Per [contracts/coverage-validation.md]. Uses the actual positional signature
    ``validate_coverage(template, edges, rules, facts=None, engine=None)`` (the
    contract's ``facts.node_types()`` pseudocode is aspirational — presence is
    checked against the raw ``edges`` list's ``object_class_iri``).
    """

    def test_coverage_expands_relationship_and_properties(self):
        """V2/V3: a present relationship expands into its target-type property checklist."""
        engine = build_drug_ontology()
        tpl = _coverage_template([_rel()])
        m = validate_coverage(tpl, [_mfr_edge()], [], engine=engine)
        by_id = {s.slot_id: s for s in m.slots}
        assert by_id[_REL_ID].status == FILLED
        assert by_id[_REL_ID].source_kind == "ontology_relation"
        # the range type's data property (manufacturerName) expands as a position
        assert by_id[_PROP_ID].status == FILLED
        assert m.missing_required == 0

    def test_required_relationship_absent_is_missing_required(self):
        """V2: required relationship whose target type is absent ⇒ one MISSING_REQUIRED."""
        engine = build_drug_ontology()
        tpl = _coverage_template([_rel(required=True)])
        m = validate_coverage(tpl, [], [], engine=engine)  # no manufacturer edge
        by_id = {s.slot_id: s for s in m.slots}
        assert by_id[_REL_ID].status == MISSING_REQUIRED
        # absent relationship does NOT expand properties (nothing to expand)
        assert _PROP_ID not in by_id
        assert m.missing_required == 1

    def test_present_relationship_blank_props_informational(self):
        """V3: blank, un-promoted props under a present relationship ⇒ blank_optional, no omission."""
        engine = build_drug_ontology()
        tpl = _coverage_template([_rel()])
        m = validate_coverage(tpl, [_mfr_edge(blank=True)], [], engine=engine)
        by_id = {s.slot_id: s for s in m.slots}
        assert by_id[_REL_ID].status == FILLED
        assert by_id[_PROP_ID].status == BLANK_OPTIONAL
        assert m.missing_required == 0

    def test_promoted_property_blank_is_missing_required(self):
        """V4: a blank property promoted via required_properties ⇒ MISSING_REQUIRED."""
        engine = build_drug_ontology()
        tpl = _coverage_template([_rel(required_properties=[MFR_NAME])])
        m = validate_coverage(tpl, [_mfr_edge(blank=True)], [], engine=engine)
        by_id = {s.slot_id: s for s in m.slots}
        assert by_id[_REL_ID].status == FILLED  # relationship itself present
        assert by_id[_PROP_ID].status == MISSING_REQUIRED
        assert m.missing_required == 1

    def test_promoted_property_matched_by_local_name(self):
        """V4: promotion also matches by local-name, not only full IRI."""
        engine = build_drug_ontology()
        tpl = _coverage_template([_rel(required_properties=["manufacturerName"])])
        m = validate_coverage(tpl, [_mfr_edge(blank=True)], [], engine=engine)
        by_id = {s.slot_id: s for s in m.slots}
        assert by_id[_PROP_ID].status == MISSING_REQUIRED

    def test_duplicate_relationship_across_sections_counts_once(self):
        """V5: same (doc,pred,range) across sections ⇒ omission counted once."""
        engine = build_drug_ontology()
        # two sections declare the identical required relationship; target absent
        tpl = _coverage_template([_rel()], [_rel()])
        m = validate_coverage(tpl, [], [], engine=engine)
        rel_positions = [s for s in m.slots if s.slot_id == _REL_ID]
        assert len(rel_positions) == 2  # both sections still surface the binding
        assert m.missing_required == 1  # but the omission counts exactly once

    def test_coverage_empty_is_noop_legacy_manifest(self):
        """V1/SC-006: coverage == [] ⇒ manifest identical with or without an engine."""
        engine = build_drug_ontology()
        tpl = load_default_template()  # every section coverage == []
        edges = [_drug_edge(), _equipment_edge()]
        base = validate_coverage(tpl, edges, _full_rules())
        witheng = validate_coverage(tpl, edges, _full_rules(), engine=engine)
        assert witheng.missing_required == base.missing_required
        assert witheng.total_slots == base.total_slots
        assert not any(s.slot_id.startswith("coverage.") for s in witheng.slots)

    def test_engine_none_degrades_gracefully(self):
        """V6/V11: engine None ⇒ substring presence, no property expansion, no exception."""
        tpl = _coverage_template([_rel()])
        # present via substring (object_class_iri contains 'Manufacturer'); no engine
        m_present = validate_coverage(tpl, [_mfr_edge()], [], engine=None)
        by_id = {s.slot_id: s for s in m_present.slots}
        assert by_id[_REL_ID].status == FILLED
        assert _PROP_ID not in by_id  # no property expansion without an engine
        # absent required relationship still flags an omission offline
        m_absent = validate_coverage(tpl, [], [], engine=None)
        absent_by_id = {s.slot_id: s for s in m_absent.slots}
        assert absent_by_id[_REL_ID].status == MISSING_REQUIRED
        assert m_absent.missing_required == 1

    def test_fact_source_emits_noncounting_manual(self):
        """V8: a fact_source binding ⇒ one non-counting MANUAL position."""
        engine = build_drug_ontology()
        tpl = _coverage_template(
            [FactSourceBinding(source="org.responsible_person", label="责任人")]
        )
        m = validate_coverage(tpl, [], [], engine=engine)
        manual = [s for s in m.slots if s.source_kind == "fact_source"]
        assert len(manual) == 1
        assert manual[0].status == MANUAL
        assert m.missing_required == 0

    def test_get_relation_schema_memoized_once_per_docclass(self):
        """V7: get_relation_schema called at most once per doc_class_iri per run."""
        engine = _CountingEngine(build_drug_ontology())
        distributed_by = DRUG_NS + "distributedBy"
        # two distinct present relationships sharing the same doc_class_iri
        binding_b = OntologyRelationBinding(
            doc_class_iri=DRUG_PRODUCT,
            predicate_iri=distributed_by,
            range_class_iri=MANUFACTURER,
        )
        tpl = _coverage_template([_rel()], [binding_b])
        validate_coverage(tpl, [_mfr_edge()], [], engine=engine)
        assert engine.schema_calls == 1


# --- 016+: semantic-slot coverage position (non-counting projection) -------- #


def _semantic_section(
    *coverage: OntologyRelationBinding, slot_source: SemanticSource | None = None
) -> Section:
    """A section carrying ontology coverage AND a fields group with one semantic slot."""
    return Section(
        section_id="s-sem",
        title="综合分析",
        prompt="综述本节",
        coverage=list(coverage),
        groups=[Group(
            group_id="g-sem", title="综述", kind="fields",
            slots=[Slot(
                slot_id="analysis.overview", label="综合分析",
                source=slot_source or SemanticSource(),
            )],
        )],
    )


class TestSemanticSlotCoverage:
    """016+: a semantic slot is a content PROJECTION — it must surface as one FILLED /
    is_llm_sourced position that NEVER increments the omission counter (parity)."""

    def test_semantic_slot_is_filled_and_llm_sourced_noncounting(self):
        engine = build_drug_ontology()
        tpl = ReportTemplate(template_id="t-sem", sections=[_semantic_section(_rel())])
        m = validate_coverage(tpl, [_mfr_edge()], [], engine=engine)
        by_id = {s.slot_id: s for s in m.slots}
        sem = by_id["analysis.overview"]
        assert sem.status == FILLED
        assert sem.source_kind == "semantic"
        assert sem.is_llm_sourced is True

    def test_semantic_slot_does_not_change_missing_required(self):
        """Parity: adding a semantic slot to a section leaves ``missing_required`` untouched —
        the omission is still counted once at the section's coverage binding, absent target."""
        engine = build_drug_ontology()
        # required relationship whose target is ABSENT ⇒ exactly one omission…
        with_slot = ReportTemplate(
            template_id="t-a", sections=[_semantic_section(_rel(required=True))]
        )
        m_with = validate_coverage(with_slot, [], [], engine=engine)
        # …and the count is identical to a coverage-only section (no semantic slot).
        without_slot = ReportTemplate(
            template_id="t-b",
            sections=[Section(section_id="s-sem", title="综合分析", groups=[],
                              coverage=[_rel(required=True)])],
        )
        m_without = validate_coverage(without_slot, [], [], engine=engine)
        assert m_with.missing_required == m_without.missing_required == 1

    def test_semantic_slot_alone_counts_nothing(self):
        """A semantic slot with NO coverage on its section is purely descriptive."""
        tpl = ReportTemplate(
            template_id="t-sem",
            sections=[Section(
                section_id="s0", title="节", coverage=[],
                groups=[Group(group_id="g0", title="t", kind="fields",
                              slots=[Slot(slot_id="s0.sem", label="l", source=SemanticSource())])],
            )],
        )
        m = validate_coverage(tpl, [], [], engine=None)
        assert m.missing_required == 0
        by_id = {s.slot_id: s for s in m.slots}
        assert by_id["s0.sem"].status == FILLED
        assert by_id["s0.sem"].is_llm_sourced is True


class TestCoverageScopedEdges:
    """``coverage_scoped_edges`` gathers the *associated ontology* facts for one
    ``ontology_relation`` binding — the slice a semantic slot narrates."""

    def test_engine_scopes_by_range_type(self):
        engine = build_drug_ontology()
        edges = [_mfr_edge(), _drug_edge()]
        scoped = coverage_scoped_edges(_rel(), edges, engine)
        classes = {e["object_class_iri"] for e in scoped}
        assert MANUFACTURER in classes  # range type kept
        assert all("DrugProduct" not in c for c in classes)  # non-range dropped

    def test_engine_none_falls_back_to_local_name_substring(self):
        edges = [_mfr_edge(), _drug_edge()]
        scoped = coverage_scoped_edges(_rel(), edges, engine=None)
        # offline: object_class_iri contains local-name 'Manufacturer'
        assert scoped and all("Manufacturer" in e["object_class_iri"] for e in scoped)

    def test_non_ontology_relation_binding_scopes_nothing(self):
        binding = FactSourceBinding(source="org.x")
        assert coverage_scoped_edges(binding, [_mfr_edge()], engine=None) == []
