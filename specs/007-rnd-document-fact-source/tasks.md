# Tasks: 研发文档事实源（按研发阶段）

**Feature**: `007-rnd-document-fact-source` | **Input**: [plan.md](./plan.md) · [spec.md](./spec.md) · [research.md](./research.md) · [data-model.md](./data-model.md) · [contracts/](./contracts/) · [quickstart.md](./quickstart.md)

**Tests**: 包含（宪章 IV「契约优先」+ FR-013 明确要求关键路径契约/集成测试）。每个用户故事内**先契约/集成测试、后实现**；测试先落地、实现完成后转绿。

**组织原则**：按用户故事分相（P1→P4），每相独立可测、独立可交付。**MVP = US1（记录层）**。

**总览（关键不变式）**：零新建表、零 Alembic 迁移（仅 String 枚举取值 + 既有 JSON 列字段约定）、零新依赖、零并行框架（宪章 V）。三处复用式接缝：连接器工厂、`_materialize` 文档分支、能力二 `doc_repo` 抽取分支 + `extractedFrom` 注入。两条红线：**facts# 个体不入 TTL**（SC-004）、**复核门禁零削弱**（SC-003）。

---

## 路径约定

- 后端：`backend/app/...`，测试 `backend/tests/...`；命令均自 `backend/` 执行（如 `cd backend && pytest -q`）。
- 本体：`ontology/slpra/slpra-document.ttl`（新 T-Box 模块）。
- 前端：`frontend/src/...`（最小增补，复用 shadcn）。

---

## Phase 1：Setup（项目就绪）

