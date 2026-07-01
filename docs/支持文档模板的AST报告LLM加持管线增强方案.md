# 支持文档模板的 AST 报告 LLM 加持管线增强方案

> 状态：待立项
> 日期：2026-07-01
> 前置特性：010-risk-report-generation（AST 覆盖契约）、011-ast-extraction-ui（前端 UI）
> 前提条件：目标环境提供本地 LLM 算力，API 为 OpenAI 兼容格式
> 替代文档：本方案合并了原「LLM加持下的AST报告管线增强方案.md」与模板管理需求

---

## 1. 背景与动机

当前 AST 报告管线存在两个独立但相关的瓶颈：

**瓶颈一：模板管理缺失**

AST 模板 `qs_a_020f05.json` 是硬编码的 JSON 文件，`load_template()` 通过
`_TEMPLATE_FILES` 字典查找，整个系统只支持一种报告模板。无法：
- 适配不同文档类型（如不同企业的 CMC 报告格式差异）
- 让用户自定义评估维度（增减槽位）
- 将文档类型自动匹配到正确的模板

**瓶颈二：抽取层召回率受限**

10 个 endpoint finder（`find_drug_product` … `find_degradation`）全靠刚性规则
（表头签名、KV 拆分、正则匹配）。文档稍有变体——表头改措辞、数据嵌在段落里而非
独立 KV 行——finder 就静默漏抽，导致 `CoverageManifest` 中 `missing_required`
堆积。

**合并立项的原因**

两个瓶颈的解法共享同一个数据层——**模板定义与文档类型的映射**：
- LLM 补抽需要知道「缺什么」→ 来自模板的 `missing_required` 槽位
- 本体驱动的动态槽位扩展需要「注册到哪里」→ 模板管理层
- 新文档类型适配需要「用哪个模板」→ 文档类型→模板匹配

先建模板管理层（012），LLM 增强（013）才有地方挂载。两个特性串行依赖、共享接口，
合并为一个方案减少接口翻修。

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                          012: 模板管理层                             │
│                                                                     │
│  ReportTemplate (DB)          DocumentTypeMapping (DB)               │
│  ┌──────────────────┐         ┌──────────────────────┐              │
│  │ id (UUID)         │         │ doc_class_iri_pattern │              │
│  │ name              │         │ template_id           │              │
│  │ version           │         │ priority              │              │
│  │ schema_json       │◄────────│                       │              │
│  │ is_default        │         └──────────────────────┘              │
│  │ created_by        │                                               │
│  │ created_at        │         模板 CRUD API                         │
│  └──────────────────┘         GET/POST/PUT/DELETE /api/ast-templates │
│                               GET /api/ast-templates/match/{job_id} │
│                                                                     │
│  前端: /settings/ast-templates  模板列表 / 编辑 / 上传               │
│  前端: AST 页面 → 模板选择器（下拉切换 → 重新计算覆盖率）           │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 │ load_template(template_id)
                                 │ resolve_template(doc_class_iri)
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         013: LLM 抽取增强                            │
│                                                                     │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────────────────┐  │
│  │ 模式 A       │    │ 模式 B        │    │ 现有规则 finder        │  │
│  │ 覆盖缺口补抽 │    │ 本体驱动      │    │ (10 个, 确定性)        │  │
│  │              │    │ 段落级深度拆解 │    │                        │  │
│  └──────┬───────┘    └──────┬───────┘    └───────────┬────────────┘  │
│         │                   │                        │               │
│         │    edges ─────────┼────────────────────────┘               │
│         ▼                   ▼                                        │
│  ┌──────────────────────────────────────────┐                        │
│  │ edges_to_facts → evaluate →               │  ← 确定性评估层       │
│  │ validate_coverage → docx_renderer         │    （不引入 LLM）     │
│  └──────────────────────────────────────────┘                        │
└─────────────────────────────────────────────────────────────────────┘
```

**关键原则**：LLM 只进入抽取层，评估层（规则引擎 + 覆盖校验 + 报告渲染）保持
完全确定性，满足 GMP 审计与可追溯性要求。

---

## 3. 特性 012：AST 模板管理

### 3.1 数据模型

```python
# backend/app/models/reporting.py (新建)

