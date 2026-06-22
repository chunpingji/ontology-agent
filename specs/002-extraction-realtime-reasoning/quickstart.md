# Quickstart: 多源抽取与实时推理 —— 能力二/能力三 GAP 闭合

**Feature**: `002-extraction-realtime-reasoning` | **Plan**: [plan.md](./plan.md)

本指南提供 US1–US6 的端到端验证场景，证明能力二/三 gap 已闭合。详细字段见 [contracts/](./contracts/) 与 [data-model.md](./data-model.md)。

## 前置

```bash
# 启动三服务（db / backend / frontend）
docker compose up -d
# 后端新增依赖 reportlab 已在 backend/pyproject 声明；迁移随启动自动 upgrade head
# 健康检查
curl -s http://localhost:8000/api/health
```

鉴权头（所有请求）：`-H "X-User: <user>" -H "X-Role: senior_analyst|operator|qa"`。

前端：`http://localhost:3000` —— 抽取工作台 / 集成（连接器 + 看板）/ 推理（结论 + QA 签名）。

---

## US1 — 抽取作业接线与进度（P1）

```bash
# 1. 创建 Excel 抽取作业（应实际触发流水线，返回 running 而非 pending）
curl -X POST http://localhost:8000/api/extraction/jobs \
  -H "X-User: analyst1" -H "X-Role: senior_analyst" \
  -F source_type=excel -F config_id=<CONFIG_UUID> -F file=@设备台账.xlsx
# → 202 { "job_id": "...", "status": "running" }

# 2. 订阅进度 SSE（应逐阶段推送 parsing→extracting→aligning→reviewing→done）
curl -N http://localhost:8000/api/extraction/jobs/<JOB_ID>/progress \
  -H "X-User: analyst1" -H "X-Role: senior_analyst"
```
**预期**：作业不停留 `pending`；SSE 报出各阶段；最终 `reviewing` 且 `total_candidates>0`。上传含表格+正文的 SOP Word 时，正文"若…则…必须…"产出 `candidate_kind=action` 候选。
**回退验证**：取消 `ANTHROPIC_API_KEY` 后重跑 → 作业完成且 SSE `degraded=true`、候选带 `degraded_reason`（不失败）。
**覆盖**：FR-001~007 / SC-001。

---

## US2 — 跨源对齐与审核闭环 + DB 源（P2）

```bash
# 1. 查看候选（跨源同一设备应归为一组、标记规范实例）
curl http://localhost:8000/api/extraction/jobs/<JOB_ID>/candidates \
  -H "X-User: analyst1" -H "X-Role: senior_analyst"
# → groups[].group_key 归组, canonical_candidate_id 标记

# 2. 确认一个候选（应进入知识库，落 committed_iri）
curl -X PUT http://localhost:8000/api/extraction/candidates/<CAND_ID>/review \
  -H "X-User: analyst1" -H "X-Role: senior_analyst" \
  -H "Content-Type: application/json" \
  -d '{"status":"confirmed"}'

# 3. 合并 / 拆分
curl -X POST http://localhost:8000/api/extraction/candidates/merge \
  -H "X-User: analyst1" -H "X-Role: senior_analyst" \
  -H "Content-Type: application/json" \
  -d '{"source_ids":["<A>"],"target_id":"<B>"}'

# 4. 数据库源抽取（表→实体、外键→关系，进入同一审核队列）
curl -X POST http://localhost:8000/api/extraction/jobs \
  -H "X-User: analyst1" -H "X-Role: senior_analyst" \
  -H "Content-Type: application/json" \
  -d '{"source_type":"database","config_id":"<CFG>","db_source":{"dsn_ref":"ERP_DSN","schema":"public","include_tables":["equipment","material"]}}'
```
**预期**：跨源归组正确、规范实例标记正确；仅 `confirmed` 进入知识库、`rejected` 不进入；歧义候选保持 `pending` 不自动合并；DB 源产出 `candidate_kind=class/link` 候选入审核队列。
**覆盖**：FR-008~013 / SC-003。

---

## US3 — 实时事实源接入与增量物化（P3）

