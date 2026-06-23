# 医学领域 BERT 引入实体抽取阶段 — 研究与集成设计 (v0.4)

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
| **P1a 零样本·专有类型（建议立即）** | **GLiNER** 后端，按 GMP 专有类型（设备/洁净级别/材质/OEB/剂型/规格）自然语言定义；Mode A 补 prose + Mode B 接地 | **否** | 开箱覆盖医学基准不含的专有实体，绕开领域错配 |
| **P1b 微调·医学类型（与 P1a 并行）** | 医学类型（药物/活性成分/疾病/化学）走 NER：英文直接用现成微调检查点（PubMedBERT/BioBERT，**零训练**）；中文微调 **MacBERT/MC-BERT @ CMeEE**。两路 span 合并入候选 | 英=否(现成)/中=是(CMeEE 公开) | 医学类型精度高，且有现成数据/检查点、不冷启动 |
| **P2 领域微调（质量上限）** | 用审核确认的候选作弱标注，微调 **MacBERT-large(中, CMeEE 配方)** + **PubMedBERT/BioBERT(英)** 到**项目实体集**，同协议替换 GLiNER | 是 | 精度上限、可审计、纯离线 |
| **P3 蒸馏→SFT Qwen（离线生成式终态）** | 以 LLM/GLiNER 为 teacher 产 silver，经人审队列沉淀 gold，SFT **Qwen** 做生成式抽取+属性映射，离线替代 Claude（详见 §6） | 是（飞轮自产） | 摆脱云 API/Key、降幻觉、可审计、纯离线 |

**P1a 与 P1b 互补，不是二选一——按「实体类型边界」分工：**

| 实体类型 | 例 | 现成公开数据/检查点? | 推荐识别器 | 理由 |
|----------|----|----------------------|-----------|------|
| **医学类型** | 药物、活性成分、疾病、化学物质 | ✅ CMeEE(中) / BC5CDR(英)，英文还有现成微调检查点 | **微调 BERT-NER（P1b）** | 有标注/检查点，精度高于零样本 |
| **GMP 专有类型** | 设备、洁净级别、材质、OEB 等级、剂型、规格 | ❌ 医学基准不含 | **GLiNER 零样本（P1a）** | 无需标注，按自然语言类型即可识别 |

两路在同一段文本上各跑各的，**span 合并去重后**统一入候选队列（Mode A）/ 接地提示（Mode B）。
语言路由不变：中文段落走 MacBERT/MC-BERT，英文段落走 PubMedBERT/BioBERT。

**为何不把医学类型也丢给 GLiNER**：零样本对「药物/疾病」这类**已有成熟标注**的类型，精度通常不如
专门微调的 BERT-NER；既然现成数据/检查点都在，P1b 直接把这部分精度吃满。**为何专有类型不微调
BERT**：医学基准根本不含「设备/洁净级别/OEB」，要微调得先自标——那正是 P1a 用 GLiNER 零样本
绕开的冷启动。

**三点边界提醒**：
1. **领域迁移**：CMeEE 是临床病历文体，本项目是 GMP 生产 SOP 文体，P1b 上线前须在项目文本上验证掉点；
2. **嵌套实体**：CMeEE 含嵌套，纯 flat-BIO 会略丢，追精度可上 GlobalPointer / W2NER；
3. **中文无现成检查点**：P1b 中文必须自训（英文可零训练直接用现成检查点）。

P1b 自训 / 审核产出的标注，与 P1a 经人审沉淀的 gold 一道，喂养 **P2 领域微调**（把专有类型也训进
BERT，同协议替换 GLiNER）与 **P3 蒸馏飞轮**（§6，SFT Qwen 离线生成式终态）。
其中**专有类型如何低成本攒到 P2/P3 所需标注**，见 **§7 GMP 专有类型的 AI 辅助标注流水线**。

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

## 7. GMP 专有类型的 AI 辅助标注流水线

