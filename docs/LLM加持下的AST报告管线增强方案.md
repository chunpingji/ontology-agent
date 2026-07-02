# LLM 加持下的 AST 报告管线增强方案

> 状态：备忘（待立项）
> 日期：2026-07-01
> 前置特性：010-risk-report-generation（AST 覆盖契约，已完成）、011-ast-extraction-ui（前端 UI，spec 阶段）
> 前提条件：目标环境提供本地 LLM 算力，API 为 OpenAI 兼容格式

---

## 术语表

| 缩写 | 全称 | 说明 |
|------|------|------|
| AST | Assessment Semantic Template（评估语义模板） | 将风险评估报告结构显式声明为可遍历、可校验的语义模板树 |
| LLM | Large Language Model（大语言模型） | 本方案中指本地部署的 OpenAI 兼容格式模型 |
| CMC | Chemistry, Manufacturing and Controls（化学、生产与控制） | 药品注册申报中的质量研究文档体系 |
| PDE | Permitted Daily Exposure（每日允许暴露量） | 共线生产中残留物质的安全阈值，单位 mg/天 |
| GMP | Good Manufacturing Practice（药品生产质量管理规范） | 药品生产和质量管理的法规标准 |
| T-Box | Terminological Box（术语框） | 本体中定义类、属性和公理的概念层（区别于实例层 A-Box） |
| RAG | Retrieval-Augmented Generation（检索增强生成） | 将检索到的文档片段作为上下文注入 LLM 提示的技术模式 |
| KV | Key-Value（键值对） | 文档中「标签：值」格式的结构化数据行 |
| IRI | Internationalized Resource Identifier（国际化资源标识符） | 本体中唯一标识类或属性的 URI |

---

## 1. 背景

AST 报告管线分两段，性质完全不同：

| 阶段 | 代码入口 | 当前实现 | 是否适合引入 LLM |
|------|---------|---------|-----------------|
| **抽取层**（产出 edges） | `docx_structure` → `document_classifier` → 10 个 endpoint finder | 正则 / 表头签名 / KV 拆分 — 刚性模式匹配 | **是** |
| **评估层**（edges → 报告） | `edges_to_facts` → `evaluate` → `validate_coverage` → `docx_renderer` | 确定性规则引擎 + AST 遍历 | **否**（确定性是审计要求，不可替换） |

LLM 的增值点集中在 **抽取层**。评估层必须保持确定性以满足 GMP 审计与可追溯性要求。

---

## 2. 增强方向

### 2.1 端点 finder 召回率（影响最大）

**现状**：10 个 endpoint finder 全靠刚性规则。以 `find_shared_line`
（`relation_extractor.py:527`）为例：

```python
tbl = ctx.structure.find_table("参数", "数值")    # 表头必须恰好含这两个词
sec = ctx.structure.find_section("日剂量", "给药")  # 标题必须含这些关键词
kv = _split_kv(para)                              # 段落必须是「key：value」格式
```

文档稍有变体（表头改叫「毒理参数 / 检测值」、PDE 写在段落中间而非独立 KV 行、
章节标题换了措辞），finder 就静默漏抽。漏抽直接导致 `CoverageManifest` 中出现
`missing_required`，报告里标注「⚠ 待评估（数据缺失）」。

**LLM 增强方案**：对 `CoverageManifest` 中 `missing_required` 的槽位，用 LLM 做
**定向二次抽取** —— 将 AST slot schema（槽位语义定义 + 期望数据类型）作为 prompt
的结构化约束，让 LLM 从原文定向提取缺失值。这是精确制导的 RAG 模式，不是让 LLM
自由发挥。

**预期收益**：覆盖率从「取决于文档格式规范度」提升到接近人工水平；
`missing_required` 数量大幅下降。

### 2.2 数据属性的语义抽取

**现状**：`_dp()` + `_split_kv()` 只能处理「标签：值」这种扁平 KV 格式。但 CMC
文档中很多数据属性嵌在自然语言段落里，例如：

> "经评估，本品 PDE 为 1.80mg/天，属非高活性、非细胞毒性品种"

现有 finder 靠正则 `kv[0].strip().upper() == "PDE"` 只能匹配独立行的 KV，上面这种
句内嵌入就漏掉了。LLM 能理解上下文语义，精准提取 `pde_mg_per_day=1.80`、
`分类=非高活性`。

