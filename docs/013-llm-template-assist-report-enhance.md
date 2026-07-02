# 013: LLM 模板设计辅助 + 报告生成增强

> 状态：规划中
> 日期：2026-07-02
> 前置特性：012-ast-template-llm-pipeline（模板管理 + LLM 补抽）
> 前提条件：本地 LLM 可用（OpenAI 兼容端点）

---

## 1. 背景

012 完成后的现状：

| 能力 | 状态 | 局限 |
|------|------|------|
| 模板 CRUD | ✅ 完成 | 模板只能手动编写 JSON 或在 slot editor 中逐个添加 |
| LLM 补抽 | ✅ 完成 | 仅在 AST 覆盖视图中补填缺失 slot 值 |
| 本体展开 | ✅ 完成 | 运行时动态补 slot，不回写模板 |
| 报告生成 | ✅ 完成 | 纯规则 + 纯抽取，LLM 补抽结果**未流入**报告 |

两个瓶颈：

**瓶颈一：模板设计门槛高**。用户需要理解 JSON schema、slot_id 命名规范、source
绑定语法才能创建模板。实际场景中，用户手里有示例文档，期望系统能「读」文档结构后
建议应有哪些 slot——当前完全不支持。

**瓶颈二：报告内容断层**。LLM 在 AST 覆盖视图中成功补填的值，生成报告时被丢弃；
报告中对应位置显示「待评估（数据缺失）」。用户看到覆盖视图里有值、报告里却没有，
体验割裂。更进一步，即使补填了值，报告中的文案（如风险描述、控制措施）仍是规则
consequent 中的模板文字，无法适配具体文档的语境和术语风格。

---

## 2. 特性 A：模板设计辅助（suggest-slots）

### 2.1 用户故事

> 作为 senior_analyst，我上传一份示例文档（或指定已抽取的 job），系统用 LLM 分析
> 文档的段落结构和内容，返回建议的 section/group/slot 列表。我在 slot editor 中
> 预览建议结果，选择性采纳到当前模板中。

### 2.2 API 设计

```
POST /api/ast-templates/suggest-slots
```

**请求体**：

```python
class SuggestSlotsRequest(BaseModel):
    # 二选一：上传文档 或 引用已有 job
    job_id: UUID | None = None           # 引用已有抽取 job（复用已解析文本）
    document_text: str | None = None     # 直接传入文档文本（前端上传后提取）

    # 可选：现有模板作为参考（避免建议重复 slot）
    template_id: UUID | None = None      # 引用已有模板
    template_json: dict | None = None    # 直接传入模板 JSON

    # 控制参数
    focus_sections: list[str] | None = None  # 仅分析指定段落标题（可选）
    language: str = "zh"                     # 输出语言
```

**响应体**：

```python
class SuggestedSlot(BaseModel):
    slot_id: str                   # 建议的 slot_id（如 "subject.active_ingredient"）
    label: str                     # 人类可读标签
    group_title: str               # 建议归属的 group
    section_title: str             # 建议归属的 section
    source_kind: str               # 建议的 source 类型（extraction / llm_extraction / manual）
    source_hint: dict | None       # source 绑定建议（如 object_class_iri_contains）
    required: bool                 # 是否建议为必填
    evidence: str                  # 文档中支撑此建议的原文片段
    confidence: float              # 置信度 0.0-1.0
    reason: str                    # 建议理由

class SuggestSlotsResponse(BaseModel):
    sections: list[SuggestedSection]   # 建议的完整结构
    total_suggested: int
    skipped_existing: int              # 与现有模板重复而跳过的数量
    document_summary: str              # LLM 对文档结构的概述
```

**权限**：`require_role(ROLE_SENIOR_ANALYST)` — 与模板编辑同级。

### 2.3 LLM Prompt 策略

分两轮调用：

**第一轮：文档结构分析**

