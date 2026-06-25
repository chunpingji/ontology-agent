# Contract: Classification Criteria CRUD (E11)

**Plan**: [../plan.md](../plan.md) | **Data model**: [../data-model.md](../data-model.md) §A E11 / §C 词汇

声明式分类判据（`logic_role=defined` → 目标类 `owl:equivalentClass` 充要公理）的可编辑 T-Box 端点。挂在既有 `/ontology` 工作台路由下，沿用既有约定：写/发布经 `require_role(senior_analyst)`（FR-016）、乐观并发经 `version`、软删除经 disable、双存储写后一致（FR-003）。

Base path: `/ontology/classification-criteria`

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| GET | `/ontology/classification-criteria` | any | 列表（可 `?target_class_iri=`、`?status=` 过滤） |
| GET | `/ontology/classification-criteria/{key}` | any | 单条详情（含展开后的 pattern AST 与投影预览） |
| POST | `/ontology/classification-criteria` | senior_analyst | 新建草稿 → 201 |
| PUT | `/ontology/classification-criteria/{key}` | senior_analyst | 更新（须带 `version`）→ 200 / 409 冲突 |
| POST | `/ontology/classification-criteria/{key}/disable` | senior_analyst | 软删除（停止投影） |

## 请求体（Create / Update）

```json
{
  "criterion_key": "R-DC3",
  "target_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug/HighSensitizingDrug",
  "logic_role": "defined",
  "pattern": { "op": "datatype_facet", "property": "sensitizationLevel", "cmp": "gt", "value": 3 },
  "regulation_ref": "CFDI 2023-03 §3.4",
  "version": 1
}
```

## 校验（违反 → 422；阻断发布 → FR-014）

- `logic_role` MUST = `defined`（产生式归 E12）。
- `pattern` MUST 通过受限模式词汇 schema（data-model §C 的 `op` 白名单）。
- `pattern` 引用的 `property` / `filler_class` / `alignment` IRI MUST 在本体可解析。
- `op=external_alignment` 的 `alignment`（ChEBI/ATC IRI）MUST 已字节级核实（`alignment_verified=true`）；否则发布门禁拦截（FR-014，research.md R3）。
- `criterion_key` 唯一；重复 → 409。

## 不变量

- **INV-1（OWA, FR-010/SC-006）**：pattern 求值三态；判定属性缺失 → 目标分类 `UNKNOWN`，响应 MUST NOT 把目标类断言为成立或为否。
- **INV-2（保真, FR-013）**：发布经 `export_diff` 出三元组级 diff；投影为目标类的 `owl:equivalentClass _:c<id>`（确定性 BNode），命名 IRI 外部对齐逐字保留（data-model §B1）。
- **INV-3（审计, FR-015）**：每次写入入 `OntologyChangeLog`，发布落 `OntologyRelease`(SHA)。
