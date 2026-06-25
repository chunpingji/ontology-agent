# Phase 0 Research: 推理引擎规则层声明式化

**Date**: 2026-06-24 | **Plan**: [plan.md](./plan.md)

解析本特性的全部未知与技术抉择。每项给出 Decision / Rationale / Alternatives。

---

## R1 — 「可推断」的实现机制：通用声明式解释器 vs DL 推理机

**Question**: 让分类「可推断」（owl:equivalentClass 充要条件 + datatype facet），由谁执行推断？

**现状事实**：代码勘察确认平台**当前不调用任何 DL 推理机**——`OntologyEngine.load()` 仅把 TTL 转 RDF/XML 喂给 Owlready2 World（`only_local`，禁联网），从不调用 `sync_reasoner_*`；分类完全由 `reasoning/rules/*.py` 的 Python 前向链规则（`ALL_RULES`）完成，经 `drug_classes` 列表状态线程实现 R-DC→R-ED 的链式触发。Owlready2 的 equivalentClass 分类需 HermiT/Pellet（**Java 运行时**）。

**Decision**: 采用**通用声明式判据/规则解释器**（Python，新增 `reasoning/interpreter.py`），由存于元数据/TTL 的声明式判据驱动；**不引入 JRE/HermiT/Pellet**。`owl:equivalentClass` + OWL2 datatype facet 公理仍写入权威 TTL，作为**人审、可移植、未来可被真正 DL 推理机直接消费**的真理载体，但运行期由解释器对一组**受限但充分的模式词汇**求值：

- `someValuesFrom`：药物经 `hasActiveIngredient` 关联的 API 持有某 `ToxicityProfile` 子类（如 `GenotoxicityProfile`）→ R-DC1。
- 关联个体的**类成员**：API 的 `hasOEBClassification ∈ {OEB4, OEB5}`（并集）→ R-DC2。
- **datatype facet 比较**：`SensitizationPotential.sensitizationLevel > 3`（`xsd:integer[xsd:minExclusive 3]`）→ R-DC3。
- **布尔 data hasValue**：`hasBetaLactamRing = true` → R-DC4。
- **外部对齐成员**：API 类经 ChEBI/ATC 对齐于抗肿瘤/激素/青霉素 → 肿瘤/激素/青霉素（决策见 R3）。
- 上述的 `intersectionOf` / `unionOf` 组合。

**Rationale**:
- 宪章 V（最小复杂度、不引入并行框架、最小依赖）：JRE+Pellet 是内网小并发系统的重运行时负担；规范 Assumptions 亦明列「推断由平台既有的本体推理能力承担（不引入并行推理框架）」。
- 与既有架构**同构**：E5 限制公理存于 TTL 但由 Python 引擎执行——本决策把分类判据同样「存于 TTL、由 Python 执行」，模式一致、心智负担低。
- **零回归可控**：解释器对每条判据求值的语义与原硬编码函数逐一对应（见 data-model 映射表），便于黄金基线比对。
- **不锁死未来**：TTL 中的 equivalentClass/facet 是标准 OWL2，未来若引入真正 DL 推理机可零改动消费——本决策是可逆的执行层选择，非知识表达的妥协。

**Alternatives considered**:
- **Owlready2 + Pellet/HermiT（Java）**：语义最纯、支持任意类表达式分类；但引入 JRE 重依赖、违「不引入并行框架/最小复杂度」、JVM 启动拖慢评估 p95、与既有 Python 引擎并存形成双推理栈。**Rejected**（保留为未来可选替换，TTL 公理已为其铺路）。
- **纯保留硬编码、仅抽参数到配置**：不满足 FR-003「本体原生、可版本化、声明式」与「规则即数据」的架构目标。**Rejected**。

---

## R2 — `owl:equivalentClass` 充要公理穿过 `surgical_merge` 的 round-trip 正确性

**Question**: 把判据投影为受管类（如 `slpra-drug:CytotoxicDrug`）的 `owl:equivalentClass [类表达式]` 后，如何既**逐字保留**该类既有的命名 IRI 外部对齐（ChEBI/ATC），又让判据公理**可重发不累积**？

