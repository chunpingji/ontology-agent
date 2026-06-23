# 维度评估器扩展设计 —— 把"设备维度 AI 预评估"复制到全维度（人员/物料/文件/三废）

> 版本：0.1 | 日期：2026-06-22
>
> 范式来源：现有结论中心流水线（`ReasoningExecution` + `ActionExecution` + `ElectronicSignature` + 全局哈希链）
>
> 关联文档：[`workflow-statemachine-design.md`](./workflow-statemachine-design.md)（结论流水线 as-built 与 G1/G2/G3）、[`gap-analysis.md`](./gap-analysis.md)
>
> 上游场景：试验原料药临床备样生产信息**全维度评审**（人员 / 生产设备 / 物料管理 / 文件 / 三废处理），各责任人分别签字
>
> 状态图例：✅ 已实现 · 🟡 部分/未接线 · ❌ 缺口 · 🆕 本设计新增

---

## 0. 一句话结论

`run_assessment`（`engine.py:34`）名为通用，实为**设备/交叉污染维度的专用评估器**：入参只认药品+设备，函数体是固定的四个规则组。把同等"AI 预评估"能力扩展到其余维度，**不是重写引擎**，而是：

1. 抽出**统一评估器契约**（`DimensionResult` + `DimensionAssessor` 协议 + 注册表），把 `run_assessment` 适配为众多评估器之一；
2. 按**三层配方**（知识层 / 事实层 / 规则层）为每个维度新建一个 `rules/<dim>.py` 规则包 + 一个 `assess_<dim>()` 评估器，内部循环**照抄** `run_assessment`；
3. 在统一的 `persist_conclusion()` 落库点**顺手收口 G1（结论无初始落库）与 G2（强制签名闸门未 arm）**；
4. 由 `ReviewCase` 对在范围维度 fan-out，每维度生成一条 `ReasoningExecution` 结论 + 一个**按责任角色门禁**的签名槽，闭合"各责任人分别签字"的会签矩阵。

**成本定调**：机制是规则化的、近零成本复制（`RuleResult` 原语已能承载全部维度）；真正的成本在①各维度的**规则内容**（需 SME + 法规映射）、②**事实可得性**（设备维度天生有富本体，其余维度多需新建 A-Box 或靠能力二抽取）。

---

## 1. 背景与目标

### 1.1 要解决的问题

设备维度已具备真正的 AI 预评估（规则组 `drug_classification` / `equipment_dedication` / `contamination_risk` / `scenario_identification` + MACO/PDE 计算器）。其余四个维度（人员、物料、文件、三废）**无任何评估内容**——这是评审工作流的最大短板（即 workflow 设计中标记的 E5）。本设计给出把该能力规模化复制到全维度的**正式工程方案**。

### 1.2 范围

| 范围内（本文规范） | 范围外（交由关联文档/后续） |
|---|---|
| 统一评估器契约（`DimensionResult` / 协议 / 注册表） | `ReviewCase` 完整生命周期状态机（→ 评审工作流专文） |
| 每维度规则包 + 评估器的三层配方与骨架 | G3（事实事件→重算自动订阅，见 workflow §4） |
| 五维度可编码性分档 + 两种规则风味 | 具体规则的法规逐条落实（按维度任务单分批交付） |
| 与 `ReviewCase`/会签矩阵的**对接面**（fan-out、签名槽、合并结论） | 前端面板实现 |
| G1/G2 收口点、DDL、接口契约、分维度任务拆解 | 路线 B（迁移定义数据化进本体，见 workflow §6） |

### 1.3 设计原则

- **复用优先**：不动正在工作的 `run_assessment` 与规则内核；新增以"加文件 + 加注册项"为主。
- **加法式变更**：DB 仅新增可空列/新表，不改既有列语义。
- **可追溯**：每条规则携带 `regulation_ref`；每条结论入全局哈希链。
- **诚实分档**：明确哪些维度今天即可做"真 AI"，哪些先以"符合性清单"起步、再渐进自动化。

---

## 2. 现状基线（as-built）

