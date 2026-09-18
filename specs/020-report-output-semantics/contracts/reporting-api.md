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

## 样例创建与源文档能力（2026-09-09 修复）

- 输出样例经 `POST /api/ast-templates/parse-sample` 解析；点击「进入模板定义」即调用 `POST /api/ast-templates` 保存草稿，随后通过 `POST /api/ast-templates/{id}/sample` 附加原始输出格式文件。两步成功后打开该模板 ID 的「AST模板定义」编辑页。关联文档类型、文档编号使用向导值；失败保留向导。模板已创建、附件失败时只重试该 ID 的附件保存，不重复创建，不宣称完整成功。
- 进入模板定义时即已保存。后续编辑使用工作区页签上方的「保存新修订」；源文档状态或识别失败不得隐藏或禁用保存入口。普通编辑入口默认页签不变，旧 `/settings/ast-templates/create` 入口回到列表，不再提供未保存编辑模式。
- 创建请求只携带模板定义与元数据，样例正文及解析结构由附件接口保存，避免重复上传大 JSON。附件接口新增可选 `include_content=false`：校验、解析、原件落盘和数据库提交均成功后返回 `204`，不返回解析全文；省略该参数仍返回原有完整解析结果，供编辑页替换样例使用。
- 向导区分「正在保存模板」「模板草稿已保存，正在保存输出样例」及「模板和输出样例已保存」。保存成功即解除等待锁并提供直接打开已存模板的链接；导航缓慢不得继续显示正在保存，也不得再次提交。保存请求等待上限为 180 秒，超时不等于服务端回滚：提示核对列表；已获得模板 ID 时保留该身份，后续只重试其附件。
- `GET /api/extraction/jobs` 和 `GET /api/extraction/jobs/{id}` 的响应增加可空 `source_mode`，仅暴露 `source_config.mode`，不暴露其他来源配置。前端读取详情后再请求相应正文、识别结果及进度。
- Word `template_default` 保留模板专用正文、证据审核与识别；`doc_repo_preview` 只可读取正文；其他 Word 不调用旧正文/识别入口。普通 Word 的关系图谱及历史入口为 `/analysis?tab=document`，打开入口不隐式创建任务。Excel 共享识别保持原契约。`preview_only` 描述返回内容，不作为执行能力依据。
