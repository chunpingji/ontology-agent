# 查询闭环验收步骤

状态：基础查询已完成隔离接口、PostgreSQL 增量迁移及真实浏览器验收，并已部署。
2026-09-28 的发现接线及竞争指称分组已按后续授权部署，当前状态见第 8 节；第 4–7 节保留各次隔离试验当时的范围和结果。
实际命令、结果与历史迁移限制见[验收记录](../../docs/调研/实体库Mock映射查询实施验收-20260923.md)。
用例在现有隔离测试 fixture 和专用可销毁数据库中运行；不清理或修改共享运行库。
前置核对：被测服务的实际本体目录能返回 `ProductionArea.areaIdentifier` 及身份注解，
不能只凭工作区 TTL 文件存在认定服务已加载；缺失时报告依赖未就绪。

## 1. 基础查询

1. 准备现有类型结构的 Mock 车间记录 `642、644、646`，每条有独立 UUID 和来源 IRI。
2. 使用现有映射写接口声明 `ProductionArea` 类绑定，`col:code → areaIdentifier`；配置显示名与来源 IRI。
3. 读取 `/api/entities/sources`，确认来源可查询，字段与键语义可见。
4. 一次调用 `/api/entities/query`，独立查询 `611/642/646`、`611`、`642`、`646`。
5. 预期前两项完整未命中，后两项分别命中；无 `644` 替换，无新实体、审计查询平台或文档任务生成。
6. 修改 `642` 的显示名后查询，结果反映当前值，UUID 身份不变；删除来源行后新查询完整未命中。
7. 应用设备映射，以 fixture 中真实设备编号查询。指定父类型与 include_subclasses 时可返回合法子类；
   指定子类型时不能将父类记录提升为子类。

## 2. 映射与身份反例

| 用例 | 必须满足 |
|---|---|
| `0642` 与 `642` | 字符串精确查询不同，不默认转数字 |
| 完整串也有记录 | 完整串与成员分别命中，查询服务不决定指称数量 |
| 一条来源记录有多个标识 | 不按标识数复制来源记录；多值键不视为已完整核对 |
| 不同来源同号 | 分别保留来源引用；不覆盖、不自动合并 |
| 多条记录给出同一 IRI | 返回各自来源引用并显示冲突，不静默折叠成一条 |
| 复合键组件分属两条记录 | 不命中完整键；不得拼接 |
| 缺少组织/场地组件 | 可以按已知值召回，完整键状态不成立，身份未核对 |
| 两个独立键组 | 分别判断，不能把它们的属性并成一组 |
| 本体键含未支持的对象属性 | 保留组及缺口，不删组件后宣称满足 |
| 属性绑定单独修改 | mapping_revision 改变，下一查询用新映射 |
| 来源没有请求属性的绑定 | unsupported_filter；不能作为完整未命中 |
| 父类映射配置了逐行类型字段 | 子类查询仍能选中该来源，只返回真实类型符合条件的行 |
| 数组重复标签、多值、转换失败 | 不取第一项，不把失败原值记为成功；影响召回时完整性为 false |
| 合法非领域 fixture | 只改类/字段/配置，查询逻辑无需增加领域分支 |

## 3. 完整性、权限与界面

1. 无映射、不可访问映射、一个来源失败、扫描超限、结果超限分别验证状态，不能等同完整未命中。
2. 查询前后核对实体、Mock、映射、抽取作业与文档运行数量/内容，确保只读；不产生模型调用。
3. 读用户可查询授权来源，不能修改映射；写映射的过期版本被拒绝。
4. 实体页面的“已映射来源”按数据集展示卡片；点击后 Drawer 自动展示该来源当前类型的实体。
   使用超过 50 条实体的数据验证翻页无重复、总数正确；切换来源、关闭重开清除旧筛选及实体详情。
   检查名称/属性筛选、完整空结果、读取不完整、实体属性及记录引用；Escape 关闭后焦点返回卡片。
   卡片页刷新只读取目录，不自动查询实体或启动模型。
   已保存实体作为本地来源卡片同屏展示，检查其搜索、模块筛选、详情、关闭重开及与外部来源切换。
5. 数据库迁移验证新增列与实际 revision；SQLite 单测不能代替所需的 PostgreSQL 验证。

## 4. 最小发现试验与后续完整质量验收

2026-09-28 的 [H01/H02 评估](../../docs/调研/Harness实体查询接入与模型验收方案评估-20260928.md)
补充了一轮查询反馈、额外调用对照、完整文档验收及各维度通过口径。
本轮已实施最小发现接线并执行 A/B/C 真实模型对照，命令、独立产物及失败结果见
[实测报告](../../docs/调研/Harness实体查询最小接线与可行性实测-20260928.md)。
当前核心句三次接库试验仍未正确分出两个成员，不能把工程测试通过记作识别目标通过。
该发现试验未覆盖完整类型/属性/身份/关系及原文档验收；后续单句对齐试验见第 5 节。