**现状事实**：`MANAGED_PREDICATES` = {rdf:type, rdfs:label/comment/subClassOf/domain/range, owl:inverseOf, slpra:actor/target}；`owl:equivalentClass` **非受管** → `surgical_merge` 不剥离它。E5 限制能干净 round-trip 是因为它挂在**受管谓词** `rdfs:subClassOf` 下、且用**确定性 BNode**（`BNode(f"r{r.id}")`）。直接把 `owl:equivalentClass` 加进 `MANAGED_PREDICATES` 会误删该类的命名 IRI 外部对齐（记忆 [[external-alignment-must-use-nonmanaged-predicates]]：外部对齐正是靠 equivalentClass 非受管才存活）→ 回归。

**Decision**: 让 `surgical_merge` 对 `owl:equivalentClass` 做**对象形态感知**的细粒度处理（而非把整个谓词列入受管）：

1. 判据投影：受管类 `—owl:equivalentClass→ 确定性 BNode 类表达式`（BNode id 由判据 PK 派生，仿 E5）。
2. `surgical_merge` 对受管主语**仅剥离** `(s, owl:equivalentClass, o)` 中 **o 为 BNode** 者（workbench 拥有的类表达式），并**递归回收**该 BNode 子图（避免孤儿三元组累积）；对 **o 为命名 IRI** 者（手工/映射的外部对齐）**逐字保留**。
3. 决策规则 / 冲突策略投影为**全新命名主语**（`slpra:DecisionRule_*` / `slpra:ConflictPolicy_*`），其谓词为新增 `slpra:` 受管谓词集；因这些主语在手工 TTL 中不存在，无外部内容可误删，安全。
4. 外部对齐（命名 IRI 目标）若经 E6 映射投影，因是**幂等命名三元组**（rdflib 图集合语义自动去重），round-trip 不累积；其撤回保持既有「非受管、加性」语义（与现状一致）。

**Rationale**: 最小且精准地扩展 `surgical_merge`——区分「workbench 生成的 BNode 类表达式」与「指向命名外部 IRI 的对齐」是天然且稳健的判别式（二者对象类型不同）。确定性 BNode + 子图回收复用 E5 的成熟手法，保证写前 diff 干净、跨发布不漂移（宪章 II）。

**Alternatives considered**:
- **把 owl:equivalentClass 整体列入 MANAGED_PREDICATES**：实现最简，但剥离命名 IRI 外部对齐 → 直接违宪 II、毁坏既有对齐。**Rejected**。
- **判据不写 equivalentClass、只存元数据行**：失去 TTL 保真与可移植/未来 DL 可消费性（R1 的核心收益）。**Rejected**。
- **用一条 `slpra:hasSufficientCondition` 自定义谓词替代 equivalentClass**：偏离标准 OWL2 语义、未来 DL 推理机不识别。**Rejected**。

**验证**：Phase 1 `test_*_roundtrip`——构造含「命名 IRI 外部对齐 + BNode 判据公理 + 未建模注释」的类，连续两次 export→parse→export，断言三元组集合稳定且对齐/注释逐字存活。

---

## R3 — 激素/青霉素/肿瘤的 ChEBI/ATC 外部对齐取值（澄清决策 2）

**Question**: 「外部对齐物化」具体取哪些 ChEBI/ATC 条目作为充分条件判定基？

**Decision**: 以下为**候选权威条目**，物化为对齐目标（复用 `slpra-integration.ttl` 既有外部对齐机制 / E6 映射，非受管命名 IRI，安全穿过合并）：

| 风险类别 | ChEBI 角色/结构 | ATC | 充分条件判据（解释器求值） |
|---|---|---|---|
| 肿瘤药物 AntineoplasticDrug（新增类） | `CHEBI:35610` antineoplastic agent | `L01` antineoplastic agents | 药物的 API 经对齐属于 antineoplastic agent → AntineoplasticDrug |
| 激素类 HormonalDrug（升级为可推断） | `CHEBI:24621` hormone（候选；CFDI「激素类」实务多指性激素/皮质激素） | `G03` / `H02` 等 | API 经对齐属于 hormone → HormonalDrug |
| 青霉素类 PenicillinDrug（升级为可推断） | `CHEBI:17334` penicillin | `J01CA/J01CE/J01CF/J01CR`（penicillins） | API 经对齐属于 penicillin → PenicillinDrug |