> 这是 P1a→P2 与 §6 飞轮**专有类型那一侧**的「标注获取引擎」：医学类型有现成数据（P1b），
> 而设备/洁净级别/材质/OEB/剂型/规格**没有**，须自标。核心原则：**预标注 + 人工校正**——
> AI 先填候选 span，人只做「点确认 / 改边界」，把"标注"降级成"校对"（经验上比从零标快约 2–5×）。

### 7.1 先按类型分级派活：多数类型根本不用人标

六类专有类型里**只有「设备」真正开放、需模型+人**，其余五类是闭集或模式，词典/正则近乎免费：

| 专有类型 | 集合性质 | 最省的 AI 手段 | 人工量 |
|---|---|---|---|
| **洁净级别** | 闭集（A/B/C/D 级、ISO 5–8、百/千/万级） | 正则 + 词典 | ≈0，抽检 |
| **OEB 等级** | 闭集（OEB1–5） | 正则 | ≈0，抽检 |
| **规格** | 模式（数值+单位 mg/ml/IU/%） | 正则（顺带规整边界） | 少，校边界 |
| **剂型** | 半闭集（~50 词：片剂/胶囊/注射剂…） | 受控词表 gazetteer | 少 |
| **材质** | 半闭集（316L 不锈钢/PTFE…，**项目已有受控词表**） | gazetteer + `tag_controlled_vocab`，新词补 GLiNER | 中 |
| **设备** | 开放（变体最多） | GLiNER + LLM + **KG 已有设备实例**当词典 + 主动学习 | 高（人力重点） |

> 先把五类压到「近乎自动」，人力集中到「设备」，整体标注量降一个量级。

### 7.2 多源弱监督预标注 + 投票去噪

同一段文本上多个标注器各出 span，按一致性分流——一致免审，分歧才送人：

```
一段文本
   ├─ 正则/词典(洁净级别/OEB/规格/剂型/材质 + KG 设备实例) ─┐
   ├─ GLiNER 零样本(全部专有类型, P1a 已有) ───────────────┤
   └─ LLM few-shot(注入类型定义+受控词表) ──────────────────┤
                                                            ▼
                                              多源 span 对齐 / 投票
                                       ┌────────────┴─────────────┐
                                  全源一致                 分歧/低置信/新词
                                       ▼                          ▼
                                 silver(免审)         ★人审队列(主动学习排序)★
                                       └────────────┬─────────────┘
                                                    ▼
                                       gold 标注 ─► 训 NER(P2) / SFT Qwen(§6)
```

三源互补：**正则/词典**精度最高、管闭集；**GLiNER**（P1a）零样本兜全类型；**LLM few-shot**
（注入类型定义+受控词表）召回最好、可附理由便于审计。**三源一致 = 高置信 silver 直接收**，
分歧 / 低置信 / 新词才进人审——人只碰模型搞不定的部分。

### 7.3 把有限人力花在刀刃上：主动学习 + 标签传播

- **主动学习排序**：人审队列不按时间排，按「最值得标」排——优先 ①模型最不确定 ②多源最分歧
  ③嵌入上最新颖（与已标 gold 距离远）；避免重复标 1000 句几乎一样的"压片机"。
- **嵌入聚类 + 标签传播**：复用 `semantic.py` 的 bge 中文嵌入，把候选提及聚类，**人标一个簇
  代表、传播到整簇**——设备/材质这种同形大量重复的类型收益最大。

### 7.4 复用项目已有件（最省的部分）

| 已有件 | 在标注里的角色 |
|---|---|
| **对齐人审队列**（§6 飞轮） | 每条审核确认候选自带 `type + source_ref + 偏移` → **直接转 BIO 标注**，是已有工作的**副产品** |
| `vocabulary.py` 受控词表 | 材质 / 剂型 gazetteer + `tag_controlled_vocab` 归一，自动预标 + 规范化 |
| **本体 T-Box** | ①标注 schema：类型定义出自本体，**减少标注分歧**；②已有个体（设备名/材质）当 **gazetteer** 远监督 |
| `semantic.py` bge 嵌入 | 聚类、标签传播、主动学习多样性采样 |

