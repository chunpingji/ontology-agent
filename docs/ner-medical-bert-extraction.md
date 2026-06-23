# 医学领域 BERT 引入实体抽取阶段 — 研究与集成设计 (v0.2)

> 范围：研究在「实体抽取」阶段引入领域预训练编码器做命名实体识别（NER）。
> 中文首选 MC-BERT / MacBERT-Large（医学微调）；英文首选 BioBERT / PubMedBERT。
> 关联既有：`services/extraction/{pipeline,llm_extractor,parser,vocabulary}.py`、
> 实体对齐语义匹配 `services/extraction/{aligner,semantic}.py`。

---

## 1. 背景与动机

当前抽取流水线（`pipeline.run_extraction_pipeline`）：

| 源类型 | 现状 | 实体召回能力 |
|--------|------|--------------|
| Excel | 列→属性 IRI 映射（`parse_excel`）+ LLM 结构化 | 结构化行，强 |
| Word 表格 | 表行→`raw_rows`→LLM | 结构化行，强 |
| **Word 正文段落** | **仅** 正则 `parse_action_from_text`（若…则…必须→Action） | **空：prose 中的实体不被抽取** |
| 数据库 | 结构反射→class/link 候选 | 结构，强 |

**三个缺口**驱动引入领域 NER：

1. **prose 实体缺口**：SOP/方案正文里的「设备、活性成分、剂型、洁净级别、材质」等实体目前完全不进候选队列，只有强制句被抓成 Action。
2. **LLM 依赖与幻觉**：抽取走 Claude 云 API（需 Key，未配置即降级），自由文本下易漏/虚构属性；缺少确定性的「实体提及」接地。
3. **离线/合规**：GMP 环境常需离线、可审计。领域 NER 编码器可本地运行（与语义对齐已引入的 `--extra semantic` / torch 同源）。

目标：以**领域 NER 作为 LLM 抽取的前置接地层**（而非替换），补 prose 召回、降幻觉、可离线，并为后续领域微调留出统一接口。

---

## 2. 候选模型对照（已逐一核实模型卡）

| 模型 | HuggingFace ID | 语言 | 类型 | 许可证 | 现成 NER? | 备注 |
|------|----------------|------|------|--------|-----------|------|
| MC-BERT | `freedomking/mc-bert` | 中 | **基座 LM** | **模型卡未注明** ⚠️ | 否 | ChineseBLUE 系，粗到细实体/跨度掩码；NER 需微调；无托管推理，须本地 |
| MacBERT-Large | `hfl/chinese-macbert-large` | 中 | **基座 LM**（Fill-Mask） | Apache-2.0 | 否 | **无官方「Medical」变体**；医学 NER = 经 CBLUE/CMeEE 自行微调 |
| BioBERT | `dmis-lab/biobert-v1.1` | 英 | **基座 LM**（Feature-Extraction） | 卡未注明（社区多按 Apache-2.0，需核） | 否 | 113+ 社区微调；生物医学语料续训 |
| PubMedBERT / BiomedBERT | `microsoft/BiomedNLP-BiomedBERT-base-uncased-abstract-fulltext` | 英 | **基座 LM**（Fill-Mask） | **MIT** | 否 | PubMed 从零预训练 + 专用词表；现成 NER 微调最多 |

**核心结论（务必先对齐预期）：**

- 这四个**都是基座语言模型，不是开箱即用的 NER 模型**。NER 需要在其上接 token 分类头并用标注数据微调。
- **「MacBERT-Large-Medical」无官方现成检查点**，它是一条「MacBERT-large + CMeEE/CBLUE 微调」的配方，官方 CBLUE 仓库原生支持。
- 现成英文 NER 微调可直接用，例如 `siddharthtumre/pubmedbert-finetuned-ner`（JNLPBA）、`Francesco-A/...bc5cdr-ner`（化学/疾病）、`pruas/BENT-PubMedBERT-NER-*`（按实体类型）。

---

## 3. 关键现实约束（设计前必须正视）

1. **编码器 vs 生成器**：NER 产出 BIO 跨度（mention + 类型 + 偏移），而非「按属性 IRI 组织的实体 dict」。要把跨度落进 `ExtractionCandidate`，需一层 **NER 类型 → 目标类/属性** 的映射。因此 NER 最佳定位是**给 LLM 做接地/召回**，结构化仍交给 LLM 或规则。