| 事实 | 代码证据 | 对扩展的含义 |
|---|---|---|
| `run_assessment(engine, drug_iri, equipment_iris)` 是设备专属评估器，四个规则组顺序编排 | `engine.py:34` | 它就是"维度评估器"的样板，需被适配进注册表 |
| 规则原语 `RuleResult(rule_id, fired, description, inputs, conclusion, regulation_ref)` 在三个规则包中**逐字相同** | `rules/{drug_classification,equipment_dedication,contamination_risk}.py` | 直接复用；建议上移到 `rules/base.py` 单一定义 |
| 规则按 `ALL_RULES = [...]` 列表分组，引擎遍历收集 `fired` 项 | `engine.py:57/74/93/128` | 新维度照搬此惯用法 |
| `ReasoningExecution(...)` **仅**在增量重算中实例化 | `incremental.py:74`（G1） | 没有首条结论 → 流水线无法自举；扩展时必须补落库点 |
| `requires_signature` 默认 `False`，**无人据风险置 True** | `models/reasoning.py:36`（G2） | 高风险维度结论不会自动进入待签；扩展时必须 arm |
| 电子签名 `signer_role` 为 `String(50)` 自由文本 | `models/reasoning.py:96` | **签名表已能存任意责任角色**（EHS/生产负责人…），无需改表即可多方会签 |
| 签名端点门禁**硬编码** `_qa = require_role(ROLE_QA)` | `compliance.py:25` | 多责任人会签需把静态 QA 门禁改为**按维度动态推导角色** |
| `VALID_ROLES` 仅 `(senior_analyst, operator, qa)` | `dependencies.py:21` | 责任角色词表需扩充（见 §9.4），否则网关拒绝 EHS/生产负责人等身份 |
| `/rules` 用 `(group_name, ALL_RULES)` 列表注册展示 | `reasoning.py:129` | 新维度规则包追加进该列表即可被溯源/展示 |

---

## 3. 设计总览

```
                         ┌─────────────────────── ReviewCase（备样生产信息评审案，🆕）
                         │  dimensions_in_scope = [equipment, waste_treatment, material, personnel, documentation]
                         │  lifecycle: draft→dimension_review→consolidation→approved→released
                         ▼
        ┌──────────── fan-out（按在范围维度逐一调用注册表）────────────┐
        ▼                ▼                ▼              ▼              ▼
  assess_equipment  assess_waste   assess_material  assess_personnel  assess_documentation
  (适配 run_assessment) (🆕)          (🆕)            (🆕)              (🆕)
        │                │                │              │              │
        └──────每个评估器返回归一化 DimensionResult（risk_level/rules_fired/findings/requires_signature）──────┘
                         │
                         ▼  persist_conclusion()  ← 收口 G1（落库）+ G2（按维度 arm requires_signature）
        ┌──── ReasoningExecution（每维度一条；dimension=X, review_case_id=case）────┐
        │        effective = not requires_signature                                  │
        ▼                                          ▼                                 ▼
  ElectronicSignature（每维度一签，         ActionExecution（生效后编排，         全局哈希链审计
  signer_role=该维度责任角色，动态门禁）       suppressed→签名后 pending）           （audit.append）
        │
        └──── 全维度签毕 → consolidation 合并结论（QA 签）→ ReviewCase=approved → release
```

核心：**标准化"边界"**（输入上下文 → `DimensionResult` → 落库/签名），**各评估器内部编排自治**（设备是四组流水线、三废是逐废物流循环、文件是清单核对）——不强套"万能循环"，避免过度抽象。

---

## 4. 核心抽象

### 4.1 归一化结果 `DimensionResult`（`AssessmentResult` 的超集）

```python
# backend/app/services/reasoning/dimensions/base.py  🆕
from dataclasses import dataclass, field
from typing import Any, Protocol

DIMENSIONS = ("equipment", "waste_treatment", "material_management",
              "personnel", "documentation")

@dataclass
class DimensionResult:
    dimension: str
    risk_level: str = "LowRisk"
    rules_fired: list[dict] = field(default_factory=list)   # 与现有 result.rules_fired 同构
    findings:    list[dict] = field(default_factory=list)   # 符合性缺陷项（blocking 标记）
    requires_signature: bool = False
    recommendations: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)   # 设备维度放 {maco_value, requires_dedication}
    inputs:  dict[str, Any] = field(default_factory=dict)   # 落 input_params 用（drug_iri / *_iris …）

class DimensionAssessor(Protocol):
    dimension: str
    def assess(self, engine, ctx: dict) -> DimensionResult: ...
```

### 4.2 注册表

