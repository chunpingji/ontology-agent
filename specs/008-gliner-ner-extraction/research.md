# Phase 0 Research: 离线本地实体抽取（air-gap 默认 + prose 召回）

**Feature**: `008-gliner-ner-extraction` | **Date**: 2026-06-26 | **Plan**: [plan.md](./plan.md)

本文件汇总规划阶段需拍板的技术决策。规范层（[spec.md](./spec.md)）无遗留 `[NEEDS CLARIFICATION]`（5 条澄清已在 specify 阶段据设计文档敲定）；以下为**实现取向**决策，逐项核验过现网代码，确保「薄接缝复用」（宪章 V）。源设计：[`docs/NER by GliNER.md`](../../docs/NER%20by%20GliNER.md)。

---

## R1 — 本地 NER 引擎选型

- **Decision**：选 **GLiNER**（`urchade/gliner_multi-v2.1`），调用 `model.predict_entities(text, labels, threshold=…)`，经 `GLiNER.from_pretrained(local_path, local_files_only=True)` 本地加载。
- **Rationale**：
  - **中文是硬约束**（Word/Excel 自由文本为中文）——`gliner_multi-v2.1` 多语权重覆盖中文；纯英文/biomed 权重不达标。
  - **零样本、标签驱动**——抽哪些字段在运行时由目标类属性 label 决定（见 R3），无需为每个本体类微调，契合「本体即配置」。
  - **CPU 可跑、单文件权重快照**——契合 air-gap 制品预置（见 R8）。
  - **可插拔**——`predict_entities` 输入输出形态与既有 `Embedder` 协议同构，便于镜像 `semantic.py` 单例/降级（R7）。
- **Alternatives considered**：
  - *GLiNER2 + `choices`*：受控词表能力更强，但当前以 `tag_controlled_vocab` span 后处理满足（R11），暂不引入第二套接口；接口预留。
  - *云端 LLM（现状）*：air-gap 不可达，正是本特性要去除的默认依赖。
  - *spaCy/HanLP 预训练 NER*：标签集固定、非零样本，无法跟随本体类属性动态变化，召回面窄。

## R2 — 云端 LLM：默认关 + 离线非降级（US1 / FR-002 / FR-003）

- **Decision**：`llm_extractor.py:extract_with_fallback` 的门控由 `if not settings.anthropic_api_key` 改为
  **`if settings.llm_cloud_enabled and settings.anthropic_api_key:` 才走云端**，否则返回 `(source_data, None)`——`degraded_reason` 置 **None**。新增设置 `llm_cloud_enabled: bool = False`（默认关）。
- **Rationale**：air-gap 离线是**正常运行态**而非降级；旧逻辑「无 Key 即标 degraded」会在内网误报降级、污染审计信号与 SC。云端保留为 **opt-in**（显式开 + 有 Key 才触发），满足「保留但默认关」澄清。`degraded` 仅在「云端已开启却调用失败/返回空」等**真实降级**时落点（见 [contracts/offline-extraction-invariants.md](./contracts/offline-extraction-invariants.md) 降级语义表）。
- **Alternatives considered**：
  - *彻底删除 anthropic 依赖*：见 R12，涉及合规决策，留后续；本期以默认关 + 强制离线达成等效安全收益，零破坏。
  - *用环境探测网络可达性自动切换*：引入隐式行为与 stray 联网风险，违背「显式 opt-in」，否。

## R3 — NER 标签集从目标本体类属性派生（FR-013）

- **Decision**：新增 `pipeline.py:_schema_from_class(target_class_iri) -> (labels, label_to_iri)`：调用 `ontology_engine.get_class_detail(iri).data_properties`（每项 `{iri, name, label, range}`，**已核验**返回 `ClassInfo`），取 `label`（缺省回退 `name`）作 GLiNER 标签，建立 `label -> iri` 映射用于回填属性键。
- **Rationale**：实现「本体即抽取配置」——抽什么字段随本体类演进自动更新，无需另维护标签表（宪章 V 复用、II 本体权威）。**只读** `get_class_detail`，不触 World 写路径，保真风险面为零。
- **Alternatives considered**：
  - *配置中手填标签集*：与本体易漂移、双维护，否。
  - *用 `name` 而非 `label`*：`label` 更贴近自然语言、利于零样本命中；`name` 仅作回退。

## R4 — 自由文本列声明：独立白名单字段 `ner_columns`（US3 / FR-007）

