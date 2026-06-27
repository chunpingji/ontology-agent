# Word / PDF 文档实体识别优化技术方案 — Gap 分析

> 基准：`docs/Word-PDF文档实体识别优化技术方案.md` v1.0 (2026-06-27)
> 对比：代码实际状态 @ `007-rnd-document-fact-source` HEAD (2026-06-27)

---

## B. 文档有但代码有偏差

| # | 章节 | 文档描述 | 代码实际 | 修复建议 |
|---|------|---------|---------|---------|
| B1 | §4.2 属性标签示例 | "这些属性标签（'NOAEL'、'分子式'、'PDE'、**制剂剂型**）天然就是 GLiNER 的零样本标签" | "制剂剂型"是数据属性标签，恰好是本轮 seed_labels 回归的罪魁祸首——它作为阶段一种子导致 GLiNER 把属性名误检为实体 span，经嵌入匹配到"中药制剂"。属性标签**仅用于阶段三** | **更新文档**：删除"制剂剂型"示例，改为纯阶段三语境（如"NOAEL"、"PDE值"、"给药途径"），并加注"仅用于阶段三 `_property_schema_for_class()`" |
| B2 | §3.2 / §3.3 | 引用行号 `429-433`、`444-452` | 本轮新增 `progress_fn` / `checkpoint` 参数后行号漂移约 +40 行 | **更新文档**：改为函数名锚点引用（如 `annotate_word() Pass 1 前缀拼接`），不再硬编码行号 |
| B3 | §5.1 种子标签 | 文档仅说"两个标签源"（domain 类 + 根类） | `OntologyEngine.data_property_labels()` (L390-401) 仍存在且 docstring 写"供 NER 种子标签第三源"——但 `seed_labels()` 已不调用它。方法保留给阶段三 `_property_schema_for_class()` 使用 | **更新文档**：加注说明 `data_property_labels()` 保留给阶段三但不参与种子标签；同步更新 engine docstring |

---

## C. 代码已实现但文档未记录

| # | 能力 | 代码位置 | 说明 |
|---|------|---------|------|
| C1 | **标注任务暂停 / 恢复 / 重新运行** | `progress.py` 控制总线（`_annotation_control` dict + `set/get/clear_annotation_control`）；`extraction.py` 三个端点 `POST /jobs/{id}/annotation/{pause,resume,rerun}`；`document_annotator.py` `_annotate_texts()` checkpoint 机制（阶段间检查 `should_pause_fn`，序列化 `raw_spans` / `typed_spans` 到 JSON checkpoint 文件） | 文档完全未提及标注任务可控——这是本轮新增的交互能力，建议补为 §11 |
| C2 | **实时子阶段进度推送（SSE）** | `_precompute_annotation_bg` → `progress_bus.publish()` 发射 `annotation_stage` 字段（`"gliner"` / `"typing"` / `"triples"` / `"done"` / `"paused"`）；前端 `JobProgress` 组件消费 SSE 事件渲染子阶段指示器 | 文档 §8 前端展示仅描述静态查看（WordViewer / ExcelViewer / TriplePanel），未提及实时进度流 |
| C3 | **NER 三元组自动入复核队列** | `_persist_ner_triples` (extraction.py:317-339)：有属性的实体三元组 → `ExtractionCandidate`（`candidate_kind='ner_triple'`、`group_key='ner:{entity_text}'`、`source_ref='ner#seg{idx}:{start}-{end}'`） | 文档 §10 覆盖矩阵仅一行"RDF 三元组生成/写入 → 已落地"，未展开入队机制、候选种类标记、溯源格式 |
| C4 | **Word 表格按 body XML 顺序保持位置** | `annotate_word` 遍历 `doc.element.body`，将 `_paras` / `_tables` 索引到 XML 元素再按序处理 | 文档 §6.1 提及了交叉保持原位的 `for child in doc.element.body` 模式，但 §3 表头前缀一节未说明表格在文档中的定位机制 |
| C5 | **字号→标题层级完整阈值** | `_font_size_heading_level`：≥20pt→h1、≥16pt→h2、≥14pt→h3 | 文档 §6.3 仅写"大字号"和一个阈值示例，未列出完整三档 |

---

## D. 文档提及但代码未实现

| # | 能力 | 文档位置 | 现状 | 影响 |
|---|------|---------|------|------|
| D1 | **skos:altLabel / owl:sameAs 同义词增强** | §10 尾部"尚未实现"第 2 项 | `type_spans` 仅匹配 `rdfs:label`，不利用同义词属性 | 对中英文表述不一致的实体召回有损（如"API" vs "活性成分"） |
| D2 | **Object Property 关系抽取** | §10 第 3 项 | 仅抽取 `DatatypeProperty` 值，实体间关系（"含有"、"生产于"等）未覆盖 | 知识图谱缺少实体关联边 |
| D3 | **向量数据库替代进程内缓存** | §10 第 1 项 | `type_spans` 用 numpy 矩阵乘 + `_index_cache` dict；当前 SLPRA ~201 类，性能可接受 | 选型 **LanceDB**（嵌入式、零运维、列存向量原生）；embedding 模型与 GLiNER backbone 对齐——GLiNER multi-v2.1 编码器为 `mdeberta-v3-base`（768-dim），阶段二语义匹配的 `SentenceTransformerEmbedder` 应切换到同维同语系模型（如 `bge-base-zh-v1.5` 768-dim 或直接复用 mdeberta token embedding），确保向量空间一致、避免跨模型 cosine 偏移 |

