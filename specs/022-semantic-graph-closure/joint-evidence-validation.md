# HRS-5592 跨记录互补证据实证记录

日期：2026-09-10。实证已完成，未部署。结论：**支持保留未决断言并进行跨记录补证，
但“仅增加上下文”未实现更快获得正确关系和属性；本轮有效属性仍为0。**

产品名称对应的同一候选从未决变为系统通过，补充简介实际进入类型及谓词证明，
随后属性任务得以执行。这验证了用户提出的阻断机制。不过，产品类型与身份仍有争议，
属性失败还受到字段角色、主体引用和证明方式协议影响，不能把所有失败归因于分段。

## 1. 本轮改变了什么

依据[预登记](joint-evidence-validation-plan.md)，对五个登记的根关系目标分别执行：

- **current**：当前正式上下文装配，含现有字段组、表头、局部主体和入边证据。
- **joint**：在current基础上加入登记的跨记录原文，新增片段保持
  `fact_eligible=false`，只能支持证明，不能直接产生另一记录中的对象或属性值。

两组使用同一主体、谓词、目标记录及claim lineage；新增证据绑定新task/context/target。
发现、独立核验提示、引用协议和ProofGate不变。产品、计划、设备、路线各有一条候选
在两组保持相同candidate_id，可追踪同一断言的变化；细胞毒诊断两组提出的候选不同，
因此不是同一细胞毒断言的严格配对纠错。

这是**预选原文位置的定向干预**。脚本复用正式识别适配器及证明门，显式安排两组任务
并观察有效图投影，没有运行生产执行器的自动检索、依赖冲突裁决和持久恢复。
不能用它报告全文召回、生产数据库性能、自动聚合已可用或完整图谱已完成。
线上核心、本体、原件和r1制品均未修改。说明和指标中的“系统通过”也不等于专家确认。

## 2. 冻结身份与预算

| 项目 | 实际值 |
|---|---|
| 原件 | 用户指定的《1、HRS-5592原料临床备样生产信息表-DP-C-PI-S-X21372 2602 01 - 删减结构式供外版.docx》；本轮复制为`source.docx` |
| 原件SHA256 | `2c1174bf616f30dd28c655fef4261c167762de807c8ad79e148fb8ef18e16436` |
| 根 | `https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport` |
| 本体 | 当前权威`ontology/slpra`冻结副本；语义hash `25a0576f6f6d0efb002c44c5449dfba4329144da3868f9b6234a4038d3d68b6c` |
| 模型 | 本机`Qwen3.6-35B-A3B`，GGUF UD-Q4_K_M；温度0.1，主请求在途1；与r1相同模型配置来源 |
| 模型制品身份 | `0b21525e972670ed59e1812e170b27c26355381f0656ecc4e25617ece7dac58b` |
| 运行ID | `joint-5081e1e747494f538e73528d3870a1cf` |
| 冻结文件 | 265项，识别及最终回放后均核验一致 |
| 冻结集合hash | `5145adf5f1db6b1dfeefc2d84518cb0a393d3ea68e0ea63caaa4a9ca815934f8` |
| 预算 | 32任务、48主请求、1200秒软截止，每lineage两组共用最多4次请求 |
| 隔离 | 新SQLite仅用于模型调度；不接业务事实提交；语义排序关闭，不下载权重 |
| 环境观察 | 4×Tesla P100；开始/结束LLM两槽均空闲。未连续采样，不能宣称全程独占或消除了缓存/负载影响 |

运行目录为`/tmp/ontology-joint-evidence-hrs5592-20260910-r2`。真实模型前冻结
`cases.json`、`protocol.md`、源码、原件、本体、配置和限额；运行后不调整这些输入。
本次不读取静态图谱、旧识别输出或专家参考作为模型输入。

## 3. 根关系的结果及耗时

下表时间是各任务自身壁钟，包含上下文装配、发现、独立核验和证明处理；
不是生产环境从上传起算的首达时间，也不是多次独立运行的均值。

| 同一原文目标 | current | joint | 独立核对 |
|---|---|---|---|
| 产品名称P33→`describes` | 44.74秒，未决 | 156.63秒，2条系统通过 | 同一DrugProduct候选确由未决转为支持，P25实际进入证明；但同一名称另建ClinicalTrialDrug节点，API/成品制剂本体张力仍在 |
| 简介P25→`describes`，细胞毒混淆诊断 | 49.60秒，2条系统通过 | 62.30秒，拒绝，0有效边 | current将“属于肿瘤药产品”“目前项目处于I期临床阶段”两句状态描述当对象；joint利用“细胞毒性：否”拒绝CytotoxicDrug，但两组候选不同 |
| 完整计划P26→`hasProductionPlan` | 29.21秒，拒绝 | 30.68秒，未决 | joint五项语义判断全支持，最终因`document_subject_description`不属于该谓词允许的证明方式而阻断 |
| 设备需求表9第1行→`usesEquipment` | 52.32秒，通过 | 68.99秒，通过 | 两组识别同一个1000L反应釜；joint额外引用实际使用记录，但current已有足够表格证据，没有观察到召回增益 |
| 路线标题P53→`hasSynthesisRoute` | 38.34秒，拒绝 | 43.94秒，未决 | joint五项语义判断全支持，也被`document_subject_description`阻断；正式谓词证明只引“总工艺：”，不能视为完整步骤链已证明 |

