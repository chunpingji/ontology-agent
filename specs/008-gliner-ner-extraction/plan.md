# Implementation Plan: 离线本地实体抽取（air-gap 默认 + prose 召回）

**Branch**: `008-gliner-ner-extraction` | **Date**: 2026-06-26 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/008-gliner-ner-extraction/spec.md`

## Summary

把能力二抽取流水线从**「云端 LLM 默认」翻转为「本地默认」**，使其在**无互联网（air-gap）、无 API Key** 的内网环境端到端可用，并补齐当前为空的 **prose 实体召回**。核心三件事，全部为**既有机制的薄接缝复用**（宪章 V）：

1. **离线翻转（US1）**：`extract_with_fallback` 由「无 Key 即降级」改为「**仅当 `llm_cloud_enabled` 且有 Key/网络才调用云端**」，否则直接返回结构化源且 **`degraded_reason=None`**（离线是正常态，不再误标降级）。Word 表格表头→IRI 映射由**确定性 `column_mapping`** 完成（`parse_word` 增 `column_mapping` 参，与 `parse_excel` 一致），替代此前唯一依赖云端 LLM 的环节。结构化主路径（Excel 列、DB 反射）本就离线、零回归。
2. **Word 正文 prose 召回（US2）**：新增 `services/extraction/gliner_extractor.py`——**逐字镜像 `semantic.py:SentenceTransformerEmbedder` 的可插拔 + 惰性加载 + 优雅降级 + `@lru_cache` 进程级单例**范式，本地零样本 NER（`local_files_only=True`）。Word `paragraph` 段落经 `gliner.extract_text` → `instance` 候选，复用既有 `tag_controlled_vocab` + `align_entity` + `_compute_group_key`，`source_ref` 落 `#para`，与既有 `parse_action_from_text`（Action 候选）**并存**。复核门禁零削弱（候选 `review_status="pending"`）。
3. **Excel 自由文本列富化（US3）**：`ExtractionConfig` 增 `ner_columns`（自由文本列白名单，JSON，经 Alembic 迁移）。`parse_excel` 把白名单列原文暂存 `__freetext__`；pipeline 抽取前用 `gliner.extract_text` 富化、`_merge_ner` **并回本行**（结构化列权威，仅补空缺），再 strip 原文——**不产生新候选**。

**技术取向（关键决策，详见 [research.md](./research.md)）**：**零新建框架**。本地 NER 引擎选 **`urchade/gliner_multi-v2.1` + `predict_entities`**（多语支持中文这一硬约束，零样本即用；GLiNER2/英文 biomed 接口预留）。NER「抽什么字段」从**目标本体类 `OntologyEngine.get_class_detail(iri).data_properties`**（每项 `{iri, name, label, range}`，已核验）派生——属性 label 作标签、回填 iri 键。优雅降级沿用 `extract_with_fallback`/`semantic.py` 思路：缺包/缺权重/加载失败 `is_available()=False`，静默跳过到结构化兜底、作业不失败、记 WARNING 供部署自检。同步推理经 `asyncio.to_thread` 不阻塞事件循环；`main.py:lifespan` 增**启动预热**（`get_gliner_extractor().is_available()` + `get_embedder().is_available()`），消除首作业冷启动。离线供给：构建期 `huggingface-cli download` 预置 `backend/models/{gliner_multi-v2.1,bge-small-zh-v1.5}/`，运行期 `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1` + `local_files_only=True` 双保险；权重不入 git，经制品库/镜像分发并登记校验和。

> 完整设计与技术权衡见 [`docs/NER by GliNER.md`](../../docs/NER%20by%20GliNER.md)。本特性**仅触能力二抽取/候选路径，不写 T-Box**（无 `surgical_merge`），故本体保真风险面极小。

## Technical Context

**Language/Version**: Python 3.11（后端）。前端**本期可不动**（自由文本列白名单与 NER 开关可经既有抽取配置入口或后端默认承载；若加最小 UI 另议，不在关键路径）。

**Primary Dependencies**: FastAPI（`APIRouter`+`Depends`）、SQLAlchemy 2.0（`Mapped`/`mapped_column`）、Alembic、Owlready2、pytest（既有）。**新增一个可选依赖** `gliner>=0.2.13`（含 `torch`+`transformers`，CPU 可跑），与 `[semantic]` 同范式置于 `[project.optional-dependencies].gliner`；缺失时优雅降级。`torch` 在已启用 `--extra semantic` 的部署中**非净新增**（`sentence-transformers` 已引入）。**不新增**抽取/NER 框架、不引入并行栈。

