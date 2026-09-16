# CMCReport：主体约束与证据校验收紧实测

**已实施收紧方案并完成项目 Qwen 的 16 次真实调用；方案仍未通过整体语义验收。** 全部响应通过指定 JSON Schema。修正检查器后，4/8 个片段满足冻结的局部必留清单；只有 N/A 片段同时没有拒绝项。PDE 两行归属、设备编号及完整质量标准得到保留，但仍有主体遗漏、类型误判、状态混淆和引用角色错误。

本轮依据[前轮完整报告与样例](../cmc-qwen-schema-probe-20260915/README.md)实施[收紧计划](plan.md)。汇总数据见 [summary.json](summary.json)。这里的“保留候选”只表示通过当前机械检查，不等于已证明正确的业务事实。

## 输入、预算与实际范围

| 项目 | 本次实测 |
|---|---|
| 文档 | `upload-23c872fb-3ab1-41de-a705-dd4b162dfa09` |
| 根类型 | `https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport` |
| 原件 | `HRS-1597原料临床备样生产信息表_--DP-C-PI-S-X2419_2601_04-原 - PDE-冲突.docx` |
| SHA-256 | `e94822808601e73f4ba4c715c664812d4a01e42f0e4e84a6b08a3653b915daf7` |
| 输入 | 复用前轮冻结 Word、完整 IR、329 张本体卡片；8 个片段、86 个不同证据单元 |
| 覆盖限制 | 原文 IR 共 447 单元；本轮不是全文或跨文档质量评测 |
| 强模型 | 项目端点实测 `Qwen3.6-35B-A3B`，`owned_by=llamacpp` |
| 声明 revision | `0b21525e972670ed59e1812e170b27c26355381f0656ecc4e25617ece7dac58b`，未重新验算整份模型权重 |
| 调用时间 | 2026-09-16 UTC 00:36:27—00:45:33，约 9 分 7 秒 |
| 调用设置 | temperature=0、thinking 关闭、原生 json_schema、max_attempts=1、无回退/重试、输出上限 4,096 tokens |
| 成本 | 16 次调用，输入 66,237 / 输出 18,689 tokens；HTTP 耗时之和 545.192 秒 |

16 次调用全部 `finish_reason=stop`，没有截断或技术失败。模型为共享服务，耗时不能视为独占部署性能。调用使用项目客户端及共享调度，新增调用账本；不提交业务事实、不修改本体或线上识别流程。结束后只读查询确认原作业 `e8b24ffa-9fbc-40e1-987f-9585d9b42676` 仍为 `paused`。

## 实际实施的约束

执行器为 [schema_card_tightened.py](../../../backend/app/evaluation/schema_card_tightened.py)，物理证据检查为 [schema_card_evidence.py](../../../backend/app/evaluation/schema_card_evidence.py)。

1. **先发现主体，再补齐主体属性菜单。** 文档根由代码固定为 `document`；发现阶段只允许候选类及原文锚点。路由使用 CMCReport 两跳范围、根关系的一般端点类型和原文/本体标签，本次提供 20 个候选类。参考答案和旧模型输出不进入提示。该词法路由仍有局限：工艺设备获得一般 Equipment 类型，未召回 Reactor。
2. **按主体编译返回结构。** 顶层只能填写已分配的主体 ID；每个主体拥有自己的属性字段和关系字段。关系目标按该谓词的 range 单独枚举，没有目标时不生成该关系字段。示例：[产品 Schema](product_properties.schema.json)、[PDE Schema](pde_conflict.schema.json)、[PDE 主体卡片](pde_conflict.subject-cards.json)。
3. **分别核对值、字段、单位、条件和图例引用。** 短引用 `u0…` 映射回真实 evidence_id；引文必须逐字且能唯一定位。检测数值截断、借错单位列、同段无关单位、单位分母截断和布尔字段错配；原值交给项目已有保守规范化器处理。
4. **按真实表格结构限制归属。** grid 与单元 span 的交集确定逻辑行列；PDE 主体使用各自行的试验名。共享 API 合并单元可以覆盖两行，试验值和来源不能因此跨行。首行表头是保守候选，未把解析器默认首行直接当作语义证明。
5. **状态与事实分别保存。** N/A、未知符号及缺单位数值不直接生成事实。JSON 契约、拒绝项、保留候选、观察和局部验收分别计数，失败调用也保留。