class AstTemplate(Base):
    __tablename__ = "ast_templates"

    id = Column(UUID, primary_key=True, default=uuid4)
    name = Column(String(200), nullable=False)          # "QS-A-020F05 风险评估"
    version = Column(String(20), nullable=False)        # "v1", "v2"
    doc_no = Column(String(50))                         # "QS-A-020F05"
    schema_json = Column(JSON, nullable=False)          # 完整模板 JSON (sections/groups/slots)
    is_default = Column(Boolean, default=False)         # 同一时间只有一个 default
    created_by = Column(String(100))
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_template_name_version"),
    )


class DocumentTypeMapping(Base):
    __tablename__ = "document_type_mappings"

    id = Column(UUID, primary_key=True, default=uuid4)
    doc_class_iri_pattern = Column(String(500), nullable=False)  # "CMCReport", "StabilityReport"
    template_id = Column(UUID, ForeignKey("ast_templates.id"), nullable=False)
    priority = Column(Integer, default=0)                        # 多规则时优先级
    created_at = Column(DateTime, default=func.now())

    template = relationship("AstTemplate")
```

### 3.2 模板解析层改造

`ast_template.py` 的 `load_template()` 扩展为 DB 优先、JSON 文件兜底：

```python
def resolve_template(doc_class_iri: str, db: Session) -> ReportTemplate:
    """按文档类型 IRI 查找匹配的模板，DB 优先，文件兜底。"""
    mapping = (
        db.query(DocumentTypeMapping)
        .filter(literal(doc_class_iri).contains(DocumentTypeMapping.doc_class_iri_pattern))
        .order_by(DocumentTypeMapping.priority.desc())
        .first()
    )
    if mapping:
        return ReportTemplate.model_validate(mapping.template.schema_json)

    # 兜底：无映射时用 is_default 模板
    default = db.query(AstTemplate).filter_by(is_default=True).first()
    if default:
        return ReportTemplate.model_validate(default.schema_json)

    # 最终兜底：原始 JSON 文件
    return load_default_template()
```

### 3.3 API 设计

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/ast-templates` | 模板列表（含 is_default 标记） |
| `POST` | `/api/ast-templates` | 上传模板 JSON（Pydantic 校验 ReportTemplate schema） |
| `PUT` | `/api/ast-templates/{id}` | 更新模板（创建新 version，旧版本保留） |
| `DELETE` | `/api/ast-templates/{id}` | 删除（非 default 时允许） |
| `POST` | `/api/ast-templates/{id}/set-default` | 设为默认模板 |
| `GET` | `/api/ast-templates/match/{job_id}` | 按 job 的 doc_class_iri 返回匹配的模板 |
| `GET` | `/api/document-type-mappings` | 文档类型→模板映射列表 |
| `POST` | `/api/document-type-mappings` | 新增映射规则 |
| `DELETE` | `/api/document-type-mappings/{id}` | 删除映射规则 |

**上传校验流程**：
```
用户上传 JSON → Pydantic ReportTemplate.model_validate()
  → 通过：slot_id 唯一性、source kind 合法性、required/on_missing 逻辑
  → 失败：返回 422 + 具体校验错误
```

### 3.4 前端 UI

**模板管理页面** `/settings/ast-templates`：

```
┌─────────────────────────────────────────────────────┐
│  AST 模板管理                          [上传模板]    │
├─────────────────────────────────────────────────────┤
│  名称                 版本   槽位数  默认   操作     │
│  QS-A-020F05 风险评估  v1     18     ★     编辑|删除 │
│  稳定性评估            v1     12           编辑|删除 │
│  清洁验证评估          v1     22           编辑|删除 │
├─────────────────────────────────────────────────────┤
│  文档类型映射                                        │
│  CMCReport        → QS-A-020F05 风险评估 (v1)       │
│  StabilityReport  → 稳定性评估 (v1)                 │
│                                        [添加映射]    │
└─────────────────────────────────────────────────────┘
```

**AST 页面模板切换**：在现有 AST 页面头部增加模板选择器

```
┌──────────────────────────────────────────────────┐
│  ← 返回    AST 覆盖率    Job 3a8f…              │
│            模板: [QS-A-020F05 v1 ▾]   [生成报告] │
└──────────────────────────────────────────────────┘
```

