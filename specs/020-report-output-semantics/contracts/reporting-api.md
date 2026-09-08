# Reporting V2 API contracts

所有入口认证，定义/发布为 senior_analyst，审核/签署按策略和实际角色。资源需服务端验证，拒绝未知字段。409 返回 code/expected/actual，422 返回 diagnostics，含 schema_path/input/output/evidence/remediation。

| Method/path | Contract |
|---|---|
| GET/POST /api/report-contracts | 版本化契约注册、审核/发布，规则/claim 不接受客户端 approved 布尔 |
| POST /api/ast-templates/{id}/compile | schema?、expected_hash；固定契约编译/类型/DAG/requirements，不读取事实 |
| POST /api/ast-templates/{id}/migration-plan | expected_hash、target_schema_version=2；原 schema、32项映射和目标草稿，不改原行 |
| POST /api/ast-templates/{id}/revisions | schema、expected_revision/hash；新 draft，family revision 唯一 |
| POST /api/ast-templates/{id}/publish | expected_hash、compilation_id；拒绝迁移/编译问题，冻结发布 |
| POST /api/report-previews | layout/data/report、template_id或draft_schema、source_bindings、idempotency_key；layout无业务值，其余仅发布来源 |
| POST /api/report-runs | template_id、source_bindings {slot:{job_id,snapshot_id,root_entity_id}}、record_refs、purpose、idempotency_key；受理冻结源 |
| GET /api/report-runs/{id} | 固定来源/快照/AST/工件和状态 |
| GET /api/report-runs/{id}/inputs /coverage /outputs | 同次快照及逐值、消费者、attempt；分页不改变完备性 |
| POST /api/report-runs/{id}/attempts | 新 attempt 复用输入，不读 latest |
| GET /api/report-runs/{id}/artifacts/{artifact_id} | 保存字节/hash，不生成 |
| POST /api/report-runs/{id}/content-versions | expected_body_hash、attempt、idempotency_key；固定正文/输入/签署策略 |
| POST /api/report-content-versions/{id}/reviews | expected_content_hash、decision、reason；真实审核人和追加记录 |
| POST /api/report-content-versions/{id}/signing-sessions | expected_content_hash、review_id、workflow_ref、parent_ref?、idempotency_key |
| POST /api/report-signing-sessions/{id}/signatures | content_hash、signature_slot_id、meaning、expected_signature_revision、password、idempotency_key；密码不保存 |
| POST /api/report-signing-sessions/{id}/signature-events | signature_ref、reason、expected_signature_revision、password、idempotency_key；追加撤销 |
| POST /api/report-signing-sessions/{id}/envelopes | content_hash、signature_revision、purpose、idempotency_key；CAS freeze，重试固定快照 |
| GET /api/report-signing-envelopes/{id} | final_ast/签署/审核/正文/工件，预览下载同封装 |

幂等限 caller+operation+key，同 key不同 payload 为409。正式事实查 EvidenceSnapshot，不接受客户端 fact payload。旧入口按协议转同一服务，V1要求迁移，历史下载保留。模板详情无来源创建副作用。
