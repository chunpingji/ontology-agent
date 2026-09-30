# 映射与实体查询契约

2026-09-28 新增 Harness 本地消费契约：发现草案最多 8 项查询，每项以当前窗口 anchor 与逐值 Quote
说明类型假设和条件；首期只支持精确名称及字符串属性条件。服务端限制映射、类型、原文范围、
键组件和每项最多 5 条返回。反馈模型仅能引用实际候选别名，不输出来源 UUID/IRI。
最终发现结果一次登记，来源关联仅为未核对建议。查询能力元数据不读取 Mock 实例；执行才调用既有查询。
同一次查询用相同当前本体副本核对冻结目录并校验映射；定义变化、失败和无映射与完整未命中区分。
查询结果保存为当前步骤，不新增历史快照；已消费临时状态删除，必要上下文由既有模型输入和候选引用承载。

状态：基础查询接口已实现并在隔离环境验收；已于 2026-09-23 部署并重启。
上述 2026-09-28 Harness 本地消费接线另行完成工程与模型试验，未重启/部署主后端。
所有 HTTP 路径均相对现有 `/api`。
查询沿用现有认证及 Mock 数据访问范围；写映射沿用 `senior_analyst` 角色和 `expected_version`。
现有 Mock 是共享业务数据；本期不虚构逐租户/逐数据集权限表。读入口使用现有认证门禁，
后续文档调用另由 Harness 校验运行所属用户与原文权限，不因能读共享 Mock 就能访问他人文档。

## 1. 来源目录

`GET /entities/sources`

返回允许访问的数据集、字段定位项、已有映射及是否可查询。包括：

- `source_system`、`dataset`、显示标签、`source_kind="mock"`。
- `fields[]`：`source_path`、显示名、源数据类型、多值能力；不返回记录值或凭据。
- `mappings[]`：映射 ID、当前类型、有效版本、查询配置、`queryable` 及具体问题。
- 本体 `identity_properties`、完整 `identity_key_groups` 与来源 `lookup_key_groups` 分开返回。

注册在 `GET /entities/{iri:path}` 之前，防止静态路径被 IRI 路由吞掉。
响应外层为 `{sources: [...], initial_mappings: [...]}`。初始配置带 `available`，只供显式应用。
字段目录返回 `field_catalog_complete` 和来源 `issues`；数组字段目录受同一 5,000 行上限约束。
映射提供 `properties`（含 `datatype_iris`）和 `mapped_property_iris` 供编辑器选择。
`identity_key_groups` 每组保留 `property_iris`、`available`、`unavailable_property_iris`；
来源键在 `query_config.lookup_key_groups` 中。缺组件的本体键不会被删减为部分键。
目录只声明结构和当前可用性，不触发连接器同步或创建默认映射。

## 2. 既有映射接口扩展

现有 `/ontology/classes/{iri:path}/mappings` 与 `/ontology/mappings/{mid}` 增加：

- 类型 `mock_dataset`，限定 `source_system="builtin_mock"`，`target` 只能取来源目录数据集。
- `query_config`：`label_path` 必填；`entity_iri_path/class_path` 可空；
  `identifier_namespace` 在有查询键时必填；`lookup_key_groups` 可为空列表。
- 每组包含非空且无重复的 `property_iris` 和可为空的 `scope_property_iris`。
  范围属性与主键属性不重复，均必须有合法数据属性映射。
- 属性绑定 `source_path` 使用来源目录的 `col:`、`attr-iri:`、`attr-label:` 定位项。
  一个映射下同一本体属性只配置一个来源定位，避免隐式覆盖；多值由该定位的返回结构表达。

校验还必须拒绝：非法/停用类型和属性、定义域不兼容、未知顶层字段、冲突的类型字段、
不受支持的转换、残缺的键组引用。数组字段尚未出现是数据缺失，不把它当作已有取值。
已存在数据集的角色/人员语义不由表名推断。

完整性不足的草稿可保存，`queryable=false`；仅没有查询键但类/名称配置有效时可名称查询。
这里“草稿”是配置尚缺必需项，不允许把已填写的非法 IRI 或越界字段作为有效配置保存。
类映射和属性绑定更新沿用各自版本；查询返回的 `mapping_revision` 由两者共同确定。
普通查询不更新映射 health。旧 DB/API 候选抽取契约不增加 Mock 物化路径。