**Storage**: PostgreSQL——`extraction_configs` 表**新增一列** `ner_columns JSON NULL`（经 Alembic 迁移，宪章「DB 变更 MUST 经 Alembic」）；其余复用现表，零建表。本地模型权重为**文件制品**（`backend/models/…`，不入库不入 git）。**不写权威 TTL、不触 Owlready2 World 写路径**（仅只读 `get_class_detail` 派生 schema）。

**Testing**: pytest（契约/集成）。新增：离线默认「不调用云 + 不标 degraded」契约、云端 opt-in 触发门控、GLiNER 不可用优雅降级（作业成功 + prose 为空 + WARNING）、`parse_word(column_mapping)` 表头→IRI 确定性映射、`parse_excel(ner_columns)` 原文暂存、`_merge_ner` 结构化权威/仅补空缺、`_schema_from_class` 从 `data_properties` 派生、同标签多值聚合、Word `#para` 溯源。GLiNER 推理以**确定性桩**注入（同 `Embedder` 协议测试桩），不下载真实权重。

**Target Platform**: 内网 / **air-gap** Linux 服务器（无互联网、无云端 LLM）；小并发、长生命周期。

**Project Type**: Web（backend FastAPI + frontend Next.js）。本特性**后端单侧**为主。

**Performance Goals**: CPU 本地推理；逐段/逐单元格天然切块。启动预热把模型加载（air-gap 下仅本地 I/O，秒级）前移到 lifespan，首作业加载等待为 0。推理 `asyncio.to_thread` 不阻塞事件循环。结构化主路径吞吐零回归。

**Constraints**: 运行期 **MUST 零外发网络**（`local_files_only=True` + `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` 双保险）；离线 MUST NOT 标 `degraded`（FR-003）；云端 LLM MUST 默认关、仅显式开启+有 Key 才触发（FR-002）；结构化列权威、NER 仅补空缺（FR-008）；prose 候选 MUST 入复核队列、门禁零削弱（FR-005/010）；缺包/缺权重 MUST 优雅降级、作业不失败（FR-012）；权重 MUST NOT 入 git（宪章 安全）。

**Scale/Scope**: 1 新模块（`gliner_extractor.py`，~60 行镜像 `semantic.py`）；3 处点状改造（`llm_extractor`/`parser`/`pipeline`）；1 列 Alembic 迁移；2 本地模型制品；2 处 schema/config 字段；1 lifespan 预热块。单位数并发用户。

## Constitution Check

*GATE: Phase 0 前与 Phase 1 后各评估一次。*

| 原则 | 适用门禁 | 本计划落点 | 结论 |
|---|---|---|---|
| **I 规范驱动** | 规范为唯一真理；实现细节不渗入规范 | 已 specify（含 5 条嵌入式澄清）→本 plan；规范只含 WHAT/WHY（「本地零样本 NER」「确定性映射」「强制离线」），引擎/权重/env 等技术落点只在本 plan 与设计制品 | ✅ PASS |
| **II 本体权威性与保真 (NON-NEGOTIABLE)** | 外科式合并、保留未建模三元组、双存储一致、写前 diff | 本特性**不写 T-Box、无 `surgical_merge`、不触 World 写路径**——仅只读 `get_class_detail` 派生 NER schema，产出**待复核候选**（A-Box 草稿）；prose 候选 `review_status="pending"`，**复核门禁零削弱**，不自动断言为权威事实 | ✅ PASS（保真风险面最小：无 TTL 写入） |
| **III 可追溯与审计** | 变更可追溯；版本/审计落点 | prose 候选携 `source_ref=…#para` 溯源；Excel 富化**并回本行、不另生候选**；候选经既有复核/提交审计链；离线翻转使 `degraded` 仅在**真实降级**（云端开启但失败/空）时落点，审计信号更准 | ✅ PASS |
| **IV 测试纪律与契约优先** | 对外接口先契约后实现；关键路径契约/集成测试；quickstart 可执行；一致性门禁 | `contracts/` 四份先行（提取器协议/离线不变量/解析与富化/schema 派生）；离线零回归基线 + 优雅降级 + 富化合并 + 多值聚合 + 溯源覆盖关键路径；`quickstart.md` 场景可执行 | ✅ PASS |
| **V 最小复杂度与复用** | 复用既有栈/模式；新依赖最小化并论证；YAGNI；不引入并行框架 | `gliner_extractor.py` **逐字镜像** `semantic.py` 单例/降级范式；复用 `pipeline`/`aligner`/`vocabulary`/`align_entity`/`_compute_group_key` 整链；**唯一新依赖** `gliner`（可选 extra、缺失即降级、`torch` 在 semantic 部署中非净新增）；1 列迁移；零并行框架、零新建表 | ✅ PASS（新依赖见 Complexity Tracking 论证） |
| **安全与合规** | 凭据不入库；最小暴露；内网 | 本特性**降低**外部暴露——移除云端默认路径、运行期强制离线零外发；`anthropic_api_key` 仍经 env、默认空；模型权重不入 git、经制品库分发登记校验和；无新凭据面 | ✅ PASS（净收益） |
| **质量门禁** | DB 变更经 Alembic；启动迁移 | `ner_columns` 列经 Alembic 迁移；`main.py` 启动迁移路径不变 | ✅ PASS |

