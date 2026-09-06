# Evidence API Contracts

日期：2026-09-05。字段使用现有 JSON/FastAPI 约定；新 schema_version=1，所有写端点沿用 senior_analyst 权限与审计。

## 结构与模板

POST /api/document-analysis/word 和 /api/ast-templates/parse-sample 保留 content_json/plain_text 或既有 content 字段，增加 analysis（DocumentIR、结构身份和诊断）。三个入口的相同文件产生相同结构身份。
Section/Group/Slot origin 包含 document_hash/parser_version/structure_hash/evidence_id 及独立 label/value anchor；样例换版明确 invalid，不能文本猜测定位。
suggest-slots 接受来源附属 analysis，结构由 IR 决定，模型仅能修改合法已有 ID 的语义。

## 候选和任务

POST /api/extraction/jobs/{job_id}/evidence/extract：运行通用抽取，返回 run/completion、diagnostics、entity/property/relationship candidates 和输入身份。模型关闭返回 incomplete/disabled（正常配置，不标 degraded），不调用旧 finder。
可选请求体 `{retry_failed: true, reason: "..."}` 显式重试已记录失败任务，理由非空，保留 senior_analyst 门禁和服务端审计。不提供请求体时兼容旧调用：恢复成功及失败状态，继续尚未尝试任务，不重复消耗失败窗口；失败状态仍使 run incomplete。暂停/重试不重置总任务或模型调用预算，也不能将客户端 checkpoint 注入服务端。
可选 `pause_after` 为 1—32，限制本次最多新尝试的任务数而不改变累计预算身份；证据面板默认每批 8 项。无请求体保持原调用兼容。当前同步工作线程接口在该批处理完成后返回，非后台 202 任务接口。
调度版本、传输协议和服务端对象分批预算均属于 run identity；变化时不复用旧 checkpoint。当前主体/谓词轮转及对象分批不会放宽独立绑定、审核和提交条件；必填字段/断言极性缺失不能用模型默认值代替。客户端不得注入对象预算或调度状态。
GET /api/extraction/jobs/{job_id}/evidence：返回有版本候选和任务状态；原文/外部来源和绑定分开。
POST /api/extraction/jobs/{job_id}/evidence/candidates：创建人工/结构化外部候选，服务端仍验证主体、来源、类型和绑定，不信任客户端传入的 passed/confirmed。
PUT /api/extraction/evidence/candidates/{id}/review：expected_revision、decision、reason、可选 edited_payload；过期 409，非法 422，缺权限 403。confirmed 只确认；编辑产生新 revision，需重验审核。
POST /api/extraction/evidence/candidates/{id}/resolve：expected_revision、canonical target；防跨作业非法归并、环和自合并，更新依赖并重验。

## 提交与查询

POST /api/extraction/jobs/{job_id}/evidence/commits：idempotency_key 和 candidate_id/revision 清单。确认未完成或引用过期拒绝。相同 key/content 返回原 commit，不同 content 为 409。不同键的并发提交须对作业快照头使用 CAS；内部重读合并直到发布，最终快照保留两批有效事实，不采用最后写入覆盖。
GET /api/extraction/evidence/commits/{id}：queued/applying/succeeded/failed、snapshot_id、错误及重试次数。
POST /api/extraction/evidence/commits/{id}/retry：原清单幂等重放，不重建节点/断言。
GET /api/extraction/jobs/{job_id}/evidence/snapshot：仅发布快照，包含事实、否定/条件断言及 provenance；无快照返回空的显式未发布状态。
GET /api/extraction/evidence/assertions/{id}/provenance：回放与发布快照一致的值/身份/绑定来源；未发布断言不能通过公共事实入口获取。

否定/条件断言可通过验证、审核、提交；writer 回读独立断言及极性/条件，不要求正向边。缺来源、无绑定、scope 越界或冲突仍拒绝/待处理。

## Coverage、补抽和报告

GET /api/extraction/jobs/{job_id}/evidence/coverage：template_version、snapshot_id、完整目标声明；返回 runtime targets 和 manifest。
POST /api/extraction/jobs/{job_id}/evidence/fill-gaps：同一通用 runner，最多 2 轮、每轮至多 1 次 scope 扩展，相同输入不重复推理；候选待审或无进展停止。
正式 report/ast-coverage 使用同一 FactSelector 和发布快照，冻结 snapshot/manifest/discovery/selector/template 身份；模型与富化不可直接向报告添加事实。
