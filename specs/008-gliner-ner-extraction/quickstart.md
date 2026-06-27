# Quickstart: 离线本地实体抽取（air-gap 默认 + prose 召回）

**Feature**: `008-gliner-ner-extraction` | **Plan**: [plan.md](./plan.md)

端到端验证指南——证明特性在 **air-gap（无网络、无 Key）** 下可用、补齐 prose 召回、且结构化主路径零回归。每个场景映射到用户故事（US）与成功标准（SC）。实现细节见 `tasks.md`/契约，**本文件只描述如何验证**。

---

## 0. 前置

| 项 | 要求 |
|----|------|
| 依赖 | `uv sync --extra gliner --extra semantic`（air-gap：经 wheelhouse / 镜像内执行） |
| 本地权重 | `backend/models/gliner_multi-v2.1/`、`backend/models/bge-small-zh-v1.5/` 已预置（research [R8](./research.md)） |
| 运行期 env | `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`；**不设** `ANTHROPIC_API_KEY`；`llm_cloud_enabled=False`（默认） |
| 迁移 | 启动自动应用 `ner_columns` 列迁移（`main.py:lifespan`） |
| 网络 | **断网**或 mock 一切出网调用为异常（验证零外发） |

> 契约/集成测试用**确定性桩**替换真实权重（[gliner-extractor.md](./contracts/gliner-extractor.md)「测试桩约定」），不下载模型；本 quickstart 的「真实运行」场景才用预置权重。

---

## 场景 1 — air-gap 零联网完成结构化抽取（US1，SC-001/SC-003）

**目的**：无网络、无 Key 下端到端抽取成功，且**不误标降级**。

1. 断网启动后端；确认无 `ANTHROPIC_API_KEY`、`llm_cloud_enabled=False`。
2. 创建抽取配置（Excel/Word，含 `column_mapping`，`ner_columns=null`），上传**结构化**样本，发起抽取作业。
3. **预期**：
   - 作业 `status=success`；结构化候选与改造前**逐字一致**（[offline-extraction-invariants O7](./contracts/offline-extraction-invariants.md)）。
   - **所有进度事件 `degraded=False`**、`degraded_reason is None`（O3/O5）。
   - 无任何 anthropic / HF 远程调用（O1/O10）。
   - Word 表格表头经 `column_mapping` 确定性映射为 IRI 键，**未调用 LLM**（[parser-and-enrichment P1/P2](./contracts/parser-and-enrichment.md)）。

✅ 通过 ⟺ 离线作业成功 + 零降级标记 + 零外发。

---

## 场景 2 — Word 正文 prose 实体进入复核队列（US2，SC-004）

**目的**：Word 正文段落的实体被本地 NER 召回为 `instance` 候选，带溯源、入复核。

1. 上传含**正文叙述段落**（非表格）的 Word 文档，目标类含若干 `data_properties`。
2. 发起抽取。
3. **预期**：
   - 正文段落产出 `candidate_kind="instance"` 候选，`properties` 为本体属性 IRI 键（经 `_schema_from_class` 派生 + `tag_controlled_vocab`，[ner-schema-derivation S3](./contracts/ner-schema-derivation.md)）。
   - 候选 `source_ref` 含 `#para`（溯源回链，宪章 III）。
   - 候选 `review_status="pending"`——**入复核队列、不自动断言**（宪章 II，[data-model §3.3](./data-model.md)）。
   - 同段落原有 `parse_action_from_text` 的 Action 候选**仍并存**。
   - 同一标签多命中聚合为 list（[gliner-extractor C5](./contracts/gliner-extractor.md)）。

✅ 通过 ⟺ prose 实体可见于复核队列 + 带 `#para` 溯源 + 复核门禁未被绕过。

---

## 场景 3 — Excel 自由文本列回填本行（US3，SC-005）

**目的**：白名单自由文本列经 NER 富化**并回本行**，结构化列权威、不另生候选。

