# Feature Specification: AST 提取前端 UI

**Feature Branch**: `011-ast-extraction-ui`

**Created**: 2026-07-01

**Status**: Draft

**Input**: 提取 AST 配套前端 UI 功能——支持用户导入目标文档，提取 AST 并管理。提供独立页面展示 AST 模板树形结构与槽位填充状态，覆盖率仪表盘，历史报告管理，缺失素材引导补充。

**依赖**: 010-risk-report-generation（后端 AST 基础设施已全部完成 AST-1 至 AST-7）

## Clarifications

### Session 2026-07-01

- Q: 页面形态——AST 视图是 ExtractionDrawer 内的新 Tab 还是独立页面？→ A: **独立页面**（如 `/entities/extraction/{jobId}/ast`），空间更充裕。
- Q: 覆盖预览时机——抽取完成后自动触发覆盖校验还是用户主动触发？→ A: **抽取完成后自动触发**覆盖校验。
- Q: 缺失标记持久化——US-5 中「标记为不适用」是否需要持久化到 DB？→ A: **需要持久化到 DB**。
- Q: 已 dismissed 槽位在生成的 .docx 报告中如何渲染？→ A: 渲染为 **"N/A（不适用）"**，与数据缺失的「⚠ 待评估（数据缺失）」区分，保持 G1 语义可辨。
- Q: dismiss/undismiss 操作是否需要写入审计日志？→ A: **是**，dismiss 和 undismiss 均需记录审计日志（actor, job_id, slot_id, action, timestamp），满足 Constitution III 可追溯要求。
- Q: 011 是否应为未来 LLM 补抽预留 UI 接缝？→ A: **预留内部接缝**，缺失槽位操作区域设计为可扩展（props/slot pattern），但不实现 LLM 按钮。LLM 为内部实现细节，用户不需要感知——补抽结果直接体现为槽位填充状态的变化。
- Q: dismiss/undismiss 操作后覆盖率是否自动刷新？→ A: **自动刷新**。dismiss API 返回更新后的 CoverageManifest，前端即时刷新覆盖摘要和槽位状态，无需用户额外操作。
- Q: dismissed 槽位在 AST 树中的视觉标识？→ A: **删除线 + 灰色标签**，传达「已确认排除」，与 blank_optional 的纯灰色区分。

## 现状评估

### 后端（已完成，010 交付）

| 组件 | 位置 | 状态 |
|------|------|------|
| AST 模板 schema + JSON 默认模板 | `ast_template.py` + `templates/qs_a_020f05.json` | ✅ |
| 覆盖校验器 → `CoverageManifest` | `coverage_validator.py` | ✅ |
| `RiskReportGenerator.generate_with_coverage()` | `risk_report_generator.py` | ✅ |
| G1 三态订正（UNKNOWN → 待评估） | `risk_report_generator.py` | ✅ |
| POST `/jobs/{id}/risk-report` + 审计 + 持久化 | `extraction.py:826` | ✅ |
| GET `/jobs/{id}/risk-report` 下载历史报告 | `extraction.py:916` | ✅ |
| `GeneratedReport.rules_summary` 存全量 manifest | `extraction.py:883` | ✅ |

### 前端（缺口，本特性交付）

| 缺口 | 说明 |
|------|------|
| 无覆盖可视化 | 后端产出 `CoverageManifest` 但前端不展示 |
| 无 AST 结构预览 | 用户无法在生成前看到模板骨架与槽位填充状态 |
| 无历史报告管理 | `GeneratedReport` 已持久化但无 UI 查看/下载历史 |
| 无缺失素材引导 | 缺失 required 槽位无法引导用户补充或确认 |
| 入口深度耦合 | 报告功能嵌在 ExtractionDrawer 内，无独立管理视图 |

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 文档导入与 AST 自动提取（Priority: P1）

作为 QA 分析师，上传一份 CMC 文档后，系统自动运行关系抽取并展示 AST 槽位填充情况，以便一目了然看到哪些评估素材已就位、哪些缺失。

**Why this priority**: 核心价值入口——将抽取结果从「隐式关系列表」提升为「显式覆盖地图」，用户第一次看到 AST 就能理解报告完整度。

**Independent Test**: 上传 CMC 文档 → 等待抽取完成 → 系统自动跳转或引导进入 AST 页面 → 页面展示完整 AST 树和覆盖摘要。

**Acceptance Scenarios**:

