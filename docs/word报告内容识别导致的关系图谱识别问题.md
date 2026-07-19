# Word 报告内容识别导致的关系图谱识别问题

## 1. 问题概述

报告 `upload-8c4f2ff8-d88a-4f84-8e79-b6472174cc27` 的关系图谱中，CMC 报告显示“置信 6”，明显低于对照报告 `upload-cc5b07b0-6562-40d6-8573-d8c024c90e98` 的“置信 22”。同时，低分报告的 `描述 → 药物产品` 关系缺少大量原文中明确存在的基本性质，包括性状、制剂剂型和毒性属性。

经数据库、标注缓存、源 DOCX 结构及抽取代码交叉检查，确认该问题不是报告内容不足、模型未运行或服务降级，而是 **HRS-1597 文档的 Word 样式和表格结构不符合当前分类与关系抽取规则的固定假设**。

“置信 6”和属性漏抽是同一个版式兼容问题的两种表现，但分数 6 本身并不会触发属性过滤：该分数已经超过分类最低阈值，系统仍然加载了 CMCReport 的关系 Schema。

## 2. 报告与抽取作业映射

| 报告 | 源文件 | 抽取作业 |
|---|---|---|
| `upload-cc5b07b0-6562-40d6-8573-d8c024c90e98` | 原料药 HRS-5678 临床备样生产信息 - 冲突测试.docx | `a3d45afa-2e31-4824-bd4a-180983bbdd43` |
| `upload-8c4f2ff8-d88a-4f84-8e79-b6472174cc27` | HRS-1597原料临床备样生产信息表_--DP-C-PI-S-X2419_2601_04.docx | `b667320b-6b8d-4548-a8d3-f540c3567eaf` |

两个作业均处于 `reviewing` 状态，`error_message` 为空，候选数据没有 `degraded_reason`。两份标注缓存只出现相同的本体属性反查警告，没有文档分类失败、关系抽取异常或模型加载失败证据。

## 3. 结果对比

| 指标 | HRS-5678 对照报告 | HRS-1597 低分报告 |
|---|---:|---:|
| UI 显示的 CMC 分数 | 22 | 6 |
| 全文实际包含的 CMC 强信号 | 11/11 | 11/11 |
| 标注缓存中的 NER 三元组 | 36 | 67 |
| 顶层关系类型 | 11 | 5 |
| 顶层关系端点 | 47 | 5 |
| `描述 → 药物产品`属性 | 14 | 0 |

HRS-1597 原文实际明确包含至少 19 条产品基本性质，例如：

- `性状：类白色到白色粉末`
- `制剂剂型：口服片剂`
- `是否细胞毒药物：否`
- `是否是高致敏药物：否`
- `是否是激素类药物：否`
- `是否是青霉素类药物：否`
- `是否是高毒高活药物：否`
- 分子式、分子量、CAS、溶解性和理化性质等

但是最终关系端点为：

```text
描述 → 药物产品：“药品”
属性：0
```

这说明原文并未缺失，内容是在结构解析和关系后处理阶段丢失。

## 4. 根因分析

### 4.1 UI 中的“置信”实际是关键词命中分，不是概率

文档分类器对每个 CMC 强信号加 2 分，最低识别阈值为 3。其分类语料只包括：

```python
structure.title + structure.headings + structure.paragraphs[:12]
```

对应实现：

- `backend/app/services/extraction/document_classifier.py:40-42`
- `backend/app/services/extraction/document_classifier.py:63-66`
- `backend/app/services/extraction/document_classifier.py:92-103`

HRS-1597 在该窗口内只暴露了三个强信号：

- 原料药
- 备样生产
- 临床备样

因此得到 `3 × 2 = 6`。

但扫描全文时，HRS-1597 和对照报告一样，均命中全部 11 个 CMC 强信号：

- 原料药
- 备样生产
- 临床备样
- 工艺描述
- 合成路线
- 设备需求
- 设备清洗
- 共线评估
- 得量
- 收率
- 降解途径

若按全文计算，HRS-1597 同样会得到 22 分。因此分数差异反映的是分类扫描窗口和标题解析差异，不是文档的 CMC 内容可信度差异。

前端在 `frontend/src/components/extraction/relation-panel.tsx:413` 直接显示“置信 {score}”，容易使用户将未归一化的关键词分数误解为概率型置信度。

