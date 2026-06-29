# Word / PDF 文档实体识别优化技术方案

> 版本 1.0 | 2026-06-27 | 基于 008-gliner-ner-extraction 已落地架构

---

## 1. 问题域

医药领域的研发文档（Word .docx、PDF）具有以下特征：

| 挑战 | 表现 |
|------|------|
| **中英文混杂** | 段落中交替出现 "吉非替尼 (Gefitinib)"、"EGFR抑制剂" |
| **结构化+非结构化并存** | 同一文档内既有表格（药品注册信息表）又有自由文本段落（工艺描述） |
| **短文本缺乏上下文** | 表格单元格如 "150mg" 脱离表头无法判定是剂量、规格还是含量 |
| **本体类与文本表述不一致** | TTL 中是 `slpra:ActiveIngredient`，文档中写"活性成分"或 "API" |
| **属性值散落在实体周围** | 实体 "吉非替尼" 的 NOAEL、PDE、分子式等属性值分布在同句或邻近句中 |

本方案记录项目已落地的三阶段标注架构及两项关键优化的工程细节。

---

## 2. 整体架构：三阶段标注流水线

```
文档（Word / Excel）
       │
       ▼  parse_word / parse_excel
┌──────────────────────────────────────────────────────┐
│  Pass 1：收集所有文本段 → all_texts[]               │
│  （段落原文 / 表格单元格拼入表头前缀）                  │
└──────────┬───────────────────────────────────────────┘
           │
           ▼  _annotate_texts() — 一次 batch，三阶段
┌──────────────────────────────────────────────────────┐
│                                                      │
│  阶段一  GLiNER 定界                                  │
│  ───────────────────                                 │
│  seed_labels(engine) → 种子标签集（≤40）              │
│  extract_batch_with_spans(all_texts, labels)         │
│  → raw spans [{start, end, text, label, score}]      │
│                                                      │
│  阶段二  嵌入归类（语义 typing）                       │
│  ───────────────────                                 │
│  _type_and_filter_spans(raw, texts, engine)          │
│  每个 span 取 ±40 字上下文窗口文本 → 嵌入向量          │
│  与全部本体类标签做余弦匹配 → 最具体类                  │
│  < 0.50 阈值丢弃；区分度 < 0.20 丢弃                  │
│  → typed spans [{..., iri, label, score}]            │
│                                                      │
│  阶段三  属性三元组抽取（二次 GLiNER）                  │
│  ───────────────────                                 │
│  _extract_property_triples(typed, texts, engine)     │
│  按已归类实体的本体类数据属性标签再跑 GLiNER            │
│  → triples [{entity, class, properties}]             │
│                                                      │
└──────────┬───────────────────────────────────────────┘
           │
           ▼  Pass 2
┌──────────────────────────────────────────────────────┐
│  组装可渲染结构：                                      │
│  Word → tiptap ProseMirror JSON（entity mark 内嵌）   │
│  Excel → {headers, rows[{value, annotations}]}       │
│  + 属性三元组面板                                     │
└──────────────────────────────────────────────────────┘
```

**性能关键设计**：整篇文档的所有文本段先收集成一个列表，一次 GLiNER batch 推理 + 一次嵌入归类，再把 span 分发回各段。逐段调用在 CPU 上对几百段会到分钟级（请求超时）。

---

## 3. 优化一：表格行级上下文拼接

### 3.1 问题

Word 表格的单元格文本通常非常简短：

| 药品名称 | 规格 | 剂型 |
|----------|------|------|
| 吉非替尼 | 250mg | 片剂 |

单独对 "250mg" 跑 NER，模型无法区分它是"规格"、"剂量"还是"含量"。逐 cell 推理上下文过短，GLiNER 置信度系统性偏低。

### 3.2 方案

在 `annotate_word()` 的 Pass 1 阶段，将同行所有单元格**以 `hdr：val | hdr：val` 格式拼接为一个行级 segment** 送入 NER，而非逐 cell 推理：

```python
# annotate_word() → _build_row_segment() 行级拼接
hdr = headers[ci]  # 如 "规格"
cell_text = _tc_text(tc_elem)
if hdr and cell_text:
    fragment = f"{hdr}：{cell_text}"  # "规格：250mg"
    # 记录 cell 在行级 segment 中的偏移位置，用于 Pass 2 坐标还原
```

