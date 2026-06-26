# NER by GLiNER — 离线本地实体抽取集成设计 (v0.2)

> **部署约束（v0.2 核心变更）**：系统部署到**无互联网环境（air-gap）**，
> **不可访问云端大模型（Claude）**。因此抽取引擎**默认使用 GLiNER 类本地部署
> 模型**；云端 LLM 降级为 opt-in（默认关闭，仅在有网络+Key 的环境显式开启）。
>
> 范围：在「实体抽取」阶段以 **GLiNER**（零样本、标签驱动的轻量 NER，本地运行）
> 作为默认抽取能力，覆盖：
> 1. **Word 正文段落** —— 从 prose 抽「新实体」候选（当前仅正则抓强制句）；
> 2. **Excel 自由文本列** —— 从单元格 prose 抽「本行漏掉的属性」并回行记录。
>
> 结构化主路径（Excel 列映射 / Word 表格 / DB 反射）本就是**确定性、离线**的，
> air-gap 不破；本方案补齐 prose 召回，并把整条链路从「云端默认」翻转为「本地默认」。
>
> 关联既有：`services/extraction/{pipeline,llm_extractor,parser,vocabulary,aligner,semantic}.py`、
> `services/ontology_engine.py`（取 schema）、`config.py`。
>
> 姊妹方案：[`ner-medical-bert-extraction.md`](./ner-medical-bert-extraction.md)
> 走「领域 BERT + 微调」路线；本方案走「GLiNER 零样本、即插即用」路线，二者
> 互补，可分阶段并存（见 §12）。

---

## 1. 背景与动机

当前抽取流水线 `pipeline.run_extraction_pipeline` 的实体召回与**联网依赖**现状：

| 源类型 | 现状 | 联网依赖 | prose 实体召回 |
|--------|------|---------|----------------|
| Excel | 列→属性 IRI 映射（`parse_excel`）+ LLM 结构化 | **Claude（可去除）** | 结构化列强；自由文本列丢失 |
| Word 表格 | 表行→`raw_rows`→LLM | **Claude（表头→IRI 映射）** | 结构化行，强 |
| **Word 正文段落** | **仅**正则 `parse_action_from_text` | 无 | **空：prose 实体不进候选** |
| 数据库 | 结构反射→class/link 候选 | 无 | 结构，强 |

**air-gap 下的两类问题：**

1. **联网依赖必须清除**：`extract_with_fallback` 当前调用 Claude 云 API。无网络/无 Key
   时它已能回退到原始结构化行——但对 Excel 而言原始行已是 IRI 键控的**正确结果**，
   对 Word 表格则缺少表头→IRI 映射。需把云端从**默认路径**移除。
2. **prose 实体缺口**：SOP/方案正文与 Excel 备注列里的实体目前不进审核队列。

**目标（v0.2）：**
- 抽取引擎**默认全本地**：结构化源走确定性映射，prose 走本地 GLiNER，**零联网**。
- 云端 LLM 改为 opt-in 增强（默认 `False`），air-gap 下永不触发。
- 给出**离线模型供给**方案（§8），确保权重/依赖在无网络部署时可用。

---

## 2. 核心认知：GLiNER 的适用边界

**GLiNER 不是通用结构化抽取器，而是对自由文本的 span 级 NER。** 输入 text + 一组
标签，输出命中的实体片段 `{text, label, score, start, end}`。由此：

| 场景 | 是否用 GLiNER | 离线下如何处理 |
|------|---------------|----------------|
| Excel / Word **表格**（已结构化） | ❌ 不用 | **确定性 `column_mapping`**（无模型，本就离线） |
| Word **正文段落**（prose） | ✅ 用 | 本地 GLiNER → 新实体候选 |
| Excel **自由文本列**（备注/描述） | ✅ 用（按列 opt-in） | 本地 GLiNER → 并回本行 |
| 数据库 | ❌ 不用 | 结构反射（本就离线） |

**两种并入策略（最本质的区别）：**

- **Word 正文**：一段 prose ≈ 一个实体 → 产出**新 instance 候选**。
- **Excel 自由文本列**：一行已经是一个实体 → NER 结果**并回本行记录**（富化），
  **不产生新候选**。

