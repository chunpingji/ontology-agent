# Tasks: 报告输出语义化

## Phase 1: Setup

- [x] T001 阅读设计与Spec Kit流程，生成 spec.md、clarify及 checklists/requirements.md。
- [x] T002 并行只读调研后端/前端/语义退役，生成 plan.md、research.md、data-model.md、contracts/、quickstart.md。
- [x] T003 固定fixture/源码/实际来源基线与退役清单于 specs/020-report-output-semantics/baseline.json。

## Phase 2: Foundation

- [x] T004 严格版本/类型/绑定/投影/呈现契约 backend/app/services/reporting/template_v2.py。
- [x] T005 模型与不可变守卫 backend/app/models/reporting.py、Alembic 0027_report_output_semantics.py。
- [x] T006 服务端契约注册及审核发布 backend/app/services/reporting/contract_registry.py。

## Phase 3: US1 authoring contracts

- [x] T007 [US1] 先写错误IRI/类型/引用/循环反例 backend/tests/test_reporting/test_output_contracts.py。
- [x] T008 [US1] 类型推导、精确路径、DAG、RequirementPlan backend/app/services/reporting/template_compiler.py。
- [x] T009 [US1] 模板compile/revision/publish及旧客户端守卫 backend/app/api/ast_templates.py、report_runs.py。
- [x] T010 [US1] 三属性编辑器/共享影响/重排/输入标记 frontend/src/components/reporting/output-template-editor.tsx。

## Phase 4: US2 frozen input and output

- [x] T011 [US2] 先写主体/时间/来源/字段冲突/部分集合反例 backend/tests/test_reporting/test_output_resolution.py。
- [x] T012 [US2] 通用facts/context/workflow/derived绑定 backend/app/services/reporting/binding_resolver.py。
- [x] T013 [US2] 逐值类型/状态/lineage及UseResolution/Coverage backend/app/services/reporting/input_resolver.py。
- [x] T014 [US2] 来源和输入冻结/幂等/预算 backend/app/services/reporting/report_snapshot.py、report_run_service.py。
- [x] T015 [US2] 通用OutputAST与composed/table/form/list/static backend/app/services/reporting/output_ast.py、output_renderer.py。
- [x] T016 [US2] 受限assisted授权/引用/失败与审核 backend/app/services/reporting/narrative_renderer.py。
- [x] T017 [US2] 同AST的DOCX/网页与内容寻址原子工件 backend/app/services/reporting/output_renderer.py、frontend/src/components/reporting/output-preview.tsx。
- [x] T018 [US2] 报告运行/预览/输入/覆盖/输出/重试/下载API backend/app/api/report_runs.py。
- [x] T019 [US2] 固定run/attempt预览和追溯 frontend/src/components/reporting/report-run-panel.tsx。

## Phase 5: US3 conditions and claims

- [x] T020 [US3] 条件定义/关联/三值求值/范围和循环 backend/app/services/reporting/condition_resolver.py。
- [x] T021 [US3] 规则精确修订/逐声明前提/ClaimCatalog backend/app/services/reporting/claim_catalog.py。
- [x] T022 [US3] 通用结构化派生view及逐值lineage backend/app/services/reporting/binding_resolver.py。
- [x] T023 [US3] 规则迁移台账/停止seed原位升级 backend/app/services/reasoning/rule_migration.py、seed_declarative.py。
- [x] T024 [US3] FALSE/UNKNOWN/非法等级/计划/缺确认记录反例 backend/tests/test_reporting/test_output_rules.py。

## Phase 6: US4 signing

- [x] T025 [US4] 先写真实身份/CAS/幂等/不可变/恢复反例 backend/tests/test_reporting/test_report_signing.py。
- [x] T026 [US4] 固定内容/审核/会话/账户重认证/追加签名事件 backend/app/services/reporting/report_signing.py。
- [x] T027 [US4] 冻结签署/封装/恢复及正式门禁 backend/app/services/reporting/report_signing.py。
- [x] T028 [US4] 独立签署API及界面 backend/app/api/report_runs.py、frontend/src/components/reporting/report-signing-panel.tsx。

## Phase 7: US5 migration and integration

- [x] T029 [US5] 通用V1及32项迁移/七列单表/共享名册 backend/app/services/reporting/template_migration.py。
- [x] T030 [US5] 保存逐Slot与R-RA1～5逐声明草稿/问题 specs/020-report-output-semantics/migrations/。
- [x] T031 [US5] 原预览/覆盖/报告门面统一，无详情隐式来源写 backend/app/api/ast_templates.py、extraction.py、reports.py。
- [x] T032 [US5] 模板创建/列表/详情/报告阅读版本分流 frontend/src/app/(dashboard)/settings/ast-templates/、components/reports/。
- [x] T033 [US5] 生产正则/finder/可执行配置/旧链退役及静态动态审计 scripts/audit_report_semantics.py、相关生产模块。

## Phase 8: Validation

- [x] T034 API/迁移/独立事务并发及来源变更集成 backend/tests/test_api/test_report_runs_v2.py。
- [x] T035 前端认证头/稳定引用/版本/AST契约 frontend/tests/reporting-v2.test.mjs。
- [x] T036 跑后端/前端适当回归、lint/typecheck/build，记录 specs/020-report-output-semantics/validation.md。
- [ ] T037 实际默认来源和部署入口验收、规则/条件人工审核、独立模型/性能与最终包门禁，记录 validation.md。

## Dependencies and execution

T001→T002→T003→T004～T006→US1→US2→US3→US4→US5→验证。US1编译与编辑独立可验，US2确定性内容先于assisted，US3不放宽上游资格，US4只消费固定正文。不同文件测试准备可并行；所有共享文件修改顺序执行。每项实际通过后勾选，业务审核/部署未完成保持未勾选。

## 本地验证结论

- [x] T038 用户调整：进入模板定义前保存模板与输出样例，附件重试保持模板身份，验证持久编辑与历史列表往返；结果见 [template-entry-validation.md](template-entry-validation.md)。
- [x] T039 保存等待修复：消除大解析 JSON 重复传输，新增附件 204 确认响应及保存超时，成功状态不依赖导航完成；验证大样例、停滞请求、停滞导航及提交失败，见 [template-save-latency-validation.md](template-save-latency-validation.md)。

T001～T036 的实现与本地检查见 [validation.md](validation.md)。全量后端 1034 passed；隔离 PostgreSQL 5 passed；前端 15 项 Node 测试和 8 项浏览器检查通过。全仓既有 lint 错误、真实来源/业务审核、独立模型质量、性能与部署验收如实记录；T037 保持未完成。
