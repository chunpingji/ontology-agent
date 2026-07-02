# 本体动态对象映射 — 能力 GAP 分析与改进方案

**日期**: 2026-07-02 | **分支**: `013-llm-template-report-enhance` | **状态**: 分析完成·方案待评审

## 1. 背景与目标

本系统以 OWL/RDFS 本体（T-Box）为权威 schema，驱动药品注册文档的数据抽取、推理和报告生成。
核心诉求是：**将异构事实源（文档、数据库表、API）中的原始数据，动态映射为本体声明的对象类型（Class）、数据属性（DataProperty）、对象属性（LinkType）。**

当前版本已实现了多源抽取管线（Excel/Word/Database/DocRepo），但各源的映射逻辑分散在独立模块中硬编码，未与本体元数据模型（E6 `OntologyClassMapping`）联动。本文档系统分析映射管线的能力 GAP，并提出分层改进方案。

---

## 2. 现有映射管线概览

### 2.1 三条数据路径

| 路径 | 数据源 | 入口 | 映射配置 | 产出 |
|------|--------|------|----------|------|
| **A. 结构化抽取** | Excel/Word 表格 | `pipeline.py:run_extraction_pipeline` | `ExtractionConfig.column_mapping` | `ExtractionCandidate` (A-Box 个体候选) |
| **B. 文档关系抽取** | Word 正文 | `relation_extractor.py:extract_relationships` | 硬编码 `_ENDPOINT_FINDERS` 字典 | `edges[]` (SPO 三元组) |
| **C. AST 模板覆盖** | edges + rules | `coverage_validator.py:validate_coverage` | 模板 JSON slot `source` 声明 | `CoverageManifest` (slot 填充状态) |

### 2.2 事实桥梁：edges → Facts

`fact_bridge.py:edges_to_facts`（第 25–76 行）将抽取 edges 转换为推理引擎消费的 `Facts` 对象：

```python
@dataclass
class Facts:
    drug_classes: list[str]                    # 药品分类标签
    relations: dict[str, list[str]]            # 对象属性短名 → 关联类 IRI 列表
    data_values: dict[str, Any]                # 数据属性短名(IRI) → 值
    scalars: dict[str, Any]                    # 数据属性标签(label) → 值
    alignments: dict[str, list[str]]           # ← 预留，从未填充
```

### 2.3 E6 OntologyClassMapping 现状

```python
# backend/app/models/ontology_meta.py:193-204
class OntologyClassMapping(VersionMixin, TimestampMixin, Base):
    __tablename__ = "ontology_class_mapping"
    class_id     → ForeignKey("ontology_class.id")   # 本体类
    mapping_type → String(20)                         # "slpra_iri" | "bfo" | "source_field"
    target       → String(500)                        # 映射目标标识
    source_system→ String(50)                         # 来源系统名（可选）
    health       → String(20)                         # "ok" | "unmapped" | "drift" | "orphan"
```

**现状**：E6 仅供 UI 展示（`ontology-mapping-panel.tsx`），无任何运行时代码消费其声明来驱动抽取或推理。

---

## 3. 已确认的 6 个 GAP

### GAP-1: E6 Mapping 是死数据 — 无运行时消费者

**位置**: `ontology_meta.py:193-204`, `ontology_meta_store.py:259`

**现象**: `OntologyClassMapping` 维护了 `slpra_iri`/`bfo`/`source_field` 三种映射类型，但这些映射仅在 `_mapping_dto` 中序列化供前端展示。`ExtractionConfig.column_mapping` 和模板 slot `source` 是完全独立的硬编码映射，未读取 E6。

**影响**: 分析师在本体编辑器中维护的映射声明与实际抽取管线脱节 — 改映射不影响抽取行为。

### GAP-2: `edges_to_facts` 映射逻辑硬编码，不查询本体

**位置**: `fact_bridge.py:25-76`

**现象**:
- `drug_classes` 依赖硬编码 `"DrugProduct" in obj_class` 字符串匹配
- `data_values` 按 IRI 短名索引（`_short_name` 截取），不检查属性是否声明在当前类的 domain 上
- `scalars` 按 label 原样存入，与本体受控词表（`controlled_vocab`）无对齐
- `alignments` 字段从未被填充

**影响**: 本体中新增/修改 data property 或 link type 后，`Facts` 构建不会自动适配；需要手动修改 Python 代码。

### GAP-3: 端点 finder 按 range IRI 硬编码注册

**位置**: `relation_extractor.py:614` (`_ENDPOINT_FINDERS` 字典)

