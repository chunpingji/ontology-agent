# Section 覆盖声明：从"编写抽取 slot"到"本体编译 slot"

> 设计说明 · 2026-07-05
> 关联实现：`backend/app/services/reporting/ast_template.py`、`coverage_validator.py`、`narrative_generator.py`、`fact_sources.py`、`docx_renderer.py`、`slot_suggester.py`、`frontend/.../template-slot-editor.tsx`
> 关联设计：[`declarative-rule-report-generator-binding-design.md`](declarative-rule-report-generator-binding-design.md)（确定性风险矩阵 + §5.4 叙事注入，FR-009）、[`rnd-document-fact-source-design.md`](rnd-document-fact-source-design.md)（事实源模型）

> **状态修订（禁止原文取数候选，post-016）** — §1 描述的两个断裂点**已修复**（AI 分析全程本体接地：`slot_suggester` 两轮均注入 `get_relation_schema(doc_class_iri)` 关系菜单 + CMCReport 的 D8 补挂边，LLM 只能**选择**菜单边、不能发明 IRI）。此外，早期设计里"绑定不到→抛出**原文取数候选**（`unresolved_candidates`）交作者处置"的那条流已**整体移除**，**取代 FR-008a**：AI 分析现在**只**产出 `{document_summary, coverage}`，绑定不到本体菜单边的位点**静默忽略**，不再有 `待解析候选` 面板。接地前提亦收紧："关联文档类型"由「基本信息」下拉框**实时**驱动 `docClassIri`（未保存即生效），未选定则拦截「AI 分析」。下文凡涉 `unresolved_candidates` / 待解析候选 / 降级为人工插槽 的表述均按历史阅读。

## 1. 背景：两个断裂点

在 AI 分析模板（例：`SECTION Ⅰ → 风险评估对象基本描述`）时观察到：

1. **所有 slot 类型退化为 `manual`**：`PRODUCT_CODE` 等本应为"抽取型"（从源文件关系图谱取值），实际全部落到手工。
2. **`EQUIP_REF_646` 是一个具体个体**，而非本体类型/类。

根因是同一个：**AI 分析全程"本体盲"，只在末尾做一次几乎必然失败的字符串精确匹配。**

- `slot_suggester.py` 两轮 LLM（结构分析 / 插槽映射）只喂样本原文，从不接触本体类/属性，LLM 自由发明 `slot_id`/`label`，并把样本具体值（"646"）当成 slot 锚点。
- `_bind_ontology_iris` 事后用 `label in dp_labels` 精确等值匹配；LLM 中文 label 与本体属性 label 属不同词表，几乎永不命中 → 退到 `llm_extraction`。
- 前端 `suggestionToSlot` 把非 `extraction` 一律折叠为 `manual`：`kind: slot.source_kind === "extraction" ? "extraction" : "manual"`。

净效果：唯一通往"抽取"的门是不可能的精确匹配，于是每个 slot 都落到手工；而 slot 锚在样本个体上，换一份源文档就无法解析。

## 2. 第一性原理：契约 vs 数据

报告生成的本质 = LLM 在**契约**约束下，把**数据**写成散文。

- **数据（已确定的输入）**：源文件关系图谱（本体类型+属性的实例子图）、事实源（组织/部门等）。运行时喂给 LLM 的原料。
- **契约（可控无遗漏的断言）**：某 section *应该*覆盖哪些东西，供校验器逐条打勾。

slot 存在的唯一理由是**当契约的载体**。现在的 `extraction` slot 却在干"把子图投影成标量"的搬运工活——这与"声明本节覆盖 DrugProduct"是冗余的：运行时把整个 DrugProduct 子图给 LLM，它自己会写"产品代码为 X、名称为 Y"。

**结论：`extraction` slot 不应是"被编写/被 LLM 发明"的工件，而应是从 section 的类型声明 + 本体自动"编译"出来的产物。**

## 3. 目标模型：Section 声明"覆盖绑定"，slot 变派生物

