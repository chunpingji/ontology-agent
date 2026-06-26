# Phase 1 Data Model: 研发文档事实源（按研发阶段）

**Feature**: `007-rnd-document-fact-source` | **Date**: 2026-06-25 | **Plan**: [plan.md](./plan.md) | **Research**: [research.md](./research.md)

> 本特性 **零新建表**。所有持久化复用既有表（`integration_connectors` / `fact_materialization_run` / `entity_shadow` / `extraction_*`），仅新增**枚举取值**、**既有 JSON 列内的字段约定**与**进程内数据形状**；新增的「类/枚举/属性」只进**权威 TTL（T-Box）**，**绝不**入 DB（[[ttl-tbox-only-abox-in-db]]）。行号锚定 research.md 勘察所见。

---

## 1. 实体清单（规范 Key Entities → 落点）

| 规范实体 | 落点（复用既有） | 表/命名空间 | 本期增量 |
|---|---|---|---|
| 研发文档（Regulatory Document） | A-Box 个体 + 影子行 | `entity_shadow`（`iri` 落 `facts#`）+ Owlready2 World | `class_iri` 指向托管 `…/slpra/document/<Subclass>`；`module='document'` |
| 研发阶段（Development Phase） | T-Box 枚举个体 | `slpra-document.ttl`（**仅** TTL） | 6 阶段个体（同 `RiskLevel` 体例） |
| 文档库连接器（Doc Repo Connector） | `IntegrationConnector` 行 | `integration_connectors` | `system_type='doc_repo'`；`connection_config.access_mode ∈ {upload,inline,http}` |
| 溯源指向（Provenance Link） | 业务实体个体属性 | `entity_shadow.properties_json` + World | `extractedFrom = <文档个体 IRI>`（提交时注入） |
| 同步留痕（Materialization Run） | `FactMaterializationRun` 行 | `fact_materialization_run` | 无结构改动（文档变更复用同一留痕） |
| 抽取作业 / 候选（内容层） | `ExtractionJob` / `ExtractionCandidate` | `extraction_jobs` / `extraction_candidates` | `source_type='doc_repo'`；`source_ref = <文档个体 IRI>` |

---

## 2. 既有表的字段/枚举约定增量（零建表）

### 2.1 `integration_connectors`（`models/integration.py:16`）

| 列 | 现状 | 本期约定 |
|---|---|---|
| `system_type` String(50) | `'aps'` 等 5 类 | **新增取值 `'doc_repo'`**（连接器工厂分发键，R1） |
| `connection_config` JSON | APS：`source_mode`/`inline_changes`/`simulate`/`base_url` | doc_repo：`access_mode ∈ {upload,inline,http}`；`inline_changes`（内联测试）；`endpoint_ref`/`token_ref`（**env 变量名**，非明文，R7/FR-010）；`base_url`（http） |
| `field_mapping` JSON | 字段名映射 | **新增 `doc_type_to_class`**：`entity_type → 托管 DocSubclass IRI` 映射表（R2） |
| `sync_cursor` JSON | `{version, versions:{eid:ver}}` | **形状不变**（文档复用同一幂等水位机制，FR-008） |
| `last_status`/`last_error` | success/timeout/error | 不变 |

> **凭据不入库不变式（FR-010）**：`connection_config` 仅存**环境变量名引用**（如 `"token_ref": "EDMS_TOKEN"`），运行时由 `os.environ` 解析；明文凭据 MUST NOT 落 `connection_config`、MUST NOT 提交版本库。沿用 `DBSourceSpec.dsn_ref`（`schemas/extraction.py:102`）既定模式。

### 2.2 `fact_materialization_run`（`models/integration.py:38`）

**无结构改动**。文档同步复用 `run_sync`（`materializer.py:38`）全套留痕：`cursor_from`/`cursor_to`/`change_count`/`changes`(JSON)/`event_ids`/`status ∈ {running,success,timeout,error}`/`error_message`。失败时 `cursor_to=None`（不推进水位，`_fail` at `materializer.py:122`，FR-009/SC-006）。

### 2.3 `entity_shadow`（文档个体影子行）

| 列 | 文档个体取值 |
|---|---|
| `iri` | `http://slpra.org/facts#<doc-eid>`（A-Box，FR-006/SC-004） |
| `class_iri` | `https://ontology.pharma-gmp.cn/slpra/core/slpra/document/<Subclass>`（**托管 T-Box 类**，由 `_materialize` 文档分支写入 `class_iris[0]`，R2） |
| `module` | `'document'`（`_detect_module` 新增前缀，见 §2.5） |
| `label_zh` | 文档标题 |
| `properties_json` | `hasDevelopmentPhase` / `documentVersion` / `approvalStatus` / `sourceSystem` / `contentHash` / `externalRef` / `_version` |

### 2.4 `extraction_configs` / `extraction_jobs` / `extraction_candidates`（`models/extraction.py`）