```python
# backend/app/services/reasoning/dimensions/__init__.py  🆕
DIMENSION_ASSESSORS: dict[str, DimensionAssessor] = {
    "equipment":           EquipmentAssessor(),     # run_assessment 的薄适配层，规则零改动
    "waste_treatment":     WasteAssessor(),
    "material_management": MaterialAssessor(),
    "personnel":           PersonnelAssessor(),
    "documentation":       DocumentationAssessor(),
}
```

### 4.3 统一落库与签名布防 `persist_conclusion()`（G1 + G2 收口点）

```python
# backend/app/services/reasoning/dimensions/persist.py  🆕
SIGNOFF_ROLE = {                       # 各维度责任签署角色（会签矩阵）
    "equipment":           "equipment_engineer",
    "waste_treatment":     "ehs_officer",
    "material_management": "material_manager",
    "personnel":           "production_head",
    "documentation":       "qa",
    "__consolidation__":   "qa",
}

def arm_signature(res: DimensionResult) -> bool:                       # —— G2 ——
    if res.dimension == "equipment":
        return res.risk_level == "HighRisk" or res.metrics.get("requires_dedication", False)
    return res.risk_level in ("HighRisk", "MediumRisk") or any(f.get("blocking") for f in res.findings)

def persist_conclusion(db, review_case_id, res: DimensionResult) -> ReasoningExecution:
    res.requires_signature = arm_signature(res)
    exe = ReasoningExecution(
        execution_type="dimension_assessment",
        dimension=res.dimension,                       # 🆕 列
        review_case_id=review_case_id,                 # 🆕 列
        input_params={"review_case_id": str(review_case_id), **res.inputs},
        rules_fired=res.rules_fired,
        results={"risk_level": res.risk_level, "findings": res.findings,
                 "recommendations": res.recommendations, "metrics": res.metrics},
        risk_level=res.risk_level,
        maco_value=res.metrics.get("maco_value"),
        requires_signature=res.requires_signature,     # —— G2 ——
        effective=not res.requires_signature,          # 需签者待签后方生效（FR-030）
    )
    db.add(exe); db.flush()                             # —— G1：结论首次落库 ——
    audit.append(db, "reasoning.dimension_assess", actor="system", entity_iri=str(exe.id),
                 details={"dimension": res.dimension, "risk_level": res.risk_level,
                          "requires_signature": res.requires_signature}, commit=False)
    db.commit(); db.refresh(exe)
    return exe
```

> 设备评估器 `EquipmentAssessor.assess` 仅是把现有 `run_assessment` 的 `AssessmentResult` 映射为 `DimensionResult`（`metrics={"maco_value":…, "requires_dedication":…}`），**`engine.py`/规则包一行不改**。

---

## 5. 三层配方（每个新维度照此落地）

| 层 | 产出 | 说明 |
|---|---|---|
| **① 知识层（T-Box + 受控词表）** | 该维度要推理的 Class / DataProperty / 风险词表 | 例：三废 `WasteStream/WasteCategory/TreatmentFacility/DischargeLimit`；人员 `TrainingRecord/Qualification/HealthCheck` |
| **② 事实层（A-Box）** | 待评估实例 | 来源三选一：手工建模 / 集成连接器 / **能力二从备样档案 PDF 抽取**（批记录、清洁验证、MSDS、培训矩阵） |
| **③ 规则层** | `rules/<dim>.py`（`RuleResult`+`ALL_RULES`）+ `dimensions/<dim>.py`（`assess_<dim>`） | 内部循环照抄 `run_assessment`；产出 `DimensionResult` |

**跨维度红利**：`drug_classification` 组产出的 `CytotoxicDrug/BetaLactamDrug/HighActivityDrug` 是**多维度共享输入**——设备判专用化、三废判危废处理、物料判隔离。分类**只算一次**，各评估器消费同一结论，消除重复推理。

---

## 6. 五维度分档 + 两种规则风味

### 6.1 可编码性 × 数据来源分档（诚实排序）