把契约的编写粒度提到**关系/类型**层——恰好是 `get_relation_schema(CMCReport)` 每条边的形状：

```
predicate=describes,         domain=CMCReport, range=DrugProduct,
    range_data_properties=[产品代码, 产品名称, 规格, …]   ← 本体白送
predicate=hasSynthesisRoute, domain=CMCReport, range=SynthesisRoute,
    range_data_properties=[…]
```

一个 section 的契约 = **一组覆盖绑定**，每条指向一个来源 + 一个选择器：

- 来源 = 本体图谱：选择器 = 一条关系路径（`CMCReport --describes--> DrugProduct`）。
- 来源 = 事实源：选择器 = 对该事实源的查询（组织/部门 → 各岗位责任人；事实源暂未实现）。

**手写到此为止。** 校验/生成时用本体把每条关系绑定展开：`describes→DrugProduct` 自动展开成 DrugProduct 的属性清单（`range_data_properties`），这才是细粒度的"无遗漏"勾选表；同一子图实例即 LLM 的输入。

`ExtractionSource` 从"持久化的编写工件"降级为"运行时中间产物"——校验器仍输出 per-position 的 `SlotCoverage`，但位置由本体生成，不再由 LLM 发明。

### 示例

- Section 1 风险评估对象基本描述 → 两条 `ontology_relation` 绑定：`describes`、`hasSynthesisRoute`。
- Section 2 审批与签署 → 一条 `fact_source` 绑定（组织/部门责任人）。

## 4. required 粒度（已定）

- **无遗漏信号定在关系粒度**：`describes→DrugProduct` 这条边缺失（图谱里根本没有 DrugProduct）= 真遗漏，进 `missing_required`，必报。
- **属性粒度默认降级为 informational（filled / blank_optional）**，不进 `missing_required`——除非本体或 per-section 覆盖显式把某属性提升为必填。

理由：一条关系展开出 N 个属性，若全部必填会淹没遗漏信号，违背"可控无遗漏"初衷（信号太多等于没信号）。

## 5. 哪些留在 slot 层（不一刀切）

坍缩掉的只有"来自图谱"的 `extraction` + `llm_extraction`，合并为 section 级关系声明。以下保留为 slot 级：

- **`rule`**：风险评估维度来自决策规则，不在实体图谱里。
- **`constant`**：模板写死的样板字。
- **`manual`**：真正人填的（如组织/部门事实源实现前的签署人）——显式兜底，而非"绑定失败的垃圾桶"。

## 6. 为什么从结构上消掉两个断裂点

不是打补丁，是让病根不成立：

- **断裂点1（全 manual）**：不再有 `label in dp_labels` 脆弱匹配，也不再有前端 `extraction/manual` 折叠。凡来自图谱的绑定，"抽取"是其与生俱来的性质，不需靠字符串命中去*争取*。
- **断裂点2（个体 646）**：编写词表就是关系图谱（类型+谓词），**物理上无法声明个体**。LLM 最多负责"这一节大概对应图谱里哪几条边"，边的两端永远是类。

## 7. Schema 形状草图

section 层新增 `coverage`，取代手写 extraction slots：

```jsonc
{
  "section_id": "s1",
  "title": "风险评估对象基本描述",
  "coverage": [
    { "kind": "ontology_relation",
      "doc_class_iri": ".../CMCReport",
      "predicate_iri": ".../describes",
      "range_class_iri": ".../DrugProduct",
      "required": true }          // 关系粒度必填；属性由本体展开为 informational
  ],
  "groups": [ /* rule / constant / manual slots 照旧 */ ],
  "prompt": "..."                  // 行文 prompt 不变
}
```

事实源绑定同理：`kind: "fact_source"`，selector 指向查询。

## 8. 改动点清单

