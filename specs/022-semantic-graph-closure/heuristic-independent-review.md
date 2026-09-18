# HRS-5592 启发式关系图谱实验：独立原文诊断参考

日期：2026-09-10。

本文件是识别运行之外准备的诊断参考，用于实验结束后核对关键节点的实体、谓词、
属性归属、证据和未决原因。它由独立代理核对原件与当前权威 TTL 形成，**不是经领域
专家复审的金标，不构成完整标注集，也不是本次模型实测结果**。不得据此计算或宣称
全文事实 precision/recall 已达标。

**本文件及其锚点、候选值、冲突清单不得输入识别器、检索查询、提示词、排序器或
运行初始化。**识别输入仍是原件、本体及通用策略；本参考只供结果冻结后的核对。
整理参考时没有读取静态演示图谱的关系或属性答案，没有调用模型，没有修改本体。

## 1. 来源与索引约定

原件名称：`1、HRS-5592原料临床备样生产信息表-DP-C-PI-S-X21372 2602 01 - 删减结构式供外版.docx`。

本次只读核对路径：`/tmp/ontology-performance-20260910/template-new/source.docx`。
这是本机已有原件副本，未改写历史实验；后续新实验应复制到自己的运行目录并核对
同一哈希。该临时路径不是长期制品保留承诺。

- 文件大小：`385664` 字节。
- SHA256：`2c1174bf616f30dd28c655fef4261c167762de807c8ad79e148fb8ef18e16436`。
- 读取到 `187` 个顶层段落、`14` 个顶层表格。
- `P31` 表示 `Document.paragraphs[31]`，`T9R1` 表示
  `Document.tables[9].rows[1]`，均为 **零基索引**，表头也计为一行。
- 段落索引不计表格中的段落；这不是在线 IR 的 record ID。将结果与本参考核对时，
  应以原文文本及表头回放为准，不直接把 `P/T/R` 当成运行的引用标识。
- 若需要对照 XML，正文 `body` 子节点的零基位置分别为：P31→32、P32→33、
  P33→34、P107→116、P108→117、T8→125、T9→129、T10→133。

本体读取自 [drug-development](../../ontology/slpra/slpra-drug-development.ttl)、
[drug](../../ontology/slpra/slpra-drug.ttl)、
[equipment](../../ontology/slpra/slpra-equipment.ttl)、
[cleaning](../../ontology/slpra/slpra-cleaning.ttl) 与
[integration](../../ontology/slpra/slpra-integration.ttl)。诊断时文件哈希如下；它们只是
本参考所查文件的身份记录，不能代替新实验完整本体快照的冻结清单。

| 文件 | SHA256 |
|---|---|
| `slpra-drug-development.ttl` | `ea90db5a324036c71a6e6b559b48aa14d202de75d2e491989791859abe2f8e25` |
| `slpra-drug.ttl` | `f4acbc4277938be189380b99eb572a171fc9c340b17e821ac8c232a67b31ef53` |
| `slpra-equipment.ttl` | `f3d8682598f19c8448fa0eeefe66e060a0eff7bfc0d1dd9da71e3087ac95d998` |
| `slpra-cleaning.ttl` | `f0f7ff3e1f173e5e464260f8849f5f05d2323b58075ab7cb26f1f9c51bffd7ef` |
| `slpra-integration.ttl` | `49e7c2caa01ccf80a450a0fc05950aa30ffd7cab24784229e6b40dd541c49146` |

## 2. 关键节点的局部证据与当前契约

下表使用第 5 节的短名，完整 IRI 均列于该节。“合法”指当前 T-Box 中定义的类型或
谓词以及相应 domain/range；它不单独证明原文事实成立。原文中的计划、要求、否定
和角色仍须保留。

