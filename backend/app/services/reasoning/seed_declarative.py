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

import hashlib
import json
import logging

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

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
    # 一次性、幂等地把未经编辑的存量 R-RA1~5 升级为模板化默认（不计入 inserted 返回值，
    # 以保持「二次运行返回 0」的幂等契约；但若有升级则须一并 commit）。
    upgraded = _upgrade_risk_assessment_templates(db)
    if inserted or upgraded:
        db.commit()
    logger.info(
        "seed_declarative_rules inserted %d rows, upgraded %d risk templates",
        inserted,
        upgraded,
    )
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
    """T031 — E12 R-ED1~6 / R-SCa~h / R-CP1~4 production rules from defaults.

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


# R-RA1~5 文本模版化升级基线（一次性、幂等的存量同步）。
#
# 规则 seeding 是**仅插入不覆盖**（``_seed_decision_rules``：``rule_key`` 已存在即跳过），
# 所以把 ``defaults.py`` 的 R-RA1~5 consequent 改成含占位符的模板文本，**不会**更新已存在的
# 数据库行。此处对每条 R-RA*：仅当当前行 consequent 与「上线前的旧默认值」**逐字节一致**
# （即从未被人工编辑）时，就地替换为新默认模板；已被编辑过的行进入日志并**跳过**，绝不覆盖。
#
# 下面冻结的是模版化**之前**的 R-RA1~5 consequent 快照——改动 defaults.py 时**不要**同步改这里，
# 它是历史基线，用于识别「纯净未改」的存量行。幂等：升级后当前值=新默认（≠旧基线）→ 二次运行跳过；
# 新库首次 insert 直接是新默认（≠旧基线）→ 无需升级。
_LEGACY_RISK_ASSESSMENT_CONSEQUENTS: dict[str, dict] = {
    "R-RA1": {
        "risk_level": "MediumRisk",
        "category": "人员",
        "description": "共线生产涉及多品种操作人员交叉，存在人为差错和交叉污染风险",
        "control_measure": "1、岗位培训与考核合格后上岗；2、严格执行SOP和批记录；3、清场确认制度",
        "traceability_docs": "1、培训记录；2、批生产记录；3、清场记录",
        "postconditions": {"training_completed": True, "sop_verified": True},
    },
    "R-RA2": {
        "risk_level": "HighRisk",
        "category": "生产设备",
        "description": "共线生产使用的设备需评估交叉污染和清洁验证有效性",
        "control_measure": "1、设备按照验证规程进行确认；2、清洁验证覆盖最难清洁产品；3、共线评估确认设备适用性",
        "traceability_docs": "1、设备确认报告；2、清洁验证报告；3、共线评估报告",
        "postconditions": {
            "equipment_qualified": True,
            "cleaning_validated": True,
            "shared_line_assessed": True,
        },
    },
    "R-RA3": {
        "risk_level": "HighRisk",
        "category": "物料管理",
        "description": "共线生产涉及多品种物料管理，存在混淆和交叉污染风险",
        "control_measure": "1、物料分区存放、标识管理；2、称量复核制度；3、物料平衡检查",
        "traceability_docs": "1、物料台账；2、称量记录；3、物料平衡记录",
        "postconditions": {"material_segregation": True, "weighing_verified": True},
    },
    "R-RA4": {
        "risk_level": "MediumRisk",
        "category": "文件",
        "description": "共线生产需要完善的文件体系支持品种切换和清场管理",
        "control_measure": "1、批记录完整记录生产过程；2、清场SOP和记录；3、偏差和变更控制",
        "traceability_docs": "1、批生产记录；2、清场记录；3、偏差/变更记录",
        "postconditions": {"documentation_complete": True},
    },
    "R-RA5": {
        "risk_level": "LowRisk",
        "category": "三废处理",
        "description": "原料药生产废弃物按照环保要求分类处理，非高活性/高毒性品种常规三废处理即可",
        "control_measure": "1、废弃物分类收集处理；2、废水/废气排放监测；3、按环评要求执行",
        "traceability_docs": "1、废弃物处理记录；2、环境监测报告",
    },
}


def _canonical_consequent_hash(consequent: dict | None) -> str:
    """稳定哈希：键排序 + 保留非 ASCII，用于判定 consequent 是否与某已知基线逐字节一致。"""
    return hashlib.sha256(
        json.dumps(consequent or {}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _upgrade_risk_assessment_templates(db: Session) -> int:
    """一次性、幂等地把**未经编辑**的存量 R-RA1~5 行升级为新的模板化默认 consequent。

    仅当当前行 consequent 与 ``_LEGACY_RISK_ASSESSMENT_CONSEQUENTS`` 冻结的旧默认逐字节一致时
    才替换；人工改过的行跳过并 log。返回被升级的行数（不计入 seeding 的 ``inserted``）。"""
    new_by_key = {
        rule.key: rule.consequent
        for rule in default_decision_rules()
        if rule.key in _LEGACY_RISK_ASSESSMENT_CONSEQUENTS
    }
    legacy_hashes = {
        key: _canonical_consequent_hash(consequent)
        for key, consequent in _LEGACY_RISK_ASSESSMENT_CONSEQUENTS.items()
    }

    upgraded = 0
    for key, new_consequent in new_by_key.items():
        row = db.query(OntologyDecisionRule).filter_by(rule_key=key).first()
        if row is None:
            continue  # 尚未 seed（新库首插会直接落新默认）
        current_hash = _canonical_consequent_hash(row.consequent)
        if current_hash == _canonical_consequent_hash(new_consequent):
            continue  # 已是新默认 → 幂等跳过
        if current_hash != legacy_hashes[key]:
            logger.info(
                "risk-template upgrade: %s consequent 已被人工编辑，跳过（保留现值）", key
            )
            continue
        row.consequent = new_consequent
        flag_modified(row, "consequent")  # JSON 列原地替换需显式标脏
        upgraded += 1
        logger.info("risk-template upgrade: %s consequent 已升级为模板化默认", key)
    return upgraded


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