- **`ast_template.py`**：`Section` 增 `coverage: list[CoverageBinding]`；新增 `OntologyRelationBinding` / `FactSourceBinding` 模型；`ExtractionSource` 保留但标注为运行时派生。
- **`coverage_validator.py`**：遍历 section 时先处理 `coverage`——`ontology_relation` 调 `get_relation_schema` 展开为 `SlotCoverage`（关系缺失→`missing_required`；属性缺失→`blank_optional`）；`fact_source` 查事实源。
- **`slot_suggester.py`**：Round 1/2 注入 `get_relation_schema(doc_class_iri)` 的类+属性图谱，让 LLM 把 section 映射到**真实关系边（IRI）**，产出 `coverage` 声明而非发明 slot；删除 `_bind_ontology_iris` 的精确等值匹配。
- **`template-slot-editor.tsx`**：停止 `llm_extraction → manual` 折叠；渲染 section 级覆盖声明（关系/类型选择器），不再逐 slot 编辑抽取绑定。

## 9. 待定

- 事实源 provider 已落地 `extraction` / `rule_results` 两类（见 §10.3）；A-Box（组织/人员/权限）与 `external.*` 仍留 stub，降级为非计数手工位，待 A-Box 个体读取 API 落定后接入。见 [`rnd-document-fact-source-design.md`](rnd-document-fact-source-design.md)。
- 属性→必填的显式提升机制（本体标注 vs per-section 覆盖）二选一或并存，实现时定。

## 10. 语义化插槽（Semantic Slot）：Section 的投影（016+ 变更）

> 变更说明 · 2026-07-05
> 关联实现：`ast_template.py`（`SemanticSource`/`coverage_key`）、`narrative_generator.py`（`generate_semantic_slots`）、`fact_sources.py`、`coverage_validator.py`、`docx_renderer.py`、`frontend/.../template-slot-editor.tsx`、`frontend/.../lib/api.ts`

### 10.1 需求变更

§3–§8 把"来自图谱"的 `extraction`/`llm_extraction` 坍缩进 section 级覆盖声明后，slot 的**定型来源**（在模板里硬编码取值路径）已不再是主要形态。进一步地：**Slot 重定义为语义化插槽**——报告生成时由本地 LLM 结合两样东西合成正文：

1. **prompt**：模板作者设定，规定本语义化插槽的输出内容；
2. **关联本体**：源文档的本体关系图谱事实 + 事实源中的事实本体。

即 slot 不再是"rule / constant / manual"这类定型搬运；这些内容统一来自**本体模型、源文档固定文字、事实源**。

### 10.2 核心决策：`SemanticSource` = 新的 `Slot.source` 类型，且是 Section 的投影

- 语义化插槽**不是**新 group 类型，**也不只在 section 层**——`Slot` 仍是可寻址单元（覆盖清单、docx 渲染、前端插槽树都以 `slot_id` 为键；`equipment_table`/`assessment_table` 组仍需 slot）。
- 语义化插槽**不存自己的绑定数据**，是 Section 的**投影**：
  - `prompt: str | None`——留空（`None`）则继承 `Section.prompt`（§7 的行文 prompt）；
  - `coverage_refs: list[str]`——是对 `Section.coverage` 的**过滤器**（用 `coverage_key(binding)` 作键；`[]` = 投影本节全部 coverage），不是拷贝。
- 旧的 5 种 source 类型（`extraction`/`rule`/`manual`/`constant`/`llm_extraction`）**保留在判别联合里**（标注 deprecated），旧 `schema_json` 继续 `model_validate` 且生成结果字节级一致。additive 字段嵌在 `schema_json`，**无 DB 迁移**。

```python
# ast_template.py
class SemanticSource(BaseModel):
    kind: Literal["semantic"] = "semantic"
    prompt: str | None = None                                # None → 继承 Section.prompt
    coverage_refs: list[str] = Field(default_factory=list)   # coverage_key 过滤；[] → 全部

SlotSource = Annotated[
    Union[ExtractionSource, RuleSource, ManualSource,
          ConstantSource, LLMExtractionSource, SemanticSource],  # ← 追加第 6 个成员
    Field(discriminator="kind"),
]
```

