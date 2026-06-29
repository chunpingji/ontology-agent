# 调研报告：中文长文本 span NER 方案（GLiNER 长文本失效根因与修复）

**Date**: 2026-06-28 | **Feature**: 009-word-table-ner-optimize | **范围**: 自由文本/正文段落与表格单元格的实体 span 召回（US2/US3 三阶段标注的阶段一）

---

## 1. 背景与问题

三阶段标注管线：**阶段一** GLiNER（`gliner_multi-v2.1`，mDeBERTa-v3-base，离线 CPU）做 span 检测 → **阶段二** bge-base-zh-v1.5 嵌入按 ~201 本体类归类（余弦阈值 0.50）→ **阶段三** 类数据属性标签跑属性三元组。

现象：阶段一在**中文长文本**（正文长句、宽表行级拼接文本）上召回崩塌——要么把"药品名称＋生产企业＋谓语"整句吞入**单一** span，要么干脆漏召。把 `gliner_threshold` 降到 0.01 也无效。这直接拖垮阶段二归类与前端 tiptap 高亮（span 偏移失真）。

## 2. 根因（已实测确认）

GLiNER 的候选 span 枚举发生在 **word-token 边界**上：它先用一个 *words splitter* 把文本切成 word-token，再在「连续 ≤ `max_width`(=12) 个 word-token」的所有区间上打分，最后 `torch.where(probs > threshold)` 取超阈区间。

`gliner_config.json` **无** `words_splitter_type` 键 → 回退默认 `whitespace`：

```python
# gliner/data_processing/tokenizer.py — WhitespaceTokenSplitter
self.whitespace_pattern = re.compile(r"\w+(?:[-_]\w+)*|\S")
```

对**无空格的中文**，`\w+` 是 Unicode-aware 的，会把一整串汉字吞成**一个** word-token。于是：

- 一个长句往往坍缩成 1–3 个 word-token，候选区间寥寥无几 → **整句被当成一个 span**（边界吞没）；
- 想要的细粒度边界（"阿莫西林胶囊" / "华北制药股份有限公司" 各自成 span）**根本没有被切出来，也就从未被打分** → 调阈值无济于事（阈值只能在已打分的区间里筛，无法凭空造边界）。

这解释了"降到 0.01 仍失效"的反直觉现象。**根因是分词边界，不是阈值。**

`words_splitter` 的内部结构（已核对 gliner 0.2.27）：

```
model.data_processor.words_splitter        # WordsSplitter 工厂
        └── .splitter                      # 实际分词实现（默认 WhitespaceTokenSplitter）
model.predict_entities(text, ...)
        └── data_processor.words_splitter(text)  # 委派到 .splitter(text)  (model.py:1845)
```

→ **只需替换内层 `.splitter`**，即可改变候选 span 的枚举边界，无需改配置、无新增运行期依赖。

## 3. 方案分层

| Tier | 方案 | 定位 |
|------|------|------|
| **A1** | 注入字符级中文 words_splitter | **已采用·根因修复·零依赖** |
| A2 | 子句/句子切块 + 偏移回写 | 已采用（长文本 >384 word-token 防截断） |
| A3 | 字符滑窗兜底 + 偏移回写 | 已采用（无标点超长串兜底） |
| A4 | jieba3 内置分词器 | 备选（有 import-guard bug；无用户词典 API） |
| A5 | threshold / flat_ner 调参 | 次级旋钮 |
| **B/C** | 替换/增强模型、本地 LLM、gazetteer 混合 | 见 §7（补充调研） |

> A2/A3 的**偏移契约**：切块器必须把 chunk 内 span 偏移映射回**段落相对**偏移再返回 `_annotate_texts`，否则阶段二上下文窗口与前端高亮都会错位。`max_len=384` 是 **word-token** 数，不是 subword；字符滑窗按**字符**切，因为一段无标点的 123 字中文只算 1 个 word-token。

## 4. A1 实现（已落地）

字符级中文分词器：**每个汉字单独成词**，使 GLiNER 在字级粒度枚举 span；ASCII 连写串（批准文号尾号 `H13020999`、规格 `0.25g`、标识符 `ID_3`）整体保留；其余非空白单字符各自成词，保证**零字符丢失、偏移精确**。

```python
# backend/app/services/extraction/gliner_extractor.py
class _CJKAwareWordsSplitter:
    _TOKEN_PATTERN = re.compile(
        r"[一-鿿㐀-䶿]"                          # 每个 CJK 表意文字单独成词
        r"|[A-Za-z0-9]+(?:[._\-/%][A-Za-z0-9]+)*"  # ASCII 连写串（含 . _ - / % 连接符）
        r"|[^\s]"                                  # 其余单个非空白字符
    )
    def __call__(self, text):
        for m in self._TOKEN_PATTERN.finditer(text):
            yield m.group(), m.start(), m.end()
```

**工作原理——字符级分词器不做"分词"，只制造边界**：

