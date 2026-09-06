# Tasks: 统一文档证据与通用语义抽取

**Input**: spec.md、plan.md、research.md、data-model.md、contracts/evidence-api.md、quickstart.md。
**Tests**: 用户设计及宪章要求契约/集成测试；测试先写并观察失败，再实现。完成标记须附实际验证依据。
**Scope**: 完整实现 FR-001—FR-028；发布金标与性能作为独立任务，不用工程测试替代。

## Phase 1: Setup

- [X] T001 创建并澄清 specs/019-evidence-semantic-extraction/spec.md 与 checklists/requirements.md，沿用两轮评审及复审 P2。
- [X] T002 生成 specs/019-evidence-semantic-extraction/plan.md、research.md、data-model.md、contracts/evidence-api.md、quickstart.md 并更新 CLAUDE.md 计划引用。
- [X] T003 固定 backend/tests/test_extraction/test_document_annotator.py、test_ontology_typer.py 与 backend/tests/test_reporting/test_coverage_validator.py 基线，101 passed；记录于 research.md。
- [X] T004 核对 backend/.dockerignore、frontend/.dockerignore、.gitignore 和 frontend/eslint.config.mjs，补齐必要生成物忽略并完成 Spec Kit 制品一致性检查（FR-025、FR-028）。

## Phase 2: Foundational

- [X] T005 先编写 backend/tests/test_extraction/test_evidence_contracts.py，覆盖 anchor、四类 provenance、逐值候选、否定/条件资格与引用约束（FR-007、FR-008、FR-009、FR-012）。
- [X] T006 实现 backend/app/schemas/evidence.py 的严格证据、主体、scope、候选、字面量及任务契约（FR-005—FR-014）。
- [X] T007 实现 backend/app/services/extraction/evidence_identity.py 的规范哈希、角色隔离、候选/依赖版本身份（FR-001、FR-004、FR-011）。
- [X] T008 实现 backend/app/services/extraction/text_scanner.py 的无正则通用字符/编号/KV/文本解析基础，并在 backend/tests/test_extraction/test_text_scanner.py 验证（FR-002、FR-022）。

## Phase 3: US1 统一结构与模板来源

**Goal**: 分析、模板、抽取同一 IR；模型关闭仍有结构草稿。
**Independent Test**: 重复标题、多层嵌套/合并表、非 BMP 偏移及换样例 origin 失效。

- [X] T009 [US1] 先编写 backend/tests/test_extraction/test_document_ir.py 与 backend/tests/test_extraction/test_template_structure_builder.py 的结构/来源契约测试（FR-001—FR-004）。
- [X] T010 [US1] 在 backend/app/services/extraction/docx_structure.py 替代结构正则，采集递归表路径和真实源单元格，保留章节/分页兼容契约（FR-001、FR-002、FR-022）。
- [X] T011 [US1] 实现 backend/app/services/extraction/document_ir.py 的 IR、EvidenceUnit、Anchor、OffsetMap、序列化和结构哈希（FR-001、FR-002、FR-004）。
- [X] T012 [US1] 实现 backend/app/services/extraction/word_analysis.py，共享入口接入 backend/app/api/document_analysis.py、ast_templates.py、extraction.py（FR-001、FR-004）。
- [X] T013 [US1] 在 backend/app/services/extraction/document_annotator.py 用 IR 统一段落/嵌套表标注坐标及兼容预览（FR-002）。
- [X] T014 [US1] 实现 backend/app/services/extraction/template_structure_builder.py，修改 slot_suggester.py 仅增强已有骨架 ID 的语义（FR-003、FR-004）。
- [X] T015 [US1] 修改 backend/app/services/reporting/ast_template.py、schemas/extraction.py 和 models/extraction.py，声明 origin 及模板分析快照；新增 backend/alembic/versions/0025_template_evidence.py 并在上传集成验证前完成升级（FR-003、FR-027）。
- [X] T016 [US1] 修改 frontend/src/lib/api.ts、components/extraction/template-slot-editor.tsx，保留稳定 ID、analysis 及 section/group/slot origin（FR-003）。
- [X] T017 [US1] 修改 frontend/src/components/extraction/word-viewer.tsx，按证据路径定位、码点转 UTF-16 并展示失效来源（FR-002、FR-003）。
- [X] T018 [US1] 运行 backend/tests/test_extraction/test_docx_structure.py、test_word_section_tree.py、test_word_formatting.py 和模板上传/来源回归，更新 quickstart.md 验证记录（FR-028）。