表格坐标采用IR零基。current不是“仅一段没有上下文”：产品名称组已有19个片段，
joint为22个；设备为26→32个；路线为24→29个。路线current原已包含步骤标题和
四张操作表的前两行，joint实际新增的是P123–P127五段总工艺说明。
设备登记的操作行经逻辑记录闭包还带入表0第0行，最终联合组的实际使用证明引用了
该行的投料原文；不能只按登记选择器猜测模型读到哪些片段。

产品名称的发现候选由1个增至9个，联合组发现43.36秒、独立核验113.03秒。
其中CommercialDrug被拒绝；六个否定类型候选却被写成`negated describes`，
还出现“非青霉素⇒非β内酰胺”的错误外推。这些不进入肯定有效图，但仍是质量问题。
跨段信息增加后，模型候选分支和验证成本同时扩大，不能把更多候选当作更高质量。

## 4. 属性是否因父关系成立而推进

实际派发6个属性任务，最终全部未形成有效属性：

| 属性目标 | current | joint | 直接阻断原因 |
|---|---|---|---|
| 产品`projectName`，P33 | 父关系未有效，未派发 | 38.43秒，未决 | 谓词、桥接和主体已支持，但`field_role`未决；模型仍混淆字面值与实体类型 |
| 产品`appearance`，P36 | 父关系未有效，未派发 | 38.25秒，未决 | `field_role`未决；本轮已引用完整P33，`local_coreference`通过 |
| 设备`equipmentID`，表9第1行 | 40.43秒，未决 | 42.92秒，未决 | current所有语义判断支持但选`adjacent_only`；joint另有字段角色未决，不能靠邻接建立证明 |
| 设备`modelSpecification`，同一行 | 35.43秒，拒绝 | 57.00秒，未决 | current把规格列误读为搅拌体积；joint又把设备编号当型号候选、名称当条件，并有非法证明方式 |

产品分支选择了模型新产生、按稳定身份排序得到的ClinicalTrialDrug父节点；没有按答案
挑选父实体。它继承相应属性菜单，但这不代表DrugProduct/API类型争议已解决。
两组设备父实体相同，设备属性可作同主体对照；产品属性没有current执行结果，不能
伪造配对耗时或把未派发计作0秒。

相对识别T0：产品joint关系任务在201.38秒完成，首属性在201.38秒开始、239.82秒
结束，仍未决。设备joint关系在518.86秒完成；按预登记顺序先完成另一父关系组后，
其首属性在571.19秒开始。**父关系门确实控制了属性派发，但父门解除不保证属性通过。**
这些时间受本次诊断任务顺序影响，不能解释为自动调度优化后的端到端首达时间。

由于父关系未有效，另有10个登记的条件性属性任务未执行：产品current的2项、
生产计划两组各3项、路线两组各1项。计划日期错配反例也在这10项内，
本轮真实模型未验证该反例，不能宣称它通过了安全验收。

## 5. 已定位到的具体机制

1. **互补证据没有自动回到旧断言。**工程正例中A有名称、B有角色说明，A单独未决，
   B单独无相应对象端点；A+B联合后同一候选通过。当前完整未决不会因普通正向补证
   自动重验；实验显式添加来源后才复检。原文并没有被删除，缺口在候选组织与重验触发。
2. **现有主体归属门仍要求完整引用。**新增说明是辅助来源，不自动变成正式owner字段。
   本轮appearance已引用完整P33，共指判断通过；不能沿用r1的归属失败解释本轮结果。
   设备型号current却只返回主体支持判断、没有主体引用，程序将共指降为未决。
   工程反例也确认任意远处owner说明不能自动获准绑定；不能删除该门掩盖引用缺口。
3. **发现协议容许选择下游不接受的证明方式。**计划和路线joint的所有语义判断均支持，
   却选择仅适用于`describes`的`document_subject_description`；设备编号current选择
   `adjacent_only`。发现阶段冻结该选择，验证只能判断它，不能修改它。
   应从实际谓词政策生成可选菜单，必要时另建合法目标重验，不把非法结果自动改名放行。
4. **字面量与实体、提及与类型仍混淆。**属性路径本身不创建type decision，不能说
   `type_verdict=undetermined`直接挡住属性；产品属性的直接语义阻断是`field_role`。
   模型理由中要求字面值有实体类型、要求局部计划有独立实体名称或预存实例，暴露了协议问题。
5. **来源数量不等于证明完整。**细胞毒joint在自由文本理由中提到P42，却没有把它
   填入结构化反证引用。路线joint理由提及完整工艺，但正式引用只到“总工艺：”。
   判定为支持、看到足够文本、返回完整可回放证明是三个不同条件。

