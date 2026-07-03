"""Unit tests for ontology-aware ``edges_to_facts`` (014 US3, T033).

US3 rewires ``edges_to_facts`` to consult the ontology instead of hardcoded
class-name string matches (research.md R10, FR-012/013/014/015). The four
acceptance scenarios exercised here:

1. **Hierarchy membership (FR-012)** — an object class that is a *subclass* of
   ``DrugProduct`` (whose IRI does NOT contain the substring "DrugProduct")
   still contributes to ``drug_classes``, via ``get_subclasses``. The legacy
   ``"DrugProduct" in obj_class`` substring test misses it.
2. **Domain gating (FR-013)** — a data property whose declared domain excludes
   the object class is NOT asserted into ``data_values`` (no cross-domain
   leakage), via ``get_data_properties_by_domain``.
3. **Alignment population (FR-014)** — a class carrying an external-standard
   alignment populates the previously-empty ``Facts.alignments``, via the new
   ``get_class_alignments``.
4. **Controlled-vocab normalization (FR-015)** — scalar values are normalized
   against the shared controlled vocabulary (reuses the R6 transform seam).

The tests drive a lightweight fake engine so they stay pure unit tests (no DB,
no published World) while pinning the exact ontology-aware contract.
"""

from __future__ import annotations

from app.services.reasoning.fact_bridge import edges_to_facts

# --------------------------------------------------------------------------- #
# Canonical IRIs (managed SLPRA namespaces — mirror seed_declarative.py)
# --------------------------------------------------------------------------- #
_DRUG_NS = "https://ontology.pharma-gmp.cn/slpra/drug/"
_EQUIP_NS = "https://ontology.pharma-gmp.cn/slpra/equipment/"
_DEV_NS = "https://ontology.pharma-gmp.cn/slpra/drug-development/"

DRUG_PRODUCT = _DRUG_NS + "DrugProduct"
# Subclass whose IRI does NOT contain the substring "DrugProduct" — the exact
# case the legacy substring test (SC-004) fails on.
BIOLOGICAL_PRODUCT = _DRUG_NS + "BiologicalProduct"
CHEMICAL_DRUG = _DRUG_NS + "ChemicalDrug"
EQUIPMENT = _EQUIP_NS + "Equipment"

PDE_IRI = _DRUG_NS + "pde_mg_per_day"          # domain: DrugProduct (+ subclasses)
MATERIAL_IRI = _EQUIP_NS + "material_grade"     # domain: Equipment (NOT DrugProduct)

# External standard IRI (non-managed) the DrugProduct class aligns to.
CHEBI_ALIGNMENT = "http://purl.obolibrary.org/obo/CHEBI_23888"

_CMC_REPORT = _DEV_NS + "CMCReport"
_DESCRIBES = _DEV_NS + "describes"
_USES_EQUIPMENT = _DEV_NS + "usesEquipment"


class _FakeEngine:
    """Minimal stand-in for ``OntologyEngine`` exposing only the three read
    methods ``edges_to_facts`` consults. Fully deterministic — no DB/World."""

    is_loaded = True

    def __init__(self, *, subclasses=None, domain_props=None, alignments=None):
        self._subclasses = subclasses or {}      # class_iri -> [child_iri, ...]
        self._domain_props = domain_props or {}  # class_iri -> [prop_iri, ...]
        self._alignments = alignments or {}      # class_iri -> [external_iri, ...]

    def get_subclasses(self, class_iri: str, recursive: bool = True) -> list[dict]:
        return [
            {"iri": c, "label": c.rsplit("/", 1)[-1]}
            for c in self._subclasses.get(class_iri, [])
        ]

    def get_data_properties_by_domain(self, class_iri: str) -> list[dict]:
        return [
            {"iri": p, "name": p.rsplit("/", 1)[-1], "label": p.rsplit("/", 1)[-1]}
            for p in self._domain_props.get(class_iri, [])
        ]

    def get_class_alignments(self, class_iri: str) -> list[str]:
        return list(self._alignments.get(class_iri, []))


def _engine() -> _FakeEngine:
    """A fully-configured fake engine covering all four scenarios."""
    return _FakeEngine(
        subclasses={DRUG_PRODUCT: [BIOLOGICAL_PRODUCT, CHEMICAL_DRUG]},
        domain_props={DRUG_PRODUCT: [PDE_IRI], BIOLOGICAL_PRODUCT: [PDE_IRI]},
        alignments={DRUG_PRODUCT: [CHEBI_ALIGNMENT], BIOLOGICAL_PRODUCT: [CHEBI_ALIGNMENT]},
    )


def _subclass_drug_edge() -> dict:
    """Edge whose object is a DrugProduct *subclass* carrying a 分类 value."""
    return {
        "subject_class_iri": _CMC_REPORT,
        "predicate_iri": _DESCRIBES,
        "object_class_iri": BIOLOGICAL_PRODUCT,
        "object_text": "曲妥珠单抗",
        "object_data_properties": [
            {"iri": None, "label": "分类", "value": "治疗用生物制品"},
        ],
        "source_ref": "§ 产品信息",
    }


