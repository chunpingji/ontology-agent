# Constitution 合规复核 — 能力一（T-Box 知识模型维护工作台）

**特性**：`001-slpra-ontology-platform` · **复核日期**：2026-06-20 · **依据**：`.specify/memory/constitution.md` v1.0.0（五项原则 + 安全合规 + 质量门禁）

结论：**通过**。逐项证据如下（路径相对仓库根，后端位于 `backend/`）。

## 原则 I — 规范驱动开发

- 全流程 `specify → clarify → plan → tasks → implement` 已落地：`specs/001-slpra-ontology-platform/`
  下含 `spec.md` / `plan.md` / `research.md` / `data-model.md` / `contracts/ontology-tbox-api.md` / `quickstart.md` / `tasks.md`。
- 实现严格按 `tasks.md`（T001–T044）分阶段执行；未在代码层私自扩展规范外能力（YAGNI，见原则 V）。

## 原则 II — 本体权威性与保真（NON-NEGOTIABLE）

- **外科式合并回写 TTL**：`app/services/ttl_merge.py::surgical_merge` 仅替换受管主语三元组，逐字保留 base 图中未建模三元组（注释/外部对齐/SWRL）。
- **受管命名空间隔离**：`ontology_meta_store.MANAGED_PREFIX = https://ontology.pharma-gmp.cn/slpra/`，创建/导入仅作用于受管前缀（`import_ttl` 跳过非受管主语）。
- **双存储写后一致**：编辑期真理来源为元数据表；发布时 `publish_release` 调 `engine.project_entities(...)` 投影至 Owlready2 World，再外科式导出 TTL。
- **三元组级 diff 预览**：`/api/ontology/export/diff` → `ttl_merge.export_diff` 返回 `triples_added/removed` + `turtle_preview`，发布前可见。

## 原则 III — 可追溯与审计

- **乐观并发**：所有可编辑对象带 `version`；`_cas_update` 比对 `expected_version`，冲突抛 409（`test_quickstart_e2e` 步骤 5 显式验证 409→正确版本重试→200）。
- **批量发布 = 一次 TTL 导出 + 一次 Git 提交**：`publish_release` → `_write_and_commit` 写 `slpra_managed.ttl` 并 `git commit`，SHA 入库 `ttl_commit_sha`。
- **审计留痕**：每次写/发布经 `self.audit(action, entity, actor, ...)` 落 `audit_log`（actor/动作/实体/批次/时间）。
- **已发布内容不物理删除**：`delete_class/link/property/action` 为软删除（`is_disabled=True`），撤销经 `rollback_release` 反向新批次实现，非就地篡改。

## 原则 IV — 测试纪律与契约优先

- **契约优先**：`contracts/ontology-tbox-api.md` 先于实现；契约测试 T016–T020 覆盖各端点。
- **关键路径测试**：`backend/tests/` 共 **36 passed**——CRUD、乐观并发冲突、导出/发布、映射健康度、校验阻断、RBAC 门禁。
- **quickstart 端到端可执行**：`tests/test_api/test_quickstart_e2e.py` 复现 `quickstart.md` §3 全链路（建类→映射→约束→风险属性→并发自检→校验→批次发布→diff→发布回写）+ §5 判据。
- **发布前健康度/一致性拦截**：`publish_release` 在 `report["blocking"]` 非空时拒绝（409）；阻断项含未映射/孤立类/停用类被引用/基数矛盾（`validate`）。

## 原则 V — 最小复杂度与复用

- 复用既有栈：FastAPI `APIRouter`+`Depends`、SQLAlchemy 2.0、Pydantic v2、Alembic、rdflib/Owlready2；前端复用 Next.js + 既有 `lib/api.ts` 模式。
- 未引入与现栈冲突的新框架；新增依赖（rdflib）已在 `plan.md` 说明必要性。

## 安全与合规

- **角色门禁（最小权限）**：写/发布端点经 `require_role(ROLE_SENIOR_ANALYST)`（`app/api/ontology.py:_writer`）；`operator` 写操作返回 403（`test_quickstart_role_gate` 验证）。
- **身份经可信网关注入**：`X-User`/`X-Role` 头（`app/dependencies.py`）；SSO 设计为可插拔后续接入。
- **合规判定不越界**：能力一未隐式实现与推理绑定的合规判定（属能力三范围）。

## 质量门禁

- `plan.md` 含 Constitution Check（Phase 0/1 双评估，回填 v1.0.0 五原则引用）。
- 结构变更经 Alembic 迁移；启动后由 TTL 幂等投影（`project_from_ttl`）补种。

## 复核中发现并修正的问题

- **测试隔离缺陷（已修复）**：发布流程 `_write_and_commit` 写真实 `ontology/slpra/` 并在真实仓库产生提交，导致 `export/diff` 基线被污染、`triples_added` 在套件复跑时为空。
  修复：`tests/conftest.py` 新增 autouse fixture `_isolate_ontology_dir`，将 `settings.ontology_dir` 指向每测试独立临时目录；并清理了历史测试run产生的 11 个噪声 `release:` 提交与测试生成的 `slpra_managed.ttl`。