**现象**: `extract_relationships` 在获取文档类的对象属性后，按 `range_iri` 在 `_ENDPOINT_FINDERS` 字典中查找对应的端点发现函数。新增本体类（如新设备类型、新物料类型）后，**必须手写新的 finder 函数并注册**，无法从本体 schema 自动派生抽取逻辑。

**影响**: 本体扩展无法自动获得关系抽取能力，新类 = 新代码。

### GAP-4: AST 模板 slot `source` 静态声明 — 无动态属性发现

**位置**: 模板 JSON 中 slot 的 `source` 字段

**现象**: slot 的 `source` 配置（`extraction`/`rule`/`manual`/`constant`）在模板 JSON 中硬编码，包括 `object_class_iri_contains`、`data_property`、`relation` 等匹配条件。slot 与本体属性的绑定是人工维护的静态声明，非从本体 class→property 关系动态推导。

013 的 `suggest-slots` 功能将通过 LLM 辅助建议绑定，但仍是一次性建议→人工采纳，非运行时动态。

**影响**: 本体 schema 变更（新增/重命名属性）后，模板必须手动更新 slot 绑定。

### GAP-5: `Facts.alignments` 字段未使用

**位置**: `interpreter.py:79-80`, `fact_bridge.py`

**现象**: `Facts` 定义了 `alignments: dict[str, list[str]]`（API 对齐：关系属性→外部类 IRI），但 `edges_to_facts` 从未填充此字段，推理规则的 `evaluate()` 也不读取它。

**影响**: 无法将外部标准体系（如 IDMP、ICH Q9）的类映射纳入推理判定。

### GAP-6: 实体对齐不感知本体层级

**位置**: `aligner.py:25-104`

**现象**: `align_entity` 只在同一 `target_class_iri` 的现有个体中匹配。不查询 `rdfs:subClassOf` 层级 — 如果候选实体实际上匹配一个子类或父类的已有个体，对齐会漏掉（返回 `action="new"` 而非 `merge`）。

**影响**: 多层级类体系下，跨层级的重复实体无法自动识别。

---

## 4. 三类事实源的映射需求对照

### 4.1 数据库表

已有骨架 `db_reader.py:reflect_database`：SQLAlchemy 反射表→`class` 候选，FK→`link` 候选。

| 映射层次 | 需求 | 当前实现 | E6 能否声明 |
|---------|------|---------|-----------|
| 表 → 类 | `drug_product` → `slpra:DrugProduct` | `ExtractionConfig.target_class_iri` 手填 | 部分：E6 `source_field` 可表达，但无消费者 |
| 列 → 数据属性 | `approval_no` → `slpra:approvalNumber` | 无 — 仅反射列的 name/type/nullable | **不能**：E6 是类级，无属性级绑定 |
| FK → 对象属性 | `drug_product.mfr_id → manufacturer.id` → `slpra:manufacturedBy` | 仅生成 `link` 候选（from/to 表名），无 IRI 绑定 | **不能**：缺属性级声明 |
| 列值 → 受控词表 | `risk_level` 列值 "高" → `slpra:HighRisk` | 无 | **不能**：E6 无 transform/vocab 映射 |

### 4.2 API (REST/GraphQL)

当前完全未实现（`source_type` 枚举无 `api`，pipeline 抛异常）。Integration Connector 框架有 CRUD + 同步骨架但不承载属性级映射。

| 映射层次 | 需求 | 当前实现 | E6 能否声明 |
|---------|------|---------|-----------|
| 端点 → 类 | `GET /api/drugs` → `slpra:DrugProduct` | ∅ | 勉强：`mapping_type=source_field`, `target=endpoint_path`，但缺 schema 约束 |
| 响应字段 → 属性 | `$.data[*].name` → `slpra:drugName` | ∅ | **不能**：类级映射无 JSON path 绑定 |
| 嵌套对象 → 关系 | `$.data[*].manufacturer` → `slpra:manufacturedBy` | ∅ | **不能**：缺属性级声明 |
| 认证/分页 | OAuth2/API Key, cursor pagination | ∅ | **不应**：运行时行为，非映射声明 |

### 4.3 文件 (Excel/Word) — 已有实现，映射分散

| 映射层次 | 需求 | 当前实现 | E6 参与度 |
|---------|------|---------|----------|
| 文档类型 → 类 | CMCReport.docx → `slpra:CMCReport` | `document_classifier.classify()` (本体驱动) | 无 |
| 表头列 → 属性 | "设备名称" → `slpra:equipmentName` | `ExtractionConfig.column_mapping` (手工配) | 无 |
| 正文段落 → 关系 | 段落→端点 finder→edge | `_ENDPOINT_FINDERS` 硬编码注册 | 无 |
| slot → 覆盖 | slot.source → edge 匹配 | AST 模板 JSON 静态声明 | 无 |