**预期收益**：`data_properties` 填充率提升，尤其对叙述性段落中的嵌入值。

### 2.3 本体驱动的细粒度槽位拆解（泛化关键）

**现状**：当前 AST 模板 `subject` 组只有 4 个粗粒度槽位：

```json
subject.name    ← DrugProduct.text        (产品名称)
subject.pde     ← DrugProduct.PDE         (PDE 值)
subject.class   ← DrugProduct.分类        (药品分类)
subject.dosage  ← DrugProduct.剂型        (剂型)
```

但实际的「风险评估对象描述」章节是一段密集叙述，包含远比 4 个字段更丰富的语义
子结构。以真实 CMC 文档为例，该章节的逻辑分解如下：

| # | 语义子结构 | 内容示例 | 映射的本体概念 |
|---|-----------|---------|--------------|
| 1 | 基本背景 | 计划于 642、646 车间新增 1 批…临床备样生产，预计批量 0.42-9.22kg，用于临床 I 期试验 | `DrugProduct` (batch_size, clinical_phase) |
| 2 | 总工艺 | 以 1234-4 和 SMC 为起始物料，通过 Suzuki 偶联反应… | `SynthesisRoute` (starting_materials, intermediates, reactions) |
| 3.1 | 产品基本信息 | 剂型为口服速释片剂，给药途径为口服，分子式，分子量 | `DrugProduct` (dosage_form, route_of_admin, molecular_formula, molecular_weight) |
| 3.2 | 溶解性 | 在甲醇中微溶，在水以及 0.1mol/L 盐酸溶液中几乎不溶 | `DrugProduct` (solubility_profile) |
| 3.3 | 毒性/风险分类 | PDE 为 1.80mg，非高活性、非细胞毒性品种，无需灭活和隔离存放 | `SharedLineAssessmentData` (pde, activity_class, cytotoxicity, isolation_required) |
| 4 | 生产区域要求 | 精制/干燥/包装在 642 车间 D 级洁净区；合成/干燥在一般区 | `ProductionArea` (cleanroom_class, workshop_id, operations) |
| 5 | 设施满足度 | 642 车间…已于 2020 年完成厂房设施确认…配套普冷、氮气… | `Facility` (qualification_date, utility_systems, validation_status) |

**核心洞察**：本体 T-Box 中这些类的数据属性（`data properties`）天然构成细粒度
槽位的定义。`OntologyEngine.get_data_properties_by_domain(class_iri)` 已能按类
反查全部数据属性 —— 这就是自动生成槽位 schema 的数据源。

**LLM + 本体联合方案**：

```
本体 T-Box
  → get_data_properties_by_domain(DrugProduct)
    → [{iri: "pde_mg_per_day", label: "PDE"},
        {iri: "dosage_form", label: "剂型"},
        {iri: "route_of_admin", label: "给药途径"},
        {iri: "clinical_phase", label: "临床阶段"},
        {iri: "molecular_formula", label: "分子式"}, ...]
      → 自动展开为细粒度 slot schema
        → LLM prompt: "从以下文本中提取这些属性值"
          → 结构化 JSON 输出
```

**与现有 AST 架构的兼容性**：

- `Slot.source` 已支持 `kind: "extraction"`，只需增加更多细分的 slot 定义
- `SlotSource` 的 discriminated union 可扩展新的 `kind: "llm_extraction"` —— 语义上
  标记此槽位由 LLM 从叙述段落中提取，而非由规则式 finder 产出
- 覆盖校验器（`validate_coverage`）无需改动 —— 它只看 slot 是否填充，不关心来源
- 审计链自然区分：`source: "llm_extraction"` vs `source: "extraction"` vs `source: "rule"`

**泛化价值**：这个方案的关键不是针对某一篇文档，而是**让本体定义驱动槽位生成**。
当需要支持新的评估文档类型时：

1. 在本体中定义新的文档类 + 数据属性（T-Box 维护）
2. AST 模板自动从本体反查属性 → 生成槽位 schema
3. LLM 按 schema 从文档段落中定向抽取

无需为每种文档类型手写 finder / 正则。这是从「一种报告类型需要一套 finder」到
「本体定义 + LLM 抽取 = 零代码适配新报告类型」的质变。

### 2.4 文档分类鲁棒性

**现状**：`document_classifier` 是规则式的。对于非标准格式或新增文档类型，需要逐一
添加分类规则。LLM 可基于文档结构 + 内容语义做零样本分类，作为规则分类器的兜底。