| 列 | 现状 | 本期约定 |
|---|---|---|
| `ExtractionConfig.source_type` String(20) | database/excel/word | **新增取值 `'doc_repo'`** |
| `ExtractionJob.source_type` String(20) | 同上 | **新增取值 `'doc_repo'`** |
| `ExtractionJob.source_config` JSON | `{db_source:{…}}` | **新增 `{doc_ref:<文档个体 IRI>, content_ref:<外部引用>}`**（Q2：仅引用，不存正文） |
| `ExtractionCandidate.source_ref` String(200) | `source_filename`/`config.source_type`（`pipeline.py:54`） | doc_repo：**`= <文档个体 IRI>`**（R3，溯源回链来源） |
| `ExtractionCandidate.review_status` String(20) | pending/committed/split/… | 不变（门禁不削弱，FR-003） |
| `ExtractionCandidate.committed_iri` String(500) | 入库 IRI | 不变（提交时注入 `extractedFrom`，R3） |

> 字段长度均已满足（`source_ref` String(200) 容纳 `facts#` IRI；`class_iri` String(500) 容纳托管 IRI）。**无需 Alembic 迁移**——新增仅 String 列的取值与 JSON 内字段约定，不改列定义。

### 2.5 `_detect_module` 双处增补（**两处都要改**）

文档个体须归入 `document` 模块：
- `services/kg_store.py:98` `KGStore._detect_module` → `module_prefixes` 增 `"document": "/slpra/document/"`
- `api/kg.py:96` `_detect_module`（图谱视图用的**重复实现**）→ `prefixes` 同步增 `"document": "/slpra/document/"`

> ⚠️ 两处是各自独立的字典字面量；只改一处会导致影子表归 `document` 而图谱视图仍归 `integration`。Phase 1 契约测试须覆盖两处一致性。

---

## 3. 归一化文档生命周期变更（进程内形状）

连接器 `fetch_incremental` 产出的每条变更，复用 APS 既有变更骨架（`materializer.py:106` `_materialize` 消费）：

```jsonc
{
  "entity_id": "doc-TTR-001",            // → facts# 个体本地名（幂等键，FR-008）
  "entity_type": "TechTransferReport",   // → 经 field_mapping.doc_type_to_class 映射为托管 DocSubclass IRI（R2）
  "version": 2,                           // → 幂等水位（versions[eid]，仅推进至最高版本）
  "label": "XX 项目技术转移报告",          // → label_zh
  "fields": {                             // → properties_json（+ _version）
    "hasDevelopmentPhase": ".../slpra/document/Phase_ClinicalI",  // 阶段枚举个体 IRI
    "documentVersion": "2",
    "approvalStatus": "approved",         // 见 §5 状态机
    "sourceSystem": "EDMS-A",
    "contentHash": "sha256:…",            // 内容指纹（ALCOA+）
    "externalRef": "edms://doc/TTR-001/v2" // Q2：外部引用，按需取正文，不入库全文
  }
}
```

**与现状的唯一差别**：`entity_type` 不再直接拼成 `facts#<entity_type>` 类 IRI，而是经 `field_mapping.doc_type_to_class`（缺省走工厂内置默认表）映射到**托管 T-Box 类 IRI** 写入 `class_iris[0]`；`iri` 仍为 `facts#<entity_id>`。元数据缺失时 `fields` 缺键即「未提供」，**不臆造**（Edge Case）。

---

## 4. 溯源回链形状（内容层 → 文档个体）

```jsonc
// _commit_candidate（extraction.py:191）确认入库时注入：
candidate.extracted_properties["extractedFrom"] = candidate.source_ref   // = 文档个体 IRI
// 经文档继承的阶段（默认实体继承文档阶段，Edge Case「阶段冲突」）：
candidate.extracted_properties.setdefault("hasDevelopmentPhase", <文档阶段 IRI>)
```

提交后业务实体个体携 `extractedFrom = http://slpra.org/facts#doc-TTR-001`，满足 FR-004/SC-002（100% 可溯源回「哪份文档的哪一版」——版本由文档个体的 `documentVersion` 承载）。

---

## 5. 状态转换

### 5.1 文档批准状态（`approvalStatus`，FR-014 不物理删除）

```
draft ──→ in_review ──→ approved ──┬─→ superseded   （被更高版本取代）
                                   └─→ withdrawn     （作废/撤回）
```
- `superseded`/`withdrawn` 经**状态变更**表达，旧版本文档个体**保留**（溯源链可追溯，MUST NOT 物理删除，FR-014/Edge Case「文档撤回/作废」）。
- 更高版本到达时：旧版本个体置 `superseded`，新版本个体以新 `entity_id`/`version` 物化（或同 `entity_id` 推进 `_version`，依连接器键策略）；二者溯源可区分（US2 AS#4）。

### 5.2 候选复核（`review_status`，FR-003 门禁不削弱）

```
pending ──→ (analyst) ──┬─→ committed   （confirmed → _commit_candidate 注入 extractedFrom）
                        ├─→ rejected    （不入库，决定可追溯）
                        ├─→ edited→committed
                        └─→ split        （派生新 pending 候选）
```
**唯一入库路径** = `confirmed`/`edited` → `_commit_candidate`（`review_candidate` at `extraction.py:203`）。来源文档可信 **不**改变此门禁（SC-003 = 0%）。