**初评结论**：无违例。唯一需论证项为新增可选依赖 `gliner`（见 Complexity Tracking），属「补 prose 召回」既定范围的必要能力，且以可选 extra + 优雅降级最小化影响。**关键风险**集中于 II 边界——「prose 候选误绕过复核」与「运行期 stray 联网」，二者在 Phase 0（R2/R7/R8）显性化、由 Phase 1 门禁/契约测试坐实。

**Phase 1 后复评**：见本文件末「Post-Design Constitution Re-Check」。

## Project Structure

### Documentation (this feature)

```text
specs/008-gliner-ner-extraction/
├── plan.md              # 本文件
├── research.md          # Phase 0：12 项决策（引擎选型/离线翻转/schema 派生/白名单字段/富化合并/Word 映射/降级单例预热/离线供给/多值/阈值/词表注入/云端去留）
├── data-model.md        # Phase 1：ner_columns 迁移 + NER schema 结构 + 富化合并语义 + prose 候选映射 + 配置/设置增项 + 降级语义表 + 离线制品
├── quickstart.md        # Phase 1：端到端验证场景（映射 US1–US3 验收与 SC-001..007）
├── contracts/           # Phase 1：
│   ├── gliner-extractor.md             # GlinerExtractor 协议（is_available / extract_text 输入输出 / 多值聚合 / 降级返回 []）
│   ├── offline-extraction-invariants.md# 离线默认零联网 + 不标 degraded + 云端 opt-in 触发门控 + 降级作业不失败
│   ├── parser-and-enrichment.md        # parse_excel(ner_columns) / parse_word(column_mapping) / _merge_ner 合并不变量
│   └── ner-schema-derivation.md        # 从 data_properties 派生 schema 契约
├── checklists/
│   └── requirements.md  # /speckit-specify 产出（已校验）
└── tasks.md             # /speckit-tasks 产出（本命令不产）
```

### Source Code (repository root)

```text
backend/
├── app/
│   ├── services/extraction/
│   │   ├── gliner_extractor.py      # 新：GlinerExtractor（本地 local_files_only 加载）+ get_gliner_extractor（@lru_cache 单例，关闭返回 None）；
│   │   │                            #     逐字镜像 semantic.py:SentenceTransformerEmbedder 的惰性加载/优雅降级/_failed 守卫范式
│   │   ├── llm_extractor.py         # 改：extract_with_fallback → 仅当 settings.llm_cloud_enabled 且有 anthropic_api_key 才调云；
│   │   │                            #     否则返回 (source_data, None)（离线正常态，不标 degraded）
│   │   ├── parser.py                # 改：parse_excel 增 ner_columns（白名单列原文暂存 __freetext__）；
│   │   │                            #     parse_word 增 column_mapping（表格行表头→IRI 确定性映射，未命中原样保留）
│   │   └── pipeline.py              # 改：_schema_from_class（从 get_class_detail().data_properties 派生）+ _merge_ner（结构化权威/仅补空缺）；
│   │                                #     Excel 富化块（抽取前 NER 并回行、strip __freetext__）+ Word 正文 GLiNER instance 候选块（#para 溯源）；
│   │                                #     parse_word(file_path, config.column_mapping) 传参
│   ├── models/extraction.py         # 改：ExtractionConfig.ner_columns: Mapped[list | None] = mapped_column(JSON)
│   ├── schemas/extraction.py        # 改：ExtractionConfigCreate/Response 增 ner_columns
│   ├── config.py                    # 改：新增 gliner_extraction_enabled=True / gliner_model_path / gliner_threshold；
│   │                                #     llm_cloud_enabled=False（云端默认关）；semantic_embedding_model 默认改本地目录（air-gap）
│   ├── main.py                      # 改：lifespan 增启动预热块（get_gliner_extractor().is_available() + get_embedder().is_available()）
│   └── alembic/versions/            # 新：迁移——extraction_configs 增 ner_columns JSON NULL 列
├── models/                          # 新（部署件，不入 git）：gliner_multi-v2.1/ 与 bge-small-zh-v1.5/ 本地权重快照
├── pyproject.toml                   # 改：[project.optional-dependencies].gliner = ["gliner>=0.2.13"]
└── tests/test_extraction/           # 新：离线不标 degraded + 云端 opt-in 门控 + GLiNER 降级 + parse_word 映射 +
                                     #     parse_excel ner_columns + _merge_ner + _schema_from_class + 多值聚合 + #para 溯源

部署件 / 运维：
└── 容器 env：HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1（+ 可选 HF_HOME 隔离缓存）；
    构建期 huggingface-cli download 预置权重 + wheelhouse 或镜像内 uv sync --extra gliner；交付清单登记校验和
```

