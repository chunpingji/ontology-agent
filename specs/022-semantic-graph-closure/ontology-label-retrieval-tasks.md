# 实施任务：S0 本体原生词汇语义检索

输入：[spec.md](spec.md)、[plan](ontology-label-retrieval-plan.md)、
[契约](contracts/ontology-label-retrieval.md)。状态只依据本次已完成工作勾选，
历史 022 测试数量不作为本次证据。

## Phase 1：规范与契约

- [x] OL-T001 在 `specs/022-semantic-graph-closure/spec.md` 记录 S0 授权、范围、需求及验收。
- [x] OL-T002 建立 `contracts/ontology-label-retrieval.md`，确定来源、冻结、v2 查询、8/80/640 预算和剪枝拒绝契约。
- [x] OL-T003 建立 `ontology-label-retrieval-plan.md`、本任务清单和 `ontology-label-retrieval-quickstart.md`，完成设计前后宪章检查。

## Phase 2：OL-US1 本体已有词汇实际参与检索

- [x] OL-T004 [P] 在 `backend/app/services/ontology_engine.py` 读取类、关系、属性的全部 label/altLabel，保留原文、语言及来源；失败不吞为无词汇。
- [x] OL-T005 在 `backend/app/services/extraction/ontology_guided/ontology_lexical.py`、`contracts.py`、`ontology_plan.py` 增加完整词汇冻结和内容核验；保护旧序列化及 hash。
- [x] OL-T006 [P] 在 `backend/app/services/extraction/ontology_guided/lexical_query.py`、`retrieval_query.py` 实现 v2 角色化选词、上限及采用/未采用审计；类词不成为实例提及。
- [x] OL-T007 在 `backend/app/services/extraction/ontology_guided/semantic_reranker.py` 接通冻结 ontology 至实际 embedding/rerank 请求。
- [x] OL-T008 在 `backend/tests/test_extraction/` 和本体引擎相关测试覆盖多语言、仅 altLabel、同名多 IRI、角色/实例隔离、预算以及模型调用输入捕获，对应 OL-AC-01～03/05。

## Phase 3：OL-US2 词汇变化、恢复与校准隔离

- [x] OL-T009 在共享检索和 `backend/app/services/document_analysis/` 适用调用方接通首次准备、后续检索、提交和恢复的同一冻结本体。
- [x] OL-T010 在 `backend/app/services/extraction/ontology_guided/adaptive_retrieval.py` 扩展 v2 校准的本体 hash、词汇 hash、选词版本绑定，保留旧形状；在 `semantic_reranker.py` 及适用门禁于模型调用前拒绝 v2 查询配旧或失配 trial/enforce 校准，无静默切换。
- [x] OL-T011 在 `backend/tests/test_extraction/` 验证注释重排、别名/语言变化、篡改、旧快照/v1 回放、后续查询与旧校准拒绝，对应 OL-AC-04～06。

## Phase 4：验证与记录

- [x] OL-T012 执行本次来源/查询/快照/真实请求消费及恢复、校准反例的隔离 pytest；运行共享核心边界及受影响回归，记录实际命令和结果。
- [x] OL-T013 对受影响源码进行 Ruff 和差异检查，验证本次文档链接、需求/任务/验收映射；更新 quickstart 与统一特性规划状态。
- [ ] OL-T014 [独立质量待验] 在新的独立评测身份下执行 E0/E1/E2 真实模型对照，按 OL-AC-07 保存实际输入、召回、最终事实、完整路径、成本及剩余缺口；不读金标构词，不修改历史冻结评测。

依赖：OL-T001 → OL-T002 → OL-T003 → 实施；OL-T004/005 与 OL-T006 可按文件归属
并行；OL-T007/009 依赖两侧契约闭合；OL-T010/011 后进行组合验证。
OL-T014 不以工程测试替代，不包含在本轮生产操作或部署范围内。
