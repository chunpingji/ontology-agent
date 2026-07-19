# Feature Specification: Ontology-Guided Word Extraction

**Feature Branch**: 017-ontology-doc-extraction

**Created**: 2026-07-19

**Status**: Approved for implementation

**Input**: User description: "基于 docs/word报告内容识别导致的关系图谱识别问题.md，修复药物产品属性抽取、表头别名和宽表毒理适配，并以统一的本体属性和关系抽取技术消除 relation_extractor.py 的遗留硬编码。"

## Clarifications

### Session 2026-07-19

- Q: 本次应继续修补专用 finder，还是建立统一声明式路径？ → A: 建立统一的本体引导文档抽取路径；DrugProduct、Equipment、Residue 和宽表毒理作为首批迁移对象，不再新增类 IRI 分支、字段别名列表或专用 find 函数。
- Q: 版式别名属于领域本体还是抽取配置？ → A: 稳定语义同义词可进入本体标签；企业模板特有的章节、表头和字段别名属于版本化文档抽取 Profile。
- Q: 已显式选择 CMCReport 时，自动分类是否仍可阻断关系抽取？ → A: 不可。显式文档类型决定根关系 Schema；自动分类只用于无显式类型时的推断，或作为一致性告警。
- Q: 宽表毒理如何避免多行数据串行？ → A: 每个 API/试验项目行必须形成独立候选并保留联合标识和行级溯源；不得把多行 NOAEL、F1-F5、PDE、OEB 和来源无结构地平铺到一个端点。
- Q: 本次是否迁移合成路线、外部车间解析和 PDE/OEB 推理？ → A: 否。复合关系组装、外部主数据 Resolver、推理/冲突层和 mock 注入保持独立，本次只定义清晰边界并保证兼容。

## User Scenarios & Testing

### User Story 1 - 真实 Word 结构可被统一识别 (Priority: P1)

作为报告分析人员，我希望企业 Word 中由目录样式、普通加粗段落、编号标题和非一级标题构成的章节能够被分类、预览和关系抽取以同一规则识别，从而使原文已有内容不再因为版式差异而不可见。

**Why this priority**: HRS-1597 的核心字段并未缺失，而是被结构解析漏掉；若章节和表格块不可见，任何本体或属性映射都无法恢复数据。

**Independent Test**: 构造仅使用 toc 2、Normal + bold 和编号标题的 DOCX，解析后验证章节边界、标题、表格行列和来源位置均可用，并验证 CMC 全文信号可被分类器命中。

**Acceptance Scenarios**:

1. **Given** 核心章节使用 toc 2，**When** 解析文档，**Then** 该段落成为正确层级的章节标题，其后键值段落归入该章节。
2. **Given** 核心章节使用短的普通加粗编号段落，**When** 解析文档，**Then** 该段落成为语义章节边界而不是普通正文。
3. **Given** 文档没有标准一级 Heading，**When** 解析文档，**Then** 标题来自可识别的视觉标题或原始文件名，而不是持久化 UUID。
4. **Given** CMC 信号出现在第 12 段之后，**When** 自动分类，**Then** 这些信号仍参与打分且分类结果可解释。

---

### User Story 2 - 声明式抽取药物产品属性 (Priority: P1)

作为本体与抽取配置维护者，我希望 DrugProduct 的章节定位、字段别名、标识符优先级、类型转换和溯源由文档抽取 Profile 声明并由通用解释器执行，从而新增模板别名时无需修改 Python 类分支。

**Why this priority**: 药物产品的性状、制剂剂型和毒性属性是当前最明显的数据缺口，也是验证统一方案能否替代专用 DrugProduct finder 的最小纵向切片。

**Independent Test**: 使用声明了章节别名和属性源别名的 Profile 运行 HRS-1597 同版式夹具，验证端点为程序代号，基本性质与毒性布尔字段均映射到本体属性并带段落溯源。

**Acceptance Scenarios**:

1. **Given** “产品的基本性质”由 toc 或普通加粗标题表示，**When** 运行抽取，**Then** 从该锚点扫描到下一语义章节边界并提取其中键值属性。
2. **Given** 字段为“是否是细胞毒药物”，Profile 为该本体属性声明了多个源别名，**When** 抽取，**Then** 值被规范为布尔语义且不需要 Python 字符串分支。
3. **Given** NER 先命中泛词“药品”且原文存在 HRS-1597，**When** 选择端点标识，**Then** 程序代号优先，泛词不得覆盖它。
4. **Given** 新增一个满足通用 section_kv locator 的属性别名，**When** 仅更新 Profile 后重跑，**Then** 新属性被抽取且应用代码无需修改。

---

### User Story 3 - OR 表头别名与宽表毒理 (Priority: P1)

作为报告分析人员，我希望设备、残留物和毒理表能够通过声明式 all-of/any-of 表头签名及属性列别名解析，使不同企业模板的同义表头和每行毒理记录都能正确入图。

**Why this priority**: 当前所有锚点按 AND 解释，直接追加别名会使表格永远无法命中；宽表又会丢失或串联关键毒理数值，直接影响共线评估。

