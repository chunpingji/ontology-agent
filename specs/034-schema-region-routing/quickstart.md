# MVP 验证

## 第二阶段原文采信验收（当前默认）

最新工程/部署与真实探针结果见[第二阶段记录](../../docs/调研/ontology-target-main-deployment-20260921/evidence-review.md)。

新建运行冻结 `record_discovery.graph_phase=evidence_review`。实体登记后独立发现并核验关系、
属性，使用冻结 Schema 卡和授权原文；上层关系未采信不阻断下层探索。

1. 使用新运行核对原件 hash、冻结阶段及模型预算，不改变旧运行的策略。
2. `/analysis?tab=graph-analysis` 查看采信实线、待定虚线；打开“显示未采信”查看原因。
   隐藏只影响展示，不删除候选或缩小目标分母。否定、条件、计划和备选组保留限定信息。
3. 属性原文值/原文单位与规范化值/规范单位分别展示；规范化不可用时保留原值且不提供规范值。
   SHACL/metric 诊断独立展示，不能代替原文采信；`N/A` 仍表示缺失。
4. 发现完成而核验暂未完成时保留候选与当前恢复入口；继续只执行未完成核验。
   逐项核对原文跳转、理由和候选修订，未知模型请求仍按既有未决契约处理。
5. 运行受影响 pytest、前端 Node、类型及静态检查，重建主后端后用指定报告新建运行。
6. 全文范围、真实多谓词批次和固定参考质量评测未完成前，只报告实际执行结果，
   不把候选增长或少量采信称为最终质量/完整度/速度提升。

## 第一阶段候选图验收（仍可按冻结策略运行）

第一阶段运行的冻结策略为 `record_discovery.graph_phase=candidate_graph`。保留实体类型、
物理指称核验，同时从授权原文发现关系候选；关闭关系核验、证明工具及属性发现/校验。
已登记实体无需上层关系证明即可展开合法下层关系。前文历史运行不能切换阶段后继续作对照。

1. 以指定报告新建运行，核对冻结阶段及原件 hash，不恢复旧证明运行。
2. 在 `/analysis?tab=graph-analysis` 查看该运行；本体目标和原文关系候选均为虚线，
   候选不计入实证完整度。通过实体选择框查看未连接到文档根的实体及其关系目标。
3. 点击关系与端点原文，核对源位置及逐字引文；否定、计划、条件、备选对象组仍保持原样。
4. 检查调用账本：实体 discovery/verification 可存在，关系仅 discovery，属性调用为零。
   多谓词批次按实际成员数核对，不能把单成员批请求算成真实多谓词合批。
5. 暂停/继续或冷恢复不重发已确认发现；页面读取、刷新和切换实体不启动模型任务。
6. 报告本次实际候选数量、执行范围和未完成项；本阶段不据候选数量报告事实质量或速度改善。

以下为历次运行记录；其中已部署阶段与验证状态须以最新第二阶段记录为准。

2026-09-21 当前主服务部署与验收见
[主服务实证记录](../../docs/调研/ontology-target-main-deployment-20260921/README.md)：
图谱分析 Tab 已默认开启，主入口目标图 API 200。当前新运行发现上限 8192、核验上限 16384，
三个请求超时均为 900 秒，无进展超时 1200 秒，外部模拟资料关闭。
恢复及 Quote schema 工程修复已部署，Parser 9 新实证运行 `20bf2d25-98f3-4efe-99f0-1e5dbbae5d05`
已创建；真实实体、属性、关系、多谓词批次及全文质量验收尚未完成。

文档驱动候选发现的验收步骤见末节。先前隔离运行的失败结果保留于
[首次 CMCReport 实证报告](../../docs/调研/ontology-target-graph-20260921/README.md)，
下文历史验证不能代替当前新运行验收。