选择不同模板 → 调用 `GET /ast-coverage?template_id=xxx` → 重新计算覆盖率 → 刷新。

### 3.5 迁移策略

Alembic 迁移将默认 JSON 模板 `qs_a_020f05.json` 种子化到 `ast_templates` 表
（`is_default=True`），并创建 `CMCReport → 该模板` 的 DocumentTypeMapping。
现有功能零回归。

### 3.6 对现有代码的影响

| 模块 | 变更 |
|------|------|
| `ast_template.py` | 新增 `resolve_template(doc_class_iri, db)` ；`load_template()` 保留为兜底 |
| `_build_ast_coverage_response` (extraction.py) | 调用 `resolve_template` 替代 `load_default_template` ；接受可选 `template_id` 参数 |
| `RiskReportGenerator.generate_with_coverage` | 接受可选 `template_id` ，传递给 `validate_coverage` |
| `validate_coverage` | 签名不变——已接受 `template: ReportTemplate` 参数 |
| `docx_renderer` | 无变更——它消费 `RiskReport` + `CoverageManifest`，不直接依赖模板 |

---

## 4. 特性 013：LLM 抽取增强

### 4.1 配置模型

```python
# backend/app/config.py 新增

class Settings(BaseSettings):
    # ... 现有配置 ...

    # 本地 LLM 补抽（AST 覆盖缺口二次抽取）
    local_llm_enabled: bool = False
    local_llm_base_url: str = "http://localhost:11434/v1"  # OpenAI 兼容端点
    local_llm_model: str = "qwen2.5:14b"                   # 模型标识
    local_llm_api_key: str = "not-needed"                   # 本地部署通常无需鉴权
    local_llm_max_tokens: int = 4096
    local_llm_temperature: float = 0.1                      # 抽取任务低温度
```

门控逻辑与现有 `llm_cloud_enabled` 同构：`local_llm_enabled` 为 `False`（默认）时
完全不触发，零回归。

### 4.2 LLM 客户端封装

```python
# backend/app/services/llm/local_client.py (新建)

from openai import OpenAI
from app.config import settings

def get_local_llm() -> OpenAI | None:
    """返回本地 LLM 客户端，未启用时返回 None。"""
    if not settings.local_llm_enabled:
        return None
    return OpenAI(
        base_url=settings.local_llm_base_url,
        api_key=settings.local_llm_api_key,
    )
```

### 4.3 模式 A：覆盖缺口补抽

在 `validate_coverage()` 返回 `CoverageManifest` 后，若 `local_llm_enabled` 且
存在 `missing_required` 槽位，自动发起 LLM 定向补抽。

```
规则 finder (10 个)
  → edges
    → validate_coverage()
      → CoverageManifest (第一轮)
        → missing_required > 0 且 local_llm_enabled?
          → YES: LLM 定向补抽
            → 补充 edges
              → validate_coverage() (第二轮)
                → 最终 CoverageManifest
          → NO: 返回第一轮结果
```

**新增模块**：

```python
# backend/app/services/extraction/llm_gap_filler.py (新建)

def fill_coverage_gaps(
    manifest: CoverageManifest,
    document_sections: list[dict],       # 章节文本 [{title, text}]
    template: ReportTemplate,
) -> list[dict]:
    """对 missing_required 槽位发起 LLM 定向补抽，返回补充的 edges。"""
```

**Prompt 结构**（模式 A）：

```
你是一个药品 CMC 文档信息抽取助手。

以下是一份文档的原文内容（已分章节）：
{document_sections}

请从文档中提取以下缺失的信息项。每个信息项的定义如下：
{missing_slots_schema}

返回 JSON 数组，每个元素包含：
- slot_id: 槽位标识
- extracted_value: 提取的值
- source_span: 原文中的来源片段（用于溯源）

如果文档中确实不包含某项信息，该项返回 null。
仅返回 JSON，不要附加其他文字。
```

`missing_slots_schema` 由模板中 `missing_required` 槽位的定义自动生成：
```json
[
  {"slot_id": "subject.pde", "label": "PDE", "description": "每日允许暴露量",
   "expected_type": "decimal", "object_class": "DrugProduct"}
]
```