## 3. 批量只读查询

`POST /entities/query`

```json
{
  "queries": [
    {
      "query_id": "member-642",
      "class_iri": "https://ontology.pharma-gmp.cn/slpra/facility/ProductionArea",
      "include_subclasses": true,
      "mapping_ids": [],
      "name": null,
      "property_filters": [
        {
          "property_iri": "https://ontology.pharma-gmp.cn/slpra/facility/areaIdentifier",
          "value": "642",
          "datatype_iri": "http://www.w3.org/2001/XMLSchema#string"
        }
      ],
      "limit": 20
    }
  ]
}
```

- `queries` 长度 1–32；`query_id` 批内唯一；`limit` 1–50。
- `mapping_ids=[]` 表示当前用户可访问且类型相容的已配置 Mock 映射，不包括任意外部源。
- `include_subclasses` 默认 false。为 true 时按本体层级选择映射和有效记录类型，不包含父类替代。
- `name` 为 null 或 `{"value":"…","match":"exact|contains"}`。包含查询只是候选召回。
- 名称或至少一个属性条件必填；仅显式指定一个 `mapping_ids` 时允许无条件浏览该映射。
  属性条件只支持精确等值，同一属性不可重复，条件之间 AND。
- `offset` 默认为 0，须为非负整数；按稳定排序分页，每页仍最多 50 条。
  返回 `total`（来源完整读取时的匹配总数，否则为 null）和 `next_offset`（无后续已读取结果时为 null）。
  翻页读取当前来源，不保存快照；来源记录变化后刷新从首页重新读取。
- 数据型值与声明范围核对；字符串不隐式转数字。所有比较在声明转换后的值上进行，返回原始值。
- 组织/场地限制以对应合法属性条件表达；服务不接受自由填写的“已证明作用域”标记。
- 指定了不可访问/不存在映射或非法类、属性时拒绝该请求，不默默减少查询范围。
- 没有可用映射时给 `unresolved/no_queryable_mapping`，不能回报完整未命中。

类型匹配以记录的有效类型为准：等于请求类型，或在 include_subclasses=true 时为其后代。
配置了 `class_path` 的父类来源映射也应被纳入子类查询的候选来源，随后逐行核对真实类型；
否则会漏掉“设备来源映射到 Equipment，但某条记录实际是 ProcessEquipment”的合法命中。
未配置类型字段的父类记录不能被提升为请求子类。

合法且有权访问的映射因后续配置/本体变化变得不可用时，按来源返回 `invalid_mapping`，
保留其他来源结果。来源未绑定某个请求属性时返回 `unsupported_filter`，该项完整性为 false；
不能把该来源悄悄移出默认查询范围。字段不存在、值缺失与查询值不相等必须分别记录。

响应示意；UUID 和版本用占位说明，实际返回真实值：

```json
{
  "results": [
    {
      "query_id": "member-642",
      "outcome": "matches",
      "complete": true,
      "truncated": false,
      "sources": [
        {"mapping_id": "<uuid>", "status": "complete", "issues": []}
      ],
      "candidates": [
        {
          "record_ref": {
            "source_system": "builtin_mock",
            "dataset": "production_areas",
            "record_id": "<source-row-uuid>"
          },
          "record_version": "<source-updated-at>",
          "mapping_id": "<uuid>",
          "mapping_revision": "<effective-config-digest>",
          "source_kind": "mock",
          "source_entity_iri": "http://slpra.org/facts#production-area-642",
          "class_iri": "https://ontology.pharma-gmp.cn/slpra/facility/ProductionArea",
          "label": "642车间",
          "properties": [
            {
              "property_iri": "https://ontology.pharma-gmp.cn/slpra/facility/areaIdentifier",
              "values": [
                {
                  "value": "642",
                  "datatype_iri": "http://www.w3.org/2001/XMLSchema#string",
                  "raw_value": "642",
                  "source_path": "col:code"
                }
              ]
            }
          ],
          "matches": [
            {"kind": "property_exact", "property_iri": "https://ontology.pharma-gmp.cn/slpra/facility/areaIdentifier"}
          ],
          "matched_lookup_groups": [0],
          "identifier_namespace": "urn:mock:production_areas",
          "business_scope_status": "unspecified",
          "identity_status": "not_checked",
          "issues": []
        }
      ],
      "issues": []
    }
  ]
}
```