`_CJKAwareWordsSplitter` 输出的是**逐字 token**，不含任何词典或统计分词逻辑。真正的实体识别由 GLiNER 的神经打分器完成：

```
输入: "阿莫西林胶囊由华北制药股份有限公司生产"

Step 1 — _CJKAwareWordsSplitter 产出逐字 token（制造细粒度边界）:
  ["阿","莫","西","林","胶","囊","由","华","北","制","药","股","份","有","限","公","司","生","产"]
    0    1    2    3    4    5   6   7    8   9   10  11  12  13  14  15  16  17  18

Step 2 — GLiNER 枚举所有 ≤ max_width(12) 个连续 token 的区间作为候选 span:
  [0:6]  = "阿莫西林胶囊"         ← 候选
  [0:7]  = "阿莫西林胶囊由"       ← 候选
  [7:17] = "华北制药股份有限公司"  ← 候选（10 token ≤ 12）
  [7:18] = "华北制药股份有限公司生" ← 候选
  ...    共约 C(19,2) 量级候选

Step 3 — mDeBERTa encoder + span head 对每个候选×每个 label 打概率分:
  [0:6]  × "药品名称"  → 0.92 ✓ (> threshold)
  [7:17] × "生产企业"  → 0.88 ✓ (> threshold)
  [0:7]  × "药品名称"  → 0.31 ✗ (< threshold, 含"由"不像药名)

Step 4 — threshold 筛选 + 贪心去重 → 最终输出精确 span
```

| 组件 | 职责 |
|------|------|
| `_CJKAwareWordsSplitter` | **制造细粒度边界**——让正确 span 成为候选（无词典、零 NLP） |
| mDeBERTa encoder + span head | **判断哪个候选是实体**——神经网络在字符序列上做"分词" |

对比默认 whitespace 分词：整句坍缩为 1-3 个 word-token，`max_width=12` 下只有巨大区间可选，正确边界**从未被枚举**，阈值调到 0 也无法凭空造出不存在的候选。字符级分词不是"规避"12-token 限制，而是让它在合理粒度上工作（12 字符覆盖绝大多数中文实体名）。

注入点（随模型加载生效，结构异常时静默降级）：

```python
def _install_cjk_words_splitter(model) -> None:
    try:
        model.data_processor.words_splitter.splitter = _CJKAwareWordsSplitter()
    except AttributeError:  # GLiNER 内部结构变更的防御路径
        logger.warning("无法注入中文分词器；沿用默认空白分词", exc_info=True)

# _ensure_model() 内 from_pretrained 之后立即调用 _install_cjk_words_splitter(self._model)
```

`_entities_to_spans` 增加**去重/消重叠**：字符级分词放开枚举边界后，同区间可能在多个 seed label 上重复命中；按分数降序贪心，仅保留与已选 span **无字符交集**者（相邻 `end==start` 不算交集），得到扁平无重叠 span 集（契合 tiptap 高亮；GLiNER label 仅临时值，阶段二按本体重归类）。

**改动文件**：
- [gliner_extractor.py](../../backend/app/services/extraction/gliner_extractor.py) — 分词器 + 注入 + 去重
- [test_gliner_extractor.py](../../backend/tests/test_extraction/test_gliner_extractor.py) — 9 个新回归用例（分词/偏移/覆盖/注入/降级/去重消重叠），全部 fake-based 无需下载权重

## 5. 实测验证（真实权重，CPU）

输入：`阿莫西林胶囊由华北制药股份有限公司生产，批准文号国药准字H13020999，规格0.25g，适用于敏感菌所致的呼吸道感染。`
labels：`["药品名称","生产企业","批准文号","规格","适应症"]`，threshold=0.5

| | 默认 whitespace（修复前） | 字符级中文（A1，修复后） |
|---|---|---|
| span 数 | **2（均被吞没）** | **5（各自正确）** |
| 结果 | 药品名称="阿莫西林胶囊由华北制药股份有限公司生产"(0,19)；批准文号="批准文号国药准字H13020999"(20,37) | 药品名称="阿莫西林胶囊"；生产企业="华北制药股份有限公司"；批准文号="H13020999"；规格="0.25g"；适应症="敏感菌" |
| 偏移回溯 | — | ✅ 全部精确 round-trip |
| 重叠 | — | ✅ 无 |

**结论**：根因修复有效——整句吞没的 2 个垃圾 span → 5 个边界正确、可被阶段二正确归类的实体。

回归测试：`pytest tests/test_extraction/` → **126 passed**。

## 6. 残留局限与后续

- **批准文号** 仅召回 `H13020999`，未含 `国药准字` 前缀；**适应症** 仅召回 `敏感菌`，未含完整短语 `敏感菌所致的呼吸道感染`。这是字级粒度的预期残差，由 **A2 子句切块** 给足上下文 + **词典/正则 gazetteer 混合**（`国药准字[A-Z]\d{8}`、`有限公司` 后缀、`\d+(mg|g|IU|%)` 规格）补全——见 §7。
- 真实语料目前段落多 ≤243 字（< 384 word-token），**A2/A3 截断防护尚非燃眉**，但已就位以面向未来宽表/长正文。