### 4.2 HRS-1597 的核心章节未使用标准 Heading 样式

对照报告的核心章节使用标准 Word 标题样式：

- `产品的基本性质`：`Heading 2`
- `工艺描述`：`Heading 3/4`
- `设备清洗方法`：`Heading 3`
- `设备需求`：`Heading 3`
- `降解途径`：`Heading 3`
- `日剂量`：`Heading 3`
- `产品毒性信息`：`Heading 3`

HRS-1597 中相同内容大量使用以下样式：

- `toc 2`
- `Normal + 加粗`
- 带编号的普通正文，例如 `4.2.3产品毒性信息`

典型情况如下：

| 文本 | HRS-1597 中的 Word 样式 |
|---|---|
| 产品的基本性质 | `toc 2` |
| 工艺 | `toc 2` |
| 工艺描述 | `toc 2` |
| 设备清洗方法 | `toc 2` |
| 设备需求 | `toc 2` |
| 4共线评估所需资料 | `toc 2` |
| 4.1降解途径 | `toc 2` |
| 4.2.1日剂量 | `Normal + 加粗` |
| 4.2.2产品NOAEL、F值 | `Normal + 加粗` |
| 4.2.3产品毒性信息 | `Normal + 加粗` |

关系抽取使用的薄结构解析器只调用 `_heading_level(style_name)` 识别标准 Heading：

- `backend/app/services/extraction/docx_structure.py:124`

HRS-1597 最终只识别出 2 个结构标题，而对照报告识别出 23 个。由于未识别到一级标题，解析器还将后端存储文件名的 UUID 当作标题：

- `backend/app/services/extraction/docx_structure.py:141`

这进一步削弱了分类输入。

### 4.3 `描述 → 药物产品`严格依赖结构化 Section

药物产品 finder 的处理逻辑为：

1. 查找标题包含“产品的基本性质”或“基本性质”的 Section；
2. 只扫描该 Section 的正文段落；
3. 仅处理 `键：值`形式的段落；
4. 将结果写入 DrugProduct 的数据属性。

对应实现：

- `backend/app/services/extraction/relation_extractor.py:257-282`

HRS-1597 的“产品的基本性质”只是 `toc 2` 普通段落，没有进入 `DocStructure.sections`，因此 `find_section()` 返回空，19 条基本性质完全没有被扫描，最终生成一个零属性 DrugProduct 端点。

### 4.4 药物产品名称被噪声 NER 结果覆盖

药物产品 finder 默认可以从标题和正文识别 `HRS-1597`，但会优先采用第一个被 NER 归类为 DrugProduct 的文本：

- `backend/app/services/extraction/relation_extractor.py:264-267`

HRS-1597 的第一个 DrugProduct NER 命中是包装语境中的泛词“药品”，还错误携带了包装袋规格。该结果覆盖了更可靠的程序代号 `HRS-1597`，造成图谱端点名称显示为“药品”。

### 4.5 关系规则依赖固定表头，真实报告使用了另一套字段名称

#### 设备关系

设备抽取要求同一表中同时存在：

- `设备规格`
- `匹配设备`

对应实现：

- `backend/app/services/extraction/relation_extractor.py:285-286`

HRS-1597 实际使用：

- `设备名称`
- `设备编号`
- `规格型号`
- `主体材质`

因此源文档虽然包含完整设备清单，`使用设备`关系仍为零。

#### 残留物关系

残留物 finder 能找到包含“物料酸碱性”和“溶解度”的表，但名称列只识别包含“名称”的表头。HRS-1597 使用的是“中间体及成品”，导致每一行的名称为空，整张表被跳过。

对应实现：

- `backend/app/services/extraction/relation_extractor.py:487-506`

#### 共线与毒理关系

共线 finder 只支持两列表：

- `参数`
- `数值`

对应实现：

- `backend/app/services/extraction/relation_extractor.py:549-589`

HRS-1597 的毒理表为宽表：

```text
活性成分(API) | 试验项目 | NOAEL | F1 | F2 | F3 | F4 | F5 | PDE (mg/天) | 来源 | OEB
```

其中两条实际 PDE 分别为 100 和 7.5 mg/天，但当前 finder 无法解析该版式。