## Phase 4: US2 通用实体、属性与关系

**Goal**: 主体先行、逐值绑定、有证据的极性与拒答，无业务 finder。
**Independent Test**: 标题主体正文值、同段 A/B、多跳、否定条件、缓存隔离。

- [X] T019 [US2] 先编写 backend/tests/test_extraction/test_semantic_binding.py、test_hierarchical_context.py、test_literal_normalizer.py（FR-005—FR-011）。
- [X] T020 [US2] 实现 backend/app/services/extraction/evidence_scope.py 与 hierarchical_context.py，含主体/竞争主体、扩展证据、预算与完整缓存身份（FR-007、FR-010、FR-011）。
- [X] T021 [US2] 实现 backend/app/services/extraction/literal_normalizer.py，无正则数字/单位/范围/比较解析，未知表达拒绝规范化（FR-008）。
- [X] T022 [US2] 实现 backend/app/services/extraction/semantic_binding.py，严格本地主体条件模型输出、独立绑定和正向资格（FR-005—FR-009、FR-025）。
- [ ] T023 [US2] 实现 backend/app/services/extraction/extraction_tasks.py，主体召回→属性/对象任务→关系判定、循环/窗口预算和 checkpoint（FR-005、FR-006、FR-010、FR-011、FR-024）。已补 token 预算菜单、精简上下文、唯一引用定位、封闭短编号、有效同批实体保留、失败恢复/累计预算，以及主体/谓词轮转、对象分批和按任务类型约束的必填值/极性。此前实际召回仍为 80/440 个非空证据单元、33 个模型实体待审；本轮独立种子诊断新增 1 条通过绑定校验的真实关系，不能视为全文进展。模板优先级、完整属性/关系、Web 批次崩溃/并发预约仍未闭合，149 项定向回归与真实诊断见 validation.md。
- [X] T024 [US2] 修改 backend/app/services/extraction/gliner_extractor.py、ontology_typer.py、document_annotator.py，移除全局数值正则过滤及旧属性绑定主路径（FR-005、FR-008、FR-022）。
- [ ] T025 [US2] 将 backend/app/services/extraction/relation_extractor.py 主路径接入通用 runner，按真实实例/路径调度；移除业务类算法分派（FR-006、FR-007、FR-010、FR-022）。
- [X] T026 [US2] 改造 backend/app/services/llm/local_client.py 的无正则结构化响应解析与有界失败，补对应测试（FR-011、FR-022、FR-024）。
- [X] T027 [US2] 在 backend/tests/test_extraction/test_semantic_binding.py 验证新增本体类型无需代码分支、标题数值链路及否定/条件断言（FR-006、FR-009、FR-028）。

## Phase 5: US3 审核、逐值来源与幂等提交

**Goal**: 真实事实与断言可回放，审核/提交独立，失败不发布。
**Independent Test**: CAS、重复提交、真实 World、否定/条件、外部来源、归并与重启恢复。