def _mixed_domain_edge() -> dict:
    """DrugProduct edge carrying one in-domain and one out-of-domain data prop."""
    return {
        "subject_class_iri": _CMC_REPORT,
        "predicate_iri": _DESCRIBES,
        "object_class_iri": DRUG_PRODUCT,
        "object_text": "HRS-1234",
        "object_data_properties": [
            {"iri": PDE_IRI, "label": "PDE", "value": "1.80"},          # in-domain
            {"iri": MATERIAL_IRI, "label": "材质", "value": "316L"},     # cross-domain
        ],
        "source_ref": "§ 产品信息",
    }


# --------------------------------------------------------------------------- #
# Scenario 1 — hierarchy membership (FR-012)
# --------------------------------------------------------------------------- #
class TestHierarchyMembership:
    def test_subclass_object_contributes_to_drug_classes(self):
        facts = edges_to_facts([_subclass_drug_edge()], _engine())
        assert "治疗用生物制品" in facts.drug_classes

    def test_legacy_substring_test_would_miss_subclass(self):
        # Without an engine, the legacy substring test cannot see the subclass
        # (its IRI has no "DrugProduct" substring) → drug_classes stays empty.
        facts = edges_to_facts([_subclass_drug_edge()], None)
        assert facts.drug_classes == []

    def test_non_drug_object_never_contributes(self):
        edge = {
            "subject_class_iri": _CMC_REPORT,
            "predicate_iri": _USES_EQUIPMENT,
            "object_class_iri": EQUIPMENT,
            "object_data_properties": [{"iri": None, "label": "分类", "value": "搅拌釜"}],
            "source_ref": "表 设备",
        }
        facts = edges_to_facts([edge], _engine())
        assert facts.drug_classes == []


# --------------------------------------------------------------------------- #
# Scenario 2 — domain gating / no cross-domain leakage (FR-013)
# --------------------------------------------------------------------------- #
class TestDomainGating:
    def test_in_domain_property_asserted(self):
        facts = edges_to_facts([_mixed_domain_edge()], _engine())
        assert facts.data_values.get("pde_mg_per_day") == "1.80"

    def test_out_of_domain_property_suppressed(self):
        facts = edges_to_facts([_mixed_domain_edge()], _engine())
        # material_grade's domain is Equipment, not DrugProduct → must NOT leak.
        assert "material_grade" not in facts.data_values

    def test_without_engine_no_gating(self):
        # Legacy path asserts every iri-keyed value (backward compatible).
        facts = edges_to_facts([_mixed_domain_edge()], None)
        assert facts.data_values.get("pde_mg_per_day") == "1.80"
        assert facts.data_values.get("material_grade") == "316L"


# --------------------------------------------------------------------------- #
# Scenario 3 — external-alignment population (FR-014)
# --------------------------------------------------------------------------- #
class TestAlignmentPopulation:
    def test_alignment_populated_from_class(self):
        facts = edges_to_facts([_mixed_domain_edge()], _engine())
        assert facts.alignments.get("describes") == [CHEBI_ALIGNMENT]

    def test_alignment_empty_without_engine(self):
        facts = edges_to_facts([_mixed_domain_edge()], None)
        assert facts.alignments == {}

    def test_subclass_alignment_also_populated(self):
        facts = edges_to_facts([_subclass_drug_edge()], _engine())
        assert CHEBI_ALIGNMENT in facts.alignments.get("describes", [])


# --------------------------------------------------------------------------- #
# Scenario 4 — controlled-vocab normalization (FR-015)
# --------------------------------------------------------------------------- #
class TestControlledVocabNormalization:
    def test_scalar_value_normalized_to_controlled_term(self):
        edge = {
            "subject_class_iri": _CMC_REPORT,
            "predicate_iri": _USES_EQUIPMENT,
            "object_class_iri": EQUIPMENT,
            "object_data_properties": [{"iri": None, "label": "OEB", "value": "oeb3"}],
            "source_ref": "表 设备",
        }
        facts = edges_to_facts([edge], _engine())
        # "oeb3" → canonical "OEB3" via the shared controlled vocabulary.
        assert facts.scalars["OEB"] == "OEB3"

    def test_non_vocab_scalar_passes_through(self):
        edge = {
            "subject_class_iri": _CMC_REPORT,
            "predicate_iri": _USES_EQUIPMENT,
            "object_class_iri": EQUIPMENT,
            "object_data_properties": [{"iri": None, "label": "设备规格", "value": "搅拌釜"}],
            "source_ref": "表 设备",
        }
        facts = edges_to_facts([edge], _engine())
        assert facts.scalars["设备规格"] == "搅拌釜"


# --------------------------------------------------------------------------- #
# Backward compatibility — engine is optional; the legacy signature still works
# --------------------------------------------------------------------------- #
class TestBackwardCompatibility:
    def test_empty_edges_produce_empty_facts(self):
        facts = edges_to_facts([], _engine())
        assert facts.relations == {}
        assert facts.data_values == {}
        assert facts.alignments == {}
        assert facts.drug_classes == []

    def test_relations_still_populated_with_engine(self):
        facts = edges_to_facts([_mixed_domain_edge()], _engine())
        assert "describes" in facts.relations
        assert DRUG_PRODUCT in facts.relations["describes"]