**Independent Test**: 使用“设备名称/设备编号/主体材质”、“中间体及成品”以及含 API、试验项目、NOAEL、F1-F5、PDE、OEB、来源的宽表夹具，验证通用解释器产生正确候选和行级溯源。

**Acceptance Scenarios**:

1. **Given** 设备表使用“设备名称 + 设备编号”，**When** Profile 声明 (设备规格 OR 设备名称) AND (匹配设备 OR 设备编号)，**Then** 表格被定位且设备属性正确映射。
2. **Given** 残留物标识列为“名称”“中间体及成品”或“中间体/成品”之一，**When** 抽取，**Then** 三种版式均生成等价端点且无需字段读取函数的新分支。
3. **Given** 宽表含两条不同 API/试验行，**When** 抽取，**Then** 每行的 NOAEL、F1-F5、PDE、OEB 和来源保持行内配对并有独立联合标识。
4. **Given** 正文仅出现“PDE：允许日暴露量”的定义句，**When** 数值属性校验，**Then** 该文本被拒绝，不形成 PDE 数值属性。

---

### User Story 4 - E6/E6b doc_pattern 运行时生效 (Priority: P2)

作为高级分析人员，我希望在本体映射界面声明的 doc_pattern 类绑定和属性绑定能够真正读取上传的 Word 文档，并输出与数据库/API 读取器同构的候选，从而让文档模板变化通过配置而非发版完成。

**Why this priority**: E6/E6b 已有模型、CRUD 和编辑界面，但运行时仍返回“读取器待接入”；补齐读取器才能让声明成为运行时权威来源。

**Independent Test**: 创建一个 doc_pattern 类绑定及多个属性绑定，上传 DOCX 发起声明式作业，验证候选、转换、标识、漂移信号和结构化来源引用。

**Acceptance Scenarios**:

1. **Given** 有效的 doc_pattern 绑定和 DOCX，**When** 创建声明式抽取作业，**Then** reader 按 locator 和属性绑定产出统一候选，而不是“暂不支持”。
2. **Given** 上传文件缺失，**When** 发起 doc_pattern 作业，**Then** API 明确拒绝请求，不创建不可执行作业。
3. **Given** 某属性别名在文档中完全缺失，**When** 抽取完成，**Then** 绑定健康度标记为 drift，其他已命中属性仍产出。
4. **Given** 值声明了 boolean/decimal/pattern 转换，**When** 原值不满足约束，**Then** 候选保留可审计说明，错误值不被误写为有效数值。

### Edge Cases

- 同一表格同时命中新旧表头别名时，按属性绑定优先级取首个非空值，不重复产生属性。
- 多行或合并表头必须形成稳定的规范化列名；空白/重复表头不得覆盖已提取的非空单元格。
- 短的加粗正文或强调句不得因无编号、句末为正文标点等情况被误判为章节。
- 文档类型显式值无效或在本体中不存在时，不得静默构造关系；应告警并回退自动分类。
- 文档损坏、非 DOCX 或 Profile 语法错误时，声明式作业优雅降级为零候选并记录原因，不影响其他抽取路径。
- 同一属性存在多候选值时，保留来源与转换说明，以确定性优先级选择，禁止无审计覆盖。

## Requirements

### Functional Requirements

**Unified Word structure**

- **FR-001**: 系统 MUST 为分类、标注预览和关系抽取提供同一套 Word 标题层级判定，至少识别标准 Heading、toc 1/2/3、大纲级别、带编号的短加粗段落和字号回退。
- **FR-002**: 统一文档结构 MUST 保留每个章节、段落、表格、行和列的可解析来源位置；抽取值 MUST 能回溯到对应块。
- **FR-003**: 系统 MUST 规范化多行/合并表头并保留原始单元格内容，供声明式表格选择器使用。
- **FR-004**: 无标准一级标题时，系统 MUST 优先使用视觉标题，其次使用调用方提供的原始文件名；MUST NOT 把持久化 UUID 当作业务标题。
- **FR-005**: 自动分类 MUST 扫描全文标题和语义信号，不得只扫描前 12 段；输出 MUST 保留命中信号和原始可解释分数。
- **FR-006**: 当调用方提供有效显式文档类型时，该类型 MUST 决定根关系 Schema；自动分类不得阻断抽取。

**Profile and interpreter**

- **FR-007**: 系统 MUST 定义受限、可校验的文档抽取 Profile，至少支持 locator、all-of/any-of 锚点、源别名、endpoint mode、标识符、转换、适用文档类和溯源。
- **FR-008**: 通用解释器 MUST 支持 section_kv、table_rows 和 table_singleton，且不得按具体 range 类 IRI 分派这些基础定位逻辑。
- **FR-009**: Profile 中的版式别名 MUST 与稳定语义标签分离；模板特有别名不得要求修改领域类或 Python 代码。
- **FR-010**: 属性映射 MUST 使用本体属性 IRI，并对 datatype、unit、controlled vocabulary 和 pattern 执行可审计校验/转换。
- **FR-011**: 所有通用候选 MUST 采用统一格式：目标类、属性、候选类型、分组/标识、结构化来源引用和转换说明。

