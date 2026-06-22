# Phase 1 Data Model: 多源抽取与实时推理 —— 能力二/能力三 GAP 闭合

**Feature**: `002-extraction-realtime-reasoning` | **Date**: 2026-06-22 | **Plan**: [plan.md](./plan.md)

数据库：PostgreSQL 16（库 `slpra`）。所有结构变更经**单一 Alembic 迁移** `alembic/versions/0002_extraction_realtime.py`，接到既有启动迁移链（`main._run_migrations` → `upgrade head`）。约定与 0001 一致：UUID 主键（`UUID(as_uuid=True)`，审计/影子类用既有惯例）、`DateTime(timezone=True)`、JSON 列。

**A-Box 事实实例**不建关系表——经 `OntologyEngine` 写入 Owlready2 World（`OWL_STORE_PATH`）并由既有 `KGStore.sync_individual_to_shadow` 投影到既有 `entity_shadow`；本特性不改影子表结构。

图例：🆕 新表 · ➕ 列扩展 · ♻️ 既有不变（引用）。

---

## 1. ➕ 扩展既有表

### 1.1 `extraction_candidates`（➕ 扩展 `models/extraction.py`）

支撑跨源归组、规范实例标记与多种候选类型（实例/类/关系/Action）。

| 新增列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| `candidate_kind` | `String(20)` | `not null`, default `"instance"` | `instance`/`class`/`link`/`action`（FR-004/005/012） |
| `group_key` | `String(500)` | nullable, index | 跨源对齐归组键（设备=唯一编号；药品=活性成分+剂型+规格）（FR-009） |
| `is_canonical` | `Boolean` | default `false` | 组内规范实例标记（FR-009/SC-003） |
| `source_ref` | `String(200)` | nullable | 来源标识（文件名/表名/源系统），用于跨源溯源 |
| `degraded_reason` | `String(200)` | nullable | LLM 回退时降级原因（FR-007） |
| `merged_into_id` | `UUID` | nullable, FK→`extraction_candidates.id` | 合并目标候选（merge 操作，FR-010） |
| `action_conditions` | `JSON` | nullable | Action 候选的前置/后置条件（FR-005） |

**`review_status`（既有列）状态机**（FR-010/011）：
```
pending ──confirm──▶ confirmed ──(commit)──▶ committed   (committed_iri 落值，进入知识库)
   │ ├─reject──▶ rejected
   │ ├─merge───▶ merged      (merged_into_id 落值)
   │ └─split───▶ split       (派生新候选，原置 split)
   └─ambiguous──▶ pending(needs_review)   (歧义不自动合并，FR-011)
```
仅 `confirmed→committed` 写入知识库；`rejected`/未确认不进入。

### 1.2 `reasoning_executions`（➕ 扩展 `models/reasoning.py`）

承载"结论生效状态"与签名绑定（FR-030）。

| 新增列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| `requires_signature` | `Boolean` | default `false` | 高风险/专用化/合规阻断结论标记需 QA 签名 |
| `effective` | `Boolean` | default `false` | 是否生效；`requires_signature=true` 时签名前恒 `false`（FR-030/SC-009） |
| `signature_id` | `UUID` | nullable, FK→`electronic_signatures.id` | 不可分割绑定的签名（Part 11） |
| `affected_subgraph` | `JSON` | nullable | 本次（增量）重算的受影响子图标识（设备/产品/区域）（FR-017） |
| `superseded_by` | `UUID` | nullable, FK→`reasoning_executions.id` | 被后续增量重算结论取代（保留历史，不就地改写） |

**生效状态机**（FR-030）：
```
created
  ├─ requires_signature=false ─▶ effective=true            (普通结论直接生效)
  └─ requires_signature=true  ─▶ pending_signature
                                   └─QA 签名──▶ effective=true   (signature_id 绑定)
未生效（pending_signature）期间：对外动作（工单/告警/回写）被抑制、置待签名（边界用例）。
```

