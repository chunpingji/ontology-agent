# 精确核验输出结构：离线证明、最小修复与真实探针

当前安装的 llama.cpp 可以在同一 JSON 外形内约束每个冻结 target、对应 content_hash 和全部
必需 facets。已实施 provider Schema 收紧；本地 `validate_verification_targets`、证据核验、
否定/未决判定及实体登记门保持独立。工程验证及一次真实 provider 结构验收通过；该次回答
仍有错误语义支持，因此本记录不代表完整质量或提速验收。

## 依据与实际支持范围

- 输入为真实保存的核验请求：运行 `8b97fa57-e2a2-477e-b836-1e8094813dc0`，调用
  `f74b6b251315888e0adedd76bda52e8ffd5184e4fff13a69fb794f24eb0ebc2c`，6 个 target、39 个 facets。
  用已存结果恢复规范 ID 后，全部目标通过 `VerificationTargetSpec` 严格反序列化；原文和
  预期语义结论未修改。当前生产 `compile_stage_schema` 重新编译输出。
- 离线 C++ 工具链接运行中服务同一 `libllama-common.so.0.0.9853` 与
  `libllama-server-impl.so`；源码 HEAD 为 `7af4279f4579094cbe121cccb3c28357396e55d0`，
  带已安装 Responses 修补。库哈希和逐项结果见[证明数据](exact-verification-schema-proof.json)。
  工具只执行 Schema/Responses 转换和 GBNF 校验，没有调用模型、读取权重或改变运行状态。
- Context7 查询了官方 `/ggml-org/llama.cpp` 的数组支持说明；它反映当前主干。此环境的结论以
  已安装 b9853 转换库及 `common/json-schema-to-grammar.cpp` 第 941 行后的实现为准。

| Schema 形态 | 当前转换器实测 |
|---|---|
| `prefixItems` 加相同 minItems/maxItems，无 items | 固定位置、固定长度；接受 `[a,b]`，拒绝 `[a,a]` |
| 再加 `items:false` | 转换失败：`Unrecognized schema: false` |
| 保留旧的普通 `items` | items 优先，prefixItems 被遮蔽；错误的 `[a,a]` 仍被接受 |
| 对 target_id、content_hash、facet name 使用 const | 正常转换，并限制为指定字面值 |

## 实现和边界

`compile_stage_schema` 按本轮冻结目标顺序生成 verifications 的固定 tuple；每项 target_id /
content_hash 固定为对应值，facets 固定为该目标 required_facets 的完整序列。重复 facet
定义通过共享 `$defs` 复用。没有要求 supported，也没有因 unsupported 省略其他 facet。
空目标仍只允许空数组；不增加领域规则、API 或协议版本。

`compile_batch_stage_schema` 继续共享声明定义，但核验 members 改为本阶段成员顺序的固定
tuple：每个 task_id 固定，result 只包含该成员自己的冻结目标。不能只修改单目标编译器并
沿用旧 BatchMemberResult，否则会要求每个成员返回整批目标。阶段拆分只编译所传子集，
冷恢复仍从已保存的发现和当前阶段成员重建，已完成成员不因此重做。

`compact_answer_schema` 保留 prefixItems 和 const，并沿引用保留必需定义。
`ModelReferenceProjection` 对 target_id、content_hash、task_id 的 const 同样执行短引用
映射；离线往返及实际测试 transport 均通过。已安装 Responses 转换函数将 text.format.schema
完整传到 response_format.json_schema.schema；本地客户端没有再次裁剪 Schema。

固定顺序是 provider 的序列化约束，本地目标/facet 集合校验仍接受完整且正确匹配的其他顺序。
支持证据是否逐字存在、归属是否正确及结论是否成立继续由原校验链处理；语法无法保证这些语义，
也无法防止输出预算不足、拒绝或传输失败。

## 验证

旧 Schema 接受的漏 target、重复 target、ID/hash 错配、unsupported 只返回 type、重复或错误
facet，现均被正式编译结果与 compact 后结果的 JSON Schema/GBNF 拒绝。完整 unsupported
答案可以通过。另验证 2/4 target 子集、各成员独立目标、成员缺失/重复/错换以及 const 短引用。
多成员离线语法用真实目标的合成分组验证契约，不冒充历史批调用。

