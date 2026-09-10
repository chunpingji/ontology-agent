# 演示接口契约

- GET `/api/reports/batch-demo?document_iri=...`：按服务端登记的类型、文件名和原件
  SHA-256 匹配。其他文档返回 `available:false`；匹配对象返回图谱、图谱哈希、
  模板及哈希、逐项校验、最新本人报告。失败不得退回模型识别。
- POST `/api/reports/batch-demo?document_iri=...`：分析员以上角色；请求必须包含
  `graph_hash`、`template_hash`、`request_key`。服务端重新核验源版本与契约，
  返回真实已保存报告 ID、job ID、阶段完成明细及可预览内容。版本变化 409，
  必填缺失 422。相同 actor/job/request_key 重放已保存结果。
- 历史查看和 DOCX 下载复用 extraction reports 接口，报告类型 `batch_record_demo`；
  报告中心显示“批记录报告（演示）”，下载文件名标注批记录演示草稿。
- 图谱采用现有 Relationship 树，增加稳定实体 ID、实体及操作行来源；模板以实际谓词路径
  选择实体、校验属性，并消费图谱里的工艺操作行，禁止另造第二份生成数据。

## 现有模板特化

- TemplateV2 增加可选 `demo_profile`：固定 fixture_id、contract_id 和共享
  document_iri。普通模板不序列化此字段，保持原哈希；演示契约继续单独版本化，
  模板不复制图谱。模板列表/详情返回该标记与专用契约的章节数。
- GET `/api/reports/batch-demo/templates/{template_id}`：仅接受指定模板 ID，
  校验持久化 profile 和默认源作业，与按 document_iri 获取的数据及哈希一致。
  只读且私有缓存；源错配、profile 缺失或无效明确失败。
- 特化工具只处理该 ID 的草稿：校验预期 schema hash、原件 hash、源登记；
  更新名称、专用 profile、默认源关联，清除普通风险计算绑定，保存前后审计。
  重复执行相同配置无额外写入。不会修改源作业、源文件、历史模板或已生成报告。
- 通用模板变更/换源入口拒绝演示 profile；通用报告编译/生成也拒绝，
  UI 仅使用已有静态批记录 POST。
- template.layout 保存参考模板 ID、schema/sample/content 哈希和 renderer_version；
  纳入 template_hash。OutputNode.provenance_refs 的 template_layout 项携带页面、
  字号、表格列宽、合并跨度及原型坐标。历史无该元数据的 AST 使用原预览。
- 参考样例缺失、损坏或原型结构不兼容返回 409；不得回退为通用样式或启动抽取。

- 打印版使用空字符串表示手工填写栏，保留字段标签、边框、行高及未勾选框。
  渲染版本纳入 template_hash；旧版结果不会被识别为当前打印版，历史文件保持原样。
- 操作参数使用版本化的静态映射，绑定源操作序号与参考样例的表/行；参数名称与记录
  格式必须匹配登记的样例字段。缺失或错位明确拒绝，映射哈希纳入 template_hash。
- 操作表 AST 每个物理参数行包含五列；provenance_refs 记录原型表/行/单元格、
  row_span 和 vmerge。DOCX 使用 w:vMerge，HTML 使用 rowSpan 并省略延续单元格。
  实际数值和签署仍为空；记录栏允许单位、时间分隔符、设备检查引用及未勾选框。
- A14 操作记录按用户后续要求使用参考样例第 10–26 张表（从 0 编号），保留全部内容
  和格式，包括样例自身单个计算项的跨列合并。此范围覆盖先前的源操作重组/计算拆列规则。
  AST 标注 reference_template 来源与表坐标，保存每段/每个 run 的文字和字体，历史预览
  不重新读取当前模板。正文其他部分继续按共享图谱填充。
- 工序页眉使用样例对应分节的完整页眉；附加来源说明无模板工序页眉，不伪造工序名称。
  页眉中已有的车间信息是模板内容，不属于需要留空的实测或签署字段。