评测入口为 `backend/app/evaluation/harness_lookup_probe.py`；在已配置本地模型与语义排序的后端环境中运行，
指定不存在的新输出目录，例如：

```bash
python -m app.evaluation.harness_lookup_probe \
  --output /app/data/evaluations/harness-lookup-new-run \
  --cases members composite missing alternative \
  --repeats 3 --call-limit 24 --seconds-limit 2400
```

`--repeats` 只重复核心 members 用例，其他反例各一次；`--ontology-dir` 默认 `/app/ontology/slpra`。
该入口复制当前本体并建立隔离来源表，不修改共享 Mock；会真实调用模型并产生调度用量。
本轮实际使用的目录与重复组合以实测报告为准，不覆盖已有结果。

基础查询验收与模型识别验收分别报告。
后续使用原段落，比较无库/有库；覆盖三记录齐全、缺少 611、同号异域、别名/改号、
整体与成员竞争、选择表达。模型自主提出查询与指称，不把预设的三个成员当作模型输入金标。
预先传入三个编号的 API 测试只能证明查询正确，不能证明模型已能拆解原文。

报告分别列出：文档实体数、编号绑定准确性、来源候选数、确认身份关联数、关系核验结果与成本。

## 5. 类型对齐后的 ID/指称二次复核试验

隔离入口为 `backend/app/evaluation/harness_key_realign_probe.py`，
方法、逐次结果及原始产物见[二次复核实测](../../docs/调研/类型对齐后二次ID复核可行性实测-20260928.md)。
在已配置本地模型与语义排序的后端环境执行，例如：

```bash
python -m app.evaluation.harness_key_realign_probe \
  --output /app/data/evaluations/harness-key-realign-new-run \
  --cases members composite missing alternative \
  --repeats 2 --conditions B A --call-limit 180 --seconds-limit 3600
```

输出目录必须不存在。A/B 共享每次真实发现及类型对齐的前缀；A 直接继续，
B 在实体核验前读取实际映射键值并增加一轮复核。可增加 C 条件，使用同一复核协议但不提供新键值。
共同前缀已有发现阶段查询，A 不能称为完全无库。复核后重新对齐类型，再继续实体、属性、证据及共指核验。
来源表及本体 World 隔离，模型调用会产生既有调度用量；实验逻辑不接入在线服务。
分别核对两个成员、逐主体已接受编号及原文归属，不能仅以节点数、流程完成或模型置信度判成功。

## 6. 统一成员编号绑定试验

隔离入口为 `backend/app/evaluation/harness_bound_key_probe.py`。复用第 5 节保存的真实类型前缀，
重新调用模型生成成员 anchor、编号引文和来源候选的统一绑定，再执行成员类型、属性及证据核验。
实际命令、同状态下游对照和原始产物见[统一绑定实测](../../docs/调研/统一成员编号绑定实测-20260928.md)。
35 次新调用已完成，核心 0/3；复合编号、明确备选表达有成功分支，未验证生产关系，未部署。
复测必须使用新的输出目录；不把一致性拒绝、未执行的下游阶段或空关系结果计为识别成功。

## 7. 竞争指称与关系分阶段原型

隔离入口为 `backend/app/evaluation/harness_competing_candidate_probe.py`，契约见
[分阶段实验契约](contracts/competing-candidate-probe.md)，结果见
[分阶段实测](../../docs/调研/竞争指称与生产关系分阶段实测-20260928.md)。
在已配置本地模型的后端环境中运行，例如：

```bash
python -m app.evaluation.harness_competing_candidate_probe \
  --output /app/data/evaluations/harness-competing-new-run \
  --repeats 3 --call-limit 45 --seconds-limit 2400
```

核心重复三次，九个反例各一次；输出目录必须不存在。原型直接给出两类候选本体定义，
不执行正式类型发现/精排，不导入线上控制器。编号表达、分组、计划提及、关系和证据均由真实模型回答；
程序只执行真实隔离查询、验证原文/引用与依赖，不补造参考值。
分开统计候选覆盖、最终编号、计划主体、关系参与方式和时间；失败/未决不从总分母移除。
本轮两轮矩阵加端点补测共68次真实调用；核心编号3/3、核心完整目标2/3，第二轮完整矩阵5/12。
当前代码包含显式成员协议、最终输出评分修正及无计划端点约束；后者另以两个保存前缀补测，
没有用补测替换第二轮原始矩阵分数。未部署，完整识别验收仍未通过。