> 推论：air-gap 真正"缺失"的只有 Claude 对 **Word 表格行（中文表头）→IRI** 的映射。
> 用确定性 `column_mapping`（Word 表格也支持）即可替代，无需任何模型（见 §6.0）。

---

## 3. 选型：GLiNER vs GLiNER2 vs 模型权重

| | 原版 GLiNER (`urchade/gliner`) | GLiNER2 (`fastino-ai/gliner2`) |
|---|---|---|
| 接口 | `predict_entities(text, labels, threshold)` → span 列表 | `extract_json(text, {结构: 字段})` → 结构化 JSON |
| 能力 | 纯 NER | NER + 分类 + 关系 + 结构化抽取（205M, CPU） |
| 多语 | `gliner_multi-v2.1` 多语 ✅ | 以**英文**为主 |
| 离线 | `from_pretrained(本地目录, local_files_only=True)` ✅ | 同左 ✅ |

**权重取舍（中文是硬约束）：** 内容为中文药企文本（活性成分/剂型/规格/洁净级别），
而 GLiNER2 与英文 biomed 权重（`gliner_large_bio-v0.1`）基本是英文域。

**结论：** 默认 **`urchade/gliner_multi-v2.1`（多语）+ `predict_entities`**（span 抽取
再聚成记录），**本地目录加载**。GLiNER2 / 英文 biomed 权重留作未来切换（接口已预留）。

---

## 4. 总体架构（离线优先 / local-first）

```
   Excel ──parse_excel(column_mapping)──► 结构化行 ──┐
                                                     ├─(自由文本列)─► GLiNER 本地 NER ─► 并回行(富化)
   Word 表格 ──parse_word(column_mapping)──► 结构化行 ┘
                                                              │
   Word 正文 ──parse_word──► 段落 ─► parse_action(Action) ────┤
                                  └► GLiNER 本地 NER ─► 新 instance 候选
                                                              ▼
                              (opt-in 云 LLM 增强，默认关) → align_entity → 候选入库
```

**抽取阶梯（默认全离线）：**

| 层 | 引擎 | 默认 | 联网 |
|----|------|------|------|
| 1 结构化确定性 | `column_mapping` / 表格映射 / DB 反射 | ✅ 开 | ❌ |
| 2 本地 NER | GLiNER（`gliner_multi-v2.1`，本地权重） | ✅ 开 | ❌ |
| 3 云 LLM 增强 | Claude | ❌ 关（opt-in） | ✅ 需网络+Key |
| 4 兜底 | 原样结构化行 | ✅ 始终 | ❌ |

设计原则（沿用 `semantic.py` 范式）：

- **离线优先**：层 1–2 + 层 4 构成完整离线链路；层 3 缺席不影响产出。
- **可插拔 + 惰性加载 + 优雅降级**：GLiNER 依赖缺失/加载失败 `is_available()=False`，
  静默跳过到层 4，作业不失败。
- **进程级单例 + 启动预热**：`@lru_cache get_gliner_extractor()`；启动时预热避免
  首作业冷启动（air-gap 下无下载，仅本地 I/O）。
- **同步模型丢线程池**：`asyncio.to_thread(...)` 包裹推理，不阻塞事件循环。

---

## 5. Word 正文抽取

数据源：`parse_word` 中 `type == "paragraph"` 的段落。每段 prose 视为一个潜在
实体，产出 **instance 候选**，`source_ref` 落 `#para`（贴合 007 文档事实源溯源）。