---

## 7. Tier B/C 补充调研：替换/增强模型与本地 LLM

> 由后台多智能体调研工作流产出（15 agents，5/5 维度，26 候选 → 8 深核 → 对抗验证 → 合成）。
> 关键结论：**没有任何纯模型替换能修复 §2 的中文边界吞没——它在 WordsSplitter，不在权重；Tier B/C 全部只能叠加在 Tier A 之上**。唯一真正 *beat*（而非仅 compose）Tier A 的低成本项是**词典/正则 gazetteer**（§7 三）。

> 本节是对已落地 **Tier A**(零依赖 CJK 字符级 `words_splitter` 注入 + 子句/滑窗分块 + 偏移回写)的**补充**,不重复 Tier A 细节。一个贯穿全节、源码已验证的硬约束先行声明:**在 gliner 0.2.27 中,`model.py:1845` 调用的是所有 GLiNER 架构共享的 `words_splitter(text)`**——因此**任何纯权重替换都不能修复中文边界吞并(boundary-swallow)bug**。下文每个模型类候选都只能**叠加在 Tier A 之上**,以"召回/精度增量"自证价值,而非取代它。判分公式:`(在真实漏检集上的召回/精度增益) × (低成本/离线CPU可行性) ÷ (延迟 + 依赖成本)`。

---

### 一、Tier B:替换/增强 Stage-1 模型

#### B-1. 中文/多语 GLiNER 权重替换:`knowledgator/gliner-x` (mT5)

- **方案说明**:把 `GLiNER.from_pretrained("models/gliner_multi-v2.1")` 换成本地 `gliner-x-large` 目录,Stage-2(bge 余弦到 201 类)、Stage-3 不变。纯权重交换。
- **离线·CPU 可行性**:`likely`。Apache-2.0(仅 v0 原版);`sentencepiece 0.2.1` 已在 `uv.lock`,非阻塞。但需要把 gliner 从 0.2.27 升级到能用 Auto 类构建 mT5 编码器的新版,且卡片安装命令一律是 `pip install gliner[stanza]` / `gliner[tokenizers]`,引入新的 stanza/tokenizers extra。
- **中文长文本适配度**:**weak**。`x-large` 0.9B/1.2GB,约为现部署 mdeberta(279M)的 **3-4 倍**,长批量段落正是最不该付 4× CPU 代价的场景。卡片从不公布 `span_mode`,若仍是 `markerV0`,`max_width=12` 的长实体上限照旧。
- **集成成本**:中。需验证 Tier-A 的 `model.data_processor.words_splitter.splitter` 注入路径(未公开内部结构)能在新 gliner 上存活;项目已踩过 mT5 `encoder_config` 必须匹配 checkpoint(vocab 250105 / position_buckets 256)的坑。
- **是否仍需 Tier-A 中文分词修复**:**是**(必需)。边界 bug 在 WordsSplitter,不在权重。
- **风险**:① 唯一显著提升中文的 **v0.5 refresh 是 cc-by-nc-sa-4.0 非商用**(zh_pud 0.709),对药企/监管商用部署几乎是致命的;Apache 路只剩 v0,x-large 仅 +0.038 zh_pud。② 基准是 zh_pud(通用/新闻 UD),**不是药品/监管**(国药准字/规格/适应症),无证据迁移到目标实体。③ x-base(0.6152)、x-small(0.5792)均**低于** 0.641 基线,只有 x-large 勉强超。
- **建议**:`consider`(明显低于 Tier A,不要先于更便宜的旋钮做)。
- **关键来源**:https://huggingface.co/knowledgator/gliner-x-large ; https://huggingface.co/knowledgator/gliner-x-large-v0.5 ; https://docs.knowledgator.com/docs/frameworks/gliner/pretrained-models/

#### B-2. ModelScope 中文医疗 NER:`iic/nlp_raner_named-entity-recognition_chinese-base-cmeee` (RaNER-CMeEE) 作为 UNION 召回源

