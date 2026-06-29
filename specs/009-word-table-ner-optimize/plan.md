# Implementation Plan: Word 表格实体识别优化与文档-代码对齐

**Branch**: `009-word-table-ner-optimize` | **Date**: 2026-06-27 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/009-word-table-ner-optimize/spec.md`

## Summary

重构 `annotate_word` 的表格文本组装逻辑：从逐 cell 独立推理改为行级上下文拼接，
增加多行表头检测、vMerge 续行去重、嵌套表格递归处理，并同步修复技术方案文档中
GAP 分析识别的所有偏差项（B1-B3、C1-C5）。

## Technical Context

**Language/Version**: Python 3.11

**Primary Dependencies**: FastAPI, python-docx, GLiNER (gliner-multi-v2.1), SentenceTransformers (bge-base-zh-v1.5), Owlready2

**Storage**: PostgreSQL (Alembic migrations) — 本特性无 DB schema 变更

**Testing**: pytest (`uv run pytest`)

**Target Platform**: Linux server (air-gap / 内网部署)

**Project Type**: Web application (FastAPI backend + Next.js frontend)

**Performance Goals**: 表格标注时间不超过段落标注的 2 倍（行级拼接减少 GLiNER 调用次数，预期更快）

**Constraints**: 离线运行（`local_files_only=True`、`HF_HUB_OFFLINE=1`）；CPU 推理经 `asyncio.to_thread` 卸载

**Scale/Scope**: SLPRA ~201 本体类；典型文档 10-50 页、5-20 张表格

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. 规范驱动开发 | ✅ PASS | 遵循 specify → clarify → plan 流程；spec.md 为唯一需求来源 |
| II. 本体权威性与保真 | ✅ PASS | 不修改 T-Box / TTL；仅改变 NER 管线读取行为 |
| III. 可追溯与审计 | ✅ PASS | 不涉及版本化/发布变更；标注结果经现有 ExtractionCandidate 入队 |
| IV. 测试纪律与契约优先 | ✅ PASS | 新增 4+ 测试用例覆盖行级拼接、多行表头、vMerge、嵌套表格 |
| V. 最小复杂度与复用 | ✅ PASS | 复用现有 `_annotate_texts` / `_type_and_filter_spans` 管线，仅改变输入组装；无新依赖 |
| VI. 离线优先与优雅降级 | ✅ PASS | 不引入任何出网依赖；GLiNER 不可用时仍然优雅降级 |

**Post-Phase 1 re-check**: 所有原则维持 PASS。无新增依赖、无 DB 变更、无外部接口变更。

## Project Structure

### Documentation (this feature)

```text
specs/009-word-table-ner-optimize/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0: technical decisions
├── data-model.md        # Phase 1: internal data structure changes
├── quickstart.md        # Phase 1: validation guide
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
backend/
├── app/
│   └── services/
│       └── extraction/
│           ├── document_annotator.py   # PRIMARY: annotate_word refactoring
│           └── ontology_typer.py       # seed_labels (read-only reference)
├── tests/
│   └── test_extraction/
│       ├── test_word_formatting.py     # PRIMARY: new table NER tests
│       └── test_document_annotator.py  # regression verification
docs/
└── Word-PDF文档实体识别优化技术方案.md  # doc updates (B1-B3, C1-C5)
```

**Structure Decision**: Web application (FastAPI backend). 本特性全部变更在 `backend/app/services/extraction/document_annotator.py`（代码）、`backend/tests/test_extraction/test_word_formatting.py`（测试）和 `docs/` 文档中。前端不涉及变更（tiptap JSON 结构不变）。

## Complexity Tracking

无 Constitution 违例，无需论证。