```
你是一份 GMP/CMC 合规文档的结构分析助手。

## 文档内容
{document_text}

## 任务
分析文档的章节结构，识别每个章节包含的关键信息点。输出 JSON：
{
  "document_type": "风险评估 / 稳定性研究 / 清洁验证 / ...",
  "sections": [
    {
      "title": "章节标题",
      "content_summary": "内容概述",
      "information_points": [
        {"name": "信息点名称", "type": "text/number/date/enum",
         "example_value": "文档中的示例值",
         "source_span": "原文片段"}
      ]
    }
  ]
}
```

**第二轮：Slot 映射生成**

将第一轮结果 + 可选的现有模板 + 本体类列表一起输入：

```
你是一个报告模板设计助手。

## 文档结构分析结果
{round_1_result}

## 现有模板结构（如有）
{existing_template_structure}

## 本体已有的类和属性
{ontology_classes_and_properties}

## 任务
基于文档结构，为报告模板设计 slot 定义。规则：
1. 每个信息点映射为一个 slot
2. slot_id 使用 group_id.field_name 格式（英文小写下划线）
3. 如果信息点对应本体中已有的类/属性，设置 source_kind 为 extraction 并绑定 IRI
4. 如果信息点无本体对应但可从文档提取，设置 source_kind 为 llm_extraction
5. 如果是需要人工判断的定性内容，设置 source_kind 为 manual
6. 跳过现有模板中已有的 slot（按语义去重，不仅按 slot_id）
7. required = true 仅用于 GMP 合规必须填写的字段

输出 JSON schema 格式的完整建议列表。
```

### 2.4 前端交互

模板编辑页面新增「AI 分析」按钮，点击后打开全屏 Drawer（右侧滑出），采用**左右
分栏联动**布局：

```
模板编辑页面（底层）                        Drawer（覆盖层，右侧滑出）
┌──────────────────────┐  ┌────────────────────────────────────────────────────────────────┐
│  AST 模板管理         │  │  AI 模板分析                                          [✕ 关闭] │
│                       │  ├──────────────────────────┬─────────────────────────────────────┤
│  (被 Drawer 遮挡)     │  │  📄 示例文档              │  🌳 Slot 建议树                     │
│                       │  │                          │                                     │
│                       │  │  [上传文档 ▾] [从Job选择] │  建议 12 个 slot（跳过 8 个重复）    │
│                       │  │  ─────────────────────── │  ─────────────────────────────────  │
│                       │  │                          │                                     │
│                       │  │  1. 风险评估对象          │  ▼ Section: 风险评估对象             │
│                       │  │                          │    ☑ active_ingredient 活性成分 0.95 │
│                       │  │  本品主药为【盐酸氨溴索】 │      ← 高亮对应                     │
│                       │  │  片，规格 30mg/片，批准   │    ☑ therapeutic_class 治疗类别 0.87 │
│                       │  │  文号国药准字H20000001。  │    ☐ shelf_life 有效期 0.62          │
│                       │  │  本品为化学药品 4 类，    │                                     │
│                       │  │  采用湿法制粒压片工艺。   │  ▶ Section: 生产设备 (3 slots)       │
│                       │  │                          │  ▶ Section: 风险评估 (5 slots)       │
│                       │  │  2. 评估小组              │                                     │
│                       │  │  ...                     │                                     │
│                       │  │                          │                                     │
│                       │  │                          │  ─────────────────────────────────  │
│                       │  │                          │  [全选] [全不选]   [采纳选中 →]     │
│                       │  ├──────────────────────────┴─────────────────────────────────────┤
│                       │  │  ℹ 替换示例文档将重新分析全部 slot                              │
└──────────────────────┘  └────────────────────────────────────────────────────────────────┘
```

**左右联动行为**：

| 用户操作 | 左栏（文档） | 右栏（slot 树） |
|---------|-------------|----------------|
| 点击右栏某个 slot | 滚动到该 slot 的 evidence 来源段落，高亮原文片段 | 选中态 |
| 点击左栏某个段落 | 选中态 | 展开并滚动到该段落对应的 section，闪烁提示 |
| Hover 右栏 slot | 左栏对应原文片段显示下划线 | — |
| 上传/替换示例文档 | 清空 → 加载新文档 | **全部清空 → 重新调用 suggest-slots → 重建 slot 树** |

