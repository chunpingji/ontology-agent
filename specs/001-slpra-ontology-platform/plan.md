# Implementation Plan: 能力一 — 知识模型（T-Box）维护工作台

**Branch**: `001-slpra-ontology-platform` | **Date**: 2026-06-20 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-slpra-ontology-platform/spec.md`

## Summary

把能力一从"只读浏览"提升到"可编辑、可校验、可发布、可回写 TTL"。高级分析师在前端工作台维护 SLPRA 知识模型（Class / 对象属性 / 数据属性 / Action / 约束 / SLPRA·BFO 映射），经乐观并发控制保存为草稿，批次化审核发布为带版本的发布，发布时以**外科式合并**导出回权威 TTL（保留未建模公理）并走 Git；全程留痕、按角色门禁。

技术路径：在既有独立后端（FastAPI + Owlready2 + PostgreSQL）上**扩展 `OntologyEngine` 的 T-Box 写方法**与一组新的 `/api/ontology` 写/映射/校验/发布/导入导出端点；新增**可编辑元数据表**（PostgreSQL，作为 T-Box 的可编辑投影 + 乐观并发 + 版本/审计载体），写操作**双写** Owlready2 World 与元数据表；前端在既有 Next.js 16 / React 19 技术栈中**自建**工作台组件（表单 CRUD、约束编辑、映射面板、风险属性向导、TTL 工具条、d3 图谱），替换现只读页。**范围严格限定为能力一**（详见 spec 范围澄清）。

## Technical Context

**Language/Version**: Python 3.11（后端）；TypeScript 5 / Node（前端，Next.js 16 + React 19）

**Primary Dependencies**:
- 后端：FastAPI ≥0.115、Owlready2 ≥0.46、SQLAlchemy 2.0、Alembic ≥1.13（**已在 pyproject 但尚未接线**）、Pydantic v2、`rdflib`（**新增**，用于 TTL 外科式合并/diff）
- 前端：Next.js 16、React 19、`@tanstack/react-query`、`@tanstack/react-table`、`zustand`、`d3`（图谱可视化，**非 ReactFlow**）、`lucide-react`、Tailwind

**Storage**:
- PostgreSQL 16（compose `db`，库 `slpra`）：可编辑元数据表（T-Box 投影）+ 既有运营表（entity_shadow / extraction_* / reasoning_* / audit_log / integration_connector）
- Owlready2 OWL 存储（SQLite World，`OWL_STORE_PATH`）：OWL 结构、SPARQL、A-Box、T-Box 运行期表示
- 权威 TTL：`ontology/slpra/*.ttl`（7 模块、~720 公理，Git 版本管理）——发布时回写

**Testing**: pytest（后端，`backend/tests/`，当前近空）；前端当前无测试框架——本特性引入后端契约/集成测试为主，前端以关键交互的手动 quickstart 验证为主

**Target Platform**: Linux 服务器，docker-compose 三服务（db / backend / frontend），内网部署

**Project Type**: web（frontend + backend 分离，独立后端 API）

**Performance Goals**: T-Box 编辑为低并发交互场景（数名分析师）；保存/校验/发布的交互响应目标 < 3s（一致性校验可选异步）；不适用高吞吐指标

**Constraints**:
- 导出 TTL **必须**外科式合并、保留未建模公理（注释/对齐/SWRL），写入前展示 diff（FR-009a）
- 并发保存采用**乐观并发**（版本/时间戳冲突检测，FR-011a）
- 发布以**批次**为单元 → 一次 TTL 导出 + 一次 Git 提交（FR-008a）
- 双存储（Owlready2 World ↔ 元数据表）写后一致
- **不依赖 infilake-dw 后端/数据库**；前端组件为自建（无 infilake-dw 组件可复用）

**Scale/Scope**: 7 模块；数百 Class；~720 公理；个位数并发分析师；元数据表行数 10²–10³ 量级

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` 已正式批准为 **v1.0.0**（2026-06-20 批准）。按其五项原则与两节附加约束评估本计划：

| 原则 / 章节 | 本计划如何满足 | 门禁 |
|------------|----------------|------|
| I. 规范驱动开发 | 严格走 specify→clarify→plan→tasks；规范为真理来源，范围经澄清固化为能力一 | PASS |
| II. 本体权威性与保真（NON-NEGOTIABLE） | R3 外科式合并保留未建模公理、保 BFO/外部对齐、双存储写后一致、写前 diff（FR-009a） | PASS |
| III. 可追溯与审计 | R4 版本化乐观并发、R5 批次发布=一次 TTL 导出+一次 Git 提交、audit_log 全程留痕（FR-008a/032/035） | PASS |
| IV. 测试纪律与契约优先 | `contracts/ontology-tbox-api.md` 契约先行；tasks 含 pytest 契约/集成测试（CRUD/并发/导出/发布/双存储一致） | PASS |
| V. 最小复杂度与复用 | 复用既有 FastAPI/SQLAlchemy/OntologyEngine/React Query/d3 模式；仅新增 `rdflib` 并已论证必要性 | PASS |
| 安全与合规 | R7 最小 RBAC（senior_analyst 方可编辑/发布）、可信身份头、SSO 可插拔；能力三合规判定明确划出范围 | PASS |
| 开发工作流与质量门禁 | 本节即 Phase 0 前门禁；DB 变更走 Alembic 迁移 + TTL 幂等投影补种 | PASS |

**Gate result**: PASS（无宪法违例；复杂度跟踪表无需填写）。

## Project Structure

### Documentation (this feature)

```text
specs/001-slpra-ontology-platform/
├── plan.md              # 本文件
├── research.md          # Phase 0：关键技术决策
├── data-model.md        # Phase 1：元数据表与版本/审计实体
├── quickstart.md        # Phase 1：US1 端到端验证指南
├── contracts/
│   └── ontology-tbox-api.md   # Phase 1：T-Box 写/映射/校验/发布/导入导出 REST 契约
├── checklists/
│   └── requirements.md  # /speckit-specify 产出的质量清单
└── tasks.md             # Phase 2（/speckit-tasks 生成，本命令不创建）
```

### Source Code (后端 backend/ + 前端 frontend/)

实际为既有 web 结构（backend + frontend）。本特性的新增/修改集中在 `backend/app/services/ontology_engine.py`、`backend/app/api/ontology.py`、新增 `backend/app/models/ontology_meta.py`、Alembic 接线，以及前端 `ontology` 页与新建 `components/ontology/`。

```text
backend/
├── app/
│   ├── api/
│   │   └── ontology.py            # 扩展：新增 T-Box 写/映射/约束/校验/发布/导入导出端点（现仅 3×GET）
│   ├── services/
│   │   ├── ontology_engine.py     # 扩展：create/update/delete_class、属性、约束、action；export/import/diff_ttl；validate
│   │   ├── ontology_meta_store.py # 新增：元数据表读写 + 双存储同步 + 乐观并发 + 发布/版本
│   │   └── ttl_merge.py           # 新增：rdflib 外科式合并 + diff（FR-009a）
│   ├── models/
│   │   └── ontology_meta.py       # 新增：ontology_class/link_type/data_property/action/mapping/restriction/release + app_user/role
│   ├── schemas/
│   │   └── ontology.py            # 扩展：写请求/响应/版本/diff/校验 Pydantic 模型
│   ├── dependencies.py            # 扩展：get_ontology_meta_store、当前用户/角色依赖（RBAC）
│   └── main.py                    # 扩展：启动时 Alembic 迁移已应用 + 元数据表 TTL 投影补种
├── alembic/                       # 新增：Alembic 配置 + 首个迁移（创建元数据表）
│   ├── env.py
│   └── versions/0001_ontology_meta.py
└── tests/
    └── test_api/test_ontology_tbox.py   # 新增：T-Box CRUD/并发/导出/发布契约与集成测试

frontend/
└── src/
    ├── app/(dashboard)/ontology/page.tsx   # 改造：只读 → 可编辑工作台壳（含 restrictions 渲染）
    ├── components/ontology/                  # 新增（src/components 当前为空）
    │   ├── class-panel.tsx                   # Class CRUD + 审核/停用/父类/字段 schema
    │   ├── link-type-panel.tsx               # 对象属性/关系（domain/range/基数/逆）
    │   ├── data-property-panel.tsx           # 数据属性（datatype）
    │   ├── action-panel.tsx                  # Action 定义（actor/target/pre/post/params）
    │   ├── restriction-editor.tsx            # 约束 someValues/allValues/cardinality/互斥/等价
    │   ├── ontology-mapping-panel.tsx        # SLPRA IRI / BFO / 字段映射 + 健康度
    │   ├── risk-attribute-wizard.tsx         # OEB/PDE/致敏… 受控词表向导
    │   ├── ttl-toolbar.tsx                   # 导入/导出/diff 预览 + 发布
    │   └── graph-visualization.tsx           # d3 图谱（Class/Action/带基数关系）
    └── lib/api.ts                            # 扩展：补齐 T-Box 写/映射/校验/发布方法（现仅 GET）

db/
└── init/                          # 新增（compose 挂载点；最小 bootstrap，主迁移走 Alembic）

ontology/slpra/*.ttl               # 权威 TTL（发布时由导出器外科式回写）
docker-compose.yml                 # 扩展：backend 启动应用迁移；（可选）db/init 挂载
```

**Structure Decision**: 沿用既有 **web（backend + frontend）** 结构，最大化复用现有模式。后端新增一个"元数据存储/同步"服务层 `ontology_meta_store.py` 与 TTL 合并工具 `ttl_merge.py`，把"可编辑投影 + 双存储一致 + 乐观并发 + 发布/版本"集中于此，保持 `ontology_engine.py` 专注 Owlready2 本体操作。前端在现有 Next.js/React Query/d3 栈中自建 `components/ontology/`（无 infilake-dw 组件可引入）。

## Phase 0 — Research

见 [research.md](./research.md)。关键决策摘要：

| # | 决策 | 结论 |
|---|------|------|
| R1 | Owlready2 中编辑 T-Box | 用 `types.new_class` / `with onto:` 动态建类与属性、`is_a` 增删约束、`destroy_entity` 删除；写后 `world.save()` |
| R2 | 双存储一致性写路径 | 元数据表为**编辑真理来源（草稿态）**，发布时投影到 Owlready2 World 并经 R3 导出 TTL；单事务 + 加锁，失败回滚 |
| R3 | TTL 外科式合并 + diff | `rdflib` 解析基线，按"受管命名空间 + 谓词白名单"识别工作台拥有的三元组，仅增删这些三元组后序列化；未建模三元组逐字保留；diff 以三元组级 + Turtle 预览呈现 |
| R4 | 乐观并发 | 每个可编辑对象带 `version`（整数）+ `updated_at`；保存携带读取时版本，服务端 CAS，不匹配 → 409 冲突 |
| R5 | 批次化发布与版本 | `ontology_release` 聚合一批草稿变更；状态 Draft→InReview→Published；发布 = 投影 World + 导出 TTL + 一次 Git 提交 + 版本归档 |
| R6 | 迁移与种子 | 接线 Alembic（首迁移建元数据表）；后端启动应用迁移后，由 TTL **投影补种**元数据表（幂等） |
| R7 | 认证与 RBAC | 引入最小身份层（`app_user`/`role`，三角色）；API 依赖校验角色；编辑/发布限 SeniorAnalyst；可插拔企业 SSO（后续） |
| R8 | 前端工作台栈 | 既有 Next.js/React Query/zustand/**d3**；自建组件；无 infilake-dw 组件 |
| R9 | 模型健康度/一致性校验 | 规则式校验（孤立类/未映射字段/TTL 漂移）+ 可选 `sync_reasoner`（HermiT）一致性；发布前阻断性问题拦截 |
| R10 | Action 作为可编辑本体元数据 | 在元数据表建模 Action（actor/target/pre/post/params）；本特性**仅维护定义**，不改动 `reasoning/rules` 运行期（属能力三） |

## Phase 1 — Design & Contracts

- 数据模型：见 [data-model.md](./data-model.md)（元数据表 7 张 + 发布/版本 + 最小用户/角色 + 审计扩展）。
- 接口契约：见 [contracts/ontology-tbox-api.md](./contracts/ontology-tbox-api.md)（T-Box 写/映射/约束/校验/发布/导入导出端点）。
- 验证指南：见 [quickstart.md](./quickstart.md)（US1 端到端：新建类 → 映射 → 约束 → 校验 → 发布 → 导出 diff）。
- Agent 上下文：已更新 `CLAUDE.md` 的 SPECKIT 区块指向本计划。

### Post-Design Constitution Re-check

对照宪法 v1.0.0 五项原则复查：设计未引入未受控复杂度。本体保真（原则 II）、双存储一致与审计（原则 III）、契约先行与测试（原则 IV）、乐观并发与批次发布均落到独立、可测的服务层方法与契约端点；新增依赖仅 `rdflib`（原则 V，已论证）。**Gate: PASS**。

## Complexity Tracking

> 无宪法违例，无需填写。

唯一新增第三方库为后端 `rdflib`（TTL 外科式合并/diff 的最小必要依赖；Owlready2 自带 save 会重生成、丢注释，无法满足 FR-009a 的保真要求）。