**预期收益**：中等 —— 当前 CMCReport 分类准确度已经够用，但对未来扩展到其他文档
类型有价值。

---

## 3. 架构设计

引入 LLM **不改动评估层**，在抽取层增加两个新能力：

### 模式 A：覆盖缺口补抽（缺失槽位后补）

```
parse_docx_structure
  → 规则式 finder（现有，快速、确定性）
    → edges_to_facts → evaluate → validate_coverage
      → CoverageManifest
        → [missing_required slots]
          → LLM 定向补抽（仅对缺失槽位触发）
            → 补充 edges → 重新 validate_coverage
              → 最终 CoverageManifest + RiskReport
```

### 模式 B：本体驱动的段落级深度拆解（细粒度抽取）

```
本体 T-Box
  → get_data_properties_by_domain(class_iri)
    → 自动展开为细粒度 slot schema
      → AST 模板动态扩展（不修改 JSON 模板，运行时合成）

parse_docx_structure → 章节定位
  → LLM 段落级抽取（以 slot schema 为结构化约束）
    → 结构化 JSON → edges
      → 汇入标准管线（edges_to_facts → evaluate → …）
```

模式 A 是**缺口修补**（先跑规则，不够再补），模式 B 是**能力扩展**（本体定义新
属性即可适配新报告类型，无需手写 finder）。二者可组合：模式 B 产出的细粒度 edges
先填充，模式 A 再兜底补缺。

### 3.1 关键设计点

1. **LLM 是增补而非替代** —— 规则 finder 先跑，LLM 只补缺口，避免不必要的 token
   消耗。规则式 finder 的结果依然是首选（确定性强、零延迟）。

2. **本地 LLM + OpenAI 兼容 API** —— 满足 Constitution Principle VI（离线优先），
   只需将现有 `anthropic.Anthropic()` 替换为
   `openai.OpenAI(base_url=local_endpoint)`。零外网依赖。

3. **覆盖清单驱动** —— LLM 的输入不是盲目「抽取所有信息」，而是由
   `CoverageManifest` 精确告知「缺什么」，slot schema 告知「长什么样」，大幅提升
   抽取精度并控制 prompt 长度。

4. **审计不变** —— LLM 补抽的值仍经 `edges_to_facts` → `evaluate` 确定性管线，
   风险等级判定完全可追溯。补抽来源在 edge 上标记 `source: "llm"`（区别于现有的
   `"rule"`），审计链可区分。

### 3.2 配置模型

扩展 `backend/app/config.py` 的 `Settings`：

```python
# 本地 LLM 补抽（AST 覆盖缺口二次抽取）
local_llm_enabled: bool = False
local_llm_base_url: str = "http://localhost:8000/v1"   # OpenAI 兼容端点
local_llm_model: str = "default"                       # 模型标识
local_llm_api_key: str = "not-needed"                  # 本地部署通常无需鉴权
```

门控逻辑与现有 `llm_cloud_enabled` 同构：`local_llm_enabled` 为 `False`（默认）时
完全不触发，零回归。

### 3.3 prompt 结构

#### 模式 A prompt（缺口补抽）

```
你是一个药品 CMC 文档信息抽取助手。

以下是一份文档的原文内容（已分章节）：
{document_sections}

请从文档中提取以下缺失的信息项。每个信息项的定义如下：
{missing_slots_schema}

返回 JSON 数组，每个元素包含 slot_id 和 extracted_value。
如果文档中确实不包含某项信息，该项返回 null。
仅返回 JSON，不要附加其他文字。
```

#### 模式 B prompt（本体驱动段落级拆解）

```
你是一个药品 CMC 文档结构化抽取助手。

## 目标章节原文
{section_text}

## 本体属性 schema
以下是本体定义的属性列表，每个属性代表一个需要提取的信息点：
{ontology_data_properties_schema}

示例：
[
  {"iri": "pde_mg_per_day", "label": "PDE", "range": "xsd:decimal",
   "description": "每日允许暴露量（mg/天）"},
  {"iri": "clinical_phase", "label": "临床阶段", "range": "xsd:string",
   "description": "当前临床试验阶段（I/II/III 期）"},
  ...
]

## 任务
1. 识别段落中的语义子结构（背景/工艺/产品信息/毒性/生产区域/设施等）
2. 将每个语义子结构中的信息提取为属性值
3. 对于每个成功提取的值，标注其在原文中的来源片段（source_span）

## 输出格式
返回 JSON 数组：
[
  {
    "iri": "pde_mg_per_day",
    "value": "1.80",
    "source_span": "HRS-1234 的 PDE 为 1.80mg",
    "semantic_block": "毒性/风险分类"
  },
  ...
]
如某属性在文本中不存在，不返回该项（而非返回 null）。
仅返回 JSON。
```