---

## 5. 改进方案：分层映射架构

### 5.1 目标架构

```
┌──────────────────────────────────────────────────────────────┐
│  L1: Source Registry (运行时生命周期)                          │
│  连接器 CRUD、认证凭据(env-only)、调度、CDC/webhook、健康检查   │
│  ← Integration Connector 已有骨架，扩展 source_type 即可       │
├──────────────────────────────────────────────────────────────┤
│  L2: Class Binding (E6 现有层 — 扩展 mapping_type)            │
│  本体类 → 源实体（表名 / API 端点 / 文档类型模式）              │
│  ← E6 OntologyClassMapping 增加 "db_table" / "api_endpoint"  │
├──────────────────────────────────────────────────────────────┤
│  L3: Property Binding (新增层 — 核心缺失) ★                   │
│  本体属性 → 源字段（列名 / JSON path / slot source 声明）      │
│  ← 新建 E6b OntologyPropertyMapping 或扩展 E6 为两级结构       │
├──────────────────────────────────────────────────────────────┤
│  L4: Transform & Validate (属性级值处理)                       │
│  类型转换、受控词表对齐、单位换算、正则验证                      │
│  ← controlled_vocab 已有雏形，需泛化                           │
└──────────────────────────────────────────────────────────────┘
```

### 5.2 数据模型扩展

#### 5.2.1 E6 扩展：增加 `mapping_type` 枚举

```python
# 现有
MAPPING_TYPES = ("slpra_iri", "bfo", "source_field")

# 扩展为
MAPPING_TYPES = (
    "slpra_iri",       # 本体 IRI 对齐（跨本体）
    "bfo",             # BFO 上层本体对齐
    "source_field",    # 通用源字段（向后兼容）
    "db_table",        # 数据库表映射
    "api_endpoint",    # API 端点映射
    "doc_class",       # 文档类型模式映射
)
```

`target` 字段语义按 `mapping_type` 区分：

| mapping_type | target 含义 | source_system 含义 |
|-------------|------------|-------------------|
| `db_table` | 表名（如 `drug_product`） | DSN 环境变量引用（如 `MES_DB_DSN`） |
| `api_endpoint` | 端点路径（如 `/api/v2/drugs`） | Connector ID 引用 |
| `doc_class` | 文档分类 IRI 模式（如 `*CMCReport*`） | — |

#### 5.2.2 新增 E6b: OntologyPropertyMapping (L3 属性级绑定)

```python
class OntologyPropertyMapping(VersionMixin, TimestampMixin, Base):
    """属性级源绑定声明 — 将本体属性映射到具体源字段。"""
    __tablename__ = "ontology_property_mapping"

    id: Mapped[uuid.UUID]           = mapped_column(GUID(), primary_key=True, default=_uuid)
    class_mapping_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("ontology_class_mapping.id"), nullable=False
    )
    property_id: Mapped[uuid.UUID]  = mapped_column(GUID(), nullable=False)
    property_kind: Mapped[str]      = mapped_column(String(10), nullable=False)  # "data" | "object"
    source_path: Mapped[str]        = mapped_column(String(500), nullable=False)
    transform: Mapped[str | None]   = mapped_column(String(50))   # "none" | "vocab" | "regex" | "cast"
    transform_config: Mapped[dict | None] = mapped_column(JSON)   # 转换参数
    is_identifier: Mapped[bool]     = mapped_column(Boolean, default=False)  # 标识属性(对齐用)
    is_label: Mapped[bool]          = mapped_column(Boolean, default=False)  # 标签属性(对齐用)
```

`source_path` 语义按父级 `mapping_type` 区分：

| 父级 mapping_type | source_path 语义 | 示例 |
|-------------------|-----------------|------|
| `db_table` | 列名 | `approval_no` |
| `api_endpoint` | JSON path | `$.data[*].approvalNumber` |
| `doc_class` | slot source 声明 key | `extraction:DrugProduct.approvalNumber` |

#### 5.2.3 关系图

