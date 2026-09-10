# 报告中心上传 Word 接入（2026-09-10）

## 需求与验收

历史上传 Word 的 `mode=auto` 已不能读取旧标注接口。报告中心须复用保存的原件，
恢复正文与章节目录，并显式创建、回看当前用户的新 Word 分析运行。打开、刷新或
切换文档不得启动模型；旧关系、缓存、检查点和事实提交不进入新运行。

## API

三个接口均位于 `/api/document-analysis/documents`，通过必填查询参数
`document_iri` 精确解析 `module=document` 的影子记录及其登记的 Word 作业。
原件路径、根类型和关联由服务器读取，客户端不能提交路径或替换作业。

| 请求 | 行为 |
|---|---|
| `GET /source?document_iri=...` | 共享 Word 结构解析器的正文预览；返回登记类型、源作业、文档版本、hash、content、section_tree、pagination、warnings；不创建运行、不调用模型、不读旧标注缓存 |
| `GET /runs?document_iri=...` | 返回 `{run: ... \| null}`；只查当前 owner、同一登记版本/类型/作业/原件 hash、未删除且未过期的最新运行；不派发 |
| `POST /runs?document_iri=...` | JSON `{request_key}`；按既有 senior_analyst 门禁复用原件创建新运行，返回既有 202 回执；同 owner/同请求键/同输入幂等，仅首次派发 |

文档库沿用现有登录用户共享读取范围；分析运行沿用 owner 隔离。运行源 artifact 的
`origin` 保存 `kind=report-document-origin-v1`、document_iri、source_job_id、
document_version、root_class_iri；源 hash 由既有上传存储冻结，不改写旧作业或影子记录。
版本、作业、类型或原件内容变化后，不把旧运行显示为当前文档的结果。

登记缺失/关联缺失/原件缺失返回明确 404，非 Word 或类型矛盾返回 422，无法解析
返回 422。运行失败、未决、未尝试继续使用已有运行/图谱语义，不解释为空图成功。
已创建运行的原件副本仍按已有运行授权和保留策略读取。

## 实现计划

复用 DocumentAnalysisApplication.create_run 和运行源 artifact 存关联，无新增表。
报告页 Word 分支复用模板页的运行订阅、取消、控制与图谱展示，Excel/生成报告继续
现有展示。新运行结构可用前使用纯正文预览；之后正文、目录、证据定位均来自同一运行。
新运行 ID/用户/结构版本参与缓存键，切换文档取消旧请求，HTTP 错误提供重试。

## 验证任务

- [x] 后端：历史 auto 原件 GET 可预览、目录可读，GET 无模型/运行/旧缓存写入。
- [x] 后端：创建幂等、owner 隔离、角色/原件/类型校验、版本变化和过期过滤。
- [x] 前端：刷新仅 GET、显式创建、正文/目录/图谱接线、身份与定位匹配。
- [x] 定向测试、类型检查与静态检查；见[验证记录](../report-document-validation.md)。