- **方案说明**:不是替换,而是**并联召回源**。每段跑 modelscope NER pipeline,把 `{type,start,end,span}` 转成与 GLiNER 同形的 dict,**丢弃其 9 个固定标签**,把 GLiNER ∪ RaNER 的 span 一起送进 `_entities_to_spans` 的贪心去重,再进 Stage-2。仅改 `document_annotator.py` 的 Stage-1 调用点。
- **离线·CPU 可行性**:**confirmed**。纯字符级中文 BERT(model_type=bert, vocab 21128, max_pos 512),pytorch_model.bin = **409 MB**,Apache-2.0;10 文件自包含仓库,**"Ra"(检索)仅训练期技巧**,推理无检索语料/索引。modelscope `pipeline(..., device='cpu')` 自动回退 CPU。字符级分词意味着边界吞并**结构上不会**在此模型内部发生。
- **中文长文本适配度**:**weak**。512 字上限仍强制 Tier-A 分块。
- **集成成本**:中偏高。**不能去掉重型 modelscope 框架**——transformer-crf 头不是 `BertForTokenClassification`,vanilla transformers 加载不了,除非自己重写 CRF + state-dict remap。气隙footprint = modelscope 框架(自带 torch/transformers/datasets,数百 MB)+ 409 MB 权重。还多一次 CPU 前向(**约翻倍 Stage-1 延迟**)。
- **是否仍需 Tier-A 中文分词修复**:**是**——GLiNER 那条 union 分支仍需 Tier-A 字符分词;512 字上限仍需 Tier-A 分块。
- **风险**(决定性):**领域错配**。CMeEE 是临床/EMR,9 类里只有 `dru`(药品,F1 84.1)和部分 `equ`(器械)迁移得过来;**生产企业、国药准字、规格/用法用量、法规——全部不在标签空间**,即恰恰是 GLiNER+Tier-A 最需要帮忙的长文本实体,RaNER 在这些上**零增益**。README 明示"垂类领域 NER 效果会降低"。AdaSeq 已停止维护(限制未来域内微调)。
- **建议**:`consider`(比 pilot 低一档,明确低于 Tier A)。仅当真实药监文档上 A/B 出**药品名召回的实质增量**且被 bge typer 保住时才值。
- **关键来源**:https://www.modelscope.cn/api/v1/models/iic/nlp_raner_named-entity-recognition_chinese-base-cmeee ; (config.json/README 同仓 repo 文件)

#### B-3. 中文监督模型 UIE / `uie-medical-base`(via `uie_pytorch` + ONNX INT8)

- **方案说明**:基于 ERNIE-3.0 的 prompt 式 span-MRC(uie-base ≈118M / ~470MB FP32, Apache-2.0),理论上能替换 Stage-1 并部分合并 Stage-2(同时出 span+type)。
- **离线·CPU 可行性**:`likely`,但需手动 staging 权重(Baidu BOS / HF 镜像 `LANZ/uie-medical-base`)+ 自建 ONNX 导出 + 自做 `quantize_dynamic` INT8——仓库**不带 INT8**(FP16 仅 GPU 可用)。
- **中文长文本适配度**:**weak**。max_seq_len 512 sub-token(子词级,免疫 CJK 空白塌缩,**不需要 Tier-A 字符分词**),`_auto_splitter` 句感知非重叠分块且偏移回写——但非重叠分块会在边界切断实体,**不优于** Tier-A 子句分块。
- **集成成本**:**高**(架构杀手)。`uie_predictor.py` 的 `_multi_stage_predict` **按 schema 每个实体类型串行跑一次编码器前向**——5-10 个药品类型 = **每窗 5-10× GLiNER 单次多标签前向**的 CPU 代价;INT8 最佳 2-4×(还依赖 AVX512-VNNI)无法抵消。还制造 typing 权威冲突(UIE 自由文本 schema vs bge 201 类),需额外对账层。
- **是否仍需 Tier-A 中文分词修复**:**否**(子词级免疫边界 bug)——但这不能弥补它在长文本召回、延迟、ONNX/INT8 工程、typing 冲突上的全面劣势。
- **风险**:零样本在目标字段上弱(实测仅抽出"药品名称",规格/用法/用量需微调,通用名微调后仍 F1=0;uie-base 零样本医疗 F1 仅 71.83);国药准字是闭式,**正则 ~100% 精度零延迟完胜**;微调需标注 + GPU。
- **建议**:`avoid` 作为 Stage-1 替换;最多对模糊类型(适应症/生产企业)在**微调后**做窄域 pilot,国药准字/规格保留正则。
- **关键来源**:https://github.com/heiheiyoyo/uie_pytorch/blob/main/uie_predictor.py ; https://huggingface.co/LANZ/uie-medical-base ; https://www.cnblogs.com/vipsoft/p/18281350

#### B-4. 域内微调 GlobalPointer / RaNER(AdaSeq)——最高天花板,门槛最高