`coverage_key(binding)` 提取为 `ast_template.py` 的纯函数，是覆盖绑定合成 slot-id 的**唯一真源**（`coverage.{shortPred}__{shortRange}` / `coverage.fact_source.{shortSource}`）；`coverage_validator` 与语义化插槽的 `coverage_refs` 都引用它，保证键逐字节一致。前端 `api.ts` 的 `coverageKey` 与之镜像。

### 10.3 事实源（Fact Source）

事实源 = 源文档抽取事实 + 结构化映射 A-Box 事实（组织/人员/权限）+ 外部结构化数据源 + 声明式规则推理结果。`fact_sources.py` 以 `Protocol` + 注册表（按 `binding.source` 键）落地，`FactContext(edges, facts, assessment_rows, engine)` 复用既有 `edges_to_facts`/`RiskRow`，不新建抽取路径：

- **`extraction`** provider——投影 `ctx.edges`/`ctx.facts`（已实现）。
- **`rule_results`** provider——投影 `ctx.assessment_rows`（**确定性事实源**，只读，见 §10.4）（已实现）。
- **`abox.*`**（组织/人员/权限）、**`external.*`**——注册 stub 降级为非计数手工位，延后接入；未知 source → `[]`，保持既有行为。

### 10.4 FR-009：确定性风险矩阵不受 LLM 影响

风险矩阵（`assessment_table`，E12 决策规则算出风险等级）**仍确定性**，不由 LLM 影响。生成期时序：`generate_semantic_slots` 在 `_evaluate_rules`/`_evaluate_post_control`/`validate_coverage` **之后**运行；`assessment_rows` 只以"确定性结论，必须原样引用，不可自行推断"的只读上下文注入 LLM prompt，**只传入不修改，输出不回流评估**。整条语义化插槽合成仍在 `settings.llm_report_narrative_enabled` flag 之后（关闭 = 字节级 no-op）。该不变量与 [`declarative-rule-report-generator-binding-design.md`](declarative-rule-report-generator-binding-design.md) §5.4（section 级叙事注入）同源——slot 级与 section 级共用 `_format_rule_results` 等确定性注入辅助。

### 10.5 清单与渲染

- **覆盖清单**：`coverage_validator._resolve_value_slot` 对 `kind=="semantic"` 返回 `FILLED + is_llm_sourced=True` 的**非计数**位点——遗漏计数已在 section 顶部由覆盖绑定完成，插槽是内容投影，不新增状态枚举、不动计数器 → golden-master 不变。
- **docx**：`docx_renderer._add_semantic_slots` 把 `report.semantic_slots`（`{slot_id, section_id, label, text}`）渲染为带 ⓘ 灰斜体标注 + 免责声明的区块，与 §15 章节行文（`section_narratives`）同体例；LLM 内容 100% 可视标注。
- **web 阅读窗**：`_narratives_payload` 附带 `semantic_slots`，与 `sections` 并列持久化供阅读窗渲染。

### 10.6 前端

`template-slot-editor.tsx` 插槽编辑器：新建插槽默认即语义化插槽，编辑面板为 (1) Prompt textarea（留空 = 继承本节行文 prompt），(2)「关联本体」多选——列出父 section 的 `coverage` 绑定（label / `predicate → range`），写入 `coverage_refs`（未选 = 投影全部）。旧定型插槽只读呈现并标「旧式定型插槽（已弃用）」。`equipment_table`/`assessment_table` 组渲染保持不动（确定性）。

### 10.7 向后兼容

共存，不强制迁移：旧定型插槽仍是合法联合成员，加载 + 生成字节级一致；`templates/qs_a_020f05.json` 保持原样（改它会破坏 golden-master parity）。语义化插槽只出现在新建/编辑的模板。无 DB 迁移。部署顺序：先上带 `SemanticSource` 的后端，再让作者创建语义化插槽（旧后端会在判别联合处拒绝 `kind:"semantic"`）。