## 8. 正式 Harness 部署验收

正式流程默认启用发现查询反馈及 `type_alignment → referent_alignment → entity_review`，
之后分别核验编号属性、关系参与方式和时间。契约见
[正式分组接线](contracts/harness-grouped-alignment.md)，实际部署与逐轮结果见
[上线验收](../../docs/调研/竞争指称分组上线验收-20260928.md)。

1. 核对服务健康、本体已加载、数据库 current/head 和实际进程启动时间。
2. 在当前代码创建新的文档运行；原句使用 CMC 报告根类型，明确并行句另用临床备样生产计划根类型检验给定计划端点的关系。
3. 从真实请求核对 `key_candidates`，从真实回答核对整体/成员表达、竞争分组和所选解释。模型输出成员 `expression_ids`，程序从引用派生成员 anchor。
4. 分别核对两个生产区域实体、逐主体的 `areaIdentifier` 精确值与来源候选；不能将带类型称谓的名称误计为正确编号。
5. 核对 `relation_groups` 的参与方式、择一与时间；组缺失计为关系目标未达成，不能以运行完成代替语义通过。
6. 使用实际 API 的浏览器检查页面、属性和关系详情；合成组展示回归另列，不替代真实模型生成关系。
7. 新目录保留输入、本体、模型原始回答、请求、终态图谱和运行代码；失败及主动取消均计入记录。

最终部署验收产物位于 `backend/data/evaluations/harness-deployment-20260928-05`。
原句一次正确识别两个车间及各自编号；完整 H01/H02 仍按独立质量验收保留未完成。

## 9. Responses 网关与 Qwen 对照

入口为 `backend/app/evaluation/harness_model_comparison.py`。在具备现有后端配置及
JSON Schema 校验依赖的独立进程中执行，输出目录必须不存在：

```bash
python -m app.evaluation.harness_model_comparison compare \
  --source /app/data/evaluations/harness-current-v3 \
  --output /app/data/evaluations/harness-model-comparison-new \
  --base-url http://172.22.0.1:31080/v1 --model gpt-6-sol \
  --repeats 3 --call-limit 90 --seconds-limit 2400
```

地址须替换为执行环境能够访问的用户授权网关；密钥由隐藏终端提示输入，不放在命令参数或文件中。
`--source` 须替换为当前 `document-harness-v3` 正式运行导出的基线目录，包含输入、
本体、状态、请求、结果及图谱；历史 v1 部署目录会在调用前被拒绝，不能直接复用。
`--repeats` 控制每条原句每模型的编号候选重复次数；设为 0 只补跑两模型的完整 Engine。
所有模型共享传输 Schema 适配，仍执行原生产契约与原文核验；不更改线上默认模型。
来源查询使用正式服务及只读事务快照，文档识别状态写入独立产物目录。
实际接口差异、模型结果、失败、冻结精排范围及成本见
[模型对照实测](../../docs/调研/GPT6-Sol与Qwen同协议对照实测-20260928.md)。

## 10. 编号候选片段 ID 回归

当前实施、五轮真实 Qwen 结果及限制见[片段 ID 实测](../../docs/调研/原文片段ID协议实施与实测-20260928.md)。

在 `backend/` 运行定向回归：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_document_harness/test_evidence_spans.py \
  tests/test_document_harness/test_grouped_alignment.py \
  tests/test_extraction/test_document_harness_runtime.py
```

核对候选与分组证据只返回当前目录的片段 ID，编号值从原文还原。目录缺项可一次
提交 `new_spans`，逐字定位后由程序分配 ID，再按扩充目录提交完整分组；补充无效或
分组非法时不能查询候选编号或登记新成员。暂停继续复用目录及已付费回答，输入预算
不足不得截断原文或放宽校验。同字串的不同位置必须有不同 ID，缺少 Mock 不得漏掉原文编号。

第 9 节的历史 v1 对照产物保留原运行代码；当前评测入口必须使用 v2 新运行生成的输入，
不能将历史 quote 输出转换成片段 ID 后冒充新模型回答。

## 11. 文内编号与全局身份边界回归

当前工程回归：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_document_harness \
  tests/test_extraction/test_document_harness_runtime.py \
  tests/test_extraction/test_ontology_guided_boundaries.py
```

GPT 隔离实测入口为 `app.evaluation.harness_identifier_semantics_probe`，密钥仅从隐藏终端读取。
在已准备后端配置与 `jsonschema` 依赖的进程中执行，输出目录须不存在：