```python
import asyncio
from app.services.extraction.gliner_extractor import get_gliner_extractor

gliner = get_gliner_extractor()
para_schema = _schema_from_class(engine, config.target_class_iri)

if word_sections:
    for sec in word_sections:
        if sec.get("type") != "paragraph":
            continue
        content = sec.get("content", "")

        # (a) 条件式「若…则…必须…」→ Action 候选（原有，不变）
        action = parse_action_from_text(content)
        if action:
            db.add(ExtractionCandidate(..., candidate_kind="action", ...))
            total += 1

        # (b) 正文实体 → instance 候选（GLiNER 本地抽取，新增）
        if gliner and gliner.is_available() and para_schema:
            for ent in await asyncio.to_thread(gliner.extract_text, content, para_schema):
                props = tag_controlled_vocab(dict(ent))
                alignment = align_entity(
                    candidate=props, target_class_iri=config.target_class_iri,
                    engine=engine, id_property=id_prop, label_property=label_prop,
                    threshold=settings.lexical_match_threshold, embedder=embedder,
                    semantic_threshold=settings.semantic_match_threshold,
                )
                db.add(ExtractionCandidate(
                    job_id=job.id,
                    target_class_iri=config.target_class_iri,
                    extracted_properties=props,
                    candidate_kind="instance",
                    group_key=_compute_group_key(props, config.target_class_iri, id_prop),
                    source_ref=f"{source_ref}#para",
                    alignment_result=alignment.action,
                    aligned_iri=alignment.match_iri,
                    match_score=alignment.match_score,
                    review_status="pending",
                ))
                total += 1
```

复用既有 `tag_controlled_vocab` + `align_entity` + `_compute_group_key`，无新增对齐逻辑。

---

## 6. Excel NER（单元格级富化）

**定位：单元格级、按列 opt-in、抽取结果并回行记录（不产生新候选）。** 结构化列
权威，NER 仅补未填属性。

### 6.0 前置：Word 表格行的确定性映射（替代 Claude）

air-gap 下不再用 Claude 把中文表头映射到 IRI。`parse_word` 的表格行改为接受
`column_mapping`（与 `parse_excel` 一致），表头→IRI 确定性映射，无模型：

```python
def parse_word(file_path, column_mapping=None):
    ...
    cmap = column_mapping or {}
    for table in doc.tables:
        headers = [c.text.strip() for c in table.rows[0].cells]
        for row in table.rows[1:]:
            row_data = {}
            for idx, cell in enumerate(row.cells):
                h = headers[idx] if idx < len(headers) else ""
                key = cmap.get(h, h)          # 命中映射→IRI，否则保留表头
                if h:
                    row_data[key] = cell.text.strip()
            if row_data:
                sections.append({"type": "table_row", "content": row_data})
    ...
```

### 6.1 三类列

| 列类型 | 例子 | 处理 |
|--------|------|------|
| 结构化列（已映射） | `设备编号`、`OEB等级` | `column_mapping` 直出，**不做 NER** |
| 自由文本列（prose） | `工艺描述`、`备注`、`变更说明` | **NER 目标** |
| 未映射杂项列 | 表头不在映射里 | 默认丢弃，或纳入白名单 |

### 6.2 三步落地

**① 配置：声明自由文本列白名单**

```python
# models/extraction.py & schemas/extraction.py
ner_columns: Mapped[list | None] = mapped_column(JSON)   # Excel 自由文本列白名单
```

**② `parse_excel`：保留白名单列原文**

```python
def parse_excel(file_path, column_mapping, ner_columns=None, ...):
    ...
    ner_idx = {i for i, h in enumerate(headers) if h in (ner_columns or [])}
    for row in rows[1:]:
        values = [c.value for c in row]
        if not any(values):
            continue
        entity = {}
        for col_idx, prop_iri in col_to_prop.items():        # 结构化列 → IRI 键
            val = values[col_idx] if col_idx < len(values) else None
            if val is not None:
                entity[prop_iri] = val
        parts = [f"{headers[i]}：{values[i]}"                 # 自由文本列 → 暂存原文
                 for i in ner_idx if i < len(values) and values[i]]
        if parts:
            entity["__freetext__"] = "\n".join(parts)
        if entity:
            results.append(entity)
```

**③ `pipeline.py`：抽取前 NER 富化，再 strip 掉原文**