**关键设计决策**：

- **替换文档 = 全量重分析**：不做增量 diff，因为不同文档的章节结构可能完全不同，
  增量合并语义复杂且容易出错。替换文档后右栏 slot 树完全重建，用户之前的勾选状态
  清空，需重新审核。用 confirm dialog 提示「替换文档将清空当前建议，是否继续？」
- **Drawer 而非独立页面**：保持模板编辑的上下文（用户可关闭 Drawer 回到 slot
  editor 查看现有 slot），采纳后 Drawer 自动关闭、slot editor 刷新
- **分栏比例**：左 40% / 右 60%，左栏文档可滚动，右栏 slot 树可独立滚动

**交互流程**：

1. 用户在模板编辑页面点击「AI 分析」→ 打开 Drawer
2. 上传示例文档（或从已有 Job 选择）→ 左栏显示文档内容
3. 自动调用 `POST /suggest-slots` → 右栏显示 slot 建议树（loading 期间显示骨架屏）
4. 用户点击 slot ↔ 左栏高亮对应原文（联动）
5. 用户勾选/取消 slot → 点击「采纳选中」
6. Drawer 关闭 → 选中的 slot 合并到模板 JSON → slot editor 刷新（新 slot 高亮）
7. 用户在 slot editor 中可继续微调 → 保存 `PUT /api/ast-templates/{id}`
8. 如需重新分析：重新打开 Drawer → 上传另一份文档 → 全量重建

### 2.5 与本体的关系

LLM 建议的 slot **不回写本体**（参见之前的讨论）。流向：

```
示例文档 → LLM 分析 → 建议 slot 列表
                          ↓
              用户审核采纳 → 保存到模板 JSON (DB)
                                ↓
                    slot.source 引用本体 IRI（如有匹配）
                    或 source_kind=manual（无本体对应）
```

本体是只读参考，不被模板设计流程修改。

---

## 3. 特性 B：报告生成 LLM 增强

### 3.1 用户故事

> 作为 senior_analyst，我生成 DOCX 报告时，LLM 补抽的 slot 值应体现在报告中（而非
> 显示「待评估」）。对于风险描述、控制措施等叙述性内容，LLM 应基于抽取到的事实，
> 生成与模板风格语言一致的正式报告文案。

### 3.2 当前管线 vs 目标管线

**当前**（012 完成后）：

```
edges → RiskReportGenerator.generate_with_coverage()
           │
           ├─ _build_subject_description()     ← 纯字符串拼接
           ├─ _build_equipment_tables()         ← 纯数据映射
           ├─ _evaluate_rules()                 ← 纯规则引擎
           └─ validate_coverage()               ← 纯 AST 遍历
                │
                ▼
         RiskReport (dataclass) → docx_renderer → DOCX
         
         LLM 补抽的值 ──────────────────────── ✗ 未参与
```

**目标**：

```
edges → RiskReportGenerator.generate_with_coverage()
           │
           ├─ _build_subject_description()
           ├─ _build_equipment_tables()
           ├─ _evaluate_rules()
           └─ validate_coverage()
                │
                ▼
         CoverageManifest (含 missing slots)
                │
                ├─ local_llm_enabled? ─── YES ──┐
                │                                │
                │              fill_coverage_gaps()  ← 已有（012）
                │                                │
                │              merge LLM values into RiskReport  ← 新增
                │                                │
                │              generate_narrative_content()  ← 新增
                │                                │
                ▼                                ▼
         RiskReport (enriched) → docx_renderer → DOCX
                                      │
                                      ├─ LLM 补填的值 → 直接渲染
                                      └─ LLM 生成的叙述 → 标注来源
```

### 3.3 两层增强

