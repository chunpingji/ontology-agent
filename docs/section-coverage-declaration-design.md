# Section 覆盖声明：从"编写抽取 slot"到"本体编译 slot"

> 设计说明 · 2026-07-05
> 关联：`backend/app/services/reporting/ast_template.py`、`coverage_validator.py`、`slot_suggester.py`、`frontend/.../template-slot-editor.tsx`

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

- 事实源接口（组织/部门）尚未实现——`fact_source` 绑定先留占位，落地见 `rnd-document-fact-source-design.md` 的事实源模型。
- 属性→必填的显式提升机制（本体标注 vs per-section 覆盖）二选一或并存，实现时定。