- [X] T001 记录零回归基线：自 `backend/` 运行 `pytest -q` 全绿并存档当前通过用例集（APS/ERP/MES/LIMS/CTMS 与 `AssessmentResult` 对外形状），作为 SC-007 的前置基线（[quickstart 前置](./quickstart.md)）—— **基线 205 passed**
- [X] T002 [P] 新增确定性 doc_repo 测试夹具：一条「归一化文档生命周期变更」样例（`inline_changes`）及其**等价 upload 载荷**（供 FR-015 逐字节 parity 用），落 `backend/tests/test_integration/fixtures/doc_repo_changes.py`（形状见 [data-model §3](./data-model.md#3-归一化文档生命周期变更进程内形状)）

---

## Phase 2：Foundational（阻塞所有用户故事的前置）

> 连接器工厂是「接入第二类连接器」必然要求的唯一结构改动，以**默认回退 APS** 保零回归（[doc-repo-connector C1](./contracts/doc-repo-connector.md)）。它是 US1/US4 共同依赖的接缝层，故置于 Foundational。

- [X] T003 新建连接器工厂 `connector_for(connector)` 于 `backend/app/services/integration/connector_factory.py`：`system_type=='aps'` → `APSConnector`（参数 `connection_config`/`field_mapping`/`timeout` 与现 `materializer.py:50` **完全一致**）；未知/缺省/`None` → **回退 `APSConnector`**（C1.1/C1.3）
- [X] T004 全部连接器入口经工厂取连接器：将 `materializer.py:50`（`run_sync`）与 `api/integration.py`（test/sync/webhook）的**硬编码 `APSConnector` 改为 `connector_for(connector)`**，不再直引 `APSConnector`，不改 `run_sync` 主流程（`materializer.py:38–104`）（C1.4）
- [X] T005 [P] 连接器工厂 APS 零回归基线测试于 `backend/tests/test_integration/test_connector_factory.py`：APS 分发参数逐项一致 + 默认回退 + 既有 APS 契约/集成测试改造后**全绿**（C1.1/C1.3/C1.4，SC-007 红线）

**Checkpoint**：工厂就位且 APS 零回归——US1+ 可在其上叠加 doc_repo 分发。

---

## Phase 3：US1（P1，MVP）— 文档作为一等溯源锚点入图（记录层）

**故事目标**：研发文档**作为记录**经能力三直接物化为 `facts#` A-Box 个体（类型由托管 T-Box `RegulatoryDocument` 子类承载），幂等、失败不留半成品、100% 携研发阶段，且 facts# 个体**永不入权威 TTL**。

**独立验收**（[quickstart 场景 1](./quickstart.md#场景-1--文档作为一等溯源锚点入图us1--p1mvp)）：喂一条 inline 文档变更 → 生成 facts# 个体 + 托管 `…/slpra/document/<Subclass>` + `module='document'` + 阶段/版本/状态/来源/指纹，可检索；重复/乱序版本仅留最高版；`simulate='timeout'` → `cursor_to=None`、无半成品；扫描 `*.ttl` 无 facts# 个体三元组。

### 测试（先落地）

- [X] T006 [P] [US1] doc_repo 连接器契约测试于 `backend/tests/test_integration/test_doc_repo_connector.py`：`inline` 增量（`version>cursor.version`、`cursor_to.version=max`）+ `upload`/`inline` 产出**同一变更骨架** + `simulate=='timeout'` 抛 `asyncio.TimeoutError`（[doc-repo-connector C2.1/C2.3/C2.4](./contracts/doc-repo-connector.md)）
- [X] T007 [P] [US1] 记录层物化不变量测试于 `backend/tests/test_integration/test_record_materialization.py`：物化后 `entity_shadow.iri` 落 `facts#`、`class_iri` 落托管 `/slpra/document/`、100% 携 `hasDevelopmentPhase`；非文档变更仍走原 `facts#<entity_type>`（[record-materialization C1.1/C1.3/C1.4](./contracts/record-materialization-invariants.md)）
- [X] T008 [P] [US1] **A-Box/T-Box 边界门禁测试**（SC-004 关键）于 `backend/tests/test_integration/test_tbox_boundary.py`：物化任意批文档后扫描 `ontology/slpra/*.ttl` **无任何** facts# 个体三元组；`slpra-document.ttl` 仅含类/枚举/属性，`RegulatoryDocument` 具名实例数 = 0（[record-materialization C2.1/C2.2](./contracts/record-materialization-invariants.md)）
- [X] T009 [P] [US1] T-Box 增补 `surgical_merge`+`export_diff` round-trip 测试于 `backend/tests/test_integration/test_document_tbox_merge.py`：重复发布幂等、未建模三元组逐字保留、外部命名 IRI（BFO/OBO）不被改写（[record-materialization C2.3](./contracts/record-materialization-invariants.md)，宪章 II）
- [X] T010 [P] [US1] 幂等与水位测试于 `backend/tests/test_integration/test_record_idempotency.py`：同 `entity_id` v1→v2 仅推进至 v2、重复/乱序跳过（SC-005）；成功 `cursor_to` 推进、`change_count` 准确、`event_ids` **提交后**回填；`_fail` 置 `cursor_to=None`、无半成品影子行（SC-006）；`upload` 与 `inline` 行为一致（[record-materialization C3.1–C3.4](./contracts/record-materialization-invariants.md)）
- [X] T011 [P] [US1] `_detect_module` 双处一致性测试于 `backend/tests/test_integration/test_detect_module_document.py`：`kg_store.py:98` **与** `api/kg.py:96` 两处均把托管 `/slpra/document/` 归 `module='document'`（[record-materialization C1.2](./contracts/record-materialization-invariants.md)，[data-model §2.5](./data-model.md#25-_detect_module-双处增补两处都要改)）
- [X] T012 [P] [US1] 生命周期状态测试于 `backend/tests/test_integration/test_document_lifecycle.py`：被取代→旧个体置 `approvalStatus='superseded'` 并保留、新版本可查；作废→`'withdrawn'` 且溯源链可追溯；任何路径**不** `DELETE` 影子行（[record-materialization C4](./contracts/record-materialization-invariants.md)，宪章 III/FR-014）
- [X] T013 [P] [US1] 审计与留痕测试于 `backend/tests/test_integration/test_record_audit.py`：每次同步 `audit.append("integration.materialize", …{run_id,change_count})`；`FactMaterializationRun` 可追溯来源/变更数/水位/时间/状态（[record-materialization C5](./contracts/record-materialization-invariants.md)，US1 AS#5）

### 实现

- [X] T014 [P] [US1] 编写 T-Box 模块 `ontology/slpra/slpra-document.ttl`（同 `RiskLevel` 手写体例，经发布路径合入）：`RegulatoryDocument ⊑ obo:BFO_0000031` + 6 子类（`INDDossier`/`TechTransferReport`/`ProcessValidationReport`/`StabilityReport`/`NDA_BLADossier`/`PVReport`）；`DevelopmentPhase` 枚举 + 6 阶段个体（`Phase_DrugDiscovery`/`Phase_Preclinical`/`Phase_ClinicalI`/`Phase_ClinicalII_III`/`Phase_NDA_BLA`/`Phase_PostMarket`，各携 `skos:notation`+`rdfs:comment`）；属性 `hasDevelopmentPhase`/`extractedFrom`/`documentVersion`/`approvalStatus`/`sourceSystem`/`contentHash`（[data-model §7](./data-model.md#7-t-box-公理增补slprasprasdocumentttl-新模块经-surgical_merge-发布)）
- [X] T015 [P] [US1] 实现 `DocumentRepositoryConnector` 于 `backend/app/services/integration/doc_repo_connector.py`（实现 `ExternalSystemConnector`，形态同 `APSConnector`）：`inline` 模式读 `connection_config.inline_changes`、`upload` 模式将上传载荷转为归一化文档变更喂入同一 `fetch_incremental` 出口；`simulate=='timeout'` → `sleep(timeout+1)`；其余抽象方法最小实现（返回 `[]`）；`http` 模式留接缝给 US4（[doc-repo-connector C2](./contracts/doc-repo-connector.md)）
- [X] T016 [US1] 工厂增 doc_repo 分发：`connector_factory.py` 中 `system_type=='doc_repo'` → `DocumentRepositoryConnector`（依赖 T003+T015，[doc-repo-connector C1.2](./contracts/doc-repo-connector.md)）
- [X] T017 [US1] `_materialize` 文档分支于 `backend/app/services/integration/materializer.py:106`：`iri=f"{_FACT_BASE_IRI}{entity_id}"`（A-Box），`class_iris[0]` 经 `field_mapping.doc_type_to_class[entity_type]`（缺省走工厂内置默认表）映射为托管 `…/slpra/document/<Subclass>`；`properties` 含 `hasDevelopmentPhase`/`documentVersion`/`approvalStatus`/`sourceSystem`/`contentHash`/`externalRef`/`_version`；非文档变更仍走原 `facts#<entity_type>` 分支（依赖 T004+T014，[record-materialization C1](./contracts/record-materialization-invariants.md)）
- [X] T018 [P] [US1] `_detect_module` 双处增补：`backend/app/services/kg_store.py:98` 与 `backend/app/api/kg.py:96` 两个独立字典均增 `"document": "/slpra/document/"`（[data-model §2.5](./data-model.md#25-_detect_module-双处增补两处都要改)）
- [X] T019 [US1] 文档生命周期状态落地（`materializer.py`/`kg_store.py`）：更高版本到达时旧个体置 `superseded`、作废置 `withdrawn`，均以**状态变更**表达且不物理删除影子行（依赖 T017，[data-model §5.1](./data-model.md#51-文档批准状态approvalstatusfr-014-不物理删除)）
- [X] T020 [US1] doc_repo 连接器 CRUD 复用 `backend/app/api/integration.py` 既有端点：确认 `system_type='doc_repo'` 被既有连接器 CRUD 接受（如有 `system_type` 校验白名单则扩展），**不新增端点形状**（依赖 T004，[doc-repo-connector C4](./contracts/doc-repo-connector.md)）

**Checkpoint**：US1 全绿 = 记录层 MVP 可独立交付（连接器 + 文档个体物化 + T-Box 增补，纯记录层零写权威风险）。

---

## Phase 4：US2（P2）— 从文档抽取业务实体并溯源回文档（内容层）

**故事目标**：文档**内部业务实体**经能力二抽取 + 人工复核，确认入库注入 `extractedFrom` 回链；**复核门禁零削弱**（0% 自动入库）、**100% 可溯源**。

**独立验收**（[quickstart 场景 2](./quickstart.md#场景-2--从文档抽取业务实体并溯源回文档us2--p2)）：模拟文档 `approved` → 编排**入待抽取队列**（pending，不自动发起）；分析师手动发起 → doc_repo 分支产出待复核候选（`source_ref=<文档个体 IRI>`）；确认一个（携 `extractedFrom`+继承阶段）、拒绝一个（可追溯）。

### 测试（先落地）

- [X] T021 [P] [US2] 抽取触发编排测试于 `backend/tests/test_extraction/test_doc_repo_trigger.py`：`approved`/新版本事件 → 创建 `ExtractionJob(source_type='doc_repo', status='pending', source_config={doc_ref,content_ref})`；入队**不自动发起**、由授权角色手动发起；新旧版本溯源可区分（[content-extraction C1](./contracts/content-extraction-orchestration.md)）
- [X] T022 [P] [US2] doc_repo 抽取分支测试于 `backend/tests/test_extraction/test_doc_repo_pipeline.py`：每个候选 `source_ref==<文档个体 IRI>`；候选一律 `review_status='pending'` 不自动断言；复用既有 `align_entity`/`group_key`/`degraded_reason`；正文经 `content_ref` 按需取、不持久化全文（[content-extraction C2](./contracts/content-extraction-orchestration.md)）
- [X] T023 [P] [US2] 复核门禁测试于 `backend/tests/test_extraction/test_doc_repo_review_gate.py`：唯一入库路径=`confirmed`/`edited`→`_commit_candidate`、`rejected` 不入库；入库经 `require_role(senior_analyst)`；拒绝决定可追溯；来源文档可信**不**绕过门禁（无 doc_repo 直入捷径）（[content-extraction C3](./contracts/content-extraction-orchestration.md)，SC-003=0%）
- [X] T024 [P] [US2] `extractedFrom` 回链注入测试于 `backend/tests/test_extraction/test_extracted_from.py`：提交个体 `extracted_properties["extractedFrom"]==candidate.source_ref`；100% 携带；缺省继承文档阶段（`setdefault hasDevelopmentPhase`）；注入仅作用于 doc_repo 来源候选、非文档候选行为不变（[content-extraction C4](./contracts/content-extraction-orchestration.md)，SC-002）
- [X] T025 [P] [US2] 受影响子图重算衔接测试于 `backend/tests/test_integration/test_affected_subgraph_document.py`：文档/派生实体变更经 `resolve_affected_subgraph` 解出 `document`（及关联 `sample`/`product`）维；重算不改 `AssessmentResult` 对外形状（[content-extraction C5](./contracts/content-extraction-orchestration.md)）

### 实现

- [X] T026 [P] [US2] `resolve_affected_subgraph` 增 `document` 维于 `backend/app/services/integration/events.py:19`：`subgraph` 增 `"document": []`，按 `entity_type∈{document,RegulatoryDocument,…}` 与 `fields` 的 `sample`/`product` 键纳入受影响范围；不改 `FactEventBus.publish` 事件信封（[data-model §6](./data-model.md#6-受影响子图扩展eventsresolve_affected_subgrapheventspy19)）
- [X] T027 [P] [US2] doc_repo 抽取分支于 `backend/app/services/extraction/pipeline.py`（`pipeline.py:57` 与 `database`/`excel`/`word` 并列）：`config.source_type=='doc_repo'` → `source_ref=job.source_config["doc_ref"]`，按 `content_ref` 按需取正文 → LLM 抽取 → 对齐 → 候选入库（`review_status='pending'`）（[content-extraction C2](./contracts/content-extraction-orchestration.md)）
- [X] T028 [US2] 文档批准事件→入待抽取队列编排端点于 `backend/app/api/extraction.py`：创建 `ExtractionJob(source_type='doc_repo', status='pending', source_config={doc_ref,content_ref})`；**不自动发起**，手动发起复用 `run_extraction_pipeline`（Q1，[data-model §5.3](./data-model.md#53-内容抽取触发fr-007--q1-手动发起)）
- [X] T029 [US2] `_commit_candidate` 注入溯源回链于 `backend/app/api/extraction.py:191`：确认入库时 `extracted_properties["extractedFrom"]=candidate.source_ref` 且 `setdefault("hasDevelopmentPhase", <文档阶段 IRI>)`，仅当候选 `source_ref` 为 facts# 文档 IRI 时生效（依赖 T028 同文件，[data-model §4](./data-model.md#4-溯源回链形状内容层--文档个体)）

**Checkpoint**：US1+US2 全绿 = 记录层 + 内容层接线完整（事件→编排→复核→`extractedFrom` 回链）。

---

## Phase 5：US3（P3）— 研发阶段作为溯源与风险/合规上下文

**故事目标**：研发阶段作横切受控词表，支持按阶段检索；阶段作评估结论的**溯源标注**（本期**仅标注**，不进规则前件——FR-011 红线）。

**独立验收**（[quickstart 场景 3](./quickstart.md#场景-3--研发阶段作为溯源与风险合规上下文us3--p3)）：物化分属不同阶段的文档/实体 → 按 `hasDevelopmentPhase` 过滤正确返回；查看临床Ⅰ期评估溯源体现阶段质量侧重；**负向门禁**：阶段 IRI 不出现在任何 006 规则前件中。

> 阶段枚举词表已由 US1 的 T014 编入 `slpra-document.ttl`；本相**消费 + 验证**词表并补检索/标注接缝。

### 测试（先落地）

- [X] T030 [P] [US3] 阶段词表测试于 `backend/tests/test_integration/test_phase_vocabulary.py`：`DevelopmentPhase` 含 6 个体；各携 `skos:notation`（次序）+ `rdfs:comment`（质量侧重）；取值受控、可版本化、发布经审计（[provenance-and-phase C1](./contracts/provenance-and-phase-query.md)）
- [X] T031 [P] [US3] 按阶段检索测试于 `backend/tests/test_integration/test_phase_filter.py`：按 `hasDevelopmentPhase` 返回该阶段**文档**与**派生实体**（备样/药物）集合；文档事实个体 100% 携阶段标注（无未标注）（[provenance-and-phase C2](./contracts/provenance-and-phase-query.md)，SC-008/SC-001）
- [X] T032 [P] [US3] 一键溯源测试于 `backend/tests/test_extraction/test_provenance_resolve.py`：经业务实体 `extractedFrom` 解析到源文档个体；由 `documentVersion` 得「抽自哪一版」；100% 经文档抽取确认入库实体可溯源（无断链）（[provenance-and-phase C3](./contracts/provenance-and-phase-query.md)，SC-002）
- [X] T033 [P] [US3] 阶段作评估溯源上下文测试于 `backend/tests/test_reasoning/test_phase_provenance_context.py`：至少一条评估结论溯源体现对应阶段质量侧重（临床Ⅰ期→共线风险/清洁确认）；接入阶段标注后 `AssessmentResult` 对外形状不变（[provenance-and-phase C4.1/C4.3](./contracts/provenance-and-phase-query.md)）
- [X] T034 [P] [US3] **FR-011 负向门禁测试**于 `backend/tests/test_reasoning/test_phase_not_in_rule_antecedent.py`：断言阶段 IRI **MUST NOT** 出现于任何 006 声明式判据/决策规则的 antecedent（本期不参与推断）（[provenance-and-phase C4.2 红线](./contracts/provenance-and-phase-query.md)）

### 实现

- [X] T035 [P] [US3] 按阶段检索接缝（复用既有 `search_entities`（`kg_store.py:54`）/ SPARQL 端点（`api/kg.py:35`），按 `properties_json.hasDevelopmentPhase` 过滤）——**不新增检索框架**（[provenance-and-phase C2 备注](./contracts/provenance-and-phase-query.md)）
- [X] T036 [P] [US3] 阶段作评估溯源标注：在评估结论溯源面（`rules_fired`/溯源标注）附阶段上下文，**仅标注**且**确保不注入规则求值/前件**（守 FR-011 红线，[provenance-and-phase C4](./contracts/provenance-and-phase-query.md)）

**Checkpoint**：US1+US2+US3 全绿 = 阶段维度作溯源/合规上下文可用，且未越界织入 006 推断逻辑。

---

## Phase 6：US4（P4）— 接入真实研发文档系统（生产接入）

**故事目标**：以 `http` 模式 + env 注入凭据接入真实 EDMS/eTMF，复用 US1 记录层 + US2 内容编排，凭据不入库。

**独立验收**（[quickstart 场景 4](./quickstart.md#场景-4--上传过渡入口us1-扩展--fr-015与真实端点us4--p4)）：配 `access_mode='http'` + env 凭据 → 探活 + 增量拉取，行为与 inline 一致；`connection_config` 不含明文凭据。

### 测试（先落地）

- [X] T037 [P] [US4] http 模式测试于 `backend/tests/test_integration/test_doc_repo_http.py`：`test_connection` 探活 + `fetch_incremental` 增量拉取（实现位可注入桩端点），产出与 `inline` 同一变更骨架、下游物化无分支差异（[doc-repo-connector C2 http](./contracts/doc-repo-connector.md)，US4 AS#1）
- [X] T038 [P] [US4] 凭据注入测试于 `backend/tests/test_integration/test_doc_repo_credentials.py`：`http` 凭据经 env 变量名引用（`token_ref="EDMS_TOKEN"`）运行时 `os.environ` 解析；持久化 `connection_config` **不含明文** token/password/密钥；凭据**不**出现于 `FactMaterializationRun.changes`/审计/日志（[doc-repo-connector C3](./contracts/doc-repo-connector.md)，FR-010）

### 实现

- [X] T039 [US4] `DocumentRepositoryConnector` http 模式于 `backend/app/services/integration/doc_repo_connector.py`：`base_url` + env 注入凭据（`os.environ` 解析 `token_ref`/`endpoint_ref`）探活/增量拉取；`cursor` 按文档系统增量机制推进；填补 US1（T015）留下的 http 接缝（[doc-repo-connector C2/C3](./contracts/doc-repo-connector.md)）

**Checkpoint**：四故事全绿 = 生产接入就绪，记录层/内容层/阶段维度全程复用同一管线。

---

## Phase 7：Polish & 跨切关注

- [X] T040 [P] 前端：事实源面板增 doc_repo 连接器卡（upload/inline/http 配置）+ 文档溯源视图于 `frontend/src/app/(dashboard)/integration/`（复用 shadcn，不新建路由架构）
- [X] T041 [P] 前端：doc_repo 连接器与文档溯源查询客户端于 `frontend/src/lib/api.ts`
- [X] T042 [P] 零回归终局门禁（SC-007）于 `backend/tests/test_integration/test_zero_regression.py`：既有 5 类事实源（APS/ERP/MES/LIMS/CTMS）回归非预期变化数 = 0；`AssessmentResult` 对外形状不变（[provenance-and-phase C5](./contracts/provenance-and-phase-query.md)，FR-012）
- [X] T043 quickstart 端到端校验：自 `backend/` 跑通 [quickstart.md](./quickstart.md) 场景 1–4 + 零回归门禁，全部预期达成（宪章 IV「quickstart 可执行」）
- [X] T044 [P] 文档：核对 `docs/rnd-document-fact-source-design.md` 与实现一致、交叉引用 specs/007 制品（轻量收尾）

---

## Dependencies & Execution Order（依赖与执行顺序）

**相位顺序（硬）**：Setup（P1）→ Foundational（P2）→ US1（P3）→ US2（P4）→ US3（P5）→ US4（P6）→ Polish（P7）。

**用户故事依赖**：
- **US1（P1）**：仅依赖 Foundational（工厂）。无其他故事依赖——**可独立交付（MVP）**。
- **US2（P2）**：依赖 US1（facts# 文档个体 + `extractedFrom` 属性 + 文档阶段可继承 + 连接器/物化路径）。
- **US3（P3）**：硬依赖 US1（阶段词表 + 文档个体可过滤）；其「派生实体按阶段」「`extractedFrom` 溯源」断言依赖 US2。
- **US4（P4）**：硬依赖 US1（连接器 + 记录层）+ Foundational 工厂；端到端复用 US2 内容编排。

**关键任务级依赖**：
- T003 → T004 → T005（工厂 → 入口接线 → 零回归基线）。
- T015 → T016（连接器存在 → 工厂 doc_repo 分发）。
- T014 + T004 → T017 →（T019）（TTL 类 + 工厂接线 → `_materialize` 文档分支 → 生命周期状态）。
- T028 → T029（同文件 `api/extraction.py`，编排端点 → `_commit_candidate` 注入，**顺序**）。
- US1 全部测试（T006–T013）先于其实现转绿；其余故事同理。

**同文件串行（非并行）**：`materializer.py`（T004 Foundational → T017/T019 US1）；`api/extraction.py`（T028 → T029）；`doc_repo_connector.py`（T015 US1 → T039 US4）；`connector_factory.py`（T003 → T016）。

---

## Parallel Example（并行示例）

```text
# Foundational：
T003 → T004 →（T005 [P] 可与 US1 测试同批启动）

# US1 测试（8 个独立测试文件，全并行）：
并行启动 T006 T007 T008 T009 T010 T011 T012 T013

# US1 实现（独立文件可并行）：
并行启动 T014（ttl）、T015（doc_repo_connector.py）、T018（kg_store.py+api/kg.py）
随后 T016（依 T015）、T017（依 T014/T004）、T019（依 T017）、T020

# US2 测试全并行：T021 T022 T023 T024 T025
# US2 实现：并行 T026（events.py）、T027（pipeline.py）；串行 T028 → T029（api/extraction.py）

# US3 测试全并行：T030 T031 T032 T033 T034
# US3 实现：并行 T035、T036

# US4 测试全并行：T037 T038 ；实现 T039
# Polish：并行 T040 T041 T042 T044 ；T043 收尾
```

---

## Implementation Strategy（实施策略）

**MVP 优先**：先交付 Setup + Foundational + **US1**（记录层）。US1 自成完整增量——文档作为一等溯源锚点入图、幂等、失败安全、100% 阶段标注、facts# 不入 TTL——纯记录层零写权威风险，可独立演示/验收后再推进。

**增量交付**：每相一个 Checkpoint。US1（MVP）→ +US2（内容层接线）→ +US3（阶段上下文）→ +US4（生产接入）→ Polish（前端 + 终局零回归 + quickstart）。任一故事完成即可独立测试与交付。

**红线守护（贯穿）**：
- **SC-004**（facts# 不入 TTL）：T008 必须作为独立门禁存在，记录层任何回归令其失败。
- **SC-003**（复核门禁零削弱）：T023 守「来源文档可信不绕过复核」。
- **SC-007**（零回归）：T005（工厂 APS 基线，前）+ T042（全套终局，后）双门禁。
- **FR-011**（阶段不进推断）：T034 负向门禁断言阶段 IRI 不入任何 006 规则前件。
- **FR-010**（凭据不入库）：T038 断言 `connection_config` 无明文、凭据不入留痕/审计/日志。

---

## Notes（备注）

- `[P]` = 不同文件、无未完成依赖，可并行；同文件任务串行。
- 测试**先落地、实现后转绿**（宪章 IV 契约优先）；测试任务描述均含目标文件路径与契约锚点。
- **零新建表、零 Alembic 迁移、零新依赖、零并行框架**——新增仅 String 枚举取值 + 既有 JSON 列字段约定 + 一连接器 + 一 T-Box 模块（宪章 V）。
- `slpra-document.ttl` 同 `RiskLevel` 手写体例编写（`build_managed_graph` 无具名个体发射器），经能力一既有发布路径（`surgical_merge`+`export_diff`+`require_role(senior_analyst)`+Git/SHA）合入，由 T009 round-trip + T008 边界门禁坐实。
- **仅在用户明确要求时**提交/推送（提交信息按仓库约定，本命令不自动提交）。