**Edge 标记**：补抽产出的 edge 携带 `source: "llm"`（区别于现有的 `source: "rule"`），
审计链可区分来源。UI 不暴露此标记（LLM 为内部实现）。

### 4.4 模式 B：本体驱动的段落级深度拆解

**依赖 012 的模板层**：模式 B 运行时从本体 T-Box 查询数据属性，动态扩展模板槽位。

```
本体 T-Box
  → OntologyEngine.get_data_properties_by_domain(class_iri)
    → [{iri: "pde_mg_per_day", name: "pde_mg_per_day", label: "PDE"}, ...]
      → 自动展开为细粒度 slot schema
        → 注入 012 模板层（运行时合成，不修改 DB 模板）
```

**实现**：

```python
# backend/app/services/reporting/template_expander.py (新建)

def expand_template_with_ontology(
    template: ReportTemplate,
    doc_class_iri: str,
    engine: OntologyEngine,
) -> ReportTemplate:
    """从本体 T-Box 查询 doc_class 关联类的数据属性，动态扩展模板槽位。

    不修改原模板，返回新的 ReportTemplate 实例。
    已在模板中声明的槽位不重复添加。
    """
```

扩展逻辑：
1. 从 doc_class_iri 出发，遍历其对象属性的 range 类
2. 对每个 range 类调用 `get_data_properties_by_domain(range_class_iri)`
3. 过滤掉已在静态模板中声明的属性（按 IRI 去重）
4. 为新属性生成 `Slot`（`source.kind="extraction"`），分组到对应的 Group 下
5. 新增槽位标记 `source.kind="llm_extraction"`（新增 SlotSource 类型）

**新增 SlotSource 类型**：

```python
class LLMExtractionSource(BaseModel):
    """LLM 从叙述段落中定向提取——语义标记，审计链可区分。"""
    kind: Literal["llm_extraction"] = "llm_extraction"
    object_class_iri: str             # 完整类 IRI
    data_property_iri: str            # 完整属性 IRI
    label: str                        # 人类可读标签
```

**Prompt 结构**（模式 B）：

```
你是一个药品 CMC 文档结构化抽取助手。

## 目标章节原文
{section_text}

## 本体属性 schema
以下是本体定义的属性列表，每个属性代表一个需要提取的信息点：
{ontology_data_properties_schema}

## 任务
从文本中提取上述属性的值。对于每个成功提取的值，标注其在原文中的来源片段。

## 输出格式
返回 JSON 数组：
[
  {"iri": "pde_mg_per_day", "value": "1.80",
   "source_span": "HRS-1234 的 PDE 为 1.80mg"}
]
如某属性在文本中不存在，不返回该项。仅返回 JSON。
```

### 4.5 组合流程

模式 A 与模式 B 可组合：

```
parse_docx_structure
  → 规则式 finder (10 个, 确定性, 快速)
    → edges (第一批)

  → 模式 B: 本体驱动段落级拆解 (if local_llm_enabled)
    → LLM 从叙述段落提取细粒度属性
      → edges (第二批, source="llm")

  → 合并 edges → edges_to_facts → evaluate
    → validate_coverage (expanded_template)
      → CoverageManifest (第一轮)

  → 模式 A: 覆盖缺口补抽 (if still missing_required > 0)
    → LLM 定向补抽剩余缺口
      → edges (第三批, source="llm")
        → 重新 validate_coverage
          → 最终 CoverageManifest
```

### 4.6 与现有管线的兼容性

| 模块 | 是否变更 | 说明 |
|------|---------|------|
| `relation_extractor.py` (10 个 finder) | **不变** | 规则 finder 保持原样，LLM 是增补不是替代 |
| `edges_to_facts` | **不变** | 消费 edges 列表，不关心来源 |
| `evaluate()` 规则引擎 | **不变** | 确定性评估，不引入 LLM |
| `validate_coverage()` | **不变** | 接受 `ReportTemplate` 参数，不关心模板来源 |
| `docx_renderer` | **不变** | 消费 `RiskReport` + `CoverageManifest` |
| `ast_template.py` SlotSource | **扩展** | 新增 `LLMExtractionSource` 到 discriminated union |
| `coverage_validator.py` | **微调** | 新增 `_resolve_llm_extraction()` 分支，模式与 `_resolve_extraction()` 同构 |
| `document_classifier.py` | **可选增强** | LLM 兜底分类（低优先级） |