### 1.3 `audit_log`（➕ 扩展 `models/reasoning.py`）

升级为 append-only 哈希链（FR-028/029）。

| 新增列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| `prev_hash` | `String(64)` | nullable | 前一条 `entry_hash`；创世为空/全 0 |
| `entry_hash` | `String(64)` | `not null`, index | `SHA-256(prev_hash ‖ 规范化记录)`（hex） |
| `seq` | `Integer` | `not null`, unique, index | 链序号，单调递增，定位断裂点 |

**不变式**：只追加；禁止 UPDATE/DELETE（应用层单写路径 `audit.py` 保证）；`entry_hash[i]` 必须等于以 `prev_hash[i]=entry_hash[i-1]` 重算之值，否则校验报告在 `seq=i` 断链。

### 1.4 `integration_connectors`（➕ 扩展 `models/integration.py`）

支撑轮询调度与同步水位（FR-014/016）。

| 新增列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| `ingest_mode` | `String(20)` | default `"poll"` | `poll`/`push`/`hybrid`（R4） |
| `poll_interval_seconds` | `Integer` | default `2` | 轮询周期，默认 ≤2s（FR-014/SC-005 余量） |
| `sync_cursor` | `JSON` | nullable | 同步水位（增量去重基准，FR-019） |
| `last_status` | `String(20)` | nullable | `ok`/`timeout`/`error`（FR-018 告警依据） |
| `last_error` | `Text` | nullable | 最近一次失败信息（脱敏，R7） |

> 敏感凭据不入 `connection_config`，经 env/`settings` 注入（R7）。

---

## 2. 🆕 新增表

### 2.1 🆕 `fact_materialization_run`（`models/integration.py`）

一次增量物化运行的完整留痕（FR-016/SC-004，US3 AC4）。

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| `id` | `UUID` | PK | |
| `connector_id` | `UUID` | FK→`integration_connectors.id`, index | 来源连接器 |
| `started_at` | `DateTime(tz)` | default now | |
| `finished_at` | `DateTime(tz)` | nullable | |
| `status` | `String(20)` | default `"running"` | `running`/`success`/`timeout`/`error` |
| `cursor_from` | `JSON` | nullable | 起始水位 |
| `cursor_to` | `JSON` | nullable | 结束水位（成功时推进） |
| `change_count` | `Integer` | default `0` | 物化的变更条目数 |
| `changes` | `JSON` | nullable | 变更条目摘要（实体 IRI/操作/版本） |
| `event_ids` | `JSON` | nullable | 产生的事实变更事件引用 |
| `error_message` | `Text` | nullable | 失败原因；失败时**不推进 cursor_to**（保留上一良好状态，FR-018） |

**幂等键**：`(connector_id, 事实版本/内容哈希)` 去重，乱序/重投不重复物化（FR-019）。

### 2.2 🆕 `action_execution`（`models/reasoning.py`）

结论触发的动作执行与留痕（FR-020–023）。

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| `id` | `UUID` | PK | |
| `conclusion_id` | `UUID` | FK→`reasoning_executions.id`, index | 触发结论 |
| `action_type` | `String(50)` | not null | `dedication_work_order`/`inactivation_task`/`recleaning_task`/`schedule_block`/`advisory_writeback`/`alert`/`generate_report` |
| `status` | `String(20)` | default `"pending"` | 见状态机 |
| `payload` | `JSON` | nullable | 动作内容（工单/任务/排期冲突详情） |
| `rule_chain` | `JSON` | nullable | 触发规则链 ID + 法规依据（溯源，FR-023） |
| `writeback_status` | `String(20)` | nullable | `advised`/`accepted`/`not_accepted`/`n_a`（建议回写，FR-022/边界"回写被拒"） |
| `result` | `JSON` | nullable | 执行结果 |
| `created_at` | `DateTime(tz)` | default now | |
| `updated_at` | `DateTime(tz)` | default now, onupdate | 人工流转更新时间 |

