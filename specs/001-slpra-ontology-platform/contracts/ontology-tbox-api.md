# API Contract: 能力一 T-Box 维护端点

**Feature**: 001-slpra-ontology-platform | **Date**: 2026-06-20 | **Phase**: 1

**Input**: [plan.md](../plan.md) · [data-model.md](../data-model.md) · [research.md](../research.md)

> 在既有 `backend/app/api/ontology.py`（现仅 3×GET）上扩展。所有路径前缀 `/api/ontology`。请求/响应为 JSON（Pydantic v2）。写/发布端点需 `senior_analyst` 角色（`X-User`/`X-Role` 头，R7）。返回 `application/json`，错误体 `{ "detail": "<msg>" }`。

## 0. 约定

- **身份头**（所有写端点必带）：`X-User: <username>`、`X-Role: <senior_analyst|operator|qa>`。缺失或角色不足 → `403`。
- **乐观并发**（R4）：所有 `PUT`/`PATCH`/`DELETE` 请求体或查询须带 `expected_version`（整数）。服务端 CAS 不匹配 → `409 Conflict`，体含当前 `version`。
- **IRI 路径参数**：`{iri:path}` 沿用既有 `/classes/{class_iri:path}` 风格。
- **通用错误码**：`400` 校验失败 / `403` 角色不足 / `404` 不存在 / `409` 版本冲突或发布态冲突 / `422` Pydantic 体不合法。

## 1. 既有（保留，只读）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/modules` | 7 模块列表（既有） |
| GET | `/{module}/classes` | 模块内类层级（既有） |
| GET | `/classes/{class_iri:path}` | 类详情含 restrictions（既有） |

## 2. Class（E1）

| 方法 | 路径 | 角色 | 请求 | 响应 |
|------|------|------|------|------|
| POST | `/classes` | senior_analyst | `ClassCreate` | `201 ClassDetail` |
| PUT | `/classes/{iri:path}` | senior_analyst | `ClassUpdate`(含 `expected_version`) | `200 ClassDetail` / `409` |
| DELETE | `/classes/{iri:path}` | senior_analyst | `?expected_version=` | `204` / `409` |
| POST | `/classes/{iri:path}/disable` | senior_analyst | `{expected_version}` | `200 ClassDetail`（`is_disabled=true`） |
| POST | `/classes/{iri:path}/review` | senior_analyst | `{expected_version}` | `200 ClassDetail`（`is_reviewed=true`） |

`ClassCreate` = `{ slpra_iri, label, comment?, module, parent_iri?, bfo_category?, field_schema? }`
`ClassUpdate` = `ClassCreate` 子集 + `expected_version`（不含 `slpra_iri` 变更）。
`ClassDetail` = data-model E1 公共列 + 专有列 + 关联 restrictions/mappings 摘要。

## 3. 对象属性 / 关系（E2 `link_type`）

| 方法 | 路径 | 请求 |
|------|------|------|
| POST | `/link-types` | `{ slpra_iri, label, domain_iri, range_iri, inverse_iri?, min_cardinality?, max_cardinality?, is_functional?, is_symmetric?, is_transitive? }` |
| PUT | `/link-types/{iri:path}` | 上 + `expected_version` |
| DELETE | `/link-types/{iri:path}` | `?expected_version=` |

校验：domain/range 指向未停用类；`min ≤ max`；逆属性一致 → 否则 `400`。

## 4. 数据属性（E3 `data_property`）

| 方法 | 路径 | 请求 |
|------|------|------|
| POST | `/data-properties` | `{ slpra_iri, label, domain_iri, datatype, unit?, controlled_vocab? }` |
| PUT | `/data-properties/{iri:path}` | 上 + `expected_version` |
| DELETE | `/data-properties/{iri:path}` | `?expected_version=` |

`datatype ∈ {string,integer,decimal,boolean,date,dateTime,anyURI}`，否则 `400`。

### 4b. 风险属性向导（受控词表）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/risk-vocabularies` | 返回 OEB/PDE/致敏等受控词表枚举（供向导下拉） |
| POST | `/data-properties/risk` | 以受控词表创建风险数据属性（封装 §4，预填 `controlled_vocab`） |

## 5. 约束（E5 `restriction`）

| 方法 | 路径 | 请求 |
|------|------|------|
| POST | `/classes/{iri:path}/restrictions` | `RestrictionCreate` |
| PUT | `/restrictions/{id}` | `RestrictionUpdate` + `expected_version` |
| DELETE | `/restrictions/{id}` | `?expected_version=` |