详细逐候选核对见[独立审查](joint-evidence-independent-review.md)。这是原文及本体诊断，
不是专家批准金标，未计算正式precision/recall或错误率。

## 6. 时间、费用和结束状态

| 节点/阶段 | 实测 |
|---|---:|
| DOCX解析、IR及记录索引 | 1.30秒 |
| 本体加载及快照 | 1.20秒 |
| 识别壁钟 | 829.27秒，即13分49秒 |
| 包含前置及任务制品写入的观察总壁钟 | 832.39秒；不含最终调度导出及末次hash归档 |
| 发现 | 16请求，客户端请求累计300.04秒 |
| 独立核验 | 16请求，客户端请求累计525.98秒 |
| 主请求累计 | 826.014秒，约识别壁钟99.6% |
| 模型服务预填充 / 生成 | 481.58秒 / 278.60秒 |
| 主模型prompt / completion / cache tokens | 137411 / 16239 / 17344 |
| 调度排队累计 | 0.354秒 |
| 任务 / 主请求 | 16 / 32，全部请求完成，无技术失败 |
| 停止原因 | `registered_tasks_finished`；所有可派发登记任务结束，未触及预算 |
| 全文完成、语义排序、生产SQL成本 | 未测量 |

阶段时间存在包含关系，不能相加为总耗时。真实运行没有单独计量ProofGate纯函数耗时；
后述回放测得的是离线诊断耗时，不能填入此处冒充原运行数据。上下文计数和真实请求
token口径不同，主模型费用以上表调度记录中的实际请求用量为准。

只有一轮、每目标每组一次且共享暖模型服务，不能报告统计显著性、稳定P95、提速倍数，
也不能与r1的全文启发式任务顺序和存储开销直接比较。

## 7. 工程验证与无模型回放

新增[证据权限及父子门测试](../../backend/tests/test_extraction/test_joint_evidence_validation.py)
10项，以及[实验选择器和预算测试](../../backend/tests/test_extraction/test_joint_evidence_runner.py)
7项。前者控制模型传输响应，保留正式引用协议、发现/独立核验、ProofGate及投影；
它证明程序机制，不能充当真实模型准确性。包括A/B互补、B单独无端点、辅助来源
不获得新事实权限、跨文档与超预算零派发、否定、条件、竞争归属和父关系后属性调度。

本轮实际运行的定向回归：

```bash
cd backend
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_joint_evidence_validation.py \
  tests/test_extraction/test_joint_evidence_runner.py \
  tests/test_extraction/test_semantic_proof_regressions.py \
  tests/test_extraction/test_heuristic_executor.py \
  tests/test_extraction/test_ontology_guided_boundaries.py
```

结果：**46 passed，8.44秒**，4项既有依赖/schema警告。三个新增源码/测试文件的
Ruff检查通过。没有生产数据库、浏览器或部署测试。

另以冻结runtime、原始请求和原始模型响应离线重放全部任务，封锁socket、禁用真实
client和tokenizer调用：**16/16 outcome、32/32请求及响应精确复现，零网络尝试**。
明确复现三处“模型语义全部支持、结构门拒绝”：生产计划joint、路线joint、设备编号current。
全部265份冻结输入及原任务/请求/响应/结果前后hash一致。

## 8. 制品与可重复执行

- [独立实验runner](../../backend/scripts/benchmark_joint_evidence.py)：准备与执行分离，执行仅接受冻结副本。
- [紧凑指标](heuristic-evidence/hrs5592-r2-joint.metrics.json)：身份、任务结果、各阶段费用、配对差异和证明门诊断。
- 受控本机目录`/tmp/ontology-joint-evidence-hrs5592-20260910-r2`：
  `manifest.json`、`cases.json`、`ir.json`、`ontology-snapshot.json`、`tasks/*/input.json`、
  原始`request-*.json`及响应、`outcome.json`、逐组`graphs`、`events.jsonl`、
  `model-requests.json`、`summary.json`、`metrics.json`、`gate-diagnostics.json`。
- `reporting/summarize_metrics.py`和`reporting/replay_gate.py`分别复算指标与离线证明门；
  后处理新增制品以`supplemental-artifact-hashes.json`归档，不重写已冻结输入或原始结果。

新真实实验须使用新目录及身份：

```bash
python backend/scripts/benchmark_joint_evidence.py --prepare \
  --source /controlled/source.docx \
  --model-config /controlled/non-secret-model-config.json \
  --output /tmp/hrs5592-joint-new-run
backend/.venv-cuda12/bin/python \
  /tmp/hrs5592-joint-new-run/runtime/benchmark_joint_evidence.py \
  --execute /tmp/hrs5592-joint-new-run
```

后续实施要求已补入[性能优化方案第8.13节](../../docs/关系谱图识别新内核的性能优化方案.md)：
按断言组织有界补证与重验，同时修复实体/字面量、owner来源和谓词证明方式协议，
再测正确关系及属性首达。本轮没有改变线上识别行为，不能据此宣布优化目标已达成。