1. **Given** 用户上传 CMC .docx 文件并完成抽取（status=done），**When** 抽取完成后，**Then** 系统自动触发覆盖校验，作业列表中出现「查看 AST」入口。
2. **Given** 用户点击「查看 AST」，**When** 页面加载完成，**Then** 展示三级 AST 树（Section → Group → Slot），每个 Slot 标注填充状态（5 种状态色编码）。
3. **Given** 抽取数据包含 DrugProduct、Equipment、SafetyRisk 等多种边，**When** AST 页面渲染，**Then** 对应 kind=extraction 的槽位显示 filled 状态，缺失的 required 槽位显示红色「⚠ 缺失」标记。
4. **Given** 文档不是 CMCReport 类型，**When** 抽取完成后，**Then** 作业列表中不出现「查看 AST」入口。

---

### User Story 2 - AST 覆盖率仪表盘（Priority: P1）

作为 QA 分析师，在生成报告前看到素材覆盖率摘要（如 18 个槽位中 13 个已填充、1 个缺失），以便决定是否需要补充数据后再生成。

**Why this priority**: 覆盖率是「可控无遗漏」承诺的前端可视化——用户需要量化信心。

**Independent Test**: 进入 AST 页面后，摘要卡片正确显示各状态计数，缺失时有警告。

**Acceptance Scenarios**:

1. **Given** 覆盖校验返回 total=18, filled=13, inferred=3, missing_required=1, manual=1，**When** AST 页面加载，**Then** 覆盖率摘要卡片展示各计数值，进度条显示填充比例。
2. **Given** missing_required > 0，**When** 摘要卡片渲染，**Then** 显示橙色/红色警告提示「存在 N 个必填素材缺失」。
3. **Given** missing_required = 0，**When** 摘要卡片渲染，**Then** 显示绿色「素材完备」标识，「生成报告」按钮可直接点击。
4. **Given** 覆盖率摘要已展示，**When** 用户点击「查看缺失详情」，**Then** 页面滚动/高亮到缺失槽位。

---

### User Story 3 - 槽位详情与源文档定位（Priority: P1）

作为 QA 分析师，点击某个槽位查看来源（哪条抽取边 / 哪条规则），并跳转到文档中对应原文位置，以便核实素材正确性。

**Why this priority**: 审计可追溯性的前端化——从模板槽位到原文的双向溯源。

**Independent Test**: 点击 kind=extraction 的 filled 槽位 → 展开详情 → 点击 source_ref → 文档预览高亮对应段落。

**Acceptance Scenarios**:

1. **Given** 用户点击一个 kind=extraction 的 filled 槽位，**When** 详情面板展开，**Then** 显示 slot_id、label、source kind=extraction、object_class_iri、实际值、source_ref。
2. **Given** 详情面板中 source_ref 非空，**When** 用户点击 source_ref 链接，**Then** 右侧文档预览区滚动到该段落并高亮（复用 WordViewer highlightRef 机制）。
3. **Given** 用户点击一个 kind=rule 的 inferred 槽位，**When** 详情面板展开，**Then** 显示规则 key（如 R-RA1）、规则描述、求值结果（TRUE/FALSE/UNKNOWN）、风险等级。
4. **Given** 用户点击一个 kind=manual 的槽位，**When** 详情面板展开，**Then** 显示「预留手工填写」标签，无来源信息。
5. **Given** 用户点击一个 missing_required 的槽位，**When** 详情面板展开，**Then** 显示缺失原因（数据缺失）和操作建议。

---

### User Story 4 - 覆盖预检后生成报告（Priority: P1）

作为 QA 分析师，在 AST 页面确认覆盖情况后点击「生成报告」，当存在缺失时弹出确认对话框，确认后生成并展示覆盖结果。

**Why this priority**: 将报告生成从「盲点击」升级为「知情生成」，缺失素材不被静默遗漏。

**Independent Test**: 有缺失时点击生成 → 弹确认对话框 → 确认后生成 → 展示覆盖结果 + 下载 .docx。

**Acceptance Scenarios**:

1. **Given** missing_required = 0，**When** 用户点击「生成报告」，**Then** 直接调用 POST API 生成报告并触发下载。
2. **Given** missing_required > 0，**When** 用户点击「生成报告」，**Then** 弹出确认对话框，列出缺失槽位 ID 和标签，提供「仍然生成」和「取消」两个选项。
3. **Given** 用户在对话框中点击「仍然生成」，**When** 生成完成，**Then** 触发 .docx 下载，覆盖摘要卡片刷新，报告历史列表新增一条记录。
4. **Given** 生成过程中，**When** API 调用进行中，**Then** 按钮显示「生成中...」并禁用，完成后恢复。

---

### User Story 5 - 历史报告管理（Priority: P2）

作为质量负责人，查看某个抽取作业的所有历史生成报告，包括每次的覆盖率、生成时间、操作人，以便追溯和对比。

**Why this priority**: 合规需要——QA 须能回溯任意历史报告的覆盖情况。

**Independent Test**: 生成多次报告后，进入 AST 页面的历史报告区域，验证列表完整、可下载、可查看覆盖。

**Acceptance Scenarios**:

1. **Given** 某作业已生成 3 次报告，**When** 用户进入该作业的 AST 页面，**Then** 历史报告区域显示 3 条记录，最新在前。
2. **Given** 历史报告列表中某条记录，**When** 用户点击「下载」，**Then** 浏览器下载该版本的 .docx 文件。
3. **Given** 历史报告列表中某条记录，**When** 用户点击「查看覆盖」，**Then** 展开该版本的 CoverageManifest 详情（从 rules_summary.coverage 读取）。
4. **Given** 各历史记录展示的字段，**Then** 包含：生成时间（精确到秒）、操作人、rules_fired_count、覆盖率摘要（filled/missing_required）。

---

### User Story 6 - 缺失素材补充与标记（Priority: P2）

作为 QA 分析师，当 AST 显示必填槽位缺失时，系统提示可能的补充方式，并支持「标记为不适用」持久化到 DB。

**Why this priority**: 闭合缺失处理环——从发现缺失到解决缺失的完整用户路径。

**Independent Test**: 发现缺失槽位 → 尝试补充 / 标记不适用 → 重新校验 → 缺失状态更新。

**Acceptance Scenarios**:

1. **Given** 某 kind=extraction 的槽位缺失，**When** 用户查看详情面板，**Then** 显示建议「重新标注」或「检查文档是否包含该信息」。
2. **Given** 某必填槽位确实不适用（文档内容不涉及），**When** 用户点击「标记为不适用」，**Then** 系统将该标记持久化到 DB，槽位状态变为 dismissed（不再计入 missing_required）。
3. **Given** 用户标记某槽位为不适用后，**When** dismiss API 返回，**Then** 前端即时刷新覆盖摘要——该槽位从 missing_required 中移除，覆盖率计数自动更新（无需手动触发重新校验）。
4. **Given** 用户误标记了某槽位，**When** 点击「撤销标记」，**Then** 恢复为 missing_required 状态。
5. **Given** 用户点击「重新标注」，**When** 重新标注完成后，**Then** AST 页面自动刷新覆盖状态。

---

### Edge Cases

- **非 CMCReport 文档**：AST 入口不出现；直接访问 AST 页面 URL 返回 404 或提示「该文档类型不支持风险评估」。
- **抽取失败的作业**：AST 入口不出现；无 edges 可校验。
- **并发生成**：多用户同时对同一作业生成报告，各自独立持久化，历史列表按时间排序。
- **dismissed 槽位报告渲染**：已 dismissed 的必填槽位在 .docx 报告中渲染为「N/A（不适用）」，区别于数据缺失的「⚠ 待评估（数据缺失）」。审阅者可一目了然区分「确认不适用」与「数据未抽到」。
- **模板变更**：AST 模板更新后重新校验时，历史覆盖清单保持生成时的快照，不回溯更新。
- **大量设备边**：设备表分组展示，折叠默认关闭，避免页面过长。

---

## Requirements *(mandatory)*

### Functional Requirements

#### 前端（核心交付）

- **FR-UI-001**: 新增独立页面 `/entities/extraction/[jobId]/ast`，展示 AST 模板树、覆盖仪表盘、文档预览、历史报告。
- **FR-UI-002**: AST 树形组件（`ASTTreeView`）展示 Section → Group → Slot 三级结构，每个 Slot 节点渲染 6 种填充状态的视觉标识（filled=绿, inferred=蓝, missing_required=红, blank_optional=灰, manual=黄, dismissed=灰+删除线）。
- **FR-UI-003**: 覆盖率摘要卡片（`CoverageSummaryCard`）展示总槽位数、各状态计数、进度条、缺失警告。
- **FR-UI-004**: 槽位详情面板（`SlotDetailPanel`）展示来源信息、实际值、source_ref 定位跳转。
- **FR-UI-005**: 文档预览区复用 `WordViewer`（source_type=word）或 `ExcelViewer`（source_type=excel），支持 highlightRef 联动。
- **FR-UI-006**: 「生成报告」按钮集成覆盖预检——missing_required > 0 时弹 AlertDialog 列出缺失槽位，需用户确认后才生成。
- **FR-UI-007**: 报告生成后刷新覆盖摘要 + 历史列表，并触发 .docx 下载。
- **FR-UI-008**: 历史报告列表（`ReportHistoryList`）展示每条 GeneratedReport 的时间、操作人、覆盖摘要，支持下载和查看历史覆盖。
- **FR-UI-009**: 缺失素材操作引导——kind=extraction 缺失建议「重新标注」，支持「标记为不适用」（持久化到 DB）和「撤销标记」。操作区域组件（`SlotActionBar`）设计为可扩展（props/slot pattern），为未来内部增强（如自动补抽）预留接缝，但不暴露实现细节给用户。
- **FR-UI-010**: 抽取完成后自动触发覆盖校验（调用新增 GET `/jobs/{id}/ast-coverage` 端点），作业列表中 CMCReport 类型的 done 作业显示「查看 AST」入口。
- **FR-UI-011**: 所有 UI 组件使用 shadcn/ui，无外部 CDN/字体依赖（离线部署）。
- **FR-UI-012**: AST 树默认折叠到 Group 层级，按需展开 Slot，避免信息过载。