- **方案说明**:Stage-1 **边界检测器替换**。字符级 RaNER(StructBERT+CRF)或 GlobalPointer(每 (start,end) sigmoid)出 `{type,start,end,span}`,推荐**模式 (a):全部塌缩为单一通用 span 类**,纯做边界检测,把 typing 全留给 Stage-2 的 201 类嵌入(标注成本最低、不冻结标签集)。
- **离线·CPU 可行性**:`likely`(非 confirmed)。AdaSeq Apache-2.0;离线两条路:(i) ModelScope 本地路径 `pipeline(..., device='cpu')`(但 `device=cpu` 未在 AdaSeq 官方推理文档中明示);(ii) 把 StructBERT 编码器导出 ONNX(opset≥13)+ NumPy 重写 CRF Viterbi,彻底甩掉 modelscope/adaseq 运行时(GlobalPointer 无 CRF,导出更简单)。后者是**额外一次性工程**。
- **中文长文本适配度**:**moderate**。字符级原生 → 边界吞并结构上不可能,**可退役 Tier-A 字符分词(A1)**;但 512-token 上限仍在 → **仍需 Tier-A 子句/滑窗分块(A2/A3)**。
- **集成成本**:**高**(门槛在数据)。需手标几百~1k 条药监句(国药准字/规格/法规/适应症/生产企业)的字符级 BIO/span 偏移;一次性 GPU 训练(推理仍 CPU);小数据有灾难性遗忘风险(从现成 RaNER 初始化 + 冻层/PEFT 缓解)。
- **是否仍需 Tier-A 中文分词修复**:**仅退役 A1(字符分词)**,**仍需 A2/A3(分块)**。
- **风险**:标注预算(排名最后的唯一原因);必须先测得 Tier-A + 零样本召回**仍不足**再投;CMeEE 上 GlobalPointer F1 ~75.9,但**那是 9 个医疗类,不是你的 schema**,无标注前中文质量不可验;`xhw205 GlobalPointer_torch` 无 license,企业气隙宜用 AdaSeq 自带 GlobalPointer;U-RaNER 陷阱(其检索 KB **不气隙可行**,别用它的 SemEval 数字背书本候选)。
- **建议**:`consider`——最后手段、最高天花板,**穷尽 Tier-A 前不要碰**。
- **关键来源**:https://github.com/modelscope/AdaSeq ; https://github.com/modelscope/AdaSeq/blob/master/docs/tutorials/model_inference_zh.md ; https://github.com/xhw205/GlobalPointer_torch/blob/main/data_loader.py

#### B-5. GLiNER2 等单次前向新架构(完整性说明)

负面发现:**公开 HF/ModelScope 上不存在中文专用或中文医疗 GLiNER checkpoint**;中文只能经多语 GLiNER(现 mdeberta multi-v2.1 或 mT5 gliner-x)。GLiNER2(`fastino-ai/GLiNER2`)单次前向做实体/分类/关系,CPU 优先,是相对 UIE 多次前向的架构优势佐证,但同样无中文医疗权重,仍受同一 WordsSplitter 约束,需 Tier-A。

---

### 二、Tier C:本地 LLM 抽取(约束 JSON 解码 + 偏移回锚)

#### C-1. 选择性第二遍:`Qwen3-4B-Instruct-2507` (GGUF) 优先,`NuExtract-2.0-2B` 次选

- **方案说明**:**Tier-C 选择性第二遍,非 Stage-1 替换**。GLiNER+Tier-A 仍是廉价批量主路;本地 LLM 只在便宜路弱的窗口(低召回段落、表格单元、国药准字/规格/适应症高价值字段)触发。新建 `llm_local_extractor.py`,用 `llama-cpp-python` 的 GBNF/JSON-schema 约束解码(`LlamaGrammar`/`SchemaConverter`,进程内零网络),返回逐字字符串字段,经共享偏移回锚(精确子串→difflib/RapidFuzz,全角/半角数字 + 中/英标点归一化)重锚成 `{start,end}`,产出与 GLiNER 同形 span,**Stage-2/3 不变**;无法回锚的串当幻觉丢弃,丢弃率即每窗质量信号。**绝不复用** `llm_extractor.py`(那是云 Claude API,`llm_cloud_enabled` 门控,非气隙合法,须保持关闭)。
- **离线·CPU 可行性**:**confirmed**。进程内约束 JSON 解码;NuExtract 路省略 `--mmproj` 走纯文本(Qwen2-VL 文本骨干即标准文本 Qwen2);`-ngl 0`、temp 0.0、`local_files_only`。权重 << RAM。
- **中文长文本适配度**:**moderate**。**唯一**能读整段中文并对其推理(而非枚举边界脆弱 span)的路线,直击 Tier-A 残留缺口(整句"敏感菌所致的呼吸道感染"、"国药准字"前缀)。Qwen3-4B C-Eval 77.5、262K 上下文、Apache-2.0、非思考模式(**约束语法在 llama.cpp 的思考模式下会被静默禁用,故必须非思考**)。
- **集成成本**:中。新增 `llama-cpp-python` + 1-3 GB GGUF 权重(预置到气隙)。Qwen3-4B GGUF:Q4_K_M 2.5 GB / Q5_K_M 2.89 GB / Q8_0 4.28 GB;NuExtract-2.0-2B-GGUF Q5_K_M 1.13 GB。
- **是否仍需 Tier-A 中文分词修复**:**是**——这是**叠加的第二遍**,GLiNER+Tier-A 仍是主批量路。
- **风险**:① CPU 延迟主导:4B Q4 约 10-20 tok/s、带宽受限、每窗数秒,**比 GLiNER 批量慢 1-2 个数量级**,**必须严格选择性触发**。② 召回未在真实国药准字/规格/适应症语料上证明:NuExtract 中文未验证(欧语微调偏向,"+81pp VAREX"卡片无据),Qwen3-4B 无公开中文 NER/IE 分数——**必须先做召回 bake-off,beat Tier-A + 廉价 gazetteer 才值**。③ 偏移回锚丢弃率(全角/半角、中/英标点)未测;须实现归一化并验证残余丢弃率,否则破坏 tiptap 高亮。④ 与项目 Principle V(最小复杂度/无新依赖)张力,除非召回增量被证决定性。
- **建议**:`pilot`——选 **Qwen3-4B-Instruct-2507**(中文)而非 NuExtract 作主;先跑 bake-off + 丢弃率测量,召回增量在触发器实际选中的窗口上**决定性**才采纳。
- **关键来源**:https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507 ; https://qwenlm.github.io/blog/qwen3/ ; https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF ; https://huggingface.co/numind/NuExtract-2.0-2B ; https://til.simonwillison.net/llms/llama-cpp-python-grammars

