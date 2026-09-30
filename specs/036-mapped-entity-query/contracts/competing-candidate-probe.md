# 竞争指称与关系分阶段实验契约

日期：2026-09-28。仅用于 H02d 隔离实验，非在线 API 或公共图协议。

1. 输入为单句原文 IR、当前窗口引用、冻结的生产区域/编号及临床备样生产计划/生产区域关系定义。
   类型是本轮显式任务范围，不计为模型类型发现结果。首次键召回来自隔离 Mock 的真实映射查询，
   只按原文字面召回，不将命中数作为对象数。
2. 指称响应 `Mentions` 提供提及组 `area_anchor`、带 ID 和逐字 quote 的 `expressions`、
   `partitions` 及可空的 `plan_anchor`。每个 partition 的 `members` 包含显式成员对象，
   每个成员有 `id` 和属于自己的 `expression_ids`；不同 partition 互为竞争解释。
   不允许同一 partition 内将重叠表达分配给不同成员，也不得遗漏同组最小编号片段。
   本契约为首轮二维数组混淆后的当前修订；首轮原始协议与结果保留在其独立目录。
3. 程序针对模型提出的所有编号表达发起完整字符串查询，逐项保留命中、未命中和读取完整性。
   查询服务不拆分原文，未命中不得删除原文成员。
4. 对齐响应 `Alignment` 选择一个 partition 或保留 null，并输出绑定该 partition 的关系组。
   `participation=options/all/single/unknown/none`；`selection=exactly_one/unspecified`；
   `timing=parallel/sequential/unspecified`；`polarity=positive/negative/uncertain`。
   有确定关系必须有原文计划端点和合法成员引用；并行/先后只适用于多个成员共同参与。
   `member_ids` 引用输入中显式给出的成员 ID，不输出数字索引或编号表达 ID。
   无计划主体时，动态 Schema 仅允许 `participation=none`、空关系成员列表、无选择和时间结论；
   指称解释选择与编号核验仍继续，不因没有生产关系取消对象识别。
5. 独立证据响应对 I（身份解释与类型）、A（逐主体编号）、P（计划）、R（关系组）、T（时间）
   分别给出 accepted/unresolved/rejected 及原文引用，不能重写候选。
   编号只依赖 I 与自身证据，不依赖 P/R；R 依赖两端确认，T 单独采信。
6. 最终只物化被采信的对象分组。options 保留为一个选项组，不展开成两条无条件肯定边；
   all/single 可投影为计划关系边，时间未获证实不能补成并行。投影不代表业务事实提交。
   所有来源关联保持 `not_checked`。不引入运行分支或历史回退。

逐字引用、成员归属和结构一致性由程序验证；语义正确性另按末端开发回归参考评分。
原始模型回答、查询产物、未决、应用拒绝和成本全部保存；参考值不进入识别输入。