---

## E. 表格内实体检出率低——根因分析

> 对比段落文本，Word 表格单元格的 NER 召回率系统性偏低。以下为叠加根因，按影响程度排序。

| # | 根因 | 代码位置 | 机制 | 影响 |
|---|------|---------|------|------|
| E1 | **单元格独立推理、上下文过短** | `annotate_word()` L462-478：每个 `cell.text.strip()` 作为独立 segment 送入 `_annotate_texts()` | GLiNER 基于 Transformer 注意力——输入文本越短，置信度越低，大量 span 低于 `gliner_threshold=0.5` 被丢弃。段落通常几十到几百字，表格单元格往往仅几个词 | **主因**：阶段一召回系统性缺失 |
| E2 | **表头前缀拼接策略有限** | `annotate_word()` L471-475：数据行拼接 `f"{hdr}：{cell_text}"`，不利用同行其他列、不利用表前标题段 | 表头自身可能无语义（"名称"/"值"/"备注"），拼接后仍上下文不足；一行内 `(原料药名称, 剂型, 规格, 批号)` 各自孤立推理，丢失行内语义关联 | 阶段一置信度提升有限 |
| E3 | **阶段二上下文窗口在短文本上失效** | `_span_with_context()` L62-73：`window=40` 字符从 `seg_text` 前后取上下文；`_type_and_filter_spans()` L86-94 | 表格 cell 的 `seg_text` 本身就是短文本（含前缀），前后窗口几乎取不到有效内容，嵌入归类失去语义锚点 → `type_spans` 返回 None | 阶段二过滤掉阶段一仅存的少量 span |
| E4 | **表头行不应走 NER** | `annotate_word()` L466-469：`ri == 0` 行的每个单元格（"原料药名称"/"PDE值"/"NOAEL"）也加入 `all_texts` 走完整三阶段 NER | 表头是属性标签而非实体（与 B1 同源问题）——产生噪声 span，同时浪费 GLiNER 推理预算 | 噪声 + 资源浪费 |
| E5 | **合并单元格重复 / 嵌套表格遗漏** | `annotate_word()` 遍历 `table.rows` 不检测 `vMerge` 续行；`cell.text` 含多段落用 `\n` 连接但不区分段落边界；嵌套 `<w:tbl>` 不递归 | 合并单元格同一文本重复 NER；嵌套表格内容完全丢失 | 重复检出 + 信息遗漏 |

### 修复方向

| 根因 | 建议 | 工作量 |
|------|------|--------|
| E1+E2 | **行级上下文拼接**：将同行所有 `hdr：cell` 拼接为一个完整 segment 送 GLiNER，而非逐 cell 推理；表前标题段作为 prompt 前缀追加 | M |
| E3 | **传入行级/表级上下文**：`_span_with_context` 的 `seg_text` 改为行级拼接文本，使 window 能捕获同行其他列的语义 | S |
| E4 | **表头行跳过 NER**：`ri == 0` 行仅作前缀/上下文材料，`prefix_lens` 标记后不参与 `_annotate_texts` | S |
| E5 | **vMerge 去重 + 嵌套表递归**：检测 `<w:vMerge w:val="continue"/>` 跳过续行 cell；递归遍历 cell 内嵌套 `<w:tbl>` | S |

---

## 修复优先级

| 优先级 | 项 | 动作 | 工作量 |
|--------|---|------|--------|
| **P0** | B1 — "制剂剂型"误导示例 | 更新文档 §4.2 示例 + 加注 | 5 min |
| **P1** | C1 + C2 — 暂停/恢复 + 实时进度 | 补文档新章节 §11（标注任务控制：控制总线、checkpoint 格式、SSE 事件协议、前端 UI） | 30 min |
| **P1** | C3 — NER 三元组入复核队列 | 补到 §4 末尾或独立 §4.8 小节 | 15 min |
| **P2** | B2 — 行号漂移 | 全文替换硬编码行号为函数名锚点 | 10 min |
| **P2** | B3 — `data_property_labels` docstring | 更新 engine docstring "第三源" → "阶段三属性标签查询" | 5 min |
| **P2** | C5 — 字号阈值完整列出 | 更新 §6.3 加完整三档表格 | 5 min |
| **P1** | E1+E2 — 行级上下文拼接 | 重构 `annotate_word` 表格文本组装：同行拼接 + 表前标题段作为上下文 | 2 h |
| **P1** | E4 — 表头行跳过 NER | `ri == 0` 行仅作前缀材料，不参与 `_annotate_texts` | 30 min |
| **P2** | E3 — 阶段二上下文窗口适配 | `_span_with_context` 的 `seg_text` 改为行级拼接文本 | 30 min |
| **P2** | E5 — vMerge 去重 + 嵌套表递归 | 检测合并单元格续行 + 递归嵌套 `<w:tbl>` | 1 h |
| **P3** | D1–D3 | 已在文档标记"尚未实现"，暂无紧迫性 | — |
