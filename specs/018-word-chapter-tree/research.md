# Research: Word 章节树与分层摘要

## R1 — Canonical parser

**Decision**: 继续使用 `python-docx 1.2 + OOXML`，以 `parse_docx_structure()` 为唯一结构入口。

**Rationale**: 当前标题推断已经统一覆盖段落大纲级别、本地化 Heading/标题、TOC、字号、编号和加粗语义；标注器还实现了 Word 格式与显式分页。构树本身只需对已确定的 `level + 顺序` 执行栈算法。

**Alternatives considered**:

- 维持多个解析入口：会继续产生标题和表格归属漂移。
- 以 LLM 修正层级：不确定且不可审计。
- 本期替换为 Docling：缺少 DOCX 分页和稳定来源坐标，不能满足原文联动。

## R2 — Docling candidate assessment

**Decision**: 本期不安装、不导入、不调用 Docling；将 canonical `DocStructure` 作为未来后端适配边界。

**Evidence**:

- Docling v2.121.0 支持 `DoclingDocument` 层级、阅读顺序、表格、图片、公式、列表、批注、控件、Strict OOXML 和 JSON 导出。
- `MsWordDocumentBackend.supports_pagination()` 明确返回 `False`；官方 DOCX ground truth 中 `pages` 为空且元素 `prov` 为空。
- 标题跳级时 Docling 会插入不可见 section group，与本特性“不生成虚假层级”冲突。
- Docling 的 Word 标题主要依赖样式/样式大纲级别，不能直接替代本项目已有的字号、加粗、TOC 和段落直接大纲推断。
- DOCX SimplePipeline 不需要模型权重，但 `docling` 标准包包含大量 PDF/OCR/模型能力。2026-08-25 在 Python 3.12 对 `docling-slim[format-docx]==2.121.0` 的隔离试装中，`DocumentConverter` 导入仍依次要求未声明的 PDF/布局可选依赖，存在离线镜像风险。

**Future admission gates**:

1. 使用固定版本和内部 wheelhouse 完成 air-gap 安装，运行期零下载。
2. 代表性 Word 语料的标题、表格和内容覆盖不低于 canonical parser。
3. 能转换成现有稳定段落/表格来源坐标，不暴露 Docling JSON pointer 作为公共 ID。
4. 不参与章节层级和分页事实判定；首选影子对比或 Strict OOXML/复杂对象补充。
5. 镜像增量、冷启动和解析时延经独立特性审查。

## R3 — Pagination semantics

**Decision**: 只接受 OOXML 明确信号：手动分页、有效 `pageBreakBefore`、`nextPage/evenPage/oddPage` 分节、表格首行显式分页和 `lastRenderedPageBreak`。

**Rationale**: DOCX 保存流式排版规则而非最终渲染页；字数或字体估算会把不可靠结果展示成事实。无标记时生成章节内虚拟页并保留空物理页码。

## R4 — Source identity

**Decision**: 使用 `paragraph:{paragraph_index}:{fragment_index}`、`table:{table_index}` 和确定性分页事件 ID；章节使用 `section:{heading_index}`。

**Rationale**: 标题文本可能重复，内容哈希会随编辑变化。原始位置 ID 在一次标注输出内唯一，并能直接映射 Tiptap `data-source-block-id`。

## R5 — Summary generation

**Decision**: 页摘要后，按实际树深度从深到浅批量生成章节摘要，最后处理文档根。父节点输入只包含直接内容和直接子摘要。

**Rationale**: 控制上下文，保持层级语义并避免逐节点调用。LLM 仅返回 `node_id + content_summary`，所有计数、路径、哈希与状态由程序确定。

**Fallback**: 功能关闭标记 `disabled/none`；显式启用但调用失败时，清理空白并截取完整句作为 `extractive_fallback`，解析与事实抽取继续成功。

## R6 — API and state boundary

**Decision**: `/analysis?tab=document` 使用独立 `POST /api/document-analysis/word`；上传、转换、解析和摘要全部限定在一次请求中，不创建作业、数据库记录、标注缓存或持久文件。既有 annotated-document JSON 扩展保留给抽取/图谱消费者，不作为该页面数据源。

**Rationale**: 文档结构分析是即时工具，若复用 ExtractionJob，会被作业状态、后台生命周期、历史缓存和图谱抽取语义耦合；独立响应仍让章节树与原文共享同一次 canonical parse，并使刷新即清空的隐私/状态边界清晰。

## R7 — Frontend ephemeral state and editor linkage

**Decision**: URL 只保存 `tab=document`；File、响应和 `selectedNodeId` 保存在 `DocumentAnalysisPanel` 本地 state，刷新/关闭即丢弃。Tiptap 通过全局属性渲染来源 `data-*`，选择变化用 DOM 查询、滚动和 CSS 类高亮，不调用 `setContent`。

**Rationale**: 文件内容和即时分析不应被 URL 或服务端作业恢复；稳定 ID 仍避免重复标题误定位，不重建内容可避免滚动和性能抖动。

## R8 — Scope isolation

**Decision**: 摘要结果不传给分类、Profile、NER、关系或候选生成；不修改 ontology TTL。反向滚动跟随、摘要编辑、树虚拟化和复杂浮动对象留待后续。