| 维度 | 风险逻辑性质 | 事实来源 | 规则风味 | 现状/成本 | 建议次序 |
|---|---|---|---|---|---|
| **设备** | 定量+分类（MACO/PDE、专用化阈值、污染通路） | A-Box 药品+设备个体 | 推断型 | ✅ 已完成 | — |
| **三废** | 半定量：废物类别→处理要求、排放限值、细胞毒/溶剂特管 | A-Box 废物流+处理设施+排放限值 | 推断为主 | 🆕 可编码性最高 | **1** |
| **物料** | 分类+隔离/状态+物料级交叉污染+供应商资质 | A-Box 物料/库区/状态 + CoA 抽取 | 推断+符合性 | 🆕 中 | **2** |
| **人员** | 完整性/有效性/状态：培训有效期？资质匹配岗位？健康当期？ | A-Box 人员/培训/资质/健康 | 符合性/完整性 | 🆕 中（状态判定，非定量） | **3** |
| **文件** | 元完整性：必备档案是否存在、已批、版本/有效期正确 | 文档登记册 + 能力二抽取 | 完整性 | 🆕 自动化高但偏清单 | **4** |

### 6.2 两种规则风味——同一个 `RuleResult` 都装得下

| 风味 | `fired` 语义 | `conclusion` 装什么 | 代表维度 | 例 |
|---|---|---|---|---|
| **推断型** | 推出了某风险等级 | `{"risk_level": "HighRisk"}` 等 | 设备、三废 | 低 PDE + 难清洁 → HighRisk |
| **符合性/完整性型** | **存在缺陷/缺口** | `{"finding": "...", "blocking": true}` | 人员、文件 | 操作员被排入无菌灌装但更衣资质过期 → 阻断 |

> 关键：无需改 `RuleResult`——`fired=True` 既能表达"推出高风险"，也能表达"发现不符合项"；`regulation_ref` 统一承载条款依据。

---

## 7. 与 ReviewCase / 会签矩阵对接

### 7.1 会签矩阵

| 维度 | AI 预评估（评估器） | requires_signature arm 条件 | 责任签署角色 | 阻断放行 |
|---|---|---|---|---|
| 生产设备 | `assess_equipment`（复用） | HighRisk ∨ 专用化 | `equipment_engineer` | 是 |
| 三废处理 | `assess_waste` 🆕 | risk∈{High,Med} ∨ 任一 blocking | `ehs_officer` | 是 |
| 物料管理 | `assess_material` 🆕 | risk∈{High,Med} ∨ 隔离/状态冲突 | `material_manager` | 是 |
| 人员 | `assess_personnel` 🆕 | 任一 blocking（资质/培训过期） | `production_head` | 是 |
| 文件 | `assess_documentation` 🆕 | 任一必备文件缺失/过期/未批 | `qa` | 是 |
| **合并放行** | `consolidate`（聚合） | 任一维度 HighRisk ∨ 仍有未决 finding | `qa`（+ 申办可选） | 是 |

### 7.2 签名槽 = 隐式复用，不新增表

一条维度结论满足 `requires_signature=True ∧ ¬effective ∧ review_case_id=X` **即是一个待签槽**；其所需角色 = `SIGNOFF_ROLE[dimension]`。会签矩阵视图 = 按 `review_case_id` 聚合各维度结论的 `(dimension, risk_level, requires_signature, effective, signature_id)`。**无需 slot 表**，全部落在既有 `ReasoningExecution` + `ElectronicSignature` 上。

### 7.3 合并放行 = 合并结论 + QA 签

`ElectronicSignature.conclusion_id` 非空，故最终放行签名需挂在一条结论上：`consolidate` 生成一条 `dimension="__consolidation__"` 的 `ReasoningExecution`（聚合各维度 `risk_level` 与未决 `findings`），由 QA 签署该合并结论 → `ReviewCase` 置 `approved` → `release`。**全程复用** `compliance.sign` 的重认证→绑定→生效→解抑动作→上链逻辑。

### 7.4 重评幂等 = 复用取代链

对同一 `ReviewCase` 重跑某维度评估时，新建结论并把同维度旧结论 `superseded_by=新id`、`effective=False`（复用 `incremental.py` 的取代链机制），保证会签矩阵恒指向**最新**结论。

---

## 8. G1 / G2 收口点（本设计的副产物）

| 缺口 | workflow §4 定义 | 本设计在何处收口 |
|---|---|---|
| **G1** 结论无初始落库（`ReasoningExecution(...)` 仅见 `incremental.py:74`） | 流水线无法自举 | `persist_conclusion()`（§4.3）在 `ReviewCase` fan-out（`POST /cases/{id}/assess`）中为每维度落库；可选地也供 `/assess` 调用 |
| **G2** `requires_signature` 无人据风险置 True（`models/reasoning.py:36`） | 闸门形同虚设 | `arm_signature()`（§4.3）按维度风险/缺陷布防，落库即生效 |
| G3 事实事件→重算未订阅 | 半自动 | **本文范围外**，见 workflow §4 最小补齐建议 #3 |