NER 在 "规格：250mg" 上运行后，阶段二的嵌入归类能正确将 "250mg" 匹配到本体类 `slpra:Specification` 而非 `slpra:Dosage`。

### 3.3 偏移校正

NER 返回的 span 坐标是基于行级拼接文本的。在 Pass 2 组装 tiptap 节点前，需要通过 `_correct_span_offsets()` 将行级坐标**映射回各 cell 内坐标**：

```python
# annotate_word() Pass 2 → _correct_span_offsets()
# 将行级 segment 上的 NER span 坐标映射回各 cell 内坐标
corrected = _correct_span_offsets(all_spans[abs_idx], offsets, headers)
# corrected = [(col_idx, adjusted_span), ...]
# adjusted_span 的 start/end 相对于 cell 文本，而非行级拼接文本
```

偏移校正仅对表格数据行执行（段落 segment 无 cell 偏移），确保段落标注零影响。

### 3.4 效果

| 场景 | 无前缀 | 有前缀 |
|------|--------|--------|
| "250mg" 归类 | 命中多个类（规格/剂量/含量），余弦接近被丢弃 | 明确命中 Specification |
| "吉非替尼" 归类 | 正确（实体本身有语义） | 正确（前缀不干扰） |
| 空单元格 | 跳过 | 跳过（`if hdr and cell_text` 守卫） |

---

## 4. 优化二：二次 GLiNER 属性三元组抽取

### 4.1 问题

阶段一/二完成后，已知一段文本中的 "吉非替尼" 是 `slpra:DrugProduct` 类的实体。但这个实体有哪些**属性值**（NOAEL=5mg/kg、分子式=C₂₂H₂₄ClFN₄O₃、剂型=片剂）散落在实体周围的文本中，尚未抽取。

传统做法需要关系抽取模型或人工规则。本方案复用同一个 GLiNER 模型，**以目标类的数据属性标签作为第二次 NER 的标签集**，在实体的上下文窗口中直接定位属性值。

### 4.2 原理

RDF 本体中，每个类的数据属性（`owl:DatatypeProperty` + `rdfs:domain`）定义了该类实例应拥有的属性：

```turtle
slpra:noael  a owl:DatatypeProperty ;
    rdfs:domain slpra:DrugSubstance ;
    rdfs:label "NOAEL" .

slpra:molecularFormula  a owl:DatatypeProperty ;
    rdfs:domain slpra:DrugSubstance ;
    rdfs:label "分子式" .
```

这些属性标签（"NOAEL"、"PDE值"、"给药途径"）天然就是 GLiNER 的零样本标签——告诉模型"在这段文本里找这些东西"。

> **注意**：属性标签仅用于**阶段三** `_property_schema_for_class()` 的 per-class GLiNER 标签集，
> **不**进入阶段一种子标签。三阶段标签来源：阶段一 `seed_labels()` 使用本体类标签（≤40），
> 阶段二嵌入归类不使用标签，阶段三 `_property_schema_for_class()` 使用目标类数据属性标签。

### 4.3 实现流程

```
已归类实体 typed_spans
    │
    ├─ 按 class_iri 分组（by_class）
    │
    ▼ 对每个类：
┌────────────────────────────────────────────────────┐
│  1. _property_schema_for_class(engine, class_iri)  │
│     → 查询该类所有 data_property 的 label + iri     │
│     → {"labels": ["NOAEL","分子式","PDE"],          │
│        "label_to_iri": {"NOAEL": "slpra:noael"}}   │
│                                                    │
│  2. 为每个实体 span 取 ±200 字上下文窗口             │
│     _span_with_context(seg_text, start, end,       │
│                        window=200)                  │
│     窗口比阶段二大 5 倍（200 vs 40），因为属性值      │
│     可能距实体更远                                   │
│                                                    │
│  3. GLiNER 批量推理（同类实体一次 batch）             │
│     extract_batch_with_spans(contexts, labels)     │
│                                                    │
│  4. 去重回填（同 label 仅保留首个命中）               │
│     → properties: [{iri, label, value}]            │
└────────────────────────────────────────────────────┘
```

### 4.4 代码核心路径

```
_extract_property_triples()              ← 阶段三入口
    │
    ├── _property_schema_for_class()     ← 从 OntologyEngine 派生标签
    │       └── engine.get_data_properties_by_domain(class_iri)
    │
    ├── _span_with_context(..., window=200)  ← 扩大上下文窗口
    │
    └── extractor.extract_batch_with_spans() ← 二次 GLiNER，按类批量
```