```python
def _merge_ner(structured: dict, ner_records: list[dict]) -> dict:
    """NER 结果并入结构化行；结构化列权威，NER 仅补未填属性。"""
    merged = dict(structured)
    for rec in ner_records:
        for iri, val in rec.items():
            if iri not in merged or merged[iri] in (None, ""):
                merged[iri] = val
    return merged

# --- excel 分支 ---
raw_rows = parse_excel(file_path, column_mapping=config.column_mapping or {},
                       ner_columns=config.ner_columns)
gliner = get_gliner_extractor()
if gliner and gliner.is_available() and config.ner_columns:
    schema = _schema_from_class(engine, config.target_class_iri)
    enriched = []
    for row in raw_rows:
        text = row.pop("__freetext__", "")
        ner = await asyncio.to_thread(gliner.extract_text, text, schema) if text else []
        enriched.append(_merge_ner(row, ner))
    raw_rows = enriched
else:
    for row in raw_rows:
        row.pop("__freetext__", None)
```

---

## 7. 新增模块 `services/extraction/gliner_extractor.py`（离线加载）

```python
"""本地零样本 NER 后端：GLiNER（air-gap，本地权重，无需 API Key / 联网）。

定位：对自由文本（Word 正文 / Excel 自由文本列）做本地实体抽取。可插拔 +
惰性加载 + 优雅降级，沿用 semantic.py / extract_with_fallback 的思路（FR-007/R3）。

离线要点：from_pretrained(本地目录, local_files_only=True)，配合 HF_HUB_OFFLINE=1 /
TRANSFORMERS_OFFLINE=1（§8）杜绝任何网络访问（否则无网络时会卡在下载重试）。
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


class GlinerExtractor:
    def __init__(self, model_path: str) -> None:
        self._model_path = model_path
        self._model = None
        self._failed = False

    def _ensure_model(self):
        if self._model is not None or self._failed:
            return self._model
        try:
            from gliner import GLiNER
            path = Path(self._model_path)
            if not path.is_absolute():
                path = Path(__file__).resolve().parents[3] / self._model_path  # backend/
            logger.info("加载本地 GLiNER 模型：%s", path)
            self._model = GLiNER.from_pretrained(str(path), local_files_only=True)
        except Exception:  # pragma: no cover - 依赖缺失/权重缺失路径
            logger.warning(
                "GLiNER 不可用（未安装、本地权重缺失或加载失败）；本地 NER 跳过，"
                "结构化主路径不受影响。air-gap 部署请先按 §8 预置权重并装 --extra gliner。",
                exc_info=True,
            )
            self._failed = True
        return self._model

    def is_available(self) -> bool:
        return self._ensure_model() is not None

    def extract_text(self, text: str, schema: list[dict]) -> list[dict[str, Any]]:
        """一段文本 → IRI 键控记录列表；同标签多次命中聚成 list。

        Word 正文：一段 → 一条记录（一个实体）。
        Excel 自由文本列：返回的记录交由 _merge_ner 并回结构化行。
        """
        model = self._ensure_model()
        if model is None or not text or not schema:
            return []
        labels = [p["name"] for p in schema if p.get("name")]
        label_to_iri = {p["name"]: p["iri"] for p in schema if p.get("name")}
        try:
            spans = model.predict_entities(
                text, labels, threshold=settings.gliner_threshold
            )
        except Exception:  # pragma: no cover
            logger.warning("GLiNER 抽取失败", exc_info=True)
            return []
        record: dict[str, Any] = {}
        for s in spans:
            iri = label_to_iri.get(s["label"], s["label"])
            if iri in record:
                prev = record[iri] if isinstance(record[iri], list) else [record[iri]]
                record[iri] = prev + [s["text"]]
            else:
                record[iri] = s["text"]
        return [record] if record else []


@lru_cache(maxsize=1)
def get_gliner_extractor() -> "GlinerExtractor | None":
    """进程级单例；关闭时返回 None（零开销，惰性加载）。"""
    if not settings.gliner_extraction_enabled:
        return None
    return GlinerExtractor(settings.gliner_model_path)
```

**`_schema_from_class`（填上 `property_schema=[]` 那个洞）：**

```python
def _schema_from_class(engine, class_iri: str) -> list[dict]:
    """从本体类的 data_properties 派生 NER schema（属性 label 作标签）。"""
    detail = engine.get_class_detail(class_iri)
    if not detail:
        return []
    return [
        {"iri": p["iri"], "name": p.get("label") or p.get("name", "")}
        for p in detail.data_properties
    ]
```

**`extract_with_fallback` 改造（云端 opt-in，离线为正常态）：**

