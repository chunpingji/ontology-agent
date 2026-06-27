---
description: "Task list for 离线本地实体抽取（air-gap 默认 + prose 召回）"
---

# Tasks: 离线本地实体抽取（air-gap 默认 + prose 召回）

**Input**: Design documents from `/specs/008-gliner-ner-extraction/`

**Prerequisites**: [plan.md](./plan.md)（必需）、[spec.md](./spec.md)（用户故事）、[research.md](./research.md)、[data-model.md](./data-model.md)、[contracts/](./contracts/)、[quickstart.md](./quickstart.md)

**Tests**: 包含测试任务——宪章原则 IV「测试纪律与契约优先」+ plan「contracts/ 先行」要求关键路径契约/集成测试；测试以确定性桩替换真实权重（[contracts/gliner-extractor.md](./contracts/gliner-extractor.md)「测试桩约定」），不下载模型。

**Organization**: 按用户故事分组，每story可独立实现与验证。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无未完成依赖）
- **[Story]**: US1 / US2 / US3（映射 spec 用户故事）；Setup/Foundational/Polish 无 story 标签
- 每个任务含精确文件路径

## Path Conventions

Web 应用：后端代码 `backend/app/...`，测试 `backend/tests/...`，迁移 `backend/app/alembic/versions/`。前端本期不改（见 plan）。

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 依赖声明与离线模型供给脚手架

- [X] T001 在 `backend/pyproject.toml` 的 `[project.optional-dependencies]` 增 `gliner = ["gliner>=0.2.13"]`（与既有 `semantic` extra 同范式）
- [X] T002 [P] 在 `.gitignore` 增 `backend/models/`（本地权重不入 git，宪章 安全）
- [X] T003 [P] 创建离线模型供给脚本 `backend/scripts/fetch_models.sh`：`huggingface-cli download urchade/gliner_multi-v2.1 → backend/models/gliner_multi-v2.1/` 与 `BAAI/bge-small-zh-v1.5 → backend/models/bge-small-zh-v1.5/`，并输出 SHA256 校验和登记交付清单（research [R8](./research.md)）

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 所有用户故事共享的配置与本地 NER 能力底座

**⚠️ CRITICAL**: 本阶段完成前，任何用户故事不得开工

- [X] T004 扩展 `backend/app/config.py:Settings`：新增 `gliner_extraction_enabled: bool = True`、`gliner_model_path: str = "models/gliner_multi-v2.1"`、`gliner_threshold: float = 0.5`、`llm_cloud_enabled: bool = False`；并把 `semantic_embedding_model` 默认改为本地 `"models/bge-small-zh-v1.5"`（data-model §2，FR-002/016，research [R2](./research.md)/[R8](./research.md)）
- [X] T005 [P] 契约测试 `backend/tests/test_extraction/test_gliner_extractor.py`：覆盖 [contracts/gliner-extractor.md](./contracts/gliner-extractor.md) C1–C6（绝不抛出 / 单例 / 功能开关 / 强制 `local_files_only` / 多值聚合 / 标签驱动），用确定性桩，先失败
- [X] T006 实现 `backend/app/services/extraction/gliner_extractor.py`：`GlinerExtractor`（`_ensure_model` try-import + `GLiNER.from_pretrained(settings.gliner_model_path, local_files_only=True)`、`_failed` 守卫、`is_available()`、`extract_text(text, labels, threshold)` 同标签多命中聚合为 list）+ `@lru_cache(maxsize=1) get_gliner_extractor()`（关闭返回 None）；逐字镜像 `semantic.py`（FR-011/012/013/014）
- [X] T007 [P] 契约测试 `backend/tests/test_extraction/test_schema_derivation.py`：覆盖 [contracts/ner-schema-derivation.md](./contracts/ner-schema-derivation.md) S1–S6（只读 / label 优先 name 回退 / 回填 / 空类安全 / 本体演进自适应 / 唯一标签），先失败
- [X] T008 实现 `backend/app/services/extraction/pipeline.py:_schema_from_class(target_class_iri)`：只读 `ontology_engine.get_class_detail(iri).data_properties` 派生 `{"labels", "label_to_iri"}`（data-model §3.1，FR-009）

**Checkpoint**: 配置与本地 NER 能力就绪——用户故事可开工

---

## Phase 3: User Story 1 - air-gap 下零联网完成抽取（去云依赖） (Priority: P1) 🎯 MVP

**Goal**: 无网络、无 Key 下结构化源端到端抽取成功，Word 表头确定性映射替代云端，云端降为 opt-in 默认关，且离线**不误标 degraded**。

