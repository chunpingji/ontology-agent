# Implementation Plan: Word 章节树与分层摘要

**Branch**: `018-word-chapter-tree` | **Date**: 2026-08-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/018-word-chapter-tree/spec.md`

## Summary

扩展现有 `python-docx + OOXML` 统一解析器，使一次解析同时产出扁平兼容视图、有序块、显式章节树、可靠度明确的叶章节页节点和稳定来源范围。复用本地 OpenAI-compatible 客户端自底向上生成可降级摘要，并通过独立无状态上传 API 交付给 `/analysis` 的文档分析三栏工具。现有 annotated-document 扩展只服务旧抽取消费者，页面不读取作业或缓存。Docling 仅记录为未来复杂对象解析候选，不增加依赖或运行时分支。

## Technical Context

**Language/Version**: Python 3.11+（镜像 3.12）；TypeScript 5、React 19、Next.js 16.2

**Primary Dependencies**: FastAPI、python-docx 1.2、OpenAI Python SDK（可选 `llm` extra）、Next.js App Router、Tiptap 3、现有 shadcn/ui

**Storage**: 即时分析仅使用请求级 `TemporaryDirectory`，无数据库/缓存写入；旧抽取链保留现有 `{job_id}.annotated.json`

**Testing**: pytest；前端 ESLint 与 Next.js production build

**Target Platform**: air-gap Linux CPU 服务端与现代桌面/移动浏览器

**Project Type**: FastAPI + Next.js Web 应用

**Performance Goals**: 每次上传只进行一次结构解析；节点切换零网络重取和零编辑器内容重建；摘要调用按页批次与章节深度批次增长

**Constraints**: 运行期不出网；LLM 缺失零回归；不得猜测 Word 自动分页；保持旧解析和 API 契约；不新增 Docling 依赖

**Scale/Scope**: 单文档通常数百章节/页片段；首期树超过 1,000 展示节点仍采用折叠渲染，不引入虚拟化依赖

## Constitution Check

*GATE: Phase 0 前检查 — PASS；Phase 1 后复查 — PASS。*

- **I 规范驱动**: spec、research、data model、contracts、quickstart 和 tasks 均在实现前落地。
- **II 本体保真**: 不修改 TTL、T-Box、实体或关系事实语义。
- **III 可追溯**: 每个树/页节点保存稳定来源范围、内容哈希、Prompt/模型和生成时间。
- **IV 契约优先**: 无状态上传 API、摘要批次和前端联动契约先于实现，并配套 pytest/构建验证。
- **V 最小复杂度**: 复用现有解析器、LLM 客户端、TreeView 和 WordViewer；无新依赖。
- **VI 离线优先**: 摘要仅调用显式启用的本地端点；关闭为正常态，失败时确定性摘录且主路径成功。

Phase 1 设计没有引入数据库、外部服务或并行文档框架，宪章检查保持通过。

## Architecture and Data Flow

```text
Browser upload DOCX / DOC
  -> request-scoped TemporaryDirectory / DOC conversion
  -> parse_docx_structure (single canonical parse)
     -> flat sections/tables/paragraphs (legacy consumers)
     -> ordered WordBlock stream + deterministic page events
     -> ChapterNode tree + leaf PageNode ranges
  -> annotate_word(engine=None, structure_only=True, rich_style=True, structure=...)
  -> summarize_word_tree (optional local LLM, bottom-up)
  -> POST /api/document-analysis/word response (no job/cache/graph data)
  -> /analysis document tab
     -> chapter tree selection
     -> WordViewer activeLocation by block IDs
     -> node metadata panel
  -> request temporary files deleted
```

关键顺序由统一 Word body 遍历产生。段内分页把展示块拆成 fragment，但旧 `paragraphs` 和 `DocSection.paras` 保持整段文本。章节树只消费既定标题层级；摘要服务只附加元数据，不能改动树、正文或抽取事实。

## Project Structure

### Documentation (this feature)

```text
specs/018-word-chapter-tree/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/requirements.md
├── contracts/
│   ├── annotated-document.md
│   ├── stateless-word-analysis.md
│   ├── summary-batch.md
│   └── document-analysis-ui.md
└── tasks.md
```

### Source Code

```text
backend/app/services/extraction/
├── docx_structure.py
├── parser.py
├── document_annotator.py
├── relation_extractor.py
└── word_tree_summarizer.py
backend/app/services/llm/local_client.py
backend/app/api/document_analysis.py
backend/app/api/extraction.py
backend/app/config.py
backend/tests/test_extraction/

frontend/src/app/(dashboard)/analysis/page.tsx
frontend/src/components/analysis/
frontend/src/components/extraction/word-viewer.tsx
frontend/src/components/tree-view.tsx
frontend/src/lib/api.ts
```

**Structure Decision**: 延续现有后端服务与前端组件分层。统一 IR 保留在既有 `docx_structure.py`，避免另建文档框架；页面复用现有 `/analysis`、TreeView、WordViewer 和 API 客户端。

## Implementation Strategy

1. 先完成 canonical IR、构树、分页和旧适配器，用确定性测试锁定基础。
2. 将标注器和关系抽取改为接收可选已解析结构，建立 block ID 到 Tiptap 属性的完整链路。
3. 增加摘要服务和配置，通过现有本地客户端批量生成并逐节点降级。
4. 保留 annotated-document 的兼容扩展供既有抽取消费者使用。
5. 增加独立无状态上传 API；请求结束清理源文件与转换产物，不触碰 DB/缓存/图谱。
6. 增加 `/analysis` 文档分析 Tab，文件与节点均为本地临时状态，节点切换只改变 DOM 高亮。
7. 跑定向测试、完整后端回归、前端 lint/build，最后更新任务清单。

## Complexity Tracking

无宪章违例。Docling、数据库表、前端虚拟化和反向滚动跟随均不在本期实现。