此外，代码会全局搜索任意 `PDE：...`段落。HRS-1597 中存在解释性文字：

```text
PDE：允许日暴露量；
```

当前规则将“允许日暴露量”误当成 PDE 数值，形成了错误的共线评估属性。这不仅是漏抽问题，也属于假阳性数据质量风险。

### 4.6 NER 已运行，但没有成为关系抽取的有效兜底

HRS-1597 标注缓存产生了 67 条 NER 三元组，多于对照报告的 36 条，因此不能归因于 NER 模型没有运行。

但结果存在明显噪声，例如：

- 将 `C23H28N6O5S` 归为 OEB 5；
- 将 `Heptane` 归为水溶性；
- 将 `NOAEL` 归为高活性 API 或急性毒性；
- 重复产生大量通用“反应”实体。

更关键的是，关系抽取器除选择药物产品名称外，基本不利用 NER 三元组作为固定章节和固定表头规则失败后的兜底。因此即使 NER 识别到部分设备、毒性或药品实体，也不能恢复相应的关系和属性。

### 4.7 用户指定的 CMCReport 类型没有用于关系分类

报告上传时用户明确选择的 `doc_class_iri=CMCReport` 当前只用于约束 NER 候选类。代码明确说明“关系抽取仍用自动分类结果”：

- `backend/app/api/extraction.py:357-367`

当前 HRS-1597 的自动分类分数 6 仍超过最低阈值，所以尚未导致关系图完全为空；但对于同类版式更弱的报告，一旦低于 3 分，即使用户已经明确选择 CMCReport，也会直接失去全部关系抽取结果。

## 5. 本体属性对齐的次要问题

即使在对照报告中成功提取，部分 DrugProduct 属性仍以 `IRI=null` 的原文兜底形式展示，例如：

- 性状
- 溶解性
- 是否肿瘤药物
- 灭活方法

这说明本体中尚缺少相应 DrugProduct 数据属性，或者现有属性的 domain/label 无法匹配原文。

以下属性已有本体定义，但 HRS-1597 的原文措辞存在变体：

- `是否高致敏药物` 对应原文 `是否是高致敏药物`
- `是否激素类药物` 对应原文 `是否是激素类药物`
- `是否青霉素类药物` 对应原文 `是否是青霉素类药物`
- `是否高活性药物` 对应原文 `是否是高毒高活药物`

当前 exact-label 映射缺少标签归一化和同义词处理。即便解决 Section 识别问题，这些变体仍可能只能作为未对齐原文属性进入图谱。

## 6. 为什么现有测试没有发现问题

当前关系抽取单元测试使用人工构造的规范结构，章节均显式构造成 `DocSection`，表头也严格使用：

- `设备规格 + 匹配设备`
- `名称 + 物料酸碱性 + 溶解度`
- `参数 + 数值`

对应测试夹具：

- `backend/tests/test_extraction/test_relation_extraction.py:58-130`

端到端验证脚本同样使用结构规范的 HRS-1234 样例，并要求 11 类核心关系全部非空。现有测试证明了标准模板路径可用，但没有覆盖企业真实报告中的 `toc` 样式、普通加粗标题、表头别名和宽表毒理格式。

## 7. 修复建议与优先级

> **方案更新**：下列 P0 是需要解决的问题范围，不应通过继续向 `relation_extractor.py` 增加类 IRI 特判、字段名列表或专用 `find_*` 函数实现。药物产品、设备/残留物表头和宽表毒理应作为统一本体引导文档抽取的首批迁移对象，具体架构见第 9 节。

### P0：统一和增强 Word 结构解析

1. 分类、在线预览和关系抽取共用同一份章节树，避免多套标题判定规则漂移。
2. 除标准 Heading 外，识别：
   - `toc 1/2/3`；
   - 带章节编号的短段落；
   - `Normal + 加粗`标题；
   - 字号、段前段后和大纲级别等组合特征。
3. 无一级 Heading 时，从文档前部视觉标题或原始 `source_filename` 推导标题，不使用存储 UUID。
4. 分类器采用受控全文扫描，或至少扫描全文标题与语义锚点，不只依赖前 12 段。

### P0：修复药物产品属性抽取