2026-09-21 历史 16K 参数调整：用户指定新运行的核验输出上限为 16384 token，发现保持 8192，
两者仍受总输出上限约束。当时验证配置冻结、API 创建持久化及实际请求参数，600 秒请求超时未调整。
下文历史实测的 8192 核验上限保持原记录，不代表调整后的新运行。

该次参数调整验证：配置、API 创建持久化和模型请求传递共 30 项定向测试通过，Ruff 通过；
隔离实证 API 8011 启动时确认 `discovery=8192`、`verification=16384`，目标图 GET 成功。
当时未重启主服务或启动新的真实模型任务，没有实测 16K 的截断率及耗时；
后续主服务重建及新运行见页首链接，不改写这一历史记录。

在 `backend/` 复用 `.venv`，不启动服务、不修改上传原件、不调用生成模型。

```sh
.venv/bin/ruff check --no-cache \
  app/services/extraction/ontology_guided/schema_region_routing.py \
  app/services/extraction/ontology_guided/reading_groups.py \
  app/services/extraction/ontology_guided/record_search.py \
  app/services/extraction/ontology_guided/record_discovery.py \
  app/services/extraction/ontology_guided/executor.py \
  tests/test_extraction/test_schema_region_routing.py

.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_schema_region_routing.py
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_reading_groups.py
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_table_reading.py
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_semantic_record_discovery.py
```

影子计划读取冻结 IR、MetadataSnapshot、OntologySnapshot 和策略，输出统计 JSON。它只调用现有
embedding 排名或复用已保存向量，不创建识别任务；若本地语义模型不可用，报告未完成，不回退为
生成调用或把未路由区域算作无事实。

## 2026-09-20 真实运行结果

目标运行：`8d1cdc6f-e349-435e-a3fa-9b4ffdc39d12`，文件为
`原料药 HRS-5678 临床备样生产信息.docx`。运行在 `ingest/paused` 且尚无识别工作时迁移到
`schema-region-routing-v1`，随后通过公开 resume/pause 控制接口执行。

- 编译 12 张根关系 Routing Card，读取 27 个元数据节点，命中 14 个唯一区域。
- 52 个普通阅读组中 32 个 routed、20 个 unrouted；普通候选组合为 61，旧运行基线为 214，
  减少 153 个，降幅约 71.5%。另有 64 个独立属性字段任务，不与普通候选组合混算。
- 首个 discovery 合并读取 7 条物理记录，输入 12,895 token、输出 6,954 token；两次独立核验
  均完成。模型错误地把“是否肿瘤药物：是”归为 `HormonalDrug`，独立核验依据
  “是否激素类药物：否”拒绝类型，并因主体指称不足将记录保留为 `ambiguous_source_quote`。
- 多次 pause/resume 后，已完成的 discovery/verification 请求未重复。空发现结果恢复为
  `record_no_claims`；伪造的 `context_text` 被 `source_excerpt_mismatch` 拒绝。
- 一组 6 个降解路径实体及属性触发了 11,887 token 输入、20,480 token 输出的核验响应；提供方
  返回的 JSON 在 45,256 字符处截断，任务按 `context_budget_exceeded` 保留为未完成，没有落图。
  当时的首轮补丁将普通连续段落组收紧到 4 条/800 字，并将 facet `reason` 限制为 160 字；
  当前运行的冻结范围仍保持 8 条/1200 字，未重写已付费工作。后续 `09a...` 证明更小分组增加
  了阅读组和任务数，因此性能 v2 已恢复新运行的 8 条/1200 字，只保留 reason 限制和核验拆分。
- 修复了真实运行发现的覆盖守恒缺陷：`unrouted` 同时计入计划总数和未尝试数。修复后进度成功
  发布，`records_planned = records_examined + records_incomplete + records_unattempted`。
- 公开 run、metadata、graph、harness API 均返回 200。任务最终在 recognition 阶段暂停，图为
  partial，仅含文档根节点；没有把未通过独立核验的候选发布为实体、属性或关系。

