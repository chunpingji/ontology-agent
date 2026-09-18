# 实施计划：S0 本体原生词汇语义检索

**Feature**: `022-semantic-graph-closure`；**日期**：2026-09-13。
**需求**：[spec.md 的 OL-US1/OL-US2、OL-001～OL-008](spec.md)。
**契约**：[ontology-label-retrieval.md](contracts/ontology-label-retrieval.md)。

## 范围与技术背景

补齐当前本体 `rdfs:label`/`skos:altLabel` → 冻结本体 → 主体/谓词/目标语境 →
向量召回/精排实际输入的数据流。复用后端 Python 3.11、现有本体引擎、
Pydantic 契约、分析运行制品和 pytest 隔离环境；不新增依赖、迁移、路由或前端流程。
独立词表产品、人工扩词、H0/H1 固定词替换、真实模型质量及部署另列。

## Constitution Check

| 原则 | Phase 0 前检查 | Phase 1 设计后检查 |
| --- | --- | --- |
| 规范驱动与契约优先 | 用户授权已先写入 spec.md | 先确定冻结/查询/兼容契约，再列任务实施 |
| 本体保真与权威 | 仅读现有注释，不写 T-Box | 不扩公理，不从定义或金标造词 |
| 追踪与审计 | 注释原文、语言、来源及 hash 随运行冻结 | 采用/未采用、查询依赖和恢复身份可核验 |
| 测试纪律 | 信息流和失败边界采用隔离测试 | 工程、真实质量、部署分别记录 |
| 最小复杂度 | 复用本体快照与既有检索入口 | 无独立词表存储；现有校准仅扩展必要绑定 |
| 离线优先 | 无运行时外网依赖 | 原有本地模型及失败政策保持 |

两次检查均无需要豁免的设计违例。不得将可选模型历史容错泛化为当前必需模型失败成功。

## Phase 0：已明确的实施决策

- 新快照用可选 `OntologySnapshot.lexical_context` 保存全部注释及内容 hash；
  原生注释变化进入新 `ontology_hash`，旧缺省字段省略以保护已有身份。
- `subject-slot-query-v2` 接收冻结 ontology；v1 输入与存储形状不升级。
- 依谓词、主体类、合法目标 IRI 选词；限额为每概念 8 词、单词 80 字符、
  合计 640 字符。v2 的既有展示 label 只能采用入选词，无词则回退 IRI，避免
  被拒用词经旧字段重新入模。原文提及与本体类型语境分开，定义不扩词。
- 校准扩展 v2 与本体 hash、词汇 hash、选词版本精确绑定；旧 v1 的形状/hash
  保留。新查询配旧校准提前拒绝，可用增强无剪枝模式；不生成或自动批准新阈值。
- 有注释能力但确无注释与旧无载荷快照分别处理；读取异常不能当作无注释。
- 快照展示只规范已有 rdfs 多标签，不将 altLabel-only 提升为快照单标签；
  新 altLabel 由 v2 查询消费，H0/H1 固定词保持。对照固定展示 label 以隔离变量。

独立的版本化词表不是前置依赖，质量是否提升仍待真实对照。

## Phase 1：模块、数据流与失败边界

| 文件或模块 | 责任 |
| --- | --- |
| `backend/app/services/ontology_engine.py` | 只读多语言原生注释，保留字面量和语言 |
| `ontology_guided/ontology_lexical.py`、`contracts.py`、`ontology_plan.py` | 冻结词汇、确定性内容身份、旧载荷兼容及核验 |
| `ontology_guided/lexical_query.py`、`retrieval_query.py` | v2 角色化选择、预算/来源诊断及查询依赖 |
| `ontology_guided/semantic_reranker.py` | 向量和精排真实消费，准备/重放保持同一输入 |
| `ontology_guided/adaptive_retrieval.py` 及既有搜索适配器 | 校准匹配与检索依赖校验 |
| `backend/app/services/document_analysis/` | 创建/执行/恢复传递已冻结本体，不读取新版补词 |
| `backend/tests/test_extraction/` 及本体引擎相关测试 | 实际 RDF、真实调用接口捕获、恢复与失败反例 |

表内 `ontology_guided/` 为 `backend/app/services/extraction/ontology_guided/`。
调用链为显式创建运行 → 当前引擎读取 → 完整词汇快照 → 冻结计划 →
检索准备 → 双意图查询 → embedding/rerank → 提交/后续检索/恢复依赖核验。
解析、记录和纯原文向量只按其真实依赖复用，不能全量失效；检索处置不得越过
词汇依赖沿用旧结果。源读取、完整性、校准失败优先于付费模型调用暴露。

## Phase 2：任务与退出

按 [tasks](ontology-label-retrieval-tasks.md) 先闭合契约，再并行实现来源/冻结与
查询核心，最后接通运行及恢复调用。工程退出依 [quickstart](ontology-label-retrieval-quickstart.md)
验证 OL-AC-01～06。只在新增修改、失败或未解边界需要时扩大回归。
OL-AC-07 的 E0/E1/E2 真实对照独立待验；缺少该证据不能宣称 S0 质量退出，
也不能将本次工程实现理解为后续词表产品自动获准实施。

## Complexity Tracking

无宪章豁免。可选载荷及独立查询版本用于维护历史运行兼容；另建词表发布服务
会扩大本轮范围，因此不实施。校准只增加必要的身份绑定，不开展阈值生成或质量审核。