**P0 migrations**

- **FR-012**: DrugProduct MUST 迁移到通用 section_kv Profile，支持章节/字段别名、编号清理、“是否是”语义归一和程序代号优先标识。
- **FR-013**: Equipment 与 Residue MUST 使用 OR 分组表头签名和声明式列别名；新增别名 MUST 不需要修改字段读取函数或新增 finder。
- **FR-014**: 宽表毒理 MUST 按行产生独立候选，以 API + 试验项目为联合标识，并保留 NOAEL、F1-F5、PDE、OEB、来源的行内配对。
- **FR-015**: PDE 仅在通过 decimal 和单位约束时成为数值属性；定义句和无数值文本 MUST 被拒绝。

**E6/E6b runtime and compatibility**

- **FR-016**: 声明式 Pipeline MUST 为 doc_pattern 接入 Word reader，并复用 E6 类绑定与 E6b 属性绑定输出候选。
- **FR-017**: doc_pattern 作业 MUST 接收并持久化上传 DOCX 的路径，缺失文件时 MUST 在作业创建前拒绝。
- **FR-018**: reader MUST 对完全未命中的源路径报告 drift，对损坏文档或无效 Profile 报告 degraded reason，并允许部分成功。
- **FR-019**: 现有 Excel、Word、数据库、API、NER 和未迁移复合 finder 路径 MUST 保持兼容；本次不得引入运行期外网依赖。
- **FR-020**: 合成路线关系组装、外部主数据解析、PDE/OEB 推理、冲突判定和 mock 数据 MUST 保持在基础属性解释器之外。
- **FR-021**: 实现 MUST 删除 DrugProduct 的类 IRI 专用分派，并使迁移后的基础属性/表格路径不再依赖新增类 IRI 分支；遗留复合策略须被明确隔离。

### Key Entities

- **Word Document IR**: 统一的文档结构视图，包含标题、章节、段落、表格、规范表头和块级来源位置。
- **Document Extraction Profile**: 某文档类/目标类的版本化定位与映射声明，描述 locator、锚点布尔组、属性源别名、端点模式、身份、转换和适用范围。
- **Property Binding**: 本体属性 IRI 到章节键或表格列别名的绑定，携带转换/校验、标识或标签角色。
- **Document Candidate**: 通用解释器输出的候选，包含目标类、抽取属性、标识/分组键、来源引用及转换说明。
- **Toxicology Row**: 宽表中的单个 API/试验记录，保持同一行的 NOAEL、F1-F5、PDE、OEB 和来源配对；后续可映射为 ToxicologyStudy 子实体。

## Success Criteria

### Measurable Outcomes

- **SC-001**: HRS-1597 同版式回归夹具中的“描述 → 药物产品”端点为 HRS-1597，且性状、制剂剂型和主要毒性属性均非空。
- **SC-002**: 在既定 CMC 信号集上，出现在任意正文位置的 11 个信号均参与分类，结果不再因第 12 段窗口截断而只得到 3 个信号。
- **SC-003**: 三种设备/残留物表头别名变体均达到 100% 夹具通过率，且新增变体仅修改 Profile/测试数据，不修改解释器代码。
- **SC-004**: 两条宽表毒理行的 NOAEL、F1-F5、PDE、OEB 和来源 100% 保持行内配对；PDE 定义句误入数值属性的数量为 0。
- **SC-005**: 100% 通用解释器抽取值带有章节或表格行列来源引用。
- **SC-006**: doc_pattern 声明式作业在有效绑定下产出候选，在缺字段时部分成功并标记 drift，在无效文档时优雅降级且不抛出未处理异常。
- **SC-007**: 新增一个满足既有 locator 的测试 range 类，仅通过 Profile/本体声明即可抽取，不增加任何类 IRI 专用 finder 或 dispatch 项。
- **SC-008**: 既有关系抽取、声明式 DB/API 抽取和 Word 标注回归测试全部通过。

## Assumptions

- HRS-1597/HRS-5678 原始文档可能不适合作为版本库夹具；允许使用最小化、脱敏的同版式 DOCX/合成结构夹具覆盖验收。
- 本次复用现有 Python、FastAPI、SQLAlchemy、python-docx、rdflib 和 pytest，不增加第三方依赖。
- E6 target 可承载受限 locator Profile，E6b source_path/transform_config 承载源别名和转换；若现有字段足以表达 P0，则不新增数据库列。
- 关系图谱在线预览继续消费现有 edge 形状；通用候选到 edge 的适配必须保持前端兼容。
- 宽表毒理在本次先保证按行候选与图谱子结构不串行；完整 ToxicologyStudy T-Box 发布如需新增权威类/关系，必须另走本体发布与三元组 diff 流程。
- 未迁移的复合 finder 暂时保留，但基础 locator 和属性映射不得再向其中增加模板别名。