**Rationale**: 与既有 IDMP/ChEBI 对齐同机制、同治理；ChEBI 角色（`has_role` / `is_a`）与 ATC 分级是制药域权威词表，溯源到 GMP 之外的国际标准，增强 FR-002 溯源强度。

**⚠ 本体保真前置约束（NON-NEGOTIABLE）**：上述本地名/编号为**候选**，实现前 MUST 对 ChEBI/ATC 权威源**逐字节核实**（沿用记忆 [[idmp-o-rebased-to-authoritative-namespaces]] 的字节级核实纪律：ChEBI 用 OLS/purl `obo/CHEBI_xxxx`，ATC 用 WHOCC）；未核实条目 MUST NOT 写入权威 TTL，并由 FR-014 一致性/健康度门禁拦截。核实与写入列为 tasks 显式步骤。

**T021 字节级核实结果（2026-06-24 完成，`alignment_verified=true`）**：

| 本地名/编号 | 权威源 | 核实标签（逐字节） | IRI（写入用） | 核实 |
|---|---|---|---|---|
| `CHEBI:35610` | EBI OLS4 (`ebi.ac.uk/ols4`, obo purl) | "antineoplastic agent"（同义：anticancer agent / antineoplastic / cytostatic） | `http://purl.obolibrary.org/obo/CHEBI_35610` | ✅ |
| `CHEBI:24621` | EBI OLS4 | "hormone" | `http://purl.obolibrary.org/obo/CHEBI_24621` | ✅ |
| `CHEBI:17334` | EBI OLS4 | "penicillin" | `http://purl.obolibrary.org/obo/CHEBI_17334` | ✅ |
| ATC `L01` | WHOCC（现托管于 `atcddd.fhi.no/atc_ddd_index`） | "ANTINEOPLASTIC AGENTS" | `https://atcddd.fhi.no/atc_ddd_index/?code=L01` | ✅ |
| ATC `G03` | WHOCC/FHI | "SEX HORMONES AND MODULATORS OF THE GENITAL SYSTEM" | `https://atcddd.fhi.no/atc_ddd_index/?code=G03` | ✅ |
| ATC `H02` | WHOCC/FHI | "CORTICOSTEROIDS FOR SYSTEMIC USE" | `https://atcddd.fhi.no/atc_ddd_index/?code=H02` | ✅ |
| ATC `J01C` | WHOCC/FHI | "BETA-LACTAM ANTIBACTERIALS, PENICILLINS" | `https://atcddd.fhi.no/atc_ddd_index/?code=J01C` | ✅ |

**核实备注**：
- WHOCC ATC/DDD index 已从 `whocc.no` 永久迁移至 `atcddd.fhi.no`（FHI 挪威公共卫生研究所）；旧 URL 返回 301，写入用 IRI 采用新主机的可解析 URL。
- 充分条件**推断信号以 ChEBI 为准**（API 个体类型化到 ChEBI 类 → `external_alignment` 命中）；ATC 作为命名 IRI 物化于对齐层，提供 FR-002 溯源/可追溯性（不参与运行期求值）。
- 仅 ChEBI 三条（35610/24621/17334）进入 T026 `VERIFIED_EXTERNAL_ALIGNMENTS` 注册表门禁（解释器实际消费的对齐目标）。

**Alternatives considered**:
- **复用未使用的布尔标记 `isHormonal`/`isPenicillinType`**：最小改动，但澄清决策 2 明确否决（语义浅、人工标注、无外部溯源）。**Rejected**（但可作为对齐缺失时的过渡兜底信号——记为 tasks 可选项，默认不启用以免稀释 OWA 语义）。
- **结构信号（青霉素=β-内酰胺母核）**：青霉素 ⊂ β-内酰胺，结构信号会过宽（头孢也是 β-内酰胺）；ATC/ChEBI 分级更精确。**Rejected** 作为主判据。

---

## R4 — 零回归基线方法（FR-012 / SC-004，澄清决策 3）

