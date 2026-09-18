# 新版查询剪枝校准与文档重新识别故障记录

日期：2026-09-14（UTC）。用户反馈上传文档重新识别显示“文档读取失败”，
并明确选择保留低分剪枝、另行更新校准。

## 故障原因与代码修复

目标文档原件存在且能解析，源作业关联正确；实际数据库为
`0040_current_recognition_state`，运行模型字段齐全。

本机配置为 `trial`，仍加载 `pruning-trial-20260911-v1/profile.json`。
该制品使用 `subject-slot-query-v1`，没有本体和词汇绑定；当前新建运行冻结
词汇本体并使用 v2 查询，因此在创建前被拒绝，符合
[词汇检索契约](contracts/ontology-label-retrieval.md#5-剪枝校准与失败)。

另有错误契约缺陷：服务抛出的 `ADAPTIVE_CONFIGURATION_INVALID` 未包含在
API schema 的错误码枚举中，导致错误响应再次校验失败，返回
`422 CONTRACT_SCHEMA_INVALID`；页面又将这个响应误报为文档读取失败。
现已补齐错误码和契约，并区分服务响应异常与原文读取异常。

定向验证：报告文档 API 测试 14 passed，包括旧校准配词汇本体的精确反例、
配置失败不创建运行或调度、无暂存原件残留。浏览器隔离场景验证失败显示、
原文保留、重新识别的幂等重试，以及用户/文档切换；TypeScript、ESLint、Ruff
和差异检查通过。浏览器模拟 API，不替代真实模型验证。

## 首轮真实模型测量：未通过

新目录：`backend/models/semantic-ranking/pruning-trial-20260914-query-v2/`。
该目录独立保存合成 DOCX、当前本体、实际 v2 查询、完整增强视图、评分 epoch、
处置与调度记录。临时本体数据库和模型调度 SQLite 均与运行数据隔离。

使用当前本机模型、6 条合成记录、`CMCReport → describes` 双意图查询，
预声明阈值保持双意图原始分数都低于 −8，组保护阈值为 0。

| 合成记录 | discover | counterevidence | 实际软剪枝 |
| --- | ---: | ---: | --- |
| 天气 | -3.01171875 | -2.78125 | 否 |
| 天文 | -2.673828125 | -2.521484375 | 否 |
| 足球 | -2.78515625 | -2.6796875 | 否 |
| 相关产品 | -2.525390625 | -2.40625 | 否 |
| 否定 | -2.716796875 | -2.630859375 | 否 |
| 条件 | -2.537109375 | -2.326171875 | 否 |

12 个意图分数完整返回，17 次独立调度请求完成，耗时 101.42 秒。
由于没有无关样本实际被剪枝，未通过预声明检查，**没有生成可启用的
`profile.json`**，也没有替换线上校准。

旧制品的实际模型身份与本次完全相同。旧冒烟使用 19/25 字手写查询，
本次真实查询为 2386/2381 字符，含 12 个允许对象类型；增强视图全部完整。
原 −8 阈值只验证过手写短句尺度，不能据此宣称已适用于真实系统查询。
本轮不能单独区分查询长度、对象范围与视图包装分别造成的影响。

本次所有合成测量均属于研发验证，不是专家校准或真实文档 precision/recall
验收；旧校准、旧运行和用户原件保留。

## 固定三场景补测：仍未通过

新目录：`backend/models/semantic-ranking/pruning-trial-20260914-query-v2-expanded/`。
核对模型、本体、实际查询和完整增强视图一致后，复用并完整保留首轮分数及
失败记录；新增合成路线、原料药 PDE 两组固定样本，各 3 条无关、1 条相关、
1 条否定、1 条条件。PDE 主体提及具有合成原文中可回放的准确锚点。

| 场景 | 实际 v2 查询字符数（双意图） | 无关样本原始分数范围 | 相关/否定/条件分数范围 | 实际剪枝 |
| --- | --- | --- | --- | --- |
| 描述产品（复用首轮） | 2386 / 2381 | -3.01172～-2.52148 | -2.71680～-2.32617 | 0 / 6 |
| 合成路线 | 900 / 895 | -6.28125～-5.32031 | -1.74219～-0.88721 | 0 / 6 |
| 原料药 PDE | 1037 / 1032 | -5.00000～-4.20312 | -3.34766～-2.47266 | 0 / 6 |

总计 18 条记录、36 个完整意图分数；补测新增 24 个分数、34 次独立调度请求，
耗时 100.94 秒。所有相关、否定、条件样本保留，但仍无无关记录触发 −8 剪枝，
故未通过固定三场景检查，**仍未生成 `profile.json`，没有替换线上校准**。

补测时 `trial` 校验限制 `rerank_thresholds <= -8`，因此当时尚不能启用更高阈值。
后续用户明确指定 −3，处理如下。这些研发分数不能证明其他谓词已获得质量校准。

## 用户指定 −3 与人工调整

用户明确要求：“取-3作为禁止阈值，再根据实际情况，由人工调整”。新版
`subject-slot-query-v2` 的 trial 因此改为接受校准文件中的有限数值；保留 v1 的
−8 限制，不把 −3 设成新的硬上限。双意图分数必须分别严格低于阈值，且完整
评分、原文/上下文/组保护条件均允许，才会软剪枝；等于阈值保留。

新制品目录：`backend/models/semantic-ranking/pruning-trial-20260914-query-v2-manual-3/`。
`profile.json` 的 `rerank_thresholds.discover` 和 `rerank_thresholds.counterevidence`
均为 `-3.0`；profile hash 为
`cabaf929be842c8454c296ab4856694748c52f3e63e6e3144b61af398813c2cc`。
绑定实际模型、词汇本体与增强视图，谓词范围为原配置与当前合法谓词的交集，共 205 项。
质量状态保持 `development`，专家复核保持 `pending`，不启用 enforce。

本次没有新增模型评分。核对原有模型/输入/视图绑定后，复用三场景的 36 个完整分数，
使用实际 `epoch_decision` 按 −3 重算处置：

| 场景 | 软剪枝 | 相关/否定/条件样本保留 |
| --- | ---: | ---: |
| 描述产品 | 0 / 6 | 3 / 3 |
| 合成路线 | 3 / 6 | 3 / 3 |
| 原料药 PDE | 3 / 6 | 3 / 3 |

总计剪枝 6 条无关记录，9 条相关/否定/条件记录全部保留；部分低分相关记录由既有
证据保护规则保留。阈值是用户在看到测量后指定的试运行值，不是独立质量评测结论。
前两轮未通过的测量目录与旧校准均未重写。

后端定向回归执行 `test_ontology_lexical_retrieval.py`、`test_adaptive_retrieval.py`、
`test_report_document_runs.py`：**63 passed**，4 条既有警告。新增回归验证 −3 可加载、
后续人工调整为 −4 或 −2.5 可被新配置读取、原冻结配置保持 −3，以及 development
不能进入在线 enforce。相关 Ruff 与 `git diff --check` 通过。

后续人工调参沿用校准文件入口：以当前 profile 为基础修改两个 `rerank_thresholds`
值，删除旧 `profile_hash`，经 `CalibrationProfile.model_validate()` 重建 hash 并用
`AdaptivePolicy(mode="trial", calibration=profile)` 校验后写入配置文件。若使用新路径，
同步修改 `.env` 的 `DOCUMENT_ANALYSIS_ADAPTIVE_CALIBRATION_PATH` 并重建后端容器；
同一路径的有效文件在新建运行时读取。新运行冻结调整后的值，旧运行继续使用原值，
不自动调参；不得通过改 hash 冒充本体/模型变更后的重新校准。

## 当前生效状态

已将 `.env` 的 `DOCUMENT_ANALYSIS_ADAPTIVE_CALIBRATION_PATH` 指向容器内
`/app/models/semantic-ranking/pruning-trial-20260914-query-v2-manual-3/profile.json`，
保留模式 `trial`。`docker compose config --quiet` 通过后，执行
`docker compose up -d --no-deps --no-build --pull never --timeout 60 backend`
加载代码与新环境变量；后端 8000 端口和网页代理 8081 端口的 `/api/health`
均返回 200，运行中 OpenAPI 包含 `ADAPTIVE_CONFIGURATION_INVALID`。

加载前后分别用目标原件、实际本体和新校准，通过隔离 SQLite、临时运行存储与
测试身份调用真实路由 `POST /api/document-analysis/documents/runs`，均返回 **202**。
第二次直接读取部署环境配置，确认 v2/trial/development、双意图 −3 和上述 profile hash。
测试进程禁止后台调度，正式数据库仅只读，原件内容 hash 未变。

实际数据库 revision 与代码 head 均为 `0040_current_recognition_state`。
加载前无活动文档分析或模型请求；前后 9 个已有运行的状态、修订号、事件头、工作版本
及制品版本摘要一致：
`4d1cdc15c26b4a637b0bb5dda5640ff10d1fd192a343474b234d7b9109030934`。

**识别启动配置已恢复并加载**；用户刷新页面后可点击“重新识别”。本次未在正式库创建
新运行，未执行这份真实文档的完整模型识别，也不宣称真实图谱质量已通过评测。
