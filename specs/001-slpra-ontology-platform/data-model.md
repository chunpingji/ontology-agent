# Data Model: 能力一 — 知识模型（T-Box）维护工作台

**Feature**: 001-slpra-ontology-platform | **Date**: 2026-06-20 | **Phase**: 1

**Input**: [plan.md](./plan.md) · [research.md](./research.md) · [spec.md](./spec.md)

> 本文件定义"可编辑元数据表"（T-Box 的可编辑投影）、发布/版本、最小用户/角色与审计实体。元数据表是**编辑期真理来源（草稿态）**（R2）；Owlready2 World 与权威 TTL 是**发布期物化产物**。所有可编辑实体均带 `version`（乐观并发，R4）与生命周期 `status`（草稿/在审/已发布）。命名沿用既有 SQLAlchemy 2.0 `Mapped`/`mapped_column` 风格，落在新增模块 `backend/app/models/ontology_meta.py`。

## 1. 实体总览

| # | 表 | 角色 | 关键关系 |
|---|----|------|---------|
| E1 | `ontology_class` | 本体类（Class）可编辑投影 | 自引用父类；← restriction/mapping/action |
| E2 | `ontology_link_type` | 对象属性 / 关系（domain→range，基数，逆属性） | →class(domain)、→class(range)、自引用逆 |
| E3 | `ontology_data_property` | 数据属性（datatype） | →class(domain) |
| E4 | `ontology_action` | Action 定义（actor/target/pre/post/params）——仅定义（R10） | →class(actor)、→class(target) |
| E5 | `ontology_restriction` | 约束（some/all/exactly/min/max/互斥/等价） | →class(owner)、→link_type/data_property、→class(filler) |
| E6 | `ontology_class_mapping` | SLPRA·BFO·字段映射 + 健康度 | →class |
| E7 | `ontology_release` | 批次发布与版本聚合（R5） | ← change_log |
| E8 | `ontology_change_log` | 发布批次内的变更条目（草稿→发布的快照） | →release、→任一可编辑实体 |
| E9 | `app_user` | 最小身份（R7） | →role |
| E10 | `app_role` | 角色（senior_analyst / operator / qa） | ← user |
| E11 | `audit_log`（既有，扩展） | 全程留痕 | →user（按 actor 字段） |

> E11 复用 `backend/app/models/reasoning.py` 既有 `audit_log`（`id/action/entity_iri/details JSON/created_at`），本特性仅**增列** `actor`、`release_id`（可空），不新建表。

## 2. 公共列（所有可编辑实体 E1–E6, E4）

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| `id` | UUID | PK | 沿用既有 UUID 主键风格 |
| `slpra_iri` | str | UNIQUE, NOT NULL | 工作台受管 IRI（外科式合并的主键，R3） |
| `label` | str | NOT NULL | 显示名（rdfs:label） |
| `comment` | str | NULL | 注释（rdfs:comment，建模化以求 TTL 保真，R3） |
| `bfo_category` | str | NULL | 对齐的 BFO 上层类别 |
| `status` | enum | NOT NULL, default `draft` | `draft` / `in_review` / `published` |
| `version` | int | NOT NULL, default 1 | **乐观并发**：保存携带读取版本，CAS 不匹配→409（R4） |
| `is_reviewed` | bool | default false | 高级分析师审核标记 |
| `is_disabled` | bool | default false | 停用（不删除，保留可追溯） |
| `confidence` | float | NULL | 抽取/建模置信度（能力二回填的入口，本特性只读展示） |
| `created_at` | datetime | server_default now | |
| `updated_at` | datetime | onupdate now | 乐观并发时间戳辅助 |
| `created_by` | UUID | FK app_user | |
| `updated_by` | UUID | FK app_user | |

## 3. 实体明细

### E1 `ontology_class`
- 专有列：`parent_class_id` (UUID, FK self, NULL=顶层)、`module` (str, 7 模块之一)、`field_schema` (JSONB, 类的字段/属性 schema 草稿)。
- 校验：`slpra_iri` 命名空间须属受管前缀；`parent_class_id` 不得成环；停用类不可作为他类父类（发布校验，R9）。

### E2 `ontology_link_type`（对象属性 / 关系）
- 专有列：`domain_class_id` (FK class)、`range_class_id` (FK class)、`inverse_link_id` (FK self, NULL)、`min_cardinality` (int, NULL)、`max_cardinality` (int, NULL)、`is_functional` (bool)、`is_symmetric` (bool)、`is_transitive` (bool)。
- 校验：domain/range 必须指向未停用类；`min ≤ max`（若均非空）；逆属性互指一致。

### E3 `ontology_data_property`（数据属性）
- 专有列：`domain_class_id` (FK class)、`datatype` (enum: string/integer/decimal/boolean/date/dateTime/anyURI)、`unit` (str, NULL)、`controlled_vocab` (JSONB, NULL — 受控词表，供风险属性向导)。
- 校验：`datatype` 必填；风险类属性（OEB/PDE/致敏）须挂受控词表（FR 风险向导）。