```
OntologyClass (E1)
  │
  ├──1:N──→ OntologyClassMapping (E6, 扩展)
  │            │   mapping_type: db_table | api_endpoint | doc_class | ...
  │            │   target: 表名 / 端点路径 / 文档模式
  │            │   source_system: DSN ref / Connector ID
  │            │
  │            └──1:N──→ OntologyPropertyMapping (E6b, 新增) ★
  │                        property_id → E3(DataProperty) 或 E2(LinkType)
  │                        source_path: 列名 / JSON path / slot key
  │                        transform: vocab | regex | cast
  │
  ├──1:N──→ OntologyDataProperty (E3)
  │            domain_class_id, datatype, controlled_vocab
  │
  └──1:N──→ OntologyLinkType (E2)
               domain_class_id, range_class_id
```

### 5.3 运行时消费：映射驱动的抽取适配器

```
                        ┌─────────────────────┐
                        │ OntologyClassMapping │
                        │ + PropertyMapping    │
                        └────────┬────────────┘
                                 │ 查询
                    ┌────────────┼────────────┐
                    ▼            ▼            ▼
            ┌──────────┐  ┌──────────┐  ┌──────────┐
            │ DB       │  │ API      │  │ File     │
            │ Adapter  │  │ Adapter  │  │ Adapter  │
            └────┬─────┘  └────┬─────┘  └────┬─────┘
                 │             │             │
                 ▼             ▼             ▼
            ┌─────────────────────────────────────┐
            │     统一 edges[] (SPO 三元组)         │
            │  subject_class_iri, predicate_iri,   │
            │  object_class_iri, data_properties   │
            └──────────────┬──────────────────────┘
                           │
                           ▼
            ┌─────────────────────────────────────┐
            │  edges_to_facts (本体感知版)          │
            │  查询 domain/range 验证，填充          │
            │  alignments，受控词表对齐              │
            └──────────────────────────────────────┘
```

#### 5.3.1 DB Adapter 伪代码

```python
def db_adapter_extract(class_mapping, property_mappings, db_session):
    """从 E6 + E6b 声明驱动数据库抽取。"""
    table_name = class_mapping.target             # E6.target = 表名
    dsn_ref    = class_mapping.source_system      # E6.source_system = 环境变量

    engine = create_engine(os.environ[dsn_ref])
    rows = engine.execute(f"SELECT * FROM {table_name}")

    edges = []
    for row in rows:
        data_props = []
        for pm in property_mappings:
            if pm.property_kind == "data":
                raw_value = row[pm.source_path]        # E6b.source_path = 列名
                value = apply_transform(raw_value, pm)  # E6b.transform
                data_props.append({
                    "iri": get_property_iri(pm.property_id),
                    "label": get_property_label(pm.property_id),
                    "value": value,
                })
        edges.append({
            "subject_class_iri": "...",
            "predicate_iri": "...",
            "object_class_iri": class_mapping.class_iri,
            "object_data_properties": data_props,
            "source_ref": f"db:{dsn_ref}/{table_name}",
        })
    return edges
```

#### 5.3.2 API Adapter 伪代码

```python
def api_adapter_extract(class_mapping, property_mappings, connector):
    """从 E6 + E6b 声明驱动 API 抽取。"""
    endpoint = class_mapping.target               # E6.target = 端点路径
    response = connector.fetch(endpoint)           # Connector 处理认证/分页

    edges = []
    for item in jsonpath(response, "$.data[*]"):   # 顶层数组路径
        data_props = []
        for pm in property_mappings:
            if pm.property_kind == "data":
                raw_value = jsonpath_one(item, pm.source_path)  # E6b.source_path = JSON path
                value = apply_transform(raw_value, pm)
                data_props.append({
                    "iri": get_property_iri(pm.property_id),
                    "label": get_property_label(pm.property_id),
                    "value": value,
                })
        edges.append({...})  # 同 DB adapter 产出格式
    return edges
```

### 5.4 `edges_to_facts` 改进方向

```python
def edges_to_facts(edges: list[dict], engine: OntologyEngine | None = None) -> Facts:
    """本体感知版：查询 domain/range 验证，填充 alignments。"""
    # ... 现有逻辑保留 ...

    # 改进 1: 消除硬编码 DrugProduct 判断
    # → 查询 engine.get_class_hierarchy("DrugProduct") 获取所有子类 IRI
    drug_class_iris = engine.get_subclasses("slpra:DrugProduct") if engine else set()

    # 改进 2: data_values 按 domain 过滤
    # → 仅当属性声明 domain 包含当前 subject class 时才存入
    if engine and iri:
        prop_domain = engine.get_data_property_domain(iri)
        # 验证 subject_class_iri 在 prop_domain 的子类链上

    # 改进 3: 填充 alignments
    # → 从 E6 mapping_type in ("slpra_iri", "bfo") 查询外部对齐
    if engine:
        for pred_short, class_iris in relations.items():
            ext_alignments = engine.get_class_alignments(class_iris)
            if ext_alignments:
                alignments[pred_short] = ext_alignments

    # 改进 4: scalars 与受控词表对齐
    # → 查询 controlled_vocab，将原始值归一化到标准取值
```