**Structure Decision**: 沿用既有 Web（backend/frontend）结构与能力二既有抽取栈，**后端单侧**改造。新模块 `gliner_extractor.py` 与既有 `semantic.py` 同目录、同范式（可插拔协议 + `@lru_cache` 单例 + 惰性加载 + 优雅降级），把「本地模型后端」这一既有模式复制到 NER；`pipeline.py`/`parser.py`/`llm_extractor.py` 为**点状复用式改造**（不改对齐/复核主流程、不改候选 schema 形态，保护既有抽取与复核门禁零回归）。`config.py` 默认翻转 + 1 列 Alembic 迁移承载配置面。前端本期不动。

## Complexity Tracking

> 无宪章违例。下表论证唯一需说明项（新依赖），非违例。

| 决策 | 为何需要 | 更简替代被否原因 |
|------|---------|------------------|
| 新增可选依赖 `gliner`（含 torch+transformers） | 「补 prose 召回」是 US2/US3 的核心范围，需本地零样本 NER 能力；无此依赖无法在 air-gap 抽取自由文本实体 | 纯正则/规则无法泛化召回任意 prose 实体（现状即正则，召回为空）；云端 LLM 在 air-gap 不可用。置于**可选 extra**、缺失即优雅降级、`torch` 在 semantic 部署中非净新增，已将影响最小化 |

唯一「主要改动」（云端→本地默认翻转）由 air-gap 部署约束**必然要求**，且以**云端保留为 opt-in + 结构化主路径零回归**确保现路径不破。其余均为既有机制的字段/分支/单例复用，非额外复杂度来源。**零新建表（仅 1 列迁移）、零并行框架、不写 T-Box**。

## Post-Design Constitution Re-Check

Phase 1 设计完成后复评（详见 [data-model.md](./data-model.md) / [contracts/](./contracts/)）：

- **II 保真**：本特性不写 TTL/不触 World 写路径；`get_class_detail` 仅只读派生 schema（[ner-schema-derivation.md](./contracts/ner-schema-derivation.md)）；prose 候选 `review_status="pending"` 经既有复核门禁，由 [offline-extraction-invariants.md](./contracts/offline-extraction-invariants.md) 的「prose 候选不自动断言」不变量坐实。✅
- **III 审计**：prose 候选携 `#para` 溯源、Excel 富化并回本行不另生候选（[parser-and-enrichment.md](./contracts/parser-and-enrichment.md)）；`degraded_reason` 仅真实降级时非空（[offline-extraction-invariants.md](./contracts/offline-extraction-invariants.md) 降级语义表）。✅
- **IV 测试**：`contracts/` 四份先行；离线零回归 + 优雅降级 + 富化合并 + 多值 + 溯源覆盖关键路径；`quickstart.md` 场景可执行、映射 SC-001..007。✅
- **V 复用**：`gliner_extractor.py` 镜像 `semantic.py`；零新框架；唯一新依赖为可选 extra 且优雅降级；1 列迁移。✅
- **安全**：运行期强制离线零外发、移除云端默认、权重不入 git——净降低暴露面。✅

**复评结论**：设计未引入新违例，门禁全部 PASS，可进入 `/speckit-tasks`。