[冻结验收参考](acceptance.frozen.json)包含 18 条必需属性、3 条关系、8 条观察，以及禁止谓词、同主体分组和 PDE 两个独立试验锚点要求。关系正例必须为 affirmed，空输出无法通过。参考级别为 assistant silver，不是业务专家金标；模型调用后没有调整参考。

## 首次执行与零调用复核

首次执行完整保留在 [result.initial.json](result.initial.json)。真实样例揭示了三处检查器问题，随后修复，并用**同一批响应、同一份参考**离线复核；未追加模型调用、未改写原响应或首次结果。

| 检查器问题 | 修复及影响 |
|---|---|
| 普通句可以被标成 not_applicable/unknown | 要求状态证据；无单位未知数值同时检查原文后缀与同列表头。拒绝了 11 条原来保留的不当观察。 |
| 为处理“是否…否”而从所有值引文删除字段引文 | 仅对“是/否”作该处理；恢复正确设备编号、设备名称和完整质量标准 3 条候选。 |
| `(mg/天)` 整段单位引文不被归一化器接受 | 仅剥除成对展示括号，仍保留原引用并检查单位归属/量纲；恢复两个 PDE 值，不允许 mg/kg/day 变成 mg/day。 |

最终机械复核结果见 [result.rechecked.json](result.rechecked.json)：

| 指标 | 首次检查器 | 修正后离线复核 |
|---|---:|---:|
| 原生 JSON 合约通过 | 16/16 | 同一批响应，无新调用 |
| 保留属性/关系候选 | 39 | 44 |
| 保留观察 | 27 | 16 |
| 拒绝项（主体、观察或属性/关系） | 41 | 47 |
| 局部必留清单全部通过的片段 | 2/8 | 4/8 |
| 清单通过且没有任何拒绝项 | 1/8 | 1/8 |

从 2/8 到 4/8 是检查器修正的结果，不能说成 Qwen 再次生成后的提升。与前轮的 2/11 卡片/引用检查也不是同一验收口径，不直接比较通过率。

## 八个片段的结果与返回样例

以下候选数、观察数和清单统计均为修正后的离线复核。每个样例包含实际两阶段返回、短引用到原文的映射和复核结果。

| 片段与样例 | 候选 / 观察 / 拒绝 | 清单 | 主要结论 |
|---|---:|---:|---|
| [简介](introduction.sample.json) | 3 / 0 / 14 | 2/4，未通过 | 计划关系方向正确；遗漏产品主体。日期返回 `2026年06月`，原文为 `2026年6月`，含义相同但原值协议不通过。 |
| [产品属性](product_properties.sample.json) | 7 / 0 / 5 | 4/4，通过 | 6 个属性和报告到产品关系均有支持；否已归一为 JSON false，溶剂与溶解程度保留。额外状态观察仍有错误，整响应不通过。 |
| [工艺设备](process_equipment.sample.json) | 4 / 0 / 14 | 5/5，通过 | 设备编号、名称、步骤使用设备得到保留，未把投料当产出；温度引用和步骤序号原值仍被拒，反应条件只留下时间，信息不完整。 |
| [质量与条件](quality_condition.sample.json) | 1 / 1 / 4 | 4/5，未通过 | 合法 Purification 继承的 inProcessControl 完整保留三项限度及重结晶条件；独立条件观察伪造辅助引文被拒，另保留的 HPLC conditional 状态不成立。 |
| [存放条件](storage.sample.json) | 4 / 4 / 1 | 4/6，未通过 | 包装完整、温度上限正确；`有效期24M` 与预期 `24M` 语义等价但严格值格式不符，导致同主体组检查也未通过。关系条件和观察角色另有错误。 |
| [残留物 N/A](residue_missing.sample.json) | 0 / 1 / 0 | 4/4，通过 | 无实体、无残留事实，仅保留 N/A 状态；没有推成“无残留”。 |
| [PDE 两试验](pde_conflict.sample.json) | 24 / 9 / 5 | 15/15，通过 | 大鼠100与犬7.5分别绑定正确试验、来源和同列单位；两个NOAEL值因缺单位保持未知。额外状态/条件角色仍不全正确。 |
| [毒性状态](toxicity_unknown.sample.json) | 1 / 1 / 4 | 9/12，未通过 | ×正确保留为否定；三个—误标不适用被拒，未知信息未完成保留。产品还被错误选成 HighSensitizingDrug。 |