#### C-2. 其他本地 LLM 候选(license/规模速查,作 Qwen3-4B 备选)

- **GLM-4-9B-Chat**(智谱,9B):中文强,但 9B GGUF Q4 ≈ 5.5GB、CPU 更慢;授权需逐版本核(部分自定义许可)。仅当 4B 召回不足且延迟预算允许时考虑。
- **MiniCPM3-4B**(面壁,4B,Apache-2.0 路线):体量与 Qwen3-4B 同档,中文不错,可作 A/B 对照,但无公开抽取/IE 背书,优先级低于 Qwen3-4B。
- **NuExtract-2.0-4B**:基于 Qwen2.5-VL-3B,**Qwen Research License 非商用**——**避开**;只有 2B(MIT)和 8B(MIT)商用干净。
- **避开 Qwen2.5-3B**(限制性 Qwen Research License)。

> Tier-C 通用气隙纪律:所有权重一次性在国内下载,运行时 `HF_HUB_OFFLINE=1` / `local_files_only`,与现有 GLiNER 离线模式一致。

---

### 三、词典/正则 Gazetteer 混合(闭域药品)——最高 ROI 的廉价高精度增强,与 Tier-A 组合

这是**唯一真正"beat"Tier-A 而非仅"compose"的项**,成本最低,命中恰是 GLiNER 在闭域系统性漏掉的闭式实体。**已在源码验证 seam 真实**。

- **方案说明**:把 Aho-Corasick gazetteer(药名/厂商)+ 正则包(国药准字/规格/剂量)的 span **UNION 进 Stage-1 候选池**。Stage-2 `type_spans`(`ontology_typer.py:147-173`)对每个 span 的**表层文本**重新嵌入到 201 类并**完全忽略传入标签**——所以 dict/regex span **只需贡献 `{start,end,text}` + score**,Stage-2 用 bge 余弦重新 typing 并验证。**无需新增 Stage-1/Stage-2 管道**,是纯 pre-Stage-2 注入。
- **两个承重设计细节**(一句话版会漏):
  1. UNION 必须发生在现有贪心去重(`gliner_extractor.py:233-242`,按 score 降序、无重叠贪心)**之前**,且 dict/regex span 的 score 要**高到能在重叠时胜过 GLiNER span**(精确匹配的国药准字/规格给 score ≈ **1.0**),否则 GLiNER 的边界吞并 span 会在贪心塌缩里**驱逐**正确的 dict span。
  2. 用 **leftmost-longest(`iter_long`)而非 `iter()`**——后者吐出所有嵌入/重叠匹配,在无空格中文里过度合并;残余重叠交给已有贪心去重收尾。