- [X] T028 [US3] 先编写 backend/tests/test_api/test_evidence_api.py 和 backend/tests/test_extraction/test_fact_commit.py，使用严格失败桩及真实临时 World（FR-012—FR-017、FR-025、FR-028）。
- [X] T029 [US3] 实现 backend/app/models/evidence.py、models/__init__.py 和 backend/alembic/versions/0026_evidence_facts.py（依赖 0025），保存候选版本/审核/提交/断言/快照头 CAS/分析/coverage 与报告字段（FR-003、FR-012—FR-017、FR-027）。
- [X] T030 [US3] 实现 backend/app/services/extraction/candidate_store.py，逐值幂等创建、CAS 审核、编辑和依赖失效/归并（FR-013、FR-014、FR-017）。
- [X] T031 [US3] 实现 backend/app/services/ontology_instance_writer.py，隔离 World、严格实例/typed literal/对象引用/断言来源写入与回读（FR-009、FR-015、FR-016）。
- [X] T032 [US3] 实现 backend/app/services/fact_commit.py 的不可变清单/outbox、幂等重放、失败恢复、继承快照及版本 CAS 发布；不同键并发冲突重读合并，验证最终快照包含两批事实（FR-015、FR-016）。
- [X] T033 [US3] 实现 backend/app/api/evidence.py 并注册到 backend/app/main.py，接入抽取/候选/审核/归并/提交/重试/快照/来源接口与权限（FR-013—FR-017、FR-025）。
- [ ] T034 [US3] 修改 backend/app/api/extraction.py 的候选持久化/旧提交适配及公共事实查询，历史无来源记录标 legacy_unverified（FR-013—FR-017、FR-027）。
- [ ] T035 [US3] 修改 backend/app/services/extraction/equipment_source.py 及外部 source，将真实记录版本/字段和身份匹配分开存为候选（FR-012）。
- [X] T036 [US3] 在 frontend/src/lib/api.ts、components/extraction/ 增加逐值/极性/来源审核、独立提交和失败重试界面（FR-009、FR-012—FR-017）。
- [X] T037 [US3] 验证 backend/tests/test_extraction/test_fact_commit.py 的真实图持久化、并发幂等、失败重启、否定/条件独立回读和历史迁移（FR-009、FR-015—FR-017、FR-027、FR-028）。

## Phase 6: US4 实例覆盖、受控补抽与报告

**Goal**: A 不遮盖 B，正确路径/对象逐值满足，正式报告只读发布快照。
**Independent Test**: 错主体/谓词/对象、exists/all、开放集合、已提交否定及两轮补抽停止。

- [X] T038 [US4] 先编写 backend/tests/test_reporting/test_fact_selector.py、test_instance_coverage.py，含否定范围和开放集合（FR-018、FR-019、FR-021）；含新增逐台设备路径与规范值等价测试，见 validation.md 的 179 项定向回归。
- [X] T039 [US4] 实现 backend/app/services/fact_selector.py，精确实例/完整谓词路径/方向/对象/属性及正向资格筛选（FR-016、FR-018、FR-021）；真实快照及完整路径、否定、条件、基数冲突与无损小数身份均已验证。
- [ ] T040 [US4] 实现 backend/app/services/extraction/template_extraction_plan.py，类型到实例展开、量词/基数、对象发现、状态和有界补抽（FR-018—FR-020）。
- [X] T041 [US4] 修改 backend/app/services/reporting/coverage_validator.py 与 API 正式覆盖入口，使用已发布事实和统一 selector（FR-018、FR-019、FR-021）；无快照只显示未满足，候选不能填充正式覆盖，见 test_evidence_api.py、test_snapshot_workflow.py。
- [ ] T042 [US4] 修改 backend/app/services/reporting/risk_report_generator.py、narrative_generator.py、fact_bridge.py 与 backend/app/api/extraction.py，冻结报告快照并移除生成期事实富化（FR-016、FR-021、FR-027）。
- [X] T043 [US4] 修改 frontend/src/components/extraction/ 及 frontend/src/lib/api.ts 显示实例缺口、否定不存在、未完成、补抽停止原因（FR-018—FR-021）；文档抽屉与模板源文档页均已接入，tsc、定向 ESLint 与 build 通过。
- [ ] T044 [US4] 运行 backend/tests/test_reporting/ 的覆盖/报告集成回归，验证正式查询不读取未发布事实（FR-016、FR-018—FR-021、FR-028）。

## Phase 7: US5 无正则生产链与退役

**Goal**: 删除旧机制而非配置化搬移，模型故障不隐式回退。
**Independent Test**: 源码/配置/产物审计及完整故障路径。