`RestrictionCreate` = `{ kind, property_iri?, property_kind?, filler_iri?, cardinality? }`，`kind ∈ {some,only,exactly,min,max,disjoint,equivalent}`。
校验（→`400`）：`some/only` 需 `property_iri`+`filler_iri`；`exactly/min/max` 需 `property_iri`+`cardinality`；`disjoint/equivalent` 需目标类。映射 Owlready2 `is_a`（R1）。

## 6. 映射与健康度（E6 `class_mapping`）

| 方法 | 路径 | 请求 / 响应 |
|------|------|-------------|
| GET | `/classes/{iri:path}/mappings` | `200 [Mapping]` |
| POST | `/classes/{iri:path}/mappings` | `{ mapping_type, target, source_system? }` |
| PUT | `/mappings/{id}` | 上 + `expected_version` |
| DELETE | `/mappings/{id}` | `?expected_version=` |
| GET | `/mappings/health` | `200 { ok, unmapped[], drift[], orphan[] }`（模型健康度概览，R9） |

`mapping_type ∈ {slpra_iri, bfo, source_field}`。

## 7. Action 定义（E4，R10 — 仅定义）

| 方法 | 路径 | 请求 |
|------|------|------|
| GET | `/actions` | `200 [Action]` |
| POST | `/actions` | `{ slpra_iri, label, actor_iri, target_iri, precondition?, postcondition?, params? }` |
| PUT | `/actions/{iri:path}` | 上 + `expected_version` |
| DELETE | `/actions/{iri:path}` | `?expected_version=` |

> 不触发运行期推理；仅写元数据（属能力三范围之外）。

## 8. 校验（R9）

| 方法 | 路径 | 响应 |
|------|------|------|
| POST | `/validate` | `200 ValidationReport` |

`ValidationReport` = `{ blocking: [{code,message,entity_iri}], warnings: [...], reasoner: {ran:bool, consistent:bool?, note?} }`。
规则式：孤立类、未映射字段、TTL 漂移、停用类被引用、基数矛盾。`reasoner` 为可选 HermiT/Pellet 一致性（无 JVM 时 `ran=false` 优雅降级）。发布前 `blocking` 非空 → 阻断（§10）。

## 9. 导入 / 导出 / Diff（R3，FR-009a）

| 方法 | 路径 | 请求 / 响应 |
|------|------|-------------|
| POST | `/import/ttl` | multipart TTL → `200 { added, updated, conflicts[] }`（投影到元数据表，幂等 upsert by IRI） |
| GET | `/export/ttl?module=` | `200 text/turtle`（外科式合并产物，保留未建模公理） |
| GET | `/export/diff?release_id=` | `200 { turtle_preview, triples_added[], triples_removed[] }`（写入前三元组级 diff 预览） |

## 10. 批次发布与版本（E7/E8，R5，FR-008a）

| 方法 | 路径 | 角色 | 请求 / 响应 |
|------|------|------|-------------|
| GET | `/releases` | any | `200 [ReleaseSummary]` |
| POST | `/releases` | senior_analyst | `{ title }` → `201 Release`（聚合当前 `draft` 变更，status=draft） |
| GET | `/releases/{id}` | any | `200 Release`（含 change_log、validation_report、ttl_diff） |
| POST | `/releases/{id}/submit` | senior_analyst | `draft→in_review`（先跑 `/validate`，blocking 非空→`409`） |
| POST | `/releases/{id}/publish` | senior_analyst | `in_review→published`：投影 World → 外科式导出 TTL → **一次 Git 提交** → 写 `ttl_commit_sha`/`published_at` → 归档；blocking→`409` |
| POST | `/releases/{id}/rollback` | senior_analyst | `in_review→draft` |

`Release` = data-model E7 列 + `change_log: [ChangeLog]`。
发布幂等：已 `published` 再次 `publish` → `409`。

## 11. 审计（E11 既有扩展）

| 方法 | 路径 | 响应 |
|------|------|------|
| GET | `/audit?entity_iri=&release_id=&actor=` | `200 [AuditEntry]`（全程留痕查询） |

每次写/发布在 `audit_log` 追加 `{action, entity_iri, actor, release_id?, details, created_at}`。

## 12. 状态码矩阵（摘要）

| 场景 | 码 |
|------|----|
| 创建成功 | 201 |
| 更新/操作成功 | 200 |
| 删除成功 | 204 |
| 体不合法（Pydantic） | 422 |
| 业务校验失败（基数/缺映射/枚举） | 400 |
| 角色不足 / 缺身份头 | 403 |
| IRI 不存在 | 404 |
| 版本冲突 / 发布态冲突 | 409 |