---

## 5. 前端交互设计

### 5.1 LLM 交互约束

**「LLM 为内部实现，用户不需要感知」**——不设显式「LLM 补抽」按钮。

- 补抽结果直接体现为槽位填充状态的变化
- `source_kind` 在 SlotDetailPanel 中展示为"自动提取"（不区分规则 vs LLM）
- 补抽 edge 携带 `source_span`，SlotDetailPanel 可展示来源原文片段供确认
- 审计日志中标记 `source: "llm"`，管理员可查

### 5.2 模板选择交互

AST 页面头部新增模板选择器（仅当 `ast_templates` 表有多个模板时显示）：

```tsx
// 仅多模板时显示
{templates.length > 1 && (
  <Select value={templateId} onValueChange={switchTemplate}>
    {templates.map(t => (
      <SelectItem key={t.id} value={t.id}>
        {t.name} ({t.version})
      </SelectItem>
    ))}
  </Select>
)}
```

### 5.3 本体属性动态槽位的 UI 呈现

模式 B 动态扩展的槽位在 ASTTreeView 中以独立分组展示：

```
▶ SECTION I 风险评估
  ▶ 1. 风险评估对象 (4 slots)     ← 静态模板槽位
  ▶ 产品扩展属性 (12 slots)        ← 本体动态生成（灰色标签 "本体属性"）
  ▶ 2. 评估前提 (1 slot)
  ▶ 3. 设备一览表 (4 slots)
  ▶ 4. 风险评估 (7 slots × N rules)
```

---

## 6. 实施路线

### Phase 1：模板管理层（012）—— 无 LLM 依赖

| 步骤 | 内容 | 产出 |
|------|------|------|
| 1.1 | DB 模型 + Alembic 迁移 + 种子数据 | `ast_templates` + `document_type_mappings` 表 |
| 1.2 | 模板 CRUD API + Pydantic 校验 | 9 个 REST 端点 |
| 1.3 | `resolve_template()` 替代 `load_default_template()` | 模板解析层改造 |
| 1.4 | `_build_ast_coverage_response` 接受 `template_id` 参数 | 覆盖率端点支持模板切换 |
| 1.5 | 前端模板管理页面 `/settings/ast-templates` | 模板 CRUD UI |
| 1.6 | AST 页面模板选择器 | 切换模板重新计算覆盖率 |
| 1.7 | 测试 + 迁移验证 | 现有功能零回归 |

**独立交付点**：012 完成后可独立上线，用户可上传自定义模板、配置文档类型映射。

### Phase 2：LLM 基础集成（013-A）

| 步骤 | 内容 | 产出 |
|------|------|------|
| 2.1 | `Settings` 扩展 + `local_client.py` | 本地 LLM 客户端封装 |
| 2.2 | `llm_gap_filler.py` | 模式 A 覆盖缺口补抽 |
| 2.3 | 补抽流程集成到 `_build_ast_coverage_response` | LLM 自动补抽 + source="llm" 标记 |
| 2.4 | edge 携带 `source_span` + 前端 SlotDetailPanel 展示 | 来源溯源 |
| 2.5 | 测试：mock LLM → 验证补抽流程 + 审计链 | `local_llm_enabled=False` 零回归 |

### Phase 3：本体驱动深度拆解（013-B）

| 步骤 | 内容 | 产出 |
|------|------|------|
| 3.1 | `template_expander.py` | 本体属性 → 动态槽位扩展 |
| 3.2 | `LLMExtractionSource` + 覆盖校验器扩展 | 新 SlotSource 类型 |
| 3.3 | 模式 B prompt 构建 + 段落定位 | 本体驱动的 LLM 段落级抽取 |
| 3.4 | 组合模式 A + B 流程 | 先深度拆解后缺口补抽 |
| 3.5 | 前端动态槽位分组展示 | ASTTreeView 支持动态组 |

### Phase 4：泛化验证