**Independent Test**: 断网+无 Key 跑一批结构化 Excel/含表格 Word/DB 抽取——作业成功、结构化产出与联网基线一致（零回归）、Word 中文表头按映射转 IRI、全程零外发、候选与进度均 `degraded=False`（[quickstart](./quickstart.md) 场景 1/5）。

### Tests for User Story 1 ⚠️（先写、先失败）

- [X] T009 [P] [US1] 契约测试 `backend/tests/test_extraction/test_offline_invariants.py`：覆盖 [contracts/offline-extraction-invariants.md](./contracts/offline-extraction-invariants.md) O1–O5、O9–O10（云端 opt-in 双条件门控 + 离线非降级 + 零外发），先失败
- [X] T010 [P] [US1] 契约测试 `backend/tests/test_extraction/test_parse_word_mapping.py`：覆盖 [contracts/parser-and-enrichment.md](./contracts/parser-and-enrichment.md) P1–P4（Word 表头→IRI 确定性映射 / 零云端 / 向后兼容 / 段落形态不变），先失败
- [X] T011 [P] [US1] 集成测试 `backend/tests/test_extraction/test_offline_pipeline.py`：断网+无 Key 跑结构化 Excel/Word/DB，断言作业成功、结构化候选与黄金基线逐字一致（O7 零回归）、所有 `_emit(..., degraded=False)`、无 anthropic 调用（quickstart 场景 1/5，SC-001/002/003）

### Implementation for User Story 1

- [X] T012 [US1] 翻转 `backend/app/services/extraction/llm_extractor.py:extract_with_fallback` 门控为 `if settings.llm_cloud_enabled and settings.anthropic_api_key:` 才调云端，否则 `return (source_data, None)`（离线 `degraded_reason=None`）（FR-002/003，research [R2](./research.md)）
- [X] T013 [US1] 给 `backend/app/services/extraction/parser.py:parse_word` 增 `column_mapping: dict | None = None`：表头命中映射→IRI 键，未命中原样保留（FR-004，research [R6](./research.md)）
- [X] T014 [US1] 在 `backend/app/services/extraction/pipeline.py` Word 分支调 `parse_word(file_path, config.column_mapping)`，使表格表头→IRI 走确定性映射（依赖 T013）
- [X] T015 [US1] 在 `backend/app/services/extraction/pipeline.py` 校核进度/降级语义：离线作业 `_emit` 恒 `degraded=False`，`degraded` 仅源于真实云端降级（FR-003，依赖 T012）

**Checkpoint**: US1 可独立交付——air-gap 结构化抽取零回归、零联网、零误降级（MVP）

---

## Phase 4: User Story 2 - Word 正文 prose 实体进入审核队列 (Priority: P2)

**Goal**: Word 正文段落实体经本地 NER 召回为 `instance` 候选、带 `#para` 溯源、入待复核队列，与既有 Action 候选并存。

**Independent Test**: 含业务实体描述段落的 Word 离线抽取——每个 prose 实体生成 `instance` 候选入队、带 `#para` 与对齐结果/分数/分组键、`review_status=pending`；Action 正则候选并存；同标签多命中聚多值（[quickstart](./quickstart.md) 场景 2）。

**Dependency**: 依赖 Foundational（T006 GlinerExtractor、T008 `_schema_from_class`）。spec 述 US2 立于 US1 链路之上；与 US3 相互独立。

### Tests for User Story 2 ⚠️（先写、先失败）

- [X] T016 [P] [US2] 集成测试 `backend/tests/test_extraction/test_word_prose.py`：Word 正文段落→`instance` 候选入队、`source_ref` 含 `#para`、`review_status=pending`、携对齐结果/分数/分组键；与 `parse_action_from_text` 的 Action 候选并存；同标签多命中聚多值（FR-005/006/010/011，data-model §3.3，先失败）

### Implementation for User Story 2

- [X] T017 [US2] 在 `backend/app/services/extraction/pipeline.py` Word 段落循环增 prose 分支：`schema=_schema_from_class(target)`；`ex=get_gliner_extractor()`，守卫 `ex and ex.is_available()`；`await asyncio.to_thread(ex.extract_text, text, schema["labels"], settings.gliner_threshold)`（FR-015 不阻塞）；经 `label_to_iri` 回填→`tag_controlled_vocab`→`align_entity`→`_compute_group_key`，构造 `candidate_kind="instance"`、`source_ref=f"{ref}#para"`、`review_status="pending"` 候选；与既有 `parse_action_from_text` 并存（依赖 T006/T008）

