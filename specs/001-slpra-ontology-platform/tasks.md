---
description: "Task list for 能力一 — 知识模型（T-Box）维护工作台"
---

# Tasks: 能力一 — 知识模型（T-Box）维护工作台

**Input**: Design documents from `specs/001-slpra-ontology-platform/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/ontology-tbox-api.md](./contracts/ontology-tbox-api.md), [quickstart.md](./quickstart.md)

**Tests**: 包含测试任务 —— 由 Constitution 原则 IV（测试纪律与契约优先，NON-NEGOTIABLE）要求；契约先行，覆盖 CRUD、乐观并发(409)、导出/发布、双存储一致性。

**Organization**: 本特性范围内仅 **US1（P1）** 一个用户故事（能力一）；US2/US3/US4 范围外；US5 中"维护操作的审计留痕与角色门禁"已并入 US1（FR-032/033/035）。因此 US1 即 MVP。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无未完成依赖）
- **[Story]**: 所属用户故事（US1）
- 每个任务含确切文件路径

## Path Conventions

Web 结构：后端 `backend/app/`、`backend/alembic/`、`backend/tests/`；前端 `frontend/src/`；权威 TTL `ontology/slpra/*.ttl`。路径依 [plan.md](./plan.md) Project Structure。

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 接线缺失的基础设施（依赖、迁移框架、测试布局）

- [X] T001 在 `backend/pyproject.toml` 新增依赖 `rdflib`（TTL 外科式合并/diff，R3），并锁定版本
- [X] T002 [P] 创建 Alembic 脚手架：`backend/alembic.ini` + `backend/alembic/env.py`（接 `Base.metadata` 与 `settings.database_url`，R6）
- [X] T003 [P] 建立后端测试布局 `backend/tests/conftest.py`（测试库会话、临时 Owlready2 World、TTL 夹具、`X-User/X-Role` 头夹具）

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 所有 US1 子流程共用的核心基础设施

**⚠️ CRITICAL**: 本阶段完成前，US1 端点/前端不可开工

- [X] T004 在 `backend/app/models/ontology_meta.py` 定义 SQLAlchemy 2.0 模型 E1–E10（公共列：`id/slpra_iri/label/comment/bfo_category/status/version/is_reviewed/is_disabled/confidence/created_at/updated_at/created_by/updated_by` + 各表专有列，见 data-model.md）
- [X] T005 在 `backend/app/models/reasoning.py` 扩展既有 `audit_log`：增列 `actor`、`release_id`（可空）（E11，FR-032）
- [X] T006 在 `backend/app/models/__init__.py` 导入全部模型，使 Alembic autogenerate 可见（现为空）
- [X] T007 创建首个迁移 `backend/alembic/versions/0001_ontology_meta.py`：建 E1–E10 表 + 为 `audit_log` 增列（R6）
- [X] T008 在 `backend/app/main.py` lifespan 启动时应用迁移（`alembic upgrade head`）（R6）
- [X] T009 [P] 在 `backend/app/dependencies.py` 实现最小 RBAC：解析 `X-User/X-Role`、`get_current_user`、`require_role("senior_analyst")`（R7，FR-033）
- [X] T010 在 `backend/app/services/ontology_engine.py` 扩展 T-Box 写方法：`create/update/delete_class`、`link_type`、`data_property`、`action`、`restriction`（`types.new_class` / `with onto:` / `is_a` 增删约束 / `destroy_entity`，写后 `world.save()`，复用现有 `_lock`）（R1，FR-001..005）
- [X] T011 在 `backend/app/services/ttl_merge.py` 实现 rdflib 外科式合并 + 三元组级 diff（受管命名空间/谓词白名单，逐字保留未建模三元组）（R3，FR-009a）
- [X] T012 在 `backend/app/services/ontology_meta_store.py` 实现元数据表 CRUD + 乐观并发 CAS（`WHERE id=? AND version=?`→409）+ 双存储同步 + `change_log` 记录（R2/R4，FR-011a）
- [X] T013 在 `backend/app/services/ontology_meta_store.py` 实现 `project_from_ttl()` 幂等投影补种，并在 `backend/app/main.py` 启动迁移后调用（R6）
- [X] T014 [P] 在 `backend/app/schemas/ontology.py` 扩展 Pydantic 模型：`ClassCreate/Update/Detail`、link-type/data-property/action/restriction/mapping 请求响应、`expected_version`、`ValidationReport`、`Release/ChangeLog`、diff（契约 §2–§11）
- [X] T015 在 `backend/app/dependencies.py` 注册 `get_ontology_meta_store` 依赖（注入 db + ontology_engine）

**Checkpoint**: 基础就绪 —— US1 端点与前端可开工

---

## Phase 3: User Story 1 - 高级分析师维护核心实体与关系 (Priority: P1) 🎯 MVP

**Goal**: 把能力一从"只读浏览"提升为可编辑、可映射、可加约束、可校验、可批次发布、可外科式回写 TTL 的工作台；全程乐观并发、角色门禁、审计留痕。

**Independent Test**: 分析师对一个药物类完成"新建 → 设标签/父类/字段 → 绑定 SLPRA·BFO 映射 → 加约束 → 校验 → 批次审核发布 → 导出 TTL 并查看与基线 diff"全流程，无需工程人员介入（对应 [quickstart.md](./quickstart.md) §3）。

### Tests for User Story 1 (契约先行，先写并确保 FAIL) ⚠️

- [X] T016 [P] [US1] Class CRUD + 乐观并发(409) + disable/review 契约测试 `backend/tests/test_api/test_ontology_class.py`（契约 §2）
- [X] T017 [P] [US1] link-type / data-property / action 契约测试 `backend/tests/test_api/test_ontology_properties.py`（契约 §3/§4/§7）
- [X] T018 [P] [US1] restriction + mapping/health 契约测试 `backend/tests/test_api/test_ontology_restriction_mapping.py`（契约 §5/§6）
- [X] T019 [P] [US1] validate + import/export/diff + release/publish 契约测试 `backend/tests/test_api/test_ontology_release.py`（契约 §8/§9/§10）
- [X] T020 [P] [US1] US1 端到端集成测试（建类→映射→约束→校验→批次→diff→发布，验证双存储一致 + TTL 保真）`backend/tests/test_api/test_ontology_tbox.py`

### Implementation for User Story 1 — Backend 端点（同改 `backend/app/api/ontology.py`，顺序执行）

- [X] T021 [US1] Class 端点 `POST/PUT/DELETE /classes`、`/disable`、`/review`（含 `require_role`、`expected_version`）于 `backend/app/api/ontology.py`（FR-001，AS-1）
- [X] T022 [US1] 对象属性/关系端点 `POST/PUT/DELETE /link-types`（domain/range/基数/逆校验）于 `backend/app/api/ontology.py`（FR-002，AS-2）
- [X] T023 [US1] 数据属性端点 `POST/PUT/DELETE /data-properties` + `GET /risk-vocabularies` + `POST /data-properties/risk` 风险向导 于 `backend/app/api/ontology.py`（FR-003/FR-010；FR-010 无专属验收场景，按 FR 覆盖）
- [X] T024 [US1] Action 端点 `GET/POST/PUT/DELETE /actions`（仅定义，不触发推理）于 `backend/app/api/ontology.py`（FR-004，R10）
- [X] T025 [US1] 约束端点 `POST /classes/{iri}/restrictions`、`PUT/DELETE /restrictions/{id}`，并确保既有约束完整渲染于类详情 GET 于 `backend/app/api/ontology.py`（FR-005，AS-4）
- [X] T026 [US1] 映射与健康度端点 `GET/POST/PUT/DELETE …/mappings` + `GET /mappings/health` 于 `backend/app/api/ontology.py`（FR-006，AS-3）
- [X] T027 [US1] 校验端点 `POST /validate`（规则式：孤立类/未映射/TTL 漂移/停用被引用/基数矛盾 + 可选 HermiT 一致性优雅降级）于 `backend/app/api/ontology.py` 及 `backend/app/services/ontology_meta_store.py`（FR-007，R9，AS-5）
- [X] T028 [US1] 导入/导出/diff 端点 `POST /import/ttl`、`GET /export/ttl`、`GET /export/diff`（调用 ttl_merge）于 `backend/app/api/ontology.py`（FR-009/FR-009a，AS-6）
- [X] T029 [US1] 批次发布生命周期 `GET/POST /releases`、`/submit`、`/publish`（投影 World→外科式导出 TTL→一次 Git 提交 subprocess→写 `ttl_commit_sha`）、`/rollback` 于 `backend/app/api/ontology.py`（FR-008/FR-008a，R5，AS-6）
- [X] T030 [US1] 审计端点 `GET /audit` + 在所有写/发布路径追加 `audit_log`（actor/action/entity/release）于 `backend/app/api/ontology.py`（FR-032/FR-035）

### Implementation for User Story 1 — Frontend（`frontend/src/`）

- [X] T031 [P] [US1] 在 `frontend/src/lib/api.ts` 补齐 T-Box 写/映射/校验/发布/导出方法（现仅 GET）+ TS 类型（契约对齐）
- [X] T032 [US1] Class 面板 `frontend/src/components/ontology/class-panel.tsx`（CRUD + 审核/停用/父类/字段 schema；保存携带 `expected_version`，409 走 T041 共享冲突处理）
- [X] T033 [P] [US1] 对象属性/数据属性面板 `frontend/src/components/ontology/link-type-panel.tsx` + `frontend/src/components/ontology/data-property-panel.tsx`
- [X] T034 [P] [US1] Action 面板 `frontend/src/components/ontology/action-panel.tsx`（actor/target/pre/post/params）
- [X] T035 [P] [US1] 约束编辑器 `frontend/src/components/ontology/restriction-editor.tsx`（some/all/exactly/min/max/互斥/等价）
- [X] T036 [P] [US1] 映射面板 `frontend/src/components/ontology/ontology-mapping-panel.tsx`（SLPRA IRI/BFO/字段 + 健康度徽标）
- [X] T037 [P] [US1] 风险属性向导 `frontend/src/components/ontology/risk-attribute-wizard.tsx`（OEB/PDE/致敏 受控词表）
- [X] T038 [P] [US1] TTL 工具条 `frontend/src/components/ontology/ttl-toolbar.tsx`（导入/导出/diff 预览/发布）
- [X] T039 [P] [US1] d3 图谱 `frontend/src/components/ontology/graph-visualization.tsx`（Class/Action/带基数关系）
- [X] T040 [US1] 改造 `frontend/src/app/(dashboard)/ontology/page.tsx`：只读 → 可编辑工作台壳，装配上述面板与工具条（FR-011，AS-1/2）
- [X] T041 [US1] 共享乐观并发冲突处理 `frontend/src/components/ontology/use-version-conflict.ts` + 冲突对话框：捕获任一写端点 409 → 提示"他人已更新"，提供「重新加载最新 / 查看差异 / 放弃」操作，由 class/link-type/data-property/action/restriction/mapping 各面板复用（FR-011a 用户侧；spec 边界"并发编辑"，AS 对应乐观并发）

**Checkpoint**: US1 完整可独立测试 —— 即 MVP 可交付

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: 跨切收尾与合规对齐

- [X] T042 [P] 按 [quickstart.md](./quickstart.md) 执行端到端验证（API §3 + 前端 §4），并显式验证 409 乐观并发"重新加载/合并"流程（T041）；记录通过判据 §5
- [X] T043 [P] 文档更新：`docs/临床药物智能辅助生产平台方案.md` 标注能力一缺口闭合状态；同步 `backend/README`（如有）
- [X] T044 Constitution 合规复核（原则 II 本体保真 / III 双存储一致+审计 / IV 测试覆盖 / 安全章节 RBAC）。注：plan.md Constitution Check 已回填引用 v1.0.0 五项原则（本特性 /speckit-constitution 已完成）。复核结论见 [compliance-review.md](./compliance-review.md)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**：无依赖，立即开始
- **Foundational (Phase 2)**：依赖 Setup；**阻断** US1
- **User Story 1 (Phase 3)**：依赖 Foundational 完成
- **Polish (Phase 4)**：依赖 US1 完成

### Foundational 内部依赖

- T004 → T006 → T007 → T008/T013（模型→导入→迁移→应用/补种）
- T005 并入 T007 迁移
- T010（引擎写方法）、T011（ttl_merge）、T012（meta-store）相互独立，但 T012 依赖 T004 模型、T010 引擎；T015 依赖 T012
- T009（RBAC）、T014（schemas）可与上并行 [P]

### User Story 1 内部依赖

- 测试 T016–T020 [P] 先写并 FAIL（契约先行）
- Backend 端点 T021–T030 **同改 `ontology.py`，顺序执行**（非 [P]）；均依赖 Phase 2（尤其 T010/T012/T014/T015）
- T027/T029 另依赖 T011（ttl_merge）与发布服务
- Frontend T031 [P] 可早做；T032–T039 多为 [P]（不同文件）；T041 共享冲突处理可早做并被各面板复用；T040 依赖 T031–T039、T041 装配
- 前端 T032+ 依赖对应后端端点就绪（或以 mock 并行开发）

### Parallel Opportunities

- Setup：T002、T003 [P]
- Foundational：T009、T014 [P]（与 T010/T011/T012 主线并行）
- US1 测试：T016–T020 全部 [P]
- US1 前端组件：T033–T039、T041 [P]（不同文件；T041 共享冲突处理为各面板的依赖，建议先行）
- 后端端点因共用单文件 `ontology.py` 不并行

---

## Parallel Example: User Story 1

```bash
# 契约测试先行（全部并行）：
Task: "Class CRUD+并发 契约测试 backend/tests/test_api/test_ontology_class.py"
Task: "属性 契约测试 backend/tests/test_api/test_ontology_properties.py"
Task: "约束+映射 契约测试 backend/tests/test_api/test_ontology_restriction_mapping.py"
Task: "校验+发布 契约测试 backend/tests/test_api/test_ontology_release.py"
Task: "端到端集成测试 backend/tests/test_api/test_ontology_tbox.py"

# 前端组件并行：
Task: "restriction-editor.tsx"
Task: "ontology-mapping-panel.tsx"
Task: "risk-attribute-wizard.tsx"
Task: "ttl-toolbar.tsx"
Task: "graph-visualization.tsx"
```

---

## Implementation Strategy

### MVP First (US1 = 唯一在范围用户故事)

1. 完成 Phase 1 Setup
2. 完成 Phase 2 Foundational（**关键**，阻断 US1）
3. 完成 Phase 3 US1
4. **STOP & VALIDATE**：按 quickstart 独立验证 US1
5. 就绪即可演示/部署 —— 即能力一缺口闭合

### Incremental Delivery（建议提交顺序）

1. Setup + Foundational → 基础就绪（可迁移、可投影补种、引擎可写）
2. US1 后端端点（T021→T030，逐组提交，测试转绿）→ API 可用
3. US1 前端（T031→T040）→ 工作台可用 → MVP
4. Polish（quickstart 验证 + 合规复核）

### 范围说明

- US2（推理）、US3（抽取）、US4（实时联动）**范围外** → 不在本 tasks 内，后续独立特性各自 `/speckit-specify`。
- US5 的推理结论 QA 电子签名 / ALCOA+ 哈希链随能力三交付；本特性仅覆盖维护操作的审计与角色门禁（已并入 US1）。

---

## Notes

- [P] = 不同文件、无依赖；后端端点共用 `ontology.py` 故标记为顺序
- 每个任务含确切文件路径，便于 LLM 直接执行
- 测试先写并确认 FAIL 后再实现（Constitution 原则 IV）
- 每完成一任务或逻辑组即提交
- 任一 checkpoint 可停下独立验证
- 严守 Constitution 原则 II：导出 TTL 必须外科式合并、保留未建模公理、写前 diff