2. **领域错配（最大风险）**：医学 NER 基准（CMeEE、BC5CDR、JNLPBA）覆盖「疾病/临床表现/药物/化学/基因」，**不覆盖** 本项目的 GMP 实体——**设备、洁净级别、材质、OEB 等级、剂型、规格**。现成医学 NER 至多抓到「活性成分/药物」一类；**全覆盖必须用项目自有标注微调**，或用**零样本**方案按自然语言定义类型。

3. **重依赖**：`transformers` + `torch`（已随语义对齐的 `semantic` extra 进入依赖面）；large 模型权重 ~1.2GB，base ~400MB；CPU 可跑批量推理，GPU 更快；完全离线。

4. **语言路由**：中文源走 MC-BERT/MacBERT，英文源走 BioBERT/PubMedBERT；用简单 CJK 字符占比判别段落语言即可。

5. **勿与对齐混用**：这些**基座** BERT 的 mean-pooling 句向量做相似度一般。**实体对齐的语义相似度仍应用 sentence-transformers 系**（中文 `BAAI/bge-small-zh-v1.5`，英文可用 `NeuML/pubmedbert-base-embeddings`）。NER 与 Embedding 是两个用途，分开选型。

---

## 4. 集成架构

### 4.1 可插拔识别器（对齐既有 `Embedder` 模式）

新增 `backend/app/services/extraction/ner_extractor.py`：

```python
from typing import Protocol, runtime_checkable
from dataclasses import dataclass

@dataclass
class EntitySpan:
    text: str
    label: str          # NER 原始类型，如 "药物" / "Chemical" / "制药设备"
    start: int
    end: int
    score: float

@runtime_checkable
class EntityRecognizer(Protocol):
    def is_available(self) -> bool: ...
    def recognize(self, text: str, lang: str) -> list[EntitySpan]: ...

class TransformersNERRecognizer:
    """微调过的 BIO 检查点：transformers token-classification pipeline。"""
    def __init__(self, model_zh: str, model_en: str, score_threshold: float): ...
    def recognize(self, text, lang):
        nlp = self._pipe(lang)               # 惰性加载 + 进程级缓存
        raw = nlp(text)                      # aggregation_strategy="simple"
        return [EntitySpan(r["word"], r["entity_group"], r["start"],
                           r["end"], float(r["score"]))
                for r in raw if r["score"] >= self._threshold]

class GLiNERRecognizer:
    """零样本：按自然语言类型列表识别，无需标注/微调。"""
    def __init__(self, labels: list[str], score_threshold: float): ...
    def recognize(self, text, lang):
        return [EntitySpan(e["text"], e["label"], e["start"], e["end"], e["score"])
                for e in self._model.predict_entities(text, self._labels)]
```

`get_recognizer()` 单例：受 `settings.ner_enabled` 门控；缺 torch/模型时 `is_available()` 返回
`False`，调用方回退（沿用 `extract_with_fallback` / `get_embedder` 的优雅降级，FR-007）。

### 4.2 两种接入点

- **Mode A — prose 实体候选（补缺口）**：对 `parse_word` 的 `paragraph` 段落跑 `recognize`，
  经「类型→class/property 映射」生成 `ExtractionCandidate`（带 `source_ref` + 字符偏移，
  `degraded_reason` 标注来源为 NER），入审核队列。直接闭合第 1 节的 prose 缺口。
- **Mode B — LLM 接地提示**：先 NER，把识别到的跨度注入 `build_extraction_prompt`
  （新增「## 已识别实体提及（接地）」段），提示 LLM **据此抽取并映射属性**，提召回、降幻觉；
  LLM 不可用时，这些跨度直接作为回退候选（强于现有「原样返回」）。

### 4.3 配置（`config.py`，沿用现有风格）

```text
ner_enabled: bool = True
ner_backend: str = "gliner"          # "transformers" | "gliner"
ner_model_zh: str = "hfl/chinese-macbert-large"   # 经 CMeEE 微调后的本地路径
ner_model_en: str = "dmis-lab/biobert-v1.1"       # 或 PubMedBERT 微调检查点
ner_score_threshold: float = 0.5
ner_gliner_labels: list[str] = ["制药设备","活性成分","剂型","规格","洁净级别","材质","OEB等级"]
```

### 4.4 类型→本体映射

NER 标签到目标类/属性的映射表（可复用 `vocabulary.py` 的集中式风格），例如
`"活性成分"→activeIngredient`、`"制药设备"→Equipment/equipmentName`、`"材质"→material`
（后者还能接力 `tag_controlled_vocab` 做受控词表归一化）。

---

## 5. 推荐路线（承认冷启动「无标注」现实）

