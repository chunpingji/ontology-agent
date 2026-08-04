"""Canonical declarative rule artifacts (single source of truth).

These in-code defaults are the authoritative seed for the editable T-Box
metadata tables (E11/E12/E13) and the fallback the engine consumes when no
published artifacts override them. The legacy `rules/*.py` functions are being
demoted to this declarative data (tasks T043); each entry mirrors a row in
data-model.md §D so the migration seed and the runtime engine stay isomorphic.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ClassificationCriterion:
    """E11 `defined` criterion: underlying-property condition → risk class."""

    key: str
    target_class: str  # slpra-drug local name, e.g. "CytotoxicDrug"
    pattern: dict
    regulation_ref: str
    description: str
    logic_role: str = "defined"


# --- Byte-verified external alignment targets (research.md R3, T021) ---------
# ChEBI purls verified 2026-06-24 against EBI OLS4. These are the *only* external
# alignment IRIs an `external_alignment` criterion may reference at release time;
# the consistency gate (T026) blocks any criterion whose alignment is absent here
# (FR-014, 宪章 II — unverified terms must never reach the authoritative TTL).
CHEBI_ANTINEOPLASTIC = "http://purl.obolibrary.org/obo/CHEBI_35610"  # "antineoplastic agent"
CHEBI_HORMONE = "http://purl.obolibrary.org/obo/CHEBI_24621"  # "hormone"
CHEBI_PENICILLIN = "http://purl.obolibrary.org/obo/CHEBI_17334"  # "penicillin"

VERIFIED_EXTERNAL_ALIGNMENTS: frozenset[str] = frozenset(
    {CHEBI_ANTINEOPLASTIC, CHEBI_HORMONE, CHEBI_PENICILLIN}
)


# --- R-DC1~4 — Tier-1 defined classes (data-model.md §D1) -------------------
DEFAULT_CLASSIFICATION_CRITERIA: list[ClassificationCriterion] = [
    ClassificationCriterion(
        key="R-DC1",
        target_class="CytotoxicDrug",
        pattern={
            "op": "some_values_from",
            "property": "hasToxicityProfile",
            "filler_class": "GenotoxicityProfile",
        },
        regulation_ref="CFDI 2023-03 §3.2",
        description="Genotoxicity profile detected → CytotoxicDrug classification",
    ),
    ClassificationCriterion(
        key="R-DC2",
        target_class="HighActivityDrug",
        pattern={
            "op": "class_membership",
            "property": "hasOEBClassification",
            "classes": ["OEB4", "OEB5"],
        },
        regulation_ref="CFDI 2023-03 §3.3",
        description="OEB4 or OEB5 classification → HighActivityDrug",
    ),
    ClassificationCriterion(
        key="R-DC3",
        target_class="HighSensitizingDrug",
        pattern={
            "op": "datatype_facet",
            "property": "sensitizationLevel",
            "cmp": "gt",
            "value": 3,
        },
        regulation_ref="CFDI 2023-03 §3.4",
        description="Sensitization level > 3 → HighSensitizingDrug",
    ),
    ClassificationCriterion(
        key="R-DC4",
        target_class="BetaLactamDrug",
        pattern={
            "op": "boolean_has_value",
            "property": "hasBetaLactamRing",
            "value": True,
        },
        regulation_ref="CFDI 2023-03 §4.4",
        description="Beta-lactam ring structure → BetaLactamDrug",
    ),
]


# --- US2 — close the assertable-only / inexpressible gap (data-model.md §D1) --
# Hormonal & penicillin upgrade from assertable-only to inferable; antineoplastic
# is a brand-new inferable class. All three fire when the drug's API individual
# aligns (via `hasActiveIngredient`) to a byte-verified ChEBI class (T021/R3).
US2_CLASSIFICATION_CRITERIA: list[ClassificationCriterion] = [
    ClassificationCriterion(
        key="HormonalDrug-suff",
        target_class="HormonalDrug",
        pattern={
            "op": "external_alignment",
            "property": "hasActiveIngredient",
            "alignment": CHEBI_HORMONE,
        },
        regulation_ref="CFDI 2023-03 §4.3；对齐 ChEBI:24621 (hormone)",
        description="API aligns to ChEBI hormone → HormonalDrug (inferable upgrade)",
    ),
    ClassificationCriterion(
        key="PenicillinDrug-suff",
        target_class="PenicillinDrug",
        pattern={
            "op": "external_alignment",
            "property": "hasActiveIngredient",
            "alignment": CHEBI_PENICILLIN,
        },
        regulation_ref="CFDI 2023-03 §4.4；对齐 ChEBI:17334 (penicillin)",
        description="API aligns to ChEBI penicillin → PenicillinDrug (inferable upgrade)",
    ),
    ClassificationCriterion(
        key="AntineoplasticDrug-suff",
        target_class="AntineoplasticDrug",
        pattern={
            "op": "external_alignment",
            "property": "hasActiveIngredient",
            "alignment": CHEBI_ANTINEOPLASTIC,
        },
        regulation_ref="ATC L01 / ChEBI:35610 (antineoplastic agent)",
        description="API aligns to ChEBI:35610 antineoplastic agent → AntineoplasticDrug (new class)",
    ),
]


def default_classification_criteria() -> list[ClassificationCriterion]:
    """Active classification criteria: R-DC1~4 (US1) + the three US2 gap-closers."""
    return list(DEFAULT_CLASSIFICATION_CRITERIA) + list(US2_CLASSIFICATION_CRITERIA)


# =========================================================================== #
# E12 — Production decision rules (R-ED / R-SC / R-CP), data-model.md §D2
# =========================================================================== #
@dataclass(frozen=True)
class DecisionRule:
    """E12 production rule: an antecedent pattern (interpreter AST over `Facts`)
    whose `TRUE` evaluation fires the verbatim `consequent` conclusion.

    `consequent` mirrors the legacy `RuleResult.conclusion` byte-for-byte so the
    external `AssessmentResult` shape is preserved (golden-master parity, FR-012).
    The antecedents reference a small, fixed fact vocabulary the engine supplies:

      relations  : hasInactivationProfile, hasOEBClassification, coProduct
      scalars    : hasPrionRisk, isShared, pathway, pde, cleanability,
                   dosageForm, areaType, formRelation
                   (`formRelation` ∈ {"same","different"} is asserted by the
                   engine only when *both* source/target dosage forms are known —
                   absent ⇒ UNKNOWN ⇒ R-CP4 stays unfired, exactly as today.)
    """

    key: str  # rule_key, e.g. "R-ED1"
    rule_group: str  # one of models.RULE_GROUPS
    antecedent: dict  # interpreter pattern AST (restricted vocabulary)
    consequent: dict  # conclusion dict (mirrors RuleResult.conclusion)
    regulation_ref: str
    description: str
    priority: int = 100


# --- R-ED1~6 — Equipment dedication (CFDI §4) -------------------------------
_EQUIPMENT_DEDICATION_RULES: list[DecisionRule] = [
    DecisionRule(
        key="R-ED1",
        rule_group="equipment_dedication",
        antecedent={"op": "class_present", "class": "PenicillinDrug"},
        consequent={"requires_dedication": True, "unconditional": True},
        regulation_ref="CFDI 2023-03 §4.4: 必须专用独立厂房、设施和设备",
        description="Penicillin drug → mandatory equipment dedication",
    ),
    DecisionRule(
        key="R-ED2",
        rule_group="equipment_dedication",
        antecedent={
            "op": "and",
            "operands": [
                {"op": "class_present", "class": "CytotoxicDrug"},
                {
                    "op": "class_membership",
                    "property": "hasInactivationProfile",
                    "classes": ["NonInactivatable"],
                },
            ],
        },
        consequent={"requires_dedication": True},
        regulation_ref="CFDI 2023-03 §4.2",
        description="Cytotoxic drug with no inactivation method → equipment dedication required",
    ),
    DecisionRule(
        key="R-ED3",
        rule_group="equipment_dedication",
        antecedent={
            "op": "and",
            "operands": [
                {"op": "class_present", "class": "BiologicalProduct"},
                {"op": "literal_eq", "key": "hasPrionRisk", "value": True},
            ],
        },
        consequent={"requires_dedication": True},
        regulation_ref="CFDI 2023-03 §4.5",
        description="Biological product with prion risk → equipment dedication required",
    ),
    DecisionRule(
        key="R-ED4",
        rule_group="equipment_dedication",
        antecedent={
            "op": "and",
            "operands": [
                {"op": "class_present", "class": "CytotoxicDrug"},
                {
                    "op": "class_membership",
                    "property": "hasInactivationProfile",
                    "classes": ["HeatInactivatable", "ChemicalInactivatable"],
                },
            ],
        },
        consequent={"requires_dedication": False, "requires_inactivation_validation": True},
        regulation_ref="CFDI 2023-03 §4.2 (conditional)",
        description="Cytotoxic drug with validated inactivation → shared line allowed with conditions",
    ),
    DecisionRule(
        key="R-ED5",
        rule_group="equipment_dedication",
        antecedent={
            "op": "and",
            "operands": [
                {"op": "class_present", "class": "HighActivityDrug"},
                {
                    "op": "class_membership",
                    "property": "hasOEBClassification",
                    "classes": ["OEB5"],
                },
            ],
        },
        consequent={"requires_dedication": True},
        regulation_ref="CFDI 2023-03 §4.6",
        description="OEB5 classification (highest potency) → equipment dedication required",
    ),
    DecisionRule(
        key="R-ED6",
        rule_group="equipment_dedication",
        antecedent={"op": "class_present", "class": "HormonalDrug"},
        consequent={"requires_independent_hvac": True},
        regulation_ref="CFDI 2023-03 §4.3",
        description="Hormonal drug → independent air handling system required",
    ),
]


# --- R-SCa~h — Scenario identification (CFDI 《药品共线生产质量风险管理指南》) ----
_SHARED = {"op": "literal_eq", "key": "isShared", "value": True}
_SCENARIO_RULES: list[DecisionRule] = [
    DecisionRule(
        key="R-SCa",
        rule_group="scenario_identification",
        antecedent={
            "op": "and",
            "operands": [
                {"op": "class_present", "class": "ClinicalTrialDrug"},
                {"op": "class_membership", "property": "coProduct", "classes": ["CommercialDrug"]},
            ],
        },
        consequent={
            "scenario": "ClinicalWithCommercialScenario",
            "requires_enhanced_documentation": True,
        },
        regulation_ref="CFDI 2023-03 情形(a)",
        description="临床试验用药品与商业化药品共线生产 → 情形(a)",
    ),
    DecisionRule(
        key="R-SCb",
        rule_group="scenario_identification",
        antecedent={
            "op": "and",
            "operands": [{"op": "class_present", "class": "TraditionalChineseMedicine"}, _SHARED],
        },
        consequent={"scenario": "TCMSharedLineScenario"},
        regulation_ref="CFDI 2023-03 情形(b)",
        description="中药产品共线生产 → 情形(b)",
    ),
    DecisionRule(
        key="R-SCc",
        rule_group="scenario_identification",
        antecedent={
            "op": "and",
            "operands": [{"op": "class_present", "class": "BiologicalProduct"}, _SHARED],
        },
        consequent={"scenario": "BiologicSharedLineScenario", "requires_tse_assessment": True},
        regulation_ref="CFDI 2023-03 情形(c)",
        description="生物制品共线生产 → 情形(c)",
    ),
    DecisionRule(
        key="R-SCd",
        rule_group="scenario_identification",
        antecedent={
            "op": "and",
            "operands": [{"op": "class_present", "class": "SterileDrugProduct"}, _SHARED],
        },
        consequent={"scenario": "TerminalVsNonTerminalSterilizationScenario", "requires_aseptic_integrity": True},
        regulation_ref="CFDI 2023-03 情形(d)",
        description="最终灭菌产品和非最终灭菌产品共线生产 → 情形(d)",
    ),
    DecisionRule(
        key="R-SCe",
        rule_group="scenario_identification",
        antecedent={
            "op": "and",
            "operands": [
                {
                    "op": "or",
                    "operands": [
                        {"op": "class_present", "class": "HormonalDrug"},
                        {"op": "class_present", "class": "CytotoxicDrug"},
                        {"op": "class_present", "class": "HighActivityDrug"},
                    ],
                },
                _SHARED,
            ],
        },
        consequent={
            "scenario": "HormonalCytotoxicHighPotencyScenario",
            "requires_independent_hvac": True,
        },
        regulation_ref="CFDI 2023-03 情形(e)",
        description="某些激素类、细胞毒性类、高活性化学药品共线生产 → 情形(e)",
    ),
    DecisionRule(
        key="R-SCf",
        rule_group="scenario_identification",
        antecedent={
            "op": "and",
            "operands": [{"op": "class_present", "class": "CellTherapyProduct"}, _SHARED],
        },
        consequent={"scenario": "CellTherapySharedLineScenario"},
        regulation_ref="CFDI 2023-03 情形(f)",
        description="细胞治疗产品共线生产 → 情形(f)",
    ),
    DecisionRule(
        key="R-SCg",
        rule_group="scenario_identification",
        antecedent={
            "op": "and",
            "operands": [
                {
                    "op": "or",
                    "operands": [
                        {"op": "class_present", "class": "NarcoticDrug"},
                        {"op": "class_present", "class": "PsychotropicDrug"},
                        {"op": "class_present", "class": "PrecursorChemical"},
                    ],
                },
                _SHARED,
            ],
        },
        consequent={"scenario": "NarcoticPsychotropicPrecursorScenario"},
        regulation_ref="CFDI 2023-03 情形(g)",
        description="麻醉药品、精神药品和药品类易制毒化学品共线生产 → 情形(g)",
    ),
    DecisionRule(
        key="R-SCh",
        rule_group="scenario_identification",
        antecedent={"op": "class_present", "class": "PenicillinDrug"},
        consequent={"scenario": "PenicillinBetaLactamScenario", "requires_dedication": True},
        regulation_ref="CFDI 2023-03 情形(h)",
        description="青霉素类及β-内酰胺结构类等产品共线生产 → 情形(h) (mandatory dedication)",
    ),
]


# --- R-CP1~4 — Contamination pathway risk scoring (CFDI §5) -----------------
_CONTAMINATION_RULES: list[DecisionRule] = [
    DecisionRule(
        key="R-CP1",
        rule_group="contamination_risk",
        antecedent={
            "op": "and",
            "operands": [
                {"op": "literal_eq", "key": "pathway", "value": "residue"},
                {"op": "literal_cmp", "key": "pde", "cmp": "lt", "value": 0.01},
                {"op": "literal_cmp", "key": "cleanability", "cmp": "lt", "value": 3},
            ],
        },
        consequent={"risk_level": "HighRisk"},
        regulation_ref="CFDI 2023-03 §5.1",
        description="Residue pathway with low PDE and poor cleanability → High Risk",
    ),
    DecisionRule(
        key="R-CP2",
        rule_group="contamination_risk",
        antecedent={
            "op": "and",
            "operands": [
                {"op": "literal_eq", "key": "pathway", "value": "airborne"},
                {"op": "literal_eq", "key": "dosageForm", "value": "powder"},
                {"op": "literal_eq", "key": "areaType", "value": "general"},
            ],
        },
        consequent={"risk_level": "HighRisk"},
        regulation_ref="CFDI 2023-03 §5.3",
        description="Powder operation in general area with airborne transmission → High Risk",
    ),
    DecisionRule(
        key="R-CP3",
        rule_group="contamination_risk",
        antecedent={
            "op": "and",
            "operands": [
                {"op": "literal_eq", "key": "pathway", "value": "residue"},
                {"op": "literal_eq", "key": "dosageForm", "value": "solution"},
                {"op": "literal_cmp", "key": "cleanability", "cmp": "gt", "value": 4},
            ],
        },
        consequent={"risk_level": "LowRisk"},
        regulation_ref="CFDI 2023-03 §5.1",
        description="Liquid product with excellent cleanability → Low Risk",
    ),
    DecisionRule(
        key="R-CP4",
        rule_group="contamination_risk",
        antecedent={
            "op": "and",
            "operands": [
                {"op": "literal_eq", "key": "pathway", "value": "confusion"},
                {"op": "literal_eq", "key": "formRelation", "value": "same"},
            ],
        },
        consequent={"risk_level": "MediumRisk"},
        regulation_ref="CFDI 2023-03 §5.4",
        description="Same dosage form parallel production → confusion Medium Risk",
    ),
]

DEFAULT_DECISION_RULES: list[DecisionRule] = (
    _EQUIPMENT_DEDICATION_RULES + _SCENARIO_RULES + _CONTAMINATION_RULES
)


# --- R-RA1~5 — Risk assessment (QS-A-020F05 HazID dimensions) ----------------
# consequent 的三列文本（description / control_measure / traceability_docs）支持
# ``{{占位符}}`` 模版化：报告生成时由 ``RiskReportGenerator._build_template_context`` 从抽取
# 事实**确定性**填充（无 LLM，FR-009）。白名单占位符仅两个：``{{药物名称/代号}}``、``{{车间}}``；
# 未解析（或非白名单）的占位符渲染为 ``【待补充：…】`` 哨兵。改动这些默认文本后，
# 存量数据库经 ``seed_declarative._upgrade_risk_assessment_templates`` 幂等同步（仅更新未经人工编辑的行）。
_RISK_ASSESSMENT_RULES: list[DecisionRule] = [
    DecisionRule(
        key="R-RA1",
        rule_group="risk_assessment",
        antecedent={
            "op": "some_values_from",
            "property": "hasSharedLineData",
            "filler_class": "SharedLineAssessmentData",
        },
        consequent={
            "risk_level": "MediumRisk",
            "category": "人员",
            "description": "研究院人员对设备操作、车间管理等了解不足，无法进行{{药物名称/代号}}临床批生产操作；车间岗位人员对{{药物名称/代号}}临床批工艺了解不足，无法进行{{药物名称/代号}}临床批生产操作。",
            "control_measure": "1、{{药物名称/代号}}由研究院人员和车间岗位人员共同进行临床批生产操作，生产前车间对研究院人员进行车间管理、设备操作等培训；研究院对车间岗位人员进行{{药物名称/代号}}临床批工艺培训，培训合格后，车间岗位人员和研究院人员再进行{{药物名称/代号}}的临床批生产操作。",
            "traceability_docs": "1、研究院及车间岗位人员培训及考核记录。",
            "postconditions": {"training_completed": True, "sop_verified": True},
        },
        regulation_ref="GMP 2010 附录1 §14",
        description="共线生产人员风险评估",
    ),
    DecisionRule(
        key="R-RA2",
        rule_group="risk_assessment",
        antecedent={
            "op": "some_values_from",
            "property": "hasSharedLineData",
            "filler_class": "SharedLineAssessmentData",
        },
        consequent={
            "risk_level": "HighRisk",
            "category": "生产设备",
            "description": "1、{{车间}}生产使用的设备、仪器仪表无法满足{{药物名称/代号}}工艺需求。\n2、{{药物名称/代号}}与{{车间}}其他产品共线生产，共用生产设备，所用设备若清洁不彻底，容易产生污染、交叉污染。",
            "control_measure": "1、生产使用的设备均按照生产工艺进行了选型、安装及确认，设备、仪器、仪表使用前均在确认有效期内，能够满足{{药物名称/代号}}工艺需求。\n2、{{药物名称/代号}}生产前，进行共线风险评估。\n3、{{药物名称/代号}}备样生产结束后进行清洁确认。",
            "traceability_docs": "1、批记录中记录设备及仪表状态检查。\n2、{{车间}}共线产品防止交叉污染风险评估报告。\n3、{{药物名称/代号}}原料药使用设备清洁确认报告。",
            "postconditions": {"equipment_qualified": True, "cleaning_validated": True, "shared_line_assessed": True},
        },
        regulation_ref="GMP 2010 附录1 §32",
        description="共线生产设备风险评估",
    ),
    DecisionRule(
        key="R-RA3",
        rule_group="risk_assessment",
        antecedent={
            "op": "some_values_from",
            "property": "hasSharedLineData",
            "filler_class": "SharedLineAssessmentData",
        },
        consequent={
            "risk_level": "HighRisk",
            "category": "物料管理",
            "description": "原料药分公司无法提供符合{{药物名称/代号}}生产要求的原辅料及包装材料。",
            "control_measure": "1、{{药物名称/代号}}专用物料由连云港研究院仓库提供。\n2、{{药物名称/代号}}公用物料由原料药分公司仓库提供。",
            "traceability_docs": "1、连云港研究院仓库出库记录；\n2、原料药分公司仓库出库记录；\n3、物料检验原始记录。",
            "postconditions": {"material_segregation": True, "weighing_verified": True},
        },
        regulation_ref="GMP 2010 §46-48",
        description="共线生产物料管理风险评估",
    ),
    DecisionRule(
        key="R-RA4",
        rule_group="risk_assessment",
        antecedent={
            "op": "some_values_from",
            "property": "hasSharedLineData",
            "filler_class": "SharedLineAssessmentData",
        },
        consequent={
            "risk_level": "MediumRisk",
            "category": "文件",
            "description": "现有文件无法指导{{药物名称/代号}}的生产。",
            "control_measure": "1、{{车间}}已建立文件体系，相关设备均有操作SOP。\n2、生产过程由研究院提供{{药物名称/代号}}生产批记录，能够保证生产顺利进行。",
            "traceability_docs": "1、车间设备使用SOP。\n2、{{药物名称/代号}}批生产记录。",
            "postconditions": {"documentation_complete": True},
        },
        regulation_ref="GMP 2010 §151-156",
        description="共线生产文件管理风险评估",
    ),
    DecisionRule(
        key="R-RA5",
        rule_group="risk_assessment",
        antecedent={
            "op": "some_values_from",
            "property": "describes",
            "filler_class": "DrugProduct",
        },
        consequent={
            "risk_level": "LowRisk",
            "category": "三废处理",
            "description": "无法有效进行{{药物名称/代号}}产生的三废处理。",
            "control_measure": "1、车间生产过程中产生的废气经过车间废气处理装置达到合格排放标准后排放。\n2、废液集中收集后转至环保站处理。\n3、固废收集在塑料袋中扎好口，集中收集后送至环保站统一处理。",
            "traceability_docs": "1、危险废物产生接收处理记录。",
        },
        regulation_ref="GMP 2010 §58",
        description="三废处理风险评估",
    ),
]

DEFAULT_RISK_ASSESSMENT_RULES: list[DecisionRule] = _RISK_ASSESSMENT_RULES


def default_decision_rules() -> list[DecisionRule]:
    """Active production rules: R-ED1~6 + R-SCa~h + R-CP1~4 + R-RA1~5 (engine fallback / seed)."""
    return list(DEFAULT_DECISION_RULES) + list(DEFAULT_RISK_ASSESSMENT_RULES)


# =========================================================================== #
# E13 — Conflict-resolution policies, data-model.md §D3
# =========================================================================== #
@dataclass(frozen=True)
class ConflictPolicy:
    """E13 strategy that aggregates contradictory rule conclusions along one
    `dimension`. Externalises the legacy `conflict_resolver` `if` branches as
    data: `priority_lattice` orders risk severities; `override_direction`
    selects which boolean conclusion wins under `safety_override`."""

    dimension: str  # "dedication" | "risk_level"
    strategy: str  # "safety_override" | "max_severity"
    regulation_ref: str
    description: str
    priority_lattice: dict | None = None
    override_direction: str | None = None


RISK_PRIORITY_LATTICE: dict[str, int] = {"HighRisk": 3, "MediumRisk": 2, "LowRisk": 1}

DEFAULT_CONFLICT_POLICIES: list[ConflictPolicy] = [
    ConflictPolicy(
        dimension="dedication",
        strategy="safety_override",
        override_direction="restrictive_wins",
        regulation_ref="CFDI 2023-03 §13.4: 安全优先元规则",
        description="Any rule requiring dedication wins (most-restrictive conclusion prevails)",
    ),
    ConflictPolicy(
        dimension="risk_level",
        strategy="max_severity",
        priority_lattice=dict(RISK_PRIORITY_LATTICE),
        regulation_ref="CFDI 2023-03 §5: 取最高污染风险等级",
        description="Highest risk level on the severity lattice prevails (default LowRisk)",
    ),
]


def default_conflict_policies() -> list[ConflictPolicy]:
    """Active conflict policies: dedication (safety_override) + risk_level (max_severity)."""
    return list(DEFAULT_CONFLICT_POLICIES)
