"""US1 — E11 `defined` criteria → owl:equivalentClass projection + release gate
(T016 / T018, data-model.md §B1, FR-014, 宪章 II).

Seeds the authoritative TTL into the metadata tables (project_from_ttl) then the
declarative rule layer (seed_declarative_rules), and asserts:
  • each R-DC1~4 target class gets a deterministic-BNode owl:equivalentClass
    class expression of the right OWL2 shape (existential / union / datatype
    facet / boolean hasValue);
  • the projection is round-trip stable AND preserves a co-located named-IRI
    external alignment verbatim;
  • the consistency gate (`validate`) blocks a criterion whose pattern or
    referenced property/filler cannot be resolved.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from rdflib import OWL, RDF, RDFS, XSD, BNode, Graph, Literal, Namespace, URIRef
from rdflib.compare import isomorphic

from app.config import settings
from app.models.ontology_meta import (
    OntologyClass,
    OntologyClassificationCriterion,
    OntologyDataProperty,
)
from app.services import ttl_merge
from app.services.reasoning.seed_declarative import (
    HAS_BETA_LACTAM_RING_IRI,
    seed_declarative_rules,
)
from tests.conftest import FakeOntologyEngine
from app.services.ontology_meta_store import OntologyMetaStore

DRUG = Namespace("https://ontology.pharma-gmp.cn/slpra/drug/")
CHEBI = URIRef("http://purl.obolibrary.org/obo/CHEBI_35610")

# Real authoritative ontology (the autouse `_isolate_ontology_dir` fixture
# redirects settings.ontology_dir to an empty tmp dir; we repopulate it so the
# full project_from_ttl → seed_declarative_rules pipeline runs realistically).
_REAL_ONTOLOGY = Path(__file__).resolve().parents[3] / "ontology" / "slpra"


def _seed(db):
    for ttl in _REAL_ONTOLOGY.glob("*.ttl"):
        shutil.copy(ttl, Path(settings.ontology_dir) / ttl.name)
    store = OntologyMetaStore(db=db, engine=FakeOntologyEngine())
    store.project_from_ttl()
    seed_declarative_rules(db)
    return store


def _expr_root(g: Graph, target: URIRef):
    """The single BNode owl:equivalentClass class-expression hung off `target`."""
    roots = [o for o in g.objects(target, OWL.equivalentClass) if isinstance(o, BNode)]
    assert len(roots) == 1, f"{target}: expected exactly one BNode equivalentClass"
    return roots[0]


def test_seed_inserts_dataproperty_and_seven_criteria(db):
    _seed(db)
    assert db.query(OntologyDataProperty).filter_by(slpra_iri=HAS_BETA_LACTAM_RING_IRI).first()
    keys = {c.criterion_key for c in db.query(OntologyClassificationCriterion).all()}
    # R-DC1~4 (US1) + the three US2 gap-closers (hormonal/penicillin upgraded to
    # inferable, antineoplastic newly added) — all targets resolve after the
    # AntineoplasticDrug class is seeded ahead of the criteria (T022 before T025).
    assert keys == {
        "R-DC1", "R-DC2", "R-DC3", "R-DC4",
        "HormonalDrug-suff", "PenicillinDrug-suff", "AntineoplasticDrug-suff",
    }
    # idempotent: a second seed inserts nothing
    assert seed_declarative_rules(db) == 0


def test_seed_is_idempotent_only_via_project(db):
    _seed(db)
    before = db.query(OntologyClassificationCriterion).count()
    seed_declarative_rules(db)
    assert db.query(OntologyClassificationCriterion).count() == before == 7


def test_r_dc1_existential_expression(db):
    _seed(db)
    g, _ = ttl_merge.build_managed_graph(db)
    root = _expr_root(g, URIRef(DRUG.CytotoxicDrug))
    assert (root, RDF.type, OWL.Restriction) in g
    assert (root, OWL.onProperty, URIRef(DRUG.hasToxicityProfile)) in g
    assert (root, OWL.someValuesFrom, URIRef(DRUG.GenotoxicityProfile)) in g


def test_r_dc2_union_expression(db):
    _seed(db)
    g, _ = ttl_merge.build_managed_graph(db)
    root = _expr_root(g, URIRef(DRUG.HighActivityDrug))
    assert (root, OWL.onProperty, URIRef(DRUG.hasOEBClassification)) in g
    union = next(g.objects(root, OWL.someValuesFrom))
    members = set(g.items(next(g.objects(union, OWL.unionOf))))
    assert members == {URIRef(DRUG.OEB4), URIRef(DRUG.OEB5)}


def test_r_dc3_datatype_facet_expression(db):
    _seed(db)
    g, _ = ttl_merge.build_managed_graph(db)
    root = _expr_root(g, URIRef(DRUG.HighSensitizingDrug))
    assert (root, OWL.onProperty, URIRef(DRUG.sensitizationLevel)) in g
    dt = next(g.objects(root, OWL.someValuesFrom))
    assert (dt, RDF.type, RDFS.Datatype) in g
    assert (dt, OWL.onDatatype, XSD.integer) in g
    facets = list(g.items(next(g.objects(dt, OWL.withRestrictions))))
    assert len(facets) == 1
    assert (facets[0], XSD.minExclusive, Literal(3, datatype=XSD.integer)) in g


def test_r_dc4_boolean_hasvalue_expression(db):
    _seed(db)
    g, _ = ttl_merge.build_managed_graph(db)
    root = _expr_root(g, URIRef(DRUG.BetaLactamDrug))
    assert (root, OWL.onProperty, URIRef(DRUG.hasBetaLactamRing)) in g
    assert (root, OWL.hasValue, Literal(True)) in g


def test_projection_roundtrip_stable_and_preserves_named_alignment(db):
    _seed(db)
    managed, subjects = ttl_merge.build_managed_graph(db)

    # Minimal base: target class carries a verbatim named-IRI external alignment.
    base = Graph()
    base.add((URIRef(DRUG.CytotoxicDrug), RDF.type, OWL.Class))
    base.add((URIRef(DRUG.CytotoxicDrug), OWL.equivalentClass, CHEBI))

    merged1 = ttl_merge.surgical_merge(base, managed, subjects)
    base2 = ttl_merge.parse_ttl(merged1.serialize(format="turtle"))
    managed2, subjects2 = ttl_merge.build_managed_graph(db)
    merged2 = ttl_merge.surgical_merge(base2, managed2, subjects2)

    assert isomorphic(merged1, merged2), "criterion projection must be round-trip stable"
    # named-IRI alignment survives; BNode criterion axiom coexists with it
    assert (URIRef(DRUG.CytotoxicDrug), OWL.equivalentClass, CHEBI) in merged1
    bnode_axioms = [
        o for o in merged1.objects(URIRef(DRUG.CytotoxicDrug), OWL.equivalentClass)
        if isinstance(o, BNode)
    ]
    assert len(bnode_axioms) == 1


def test_validate_clean_seed_has_no_criterion_blocking(db):
    store = _seed(db)
    report = store.validate()
    crit_codes = {
        b["code"] for b in report["blocking"] if b["code"].startswith("criterion_")
    }
    assert crit_codes == set()


def test_validate_blocks_unresolvable_filler(db):
    store = _seed(db)
    crit = db.query(OntologyClassificationCriterion).filter_by(criterion_key="R-DC1").one()
    crit.pattern = {
        "op": "some_values_from",
        "property": "hasToxicityProfile",
        "filler_class": "NoSuchProfile",
    }
    db.commit()
    report = store.validate()
    codes = {b["code"] for b in report["blocking"]}
    assert "criterion_filler_unresolved" in codes


def test_validate_blocks_malformed_pattern(db):
    store = _seed(db)
    crit = db.query(OntologyClassificationCriterion).filter_by(criterion_key="R-DC3").one()
    crit.pattern = {"op": "datatype_facet", "property": "sensitizationLevel"}  # missing cmp/value
    db.commit()
    report = store.validate()
    codes = {b["code"] for b in report["blocking"]}
    assert "criterion_pattern_invalid" in codes


def test_validate_blocks_unverified_external_alignment(db):
    """T026 / FR-014 (宪章 II): an `external_alignment` criterion pointing at an
    IRI absent from VERIFIED_EXTERNAL_ALIGNMENTS must block release — an
    un-byte-verified external term may never project an axiom onto a managed class."""
    store = _seed(db)
    crit = db.query(OntologyClassificationCriterion).filter_by(
        criterion_key="HormonalDrug-suff"
    ).one()
    crit.pattern = {
        "op": "external_alignment",
        "property": "hasActiveIngredient",
        "alignment": "http://purl.obolibrary.org/obo/CHEBI_99999999",  # not verified
    }
    db.commit()
    report = store.validate()
    blocked = [b for b in report["blocking"] if b["code"] == "criterion_alignment_unverified"]
    assert blocked, "unverified alignment IRI must produce a blocking gate"
    assert "HormonalDrug-suff" in blocked[0]["message"]


def test_validate_allows_verified_external_alignment(db):
    """The three seeded US2 criteria all reference byte-verified ChEBI IRIs, so a
    clean seed yields no `criterion_alignment_unverified` block (gate is precise)."""
    store = _seed(db)
    report = store.validate()
    codes = {b["code"] for b in report["blocking"]}
    assert "criterion_alignment_unverified" not in codes
