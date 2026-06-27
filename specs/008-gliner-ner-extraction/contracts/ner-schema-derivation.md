# Contract: NER Schema 派生（从本体类属性派生标签集）

**Feature**: `008-gliner-ner-extraction` | **Module**: `backend/app/services/extraction/pipeline.py:_schema_from_class`

界定「抽什么字段」由目标本体类只读派生（FR-013，research [R3](../research.md)）。**只读** `OntologyEngine`，不触 World 写路径（宪章 II）。

---

## 接口

```
_schema_from_class(target_class_iri: str) -> NerSchema

NerSchema = {
    "labels":       list[str],        # 供 GLiNER：每个 data_property 的 label（缺省回退 name）
    "label_to_iri": dict[str, str],   # label → 属性 IRI（回填抽取结果到 IRI 键）
}
```

**数据来源**（已核验形态）：`ontology_engine.get_class_detail(target_class_iri).data_properties`
→ `list[dict]`，每项 `{"iri": str, "name": str, "label": str, "range": list}`。

派生规则：
- `label = p.get("label") or p.get("name")`；`labels` 收集去重后的 label。
- `label_to_iri[label] = p["iri"]`；同 label 多属性 → 保留首个并记 WARNING。
- 类无 `data_properties` → `{"labels": [], "label_to_iri": {}}`。

---

## 不变量

| # | 不变量 | 验证方式 |
|---|--------|----------|
| S1 | **只读**：派生仅调用 `get_class_detail`（读），不触发任何 TTL/World 写入 | 断言无 `surgical_merge`/导出调用；World 写计数为 0 |
| S2 | **label 优先、name 回退**：`label` 非空用 `label`，否则用 `name` 作 GLiNER 标签 | 构造缺 label 的属性桩，断言用 name |
| S3 | **回填正确**：`extract_text` 返回的 `{label: value}` 经 `label_to_iri` 准确落到对应属性 IRI 键 | 端到端断言 IRI 键 = 属性 IRI |
| S4 | **空类安全**：类无 `data_properties` → 空 schema → NER 跳过、不报错 | 空属性桩，断言 `labels=[]` 且 pipeline 成功 |
| S5 | **本体演进自适应**：类属性增减后，下次抽取标签集随之变化（无需改配置） | 改属性桩两次，断言标签集随之变 |
| S6 | **唯一标签**：同 label 多属性时确定性保留首个并 WARNING（不随机） | 双同 label 属性桩，断言映射稳定 + WARNING |

---

**关联**：[gliner-extractor.md](./gliner-extractor.md)（`labels` 的消费者）、[parser-and-enrichment.md](./parser-and-enrichment.md)（`label_to_iri` 回填到 row）、[data-model.md §3.1](../data-model.md)（NerSchema 结构）。