- **Decision**：`ExtractionConfig` 增 `ner_columns: Mapped[list | None] = mapped_column(JSON)`（源表头字符串列表），经 **Alembic 迁移**新增列；`schemas/extraction.py` 的 `ExtractionConfigCreate/Response` 同步增 `ner_columns: list[str] | None = None`。
- **Rationale**：与「IRI 映射」职责正交的「该列是否走 NER 富化」用**独立字段**表达，比在 `column_mapping` 里塞哨兵值更清晰、可独立演进、不污染既有映射语义（宪章 V 最小复杂度）。DB 变更经 Alembic（宪章质量门禁）。
- **Alternatives considered**：
  - *`column_mapping` 内哨兵值（如映射到特殊 IRI）*：语义重载、易误用，否。
  - *全局开关「所有自由文本列都抽」*：召回噪声大、不可控；按列 opt-in 更稳。

## R5 — Excel 富化合并语义：结构化列权威、仅补空缺（FR-008 / FR-009）

- **Decision**：`parse_excel` 对 `ner_columns` 命中的列，把原文暂存到行内 `__freetext__`（不直接当属性值）；`pipeline` 在抽取前对每行 `__freetext__` 跑 `gliner.extract_text`，经新 `_merge_ner(row, ner_props)` **并回本行**——**仅填充结构化映射未提供（空缺）的属性键**，结构化列已有值则保留；合并后 `pop("__freetext__")`。**不产生新候选**。
- **Rationale**：满足「结构化列权威、NER 仅富化空缺、自由文本列回填本行不另生实例」澄清；保持 Excel 一行一候选的既有形态，复核负担零增。
- **Alternatives considered**：
  - *NER 值覆盖结构化值*：违背「结构化权威」，否。
  - *自由文本另生候选*：与 US3「富化本行」语义冲突（那是 US2/Word 的语义），否。

## R6 — Word 表格表头→IRI：确定性映射替代云端（US1 / FR-004）

- **Decision**：`parse_word` 增 `column_mapping: dict | None` 参数，表格行从「以**原始表头文本**为键」改为「表头经 `column_mapping` 命中则以 **IRI** 为键，未命中原样保留」，与 `parse_excel` 行为一致；`pipeline` 调 `parse_word(file_path, config.column_mapping)`。
- **Rationale**：Word 表格此前是**唯一**依赖云端 LLM 做表头→属性映射的环节；用既有 `column_mapping` 确定性完成后，结构化主路径在 air-gap 完全自洽、零回归。
- **Alternatives considered**：
  - *保留 LLM 映射 Word 表头*：air-gap 不可用，否。
  - *Word 与 Excel 各用一套映射约定*：徒增复杂度，复用同一 `column_mapping` 更简。

## R7 — 优雅降级 + 进程级单例 + 启动预热 + 同步推理隔离（FR-012）

- **Decision**：`gliner_extractor.py` **逐字镜像** `semantic.py`：
  - `GlinerExtractor`：`_ensure_model()` try-import + `from_pretrained(path, local_files_only=True)`，失败置 `_failed=True` 记 WARNING；`is_available()`；`extract_text(text, labels, threshold)`。
  - `@lru_cache(maxsize=1) get_gliner_extractor()`：`settings.gliner_extraction_enabled` 关闭时直接返回 `None`。
  - pipeline 侧守卫：`ex = get_gliner_extractor(); if ex and ex.is_available(): …`，否则**静默跳过 prose/富化、走结构化兜底、作业不失败**。
  - GLiNER 推理为 CPU 同步阻塞 → 经 **`asyncio.to_thread`** 调用，不阻塞事件循环。
  - `main.py:lifespan` 增**启动预热块**：`get_gliner_extractor() and .is_available()` + `get_embedder() and .is_available()`，把（air-gap 下纯本地、秒级的）模型加载前移，消除首作业冷启动等待。
- **Rationale**：直接复用现网已验证范式（`semantic.py`/`extract_with_fallback`），降级行为与现有语义对齐、零认知负担；预热把加载移出请求路径。
- **Alternatives considered**：
  - *每次请求新建模型*：重复加载、慢且耗内存，否（既有 `@lru_cache` 范式已解决）。
  - *子进程/进程池跑推理*：小并发内网无必要，徒增运维面（违 YAGNI/V）。

## R8 — 离线模型供给与强制离线（FR-001 / FR-011 / 安全）

- **Decision**：
  - **构建期**预置权重：`huggingface-cli download urchade/gliner_multi-v2.1 → backend/models/gliner_multi-v2.1/` 与 `BAAI/bge-small-zh-v1.5 → backend/models/bge-small-zh-v1.5/`（后者供既有 semantic 对齐离线化）。
  - **运行期强制离线双保险**：容器 env `HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1`，且加载一律 `local_files_only=True`。
  - **设置默认改本地路径**：新增 `gliner_model_path="models/gliner_multi-v2.1"`；`semantic_embedding_model` 默认由 `BAAI/bge-small-zh-v1.5` 改为本地目录 `models/bge-small-zh-v1.5`（air-gap 下不可触发联网下载）。
  - **依赖供给**：`gliner` 经 wheelhouse 或镜像内 `uv sync --extra gliner` 安装（air-gap 无 PyPI）。
  - **权重不入 git**：经制品库/镜像分发，交付清单登记 **SHA256 校验和**（宪章 安全）。