以下行为应由 DrugProduct 的文档抽取 Profile 声明，并由通用 `section_kv` 解释器执行，而不是固化到 `find_drug_product()`：

1. `find_section()` 找不到标准 Section 时，回退到普通段落语义锚点。
2. 从“产品的基本性质”锚点扫描至下一个语义章节边界。
3. 药物产品名称优先采用符合程序代号正则的 `HRS-\d+`，泛词“药品”“本品”不得覆盖程序代号。
4. 对属性名执行编号清理、`是否是 → 是否`归一化和同义词映射。

### P0：增加表头别名和宽表毒理适配

以下别名、宽表列映射和数值约束应进入可版本化的 `doc_pattern` Profile；禁止继续在 `_row_get()`、`find_equipment()`、`find_residue()` 或 `find_shared_line()` 中追加字符串分支：

1. 设备列别名：
   - `设备名称/设备规格`
   - `设备编号/匹配设备`
   - `主体材质/材质`
2. 残留物名称列别名：
   - `名称`
   - `中间体及成品`
   - `中间体/成品`
3. 增加宽表毒理解析：按每个 API/试验行提取 NOAEL、F1-F5、PDE、OEB 和来源。
4. PDE 必须通过数值和单位校验，禁止将定义句“允许日暴露量”作为数值入图。

### P1：让明确的文档类型贯穿关系抽取

报告中心上传时用户已经选择 CMCReport，应将该类型作为关系抽取的可信约束或明确先验，而不只用于 NER 候选类。自动分类结果可作为一致性校验和告警，不应在已明确指定类型时成为唯一门控。

### P1：补齐本体属性和关系兜底

1. 补充或明确建模性状、溶解性、肿瘤属性和灭活方法。
2. 为常见毒性布尔字段增加同义标签。
3. 固定规则失败时，可使用带章节位置和表格列上下文的 NER 三元组作为候选兜底，但需设置类型、上下文和数值校验，避免直接物化噪声。

### P1：增加真实报告回归测试

将 HRS-1597 文档或脱敏后的同版式样例加入回归集，至少断言：

- CMC 全文信号能够完整识别；
- `描述 → 药物产品`端点为 `HRS-1597`；
- 性状和制剂剂型非空；
- 主要毒性布尔属性非空；
- 设备、残留物、降解途径关系非空；
- 宽表 NOAEL/PDE 能正确解析；
- PDE 定义句不会形成数值属性。

## 8. 最终判断

本问题的主要根因是 **结构解析与真实企业 Word 模板不兼容**，其次是 **表头和毒理版式规则过窄**；本体属性对齐不足和 NER 噪声属于次要放大因素。

因此不应将该问题归因于报告质量差或模型能力不足。优先修复章节识别、分类扫描窗口和表格别名后，HRS-1597 原文中已经存在的大部分基本性质和关系可以通过确定性规则恢复。

## 9. 统一的本体引导属性和关系抽取方案

### 9.1 方案结论

可以并且应当使用统一的本体引导属性和关系抽取技术解决本问题，但不能仅依靠 OWL 类、数据属性或 `skos:altLabel` 自动完成。统一方案采用三层分工：

> 本体定义“抽什么”，文档抽取 Profile 定义“从哪里、怎样抽”，通用解释器负责执行、校验和溯源。

OWL 本体负责稳定语义；章节位置、表格签名、列别名和行分组属于易变的源版式知识，应存放在抽取 Profile，避免污染领域本体，也避免再次散落到 Python。

### 9.2 现有能力与遗留断点

系统已经具备部分统一骨架：

1. `OntologyEngine.get_relation_schema()` 可以从文档类遍历对象属性、range 类、数据属性及多跳子关系。
2. TTL 已声明 `extractionMethod`、`extractionAnchor` 和 `extractionPattern`。
3. `relation_extractor.py` 已有 `table_scan`、`section_kv`、`section_paragraph` 通用策略。
4. E6 `OntologyClassMapping` 和 E6b `OntologyPropertyBinding` 已支持 `doc_pattern`、属性绑定、`source_path`、值转换、标识符和嵌套对象。

遗留断点是：