1. 配置 `ner_columns=["备注"]`（或样本中的自由文本列表头），上传含该列的 Excel。
2. 让某行：结构化列 A 已有值，结构化列 B 留空，且 B 的实体出现在「备注」自由文本里。
3. 发起抽取。
4. **预期**：
   - 列 A 保留结构化原值——NER **不覆盖**（[parser-and-enrichment P8](./contracts/parser-and-enrichment.md)）。
   - 列 B 被「备注」中抽出的实体**补齐**（仅补空缺，P9）。
   - 候选数 = 行数——富化**不新增候选**（P10）。
   - 候选 `properties` 不含 `__freetext__` 临时键（P11）。

✅ 通过 ⟺ 空缺被富化 + 结构化值不被覆盖 + 候选数不变。

---

## 场景 4 — NER 不可用优雅降级（FR-012，SC-006）

**目的**：缺包/缺权重时作业**不失败**，退回结构化兜底。

1. 临时令 NER 不可用（设 `gliner_extraction_enabled=False`，或移走 `backend/models/gliner_multi-v2.1/`）。
2. 重跑场景 2、3 的输入。
3. **预期**：
   - 作业 `status=success`（[offline-extraction-invariants O6](./contracts/offline-extraction-invariants.md)）。
   - prose 候选为空、Excel 不富化；结构化候选与场景 1 一致（O7）。
   - 日志含 `WARNING`，但**不**标 `degraded`、不报错给用户（O8）。
   - `__freetext__` 临时键仍被清除（[parser-and-enrichment P12](./contracts/parser-and-enrichment.md)）。

✅ 通过 ⟺ NER 缺失时作业照常成功 + 结构化零回归 + 仅 WARNING。

---

## 场景 5 — 云端 LLM opt-in 不影响离线默认（FR-002，SC-002）

**目的**：云端默认关、显式开启才触发；离线默认永不调用云端。

1. 默认（`llm_cloud_enabled=False`）跑任一作业 → 断言**零** anthropic 调用（[O1](./contracts/offline-extraction-invariants.md)）。
2. 设 `llm_cloud_enabled=True` 但**不给 Key** → 仍不触发云端，回退结构化，`degraded_reason` 非空（配置缺失说明，O2/降级表）。
3. （仅非 air-gap 环境）`llm_cloud_enabled=True` + 有 Key → 触发云端补充。

✅ 通过 ⟺ 双条件门控正确 + 离线默认零云端调用。

---

## 场景 6 — 启动预热消除首作业冷启动（research [R7](./research.md)，SC-007）

**目的**：模型在 `lifespan` 预加载，首作业无加载等待。

1. 启动后端，观察 lifespan 日志含 GLiNER + embedder 预热（`is_available()` 触发加载）。
2. 启动后立即发起首个抽取作业。
3. **预期**：首作业**无**模型加载耗时尖峰（加载已在启动期完成）；air-gap 下加载为纯本地 I/O、秒级。

✅ 通过 ⟺ 首作业延迟与后续作业相当（无冷启动惩罚）。

---

## 验收矩阵

| 场景 | US | 关联 SC | 关键不变量 |
|------|----|---------|-----------|
| 1 离线结构化 | US1 | SC-001/003 | O1/O3/O5/O7/O10、P1/P2 |
| 2 Word prose | US2 | SC-004 | S3、C5、`#para`、`pending` |
| 3 Excel 富化 | US3 | SC-005 | P8/P9/P10/P11 |
| 4 优雅降级 | — | SC-006 | O6/O7/O8、P12 |
| 5 云端 opt-in | US1 | SC-002 | O1/O2、降级表 |
| 6 启动预热 | — | SC-007 | R7 预热块 |

> SC-004 / SC-005 的**目标数值**（prose 召回率 / 富化命中率）依赖真实中文样本标定（research [R10](./research.md)），标定后回填规范；本 quickstart 验证**行为正确性**，数值阈值在标定阶段单独验收。