- **正则修正(必做)**:候选的字面正则 `国药准字[HZSBTFJ]J?\d{8}` **有 bug**——`J?` 多余(J 已在字符类内),且漏了 `国药试字` 试制变体与较新的 `国药准字C+8` 经典名方类。**应改为 `国药(准|试)字[HZSBTFJ]\d{8}`**(并按需扩 C 类)。规格/剂量用 `\d+(\.\d+)?\s*(mg|g|μg|ug|IU|ml|%)` 一类;`/`、`%`、`.` 在字母数字间须设为**永不裁剪**(配合 B-向 boundary-snap 的 never-trim 守卫),保住 `0.25g/片`、`5mg/ml`。
- **离线·CPU 可行性**:**confirmed**。`pyahocorasick 2.3.1`(BSD-3,manylinux2014 wheels cp310-314,无需编译器);几十万规模 gazetteer 用纯 dict+正则或纯 Python trie 也够,C 扩展可选。
- **中文长文本适配度**:**moderate**(闭式实体高精度,但开放词不覆盖)。
- **集成成本**:**最低**(零模型、零 GPU)。
- **是否仍需 Tier-A 中文分词修复**:**是,不替代 Tier-A**——药品商品名、适应症、长文本自由 span 非闭式,仍靠 GLiNER + Tier-A 字符分词。
- **风险**(gazetteer 侧两个未决,把它从 adopt 降到 pilot):
  1. **LICENSE/再分发(#1 阻塞)**:NMPA 橙皮书(中国上市药品目录集)、DrugFuture 均带"版权所有 未经许可禁止转载或建立镜像",**无开放许可**可在气隙商用产品里再分发派生药名/厂商表——需法务签字或显式授权源。
  2. **覆盖率未测**:ACL-2022(arXiv 2207.02802)双刃——gazetteer 只在**覆盖测试期实体时**才帮忙;国药准字/规格/厂商相对 Tier-A-alone 的召回增量**未在留出集测过**,pilot 前必测。
  3. 厂商后缀正则(有限公司/制药/药业)是召回天花板 + 精度泄漏:抓全名漏裸品牌,无空格中文左边界模糊,需精度守卫,不能去掉 GLiNER。
- **建议**:**正则包(国药准字/剂量,~100% 精度)立即先发**(改对正则后);**gazetteer 侧门控在"留出集召回测量 + 法务清结的授权名单"之后**。
- **配套(同 PR 必发)**:① 把 `_entities_to_spans` 抽成可复用 `merge_spans(sources, flat_ner)` 供 Tier-A 窗口/子句 remap 与 dict/regex union 共用;② **单位感知 boundary-snap**(裁尾随 `的/，/。`,never-trim 守卫复用现有 `_CJKAwareWordsSplitter` 的 `[._\-/%]` 连接符类);③ **`text[start:end]==span.text` 往返断言**——在每次 window→doc→cell remap 后丢弃(并**计数/记日志,不静默吞**)失败 span,杜绝坏偏移污染 Stage-3 三元组或 tiptap 高亮(今 `_correct_span_offsets`(`document_annotator.py:547`)仅做包含式 remap,无子串校验)。**不要做嵌套合并**:tiptap 渲染器假设非重叠标注。
- **关键来源**:https://arxiv.org/abs/2207.02802 ; https://pypi.org/project/pyahocorasick/ ; https://www.nmpa.gov.cn/directory/web/nmpa/xxgk/fgwj/gzwj/gzwjyp/20020128010101658.html

---

### 四、Tier B/C 对比表

| # | 方案 | 层级 | 离线·CPU | 中文长文本 | 集成成本 | 仍需 Tier-A 分词? | 体量/许可 | 是否 beat Tier-A | 建议 |
|---|------|------|---------|-----------|---------|------------------|----------|-----------------|------|
| Gz | **词典+正则 gazetteer UNION** | 增强 Stage-1 | **confirmed** | moderate(闭式) | **最低** | 是(不替代) | pyahocorasick 2.3.1 BSD-3;数据需授权 | **是(正则侧)** | **正则立即发 / gazetteer pilot** |
| B-1 | gliner-x-large (mT5) 权重换 | 替换 Stage-1 | likely | weak | 中(gliner升级+stanza) | 是(必需) | 0.9B/1.2GB;v0 Apache,v0.5 **NC** | 否 | consider |
| B-2 | RaNER-CMeEE UNION | 增强 Stage-1 | confirmed | weak | 中高(modelscope+CRF) | 是(必需) | 409MB,Apache-2.0 | 否(仅药品名) | consider |
| B-3 | UIE / uie-medical-base | 替换 Stage-1 | likely | weak | **高**(每类一次前向) | 否(子词级) | ~470MB,Apache-2.0 | 否 | **avoid** |
| B-4 | AdaSeq 域内微调 GlobalPointer/RaNER | 替换 Stage-1 | likely | moderate | **高**(标注+GPU) | 仅退役 A1,留 A2/A3 | ~100M-base,Apache-2.0 | 潜在(最高天花板,未证) | consider(最后) |
| C-1 | Qwen3-4B-Instruct-2507 (GGUF) 选择性二遍 | Tier-C | confirmed | moderate | 中(+llama-cpp+权重) | 是(主路仍 GLiNER) | 2.5-4.3GB GGUF,Apache-2.0 | 潜在(高价值窗口,未证) | **pilot** |
| C-1' | NuExtract-2.0-2B (GGUF) | Tier-C | confirmed | moderate(中文未验) | 中 | 是 | 1.13GB Q5,MIT | 次选 | pilot(次选) |

---

### 五、"何时升级到 B/C"决策指南(按可度量条件)

**前置原则**:所有候选必须在真实药监语料(国药准字/规格/适应症/法规)上**测召回**(而非 valid-JSON 率或通用 benchmark F1)再决定。先量化 Tier-A 单独的 span-F1 / 召回。

1. **Tier-A 已修复边界、span-F1 ≥ ~0.80 且残留漏检集主要是闭式实体(国药准字/规格/厂商)** → 走 **Gazetteer 正则包**(立即,几乎零风险),不必动模型。这是默认第一步。
2. **Gazetteer 正则上线后,留出集上厂商/药名召回仍 < ~0.70 且 ACL 覆盖测量显示 gazetteer 帮得动** → 上 **gazetteer 名单**(法务清结后)+ 评估 **B-2 RaNER-CMeEE** 并联(仅当 A/B 出药品名实质召回增量且 bge 保住)。
3. **整句语义型实体(适应症"……所致的呼吸道感染"、跨句批准文号上下文)召回仍不足,且这些窗口可被便宜信号(低 GLiNER 置信、表格单元)选择性识别** → pilot **C-1 Qwen3-4B 选择性二遍**;采纳前提是**召回 bake-off 同时 beat Tier-A 与 gazetteer**,且延迟在严格触发器下可接受。
4. **域内 schema(国药准字/规格/法规)精度/召回有明确预算上限、闭式与监督召回均已穷尽、且有标注预算 + 一次性 GPU** → 才投 **B-4 AdaSeq 域内微调**(最后手段、最高天花板)。
5. **几乎不推荐**:B-1 gliner-x(通用 +0.04 zh_pud、4× CPU、商用版仅 +0.038)只在 1-4 都不解且需通用多语提升时按 Apache v0 + Tier-A 在真实药监集 A/B;B-3 UIE 仅对模糊类型(适应症/生产企业)**微调后**窄域 pilot,闭式字段永远留正则。

**采纳顺序(综合)**:Gazetteer 正则 + merge/boundary-snap/offset-assert(一个低成本确定性 PR)→ 两遍阈值放宽(Stage-1 降阈,需 in-domain gold set 后)→ B-2 RaNER 并联(pilot 后)→ B-3 UIE(若仍缺,药品专用类型)→ C-1 LLM(仅选择性二遍)→ B-1/B-4(最后,门控于度量缺口与标注预算)。

---

### Sources

- https://arxiv.org/abs/2207.02802 — Gazetteer-enhanced NER(ACL 2022),覆盖率为收益前提
- https://pypi.org/project/pyahocorasick/ — pyahocorasick 2.3.1,BSD-3,manylinux wheels
- https://www.nmpa.gov.cn/directory/web/nmpa/xxgk/fgwj/gzwj/gzwjyp/20020128010101658.html — NMPA 国药准字/国药试字 官方格式
- https://www.nmpa.gov.cn/datasearch/home-index.html — NMPA 数据"版权所有 未经许可禁止转载"(再分发阻塞)
- https://huggingface.co/knowledgator/gliner-x-large — gliner-x-large 0.9B,Apache-2.0,zh_pud 0.6794
- https://huggingface.co/knowledgator/gliner-x-large-v0.5 — v0.5 zh_pud 0.709,**cc-by-nc-sa-4.0 非商用**
- https://docs.knowledgator.com/docs/frameworks/gliner/pretrained-models/ — GLiNER-X 尺寸/编码器(mt5-small/base/large)
- https://www.modelscope.cn/api/v1/models/iic/nlp_raner_named-entity-recognition_chinese-base-cmeee — RaNER-CMeEE,409MB,Apache-2.0,CMeEE 9 类
- https://github.com/heiheiyoyo/uie_pytorch/blob/main/uie_predictor.py — UIE 每 schema 类型一次前向(CPU 延迟杀手)+ auto_splitter
- https://huggingface.co/LANZ/uie-medical-base — uie-medical-base,~470MB,Apache-2.0,卡片无 schema
- https://www.cnblogs.com/vipsoft/p/18281350 — UIE 零样本药品说明书实测(仅药品名,规格/用法需微调)
- https://github.com/modelscope/AdaSeq — AdaSeq(BERT-CRF/RaNER/GlobalPointer),Apache-2.0
- https://github.com/modelscope/AdaSeq/blob/master/docs/tutorials/model_inference_zh.md — AdaSeq 本地推理 {type,start,end,span}
- https://github.com/xhw205/GlobalPointer_torch/blob/main/data_loader.py — GlobalPointer 字符级 (start,end) 评分(无 license)
- https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507 — Qwen3-4B,Apache-2.0,262K 上下文,非思考
- https://qwenlm.github.io/blog/qwen3/ — Qwen3 C-Eval 77.5,119 语,中文 BPE
- https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF — Qwen3-4B GGUF 量化尺寸(Q4_K_M 2.5GB 等)
- https://huggingface.co/numind/NuExtract-2.0-2B — NuExtract-2.0-2B,MIT,Qwen2-VL-2B 基座
- https://huggingface.co/numind/NuExtract-2.0-2B-GGUF — NuExtract GGUF,省略 mmproj 走纯文本
- https://til.simonwillison.net/llms/llama-cpp-python-grammars — llama-cpp-python GBNF/JSON-schema 约束解码(思考模式下失效)
- https://github.com/fastino-ai/GLiNER2 — GLiNER2 单次前向(实体/分类/关系),CPU 优先