- 已知 range 类仍优先进入 `find_drug_product/find_equipment/find_residue/...`；
- `SynthesisRoute`、`SharedLineAssessmentData` 和 `ClinicalSampleProductionPlan` 仍由 `_RANGE_OVERRIDES` 包装遗留 finder；
- `doc_pattern` 只有模型、CRUD 和编辑器，声明式 Pipeline 仍返回“读取器待接入”。

因此当前实际是“本体驱动关系骨架 + Python 硬编码端点定位”的混合模式；去硬编码重构只完成了前一半。

### 9.3 目标架构

```text
本体 T-Box
类、关系、domain/range、datatype、unit、基数、受控词表
        +
文档抽取 Profile（复用并扩展 E6/E6b doc_pattern）
章节锚点、表格别名、属性映射、实体标识、分组、转换、适用模板
        ↓ 编译
DOCX → 统一文档 IR → 通用定位器 → 实体候选 → 本体校验 → 关系图谱
```

通用解释器仅提供有限、稳定的 locator：

- `section_kv`：章节内键值属性；
- `table_rows`：表格逐行实体；
- `table_singleton`：整表聚合实体；
- `paragraph_pattern`：正文正则；
- `ner_entity`：NER 候选兜底；
- `external_resolver`：外部主数据解析。

解释器统一负责类型/单位转换、受控词表、候选评分、domain/range 校验和块级溯源，不再按 `DrugProduct`、`Equipment` 等具体类 IRI 分派专用函数。

### 9.4 前置 P0：统一 Word 文档 IR

本体声明无法弥补底层结构不可见，必须先让分类、预览和关系抽取共用同一文档 IR：

1. 识别标准 Heading、`toc 1/2/3`、章节编号、`Normal + 加粗`和字号/大纲级别组合标题。
2. 统一多行表头、合并单元格、嵌套表和表题解析。
3. 每个 block 保留 section、table、row、column 和 offset 溯源。
4. 无一级标题时使用视觉标题或原始 `source_filename`，不能使用存储 UUID。
5. 分类采用受控全文扫描，不只依赖前 12 段。

### 9.5 P0：实现 E6/E6b `doc_pattern` reader

在声明式 Pipeline 中接入文档 reader，使其与 DB/API reader 输出同一种候选：`target_class_iri`、`extracted_properties`、`candidate_kind`、`group_key`、`source_ref` 和 transform notes。

文档 reader 消费根文档类型、`get_relation_schema()` 关系计划、E6/E6b Profile 和统一文档 IR。报告中心显式选择的 CMCReport 应决定根关系 Schema；自动分类只作为一致性告警或无显式类型时的推断。

### 9.6 DrugProduct 声明示例

```text
文档类：CMCReport
关系：describes
目标类：DrugProduct
locator：section_kv
章节 match-any：产品的基本性质 | 产品基本信息 | 基本性质
端点模式：singleton
标识符：优先匹配 HRS-\d+
```

属性绑定示例：

```text
dosageForm
  source aliases = [制剂剂型, 剂型]

isCytotoxic
  source aliases = [是否细胞毒药物, 是否是细胞毒药物]
  transform = boolean

isHighlyActive
  source aliases = [是否高活性药物, 是否是高毒高活药物]
  transform = boolean
```

迁移后，新增字段名或章节别名只修改 Profile/语义同义词，不再修改 `find_drug_product()`。

### 9.7 表头别名必须支持 OR 组合

当前 `extractionAnchor` 是扁平列表，`find_table(*anchors)` 将所有 anchor 解释为 AND。直接追加“设备名称”“设备编号”等新值，会错误要求一张表同时具有全部新旧表头。

Profile 应支持布尔组合：

```text
table signature:
  all-of:
    - any-of: [设备规格, 设备名称]
    - any-of: [匹配设备, 设备编号]
```

属性绑定负责列映射：

```text
equipmentName ← [设备名称, 设备规格]
equipmentID   ← [设备编号, 匹配设备]
material      ← [主体材质, 材质]
model         ← [规格型号]
```

残留物的“名称”“中间体及成品”“中间体/成品”同样应作为标识属性的源别名，不再追加到 `_row_get()`。

### 9.8 宽表毒理声明

表格 Profile 支持多版式 `match-any`：

```text
table variants:
  - all-of: [参数, 数值]
  - all-of: [活性成分, 试验项目, NOAEL, PDE]
```

列绑定：