| 阶段 | 内容 | 依赖标注? | 价值 |
|------|------|-----------|------|
| **P0 骨架** | `EntityRecognizer` 抽象 + 语言路由 + 无模型即 no-op 回退；桩单测 | 否 | 接口落地、零风险 |
| **P1 零样本（建议立即）** | **GLiNER** 后端，按 GMP 类型自然语言定义；Mode A 补 prose + Mode B 接地 | **否** | 开箱即覆盖 GMP 专有实体，绕开领域错配 |
| **P2 领域微调（质量上限）** | 用审核确认的候选作弱标注，微调 **MacBERT-large(中, CMeEE 配方)** + **PubMedBERT/BioBERT(英)** 到**项目实体集**，同协议替换 GLiNER | 是 | 精度上限、可审计、纯离线 |
| **P3 蒸馏→SFT Qwen（离线生成式终态）** | 以 LLM/GLiNER 为 teacher 产 silver，经人审队列沉淀 gold，SFT **Qwen** 做生成式抽取+属性映射，离线替代 Claude（详见 §6） | 是（飞轮自产） | 摆脱云 API/Key、降幻觉、可审计、纯离线 |

**为何 P1 用 GLiNER 而非直接上 MC-BERT/MacBERT**：用户点名的四个模型都是基座 LM，直接做 NER
需先有 GMP 标注数据微调——而项目当前**没有**这批标注。GLiNER 零样本按自然语言类型识别，
正好覆盖「设备/洁净级别/OEB」这些医学基准不含的类型，把项目从「无标注」过渡到「有标注」。
待 P2 标注积累足够，再把用户点名的领域 BERT 微调进来，作为离线高精度终态。
攒下的标注同时喂养 **P3 蒸馏飞轮**（§6）——以 LLM/GLiNER 为 teacher、人审队列沉淀 gold、
SFT Qwen 做离线生成式终态。

---

## 6. 蒸馏飞轮：teacher → 人审 → SFT Qwen（P3 离线生成式终态）

> 思路：用「能立即产标注」的模型当 teacher 生成 silver 标注，经**已有的对齐审核队列**
> 沉淀为 gold，再 SFT **Qwen** 做生成式抽取，离线替代 Claude。
> 注意：本文第 2 节四个**基座** BERT **不能**充当此处的 teacher（见 6.1）。

### 6.1 为什么基座 BERT 当不了 teacher

知识蒸馏 / 弱监督的铁律：**teacher 自己必须已经会做该任务**，才能产出可用标注去教
student。第 2 节四个 BERT 停在 Fill-Mask，**产不出 NER 标注**，因此不能直接充当蒸馏
teacher。更有一个**死循环**：要把基座 BERT 变成能产标注的 teacher，须先用 GMP 标注微调它
（P2）——而 SFT 缺的正是这批标注，teacher 与 student 卡在同一份不存在的标注上。

### 6.2 谁能当 teacher

| 候选 teacher | 现可产标注 | 覆盖 GMP 类型 | 作为 Qwen 老师 |
|---|---|---|---|
| **Claude / 强 LLM**（已在管线） | ✅ | ✅（提示即可） | ✅ **最佳**，质量最高 |
| **GLiNER 零样本** | ✅ | ✅（自然语言定义类型） | ✅ 离线弱标注源 |
| 现成英文医学 NER（已微调） | ✅ | ❌ 仅化学/疾病 | ⚠️ 仅英文窄类 |
| 第 2 节四个基座 BERT | ❌ | ❌ | ❌ 当不了 teacher |

### 6.3 数据闭环（飞轮）

```
        能立即产标注的 teacher
   ┌──────────────┐   ┌───────────────┐
   │ Claude / LLM │   │ GLiNER 零样本 │
   └──────┬───────┘   └──────┬────────┘
          └─────────┬────────┘
                    ▼   silver 标注 (text, spans, 类型, 属性)
            一致投票留 / 分歧送审
                    ▼
     ★ 已有对齐审核队列（人审）★ ─────► gold 标注
                    │  每条确认 = 一条带标注样本，自然沉淀
                    ▼
          ┌─────────┴──────────┐
          ▼                    ▼
   SFT Qwen（生成式抽取）   微调 BERT→NER（P2）
    离线替代 Claude          轻量交叉校验
          │                    │
          └──── 新文档抽取 → 更多候选 → 回审核队列 ────┐
                    ▲                                  │
                    └──────────  飞轮回流  ────────────┘
```