| 步骤 | 内容 | 产出 |
|------|------|------|
| 4.1 | 创建第二个文档类型的模板（如清洁验证报告） | 验证模板管理层泛化 |
| 4.2 | 本体 T-Box 新增对应类 + 属性 | 验证本体驱动泛化 |
| 4.3 | 端到端：上传新类型文档 → 自动匹配模板 → LLM 抽取 → 覆盖率 → 报告 | 全流程验证 |

---

## 7. 泛化路径

模板管理 + 本体驱动 LLM 抽取成熟后，适配新的评估文档类型只需：

```
1. 本体 T-Box 新增文档类 + 数据属性定义（TTL 维护）
2. 上传对应的 AST 模板 JSON（通过 012 管理 UI）
3. 配置文档类 → 模板映射规则（012 管理 UI）
4. LLM 按本体 schema 自动抽取 → edges → 标准确定性管线
```

无需为每种文档类型编写 Python finder / 正则。本体成为「抽取 schema 的唯一权威源」
（Constitution Principle I: 规范驱动开发），LLM 成为「按 schema 执行抽取的通用引擎」。

---

## 8. 不引入 LLM 的部分

| 环节 | 原因 |
|------|------|
| `evaluate(rule, facts)` 规则引擎 | 风险等级判定必须确定性、可审计、可复现 |
| `validate_coverage()` 覆盖校验 | AST 遍历是形式化契约，不容 LLM 模糊判断 |
| `docx_renderer` 报告渲染 | 模板驱动，无语义理解需求 |
| G1 三态映射 `TRUE/FALSE/UNKNOWN` | 确定性语义订正 |

---

## 9. 风险与应对

| 风险 | 应对 |
|------|------|
| 本地 LLM 精度不如云端大模型 | slot schema 提供强约束（类似 function calling），降低对模型通用能力的依赖 |
| LLM 补抽结果不可靠 | edge 标记 `source: "llm"` + `source_span` 溯源；评估层确定性不变 |
| 增加报告生成延迟 | LLM 仅对 `missing_required` 触发，非全量；本地部署延迟可控（< 5s） |
| 模板 JSON schema 校验不够 | Pydantic `ReportTemplate.model_validate()` 已有完整校验：slot_id 唯一性、source kind 合法性 |
| 模板切换导致历史覆盖清单不一致 | `GeneratedReport.rules_summary` 快照模板 ID + 覆盖数据，不回溯更新 |
| 与离线优先原则（Constitution VI）的兼容性 | 本地 LLM 完全离线运行；`local_llm_enabled` 默认关 |

---

## 10. 现有代码入口参考

| 文件 | 关键函数/类 | 在本方案中的角色 |
|------|-----------|----------------|
| `backend/app/services/reporting/ast_template.py` | `ReportTemplate`, `load_template()`, `SlotSource` union | 012 扩展 `resolve_template()`；013 扩展 `LLMExtractionSource` |
| `backend/app/services/reporting/coverage_validator.py` | `validate_coverage()`, `CoverageManifest` | 013 模式 A 的触发点（`missing_required_slots`） |
| `backend/app/services/reporting/risk_report_generator.py` | `generate_with_coverage()` | 012 接受 `template_id` 参数 |
| `backend/app/api/extraction.py` | `_build_ast_coverage_response()` | 012 调用 `resolve_template()`；013 集成 LLM 补抽 |
| `backend/app/services/ontology_engine.py:370` | `get_data_properties_by_domain()` | 013 模式 B 的数据源——本体属性 → 动态槽位 |
| `backend/app/services/extraction/relation_extractor.py` | 10 个 `find_*` 函数 | 保持不变——LLM 是增补不是替代 |
| `backend/app/config.py` | `Settings` | 013 新增 `local_llm_*` 配置 |
| `backend/app/services/reporting/templates/qs_a_020f05.json` | 默认模板 (18 slots) | 012 迁移种子数据 |
| `frontend/src/app/(dashboard)/entities/extraction/[jobId]/ast/page.tsx` | AST 页面 | 012 增加模板选择器 |
| `frontend/src/components/extraction/slot-detail-panel.tsx` | 槽位详情 | 013 展示 `source_span` 溯源 |
