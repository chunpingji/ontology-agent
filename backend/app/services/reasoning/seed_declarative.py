"""Idempotent seed for the declarative rule layer (spec 006, T013/T014).

Runs at startup *after* `project_from_ttl` (which seeds the E1–E3 entities the
criteria reference). It is the application-level "independent seed" the tasks
allow (FR-005): it materialises the rule knowledge that does **not** yet exist
in the authoritative TTL —

  T013: the `slpra-drug:hasBetaLactamRing` E3 data property (domain=API,
        range=xsd:boolean). New term; once seeded it is projected back into
        `slpra-drug.ttl` by the surgical merge (never hand-edited).
  T014: the R-DC1~4 E11 classification criteria (`logic_role=defined`),
        derived verbatim from `defaults.DEFAULT_CLASSIFICATION_CRITERIA` so the
        editable T-Box metadata and the runtime engine stay single-sourced.

Idempotent: every row is keyed (slpra_iri / criterion_key) and inserted only
when absent — re-running is a no-op, mirroring `project_from_ttl`.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.ontology_meta import (
    STATUS_PUBLISHED,
    OntologyClass,
    OntologyClassificationCriterion,
    OntologyConflictPolicy,
    OntologyDataProperty,
    OntologyDecisionRule,
)
from app.services.reasoning.defaults import (
    default_classification_criteria,
    default_conflict_policies,
    default_decision_rules,
)

logger = logging.getLogger(__name__)

# slpra-drug module namespace (the criteria's target classes + referenced
# properties all live here; see ontology/slpra/slpra-drug.ttl).
DRUG_NS = "https://ontology.pharma-gmp.cn/slpra/drug/"
# slpra-core module namespace — E12/E13 named resources live here (mirrors
# ttl_merge._DECISION_RULE_PREFIX / _CONFLICT_POLICY_PREFIX).
CORE_NS = "https://ontology.pharma-gmp.cn/slpra/core/"
DECISION_RULE_PREFIX = CORE_NS + "DecisionRule_"
CONFLICT_POLICY_PREFIX = CORE_NS + "ConflictPolicy_"

HAS_BETA_LACTAM_RING_IRI = DRUG_NS + "hasBetaLactamRing"
API_CLASS_IRI = DRUG_NS + "ActivePharmaceuticalIngredient"
ANTINEOPLASTIC_DRUG_IRI = DRUG_NS + "AntineoplasticDrug"
DRUG_PRODUCT_IRI = DRUG_NS + "DrugProduct"


def seed_declarative_rules(db: Session) -> int:
    """Idempotently seed the new ontology terms + classification criteria.

    Order matters: the `AntineoplasticDrug` class (T022) must be seeded before
    the criteria (T014/T025) so the `AntineoplasticDrug-suff` criterion's target
    class resolves on the same run. Returns rows inserted (0 when fully seeded)."""
    inserted = (
        _seed_has_beta_lactam_ring(db)
        + _seed_antineoplastic_drug(db)
        + _seed_classification_criteria(db)
        + _seed_decision_rules(db)
        + _seed_conflict_policies(db)
    )
    if inserted:
        db.commit()
    logger.info("seed_declarative_rules inserted %d rows", inserted)
    return inserted


def _seed_antineoplastic_drug(db: Session) -> int:
    """T022 — E1 `AntineoplasticDrug` (subClassOf DrugProduct), FR-007.

    New term introduced by the §8.0 upgrade path. Once seeded it is projected
    into `slpra-drug.ttl` by the surgical merge (never hand-edited); its
    sufficient condition comes from the `AntineoplasticDrug-suff` E11 criterion
    and its external ChEBI/ATC alignment from the integration layer (T023)."""
    if db.query(OntologyClass).filter_by(slpra_iri=ANTINEOPLASTIC_DRUG_IRI).first():
        return 0
    parent = db.query(OntologyClass).filter_by(slpra_iri=DRUG_PRODUCT_IRI).first()
    if parent is None:
        logger.warning(
            "seed_declarative_rules: %s not found — deferring AntineoplasticDrug seed",
            DRUG_PRODUCT_IRI,
        )
        return 0
    db.add(
        OntologyClass(
            slpra_iri=ANTINEOPLASTIC_DRUG_IRI,
            label="Antineoplastic Drug",
            comment="肿瘤药物：API 经 ChEBI:35610/ATC L01 对齐为抗肿瘤剂（可推断，R3/T021）",
            parent_class_id=parent.id,
            status=STATUS_PUBLISHED,
        )
    )
    # Flush so the new class is queryable by `_seed_classification_criteria` in
    # this same pass — without it (autoflush off) the AntineoplasticDrug-suff
    # criterion's target wouldn't resolve until a second startup (FR-007).
    db.flush()
    return 1


def _seed_has_beta_lactam_ring(db: Session) -> int:
    """T013 — E3 `hasBetaLactamRing` (domain=API, range=xsd:boolean), FR-005."""
    if db.query(OntologyDataProperty).filter_by(slpra_iri=HAS_BETA_LACTAM_RING_IRI).first():
        return 0
    api = db.query(OntologyClass).filter_by(slpra_iri=API_CLASS_IRI).first()
    if api is None:
        # API class not seeded yet (project_from_ttl must run first); retry next
        # startup rather than insert a dangling-domain row.
        logger.warning(
            "seed_declarative_rules: %s not found — deferring hasBetaLactamRing seed",
            API_CLASS_IRI,
        )
        return 0
    db.add(
        OntologyDataProperty(
            slpra_iri=HAS_BETA_LACTAM_RING_IRI,
            label="has beta-lactam ring",
            comment="含β-内酰胺环：API 是否含 β-内酰胺环结构（R-DC4 判据所读）",
            domain_class_id=api.id,
            datatype="boolean",
            status=STATUS_PUBLISHED,
        )
    )
    return 1


def _seed_classification_criteria(db: Session) -> int:
    """T014 — E11 R-DC1~4 `defined` criteria from the single-source defaults."""
    seeded = 0
    for crit in default_classification_criteria():
        if db.query(OntologyClassificationCriterion).filter_by(
            criterion_key=crit.key
        ).first():
            continue
        target_iri = DRUG_NS + crit.target_class
        target = db.query(OntologyClass).filter_by(slpra_iri=target_iri).first()
        if target is None:
            logger.warning(
                "seed_declarative_rules: target class %s not found — deferring %s",
                target_iri, crit.key,
            )
            continue
        db.add(
            OntologyClassificationCriterion(
                criterion_key=crit.key,
                target_class_id=target.id,
                logic_role=crit.logic_role,
                pattern=crit.pattern,
                regulation_ref=crit.regulation_ref,
                status=STATUS_PUBLISHED,
            )
        )
        seeded += 1
    return seeded


def _seed_decision_rules(db: Session) -> int:
    """T031 — E12 R-ED1~6 / R-SC1~8 / R-CP1~4 production rules from defaults.

    Keyed by `rule_key`; antecedents reference class local-names as *strings*
    (not FKs), so seeding is independent of class-table readiness. Each row is
    projected to `slpra:DecisionRule_<rule_key>` by the surgical merge (T032,
    never hand-edited). FR-016: the runtime engine and the editable T-Box
    metadata stay single-sourced from `defaults.DEFAULT_DECISION_RULES`."""
    seeded = 0
    for rule in default_decision_rules():
        if db.query(OntologyDecisionRule).filter_by(rule_key=rule.key).first():
            continue
        db.add(
            OntologyDecisionRule(
                slpra_iri=DECISION_RULE_PREFIX + rule.key,
                label=rule.key,
                comment=rule.description,
                rule_key=rule.key,
                rule_group=rule.rule_group,
                antecedent=rule.antecedent,
                consequent=rule.consequent,
                priority=rule.priority,
                regulation_ref=rule.regulation_ref,
                status=STATUS_PUBLISHED,
            )
        )
        seeded += 1
    return seeded


def _seed_conflict_policies(db: Session) -> int:
    """T031 — E13 `dedication` / `risk_level` conflict policies from defaults.

    Keyed by `dimension`; projected to `slpra:ConflictPolicy_<dimension>`."""
    seeded = 0
    for pol in default_conflict_policies():
        if db.query(OntologyConflictPolicy).filter_by(dimension=pol.dimension).first():
            continue
        db.add(
            OntologyConflictPolicy(
                slpra_iri=CONFLICT_POLICY_PREFIX + pol.dimension,
                label=pol.dimension,
                comment=pol.description,
                dimension=pol.dimension,
                strategy=pol.strategy,
                priority_lattice=pol.priority_lattice,
                override_direction=pol.override_direction,
                regulation_ref=pol.regulation_ref,
                status=STATUS_PUBLISHED,
            )
        )
        seeded += 1
    return seeded