模式 B 的 prompt 特点：
- **本体属性 schema 即约束** —— 类似 function calling 的参数定义，LLM 不自由发挥
- **source_span 溯源** —— 每个提取值附带原文片段，供用户确认和审计
- **semantic_block 标注** —— 识别段落内的逻辑子结构，辅助 UI 展示分组

### 3.4 与 011 前端 UI 的交互

**关键设计约束（2026-07-01 确认）**：LLM 补抽是**内部实现细节**，用户不需要感知。
不设显式「LLM 补抽」按钮，补抽结果直接体现为槽位填充状态的变化。

交互模式：

- 抽取完成后自动触发覆盖校验（011 现有设计）
- 若 `local_llm_enabled=true`，覆盖校验管线内部自动对 `missing_required` 槽位
  发起 LLM 定向补抽，补抽结果合入 edges → 重新校验
- 用户看到的是最终覆盖状态（补抽后），不感知中间过程
- 补抽来源在 edge 上标记 `source: "llm"`，审计链可区分，但 UI 不暴露
- 011 `SlotActionBar` 组件预留可扩展 props（内部接缝），便于未来调整补抽策略

---

## 4. 不引入 LLM 的部分

以下环节 **不应** 引入 LLM，需保持确定性：

| 环节 | 原因 |
|------|------|
| `evaluate(rule, facts)` 规则引擎 | 风险等级判定必须确定性、可审计、可复现 |
| `validate_coverage()` 覆盖校验 | AST 遍历是形式化契约，不容 LLM 模糊判断 |
| `docx_renderer` 报告渲染 | 模板驱动，无语义理解需求 |
| G1 三态映射 | `TRUE/FALSE/UNKNOWN` → 风险等级是确定性语义订正 |

---

## 5. 风险与权衡

| 风险 | 应对 |
|------|------|
| 本地 LLM 精度不如云端大模型 | slot schema 提供强约束（类似 function calling），降低对模型通用能力的依赖；可按模型能力调整 prompt 粒度 |
| LLM 补抽结果不可靠 | 补抽值必须经用户确认才进入最终报告（011 UI 的确认流程）；审计链标记 `source: "llm"` 以区分来源 |
| 增加报告生成延迟 | LLM 仅对 `missing_required` 槽位触发，非全量抽取；本地部署延迟可控（通常 < 5s） |
| 与现有离线优先原则的兼容性 | 本地 LLM 完全离线运行，`local_llm_enabled` 默认关，不影响现有管线 |

---

## 6. 实施建议

建议在 011（AST 前端 UI）完成后独立立项（012 或后续），分四步：

1. **基础集成**：`Settings` 扩展 + OpenAI 兼容客户端封装 + 门控逻辑
2. **模式 A — 覆盖缺口补抽**：`CoverageManifest.missing_required_slots` →
   batch prompt → 补充 edges → 重新校验。最小增量，立即可用。
3. **模式 B — 本体驱动段落级拆解**：
   - `OntologyEngine.get_data_properties_by_domain()` → 动态 slot schema 生成
   - 段落定位（章节匹配）→ LLM 结构化抽取 → edges
   - AST 模板运行时扩展机制（静态 JSON 模板 + 动态本体属性合成）
4. **前端交互**：011 UI 中增加「LLM 深度抽取」交互 + source_span 溯源高亮 +
   逐项确认流程

### 6.1 泛化路径

模式 B 成熟后，适配新的评估文档类型只需：

```
1. 本体 T-Box 新增文档类 + 数据属性定义（TTL 维护）
2. 配置文档类 → 章节映射规则（JSON 或 DB）
3. LLM 按本体 schema 自动抽取 → edges → 标准管线
```

无需为每种文档类型编写 Python finder / 正则。本体成为「抽取 schema 的唯一权威源」
（Constitution Principle I: 规范驱动开发），LLM 成为「按 schema 执行抽取的通用引擎」。