源码位置：`document_annotator.py` → `_extract_property_triples()` / `_property_schema_for_class()`

### 4.5 输出数据结构

每个三元组包含实体定位 + 抽取到的属性值对：

```json
{
  "entity_text": "吉非替尼",
  "entity_class_iri": "http://slpra.example.org/DrugSubstance",
  "entity_class_label": "原料药",
  "segment_index": 5,
  "span_start": 12,
  "span_end": 16,
  "properties": [
    {"iri": "slpra:noael", "label": "NOAEL", "value": "5mg/kg"},
    {"iri": "slpra:molecularFormula", "label": "分子式", "value": "C₂₂H₂₄ClFN₄O₃"}
  ]
}
```

前端 `TriplePanel` 组件按类分组展示，每个实体可展开查看属性列表。

### 4.6 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 上下文窗口大小 | 200 字符（vs 阶段二的 40） | 属性值可能距实体更远，如 "吉非替尼…经研究，其 NOAEL 为 5mg/kg" |
| 按类分组批量推理 | 同类实体共享标签集，一次 batch | 避免逐实体调用，CPU 推理效率提升 N 倍 |
| 同 label 去重 | `seen_labels` 集合，保留首个 | 同一上下文窗口中 "NOAEL" 可能命中多次，取最靠近的 |
| 无属性类的处理 | `properties: []`，结构保留 | 不丢弃实体，仅标记无属性可抽（优雅降级） |
| GLiNER 不可用 | 返回 `[]`，整个阶段三跳过 | 阶段一/二的实体标注不受影响（降级不扩散） |

### 4.7 与阶段一/二的对比

| 维度 | 阶段一 GLiNER | 阶段三 GLiNER |
|------|--------------|--------------|
| **目的** | 定界：找实体边界 | 抽值：找属性值 |
| **标签来源** | `seed_labels()`：本体类标签（≤40） | `_property_schema_for_class()`：目标类数据属性标签 |
| **标签规模** | 23-40 | 通常 5-15（单个类的属性数） |
| **上下文窗口** | ±40 字符 | ±200 字符 |
| **批量粒度** | 全文档所有段一次 batch | 按类分组，每类一次 batch |
| **输出** | `{start, end, text, label, score}` | `{iri, label, value}` |

### 4.8 NER 三元组入复核队列

阶段三抽取的属性三元组通过 `_persist_ner_triples()` 自动转化为待审核候选项：

- **候选类型**：`candidate_kind='ner_triple'`
- **分组键**：`group_key='ner:{entity_text}'`——同一实体的多个属性值归为一组
- **溯源引用**：`source_ref='ner#seg{idx}:{start}-{end}'`——记录实体在哪个 segment 的哪个字符位置被识别
- **入队条件**：仅当实体存在已抽取属性（`properties` 非空）时创建 `ExtractionCandidate`

复核队列允许领域专家在前端逐条确认或修正 NER 自动抽取的属性值，确保入库数据质量。

---

## 5. 阶段二补充说明：种子标签策略与嵌入归类

### 5.1 种子标签生成策略

`seed_labels(engine)` 从两个标签源按优先级递减生成（上限 40，GLiNER 标签 >50 注意力稀释）：

1. **数据属性 domain 类**（优先）：`engine.data_property_domain_classes()`
   - 如 "PDE计算"、"MACO计算"、"洁净区"——精确召回
2. **模块根类**（depth==0）：补充高层类别
   - 如 "药物产品"、"设备"——兜底召回

> **设计决策**：数据属性标签（"NOAEL"、"PDE值"…）**不**进入种子标签——它们是字段名
> 而非实体类型。混入种子会导致 GLiNER 把文档中的属性名误检为实体 span，且大量英文属性
> 标签稀释中文 NER 注意力。属性标签仅用于阶段三的 per-class GLiNER 标签集
> （`_property_schema_for_class()`）。`OntologyEngine.data_property_labels()` 方法保留
> 供阶段三属性标签查询使用，不参与 `seed_labels()` 种子标签生成。

源码位置：`ontology_typer.py` → `seed_labels()`

### 5.2 嵌入归类核心逻辑

`type_spans()` 对每个 span 文本做余弦相似度匹配：