| 核对节点 | 当前契约 | 原文锚点与判断 |
|---|---|---|
| 文档及所描述产品 | `dev:CMCReport` 经 `dev:describes` 指向 `drug:DrugProduct` | P25：“HRS-5592项目为KRAS G12D抑制剂……规格为原料药……属于肿瘤药产品”；P26：“原料药HRS-5592计划在2026年08月……”。可以核对产品名称 HRS-5592 及文档描述关系；原料药与 DrugProduct 的类型张力须单独记录，见第 4 节。 |
| 产品项目名 | `drug:projectName`，domain `drug:DrugProduct`，range `xsd:string` | P28“产品的基本性质”下，P33“项目名称：HRS-5592”。局部字段值为 `HRS-5592`。P38“结构特点……”整句不是产品实体名称。 |
| 产品分子式 | `drug:molecularFormula`，domain `drug:DrugProduct`，range `xsd:string` | P31“分子式：C26H25F2N7O2S”。这是产品基本性质中的局部原文值；后文还有冲突性描述，不能直接认定为全文唯一可信值。 |
| 产品分子量 | `drug:molecularWeight`，domain `drug:DrugProduct`，range `xsd:decimal` | P32“分子量：537.18”。局部数值为 `537.18`，该原句未注明单位；不得补造单位或忽略第 3 节的冲突。 |
| 文档及整体合成路线 | `dev:hasSynthesisRoute` → `dev:SynthesisRoute` | P51—P56依次含“工艺”“工艺描述”“3.1.1合成路线图”“3.1.2 工艺描述”；P123“总工艺：”与 P124—P127 贯穿 A14、A15、HRS-5592粗品、成品。整体路线判断应以整条链及最终产物为依据，不能只把某个局部阶段标题当整个产品路线。 |
| 路线及步骤 | `dev:hasStep` → `dev:SynthesisStep`；步骤顺序可用 `dev:nextStep` | P57“步骤1：SM5592-A14的合成”；P61“步骤2：SM5592-A15的合成”；P64“步骤3：HRS-5592粗品的合成”；P68“步骤3：HRS-5592成品的合成”。T0—T3分别给出对应操作。原文有两个“步骤3”，不得静默改为“步骤4”。文档阶段与单元操作的粒度还需区分。 |
| 清洁过程 | `dev:hasCleaningMethod` → `clean:CleaningProcess` | P115“设备清洗方法”；T8R0表头“序号／设备名称及编号／清洁方法”。T8R1给出100L反应釜的完整清洗次序，见下文。对象应是清洁过程，不能仅把设备“100L反应釜”改成清洁过程类型。 |
| 设备表首条设备 | `dev:usesEquipment` → `equip:Equipment`；`equip:Reactor` 为合法子类 | T9R0表头含设备名称、编号、规格、用途；T9R1为“SM5592-A14制备／1000L反应釜／RE64615/RE64215／1000L／搪玻璃”，用途“投料1/3/4+减压浓缩+热水打浆”。T0R0也明确“于1000L反应釜（RE64615/RE64215）中加入……”。编号组合不能自动拆成两台确定同时使用的设备。 |
| 设备属性 | `equip:equipmentName`、`equip:equipmentID`、`equip:modelSpecification`，均为 `xsd:string` | T9R1分别支持“1000L反应釜”“RE64615/RE64215”“1000L”。这些属性的主体是该设备表行所指设备，不能仅凭容量同名与清洗表另一行强行合并。 |
| 物料表首条物料 | `dev:usesMaterial` → `dev:ProcessMaterial`；`dev:StartingMaterial` 为合法子类 | T10R0为“物料编码／物料名称／规格／批耗量……”；T10R1为“02002357／SM5592-A13／工业／15kg”。T0R0明确加入15.0kg的SM5592-A13，T0R5明确“原料SM5592-A13”，可支持其投料与起始原料角色。 |
| 首物料规格 | `dev:materialGrade`，domain `dev:ProcessMaterial`，range `xsd:string` | T10R1“规格”列为“工业”。本次在当前权威 TTL 中未找到名为 `materialCode` 或 `batchConsumption` 的属性；编码与15kg可以保留证据，不能自行发明谓词或挤入物料规格。 |

T8R1的清洗证据应读作同一设备对应的完整流程：

1. “用纯化水冲洗反应釜内壁5~10min”，并操作底阀清洗。
2. “加32kg丙酮回流（55~65℃）洗涤10~20min，通过管道排出”。
3. “加纯化水40L回流（95~105℃）洗涤10~20min，通过管道排出”。
4. “加纯化水80L搅拌洗涤10~20min，通过管道排出”。
5. “烘干至目视无水渍并挂清洁标志牌”。

若为该过程生成“100L反应釜清洁过程”之类可读名称，应标记为依据表头和行归属
生成的显示名，不能声称原文连续出现了这个名称。原文也不足以仅凭“回流”断定
该过程是 CIP 或 SIP，或把清洁要求当作清洁验证已经通过。

## 3. 全文范围内发现的产品属性冲突

启发式首轮命中 P31/P32 可以检验局部字段的识别与挂接速度，但不能据此检验全文
唯一值已确立。下列是本次定向核对发现的冲突性原文，并非全文矛盾的穷尽清单。