### E4 `ontology_action`（Action 定义，R10）
- 专有列：`actor_class_id` (FK class)、`target_class_id` (FK class)、`precondition` (JSONB)、`postcondition` (JSONB)、`params` (JSONB)。
- 范围约束：**仅维护定义**，不触发 `reasoning/rules` 运行期（属能力三）。

### E5 `ontology_restriction`（约束）
- 列：`id` UUID PK、`owner_class_id` (FK class, NOT NULL)、`kind` (enum: `some` / `only` / `exactly` / `min` / `max` / `disjoint` / `equivalent`)、`on_property_id` (FK link_type 或 data_property, NULL for disjoint/equivalent)、`property_kind` (enum: object/data)、`filler_class_id` (FK class, NULL)、`cardinality` (int, NULL)、`version`、`status`、审计列。
- 校验：`some/only` 需 `on_property` + `filler`；`exactly/min/max` 需 `on_property` + `cardinality`；`disjoint/equivalent` 需目标类集合（`filler_class_id` 或扩展关联表）。映射到 Owlready2 `is_a`（R1）。

### E6 `ontology_class_mapping`（映射 + 健康度）
- 列：`id` UUID PK、`class_id` (FK class)、`mapping_type` (enum: slpra_iri / bfo / source_field)、`target` (str, 外部 IRI 或字段路径)、`source_system` (str, NULL: Excel/Word/DB/逻辑库)、`health` (enum: ok / unmapped / drift / orphan)、`version`、`status`、审计列。
- 校验：每个非停用类至少一条 `slpra_iri` + 一条 `bfo` 映射方可发布（R9 阻断项）。

### E7 `ontology_release`（批次发布，R5）
- 列：`id` UUID PK、`release_no` (str, UNIQUE, 形如 `R2026.06.20-01`)、`title` (str)、`status` (enum: `draft` / `in_review` / `published` / `archived`)、`ttl_commit_sha` (str, NULL — 发布时一次 Git 提交，FR-008a)、`ttl_diff` (TEXT, NULL — 三元组级 diff 预览，FR-009a)、`validation_report` (JSONB, NULL — R9 校验结果)、`published_at` (datetime, NULL)、`created_by`/`published_by` (FK app_user)、`created_at`。
- 状态机：`draft → in_review → published`（→ `archived`）。`published` 时：投影 World → 外科式导出 TTL → 一次 Git 提交（写 `ttl_commit_sha`）→ 归档版本。回退仅允许 `in_review → draft`。

### E8 `ontology_change_log`（批次内变更条目）
- 列：`id` UUID PK、`release_id` (FK release)、`entity_table` (str)、`entity_id` (UUID)、`change_kind` (enum: create / update / delete / disable)、`before` (JSONB, NULL)、`after` (JSONB, NULL)、`created_at`。
- 作用：把"草稿期累计变更"绑定到一个发布批次，支撑批次化发布与可追溯回写（FR-008a）。

### E9 `app_user` / E10 `app_role`（最小身份，R7）
- `app_user`：`id` UUID PK、`username` (str, UNIQUE)、`display_name` (str)、`role_id` (FK role)、`is_active` (bool)、`created_at`。
- `app_role`：`id` UUID PK、`name` (enum: `senior_analyst` / `operator` / `qa`, UNIQUE)、`description` (str)。
- 身份来源：可信网关注入 `X-User` / `X-Role` 头（R7）；写/发布端点 `require_role("senior_analyst")`。SSO 后续可插拔。

## 4. 关系图（文字版）

```text
app_role 1───* app_user ───*(created_by/updated_by) {E1..E8}
ontology_class ──self── parent_class_id
ontology_class 1───* ontology_link_type   (domain)
ontology_class 1───* ontology_link_type   (range)
ontology_link_type ─self─ inverse_link_id
ontology_class 1───* ontology_data_property (domain)
ontology_class 1───* ontology_action (actor / target)
ontology_class 1───* ontology_restriction (owner / filler)
ontology_link_type|data_property 1───* ontology_restriction (on_property)
ontology_class 1───* ontology_class_mapping
ontology_release 1───* ontology_change_log ───*→ {任一可编辑实体}
audit_log *───1 app_user (actor)；audit_log *───0..1 ontology_release
```

## 5. 状态转换

**可编辑实体（E1–E6, E4）**：`draft` →（纳入发布批次审核）`in_review` →（批次发布）`published`。停用走 `is_disabled=true`（保留行，不物理删除）。

**发布批次（E7）**：`draft` → `in_review` → `published` → `archived`；`in_review` 可退回 `draft`。

**乐观并发（R4）**：任意 `update` 必须携带客户端读取时的 `version`；服务端 `UPDATE ... WHERE id=? AND version=?` 命中 0 行 → HTTP 409（见 [contracts/ontology-tbox-api.md](./contracts/ontology-tbox-api.md)）。

## 6. 迁移与种子（R6）

- Alembic 首迁移 `0001_ontology_meta.py` 创建 E1–E10 并为既有 `audit_log` 增列 `actor`、`release_id`。
- 启动应用迁移后，由权威 TTL **投影补种**（幂等 upsert by `slpra_iri`）元数据表，确保现有 ~720 公理可在工作台编辑（`project_from_ttl()`）。