---

## 6. 实施路线

### Phase 0: 分析验证（当前阶段 ✅）

- [x] 映射管线全链路代码分析
- [x] 6 个 GAP 确认
- [x] 三类事实源需求对照
- [x] 分层架构方案设计

### Phase 1: E6 扩展 + E6b 新增（数据模型）

- [ ] 扩展 `MAPPING_TYPES` 枚举（`db_table`, `api_endpoint`, `doc_class`）
- [ ] 新增 `OntologyPropertyMapping` (E6b) 表 + Alembic 迁移
- [ ] 扩展 `ontology_meta_store.py` CRUD
- [ ] 扩展 `ontology-mapping-panel.tsx` UI：属性级绑定编辑器

### Phase 2: DB Adapter 驱动（消费 E6/E6b）

- [ ] `db_reader.py` 从 E6b 读取列→属性映射（取代纯结构反射）
- [ ] 产出统一 edges 格式（复用已有 pipeline 下游）
- [ ] `_run_database_branch` 从 E6 读取表→类映射（取代 `config.target_class_iri` 手填）

### Phase 3: API Adapter（新增）

- [ ] 新增 `api_reader.py`：从 Connector 拉取 + E6b JSON path 绑定
- [ ] `pipeline.py` 新增 `source_type="api"` 分支
- [ ] Connector 框架扩展：认证管理、分页策略、限流

### Phase 4: `edges_to_facts` 本体感知化

- [ ] 接入 `OntologyEngine` 查询 domain/range
- [ ] 消除硬编码 `DrugProduct` 判断
- [ ] 填充 `alignments` 字段
- [ ] 受控词表自动对齐

### Phase 5: 对齐器层级感知

- [ ] `align_entity` 查询 `rdfs:subClassOf` 链，跨层级匹配
- [ ] 回退策略：子类优先 → 同类 → 父类

---

## 7. 宪章合规性评估

| 宪章原则 | 影响评估 | 合规策略 |
|---------|---------|---------|
| II. 本体权威 | ✅ 强化 — E6/E6b 声明成为运行时唯一映射源 | 所有 adapter 只读本体；E6b 只声明映射关系，不修改本体 |
| III. 可追溯 | ✅ 强化 — 每条 edge 的 `source_ref` 携带完整溯源链 | `source_ref` 格式：`db:DSN/table/row` / `api:connector/endpoint/id` |
| V. 最小复杂度 | ⚠️ E6b 新增表 + adapter 层 | 增量式：Phase 1 仅数据模型，Phase 2-3 按需扩展 adapter |
| VI. 离线优先 | ✅ 无影响 — E6b 声明式，离线可用 | API adapter 在离线时返回空 edges（优雅降级） |

---

## 8. 附录：关键代码位置索引

| 模块 | 文件 | 关键函数/类 |
|------|------|-----------|
| E6 模型 | `backend/app/models/ontology_meta.py:193-204` | `OntologyClassMapping` |
| E6 CRUD | `backend/app/services/ontology_meta_store.py:259` | `_mapping_dto`, `class_detail` |
| E6 前端 | `frontend/src/components/ontology/ontology-mapping-panel.tsx` | `OntologyMappingPanel` |
| 事实桥梁 | `backend/app/services/reasoning/fact_bridge.py:25-76` | `edges_to_facts` |
| Facts 模型 | `backend/app/services/reasoning/interpreter.py:63-83` | `Facts` dataclass |
| 抽取管线 | `backend/app/services/extraction/pipeline.py:43-167` | `run_extraction_pipeline` |
| DB 反射 | `backend/app/services/extraction/db_reader.py:41-99` | `reflect_database` |
| 关系抽取 | `backend/app/services/extraction/relation_extractor.py:635-680` | `extract_relationships` |
| 端点 finder | `backend/app/services/extraction/relation_extractor.py:614` | `_ENDPOINT_FINDERS` |
| 实体对齐 | `backend/app/services/extraction/aligner.py:25-104` | `align_entity` |
| 覆盖验证 | `backend/app/services/reporting/coverage_validator.py:294-332` | `validate_coverage` |
| LLM 补抽 | `backend/app/services/extraction/llm_gap_filler.py:119-170` | `fill_coverage_gaps` |
| Connector | `frontend/src/lib/api.ts:187-207` | `listConnectors`, `syncConnector` |