| 原文位置和角色 | 分子式原文 | 分子量原文 |
|---|---|---|
| P28“产品的基本性质”范围内的 P31/P32，P33项目名为HRS-5592 | `C26H25F2N7O2S` | `537.18` |
| P83“设备清洗残留物基本信息”范围内，P104“HRS-5592CP/HRS-5592”下的 P107/P108 | `C38H40O3N8F4S` | `764.84` |
| T9R17等设备需求行，主残留物为HRS-5592粗品；T9R23等行主残留物为HRS-5592 | `C35H40F4O3N8S` | `FW=764.8` |

这三组分子式的元素计数不同，不是仅换了元素排列顺序；537.18与764.84也不同。
764.84与764.8可以有精度差，但该差别不能消解相应分子式差异。

这些原文可能涉及产品、粗品与清洗残留物的角色差异，也可能有源文档错误。
本参考没有领域专家依据判定哪一组是正确化学事实，因此不指定一个“正确答案”
替代其余来源。实测核对时应分别记录：

- 是否正确识别、引用和挂接了所读取局部字段。
- 是否保留产品、工艺中间体、粗品和残留物的主体角色及身份不确定性。
- 是否发现并报告竞争值；尚未检查后文时，是否避免宣称全文一致。
- 是否在没有新证据时覆盖、拼接或合并出原文没有的新分子式/数值。

## 4. 类型、层级和身份的诊断边界

### 4.1 原料药与 DrugProduct 的契约张力

`dev:describes` 的 range 仅为 `drug:DrugProduct`，但它的 comment 明确写
“drug product / drug substance”。另一方面，`drug:DrugProduct` 的 comment 是
“A finished dosage form drug product that bears a clinical drug role.”，而原文 P25
明确“规格为原料药”。`drug:ActivePharmaceuticalIngredient` 与 `drug:DrugProduct`
分别继承 BFO material entity，当前定义未将 API 声明为 DrugProduct 的子类。

因此，应把“在当前菜单中允许挂接 DrugProduct”与“原文可以支持 API 的语义分类”
分开核对。模型若选 API 后被 `describes` 的类型门禁拒绝，不能自动记为模型无原文
证据；模型若选 DrugProduct，也不意味着这处本体类型张力已经解决。当前 TTL 是
实验约束，本参考不建议为通过实验而改 range、跳过门禁或强改输出类型。

### 4.2 整体路线、文档阶段与单元操作

`dev:SynthesisRoute` 的 comment 是从起始物料经中间体至最终 API 的整体合成路径；
`dev:SynthesisStep` 的 comment 是其中的单个单元操作，例如偶联、脱保护、成盐、
纯化。文档“SM5592-A14的合成”阶段包含投料、反应、浓缩、过滤、结晶、干燥等
多行操作。它与单一 `dev:Reaction` 不是天然同一个粒度。

本次参考可以确认以下结构性证据：P123—P127给出整体链；P57/P61/P64/P68给出
文档阶段；T0—T3给出具体操作。不能仅凭标题相邻就把任意操作挂到同一路线，
也不能强行将一个中间体阶段命名成整个 HRS-5592 的完整路线。

对产物的核对同样保留层级：`dev:producesIntermediate` 指向非最终工艺中间体；
`dev:producesFinalProduct` 指向最终 DrugProduct/API。P124/P125分别说得到A14/A15，
P126说得到HRS-5592粗品，P127说“即得HRS-5592”。这些明示产物与前后投料
是建立链条的证据，不能由同一报告共现替代。

### 4.3 设备编号、清洗目标及物料身份

`dev:usesEquipment` 的 domain 是 CMCReport 与 SynthesisStep 的 union，comment
明确指所需设备；它不是“实际生产已经发生”的证明。设备需求和投料操作可支持
文档层的设备要求，但应保留未来生产计划及条件。

T9中的`RE64615/RE64215`以及其他行的“或”要原样保留其组合或选择含义。
同名设备、相同容量、同报告出现均不足以保证同一实体、确定同时使用或全局唯一。
T8R1仅写100L反应釜，不能仅用容量将它等同于T9中某个已编号设备。

`dev:usesMaterial` 当前声明了 range `dev:ProcessMaterial`，没有显式 domain；
不要擅自把它的 domain 描述为与 `usesEquipment` 相同的 union。SM5592-A13的
名称、编码和用量来自同一物料行，起始原料角色还有T0直接投料和“原料”文字
支持；编号的全局唯一性仍需要显式命名空间证据。

## 5. 本参考使用的完整 IRI