#### 层 1：LLM 补抽值流入报告（数据层）

将 `fill_coverage_gaps` 的结果合并到 `RiskReport` 数据类。

**改造点**：`RiskReportGenerator.generate_with_coverage()`

```python
def generate_with_coverage(self, edges, source_filename, dismissed_slot_ids=None):
    facts = edges_to_facts(edges)
    rules = self._load_rules()
    pre_rows = self._evaluate_rules(rules, facts)
    post_rows = self._evaluate_post_control(rules, facts, pre_rows)

    manifest = validate_coverage(self._template, edges, rules, facts,
                                  dismissed_slot_ids=dismissed_slot_ids)

    # ── 新增：LLM 补抽 ──────────────────────────────────────────
    llm_values = {}
    if settings.local_llm_enabled and manifest.missing_required > 0:
        llm_results = fill_coverage_gaps(manifest, document_path, self._template)
        for slot_id, value in llm_results.items():
            llm_values[slot_id] = value
            # 同步更新 manifest 中对应 slot 的状态
            for sc in manifest.slots:
                if sc.slot_id == slot_id and sc.status == MISSING_REQUIRED:
                    sc.status = FILLED
                    sc.value = value
                    sc.is_llm_sourced = True
    # ────────────────────────────────────────────────────────────

    subject = self._build_subject_description(edges, source_filename, llm_values)
    equipment_tables = self._build_equipment_tables(edges)

    report = RiskReport(
        subject_description=subject,
        equipment_tables=equipment_tables,
        assessment_rows=post_rows,
        llm_supplements=llm_values,  # 新增字段：传递给渲染器
    )
    return report, manifest
```

**渲染器改造**：`docx_renderer.py`

```python
def _add_section_one(doc, report):
    # ... 现有逻辑 ...
    
    # 对 LLM 补填的值，渲染时附加来源标注
    if report.llm_supplements:
        for slot_id, value in report.llm_supplements.items():
            # 在对应位置渲染值，并以脚注标注「自动提取」
            pass
```

#### 层 2：LLM 生成风格一致的叙述内容（内容层）

对于叙述性字段（风险描述、控制措施、结论），LLM 基于抽取到的事实生成与模板风格
一致的正式文案。

**新增模块**：

```python
# backend/app/services/reporting/narrative_generator.py

class NarrativeGenerator:
    """基于抽取事实生成模板风格一致的报告叙述内容。"""

    def generate_subject_narrative(
        self, facts: Facts, template: ReportTemplate, style_examples: list[str]
    ) -> str:
        """生成「风险评估对象」段落。"""

    def generate_assessment_narrative(
        self, risk_row: RiskRow, facts: Facts, style_examples: list[str]
    ) -> str:
        """为单个风险维度生成评估叙述。"""

    def generate_conclusion(
        self, manifest: CoverageManifest, assessment_rows: list[RiskRow]
    ) -> str:
        """生成报告结论段落。"""
```

**风格一致性策略**：

Prompt 中包含模板已有的文案作为 few-shot 示例：

```
你是一位 GMP 合规文档撰写专家。

## 任务
基于以下事实数据，撰写风险评估报告的「风险评估对象」章节。

## 事实数据
{extracted_facts}

## 风格参考（本模板已有文案）
以下是同一模板中其他章节的写作风格，请保持一致：
---
{style_example_1}
---
{style_example_2}
---

## 要求
1. 使用正式的 GMP 合规文档语言
2. 保持与上述风格参考一致的术语、句式、段落结构
3. 所有数据必须来自提供的事实数据，不得编造
4. 药品名称、剂型、规格等使用文档中的原始表述
5. 输出纯文本，不含 Markdown 标记

## 输出
直接输出章节内容，无需额外说明。
```

### 3.4 报告中的 LLM 内容标注

**原则**：LLM 生成的内容在报告中必须可辨识，满足 GMP 审计要求。

标注方式（在 docx_renderer 中实现）：