```python
async def extract_with_fallback(source_data, target_class_iri, property_schema, ...):
    # air-gap 默认：不调用云 LLM。结构化源已是 IRI 键控的正确结果，prose 另由 GLiNER 处理。
    if not (settings.llm_cloud_enabled and settings.anthropic_api_key):
        return source_data, None          # 不再标记 degraded —— 本地模式是正常态
    try:
        entities = await extract_entities_with_llm(...)
        return (entities or source_data), (None if entities else "云 LLM 返回为空，回退结构化")
    except Exception as exc:               # pragma: no cover
        return source_data, f"云 LLM 调用失败，回退结构化：{type(exc).__name__}"
```

> 关键：无 Key/无网络在 air-gap 下是**正常态**，`degraded_reason` 置 `None`，避免每条
> 候选都被误标降级、SSE 误报 `degraded=true`。

---

## 8. 离线模型供给（air-gap 必读）

无互联网部署时，**权重不能在运行时从 HuggingFace 下载**，必须在构建期（有网络的
CI/打包机）预置，运行期纯本地加载。涉及**两个**本地模型：

| 模型 | HF ID | 用途 | 本地目录 |
|------|-------|------|----------|
| GLiNER 多语 | `urchade/gliner_multi-v2.1` | 本方案 NER | `backend/models/gliner_multi-v2.1/` |
| 中文嵌入 | `BAAI/bge-small-zh-v1.5` | 语义对齐（已用） | `backend/models/bge-small-zh-v1.5/` |

### 8.1 构建期下载（有网络）

```bash
# 在 CI/打包机执行；产物随部署件一起分发
pip install "huggingface_hub[cli]"
huggingface-cli download urchade/gliner_multi-v2.1 \
    --local-dir backend/models/gliner_multi-v2.1
huggingface-cli download BAAI/bge-small-zh-v1.5 \
    --local-dir backend/models/bge-small-zh-v1.5
```

> 目录需包含完整快照：`config.json`、`pytorch_model.bin`/`model.safetensors`、
> `tokenizer*`、GLiNER 的 `gliner_config.json` 等。权重较大，**不入 git**——经制品库 /
> 部署件 / git-LFS 分发，并在交付清单登记校验和。

### 8.2 运行期强制离线（防止卡在下载重试）

容器/服务环境变量（双保险，任何 stray `from_pretrained` 都不尝试联网）：

```bash
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_HOME=/app/backend/models/.hf-cache   # 可选：隔离缓存目录
```

代码侧已用 `local_files_only=True`（§7），与上述 env 形成双重离线保证。

### 8.3 Python 依赖离线安装

`gliner` 依赖 `torch` + `transformers`，air-gap 下需提前备好 wheel：

```bash
# 构建期：导出 wheelhouse（CPU 版 torch 以控体积）
pip download gliner torch --dest wheelhouse \
    --extra-index-url https://download.pytorch.org/whl/cpu
# 部署期：离线安装
uv pip install --no-index --find-links wheelhouse gliner torch
```

或：构建期在镜像内完成 `uv sync --extra gliner`，直接分发镜像（推荐，最省事）。

### 8.4 启动预热

应用启动钩子调用一次 `get_gliner_extractor().is_available()` 与
`get_embedder().is_available()`，把首作业的加载延迟前移；air-gap 下无下载，仅本地
I/O，秒级完成。缺权重时此处即记 WARNING，便于部署自检。

---

## 9. 配置与依赖

**`config.py`（默认翻转为离线本地）：**

```python
# ── 抽取引擎：离线优先，默认本地 GLiNER（air-gap，无需联网/API Key）──
gliner_extraction_enabled: bool = True
gliner_model_path: str = "models/gliner_multi-v2.1"   # 本地权重目录（相对 backend/）
gliner_threshold: float = 0.5

# ── 云端 LLM 增强：默认关闭；仅在有网络+Key 的环境显式开启，air-gap 保持 False ──
llm_cloud_enabled: bool = False
anthropic_api_key: str = ""

# 语义对齐嵌入模型也改为本地目录（air-gap）
semantic_embedding_model: str = "models/bge-small-zh-v1.5"
```