**Checkpoint**: US1 与 US2 各自独立可用——正文段落成为一等实体来源

---

## Phase 5: User Story 3 - Excel 自由文本列回填本行属性（富化） (Priority: P3)

**Goal**: 按列白名单声明的 Excel 自由文本列经本地 NER 抽取并回本行——结构化列权威、仅补空缺、不另生候选、白名单外列不处理。

**Independent Test**: 含自由文本列的 Excel、声明该列为 NER 列、离线抽取——结果并回本行；结构化已填值不被覆盖、仅补空缺；未声明列不做 NER；不产生新候选；NER 不可用时原文丢弃、结构化行照常（[quickstart](./quickstart.md) 场景 3/4）。

**Dependency**: 依赖 Foundational（T006 GlinerExtractor、T008 `_schema_from_class`）。与 US2 相互独立（共享 `pipeline.py`/`parser.py`，按文件顺序衔接）。

### Tests for User Story 3 ⚠️（先写、先失败）

- [X] T018 [P] [US3] 契约测试 `backend/tests/test_extraction/test_excel_enrichment.py`：覆盖 [contracts/parser-and-enrichment.md](./contracts/parser-and-enrichment.md) P5–P12（白名单暂存 `__freetext__` / 不污染属性 / 向后兼容 / 结构化权威 / 仅补空缺 / 不另生候选 / 清除暂存 / NER 不可用零回归），先失败
- [X] T019 [P] [US3] 集成测试 `backend/tests/test_extraction/test_excel_enrichment_pipeline.py`：声明 `ner_columns` 后跑 Excel——空缺属性被富化、结构化已填值 0 覆盖、候选数=行数、候选无 `__freetext__`；NER 不可用时优雅降级（quickstart 场景 3/4，SC-005，先失败）

### Implementation for User Story 3

- [X] T020 [US3] 给 `backend/app/models/extraction.py:ExtractionConfig` 增 `ner_columns: Mapped[list | None] = mapped_column(JSON)`（data-model §1.1，FR-017）
- [X] T021 [US3] 新建 Alembic 迁移 `backend/app/alembic/versions/<rev>_add_ner_columns.py`：`extraction_configs` 增 `ner_columns JSON NULL`（upgrade/downgrade，data-model §1.2，宪章 质量门禁；依赖 T020）
- [X] T022 [P] [US3] 给 `backend/app/schemas/extraction.py` 的 `ExtractionConfigCreate`/`ExtractionConfigResponse` 增 `ner_columns: list[str] | None = None`（data-model §1.3）
- [X] T023 [US3] 给 `backend/app/services/extraction/parser.py:parse_excel` 增 `ner_columns: list[str] | None = None`：命中列原文暂存 `row["__freetext__"][表头]`，不直接作属性值（FR-007，research [R5](./research.md)；与 T013 同文件，T013 后衔接）
- [X] T024 [US3] 在 `backend/app/services/extraction/pipeline.py` Excel 分支：传 `parse_excel(..., ner_columns=config.ner_columns)`；抽取前对每行 `__freetext__` 经 `get_gliner_extractor()` 守卫 + `asyncio.to_thread` 跑 `extract_text`，经 `label_to_iri` 得 `ner_props`，调新增 `_merge_ner(row, ner_props)`（仅补空缺、结构化权威）后 `row.pop("__freetext__")`；**不产生新候选**（FR-008/018，data-model §3.2；依赖 T006/T008/T023）

**Checkpoint**: 三个用户故事均可独立验证

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 跨故事的预热、降级保障、部署文档与端到端验收

- [X] T025 [P] 在 `backend/app/main.py:lifespan` 增启动预热块：`get_gliner_extractor()` 与 `get_embedder()` 各 `is_available()` 触发本地加载，消除首作业冷启动（FR-014，SC-007，quickstart 场景 6）
- [X] T026 [P] 集成测试 `backend/tests/test_extraction/test_ner_degradation.py`：NER 不可用（`gliner_extraction_enabled=False` 或缺权重桩）时，US2/US3 输入作业仍成功、prose 为空、Excel 不富化、记 WARNING、不标 degraded（[contracts/offline-extraction-invariants.md](./contracts/offline-extraction-invariants.md) O6–O8，FR-012，SC-006）
- [X] T027 [P] 文档：在部署说明/quickstart 引用处记 air-gap 运行期 env（`HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1`）、`uv sync --extra gliner`、权重供给与校验和流程（FR-013，research [R8](./research.md)）
- [X] T028 执行 [quickstart.md](./quickstart.md) 场景 1–6 端到端验收，记录 SC-001..007 结果；为 SC-004/SC-005 留真实中文样本标定占位（research [R10](./research.md)）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，立即可开始
- **Foundational (Phase 2)**: 依赖 Setup（T001 装 `gliner` 后 T005/T006 方可跑）；**阻塞所有用户故事**
- **User Stories (Phase 3–5)**: 均依赖 Foundational 完成
  - US1 不依赖 T006/T008（仅用 T004 配置）；US2/US3 依赖 T006+T008
  - 故事间可并行（若人力允许）或按 P1→P2→P3 顺序
