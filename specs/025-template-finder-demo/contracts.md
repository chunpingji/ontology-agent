# Finder 展示接口

根 `/api/ast-templates/{template_id}/sources/{source_job_id}`。读为登录用户，写为 senior_analyst；源由服务端核验，内部 job 不可作为公共源。

- GET `/finder`：当前状态，无运行 not_started，不派发。
- POST `/finder`：`{request_key, expected_execution_id}`，首次 expected null；同请求重放，键/输入/expected 冲突 409，创建 202。
- GET `/finder/graph?execution_id=...`：当前匹配结果；未完成 409。
- GET `/finder/source?execution_id=...`：同次正文/IR/目录；无 execution 时仅结构预览。
- GET `/api/ast-templates/recognition-context?document_iri=...&template_id=...`：只读模板/源/模式；无唯一登记不自动选 Finder。

指定 `HRS-1597原料临床备样生产信息表_--脱敏-原 - PDE-冲突.docx` 的上下文返回
`selection_locked: true`，`templates` 仅含“风险评估文档V1版”同根类最新修订，
`selected` 为该项，`selection_required: false`。显式旧模板参数仍解析到该最新项。
选项的 `version` 是实际模板版本；`schema_version` 保持结构格式含义。
其他文档 `selection_locked: false`，保持既有候选与显式选择规则。
目标模板不存在时返回 `404 TEMPLATE_NOT_FOUND`，不选择其他模板。

响应包含 mode、template_id、source_job_id、execution_id、status、stage、input、has_result、stale、error、counts。公开 execution_id 不等于内部令牌。

树形关系及属性增加 source `{kind: document|external|computed, label, anchors, raw_value?}`；anchors 沿用 EvidenceAnchor，正文/图谱 execution 和文档/解析身份相同。不提供新核验状态，不作为报告输入。

指定 HRS-1597 文档的显式 Finder 识别可在共线评估节点附加既有 `conflict` DTO，
包含原文/推导 PDE、OEB 点估计分档、差值/比值及公式/因子；未具备比较输入的节点
返回 `pde_review_issues` 中文说明。未知特殊危害不视为阴性，也不单独作为 PDE 数值冲突。

- GET `/finder/pde-conflict/decision?execution_id=...`：读取当前匹配执行的人工审核；仅读取已有图谱和决策，不重新计算或启动任务。
- POST 同路径：高级分析师提交 `{chosen: derived|asserted|pending, expected_version, note?}`，返回既有人工决策 DTO（`job_id, conflict_key, chosen, note, actor, version, decided_at`）。
- 服务端核验当前用户/模板/源的已完成执行及其冲突。旧执行、原件变化、无冲突和版本冲突不能保存；不放开通用 Word 历史审核接口。
- 复用源作业的人工决策记录，在当前执行头记录已采纳的决策版本；重新识别后显示待复核，保留原记录，防止旧决定自动套用到新结果。版本号仍用于原子并发校验。

属性合法性按当前本体声明核验。Finder 显式引用且已声明为数据属性、但未声明 `rdfs:domain` 的属性也纳入只读校验菜单。生产风险表中未建模的风险因素、控制前后风险水平、可追溯性及风险状态沿用 `iri: null` 的原文展示契约，保留值和出处，不制造本体属性；其他缺失或类型错误的属性标识仍拒绝。

只保存最近 request_key；旧请求以 expected 冲突拒绝。重跑替换当前头/缓存，无历史回放，无 Finder 暂停/继续。

模板引擎设置（与 schema V1/V2、报告编译分离）：

- GET `/api/ast-templates/{template_id}/recognition-engine`：当前 `recognition_mode`、`finder_profile_id` 及适用的 `finder_profiles[{id,label}]`，登录用户可读。
- PATCH 同路径：高级分析师提交 `{recognition_mode, finder_profile_id, expected_recognition_mode, expected_finder_profile_id}`。期望值与当前有效配置不符或并发更新返回 409；不支持的引擎/profile/根类组合返回 422；静态演示模板返回 409。
- 页面显式设置优先于文件绑定。只更新当前修订的引擎元数据，不改 schema/hash/发布状态，不启动或取消任务、不删除缓存，新修订不继承。保存成功后模板页和报告中心刷新模式上下文；切回同引擎可读取其已有结果，沿用原失效检查。
- PATCH 成功即结束保存状态并使用响应更新本地引擎配置；不因此重新下载整份模板原文。报告上下文在后台刷新，慢请求或刷新失败不改变已保存状态；保存前发出的配置/详情读取会取消，防止迟到响应覆盖新选择。

V2「AST模板定义」保留「AI 自动分析结构」入口，两种关系图谱识别引擎均可使用。显式点击后复用 `/api/ast-templates/suggest-slots`，输入模板样例及其已保存结构信息，空模板将返回的章节、分组、候选转换为 V2 内容项并保留原文锚点。生成结果只进入编辑草稿，由作者另行保存新修订；已有章节及分析等待期间的人工编辑保留。未完成的语义建议和缺失结构信息须明确提示，不以样例值填造事实绑定或报告结论。