- [ ] T045 [US5] 实现 scripts/audit_extraction_rules.py，输出逐符号/配置/动态入口职责和第三方依赖边界（FR-022、FR-023）。
- [ ] T046 [US5] 替代 backend/app/services/extraction/sentence_grouping.py、document_classifier.py、word_tree_summarizer.py、vocabulary.py 和 transforms.py 的正则生产依赖（FR-022、FR-024）。
- [ ] T047 [US5] 退役 backend/app/services/extraction/document_profile.py、pipeline.py、relation_extractor.py 和 llm_gap_filler.py 中可执行 pattern/finder/隐式 fallback（FR-022、FR-024）。
- [ ] T048 [US5] 替代 backend/app/services/reporting/aps_equipment_resolver.py、risk_report_generator.py、narrative_generator.py 的业务文本兜底及 ast_template.py、placeholder_util.py、docx_renderer.py 的格式正则（FR-021、FR-022）。
- [ ] T049 [US5] 在 specs/019-evidence-semantic-extraction/retirement-diff.md 生成本体注解三元组 diff，再外科式移除 ontology/slpra/slpra-integration.ttl 中执行规则；复用既有同步/发布机制验证元数据与 World 一致、失败回滚、健康检查、批次审计及其他公理/注释不变（FR-022、FR-025）。
- [ ] T050 [US5] 迁移配置加载/API 与 frontend/src/components/data-mapping/binding-editor.tsx、components/ontology/property-binding-editor.tsx，拒绝执行旧 pattern 并显示迁移诊断（FR-022、FR-027）。
- [ ] T051 [US5] 依据 scripts/audit_extraction_rules.py 清单消除其他自有生产可达正则及业务 finder，记录完整替代/历史归档结果（FR-022、FR-023）。
- [ ] T052 [US5] 编写并运行 backend/tests/test_extraction/test_generic_offline_pipeline.py，禁止旧入口后验证模型关闭/超时/IR失败/提交失败；本地可选 NER/嵌入缺包或权重不使结构化路径失败、不标 degraded，运行期禁止下载，同步模型推理经线程卸载（FR-024、FR-025、FR-028）。

## Phase 8: US6 评测和发布门禁

**Goal**: 工程可验证、质量证据可复现，不伪造独立金标。
**Independent Test**: 无真实金标或固定模型版本时 release gate 拒绝通过。

- [ ] T053 [US6] 实现 scripts/evaluate_extraction.py 及 backend/tests/test_extraction/test_extraction_evaluation.py，精确主体/值/关系、拒答、来源、覆盖误满足与门禁（FR-026、FR-028）。
- [ ] T054 [US6] 在 specs/019-evidence-semantic-extraction/evaluation-protocol.md 定义独立人工标注、分组隔离、C0—C4 消融及固定模型/策略/性能输入；质量阈值、分桶、生产 SLO 和内存预算必须有来源配置，任一缺失则发布 gate 失败（FR-026）。
- [ ] T055 [US6] 以实际独立人工金标及固定真实模型运行 scripts/evaluate_extraction.py，记录按类型/版式质量与多主体改进，未具备数据不可勾选（FR-026；SC-009、SC-010、SC-012）。
- [ ] T056 [US6] 实测生产同桶 p95/内存和旧入口禁用的全链质量，记录 specs/019-evidence-semantic-extraction/validation.md；未有真实结果不可勾选（FR-026；SC-011、SC-012）。

## Phase 9: Polish

- [ ] T057 运行全部相关 backend/tests/、frontend lint/tsc/build 与 Alembic 升级/持久化验证，更新 specs/019-evidence-semantic-extraction/validation.md（FR-028）。
- [ ] T058 完成源码/配置/最终产物审计和实现评审，更新 specs/019-evidence-semantic-extraction/retirement-inventory.json 及 quickstart.md，保留真实未完成门禁（FR-022、FR-023、FR-028）。
- [ ] T059 更新 README.md、配置文档及 specs/019-evidence-semantic-extraction/tasks.md 的完成证据、剩余外部依赖和迁移说明（FR-024、FR-027、FR-028）。

## Dependencies & Execution Order

Setup → Foundational → US1 → US2 → US3 → US4 → US5 → US6 → Polish。
US3 持久化测试可在 US2 模型工作期间独立设计；US5 审计可提前只读运行，但删除须等待对应替代实现和测试。每故事只有其依赖与独立测试通过才算完成。

## Parallel Opportunities

研究阶段已按 plan 技能并行核查 IR/模板与提交/覆盖；实现阶段同文件任务顺序执行。可并行运行独立测试命令和静态分析，不能用并行绕过基础模型、迁移或审核依赖。

## Implementation Strategy

先通过结构与来源故事验证基础，再逐故事完整接入生产 UI/API。测试失败先修复再推进依赖任务；不提交/推送现有工作区改动。Spec Kit 可选 git commit 钩子保留未执行，必选 feature 钩子已完成。人工金标和生产性能只能在真实证据具备时完成，不将 T055/T056 改为桩测试以勾选。