- **Rationale**：双保险确保即便代码漏传 `local_files_only` 也不会 stray 联网；本地路径默认杜绝运行期解析远程 repo id。
- **Alternatives considered**：
  - *运行期首次联网下载并缓存*：air-gap 直接失败、且违「零外发」约束，否。
  - *把权重打进 git*：仓库膨胀、违安全约束，否。

## R9 — 同标签多命中聚合为列表（FR-014）

- **Decision**：`GlinerExtractor.extract_text` 内把 `predict_entities` 结果按 `label` 聚合：单命中 → 标量，多命中 → `list`，返回 `{label: value|[values]}`，再经 `label_to_iri` 回填到 IRI 键。
- **Rationale**：一段文本可含同类多个实体（如多种物料）；保留全部命中避免召回损失。下游 `align_entity`/候选已能承载列表属性（与既有多值属性一致）。
- **Alternatives considered**：*只取首个/最高分*：丢召回，违 US2 召回目标，否。

## R10 — 阈值标定（SC-004 / SC-005）

- **Decision**：`gliner_threshold` 默认 **0.5**；prose 召回偏低时下调至 **0.3–0.4**。以**真实中文样本**标注 P/R 后定稿；SC-004/SC-005 的目标数值在标定后填入（规范已显式标注「标定后确定」，不臆造）。
- **Rationale**：阈值是召回/精确权衡的运营旋钮，须用真实分布标定；设为可配置（`settings.gliner_threshold`）便于免改码调参。
- **Alternatives considered**：*硬编码阈值*：不可运营调参，否。

## R11 — 受控词表注入沿用 span 后处理（复用 vocabulary.py）

- **Decision**：prose/富化抽出的属性，复用既有 `tag_controlled_vocab(properties)` 做受控词表标准化（oeb/cleanliness_grade/material/pde_unit），不切换到 GLiNER2 `choices`。
- **Rationale**：现有后处理已覆盖受控字段、与结构化路径一致；零新接口（宪章 V）。
- **Alternatives considered**：*GLiNER2 choices 内建约束*：需引第二套引擎接口，收益不抵复杂度，留作 R1 的未来预留。

## R12 — 云端 LLM 去留（开放项，本期不决）

- **Decision**：**本期保留** anthropic 依赖与 `extract_entities_with_llm` 路径，仅默认关（R2）。是否彻底移除留作后续合规/采购决策。
- **Rationale**：保留 opt-in 不增 air-gap 风险（默认关 + 强制离线），又为有外网的非 air-gap 部署留弹性；彻底删除是不可逆破坏性变更，不在本特性范围。
- **Alternatives considered**：*本期删除*：超范围、不可逆，否。

---

## 决策汇总

| # | 决策 | 落点 | 关联 |
|---|------|------|------|
| R1 | GLiNER `gliner_multi-v2.1` + `predict_entities` 本地加载 | `gliner_extractor.py` | FR-001/013 |
| R2 | 云端默认关 + 离线非降级；新增 `llm_cloud_enabled=False` | `llm_extractor.py` / `config.py` | FR-002/003 |
| R3 | NER 标签从 `get_class_detail().data_properties` 派生 | `pipeline.py:_schema_from_class` | FR-013 |
| R4 | 独立白名单字段 `ner_columns`（JSON，Alembic 迁移） | `models/`+`schemas/extraction.py`+迁移 | FR-007 |
| R5 | 富化合并：结构化权威、仅补空缺、不另生候选 | `parser.py`+`pipeline.py:_merge_ner` | FR-008/009 |
| R6 | Word 表头→IRI 确定性映射（`parse_word` 增 `column_mapping`） | `parser.py`+`pipeline.py` | FR-004 |
| R7 | 优雅降级 + `@lru_cache` 单例 + 启动预热 + `to_thread` | `gliner_extractor.py`+`main.py` | FR-012 |
| R8 | 离线供给：预置权重 + `HF_*_OFFLINE`/`local_files_only` + 校验和 | env/制品/`config.py` | FR-001/011 |
| R9 | 同标签多命中聚合为 list | `gliner_extractor.py` | FR-014 |
| R10 | 阈值 0.5（可调），真实样本标定 SC-004/005 | `config.py:gliner_threshold` | SC-004/005 |
| R11 | 受控词表沿用 `tag_controlled_vocab` 后处理 | `pipeline.py` | 复用 |
| R12 | 云端本期保留 opt-in，彻底去留留后续 | — | 开放项 |

**所有 NEEDS CLARIFICATION 已解决** → 进入 Phase 1 设计。