### 5.3 内容抽取触发（FR-007 / Q1 手动发起）

```
文档 approved 事件 ──→ fact_event_bus.publish ──→ 编排「入待抽取队列」
   ──→ 创建 ExtractionJob(source_type='doc_repo', status='pending', source_config={doc_ref,content_ref})
   ──→ [分析师手动发起] ──→ run_extraction_pipeline（doc_repo 分支）──→ 候选入复核队列
```
**记录层自动物化 / 内容层人工发起**——「记录是事实，内容是候选」在触发层的体现（R5）。

### 5.4 同步留痕（`FactMaterializationRun.status`）

```
running ──┬─→ success   （cursor_to 推进；先提交后发事件 materializer.py:94）
          ├─→ timeout   （cursor_to=None，水位不推进，告警）
          └─→ error     （同上）
```

---

## 6. 受影响子图扩展（`events.resolve_affected_subgraph`，`events.py:19`）

现 `subgraph = {equipment, product, area}`。本期增 `document` 维（及文档关联的 `sample`/`product`），供文档/阶段事实变更触发受影响子图推理重算（FR-007 下半句）：

```python
subgraph = {"equipment": [], "product": [], "area": [], "document": []}
if etype in ("document", "RegulatoryDocument", ...) and eid:
    subgraph["document"].append(eid)
# 文档 fields 关联的样品/产品也纳入受影响范围
for key in ("sample", "product"):
    if fields.get(key):
        subgraph.setdefault(key, []).append(str(fields[key]))
```
不改 `FactEventBus.publish` 事件信封结构，不改 `AssessmentResult` 对外形状（FR-012/零回归）。

---

## 7. T-Box 公理增补（`ontology/slpra/slpra-document.ttl` 新模块，经 `surgical_merge` 发布）

> **仅** T-Box（类/枚举/属性）。文档**个体**永不入此文件（SC-004 边界，由 contracts 门禁测试坐实）。

**模块 IRI**：`https://ontology.pharma-gmp.cn/slpra/core/slpra/document/`

| 公理 | 内容 |
|---|---|
| 类层次 | `RegulatoryDocument ⊑ obo:BFO_0000031`（generically dependent continuant，与 `RiskAssessmentReport` 同挂位，有先例 `slpra-risk.ttl:92`） |
| 文档子类（约 6） | `INDDossier` / `TechTransferReport` / `ProcessValidationReport` / `StabilityReport` / `NDA_BLADossier` / `PVReport` ⊑ `RegulatoryDocument`（对应规范 §1.1 关键产出） |
| 阶段枚举 | `DevelopmentPhase`（枚举类）+ 6 个体：`Phase_DrugDiscovery` / `Phase_Preclinical` / `Phase_ClinicalI` / `Phase_ClinicalII_III` / `Phase_NDA_BLA` / `Phase_PostMarket`（同 `RiskLevel` 枚举体例，R4，FR-005） |
| 阶段属性 | `hasDevelopmentPhase`（domain: 文档/业务实体；range: `DevelopmentPhase`） |
| 溯源属性 | `extractedFrom`（业务实体 → `RegulatoryDocument`，FR-004）；`documentVersion` / `approvalStatus` / `sourceSystem` / `contentHash`（数据属性，文档个体生命周期元数据） |
| 阶段序/侧重 | 每阶段个体携 `skos:notation`（次序）+ `rdfs:comment`（质量体系侧重，US3 溯源标注用） |

发布经能力一既有 `surgical_merge` + `export_diff`（三元组级 diff）+ `require_role(senior_analyst)`；外部命名 IRI（BFO/OBO）逐字保留（宪章 II）。

---

## 8. 校验规则 → 需求映射

| 校验规则 | 来源 |
|---|---|
| 文档个体 IRI 必在 `facts#`；其三元组不得出现于任何 `*.ttl` | FR-006 / SC-004 |
| 同一 `entity_id` 重复/乱序 `version` 仅推进最高版本，不产生多个版本个体 | FR-008 / SC-005 |
| 超时/不可达：`cursor_to=None`、`last_status='timeout|error'`、无半成品事实 | FR-009 / SC-006 |
| 内容层候选 `review_status` 非 `committed` 前不入图谱 | FR-003 / SC-003 |
| 每条 `committed` 文档抽取实体携 `extractedFrom`（指回文档个体） | FR-004 / SC-002 |
| `system_type='doc_repo'` 接入后，APS 等 5 类零回归 | FR-012 / SC-007 |
| 文档/实体 100% 携 `hasDevelopmentPhase` 且可按阶段检索 | FR-005 / SC-008 |
| 凭据仅以 env 变量名引用存于 `connection_config` | FR-010 |
| 作废/取代以状态变更表达，不物理删除 | FR-014 |
| 上传导入路径的记录层物化/溯源/幂等与连接器拉取一致 | FR-015 |

**Phase 1 数据模型结论**：零建表、零迁移（仅 String 取值与 JSON 字段约定）、零新依赖；T-Box 增补独立成模块经既有发布路径入库。进入 [contracts/](./contracts/)。