138 项相关 pytest 通过，其中新增精确 Schema 测试 16 项，覆盖真实 adapter 的拆批和冷恢复；
定向 Ruff 通过。原有 record 组成反例曾因只有一个 value 组件、没有 subject 而在发现冻结前
被拒，恢复旧 Schema 仍复现相同失败。测试现补足合法 subject 组件，核验只覆盖 subject 而
漏 value，保留 `record_composition_source_coverage_missing` 断言，并断言发现冻结已通过。
没有放宽生产门。JSON Schema 验证沿用既有可选 jsonschema 测试方式；本次本地虚拟环境已安装
并全部实跑，无跳过；未修改运行依赖或锁文件。

正式编译的 compact Schema 在这 6 个 target 样本中由旧版 3,799 字节增至 11,979 字节，
规范 ID 下 GBNF 从 6,108 增至 32,060 字节。现有请求容量计数会纳入实际 Schema；减少结构纠错
是否抵消额外输入和语法开销，需要对照验证；下述单次真实探针不足以证明提速。

离线脚本与生成物位于 `/tmp/ontology-target-main-20260921/schema-tuple-probe/`。

## 真实 provider 结构探针（2026-09-21）

探针 `cmc-verification-structure-probe-20260921` 使用主运行
`20bf2d25-98f3-4efe-99f0-1e5dbbae5d05` 中 lineage
`07b11ded29ac54db00f87e1e01961fc903e69e5338588079f984206bf7048029` 的已付费核验请求。
[重建证明](verification-structure-probe-reconstruction-proof.json)核对了原 request hash、card、
context、evidence 及 verification input 的一致性。随后只替换 `text.format.schema`，
输入、instructions、模型、工具及预算保留原值，没有加载金标或预期答案。
[发送前记录](verification-structure-probe-preflight.json)保留这些输入身份和改动范围。

实际通过既有 Responses 路径调用 `Qwen3.6-35B-A3B` 一次，输出上限 16,384 token，
`reasoning.effort=none`、`enable_thinking=false`。没有自动重试或结构纠正请求。
返回 `completed`，2 个冻结 target、6 个必需 facets 全部返回，target/hash/facet 精确对应。
生产解析器与离线 Draft 2020-12 Schema 校验均通过，见
[原始结果](verification-structure-probe-result.json)和
[完成后的结构校验](verification-structure-probe-offline-validation.json)。
原始结果中 `jsonschema_validation=pending_offline_validation` 是收到回答时的状态，保持原样；
完成后的校验记录明确为通过，且其 result SHA256 与归档结果文件一致。

| 成本与执行项 | 实测值 |
|---|---|
| 实际模型调用 / 自动重试 | 1 / 0 |
| 探针整体墙钟 | 21.214 秒 |
| 共享账本排队 / 请求 / 总耗时 | 0.167 / 20.388 / 20.556 秒 |
| 输入 / 输出 / 总 token | 12,762 / 1,002 / 13,764 |
| 缓存输入 token | 12,246 |
| 共享账本请求开始 / 完成（UTC） | 06:03:44.490751 / 06:04:04.878738 |

这些数字由[共享调用账本的只读核对结果](verification-structure-probe-shared-ledger-readonly.json)
与 provider usage 对应。探针未写入文档分析工作状态或提交实体，但真实调用写入了共享调度与
用量账本，不能把整个探针称为只读。缓存命中较高，且没有同条件对照，不能由单次耗时宣称提速。

该结果只验收输出结构。模型对 Centrifuge 和 CleaningEquipment 两个目标的三个 facets
均返回 supported；其中将“甩滤”操作/用途支持为 CleaningEquipment 仍然错误。其 type 理由
也提及缺少清洗方面的信息，与 supported 不一致。固定结构没有验证类型、指称或主体角色的
语义正确性，不能将本次 2/2 target、6/6 facet 的结构完成解释为实体全部正确或允许登记。
这也不构成全文实体、关系和属性完整度验收。