```
┌─────────────────────────────────────────────────────────────┐
│  1. 风险评估对象 Subject Description                         │
│                                                              │
│  盐酸氨溴索片，规格 30mg/片，批准文号国药准字 H20000001。    │
│  本品为化学药品 4 类，采用湿法制粒压片工艺。                  │
│  ────────────────────────────────────────────                │
│  ⓘ 以上内容由系统基于文档抽取结果自动生成，仅供参考，         │
│    请核实后确认。                                             │
│                                                              │
│  4. 风险评估 Risk Assessment                                 │
│  ┌────────┬──────────────┬──────────┬──────────┐            │
│  │ HazID  │ 风险因素      │ 控制前    │ 状态     │            │
│  ├────────┼──────────────┼──────────┼──────────┤            │
│  │ 交叉   │ 盐酸氨溴索... │ 中ⓘ      │ 待确认ⓘ │            │
│  └────────┴──────────────┴──────────┴──────────┘            │
│                                                              │
│  ⓘ = 含自动生成内容，需人工确认                               │
└─────────────────────────────────────────────────────────────┘
```

**实现**：

- `RiskReport` 新增 `llm_generated_fields: set[str]` 跟踪哪些字段由 LLM 生成
- `docx_renderer` 对 LLM 字段使用特殊样式（灰色斜体 + ⓘ 标记）
- 报告末尾附「自动生成内容说明」段落

### 3.5 评估层确定性不受影响

**关键约束**：LLM 仅参与内容填充和文案生成，不参与以下环节：

| 环节 | LLM 参与 | 原因 |
|------|---------|------|
| `evaluate(rule, facts)` | ✗ | 风险等级判定必须确定性 |
| `validate_coverage()` | ✗ | AST 遍历是形式化契约 |
| G1 三态 TRUE/FALSE/UNKNOWN | ✗ | 确定性语义 |
| 风险等级映射 pre/post control | ✗ | 规则 consequent 驱动 |
| 风险评估对象**描述文案** | ✓ | 叙述内容，非判定 |
| 缺失 slot 值**补填** | ✓ | 数据层，不影响评估逻辑 |
| 结论段落**文案** | ✓ | 叙述内容，审计要求标注来源 |

---

## 4. 技术方案

### 4.1 共享基础设施

两个特性共用 012 已有的 LLM 客户端：

```python
# backend/app/services/llm/local_client.py（已有）
def get_local_llm() -> OpenAI | None
```

新增：

```python
# backend/app/services/llm/local_client.py 扩展

def chat_with_schema(
    prompt: str,
    response_schema: dict,
    system_prompt: str | None = None,
    temperature: float = 0.1,
) -> dict | None:
    """统一的 structured output 调用封装。
    
    先尝试 response_format: json_schema，回退到 prompt + JSON 解析。
    """
```

### 4.2 新增文件

| 文件 | 特性 | 说明 |
|------|------|------|
| `backend/app/api/ast_templates.py` 扩展 | A | 新增 `suggest-slots` 端点 |
| `backend/app/services/reporting/slot_suggester.py` | A | 文档分析 + slot 建议生成 |
| `backend/app/services/reporting/narrative_generator.py` | B | 风格一致的叙述内容生成 |
| `backend/app/services/reporting/risk_report_generator.py` 改造 | B | LLM 补抽值合并入报告 |
| `backend/app/services/reporting/docx_renderer.py` 改造 | B | LLM 内容标注渲染 |
| `frontend/src/components/extraction/slot-suggestion-panel.tsx` | A | 建议预览 + 采纳 UI |
| `backend/tests/test_reporting/test_slot_suggester.py` | A | 测试 |
| `backend/tests/test_reporting/test_narrative_generator.py` | B | 测试 |

### 4.3 配置扩展