- **Polish (Phase 6)**: 依赖所需用户故事完成

### User Story Dependencies

- **US1 (P1)**: Foundational 后即可——仅用 T004；与 US2/US3 独立
- **US2 (P2)**: 依赖 Foundational（T006/T008）；与 US3 独立
- **US3 (P3)**: 依赖 Foundational（T006/T008）；与 US2 独立

### 同文件顺序约束（非 [P]）

- `pipeline.py`：T008（Found）→ T014/T015（US1）→ T017（US2）→ T024（US3） 顺序编辑，互不 [P]
- `parser.py`：T013（US1, parse_word）→ T023（US3, parse_excel） 顺序编辑
- `config.py`：T004 单点
- `models/extraction.py` T020 → 迁移 T021（依赖关系）

### Within Each User Story

- 测试先写并先失败（宪章 IV），再实现
- 模型/配置 → 服务 → 流水线接线 → 集成

### Parallel Opportunities

- Setup：T002、T003 可并行
- Foundational：T005、T007 两个测试文件可并行；实现 T006/T008 各依赖其测试
- US1：T009、T010、T011 三个测试文件可并行
- US3：T018、T019 测试可并行；T022（schema）与 T020/T021（model/迁移）不同文件可并行
- Polish：T025、T026、T027 可并行
- Foundational 完成后，US1 / US2 / US3 可由不同开发者并行推进（注意上述同文件顺序约束）

---

## Parallel Example: User Story 1

```bash
# US1 测试先行，三个不同文件可并行：
Task: "契约测试 test_offline_invariants.py（云端门控 + 离线非降级 + 零外发）"
Task: "契约测试 test_parse_word_mapping.py（Word 表头→IRI 确定性映射）"
Task: "集成测试 test_offline_pipeline.py（断网结构化作业零回归 + 零降级）"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 Setup（至少 T001 装 `gliner`、T004 配置可在 Phase 2）
2. Phase 2 Foundational（US1 仅强依赖 T004；T006/T008 可与 US1 并行或延后到 US2 前）
3. Phase 3 US1
4. **STOP & VALIDATE**：断网+无 Key 跑结构化批次，确认零回归/零联网/零降级（quickstart 场景 1/5）
5. 可交付——air-gap 部署底座成立

### Incremental Delivery

1. Setup + Foundational → 能力底座就绪
2. US1 → 独立验证 → 交付（MVP：去云依赖）
3. US2 → 独立验证 → 交付（Word prose 召回）
4. US3 → 独立验证 → 交付（Excel 自由文本富化）
5. Polish（预热/降级保障/文档/端到端验收）
6. 每个故事增值且不破坏既有结构化主路径（FR-018 零回归）

### Parallel Team Strategy

1. 团队先合力完成 Setup + Foundational
2. 之后：开发 A→US1、B→US2、C→US3（遵守 `pipeline.py`/`parser.py` 同文件顺序约束，必要时串行化这些编辑）
3. 各故事独立完成与集成

---

## Notes

- [P] = 不同文件、无未完成依赖
- [Story] 标签把任务映射到用户故事，便于追溯
- 每个用户故事应独立可完成、可测试
- 实现前先确认测试失败（宪章 IV 契约优先）
- prose 候选 `review_status="pending"`——复核门禁零削弱（宪章 II）
- 本特性不写 T-Box、无 `surgical_merge`、`get_class_detail` 只读——本体保真风险面为零
- 权重不入 git；运行期强制离线（`local_files_only` + `HF_*_OFFLINE` 双保险）
- 关键避坑：`pipeline.py`/`parser.py` 跨故事同文件编辑须串行；NER 推理必经 `asyncio.to_thread`（FR-015）；离线永不标 `degraded`（FR-003）