本次定向回归共 133 项通过；另行覆盖 record executor、预算、恢复和路由相关测试均通过。
真实运行验证的是执行闭环和安全门，尚不代表整份文档识别完成或质量评测达标。

## 2026-09-20 性能 v2 基线与复验

失败基线为 `09a85be4-d109-4339-beb9-b107550fc7a7`。快照时计划 263、已检查 6、未完成 22、
未尝试 235；28 个已尝试任务产生 35 次已完成模型调用和 1 次在途调用。60 个阅读组中 47 个
被路由，每组都选择两张卡，形成 94 个普通任务；另有 64 个独立属性字段任务。发现平均约
91 秒、核验平均约 169 秒，排队约 0.03 秒。该运行证明主要耗时在模型调用数量和生成体积，
不是 embedding 或排队。

新运行的冻结策略必须同时满足：

- `execution_mode=region_batch`、`property_field_mode=region_batch`；
- 普通组 1200 字/8 条；
- `reasoning.effort=none`、`chat_template_kwargs.enable_thinking=false`；发现/核验均 8192 output tokens；
- `max_lineage_calls=6`、`max_feedback_reopens=1`。

工程复验增加：同一区域两张卡只执行一次 discovery，字段进入 `property_fields` 且不产生
`attribute_disambiguation`；核验响应截断后保存原始回执并拆成单成员/target 请求，discovery
不重复；实际 transport 收到对应阶段上限。真实提速只能由使用上述冻结策略的新运行确认。
当前仍为单活动单元，双并发未实施。

当前开发实例已在没有 queued/running 文档任务时重启。容器内冻结函数返回上述全部新值，后端
健康检查为 `status=ok`，前端分析页可访问。`09a...` 仍为原策略下的 paused 运行；部署检查没有
迁移该任务，也没有产生新的模型调用。

## 2026-09-20 同类字段投影合并回归

运行 `74b8de8e-22c8-4b28-870e-f937056fc193` 在首次生成调用前失败。保存的结构、元数据和排序制品
证明 Word 已解析成功；真实异常为完整实体卡与字段投影卡共享 class IRI、属性集合不同，被旧合并器
错误判定为 `record_schema_class_definition_conflict`。执行层的通用 `ValueError` 映射又将其显示为
`INVALID_WORD`。

当前实现按卡内成员键合并互补投影，仅拒绝同一成员键的定义冲突，并停止将通用 `ValueError` 映射为
Word 错误。用该运行的冻结制品进行无模型探针时，83 张执行卡和 68 张字段卡合并成功；旧相等判断
会遇到 642 次同类投影差异。路由及执行恢复相关 43 项测试通过，后端重启后健康检查通过。原失败
记录保持原样，不能把历史错误状态当作修复后的新运行结果。

## 2026-09-20 GLiNER 与直接 LLM 实测

该运行继续后最终在 recognition 阶段因 32 次模型调用预算用尽而暂停。54 个阅读组均已路由，
135 个计划记录中仅4个完成、9个未完成、122个未尝试，说明调用数和单任务轮次仍是瓶颈。

同一批7个清洗原文单元出现两次 GLiNER `no_match`。区域记录任务中，GLiNER 冷调用约132.9秒；
该任务相邻完成事件间隔约379.8秒，4次 LLM 共输入34,993、输出9,073 token。LLM 随后直接提出
3个记录实体，包括“本产品的设备清洗方法”，但旧执行卡将其错误收窄为 `ManualCleaning`。
`hasCleaningMethod` 关系任务中，GLiNER 热调用约16.3秒；任务完成间隔约98.2秒，4次 LLM 共输入
29,340、输出11,489 token。LLM 曾提出关系，但因对象集合错配被拒绝，最后无合法声明。