```python
# backend/app/config.py 新增

class Settings(BaseSettings):
    # ... 现有 local_llm_* ...

    # 特性 A：模板设计辅助
    llm_suggest_slots_enabled: bool = False    # 默认关闭（Constitution: 凡需出网的能力默认关闭）
    llm_suggest_max_slots: int = 50            # 单次建议上限

    # 特性 B：报告 LLM 内容生成
    llm_report_narrative_enabled: bool = False  # 默认关闭
    llm_report_merge_values: bool = False       # LLM 补抽值是否合并入报告（独立开关）
```

所有开关默认 `False`，符合 Constitution：「默认本地优先…凡需出网的能力 MUST 默认
关闭，仅在显式开启且配置就绪时方可触发」。本地 LLM 虽不出网，但 LLM 介入报告属
新行为，应显式启用。

---

## 5. 实施路线

### Phase 1：特性 A — 模板设计辅助

| 步骤 | 内容 | 估时 |
|------|------|------|
| A.1 | `slot_suggester.py` — 两轮 LLM prompt + 解析 | |
| A.2 | `POST /suggest-slots` 端点 + 请求/响应 schema | |
| A.3 | 与本体引擎集成 — 建议中匹配已有 class/property IRI | |
| A.4 | 前端 `SlotSuggestionPanel` — checkbox 树 + 采纳合并 | |
| A.5 | 前端集成到模板编辑页面 | |
| A.6 | 测试（mock LLM + 端到端） | |

**独立交付点**：A 完成后可独立上线。

### Phase 2：特性 B — 报告 LLM 增强

| 步骤 | 内容 | 估时 |
|------|------|------|
| B.1 | `RiskReport` 扩展 — `llm_supplements` + `llm_generated_fields` | |
| B.2 | `generate_with_coverage` 改造 — 集成 `fill_coverage_gaps` | |
| B.3 | `docx_renderer` 改造 — LLM 值渲染 + ⓘ 标注样式 | |
| B.4 | `narrative_generator.py` — 风格一致文案生成 | |
| B.5 | Prompt 风格参考提取 — 从模板/已有报告中采样 | |
| B.6 | 报告末尾「自动生成内容说明」段落 | |
| B.7 | 测试（mock LLM + 报告内容验证） | |

### Phase 3：集成验证

| 步骤 | 内容 |
|------|------|
| C.1 | 端到端：上传文档 → suggest-slots → 采纳 → 保存模板 → 生成报告（含 LLM 内容） |
| C.2 | `local_llm_enabled=False` 全路径零回归验证 |
| C.3 | 报告 LLM 标注的 GMP 审计合规性审查 |

---

## 6. Constitution 合规检查

| 原则 | 合规方式 |
|------|---------|
| 密钥凭据不入库 | LLM API key 通过环境变量注入，不硬编码 |
| 运行期强制离线 | 本地 LLM 端点，无外网请求 |
| 默认本地优先、能力默认关闭 | `llm_suggest_slots_enabled` / `llm_report_*` 默认 `False` |
| 离线不标记为降级 | 关闭 LLM 时模板手动编辑、报告纯规则生成——功能完整，非降级 |
| 写/发布端点基于角色门禁 | `suggest-slots` 和报告生成均需 `senior_analyst` 角色 |

---

## 7. 风险与应对

| 风险 | 应对 |
|------|------|
| LLM 建议的 slot 质量不高 | 两轮 prompt（结构分析→slot 映射）+ 本体交叉验证 + 置信度排序 + 用户人工筛选 |
| LLM 生成的报告文案不准确 | 所有事实来自抽取数据（不让 LLM 编造）+ ⓘ 标注 + 人工确认流程 |
| 叙述风格不一致 | Prompt 包含模板已有文案作为 few-shot + 低 temperature |
| 报告审计合规性 | LLM 内容明确标注来源、不替代规则判定、可一键关闭回退纯规则报告 |
| LLM 响应延迟 | suggest-slots 用 loading 状态 + 前端轮询/SSE；报告生成异步化 |
| 本地 LLM 不支持 structured output | 已有 fallback（012 `_build_response_format` + `_parse_llm_response`） |