下表列出本文所用本体类型、谓词与数据类型的完整 IRI。短名仅用于排版，不能
以中文标签代替运行中实际冻结的 IRI。

| 短名 | 完整 IRI |
|---|---|
| `dev:CMCReport` | `https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport` |
| `dev:describes` | `https://ontology.pharma-gmp.cn/slpra/drug-development/describes` |
| `drug:DrugProduct` | `https://ontology.pharma-gmp.cn/slpra/drug/DrugProduct` |
| `drug:ActivePharmaceuticalIngredient` | `https://ontology.pharma-gmp.cn/slpra/drug/ActivePharmaceuticalIngredient` |
| `drug:projectName` | `https://ontology.pharma-gmp.cn/slpra/drug/projectName` |
| `drug:molecularFormula` | `https://ontology.pharma-gmp.cn/slpra/drug/molecularFormula` |
| `drug:molecularWeight` | `https://ontology.pharma-gmp.cn/slpra/drug/molecularWeight` |
| `dev:hasSynthesisRoute` | `https://ontology.pharma-gmp.cn/slpra/drug-development/hasSynthesisRoute` |
| `dev:SynthesisRoute` | `https://ontology.pharma-gmp.cn/slpra/drug-development/SynthesisRoute` |
| `dev:hasStep` | `https://ontology.pharma-gmp.cn/slpra/drug-development/hasStep` |
| `dev:SynthesisStep` | `https://ontology.pharma-gmp.cn/slpra/drug-development/SynthesisStep` |
| `dev:nextStep` | `https://ontology.pharma-gmp.cn/slpra/drug-development/nextStep` |
| `dev:Reaction` | `https://ontology.pharma-gmp.cn/slpra/drug-development/Reaction` |
| `dev:producesIntermediate` | `https://ontology.pharma-gmp.cn/slpra/drug-development/producesIntermediate` |
| `dev:producesFinalProduct` | `https://ontology.pharma-gmp.cn/slpra/drug-development/producesFinalProduct` |
| `dev:hasCleaningMethod` | `https://ontology.pharma-gmp.cn/slpra/drug-development/hasCleaningMethod` |
| `clean:CleaningProcess` | `https://ontology.pharma-gmp.cn/slpra/cleaning/CleaningProcess` |
| `dev:usesEquipment` | `https://ontology.pharma-gmp.cn/slpra/drug-development/usesEquipment` |
| `equip:Equipment` | `https://ontology.pharma-gmp.cn/slpra/equipment/Equipment` |
| `equip:Reactor` | `https://ontology.pharma-gmp.cn/slpra/equipment/Reactor` |
| `equip:equipmentName` | `https://ontology.pharma-gmp.cn/slpra/equipment/equipmentName` |
| `equip:equipmentID` | `https://ontology.pharma-gmp.cn/slpra/equipment/equipmentID` |
| `equip:modelSpecification` | `https://ontology.pharma-gmp.cn/slpra/equipment/modelSpecification` |
| `dev:usesMaterial` | `https://ontology.pharma-gmp.cn/slpra/drug-development/usesMaterial` |
| `dev:ProcessMaterial` | `https://ontology.pharma-gmp.cn/slpra/drug-development/ProcessMaterial` |
| `dev:StartingMaterial` | `https://ontology.pharma-gmp.cn/slpra/drug-development/StartingMaterial` |
| `dev:materialGrade` | `https://ontology.pharma-gmp.cn/slpra/drug-development/materialGrade` |
| `xsd:string` | `http://www.w3.org/2001/XMLSchema#string` |
| `xsd:decimal` | `http://www.w3.org/2001/XMLSchema#decimal` |

## 6. 后续实验的使用口径

结果冻结后逐项比较本参考，记录实际发现、核验、准入与属性写入状态。应分别
报告“未调度”“未召回”“技术失败”“模型拒绝”“本体类型不合”“证据冲突”与
“已支持”，不能将它们归为同一种空结果。

性能测量可分别记录首条候选、首条有效关系、首条属性、关键分支以及整轮预算结束
的时间。局部挂接正确与原文回放通过不代表化学事实正确或全文一致；本参考没有
全量标注分母，不能将这些诊断点包装为完整准确率。

编制过程中最后一次只读环境采样为 `2026-09-10T04:01:04Z`：本机8080的两个推理
槽位均空闲，应用库中有效租约的 queued/running 请求为零。这仅是该时刻的采样，
不表示后续实验独占模型；模型实测的时间、负载和结果应由运行制品另行记录。
