# 模板关系图谱的新内核运行契约

对应 [迁移方案](../kernel-panel-migration.md)。扩展现有 `document-analysis-runs-v1`，
不新增执行域或业务事实写入。不接受浏览器提供原件路径、本体、优先路径或期待结果。

## 模板源运行入口

`/api/document-analysis/templates/{template_id}/sources/{job_id}/runs`

- `GET`：鉴权只读，返回 `{ "run": DocumentAnalysisRunResponse | null }`。
  仅查询当前 owner、指定模板与源作业的最新未删除、未到期运行；不能以文档哈希共享
  其他 owner 的运行。刷新及事件订阅不启动解析、模型调用或新运行。
- `POST`：仅 `senior_analyst`，JSON `{ "request_key": string }`，202 返回现有
  `CreateRunResponse`。相同 owner/key/输入幂等；不同模板版本或不同原件复用 key 返回409。
  服务端核对登记源类型与模板 IRI 模式，读取原件并独立保存。默认 `cached_summary`，
  当前实现无跨运行摘要缓存，按既有契约记录 `structure_only` 回退。
- 未找到模板、源作业或原件返回404；登记类型不匹配返回422；未知请求字段返回400；
  角色不足返回403。错误沿用现有文档分析错误结构。

源制品 payload 冻结 `origin`：`kind=template-document-origin-v1`、`template_id`、
`source_job_id`、`template_hash`、`priority_paths`。origin 参与 request_hash 与运行指纹。
模板路径只排序本体合法任务；不改变事实权限，不合并同名实体，不包含参考答案。
仅同模板/hash 的显式 operator 优先路径可继续采用。

运行的读取、暂停、恢复、取消、graph、source、SSE 继续使用
`/api/document-analysis/runs/{recognition_run_id}/...` 的 owner、版本冲突和幂等契约。
新协议创建新运行；指纹不匹配的历史断点不得改写指纹重用。

## 图谱菜单与原文

GraphEntity 增加可选 `predicate_menu`：
`[{predicate_iri, predicate_label, kind: "property" | "relationship"}] | null`。
菜单只从该运行冻结本体编译；空数组表示本体未声明任何菜单，null 表示定义不可用。
不能用当前在线本体补齐历史菜单。coverage 继续使用已计划、检查、未完成、未尝试的
守恒计数；`candidate_policy=sparse-candidates-v1` 时计实际候选任务，未入选全文
范围单独诊断。finished 显示“本轮识别完成”；没有有效结果不构成全文否定，也不能
仅凭空图宣称策略完成。旧载荷保留原计数口径，详见[候选完成契约](candidate-completion.md)。

模板面板默认读取 `effective_affirmed`。候选详情、实体名、属性值、主体归属、谓词、
条件、反证均用 run-owned selection_ref 获取原文。正文与锚点同时来自该 source 响应，
核对 recognition_run_id 与 graph analysis_id；切换源或运行时不复用旧选区。
系统验证与人工确认分开；新图不调用旧候选审核、PDE 或提交接口。

## 验证入口

- `tests/test_api/test_template_document_runs.py`：GET 无任务、幂等、owner、源类型、模板版本、删除/到期。
- `tests/test_extraction/test_template_kernel_priorities.py`：关系/属性排序、断点回放、章节背景权限。
- `tests/test_extraction/test_ontology_task_citations.py`：名称/证明分离、原子引用、字段归属双端证明。
- 真实模板验收的运行及证据单独记录于迁移方案，不以旧 B 路径结果替代。