> 即：把"多维度评估器扩展"做完，G1/G2 自然闭合——因为每个维度评估都必须经过统一落库点。

---

## 9. 数据模型变更 / DDL

### 9.1 新表 `review_cases`（🆕，最小自包含；完整生命周期状态机见评审工作流专文）

```python
# backend/app/models/review.py  🆕
class ReviewCase(Base):
    __tablename__ = "review_cases"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    subject_iri: Mapped[str]  = mapped_column(String(500), nullable=False)  # 备样生产信息对象 IRI
    title:       Mapped[str]  = mapped_column(String(200), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(30), default="draft")
    dimensions_in_scope: Mapped[dict] = mapped_column(JSON, nullable=False) # ["equipment", ...]
    version:    Mapped[int]   = mapped_column(Integer, default=1)           # 乐观并发 CAS
    created_by: Mapped[str]   = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
```

### 9.2 `reasoning_executions` 增列（加法式，均可空）

| 列 | 类型 | 用途 |
|---|---|---|
| `dimension` | `String(30)` nullable | 维度判别（`equipment`/`waste_treatment`/…/`__consolidation__`）；不复用 `execution_type` 以免改其既有语义 |
| `review_case_id` | `GUID()` nullable, FK→`review_cases.id`, index | 归属评审案，支撑会签矩阵聚合查询 |

> 兼容性：两列可空，存量逻辑（`/assess` 无状态、`incremental` 重算）不受影响。

### 9.3 签名表：**无需改表**

`electronic_signatures.signer_role` 已是 `String(50)` 自由文本（`models/reasoning.py:96`），可直接存 `ehs_officer/production_head/material_manager/equipment_engineer/qa`。仅**门禁逻辑**需从静态 QA 改为动态（§10.3）。

### 9.4 角色词表扩充（`dependencies.py:21`，必改）

```python
# 现状仅 3 角色，多责任人会签需扩充：
ROLE_EQUIPMENT_ENGINEER = "equipment_engineer"
ROLE_EHS_OFFICER        = "ehs_officer"
ROLE_MATERIAL_MANAGER   = "material_manager"
ROLE_PRODUCTION_HEAD    = "production_head"
VALID_ROLES = (ROLE_SENIOR_ANALYST, ROLE_OPERATOR, ROLE_QA,
               ROLE_EQUIPMENT_ENGINEER, ROLE_EHS_OFFICER,
               ROLE_MATERIAL_MANAGER, ROLE_PRODUCTION_HEAD)
```

> 网关按 `X-Role` 注入身份（`dependencies.py:43`），新角色须同时在网关侧放行。`senior_analyst` 仍是 AI 预评估发起者，`operator` 保持只读。

### 9.5 迁移

新增 `backend/alembic/versions/0003_review_workflow_dimensions.py`（承接 `0002_extraction_realtime.py`）：建 `review_cases`、为 `reasoning_executions` 加 `dimension`/`review_case_id` 两列 + 索引。

---

## 10. 接口契约

### 10.1 评审案与 fan-out（🆕 `/api/review`）

| 端点 | 角色 | 说明 |
|---|---|---|
| `POST /api/review/cases` | senior_analyst | 建评审案：`{subject_iri, title, dimensions_in_scope}` |
| `POST /api/review/cases/{id}/assess` | senior_analyst | 对在范围维度 fan-out：调注册表→`persist_conclusion`（G1/G2）→重评走取代链；返回各维度结论摘要；`lifecycle_state→dimension_review` |
| `GET /api/review/cases/{id}` | reader | 评审案 + **会签矩阵视图**（各维度 risk/待签/已签/责任角色） |
| `POST /api/review/cases/{id}/consolidate` | senior_analyst | 生成 `__consolidation__` 合并结论；`lifecycle_state→consolidation` |
| `POST /api/review/cases/{id}/release` | qa | 校验合并结论已签 → `lifecycle_state→released` |

fan-out 核心（摘要）：