#### 后端（配套补充）

- **FR-API-001**: 新增 `GET /api/extraction/jobs/{job_id}/ast-coverage` 端点——不生成报告，仅运行覆盖校验返回 CoverageManifest JSON（含 AST 模板结构 + 各槽位状态），用于前端预览。
- **FR-API-002**: 新增 `GET /api/extraction/jobs/{job_id}/reports` 端点——列出该作业的所有历史 GeneratedReport（含 rules_summary），按 created_at 倒序。
- **FR-API-003**: 新增 `SlotDismissal` 模型（job_id, slot_id, dismissed_by, dismissed_at），持久化「标记为不适用」操作。
- **FR-API-004**: 新增 `POST /api/extraction/jobs/{job_id}/ast-coverage/dismiss` 端点——标记某槽位为不适用，写入 DB，通过 `audit.append()` 记录审计日志（action=`slot.dismiss`, actor, job_id, slot_id），并返回更新后的 `CoverageManifest`（前端即时刷新，无需二次请求）。
- **FR-API-005**: 新增 `DELETE /api/extraction/jobs/{job_id}/ast-coverage/dismiss/{slot_id}` 端点——撤销不适用标记，通过 `audit.append()` 记录审计日志（action=`slot.undismiss`, actor, job_id, slot_id），并返回更新后的 `CoverageManifest`。
- **FR-API-006**: `GET /ast-coverage` 端点在计算覆盖时考虑已 dismissed 的槽位——将其从 missing_required 移除，状态标记为 `dismissed`。生成报告时 dismissed 槽位渲染为「N/A（不适用）」（区别于 missing_required 的「⚠ 待评估（数据缺失）」）。
- **FR-API-007**: 覆盖校验端点的 `CoverageManifest` 响应需包含 AST 模板的树形结构（Section/Group 名称与层级），使前端可直接渲染树形视图而无需单独加载模板。

### Key Entities

- **ReportTemplate**: AST 模板（已有）——声明报告骨架的语义树，Section → Group → Slot 三级结构。
- **Slot**: 模板叶子节点（已有）——slot_id, label, source(kind/绑定), required, on_missing。
- **CoverageManifest**: 覆盖清单（已有）——template_id, slots[SlotCoverage], 摘要计数。
- **SlotCoverage**: 单槽位覆盖记录（已有）——slot_id, status, source_ref, value。
- **GeneratedReport**: 已生成报告记录（已有）——job_id, file_path, rules_summary(含全量 manifest)。
- **SlotDismissal**: 不适用标记（新增）——job_id, slot_id, dismissed_by, dismissed_at。记录用户对缺失槽位的「不适用」判定。

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: AST 页面加载时间 < 2 秒（含覆盖校验 API 往返）。
- **SC-002**: 覆盖率摘要与 CoverageManifest 数据一致——前端显示的各状态计数与后端返回值完全匹配。
- **SC-003**: 槽位到源文档的跳转准确——点击 source_ref 后 WordViewer 高亮正确段落，零误定位。
- **SC-004**: 历史报告列表完整——每次生成都出现在列表中，可下载且覆盖清单可回放。
- **SC-005**: 缺失标记持久化——刷新页面后「不适用」标记保持，覆盖计数正确更新。
- **SC-006**: 非 CMCReport 文档不出现 AST 入口——类型守卫 100% 生效。
- **SC-007**: 全功能离线可用——断网环境下所有 UI 组件正常渲染和交互。
- **SC-008**: 覆盖预检对话框在 missing_required > 0 时 100% 触发——不存在绕过路径。

---

## Assumptions

- 后端 AST 基础设施（010 AST-1 至 AST-7）已全部完成且测试通过，不需修改核心逻辑。
- `CoverageManifest.to_dict()` 已包含足够信息供前端渲染槽位详情，无需额外扩展。
- WordViewer 的 `highlightRef` 机制支持从 AST 页面跨区域联动（槽位面板 → 文档预览）。
- `SlotDismissal` 是轻量模型，不涉及审批流（dismissed 即生效，可随时撤销）。
- 覆盖校验的计算开销可控（< 1s），适合抽取完成后自动触发。
- 前端路由使用 Next.js 14 App Router 的动态路由 `[jobId]`。
- 历史报告的 .docx 文件持久化在 `data/reports/` 目录，GET 端点直接返回 FileResponse。
