# Palantir Foundry / AIP:基于本体与知识图谱的低代码应用实现机制(深度还原 + 自研借鉴)

> 版本:v1.0 ｜ 调研日期:2026-06-23 ｜ 面向:自研 ontology-agent(本体 + 知识图谱 + 低代码平台)

> **可信度声明(先读这段)**:本报告全部结论源自 Palantir 官方一手产品文档(`palantir.com/docs/foundry`)与官方工程博客。其中**语义层、对象后端、回写机制、Workshop 运行时**这四块经过了多源、三票对抗式验证(均 3-0 一致通过);**OSDK、AIP、权限/分支治理**三块由合成阶段补抓一手文档补齐,属"厂商文档描述",未做独立基准复现。两个硬边界:① **OSv1/Phonograph 将于 2026-06-30(距调研日约一周)下线**,其专属机制(writeback 数据集、直连编辑 API)已属历史,新设计只应参考 OSv2;② 厂商文档描述的是"设计意图与文档化行为",非第三方实测性能。

---

## 〇、一句话架构观:Ontology 不是"语义层",是"决策操作系统"

Palantir 在架构文档里**明确拒绝**把 Ontology 称作"一层薄薄的 semantic layer 或一个单体设计",而定义为"由数十个底层组件构成的多模态系统"。它的中心命题是:**建模的不是数据,而是企业的互联决策**——通过 **Data(数据)+ Logic(逻辑)+ Action(行动)+ Security(安全)** 四者的一体化集成来表达决策。([architecture-center/ontology-system](https://www.palantir.com/docs/foundry/architecture-center/ontology-system))

这句话是理解后面一切实现的钥匙。它直接决定了一个最重要的设计基因:

> **语义(名词)必须与动能(动词)配对** —— "semantics must be paired with kinetics"。

即:对象/链接(semantic primitives,**名词**)负责"是什么",Actions/Functions(kinetic primitives,**动词**)负责"做什么"。整个平台、低代码层、SDK、AI Agent 全部围绕这条"名词×动词"的二分轴展开。这是 Foundry 区别于"纯图数据库 + BI 报表"的根本。

整体分层(自下而上):

```
┌──────────────────────────────────────────────────────────────┐
│ 消费层  Workshop(低代码) │ OSDK(代码) │ AIP Logic/Agent(AI) │
├──────────────────────────────────────────────────────────────┤
│ 本体语义层  Object Types · Link Types │ Action Types · Functions│
│            （semantic / 名词）         │   （kinetic / 动词）     │
├──────────────────────────────────────────────────────────────┤
│ 运行时对象后端 (Object Storage V2 微服务)                       │
│  OMS(元数据) · Funnel(写/索引) · OSS(读/搜索/聚合) ·          │
│  Actions(唯一写入口) · Object DBs(索引) · Functions(算)       │
├──────────────────────────────────────────────────────────────┤
│ 数据底座  Datasets / Restricted Views / Streams / Models        │
└──────────────────────────────────────────────────────────────┘
            安全(Security)横切贯穿以上每一层
```

---

## 一、本体语义层:Schema-over-Data,而非又一个数据库

### 1.1 四类构造(verbatim 定义)

Ontology 的语义由四种 schema 构造组成,官方定义如下([core-concepts](https://www.palantir.com/docs/foundry/ontology/core-concepts)):

| 构造 | 角色 | 官方定义(原文) |
|---|---|---|
| **Object Type** | 名词·实体 | "schema definition of a real-world entity or event" |
| **Link Type** | 名词·关系 | "relationship between two object types" |
| **Action Type** | 动词·变更 | "set of changes or edits to objects, property values, and links" |
| **Function** | 动词·计算 | "piece of code-based logic that takes in input parameters and returns an output" |

关键定性:Ontology 是"一层富语义层,坐落在数字资产(数据集与模型)之上"(*"a rich semantic layer that sits on top of the digital assets (datasets and models)"*)。**它不是独立存储,而是对底层数据集/模型的类型化投影。**

### 1.2 关系世界 → 对象图的直接映射

文档给出了一张几乎是"翻译字典"级别的映射表,这是工程上最值得抄的一点:

| 关系/表概念 | → | 本体/图概念 |
|---|---|---|
| Dataset | → | Object Type |
| Row(行) | → | Object(对象实例) |
| Column(列) | → | Property(属性) |
| Join(连接) | → | Link Type(链接类型) |

并明确:"可以把每个 object type 看作类比于一个 dataset"(*"you can think of each object type as analogous to a dataset"*)。

**工程含义**:本体建模不需要重新发明一套图存储语义,而是把"表 + 外键 join"这套成熟关系语义**一对一抬升**为"对象 + 链接"。链接类型本质是被声明为一等公民的 join。这让"从已有数仓/数据集冷启动一套知识图谱"成为可能,而不是要求先有图。

### 1.3 双重身份:同一个 Ontology 既是"语义模型"又是"运行时数据图"

这是"本体如何同时承载语义模型 + 运行时数据图"的答案。机制上靠两件事实现:

1. **语义模型(冷)** = OMS(Ontology Metadata Service)里存的 schema:有哪些 object/link/action 类型、属性名、数据类型、描述。这些"不引用真实属性值或主键值"([object-permissioning/overview](https://www.palantir.com/docs/foundry/object-permissioning/overview))。
2. **运行时数据图(热)** = Object Storage V2 把数据源 + 用户编辑**索引**进专用对象数据库后形成的、可被实时查询/搜索/遍历的活体对象集合。

语义模型定义"形状",运行时后端把数据"灌"进这个形状并提供低延迟读写。**两者通过 OMS 元数据 + Funnel 索引管线粘合**,使得"改一次 schema → 运行时图随之生效"成为一个工程闭环。

---

## 二、运行时对象后端:Object Storage V2 的微服务解剖

这是 Foundry 把"语义"变成"可运营的活数据图"的真正引擎。OSv2 是对 OSv1/Phonograph 的**重新架构**,核心动机是"拆分原本在 V1 里被耦合在一起的关注维度",从而独立横向扩展([object-backend/overview](https://www.palantir.com/docs/foundry/object-backend/overview))。

### 2.1 微服务拆分:一个服务一个关注点

| 服务 | 单一职责(原文要点) |
|---|---|
| **OMS** | "defines the set of ontological entities that exist" —— 元数据/schema |
| **Object Databases** | "store the indexed object data" —— 索引后的对象数据存储 |
| **Object Set Service (OSS)** | 读路径:"searching, filtering, aggregating, and loading" |
| **Actions** | 写路径:"responsible for applying user edits to object databases" |
| **Object Data Funnel** | 索引编排:"orchestrating data writes into the Ontology" |
| **Functions on Objects** | 计算:在对象上跑逻辑 |

**这套"元数据 / 读 / 写 / 索引 / 算"五分法,是 OSv2 能扩展到几十亿对象的根本。**

### 2.2 写/索引路径:Object Data Funnel

Funnel 同时吃两类输入并把它们索引进对象数据库:

- **数据源**:datasets、restricted views、streaming datasources;
- **用户编辑**:来自 Actions 的 edits。

并"确保索引数据随底层数据源更新而保持同步"。批管线默认**增量索引**,并按对象类型的**主键**把近期的 Action edits join 进去([object-indexing/overview](https://www.palantir.com/docs/foundry/object-indexing/overview),[funnel-batch-pipelines](https://www.palantir.com/docs/foundry/object-indexing/funnel-batch-pipelines))。

> **要点**:运行时数据图 = `数据源(真相)` ⨝主键 `用户编辑`,增量同步。这一个公式就是"知识图谱实例层"的本质。

### 2.3 读路径:Object Set Service

OSS 提供搜索、过滤、聚合、加载;在 OSv2 中通过 **Spark-based 查询执行层**支撑高规模的 **Search Around**(沿链接遍历的图查询),并支持流式低延迟索引。

### 2.4 规模数字(官方声称,非实测)

- 单个 object type 可达 **数百亿(tens of billions)** 对象;
- 单个 object type 最多 **2000 个属性**;
- 单个 Action 最多编辑 **10,000 个对象**。

### 2.5 历史包袱:OSv1 / Phonograph

OSv1 即 Phonograph,"暴露了大量底层数据库功能、API 面极大",且数据"必须先注册进 Phonograph 才能被查询/展示"。**它被标记为计划下线,2026-06-30 后不可用**([object-storage-v1](https://www.palantir.com/docs/foundry/object-databases/object-storage-v1))。结论:**别参考 OSv1 的任何专属机制建新系统**。

---

## 三、数据回写(Writeback):唯一写入口 + 两层写一致性

这是整套设计里**最精妙、最值得抄**的工程机制。

### 3.1 铁律:所有写入只走 Action Types

- 终端用户"通过 apply actions 来修改对象",Action 可携带"提交时触发的 side effect 行为"([core-concepts](https://www.palantir.com/docs/foundry/ontology/core-concepts));
- OSv2 **只支持经由 Actions 的用户编辑**,且"所有 OSv1 直连 edit API 的查询必须重构为 Actions 才能迁移到 OSv2"([breaking-changes](https://www.palantir.com/docs/foundry/object-backend/object-storage-v2-breaking-changes))。

> **架构原理**:把一切 mutation 收敛进单一受治理的 Action 层——它统一强制权限/条件校验,并可触发副作用。**绝不允许任何路径直接写索引。** 这是"可治理的知识图谱"与"裸图数据库"的分水岭。

### 3.2 两层写:一致性(内存索引)+ 持久性(merged dataset)

提交一个 Action 后的真实路径([how-edits-applied](https://www.palantir.com/docs/foundry/object-edits/how-edits-applied)):

1. **立即应用到对象数据库的内存索引** —— 保证 **read-after-write 一致性**(提交后读"保证包含该用户编辑");
2. Actions 服务向 Funnel **发一条 modification 指令**,进入一个**带 offset 跟踪的 Funnel 队列**,以支持**并发编辑**的排序;
3. 索引数据是**易失的(ephemeral)**;持久性来自一个 **merged dataset**(= 数据源 + 用户编辑的合并),在**有新数据源事务时重建**,否则**每 6 小时**在检测到编辑时 flush 一次。

> **抄作业要点**:把"被服务的索引"当作**易失缓存**;把"真相"持久化为 `源 + 编辑` 的 **merged 数据集**;用**带 offset 的队列**解决并发写排序与读后写保证。这是一套教科书级的 CQRS + 事件日志变体。

### 3.3 物化(Materializations)

OSv1 的"writeback 数据集"(必需)在 OSv2 里变成**可选的 materializations**:一份"反映每个对象最新状态"的数据集(源 + 编辑合并),供下游管线和批量导出用。OSv2 里**启用编辑只需一个配置开关**,与是否物化解耦([materializations](https://www.palantir.com/docs/foundry/object-edits/materializations))。

> **要点**:把"是否允许编辑"和"是否物化产物"**解耦**;只在下游管线/导出需要时才物化。

---

## 四、权限与版本治理:两级授权 + 分支/提案

### 4.1 两级授权模型

授权发生在两个层级([object-permissioning/overview](https://www.palantir.com/docs/foundry/object-permissioning/overview)):

1. **Ontology Resources(schema 级)**:object/link/action 类型本身——只管"显示名、属性名、数据类型、描述"等**结构**,"不引用真实属性值或主键值"。
2. **Objects & Links(数据级)**:带真实主键和属性值的**实例数据**。

### 4.2 行级 / 列级 / 单元格级安全(与数据源解耦)

[object-security-policies](https://www.palantir.com/docs/foundry/object-permissioning/object-security-policies) 的关键机制:

- **Object security policy** 在 object-type 层配置实例可见性,**独立于底层数据源权限** → **行级安全**;
- **Property security policy** 把控制细化到具体属性 → **列级安全**;两者叠加 = **单元格级安全**;
- 行为细节:未过 object policy → 实例不可见;过了 object policy 但未过 property policy → **该属性值显示为 null**;
- 重大解耦:配置了策略后,**用户无需对 backing data source 拥有 Viewer 权限**也能看对象实例;
- 约束:主键属性不能进任何 property security policy;非主键属性最多属于一个 property security policy;
- **物化产物按"最严格权限"求并集**,且事务"按物化构建时生成的安全策略加密",标记变更只对持有该标记期间提交的事务生效。

### 4.3 版本治理:分支(Branching)+ 提案(Proposals)

Foundry 把版本控制实践搬进平台([foundry-branching/core-concepts](https://www.palantir.com/docs/foundry/foundry-branching/core-concepts)):

- **Ontology 资源在分支上完全隔离**:可在分支上"创建/修改/删除实体而不影响 `main`"(注意:**普通 Foundry 资源的创建/删除会影响 main**,但 ontology 资源不会——这是专门为本体治理设计的);
- **Rebase** 拾取 `main` 的更新;真正冲突(同一资源同一属性两边都改)需手动选择;
- **Proposal = 合并请求**:每个资源跑 **checks**,"所有 checks 必须通过才能 merge";"所有审批策略必须满足";**任何一个 reviewer 拒绝就把该资源标为 Rejected**,阻断合并;
- 合并时可选构建策略:全量构建受影响资源 / 仅构建已改资源 / 不构建。

> **要点**:本体 schema 的演进当作**代码评审**来治理——分支隔离 + 提案 + 强制 checks + 多人审批 + 一票否决。

---

## 五、低代码应用构建层:Workshop

Workshop 是 Foundry 的**主力对象导向低代码构建器**,支持 no-code / low-code / code-based 三档,"上手无需技术背景"([app-building/overview](https://www.palantir.com/docs/foundry/app-building/overview))。

### 5.1 运行时只消费两类本体原语

Workshop 运行时"利用 Ontology 内的 **semantic primitives(对象、链接)** 与 **kinetic primitives(Actions、Functions)**":

- **语义原语 → 读/展示**:Object Table、Object List、Object View 等部件直接绑定对象/链接;
- **动能原语 → 受治理的变更**:交互部件调用 Actions/Functions。

> 低代码层与语义层**共享同一套"名词×动词"模型**——这是它能"零胶水"消费本体的根本原因。

### 5.2 Variables:类型化数据流原语 + 惰性计算

Variables 是"数据在 Workshop 模块中如何流动"的配置原语([concepts-variables](https://www.palantir.com/docs/foundry/workshop/concepts-variables)),关键类型:

- **Object set**:存一组对象,可经 **Search Around** 过滤/透视;
- **Object property**、**Object set aggregation**、**Function-backed**(由 Function 计算)。

**惰性计算**是核心性能杠杆:变量"只在被可见部件/布局展示时才(重新)计算";非可见的 page/tab/overlay 里的变量"在被展示前不会计算"。

### 5.3 Events:顺序但异步的事件模型

Events 让构建者"在用户做出某动作时触发特定行为",可由 Button Group、Object Table 行选中、String Dropdown 选择、Tabs 等触发([concepts-events](https://www.palantir.com/docs/foundry/workshop/concepts-events))。执行语义很关键:

- 事件**按配置顺序串行**执行,但**异步**——"不会等待前一个事件的下游计算完成";
- Workshop **不支持强制事件等待所有下游更新完成**;
- 直接的变量 set 是**同步**的,依赖性 transform 是异步的;
- 变通:拆成多个用户触发的事件。

### 5.4 写回闭环(置信度:中,2-1)

提交 Action → 上游对象 reload → 依赖变量**自动重算** → UI 刷新("当上游对象 reload 时重算,例如 action 提交后或 auto-refresh 等数据 reload 触发")。

> **诚实标注**:这条"闭环"是本研究中唯一未全票通过的结论(2-1)。文档同时指出该重算可能"在上游值并未真正改变时也触发",并提供抑制手段(function-backed 变量、其他重算模式)。**借鉴时务必给构建者控制重算范围的能力**,以免无谓刷新。

> **已被证伪、勿抄**:网上常见的"Workshop 有 Layouts/Widgets/Variables/Events/Permissions 五大核心概念"这一分类法,本研究 **0-3 证伪**,不要当作权威分类。

---

## 六、开发者 SDK:Ontology SDK(OSDK)

OSDK 把 Ontology"直接搬进你的开发环境",其架构立场是**把 Foundry 当后端**:外部/独立应用跑高规模查询、回写、并继承治理([ontology-sdk/overview](https://www.palantir.com/docs/foundry/ontology-sdk/overview))。

### 6.1 代码生成机制:从本体元数据 → 类型安全客户端

- "类型和函数从你的 Ontology 生成,让你在编辑器里直接查询和探索 Ontology";
- **只生成你需要的子集**:"为 OSDK 生成的函数和类型,只基于与你相关的那部分 Ontology";
- 生成器读取**本体元数据**(属性名、描述):"生成的代码使用关于你 Ontology 的元数据,包括属性名和描述"。

### 6.2 多语言与分发

- 原生支持 **TypeScript(NPM)、Python(Pip/Conda)、Java(Maven)**;
- 其他语言走 **OpenAPI**:Developer Console → Application API → SDK generation → Other languages → Export as OpenAPI,再用开源 OpenAPI 生成器产出几乎任意语言的客户端([generate-osdk-for-other-languages](https://www.palantir.com/docs/foundry/ontology-sdk/generate-osdk-for-other-languages));
- TS 另有前端绑定,"方便快速在 Foundry 之上构建 React 应用"。

### 6.3 双层安全模型

"OSDK 使用一个**仅限于你希望应用访问的本体实体**的 token,叠加**用户自身对数据的权限**"——即 `scoped app token` ∩ `caller 用户权限`。

### 6.4 生命周期:Developer Console 注册

应用在 Developer Console 创建/管理,注册即定义了**受限实体访问范围**并产出生成 SDK 所依赖的配置。开发者因此"无需维护独立的数据底座,专注 app 逻辑"。

> ⚠️ 导出的 OpenAPI 文件会包含资源的名称与描述,需确保**不含敏感信息**。

---

## 七、AI / AIP 集成:本体作为 LLM 的"工具 + 上下文 + 护栏"

### 7.1 AIP Logic:无代码构建 LLM 函数

AIP Logic 是"创建/测试/发布 LLM 驱动函数"的无代码环境,目标是"利用 Ontology 而不引入开发环境和 API 调用的复杂度"([logic/overview](https://www.palantir.com/docs/foundry/logic/overview))。

- **输入→处理→输出**(类型化):输入可为 Ontology 对象或文本;输出可为对象、字符串,**或直接对 Ontology 做编辑**;
- **Blocks** 是组合单元,核心是 **"Use LLM" block**,它绑定:① prompt;② LLM 可调用的 **Tools**(如限定到某 object type 的 "Query objects" 工具);③ **属性访问**(只授予 LLM 读特定属性);④ **类型化输出变量**;
- 产物是一个**可复用的 Function**,可在别处调用——尤其经 **Automate**,编辑可"自动应用或暂存供人工审核";
- 安全:建立在平台安全模型上,"只授予 LLM 完成任务所必需的访问"。

### 7.2 Agent / Chatbot Studio:把本体暴露为可调用工具

Agent 通过**六类工具**与本体交互([agent-studio/tools](https://www.palantir.com/docs/foundry/agent-studio/tools)):

| 工具 | 作用 |
|---|---|
| **Action** | 执行一次本体编辑;**可配置自动运行或需用户确认**(治理写回的关键) |
| **Object query** | 指定 LLM 可达的对象类型;支持过滤、聚合、检查、**沿链接遍历**;可限属性以省 token |
| **Function** | 调用任意 Foundry function(含 AIP Logic);默认最新版,可锁定版本 |
| **Update application variable** | 修改应用变量 |
| **Command** | 触发其他 Palantir 应用的操作 |
| **Request clarification** | 让 Agent 暂停并向用户澄清 |

**工具如何抵达模型**取决于 **tool mode**:

- **Prompted tool calling**:把工具说明**插入 prompt**;一次只调一个;支持所有工具/模型;
- **Native tool calling**:用模型内建能力直接调工具,**支持并行**;但仅限部分 Palantir 模型,且仅 actions/object query/function/update variable。

并提供 **View reasoning** 审视 LLM 推理过程。

### 7.3 Retrieval Context:确定性检索 + 本体接地

检索上下文"对**每一条**新用户消息**确定性运行**,检索结果喂进 LLM"([agent-studio/retrieval-context](https://www.palantir.com/docs/foundry/agent-studio/retrieval-context))。三类:

- **Ontology context**:两种取数——固定对象集 / **语义搜索**(需对象类型有 **vector embedding 属性**);起始集可为静态(整个对象类型)或变量(过滤后的集合);**可选哪些属性打印进 prompt**(默认全选,排除无法打印的如 media reference、向量);
- **Document context**:全文模式 / 相关分块模式(语义搜索取 top-K chunk,beta);
- **Function-backed context**:用 TS 函数实现 `AipAgentsContextRetrieval` 接口自定义检索,返回一个 `retrievedPrompt` 字符串注入系统提示;可返回对象做**引用(citation)**。

### 7.4 本体为何降低幻觉(架构论点)

机制上的接地有三条腿:① **结构化对象属性直接喂进 prompt**(而非让模型凭记忆);② **工具限定可达对象/属性范围**;③ **security scoping 只给必需访问**。再叠加 **Action 工具的确认步骤**,LLM 的"写"被关进受治理的 Action 闸门。([架构文档](https://www.palantir.com/docs/foundry/architecture-center/ontology-system)印证:AI agent "必须有从人类用户或项目权限结构继承的安全范围"。)

---

## 八、对自研「ontology-agent」(本体 + 知识图谱 + 低代码)的可执行借鉴建议

下面每条都标了**优先级**与**落地动作**,按"四层全要、深机制"的目标排序。

### P0 —— 地基,不抄会后期推倒重来

1. **名词/动词二分作为全局架构轴**。把数据模型拆成 semantic(Object/Link 类型,只读语义)与 kinetic(Action/Function,受治理变更)。让低代码层、SDK、AI Agent **共用同一套原语**——这是"零胶水消费本体"的前提。

2. **语义层做成 schema-over-data,而非又一个图库**。Object Type ≈ 数据集的类型化投影;落地一张映射字典:`表→对象类型 / 行→对象 / 列→属性 / join→链接类型`。这让你能从已有数仓/数据集**冷启动**知识图谱,而不是先要求有图。(本项目已有 BFO 对齐与术语表——可把 BFO 上层范畴作为 Object Type 的**元类型/约束**,术语表作为属性/类型的**描述元数据**,正好对应 OSDK"用元数据生成代码"的入口。)

3. **所有 mutation 收敛到唯一的 Action 入口**。禁止任何路径直写索引/图。Action 层统一做:权限校验 → 条件校验 → 写 → 触发副作用。这是"可治理 KG"与"裸图库"的分界,也是 AI 能安全写回的前提。

4. **读写分离 + 两层写一致性(CQRS 变体)**:
   - 服务用的索引当**易失缓存**;
   - 真相持久化为 `源数据 ⨝主键 用户编辑` 的 **merged 数据集**;
   - 写经**带 offset 的队列**排序,保证并发写顺序 + read-after-write;
   - 周期性 flush(Palantir 用 6h / 新事务触发,按业务调)。

### P1 —— 体验与扩展性,决定能不能做大

5. **索引(写)与查询(读)拆成独立服务**,各自横向扩展(对应 Funnel vs OSS)。早期可单体,但**接口先按这条边界切**,别把搜索/聚合和索引揉在一起。

6. **类型化 Variables + 惰性计算**作为低代码数据流骨架。变量分 `对象集 / 属性 / 聚合 / 函数支撑` 四型;**只在可见部件展示时才计算**——这是低代码运行时最大的性能杠杆。

7. **事件模型设计成"串行配置序、异步执行",并明确同步边界**(直接变量 set 同步,依赖 transform 异步)。把"无法强制等待全部下游"这个限制**显式告诉构建者**,并提供"拆成多个用户触发事件"的官方变通。

8. **写回闭环要可控**:Action 提交 → 失效相关数据 → 重算依赖变量 → 刷新 UI;但**给构建者控制重算范围**的开关(Palantir 这条是 2-1 的弱项,重算会误触发——从一开始就把"精确失效"做对)。

### P1 —— 对外开放与 AI,决定生态

9. **从本体元数据生成类型安全 SDK**:读 OMS 元数据 → 只生成"应用相关子集" → 产出 TS/Python/Java 客户端;其他语言走 **OpenAPI 导出 + 开源生成器**(成本极低,强烈建议直接照搬)。安全用**双层**:`应用 scoped token` ∩ `调用用户权限`。

10. **把本体工具化喂给 LLM**(这是 AIP 最值得抄的部分):
    - **Object query 工具**:声明 LLM 可达的对象类型 + 可读属性(限属性省 token),支持过滤/聚合/**沿链接遍历**;
    - **Action 工具**:LLM 的唯一写路径,**带"需用户确认"开关**;
    - **确定性 retrieval context**:每条消息都跑;Ontology context 支持"固定集 / 向量语义搜索(对象类型挂 embedding 属性)";**可配置哪些属性打印进 prompt**;
    - 提供 **Function-backed 自定义检索**逃生口(返回注入系统提示的字符串 + 引用对象)。
    - 接地三件套对抗幻觉:**结构化属性入 prompt + 工具限范围 + 安全 scoping**。

### P2 —— 治理,规模化后才显价值但要早埋

11. **两级权限**:schema 级(类型/属性定义)与 data 级(实例值)分开授权;支持**行级(object policy)/列级(property policy)/单元格级**;关键解耦:**对象可见性独立于底层数据源权限**(过策略即可见,无需对源表有权限)。

12. **本体演进当代码评审治理**:schema 变更走**分支隔离 + 提案 + 强制 checks + 多人审批 + 一票否决**;让 ontology 资源在分支上**完全隔离**(创建/删除都不影响 main),这正是支撑"安全演进活体图"的机制。

### 与现有 004 / 005 工作的衔接

- 正在做的 IA 重构(总览/本体/实体/应用/治理)与 shadcn UI 重构——上面 #6/#7 的"类型化变量 + 惰性计算 + 事件模型"应作为**低代码运行时的内核契约**先定下来,UI 组件库只是它的渲染层;
- "治理"导航分区可直接落 #11/#12 的**两级权限 + 分支/提案**;
- 本体编辑器(已有数据属性/关系/动作/映射面板)其实就是 Palantir 的 OMS + Action Type 编辑器——确认"映射面板"已实现 #2 的 `表→对象` 映射字典,"动作面板"已是 #3 的唯一写入口语义。

---

## 九、未决问题(本研究未能一手坐实,建议后续补)

1. **OSS 的精确查询/服务路径**:Search Around、聚合、Spark 执行层如何把本体查询翻译成索引/Spark 操作(本研究只坐实了规模数字,未坐实机制)。
2. **Action 的权限检查细节**:文档明确 Action "支持复杂权限与条件",但 "Permission checks for Actions" 专页未取到,具体校验点未坐实。
3. **OSDK 方法级 API 面**:Links/Actions/Queries 的具体客户端方法签名在语言专页,本研究未逐一取证。
4. **AIP Logic 的多步编排/评估/监控**机制细节(blocks 之间如何编排、评估如何打分)未深入。

---

## 主要信源(全部 Palantir 官方一手)

- [ontology/core-concepts](https://www.palantir.com/docs/foundry/ontology/core-concepts)
- [object-backend/overview](https://www.palantir.com/docs/foundry/object-backend/overview)
- [object-databases/object-storage-v1](https://www.palantir.com/docs/foundry/object-databases/object-storage-v1)
- [object-backend/object-storage-v2-breaking-changes](https://www.palantir.com/docs/foundry/object-backend/object-storage-v2-breaking-changes)
- [object-edits/how-edits-applied](https://www.palantir.com/docs/foundry/object-edits/how-edits-applied)
- [object-edits/materializations](https://www.palantir.com/docs/foundry/object-edits/materializations)
- [object-indexing/overview](https://www.palantir.com/docs/foundry/object-indexing/overview) ｜ [object-indexing/funnel-batch-pipelines](https://www.palantir.com/docs/foundry/object-indexing/funnel-batch-pipelines)
- [app-building/overview](https://www.palantir.com/docs/foundry/app-building/overview)
- [workshop/concepts-variables](https://www.palantir.com/docs/foundry/workshop/concepts-variables) ｜ [workshop/concepts-events](https://www.palantir.com/docs/foundry/workshop/concepts-events)
- [ontology-sdk/overview](https://www.palantir.com/docs/foundry/ontology-sdk/overview) ｜ [ontology-sdk/generate-osdk-for-other-languages](https://www.palantir.com/docs/foundry/ontology-sdk/generate-osdk-for-other-languages)
- [logic/overview](https://www.palantir.com/docs/foundry/logic/overview)
- [agent-studio/tools](https://www.palantir.com/docs/foundry/agent-studio/tools) ｜ [agent-studio/retrieval-context](https://www.palantir.com/docs/foundry/agent-studio/retrieval-context)
- [architecture-center/ontology-system](https://www.palantir.com/docs/foundry/architecture-center/ontology-system)
- [object-permissioning/overview](https://www.palantir.com/docs/foundry/object-permissioning/overview) ｜ [object-permissioning/object-security-policies](https://www.palantir.com/docs/foundry/object-permissioning/object-security-policies)
- [foundry-branching/core-concepts](https://www.palantir.com/docs/foundry/foundry-branching/core-concepts)

---

> 调研方法:deep-research 多 agent 扇出(5 角度 × 并行搜索 → 27 源抓取 → 129 条声明抽取 → top-25 三票对抗式验证,24 confirmed / 1 killed)+ 合成阶段对 OSDK / AIP / 治理三层的定向一手补抓。