```python
@router.post("/cases/{case_id}/assess")
def assess_case(case_id, db, engine, _=Depends(require_role(ROLE_SENIOR_ANALYST))):
    case = db.get(ReviewCase, case_id) or abort(404)
    out = []
    for dim in case.dimensions_in_scope:
        res = DIMENSION_ASSESSORS[dim].assess(engine, _ctx_for(case, dim))
        _supersede_prior(db, case_id, dim)        # 重评：旧维度结论入取代链
        out.append(persist_conclusion(db, case_id, res))
    case.lifecycle_state = "dimension_review"; db.commit()
    return {"conclusions": [ConclusionResponse.model_validate(e) for e in out]}
```

### 10.2 推理查询扩展（`/api/reasoning`）

- `GET /api/reasoning/conclusions?review_case_id=&dimension=` — 按评审案/维度过滤（新增过滤参数）。
- `GET /api/reasoning/rules` — 把 `waste_treatment`/`material_management`/`personnel_qualification`/`documentation_completeness` 追加进 `groups` 列表（`reasoning.py:129` 既有模式）。

### 10.3 泛化签名门禁（`/api/compliance/signatures`，改：静态 QA → 动态角色）

```python
def _required_role_for(c: ReasoningExecution) -> str:
    return SIGNOFF_ROLE.get(c.dimension or "__consolidation__", ROLE_QA)

@router.post("/signatures", response_model=SignResponse, status_code=201)
def sign_conclusion(req, db, identity=Depends(get_current_user)):   # 不再写死 _qa
    c = db.get(ReasoningExecution, req.conclusion_id) or abort(404)
    required = _required_role_for(c)
    if identity.role != required:
        raise HTTPException(403, f"该维度须由 {required} 签署，当前 {identity.role}")
    # —— 以下重认证→绑定 signature_id+effective→解抑 suppressed 动作→上链，逐字复用现有实现 ——
    sig.signer_role = identity.role
```

- `GET /api/compliance/signatures/pending?role=` — 把现有 QA 专用待签列表泛化为"按责任角色列出待签维度结论"（默认仍可按调用者角色过滤）。

---

## 11. 分维度任务拆解

> 规模：S≈0.5–1d，M≈2–3d，L≈1w+（含 SME 规则梳理与 T-Box 建模）。"收口"列标注顺带闭合的缺口。

| ID | 任务 | 文件 | 依赖 | 规模 | 收口 |
|---|---|---|---|---|---|
| **E-CORE** | `DimensionResult`/协议/注册表/`persist_conclusion`/`arm_signature`；`rules/base.py` 抽 `RuleResult`；migration 0003；角色词表扩充 | `dimensions/{base,persist,__init__}.py`、`rules/base.py`、`models/review.py`、`dependencies.py`、`alembic/0003` | — | M | **G1/G2** |
| **E-EQUIP** | `EquipmentAssessor`：`run_assessment`→`DimensionResult` 适配 | `dimensions/equipment.py` | E-CORE | S | — |
| **E-WASTE** | `rules/waste_treatment.py`（R-WT1~4）+ `assess_waste`；T-Box 三废类+排放限值词表 | `rules/waste_treatment.py`、`dimensions/waste.py`、TTL | E-CORE | M | — |
| **E-MAT** | `rules/material_management.py` + `assess_material`；物料/库区/状态建模；CoA 抽取接入 | 同构 + 能力二 | E-CORE | M–L | — |
| **E-PERS** | `rules/personnel_qualification.py`（符合性）+ `assess_personnel`；人员/培训/资质/健康建模 | 同构 | E-CORE | M | — |
| **E-DOC** | `rules/documentation_completeness.py`（完整性）+ `assess_documentation`；文档登记册 + 抽取 | 同构 + 能力二 | E-CORE | M | — |
| **E-REVIEW** | `/api/review` fan-out + 会签矩阵视图 + consolidate/release；泛化签名门禁 | `api/review.py`、`api/compliance.py` | E-CORE，≥1 维度评估器 | M | — |
| **E-GOV** | `RiskControlPoint` 受控词表（清单型维度）入 T-Box 工作台；规则 `regulation_ref` 校核 | TTL、`ontology_meta_store` | E-PERS/E-DOC | M | — |

**推进次序**：`E-CORE → E-EQUIP（验证适配）→ E-WASTE（验证新维度全链）→ E-REVIEW（打通会签）→ E-MAT/E-PERS/E-DOC → E-GOV`。