```bash
python -m app.evaluation.harness_identifier_semantics_probe \
  --source /app/data/evaluations/harness-model-comparison-20260928-01/paired-03 \
  --output /app/data/evaluations/harness-identifier-semantics-new \
  --base-url http://172.22.0.1:31080/v1 --model gpt-6-sol \
  --call-limit 50 --seconds-limit 1800
```

入口仅复用历史失败的属性核验请求、本体、原文及精排；先校验属性核验 Schema 一致，
每句旧/新提示各三次对照，再验证无来源候选、错配成员、名称数字反例及属性自带认证限定。
最后以当前 v2 协议从空 Engine 执行两条单句，全部识别回答新调用 GPT，不回放旧候选。
来源查询使用正式服务与只读事务，不修改默认模型或重启服务。

编号采信仍须满足原文语义、正确归属及既有置信门槛；来源身份保持 `not_checked`。
反例不能因绑定或外部命中被自动采信。分别报告阶段通过率、最终编号、正文计划、关系、
完整执行及成本，不将给定计划根算为模型新发现。实测结果见
[语义边界修正报告](../../docs/调研/文内编号与全局身份语义边界修正及GPT复测-20260928.md)。

## 12. 分组明确判定与辅助自评分

当前消息协议为 v3，`referent_selection` 必须给出 `verdict=supported|unresolved`。
supported 要求有效分组及非空原文片段，unresolved 的分组 ID 必须为 null；
成员登记由语义判定及已有原文/分组/覆盖约束决定，不再比较自报 confidence 与 0.85。
分数保留在当前选择回答中。覆盖失败原因通过 `selection_issue` 和观察理由体现。
该变更限定指称分组阶段，不表示其后实体、属性或关系已被采信。

工程回归重点为 `tests/test_document_harness/test_referent_decision.py`，覆盖低分支持、
高分未决、高分缺证、矛盾消息、未知分组及保存后继续；同时运行完整 Harness 及运行适配回归。
真实模型阶段试验复用上一轮原句与并行句的选择输入，补充显式复合编号和明确歧义两个反例：

```bash
# backend/；输出目录须不存在，密钥仅从隐藏终端读入
.venv/bin/python -m app.evaluation.harness_referent_decision_probe \
  --source data/evaluations/harness-identifier-semantics-20260928-02 \
  --output data/evaluations/harness-referent-decision-new \
  --base-url http://127.0.0.1:31080/v1 --model gpt-6-sol
```

原句和并行句各三次，两个反例各一次，上限 8 次/600 秒。只提供冻结的候选，重新生成
当前协议选择回答；程序重建片段位置并使用正式分组校验与选择函数评分。它不是从头发现
或完整图谱流程试验，不更新线上来源，也不把旧分数变成新判定。结果与限制见
[实施与验证记录](../../docs/调研/分组语义判定替代自评分门槛实施与验证-20260928.md)。

## 13. 局部分组联合证据

保持 v3 字段、原文定位和登记约束，更新选择提示的证据口径：共享称谓、完整编号边界、
对应类型和编号属性的精确键匹配可共同支持局部多成员分组。候选菜单中理论上可能的
复合编号解释不自动构成未决；原文明确的复合编号、改号/别名、层级冲突及编号子串仍须拦截。
整体未命中不证明对象不存在，来源身份和生产参与方式仍独立核验。

真实评测入口为 `app.evaluation.harness_joint_evidence_probe`，复用此前固定输入及本体。
在具备 JSON Schema 评测依赖及数据库访问的后端环境执行（历史产物路径须可访问）：

```bash
python -m app.evaluation.harness_joint_evidence_probe \
  --source /app/data/evaluations/harness-identifier-semantics-20260928-02 \
  --baseline /app/data/evaluations/harness-referent-decision-20260928-01 \
  --output /app/data/evaluations/harness-joint-evidence-new \
  --base-url http://172.22.0.1:31080/v1 --model gpt-6-sol
```

密钥仅从隐藏终端读入。原句旧/新提示各三次交替调用，输入与 Schema 相同；明确并行
三次，七个边界各一次，再从空状态各执行原句和并行例。全轮最多 60 次请求/1800 秒，
每个完整运行最多 22 次请求。阶段采用冻结来源反馈；完整流程读取真实 Mock 的只读事务
快照，复用本体与精排，所有识别回答重新生成。金标不进入请求；单独报告分组、编号归属、
正文计划、关系、来源身份及失败，阶段通过不代表整份文档验收通过。

本轮结果及原始产物见[联合证据优化及 GPT 复测](../../docs/调研/局部分组联合证据采信优化及GPT复测-20260928.md)。
