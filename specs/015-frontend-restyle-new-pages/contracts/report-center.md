# UI Contract: Report Center (报告中心) + Report Detail (报告详情)

**Feature**: 015 | Routes: `/reports`, `/reports/[reportId]` | User Stories 5 & 6 | Clarify Q1 (unified center)

## Backend surface（2026-09-14 报告列表优化）

用户已明确授权列表摘要查询、后端分页和解除编辑器依赖，替代历史 R2 的逐任务聚合。
文档继续使用 `/api/entities?module=document`，报告改用 `GET /api/reports?page=1&page_size=25`。
详情、下载、上传及删除沿用既有接口。

### GET /api/reports

- `page` 从 1 开始；`page_size` 默认 25，范围 1–100；非法参数返回 422。
- 响应为 `{items, total, page, page_size}`；超过末页返回空 `items`，仍返回可见总数。
- 每项仅含 `id`、`job_id`、`source_filename`、`report_type`、`file_size`、`created_at`。
  SQL 也只查询这些列，不加载 `rules_summary`、正文 AST、图谱、模板或 narratives。
- 顺序为 `created_at DESC, id DESC`。查询直接分页报告，不枚举任务。
- 可见性沿用既有按任务报告列表：排除已删除及未完成报告；兼容状态为空且文件大小大于零的
  旧报告；`batch_record_demo` 仅对原 actor 可见，其他报告保持既有共享语义。认证沿用现有门禁。
- 列表读取不调用模型、不修改报告和历史内容；详情接口继续提供完整报告。

文档及报告摘要使用独立查询并行加载，先完成的内容先展示；失败保留其他已加载内容并允许重试。
“加载更多”追加下一页，不重复请求文档或前面的报告页；页面缓存按用户隔离。删除后刷新报告页，
使分页位置和总数与服务端一致。分类按当前已加载条目统计。
列表的字节格式化、下载工具来自轻量模块，不依赖详情编辑器；详情链接关闭自动预取。
缺少查询参数的既有报告深链接按摘要页查找条目，正常列表导航不触发该查找。

## Report Center behavior (FR-021–023)

1. **Given** reports+documents organized by category, **When** the page loads, **Then** a category tree (report types ∪ `DOC_TYPE_LABELS` ∪ `DEVELOPMENT_PHASES`) and an item list show each item's title, type, date, and size.
2. **Given** an item, **When** its actions open (DropdownMenu), **Then** preview, download, and (if authorized) delete are available (FR-022).
3. **Given** an upload affordance (button + drag-and-drop), **When** used, **Then** the document is added (existing doc-repo `upload` / entity ingestion) and appears in the list (FR-023).
4. **Given** an empty category, **When** opened, **Then** a defined empty state renders (FR-023).

## Report Detail behavior (FR-024–026)

1. **Given** a selected item, **When** Detail opens, **Then** a main reading pane renders — a document via tiptap (`getAnnotatedDocument`) or a generated report's preview + download — with a **breadcrumb** back to Report Center (FR-024).
2. **Given** a long item, **When** the outline is used, **Then** selecting an entry scrolls the reading pane to that section (tiptap headings / report sections) (FR-025).
3. **Given** a related-info panel, **When** opened, **Then** linked ontology entities/metadata (`listExtractedFrom` / item metadata) are shown (FR-026).
4. **Given** an open item, **When** download or share is chosen, **Then** the corresponding action is available (FR-026).

## Invariants

- 当前列表允许上述已授权的只读摘要分页接口；其他写入契约不扩展。不增加存储、快照或历史清理。
- Full in-browser DOCX fidelity is **not** required — preview is best-effort; the authoritative file is always downloadable (FR-024).
- Delete/upload gated by role (FR-022).

## Verification

- Browse categories → open item preview/download → upload a document → view empty-category state (quickstart).
- Detail: outline navigation moves the pane; related-info shows linked entities; breadcrumb returns to center.
- 首屏仅请求文档及报告摘要；跨任务分页不漏掉旧任务中的新报告，列表 SQL 不读取大字段；
  权限/状态过滤与原列表一致，加载更多保留旧页，首次报告失败不遮挡文档，报告后续页失败可重试。