```bash
# 1. 创建 APS 连接器（凭据经 env，connection_config 不含明文）
curl -X POST http://localhost:8000/api/integration/connectors \
  -H "X-User: analyst1" -H "X-Role: senior_analyst" \
  -H "Content-Type: application/json" \
  -d '{"system_type":"APS","name":"APS","ingest_mode":"hybrid","poll_interval_seconds":2,"connection_config":{"base_url":"http://aps.internal","dsn_ref":"APS_DSN"}}'

# 2. 真实连接测试 + 触发同步
curl -X POST http://localhost:8000/api/integration/connectors/<ID>/test  -H "X-Role: senior_analyst" -H "X-User: a"
curl -X POST http://localhost:8000/api/integration/connectors/<ID>/sync  -H "X-Role: senior_analyst" -H "X-User: a"

# 3. 注入一次排产变更后，查看物化运行与事实变更事件
curl http://localhost:8000/api/integration/connectors/<ID>/runs -H "X-Role: operator" -H "X-User: op"
curl -N http://localhost:8000/api/integration/events -H "X-Role: operator" -H "X-User: op"
```
**预期**：增量归一为 A-Box 事实、产生 `fact_materialization_run`（含水位/变更/事件引用）与事实变更事件；事件→结论刷新 **≤ 5s** 且仅受影响子图重算。
**超时验证**：令 APS 不可达 → run `status=timeout`、`cursor_to=null`、告警、知识库不被污染。
**幂等验证**：重复/乱序投递同一变更 → 不重复物化。
**覆盖**：FR-014~019 / SC-004,005。

---

## US4 — Action 编排与风险报告（P4）

```bash
# 注入触发"需专用化"的事实后，查看自动编排的动作（应有工单+告警）
curl http://localhost:8000/api/actions?conclusion_id=<CID> -H "X-Role: operator" -H "X-User: op"

# 排期冲突 → schedule_block + advisory_writeback；回写被拒
curl -X POST http://localhost:8000/api/actions/<AID>/writeback-result \
  -H "X-Role: senior_analyst" -H "X-User: a" -H "Content-Type: application/json" \
  -d '{"writeback_status":"not_accepted"}'

# 报告产物：JSON + PDF（PDF 含 QA 签批信息）
curl http://localhost:8000/api/reports/<CID>      -H "X-Role: operator" -H "X-User: op"
curl -o report.pdf http://localhost:8000/api/reports/<CID>/pdf -H "X-Role: operator" -H "X-User: op"
```
**预期**：动作类结论 100% 触发对应编排并留痕（动作类型/触发结论/规则链/结果/回写状态）；回写为建议性、`not_accepted` 不算失败；报告 PDF+JSON 双产物生成。
**覆盖**：FR-020~024 / SC-006,007。

---

## US5 — 实时推理看板（P5）

1. 前端打开 **集成 → 实时推理看板**：展示设备×产品相容性热力图 + 未来排期风险。
2. 触发一次事实变更（重跑 US3 步骤 3）→ 看板对应单元 **≤5s 近实时刷新**。
3. 点击任一单元 → 展开规则链 ID + 法规依据。

```bash
curl http://localhost:8000/api/integration/dashboard -H "X-Role: operator" -H "X-User: op"
curl http://localhost:8000/api/reasoning/conclusions/<CID>/trace -H "X-Role: operator" -H "X-User: op"
```
**覆盖**：FR-025~027 / SC-005,007。

---

## US6 — 合规硬化：哈希链 / QA 签名 / RBAC（P5）

```bash
# 1. 审计哈希链完整性校验（完好 → ok:true）
curl http://localhost:8000/api/compliance/audit/verify -H "X-Role: qa" -H "X-User: qa01"

# 2. 高风险结论未签名前不生效、不触发动作；QA 重认证签名后方生效
curl http://localhost:8000/api/compliance/signatures/pending -H "X-Role: qa" -H "X-User: qa01"
curl -X POST http://localhost:8000/api/compliance/signatures \
  -H "X-Role: qa" -H "X-User: qa01" -H "Content-Type: application/json" \
  -d '{"conclusion_id":"<CID>","username":"qa01","password":"<pwd>","meaning":"已复核批准生效"}'
# → 201 effective:true；该结论的 suppressed 动作被解除

# 3. RBAC 边界：operator 尝试写 → 403
curl -X POST http://localhost:8000/api/reasoning/incremental \
  -H "X-Role: operator" -H "X-User: op" -H "Content-Type: application/json" -d '{}'
# → 403
```
**篡改验证**：直接改一条 `audit_log` 记录后 `verify` → `ok:false` + `broken_at_seq` 定位首个断点。
**覆盖**：FR-028~031 / SC-008,009,010。

---

## 验收对照

| User Story | 主要 FR | SC |
|---|---|---|
| US1 抽取接线 | FR-001~007 | SC-001 |
| US2 对齐审核+DB源 | FR-008~013 | SC-002,003 |
| US3 实时事实源 | FR-014~019 | SC-004,005 |
| US4 Action+报告 | FR-020~024 | SC-006,007 |
| US5 看板 | FR-025~027 | SC-005,007 |
| US6 合规硬化 | FR-028~031 | SC-008,009,010 |

后端契约/集成测试见 `backend/tests/test_api/`（test_extraction_pipeline / test_alignment_review / test_aps_connector / test_fact_materialization / test_incremental_reasoning / test_action_engine / test_compliance / test_risk_report）。