新实现保留可达下游类型用于区域路由，根层 Execution Card 只含正式关系直接 range；record 管线
不再提供 `propose_mentions`。这不是增加替代 LLM 调用，而是让已有 discovery LLM 在第一次回答
直接提交实体和逐字引文，移除“模型请求工具—GLiNER—空结果回填—模型再回答”的串行环节。
两次边界工具已实测消耗约149.2秒且有效召回为零。部署后须用新运行复测完整墙钟和首实体时间；
旧任务的冻结卡和已付费结果不原地升级。

新运行 `8234c673-ff16-443c-ab9f-0f4df7f22f91` 已移除 GLiNER，但前两次直接 LLM
调用均在 4096 token 处被截断，且全部输出为 reasoning。隔离探针确认标准
`reasoning.effort=none` 未关闭本地 Qwen thinking。传入
`chat_template_kwargs.enable_thinking=false` 后，4K 请求在84.5秒被答案 JSON 截断；
提高到8K后在99.7秒返回5354 output token、15,487字符的完整JSON，包含6个实体
及属性。因此新运行固定 thinking false、发现/核验均 8192，且记录实体首轮不提供工具。

部署后新运行 `56dc5fdc-bd2f-49d4-ae3b-4768f209bd99` 确认稀疏单类型 discovery 为
21.2秒、2688/576 input/output token，但12个竞争类型与属性合并时发生143.4秒的错误回答和
153.1秒的8K截断纠错。同请求改为实体-only后，77.2秒返回1个 `DrugProduct`，输出
1115 token。新实现因此在类型数超过4时强制首轮 `properties=[]`，实体核验后再按局部菜单
抽取属性；Schema失败回答不再重放进纠错请求。

第三个同文档运行 `0881a506-24dc-47d2-9900-49823fef89ba` 已完成实体-only真实链路验证。
12类型请求为62.7秒、13,400/335 input/output token，`properties.maxItems=0`、无工具、无
thinking、JSON完整；其1个 `DrugProduct` 候选被事实门拒绝。同一批曾被 GLiNER 两次
`no_match` 的7段清洗原文，直接 LLM 首答33.0秒但因一个空提及候选需纠正，53.8秒纠正后得到
12个实体。两批独立核验分别用63.8秒和43.5秒，最终11个实体通过、1个总述实体被拒绝；菜单由
1份增至12份，计划由12项增至45项，证明后续局部遍历已建立。发现链86.8秒，比 GLiNER 冷调用
132.9秒减少34.7%；含核验的模型墙钟共194.1秒。运行在核验边界人工暂停/继续，现保持暂停，
这些数字不是连续整文档墙钟。清洗实体仍有动作粒度偏细风险，模型核验通过不代表人工 precision
验收；首答空提及造成的纠正和12实体核验是当前下一项延迟来源。

最终实现决定为：record 管线只使用直接 LLM NER。新运行忽略配置中的 `gliner2`，适配器拒绝
注入 `mention_extractor`，模型工具清单和服务端分派均禁止 `propose_mentions`；文档 Harness 不再
显示 GLiNER 配置或工具名称。非 record 的旧抽取域仍由其自身契约管理，不进入本次 record 改造。

## 文档驱动候选发现验收（2026-09-21）

以下为验收步骤，不是执行结果。只有相应代码、公开展示契约及定向工程检查完成后，
才用用户指定的 `upload-a255fd30-192c-4f81-92df-5f76a56b8383` 新建 CMCReport 运行；固定原件
哈希、本体、模型和范围，独立参考不进入模型输入，不复写旧运行或历史评测制品。

1. 从 `/analysis` 外层“图谱分析”选择该报告。开始前能看到本体类型及目标范围，创建运行后
   在尚无模型结果时能看到关系/属性目标占位；进入、切换和刷新页面不启动模型。
2. 显式开始分析后，核对同区域实体和属性批量发现；实体核验登记后，合法关系批次使用这些
   精确端点。检查实际请求与调用账本，确认没有按每条虚线重复执行相同实体发现。