- **阈值 0.50**：允许药品代号（HRS-1234）、属性名（性状/溶解性）通过
- **区分度门槛 0.20**：best_score 需高出所有类平均余弦至少 0.20，否则视为无明确类归属
- **近似并列取最深**：相差 0.05 以内的命中视为"同样好"，取层级最深（最具体）的类
- **嵌入器**：`SentenceTransformerEmbedder`（默认 `bge-large-zh-v1.5`），归一化向量，余弦等价于点积

源码位置：`ontology_typer.py` → `type_spans()`

### 5.3 GLiNER 仅负责定界

阶段一 GLiNER 给出的种子标签在阶段二被丢弃，最终类别由嵌入归类决定。这解耦了 NER 召回与语义分类：

- GLiNER 擅长找 span 边界（"吉非替尼" 从第 12 到第 16 字符）
- 嵌入模型擅长语义匹配（"吉非替尼" → `slpra:DrugSubstance`）

---

## 6. Word 文档处理的工程细节

### 6.1 段落与表格交叉保持原位

`annotate_word()` 遍历 `doc.element.body` 子元素而非分别遍历 `doc.paragraphs` 和 `doc.tables`，确保段落与表格在 tiptap JSON 中保持文档原始顺序：

```python
for child in doc.element.body:
    if child in _paras:    # 段落
        ...
    elif child in _tables: # 表格
        ...
```

### 6.2 行内样式与实体标注合并

Word 的粗体/斜体/下划线/删除线通过 `_para_runs_and_text()` 提取为 `(start, end, marks)` 列表，在 `_inline_nodes()` 中与 NER span 按字符边界切段合并：

- 同一文本片段可同时携带 `bold` mark + `entity-annotation` mark
- 前端 tiptap 编辑器（只读模式）同时渲染格式和实体高亮

### 6.3 标题层级还原

中文文档常用"正文"样式 + 手动字号排版而非内置标题样式。双重回退策略：

1. `_heading_level()`：按样式名匹配（"Heading 1" / "标题 1"）
2. `_font_size_heading_level()`：按字号启发式，完整三档阈值：

| 字号范围 | 映射层级 | 常见用途 |
|----------|----------|----------|
| ≥ 20pt（小二以上） | h1 | 文档主标题 |
| ≥ 16pt（三号） | h2 | 章节标题 |
| ≥ 14pt（四号） | h3 | 子节标题 |
| < 14pt | paragraph | 正文 |

---

## 7. 降级与容错设计

```
┌──────────────────────┬───────────────────────────────────┐
│ 故障场景              │ 降级行为                           │
├──────────────────────┼───────────────────────────────────┤
│ GLiNER 未安装/缺权重  │ 三个阶段均跳过，返回无标注文档      │
│ 嵌入器未安装          │ 阶段二全返回 None，span 全丢弃      │
│ 目标类无数据属性      │ 阶段三该类 properties=[] 结构保留   │
│ GLiNER 单次推理异常   │ 捕获异常，返回空结果，不中断流水线  │
│ 表格表头为空          │ 单元格不拼前缀，prefix_len=0       │
│ 空段落/空单元格       │ 跳过，不送 NER                     │
└──────────────────────┴───────────────────────────────────┘
```

每个阶段独立降级、绝不抛出异常，确保**结构化抽取主路径零回归**。

---

## 8. 前端展示

### 8.1 文档标注视图

- **Word**：`WordViewer` — tiptap 只读编辑器，`entity-annotation` mark 高亮实体，hover tooltip 显示"实体类型 · 置信度%"
- **Excel**：`ExcelViewer` — 每个单元格的 `annotations` 渲染为行内高亮标签

### 8.2 属性三元组面板

`TriplePanel` 组件：
- 按本体类分组，每组一色（取自 `ENTITY_PALETTE`）
- 每个实体可展开查看其数据属性列表（属性名: 值）
- 摘要行显示 `{有属性的实体数} / {总实体数}`

### 8.3 实体统计

`extractEntityStats()` 遍历 tiptap 节点或 Excel 行，按 label 去重计数，供面板展示各类型实体数量。

---

## 9. 配置参数速查