**状态机**（内部记录可人工流转）：
```
suppressed (结论未签名时)  ──签名生效──▶ pending ──▶ executed ──(人工)──▶ in_progress ──▶ done
                                              └─▶ failed
advisory_writeback.writeback_status: advised ──▶ accepted | not_accepted   (外部决定，not_accepted 不算失败)
```

### 2.3 🆕 `electronic_signatures`（`models/reasoning.py`）

21 CFR Part 11 电子签名（FR-030）。

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| `id` | `UUID` | PK | |
| `conclusion_id` | `UUID` | FK→`reasoning_executions.id`, index, **not null** | 不可分割绑定对象 |
| `signer` | `String(100)` | not null | 签名人（QA） |
| `signer_role` | `String(50)` | not null | 固定 `qa` |
| `meaning` | `String(200)` | not null | 签名含义（如"已复核批准生效"）（Part 11） |
| `reauth_verified` | `Boolean` | not null | 重认证（用户名+密码）通过标记 |
| `signed_at` | `DateTime(tz)` | default now | 签名时间 |
| `audit_seq` | `Integer` | nullable | 关联审计链序号（防抵赖留痕） |

**约束**：签名写入即在 `reasoning_executions` 置 `effective=true`、回填 `signature_id`，并写一条审计链记录。**唯一绑定**：每条 `conclusion_id` 一条有效签名。

> 报告产物（RiskAssessmentReport）**不单独建表**：PDF 落 `config` 配置的报告输出目录，元信息（路径/JSON/签批引用）随 `action_execution(action_type=generate_report)` 的 `result`/`payload` 留痕；JSON 由 `GET /api/reports/{conclusion_id}` 即时组装。

---

## 3. 实体关系总览

```
extraction_jobs 1──* extraction_candidates  (➕ candidate_kind/group_key/is_canonical/merged_into_id…)
                                  └─(confirmed→committed)──▶ A-Box 个体 / entity_shadow (♻️)

integration_connectors 1──* fact_materialization_run (🆕)
        │ (poll/push)                      └─发布──▶ 事实变更事件(进程内, events.py)──▶ 增量重算
        └─(➕ ingest_mode/sync_cursor/poll_interval…)

reasoning_executions (➕ effective/requires_signature/signature_id/affected_subgraph)
        ├─1──* action_execution (🆕)
        └─1──1 electronic_signatures (🆕, Part 11 绑定)

audit_log (➕ prev_hash/entry_hash/seq)  ◀── 全链路操作 append-only 写入 (抽取/对齐/物化/推理/动作/签名)
```

## 4. 校验规则（来自需求）

- **VR-1**（FR-009/011）：`group_key` 相同→归一组；同 `group_key` 出现疑似不同实体→不自动合并，候选 `review_status` 维持需人工审核。
- **VR-2**（FR-010）：仅 `review_status=confirmed` 的候选可 `commit`（落 `committed_iri`）进入知识库。
- **VR-3**（FR-019）：`fact_materialization_run` 以 `(connector_id, 版本/哈希)` 幂等；重复事件不产生新 A-Box 写。
- **VR-4**（FR-018）：物化 `status∈{timeout,error}` 时**不推进** `cursor_to`、不写 A-Box，置 `connector.last_status` 并告警。
- **VR-5**（FR-028/029）：`audit_log` 每条 `entry_hash=SHA-256(prev_hash‖规范化记录)`；`seq` 连续；校验失败定位首个 `seq`。
- **VR-6**（FR-030）：`requires_signature=true` 的结论在无有效 `electronic_signatures` 前 `effective=false`，其 `action_execution` 恒 `suppressed`。
- **VR-7**（FR-022）：`schedule_block`/`advisory_writeback` 不改写外部权威数据；`writeback_status=not_accepted` 不置失败。
- **VR-8**（FR-017）：增量重算 `affected_subgraph` 非空且限定相关设备/产品/区域，禁止全量。
