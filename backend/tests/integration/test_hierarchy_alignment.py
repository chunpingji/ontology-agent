"""Integration tests for hierarchy-aware entity alignment (014 US4, T036).

``align_entity`` must search existing individuals across the subclass/superclass
chain — not only the exact ``target_class_iri`` — so a duplicate recorded at a
different hierarchy level is detected as a *merge* instead of a new duplicate
(research.md R9, FR-016/FR-017). It records **which level** (``matched_level`` ∈
{subclass, same, parent, none}) and **method** (id/lexical/semantic) produced the
match, applies precedence **subclass → same → parent**, and refuses to
auto-merge genuinely ambiguous (equal-rank) matches — surfacing them for review.

Driven by the seeded DrugProduct test T-Box (``drug_engine`` fixture):

    DrugProduct                     ← target/parent
    └── BiologicalDrugProduct       ← subclass
    Manufacturer                    (separate)

The engine double's ``get_individuals`` returns only individuals seeded at that
exact class key, so a passing test proves the aligner actively traverses the
hierarchy (gathering from subclass/ancestor classes) rather than leaning on the
real engine's ``cls.instances()`` subclass inclusion.
"""

from __future__ import annotations

from app.services.extraction.aligner import align_entity
from tests.fixtures.ontology import (
    APPROVAL_NUMBER,
    BIOLOGIC,
    DRUG_NAME,
    DRUG_PRODUCT,
)

_ID = "approvalNumber"
_LABEL = "drugName"


def _candidate(approval: str | None = None, name: str | None = None) -> dict:
    cand: dict = {}
    if approval is not None:
        cand[APPROVAL_NUMBER] = approval
    if name is not None:
        cand[DRUG_NAME] = name
    return cand


# --------------------------------------------------------------------------- #
# Acceptance 1 — existing at subclass, candidate at parent, same id → merge
# --------------------------------------------------------------------------- #
class TestSubclassMerge:
    def test_candidate_at_parent_merges_to_existing_subclass_individual(self, drug_engine):
        existing = drug_engine.add_individual(
            BIOLOGIC, "bio_trastuzumab",
            properties={APPROVAL_NUMBER: "国药准字S20240001"},
            label_zh="曲妥珠单抗",
        )
        result = align_entity(
            _candidate(approval="国药准字S20240001", name="曲妥珠单抗"),
            DRUG_PRODUCT, drug_engine,
            id_property=_ID, label_property=_LABEL,
        )
        assert result.action == "merge"
        assert result.match_iri == existing.iri
        assert result.matched_level == "subclass"
        assert result.method == "id"

    def test_subclass_merge_by_label_records_lexical_method(self, drug_engine):
        existing = drug_engine.add_individual(
            BIOLOGIC, "bio_by_label", label_zh="注射用曲妥珠单抗",
        )
        result = align_entity(
            _candidate(name="注射用曲妥珠单抗"),
            DRUG_PRODUCT, drug_engine,
            id_property=_ID, label_property=_LABEL,
        )
        assert result.action == "merge"
        assert result.match_iri == existing.iri
        assert result.matched_level == "subclass"
        assert result.method == "lexical"


# --------------------------------------------------------------------------- #
# Acceptance 2 — matches at multiple levels → precedence subclass → same → parent
# --------------------------------------------------------------------------- #
class TestPrecedence:
    def test_subclass_wins_over_same_level(self, drug_engine):
        sub = drug_engine.add_individual(
            BIOLOGIC, "bio_dup",
            properties={APPROVAL_NUMBER: "国药准字H20250009"}, label_zh="双胞胎-子类",
        )
        drug_engine.add_individual(
            DRUG_PRODUCT, "dp_dup",
            properties={APPROVAL_NUMBER: "国药准字H20250009"}, label_zh="双胞胎-本类",
        )
        result = align_entity(
            _candidate(approval="国药准字H20250009"),
            DRUG_PRODUCT, drug_engine, id_property=_ID, label_property=_LABEL,
        )
        assert result.action == "merge"
        assert result.match_iri == sub.iri          # subclass precedes same class
        assert result.matched_level == "subclass"
        assert result.method == "id"

    def test_same_level_match_records_same(self, drug_engine):
        same = drug_engine.add_individual(
            DRUG_PRODUCT, "dp_same",
            properties={APPROVAL_NUMBER: "国药准字H20250010"}, label_zh="本类药",
        )
        result = align_entity(
            _candidate(approval="国药准字H20250010"),
            DRUG_PRODUCT, drug_engine, id_property=_ID, label_property=_LABEL,
        )
        assert result.action == "merge"
        assert result.match_iri == same.iri
        assert result.matched_level == "same"
        assert result.method == "id"

    def test_parent_level_match_records_parent(self, drug_engine):
        # Target is the subclass; the only existing match lives at the parent.
        parent_ind = drug_engine.add_individual(
            DRUG_PRODUCT, "dp_parent",
            properties={APPROVAL_NUMBER: "国药准字H20250011"}, label_zh="父类药",
        )
        result = align_entity(
            _candidate(approval="国药准字H20250011"),
            BIOLOGIC, drug_engine, id_property=_ID, label_property=_LABEL,
        )
        assert result.action == "merge"
        assert result.match_iri == parent_ind.iri
        assert result.matched_level == "parent"
        assert result.method == "id"


# --------------------------------------------------------------------------- #
# Acceptance 3 — equal-rank ambiguity → no auto-merge, surfaced for review
# --------------------------------------------------------------------------- #
class TestAmbiguityReview:
    def test_equal_rank_duplicates_are_not_auto_merged(self, drug_engine):
        a = drug_engine.add_individual(
            BIOLOGIC, "bio_amb_a",
            properties={APPROVAL_NUMBER: "国药准字S20250099"}, label_zh="歧义A",
        )
        b = drug_engine.add_individual(
            BIOLOGIC, "bio_amb_b",
            properties={APPROVAL_NUMBER: "国药准字S20250099"}, label_zh="歧义B",
        )
        result = align_entity(
            _candidate(approval="国药准字S20250099"),
            DRUG_PRODUCT, drug_engine, id_property=_ID, label_property=_LABEL,
        )
        assert result.action == "review"          # NOT auto-merged
        assert result.match_iri is None
        assert result.matched_level == "subclass"  # the rank where the tie occurred
        assert set(result.ambiguous_iris) == {a.iri, b.iri}


# --------------------------------------------------------------------------- #
# No match → new; backward-compatible same-class behavior preserved
# --------------------------------------------------------------------------- #
class TestNoMatch:
    def test_unmatched_candidate_is_new(self, drug_engine):
        drug_engine.add_individual(
            DRUG_PRODUCT, "dp_other",
            properties={APPROVAL_NUMBER: "国药准字H20259999"},
        )
        result = align_entity(
            _candidate(approval="国药准字H20250000", name="全新药品"),
            DRUG_PRODUCT, drug_engine, id_property=_ID, label_property=_LABEL,
        )
        assert result.action == "new"
        assert result.match_iri is None
        assert result.matched_level == "none"