| 参数 | 默认值 | 作用 | 位置 |
|------|--------|------|------|
| `gliner_threshold` | 0.35 | GLiNER 推理置信度阈值 | `settings` |
| `DEFAULT_TYPE_THRESHOLD` | 0.50 | 嵌入归类接受阈值 | `ontology_typer.py` |
| `_DISCRIMINABILITY_MARGIN` | 0.20 | 区分度门槛（best - mean） | `ontology_typer.py` |
| `_SPECIFICITY_MARGIN` | 0.05 | 近似并列窗口（取最深） | `ontology_typer.py` |
| `_CONTEXT_WINDOW` | 40 字符 | 阶段二上下文窗口 | `document_annotator.py` |
| `_PROPERTY_CONTEXT_WINDOW` | 200 字符 | 阶段三上下文窗口 | `document_annotator.py` |
| `_SEED_LABEL_CAP` | 40 | GLiNER 种子标签上限 | `ontology_typer.py` |
| `semantic_embedding_model` | bge-large-zh-v1.5 | 嵌入模型 | `settings` |
| `gliner_model_path` | 本地路径 | GLiNER 权重路径 | `settings` |

---

## 11. 标注任务生命周期控制

### 11.1 暂停 / 恢复 / 重新运行

标注任务支持运行时控制，通过 REST 端点操作：

| 端点 | 方法 | 作用 |
|------|------|------|
| `/jobs/{id}/annotation/pause` | POST | 在当前阶段完成后暂停标注 |
| `/jobs/{id}/annotation/resume` | POST | 从暂停点恢复标注（加载 checkpoint） |
| `/jobs/{id}/annotation/rerun` | POST | 丢弃所有标注结果，从头重新运行 |

**控制总线**：`progress.py` 维护 `_annotation_control` 字典，`set/get/clear_annotation_control()` 函数提供线程安全的控制信号读写。

**Checkpoint 机制**：`_annotate_texts()` 在阶段间检查 `should_pause_fn()` 回调。若收到暂停信号，将当前进度序列化为 JSON checkpoint（包含 `raw_spans`、`typed_spans` 中间结果），返回给调用方保存。恢复时传入 checkpoint 参数，跳过已完成的阶段继续执行。

### 11.2 实时子阶段进度推送（SSE）

标注过程通过 Server-Sent Events 实时推送子阶段进度：

```
_precompute_annotation_bg()
    │
    ├── progress_bus.publish(annotation_stage="gliner")    → 阶段一开始
    ├── progress_bus.publish(annotation_stage="typing")    → 阶段二开始
    ├── progress_bus.publish(annotation_stage="triples")   → 阶段三开始
    ├── progress_bus.publish(annotation_stage="done")      → 全部完成
    └── progress_bus.publish(annotation_stage="paused")    → 暂停中
```

前端 `JobProgress` 组件通过 `/ws/annotation-progress` SSE 端点消费事件，渲染子阶段指示器（当前阶段高亮 + 已完成阶段勾选），使用户在长时间标注过程中了解实时进展。

---

## 10. 与你方案对照的覆盖矩阵

| 你的方案步骤 | 本项目对应实现 | 状态 |
|---|---|---|
| 第一步：从 TTL 提取语义字典 | `seed_labels()` + `_property_schema_for_class()` + `build_class_index()` | 已落地 |
| 第二步：多语 GLiNER 召回 | `GlinerExtractor.extract_batch_with_spans()` | 已落地 |
| 第三步-1：精确匹配 | 表头 `column_mapping` 确定性 IRI 映射 (pipeline.py) | 已落地 |
| 第三步-2：跨语言嵌入匹配 | `type_spans()` + `SentenceTransformerEmbedder` 余弦匹配 | 已落地 |
| 第三步-3：类别约束过滤 | 区分度门槛 + 深度优先选最具体类 | 已落地 |
| 第四步：属性/关系映射 | `_extract_property_triples()` 二次 GLiNER | 已落地 |
| 表格单元格表头前缀注入 | `annotate_word()` prefix 拼接 + 偏移还原 | 已落地 |
| 向量数据库索引 | 当前用进程内缓存 + numpy 矩阵乘（本体规模 ≤300 类可承受） | 待评估 |
| RDF 三元组生成/写入 | `_persist_ner_triples()` 持久化到审核队列 | 已落地 |

**尚未实现但你方案中提及的扩展点**：
- 向量数据库（Faiss/Milvus）替代进程内缓存——当本体规模超过 1000+ 类时有必要
- skos:altLabel / owl:sameAs 同义词增强——当前仅用 rdfs:label，未利用同义词属性
- 对象属性（Object Property）关系抽取——当前仅抽数据属性值，实体间关系未覆盖