`record_version` 使用来源更新时间；来源记录内容在一个批次内仅读取一次。
`source_path` 为数组选择时，值额外返回本次精确数组位置；索引仅用于本次来源定位，不作永久身份。
`matched_lookup_groups` 只在该组全部组件有单一可用值、全部条件被提供并匹配时返回组下标。
它既不证明数据集中唯一，也不表示 OWL 身份成立；重复编号记录仍全部返回。
业务范围只有在对应属性值存在且查询提供了全部要求条件时为 `provided`，否则 `unspecified`；
`provided` 仍不证明文档已经绑定到该范围。

多值非键属性采用“至少一个值精确满足该属性条件”；多值键只作为候选匹配，不记完整键组命中。
未映射属性不作为模型可用本体事实返回。请求中其他合法已映射字段可用于筛选。

## 4. 空结果、限制和错误

| 情况 | 语义 |
|---|---|
| 有候选，全部来源和结果读取完成 | `outcome=matches, complete=true` |
| 有候选，但来源失败、扫描超限或结果截断 | `outcome=matches, complete=false`；标注来源及原因 |
| 无候选且全部可查询来源完整查完 | `outcome=no_match, complete=true`；仅对请求范围成立 |
| 无候选且未配置、失败或扫描未完成 | `outcome=unresolved, complete=false` |

每数据集每批最多处理 5,000 行，额外探测第 5,001 行以判断 `scan_limit`；不能把未扫描部分排除。
当前页未包含全部已读取候选时 `truncated=true, complete=false`，返回 `result_limit`。
`complete` 仍表示当前响应覆盖全部查询结果，不能用末页完整代替整体完整。
`outcome` 按本次扫描的全部匹配判断；超出末页时可为 `matches` 且当前页为空。
`total=null` 独立表示来源读取不完整；分页本身不使已核实的总数变为未知。
所有结果保持请求顺序；候选按来源、记录 UUID、映射 ID 确定排序，不按偶然数据库顺序裁决身份。

HTTP：未认证 401；无权访问 403；非法参数/映射 422；映射写入版本冲突 409。
合法批查询中的来源可用性故障使用 200 和逐项不完整结果；服务整体故障按现有错误处理返回 5xx。
故障诊断不暴露凭据、SQL 或内部堆栈。

## 5. 文档调用边界（后续消费约定）

查询 API 不接收或信任原文证据权限。Harness 在本地调用前校验当前用户、运行和窗口引用，
将有效本体条件传给同一个查询服务；调用后仅登记这次真实返回的候选。
不存在“直接让模型传数据库表名”或“命中就合并”的接口。
查询 API 可独立上线；后续工具接线不作为本期接口已实现的默认结论。

## 6. 已映射来源卡片与抽屉

实体管理按来源数据集呈现卡片，外部来源只展示已有映射的项目；卡片显示类型与可查询状态。
页面统一进入此卡片列表，不再并列“已保存实体/已映射来源”视图切换。
“已保存实体”作为本地实体库卡片纳入同一列表，使用现有实体列表与详情 API，
在 Drawer 中保留搜索、模块筛选、分页和详情；不创建 Mock 映射或复制实体。
本地卡片独立于外部来源目录加载，目录读取失败时仍可访问；打开前不读取实体列表。
点击卡片打开右侧 Drawer，自动读取首个可查询类型映射的实体（包含子类）；
多类型来源通过抽屉内的类型选择切换映射。每次请求仅指定该来源的一个映射。
抽屉支持名称、属性筛选、分页、实体详情和身份指引；关闭、切换类型或重新筛选时
取消旧请求并清除旧选择。刷新卡片页只读取目录，不自动查询实体或启动模型任务。
配置无效、读取失败、完整空结果及扫描不完整分别显示，不把异常解释为没有实体。