**每任务 DoD**：①规则包有 `ALL_RULES` 且 `/rules` 可列出；②`assess_<dim>` 返回合法 `DimensionResult`；③经 `persist_conclusion` 落一条 `ReasoningExecution` 并按风险 arm；④高风险样例能在会签矩阵中显示为待签、并可由对应责任角色签署生效；⑤结论入哈希链且 `verify` 通过。

---

## 12. 治理与本体侧

- **可追溯**：每条规则 `regulation_ref` 必填（对标设备规则的 `CFDI 2023-03 §x`）；结论 `rules_fired` 经 `/conclusions/{id}/trace` 溯源（`reasoning.py:120`）。
- **清单型维度的知识沉淀**（接能力一"预置到本体库"）：把人员/文件的控制点建模为本体受控词表 `RiskControlPoint`（控制点 + 适用条件 + 法规依据 + 责任角色），由 SME 在 **T-Box 工作台**维护，而非写死代码——形成"**定量规则进代码、清单型控制点进本体**"的互补。
- **与路线 A/B 兼容**（workflow §6）：本设计属路线 A（规则内容在代码、务实闭环）；未来若走路线 B（迁移/规则定义数据化进 `OntologyAction`），`DimensionAssessor` 协议可保留为执行层，规则元数据改由本体解释——抽象边界不变。

---

## 13. 风险与未决

| # | 事项 | 取向 |
|---|---|---|
| 1 | **事实可得性**（最大风险）：人员/物料/文件需新建 A-Box 或靠能力二抽取 | 起步可"人工核对清单即事实"落库，规则照跑，再渐进用抽取替代 |
| 2 | **规则内容成本**：非设备维度需 SME + 法规逐条映射 | 按 §11 分批；先 1–2 条高价值规则跑通骨架，再扩规则集 |
| 3 | 角色词表扩充牵动网关 | §9.4 与网关同步放行；过渡期可临时用 `qa` 兼签并在 `meaning` 注明代签 |
| 4 | 合并结论建模（`__consolidation__` vs 独立表） | 暂用合并结论复用签名链；若需富会签态（委派/并签）再升级为 slot 表 |
| 5 | `dimension` 列 vs 复用 `execution_type` | 采用独立 `dimension` 列，避免改 `execution_type` 既有语义 |
| 6 | 与 G3 联动 | 维度结论同样带 `affected_subgraph` 即可被增量重算覆盖；G3 自动订阅独立推进 |

---

## 附录 A：规则目录模板（每维度交付物）

每个 `rules/<dim>.py` 提供一张规则目录，便于评审与法规核对：

| 规则 ID | 触发条件（inputs） | 结论（conclusion） | 风味 | regulation_ref |
|---|---|---|---|---|
| R-WT1 | 细胞毒/高活性 API | `requires_dedicated_waste_treatment` + HighRisk | 推断 | 危废名录 + GMP |
| R-WT2 | β-内酰胺类废水 | `requires_pretreatment: 破环` + MediumRisk | 推断 | 制药废水排放要求 |
| R-WT3 | 废气 VOCs 浓度 > 限值 | HighRisk + blocking | 推断（阈值） | GB 31573 |
| R-WT4 | 缺危废转移联单/资质 | `finding` + blocking | 符合性 | 危废转移管理办法 |

（物料/人员/文件按同表交付。）

## 附录 B：代码触点索引

| 关注点 | 文件 |
|---|---|
| 现有设备评估器（被适配） | `backend/app/services/reasoning/engine.py:34` |
| 规则原语 `RuleResult` / `ALL_RULES` | `backend/app/services/reasoning/rules/*.py` |
| 结论落库（G1 原点） | `backend/app/services/reasoning/incremental.py:74` |
| 签名闸门（G2 字段 + 静态门禁） | `backend/app/models/reasoning.py:36`、`backend/app/api/compliance.py:25` |
| 角色词表（需扩充） | `backend/app/dependencies.py:21` |
| 规则溯源/展示 | `backend/app/api/reasoning.py:120/129` |
| 新增（本设计） | `dimensions/{base,persist,__init__,equipment,waste,materials,personnel,documentation}.py`、`rules/{waste_treatment,material_management,personnel_qualification,documentation_completeness,base}.py`、`models/review.py`、`api/review.py`、`alembic/versions/0003_review_workflow_dimensions.py` |