### 7.5 自举（self-training）：越标越省

```
首批 gold → 训轻量 NER(MacBERT) → 用它预标注（比通用 GLiNER 更贴项目）
         → 人只改它的错 → 再训 → 预标注更准 → 人工再降 …
```

首批 gold 多来自「闭集自动 + 设备少量人审」；每轮预标注质量上升、人工下降，把项目从「冷启动」
推到「有标注」，正好接上 **P2 领域微调**（把专有类型训进 BERT）。

### 7.6 质量与可审计（GMP 要点）

- **多源一致性**当天然质检：不一致即自动标记送复核。
- **边界规整**：规格/洁净级别/OEB 用正则把 span 边界对齐标准形，省掉占 NER 标注大头的"改边界"工。
- **来源留痕**：每条标注记录来源（哪个标注器 / 谁审 / 模型版本）→ 可追溯、可复现，符合 GMP 审计与 R7「大件不入库」。

### 7.7 落地起步件（待实现，非本次）

1. **审核候选 → BIO / 指令样本导出脚本**：零新组件，把日常审核直接变成标注产出（同时供 P2 NER 与 §6 SFT Qwen）。
2. **闭集/正则标注器 + gazetteer**：洁净级别/OEB/规格走正则，材质/剂型/设备实例走词典——覆盖五类大头，一天可成。
3. **多源投票 + 主动学习排序**接入人审队列。

---

## 8. 许可证与合规

- **MC-BERT 许可证未在模型卡注明** ⚠️ → GMP/合规敏感场景，法务核实授权后再用于生产。
- MacBERT-large **Apache-2.0**、PubMedBERT/BiomedBERT **MIT** 较安全；BioBERT 卡未注明，需核实。
- 模型权重**不入库**，经本地路径/缓存加载（沿用「凭据/大件不入库」R7 精神）；记录模型版本号入审计，保证可复现。

---

## 9. 验证方式

- **单测**：注入桩 `EntityRecognizer`（确定性跨度），验证 Mode A 候选生成、Mode B 提示注入、
  语言路由、无模型回退；**不在 CI 下载真模型**（沿用 `conftest` 关闭开关 + 桩的做法）。
- **端到端**：上传含设备/药品 prose 的 Word SOP → 审核队列出现 prose 实体候选；与纯 LLM
  路径对比召回。
- **回归**：现有 Excel/表行路径不受影响（NER 仅作用于 paragraph + 作为提示增强）。

---

## 10. 参考来源

- MC-BERT：<https://huggingface.co/freedomking/mc-bert> · 论文 *Conceptualized Representation Learning for Chinese Biomedical Text Mining* <https://arxiv.org/pdf/2008.10813> · ChineseBLUE <https://github.com/alibaba-research/ChineseBLUE>
- MacBERT-large：<https://huggingface.co/hfl/chinese-macbert-large> · CBLUE/CMeEE <https://github.com/CBLUEbenchmark/CBLUE>
- BioBERT：<https://huggingface.co/dmis-lab/biobert-v1.1>
- PubMedBERT/BiomedBERT：<https://huggingface.co/microsoft/BiomedNLP-BiomedBERT-base-uncased-abstract-fulltext>
- 现成英文 NER 微调：<https://huggingface.co/siddharthtumre/pubmedbert-finetuned-ner> · <https://huggingface.co/pruas/BENT-PubMedBERT-NER-Gene>
- 英文医学嵌入（供对齐用）：<https://huggingface.co/NeuML/pubmedbert-base-embeddings>
- 零样本生物医学 NER：GLiNER-biomed <https://arxiv.org/pdf/2510.08588>