**Question**: 如何证明「除预期新增推断外，结论零回归」，并把 OWA「否→未知」记为预期改进？

**现状事实**：`AssessmentResult` 有 4 个既有测试调用方（`test_assess_bootstrap` / `test_qa_gate` / `test_qa_reject` / `test_audit_chain_workflow`）；`api/reasoning.py::_build_canonical_results` 依赖 `rules_fired`/`risk_level`/`requires_dedication`/`scenarios` 的精确形状。

**Decision**: 建**黄金基线（golden-master）回归**：
1. **重构前**用现行引擎对一组输入矩阵（覆盖 R-DC1~4、R-ED1~6、R-SC1~8、R-CP1~4 各触发/不触发分支 + 冲突场景）跑 `run_assessment`，快照 `{rules_fired[].rule_id, requires_dedication, risk_level, scenarios[].scenario_iri}` 为基线 fixture。
2. **重构后**用解释器重算，断言与基线**逐用例相等**，例外仅限两类**预先声明的允许差异**：(a) 三个新增可推断类别（激素/青霉素/肿瘤）本就该新点亮；(b) OWA 改进——某属性缺省时原引擎隐式判否（如 `sensitization_level` 默认 0 → R-DC3 不触发）现表现为「未知/不触发负断言」，差异方向 MUST 为「否→未知」而非「真→假」。
3. 既有 4 个调用方测试保持绿（对外形状不变是硬约束）。

**Rationale**: 黄金基线把「零回归」从主观口径变为可执行断言（宪章 IV）；显式「允许差异清单」把 OWA 改进与真回归区分开，杜绝 SC-006 的负类误断。

**Alternatives considered**:
- **仅靠既有 4 个测试**：覆盖不到全部规则分支，无法证明 SC-004。**Rejected**。
- **严格逐用例完全一致（含否→未知视为回归）**：被澄清决策 3 否决（会掩盖 OWA 改进）。**Rejected**。

---

## R5 — 前端「最小编辑入口」范围（澄清决策 1 的 UI 可编后果）

**Question**: 决策 1 要求判据/规则经 UI 可编，与原非目标「不改前端」如何收敛？

**Decision**: 前端**仅新增**判据/决策规则/冲突策略三类声明式制品的**列表 + 只读详情 + 受控编辑表单**最小入口（挂在既有 `ontology` 工作台下，复用 `lib/api.ts`+React Query 模式与既有乐观并发/角色门禁 UI）；**不改**整体布局/导航/评估流程页。富类表达式编辑（任意 intersection/union 嵌套）**不做可视化编辑器**——UI 仅暴露「受限模式词汇」的结构化表单（property + 比较算子/阈值 + 目标类/对齐），与 R1 的解释器词汇一一对应。

**Rationale**: 满足决策 1 的「经 UI 可编」同时遵 YAGNI（宪章 V），把前端增量压到最小、风险可控。后端 CRUD + 一致性门禁是主体；UI 是薄壳。

**Alternatives considered**:
- **完全不改前端、仅 API/DB 可编**：与决策 1「经 UI 可编」字面冲突。**Rejected**。
- **通用类表达式可视化编辑器**：超范围、高复杂度，违 YAGNI。**Rejected**（记为未来增强）。

---

## 研究结论汇总

| 决策 | 取定 |
|---|---|
| R1 推断机制 | 通用 Python 声明式解释器；equivalentClass/facet 仍写 TTL 作真理载体；不引入 JRE/Pellet |
| R2 round-trip | `surgical_merge` 对象形态感知：剥离/回收受管类的 BNode 类表达式，保留命名 IRI 对齐；规则/策略为全新受管主语 |
| R3 对齐取值 | ChEBI:35610/ATC L01（肿瘤）、CHEBI:24621/ATC G03·H02（激素）、CHEBI:17334/ATC J01C（青霉素）—**已逐字节核实（T021，2026-06-24，`alignment_verified=true`）** |
| R4 零回归 | 黄金基线 + 显式允许差异清单（新增三类 + OWA 否→未知） |
| R5 前端 | 三类制品的最小列表/详情/受控表单入口；不做通用类表达式编辑器 |

全部 NEEDS CLARIFICATION 已解析 → 进入 Phase 1。