**关键洞察**：项目**已有的对齐审核队列就是天然 gold 标注工厂**——每条审核确认的候选 =
一条带标注样本。冷启动不靠基座 BERT，而是 **LLM + GLiNER 产 silver → 人审沉淀 gold →
攒够 → SFT Qwen**，自产自用形成飞轮；上线后 Qwen / BERT-NER 抽出的新候选再回流审核队列，
持续扩充标注、迭代下一轮 SFT。

### 6.4 四个基座 BERT 的真实位置

不是 teacher，而是**辅助件**：

1. **嵌入辅助弱监督**：用基座 BERT 的上下文向量对相似提及聚类 / 检索，把人审过的**一个**
   标签传播到**一批**相似句，加速攒标注。
2. **交叉校验 / 置信过滤**：与 KG 已知实体嵌入高相似的候选 → 更可信 → 优先送审。
3. **P2 才登场**：微调成轻量 NER，与 Qwen 互为**判别式 ↔ 生成式**交叉验证，分歧即送审。

### 6.5 为何 SFT 目标选 Qwen

| 方案 | 输出 | 离线 | 取舍 |
|------|------|------|------|
| 基座 BERT→NER（P2） | 仅实体 span，仍需再映射本体 | ✅ | 轻、快；但只做识别 |
| **SFT Qwen（生成式，P3）** | 结构化实体 JSON + 属性映射 + 受控词表归一 + 推理，**一遍出** | ✅ | 正好离线复刻 Claude 现职责，摆脱云 API/Key 与幻觉、可审计 |

两者互补：**Qwen 做离线生成式抽取终态，BERT-NER 做轻量交叉校验**，而非用 BERT 教 Qwen。

### 6.6 落地要点（待实现，非本次）

- **数据规格**：审核确认的候选导出为指令样本 `{instruction(类型定义+本体schema), input(原文/段落), output(实体JSON)}`；保留 `source_ref` + 字符偏移以可溯源、可审计。
- **训练**：Qwen2.5-7B/14B-Instruct + **LoRA/QLoRA** 起步（单卡可训、权重小、可热插拔）；按 R7「大件不入库」，adapter 与基座经本地路径加载、版本号入审计。
- **门控/回退**：沿用 `ner_enabled` / `get_embedder` 的优雅降级——未配置本地 Qwen 时回退现有 Claude 路径，CI 不加载真权重（桩注入 + 关闭开关）。
- **触发条件**：gold 标注积累达阈值（先定个量级，如数千条/各实体类型覆盖）再启动首轮 SFT，避免欠数据微调。

---

## 7. 许可证与合规

- **MC-BERT 许可证未在模型卡注明** ⚠️ → GMP/合规敏感场景，法务核实授权后再用于生产。
- MacBERT-large **Apache-2.0**、PubMedBERT/BiomedBERT **MIT** 较安全；BioBERT 卡未注明，需核实。
- 模型权重**不入库**，经本地路径/缓存加载（沿用「凭据/大件不入库」R7 精神）；记录模型版本号入审计，保证可复现。

---

## 8. 验证方式

- **单测**：注入桩 `EntityRecognizer`（确定性跨度），验证 Mode A 候选生成、Mode B 提示注入、
  语言路由、无模型回退；**不在 CI 下载真模型**（沿用 `conftest` 关闭开关 + 桩的做法）。
- **端到端**：上传含设备/药品 prose 的 Word SOP → 审核队列出现 prose 实体候选；与纯 LLM
  路径对比召回。
- **回归**：现有 Excel/表行路径不受影响（NER 仅作用于 paragraph + 作为提示增强）。

---

## 9. 参考来源

- MC-BERT：<https://huggingface.co/freedomking/mc-bert> · 论文 *Conceptualized Representation Learning for Chinese Biomedical Text Mining* <https://arxiv.org/pdf/2008.10813> · ChineseBLUE <https://github.com/alibaba-research/ChineseBLUE>
- MacBERT-large：<https://huggingface.co/hfl/chinese-macbert-large> · CBLUE/CMeEE <https://github.com/CBLUEbenchmark/CBLUE>
- BioBERT：<https://huggingface.co/dmis-lab/biobert-v1.1>
- PubMedBERT/BiomedBERT：<https://huggingface.co/microsoft/BiomedNLP-BiomedBERT-base-uncased-abstract-fulltext>
- 现成英文 NER 微调：<https://huggingface.co/siddharthtumre/pubmedbert-finetuned-ner> · <https://huggingface.co/pruas/BENT-PubMedBERT-NER-Gene>
- 英文医学嵌入（供对齐用）：<https://huggingface.co/NeuML/pubmedbert-base-embeddings>
- 零样本生物医学 NER：GLiNER-biomed <https://arxiv.org/pdf/2510.08588>