```text
活性成分(API) → API/产品标识
试验项目      → studyType
NOAEL         → noael
F1...F5       → uncertainty factors
PDE (mg/天)   → pde_mg_per_day
来源          → sourceReference
OEB           → oebBand
```

Profile 还需声明 `row/singleton/group_by`端点模式、API + 试验项目联合标识、数值单位、空值策略和冲突优先级。PDE 必须通过 decimal + unit 校验；“PDE：允许日暴露量”不满足类型约束，应被拒绝。

毒理本体形状也应先明确：两行试验的 NOAEL、F1-F5、PDE 和来源必须保持行内配对。优先考虑每行形成 ToxicologyStudy 子实体，再由 SharedLineAssessmentData 关联，避免把多组同名属性无结构地平铺到一个端点。

### 9.9 E6/E6b 需要补充的声明能力

| 能力 | 用途 |
|---|---|
| locator 类型 | section、table、paragraph、NER |
| anchor 布尔组 | `all-of`、`any-of` |
| source aliases | 属性名、表头和章节别名 |
| endpoint mode | singleton、row、group_by |
| composite identity | 多列联合标识 |
| relationship path | 根文档到目标实体的关系路径 |
| repeat/cardinality | 重复值、对象数量和缺失策略 |
| transform/validation | boolean、decimal、unit、vocab、pattern |
| applicability | 文档类、模板、来源、版本和优先级 |
| provenance | section/table/row/column/offset |

可以将 `source_path` 演进为受限文档选择器 DSL，或增加结构化 `selector_config`；结构化 JSON 更容易校验、编辑和做版本 diff，优先推荐。稳定语义同义词使用 `skos:altLabel`，企业模板特有别名保留在 Profile。

### 9.10 统一方案边界

以下逻辑不应继续混在属性 finder 中，也不能简化为表头别名：

- 合成路线的步骤顺序、步骤—设备—中间体分组；
- 生产车间外部主数据解析；
- PDE 推导、OEB 推断和原文冲突判定；
- mock 演示数据注入。

建议拆为通用事实候选抽取器、声明式关系组装器、少量命名 Resolver 插件以及独立推理/冲突层。mock 必须与生产抽取隔离。“统一”是统一 Schema、选择器、候选格式、校验和溯源，不是形成新的巨型通用函数。

### 9.11 迁移顺序

1. 建立统一 Word IR，以 HRS-5678/HRS-1597 验证结构。
2. 扩展 E6/E6b 文档选择器，实现 `doc_pattern` reader。
3. 首批迁移 DrugProduct `section_kv` 和宽表毒理。
4. shadow mode 同时运行新旧抽取器，对比实体、属性、关系和溯源。
5. 再迁移 Equipment、Residue、StorageCondition、Risk 和 CleaningProcess。
6. 将 synthesis route 等复合逻辑逐步拆为关系组装和 Resolver。
7. 黄金基线稳定后删除已迁移类对应的 `find_*`、`_RANGE_OVERRIDES` 和 `_METHOD_STRATEGIES` 类 IRI 特判。

每个作业必须固定本体 release 和抽取 Profile 版本，保证历史结果可复现。

### 9.12 验收标准

- HRS-1597 的 `描述 → 药物产品`端点为 `HRS-1597`；
- 性状、制剂剂型和主要毒性属性非空；
- 设备、残留物和降解途径关系非空；
- 两条毒理记录的 NOAEL、F1-F5、PDE 和来源不串行；
- PDE 定义句不会形成数值属性；
- 每个值均可追溯到章节或表格行列；
- 新增表头/字段别名只修改 Profile，不修改 Python；
- 新增满足通用 locator 的 range 类无需增加专用 finder；
- domain/range、datatype、unit 和 controlled vocabulary 由本体统一校验；
- 新旧引擎 shadow diff 可解释、可审计。

### 9.13 更新后的最终判断

本问题的直接根因是结构解析与企业 Word 模板不兼容，深层原因是去硬编码重构只完成了“关系 Schema 遍历”，没有完成“端点和属性定位声明化”。

应复用现有 `get_relation_schema()`、TTL 抽取注解和 E6/E6b 声明层，补齐统一文档 IR、文档选择器语义和 `doc_pattern` reader，而不是继续给 `relation_extractor.py` 增加字段别名及专用分支。