### PDE 修复的有效范围

| 报告值 | 大鼠主体 s1 / 第1行 | 犬主体 s2 / 第2行 |
|---|---|---|
| 试验 | 大鼠14天亚急毒试验（试验1） | 犬14天亚急毒 |
| PDE（mg/day） | 100 | 7.5 |
| 来源 | 19191-25030-NG | 19191-25031-NG |
| F1 | 5 | 2 |
| NOAEL 原值 | 200，单位待确认 | 30，单位待确认 |

两个主体的 API 使用原文明确合并的单元，其他值各守本行。24 条保留属性的值和行归属均得到原文复核支持；它们是报告中的值，不是本轮重新计算或裁决的产品唯一 PDE。两个报告到试验的关系提议仅引用共享 API 名，无法区分目标试验，仍因引用不充分被拒。

### 为什么仍不能整体验收

**正式类型本身也需要证据。** 毒性片段的 HighSensitizingDrug 是本体中的合法类型，且位于 describes 的 range；但原文致敏性为“—”，不能证明高致敏。机械检查保留的这条关系因此仍是错误候选。仅验证 IRI、domain/range 和逐字产品名不够。

**辅助引用逐字，不等于角色正确。** 模型将表头放进 condition、将数字本身放进 legend，将 HPLC 检测动作标成 conditional。存放条件中 packagingType/shelfLife 已成功映射，却还存在声称 unmapped 的观察。PDE 仍把固定 F2 和 OEB 数值描述成条件或未知。当前检查器没有完整证明这些语义，人工复核也未把它们算成正确状态。

**更严格的引用也会损失正确内容。** 简介批量下限 3.8 有支持，但当前标量检查拒绝从区间取下限；设备温度引用中的“℃”重复而未通过唯一定位；步骤一未转换为整数；日期前导零和有效期标签造成契约失败。这些应归为原值表达、引用定位或检查器能力限制，不能全部归咎于模型理解错误。

下一轮应优先验证：主体类型的原文依据与发现覆盖、条件/图例/未映射的角色核验，以及带局部位置的数值和单位引用。保留已有固定主体 Schema、完整属性菜单、行归属和单位检查；本轮不继续堆叠凭关键词裁决所有语义的规则，也不将剩余候选自动提交。

## 工程验证与复现

实际执行 112 项定向测试通过：旧 probe 12 项、证据检查 36 项、新主体约束/复核 64 项。定向 Ruff 通过。出现 4 条既有依赖弃用/字段命名警告；未执行全库测试、部署或全文质量评测。

```bash
# 在 backend/，使用项目现有环境
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_schema_card_probe.py \
  tests/test_extraction/test_schema_card_evidence.py \
  tests/test_extraction/test_schema_card_tightened.py

.venv/bin/ruff check --no-cache \
  app/evaluation/schema_card_tightened.py app/evaluation/schema_card_evidence.py \
  tests/test_extraction/test_schema_card_tightened.py \
  tests/test_extraction/test_schema_card_evidence.py

# 在配置了项目Qwen和调度数据库的后端环境；输出目录必须不存在
python -m app.evaluation.schema_card_tightened \
  --baseline /app/data/evaluations/schema-card-qwen-cmc-20260915-02 \
  --output /app/data/evaluations/schema-card-qwen-cmc-tightened-new

# 在 backend/，对保存响应做零模型调用复核；另建输出目录
.venv/bin/python -m app.evaluation.schema_card_tightened \
  --recheck-from ../output/schema-card-qwen-cmc-tightened-20260916/run \
  --output ../output/schema-card-qwen-cmc-tightened-20260916/rechecked-new
```

原始制品位于工作区 `output/schema-card-qwen-cmc-tightened-20260916/run/`，包括 Word、IR、完整卡片、参考、冻结执行代码、逐次请求/响应和用量；`rechecked/` 保存修改后的检查代码和零调用复核结果。首次容器目录为 `/app/data/evaluations/schema-card-qwen-cmc-tightened-20260916-01`。这些实验目录被 Git 忽略，本文目录保留可审阅结果及八个返回样例。首次结果应使用 run 内冻结代码解释，当前脚本包含本报告明确列出的三项后续检查器修正。

JSON Schema 子集使用方式依据已查询的 Context7 llama.cpp grammar 文档；最终兼容性以本次项目端点的 16 次实际返回为证据。语法约束不能承担主体类型与条件含义的语义证明。