3. 抽查已实证关系和属性能定位原文；虚线逐项解释待检查、未决、缺证据或技术未完成。条件、
   计划及否定保留明确标记，共现或仅有摘要的候选不能变成肯定实线。
4. 核对目标分母包含未找到候选的项；设备等多值项部分命中时保持部分完成。实例展开后分母
   增加可见，待展开/未检查范围不能被隐去。不适用项必须展示依据，处理结束不强制完整度 100%。
5. 工程反例覆盖同名异对象、错误主体/方向、一批部分通过、孤立实体不能提前递归，以及暂停
   继续不重复发现、不同运行不能借用证据；这些隔离测试不能替代真实文档的人工核对。
6. 新验证记录报告实体/属性发现、关系发现、独立核验、纠正/补查的调用数、token 与墙钟，
   实线和属性实证数、完整项/目标数、漏识别、未决及未评分项。无法确定成员总数时如实标注，
   不用图谱非空或模型置信度证明质量。部署状态和功能实际生效单列。

## 联合候选与组合证据工程验收（2026-09-21）

本节对应 J1–J4，覆盖上文先登记实体再发现属性/关系的顺序要求。主文档新运行仍默认
`evidence_review`，区域卡允许一次 discovery 同时提出实体、属性、关系；随后独立核验，
关系可引用同轮新端点。输出谓词必须属于当前冻结卡中实际主体类型的合法菜单。

最小复验，在 `backend/` 执行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_joint_region_discovery.py \
  tests/test_extraction/test_ontology_scope_fidelity.py \
  tests/test_extraction/test_evidence_review_executor.py \
  tests/test_extraction/test_evidence_review_acceptance.py \
  tests/test_extraction/test_evidence_review_persistence.py \
  tests/test_extraction/test_document_target_graph.py
```

在 `frontend/` 执行 `node --test tests/target-graph.test.mjs`，核对实虚线、未采信理由、
属性原文/规范化分列及 SHACL 诊断展示。

工程验收判据：

- 受控模型使用两段实际解析的测试 DOCX，一次发现和一次核验产出两个实体、两个属性和
  一条带条件的采信关系；多段原文共同支持谓词，不拼造引文。属性未采信不阻断关系核对。
- 无额外条件/反证时保存明确判断和精确检查范围；有指定反证、真实条件缺引文或检查范围
  被篡改时不得取得证明。非法谓词、错主体、假引文和未通过的端点类型不能成为采信关系。
- 联合发现保存后暂停，冷恢复只继续核验；执行层保存区域关系，已完成结果不重发模型调用。
  下层任务可读取当前授权范围内的端点原文属性，不能借属性扩大原文权限。
- 同一冻结本体改换命名空间，合法关系范围保持一致；不再按 CMC 类/谓词收窄类型，不按
  清洗术语重写实体粒度，不按固定 contentHash IRI 隐藏目标。类型和关系仍依据本体定义判断。

这些检查使用隔离存储和受控模型，不代表指定上传报告的真实质量、全文覆盖或速度提升。
工程验收时尚未部署，未新建真实模型运行；历史实测数据保持原样。用户随后要求重新部署，
主后端已于 09:55:58 UTC 加载本次修改，见 [主服务部署记录](joint-candidate-deployment.md)。

本轮实际结果：后端 32 文件受影响回归 **532 passed**（145.68 秒），涵盖联合发现、原文采信、
冻结范围、Schema、适配器、执行/持久化、恢复、目标图及 API。随后新增不完整关系证明反例，
联合发现与关系适配器定向补验 **22 passed**（包含上述回归的重叠用例）。前端目标图
**21 passed**；38 个修改涉及的源码/测试文件 Ruff 及 `git diff --check` 通过。
回归期间修正了空引文误报工具异常的分类，更新当前图对象字段的固定哈希断言并保留往返核对；
未为旧运行添加兼容逻辑。以上不包含 PostgreSQL 专用锁/并发验收或真实模型质量评测。