**`pyproject.toml`（与 `[semantic]` 同范式）：**

```toml
gliner = [
    "gliner>=0.2.13",   # 含 torch + transformers；CPU 可跑，需离线 wheel/镜像（§8.3）
]
```

---

## 10. 改动清单

| 文件 | 改动 |
|------|------|
| `services/extraction/gliner_extractor.py` | **新增**：`GlinerExtractor`（本地加载）+ `get_gliner_extractor`（§7） |
| `services/extraction/llm_extractor.py` | `extract_with_fallback` 改为云端 opt-in、离线不标 degraded（§7） |
| `services/extraction/parser.py` | `parse_excel` 增 `ner_columns`；`parse_word` 表格支持 `column_mapping`（§6.0/§6.2） |
| `services/extraction/pipeline.py` | `_schema_from_class`、`_merge_ner`；Excel 富化块、Word 正文 GLiNER 块 |
| `config.py` | 默认翻转：`gliner_extraction_enabled=True`、`llm_cloud_enabled=False`、本地模型路径（§9） |
| `models/extraction.py` + `schemas/extraction.py` | `ExtractionConfig.ner_columns` + Alembic 迁移 |
| `pyproject.toml` | `[project.optional-dependencies].gliner`（§9） |
| 部署件 | 预置 `backend/models/{gliner_multi-v2.1,bge-small-zh-v1.5}/`；离线 env（§8） |

结构化主路径逻辑零回归；变化在于**默认不再走云**、prose 新增本地召回。

---

## 11. 前置条件与注意事项

- **`property_schema=[]` 必须补**：GLiNER 依赖它知道「抽什么字段」。`_schema_from_class`
  取自本体类 `data_properties`（`OntologyEngine.get_class_detail(iri).data_properties`
  每项含 `{iri, name, label, range}`）。后续可把 `CONTROLLED_VOCAB` 作枚举约束注入。
- **多实体单元格 / 多值属性**：同一属性多次命中 → 聚成 list。一个 cell 描述多个独立
  实体（应拆行）不在 NER 富化范围，保持多值或人工拆分。
- **token 窗口**：`predict_entities` 有长度上限；逐段/逐单元格已是天然切块，超长再切。
- **阈值**：`gliner_threshold` 初始 0.5；正文召回偏低降到 0.3~0.4，需以真实样本标定 P/R。
- **优雅降级**：缺包/缺权重 `is_available()=False`，NER 静默跳过到结构化兜底，作业不失败。

---

## 12. 与 medical-BERT 方案的关系

| 维度 | 本方案（GLiNER 零样本） | [`ner-medical-bert-extraction.md`](./ner-medical-bert-extraction.md)（领域 BERT） |
|------|--------------------------|---------------------------------------------------------------|
| 上线成本 | 低，零样本即用 | 高，需 CBLUE/CMeEE 等微调 |
| 领域精度 | 中（通用多语） | 高（医学语料微调） |
| 离线部署 | 一致：本地权重 + `local_files_only`（§8） | 一致 |
| 定位 | **快速前哨**，补 prose 召回 | **精度上限**，领域沉淀 |

二者均为本地模型，离线供给方案（§8）通用。可先以 GLiNER 打通 prose 抽取链路与
审核闭环，待标注数据积累后在同一 `GlinerExtractor` 协议位置替换/并联领域 BERT NER，
对齐/候选下游完全复用。

---

## 决策 / 开放问题

- [ ] **云端 LLM 路径去留**：当前保留为 opt-in（默认关）。若合规要求**彻底移除**
      `anthropic` 依赖与代码，可进一步删 `extract_entities_with_llm` 与该 dep。
- [ ] **权重分发载体**：制品库 / git-LFS / 随镜像。需定交付清单与校验和流程。
- [ ] `ner_columns` 用独立配置字段（推荐）还是 `column_mapping` 哨兵？
- [ ] 受控词表注入方式：span 后处理归一化（复用 `tag_controlled_vocab`）vs 切 GLiNER2 `choices`。
- [ ] 评估集与阈值标定：准备中文 SOP/Excel 自由文本样本做 P/R 评估。
