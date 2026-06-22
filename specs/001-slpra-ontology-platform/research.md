# Phase 0 Research: 能力一 T-Box 维护工作台

**Feature**: 001-slpra-ontology-platform | **Date**: 2026-06-20

本文件解决 Technical Context 中的未知项与关键技术选型。每项给出 **Decision / Rationale / Alternatives considered**。澄清会话（spec §Clarifications）已固定 4 项决策（范围、乐观并发、TTL 外科式合并、批次发布），此处落到实现策略。

---

## R1 — 在 Owlready2 中编辑 T-Box（类/属性/约束/Action）

**Decision**: 在 `OntologyEngine` 新增 T-Box 写方法，复用其既有加锁 + `with onto:` + `world.save()` 模式：
- 建类：`types.new_class(name, (parent_or_Thing,), {"namespace": onto})`；标签/注释经 `cls.label`/`cls.comment` 追加（带 `locstr` 语言标签 zh/en）。
- 对象/数据属性：`types.new_class(name, (ObjectProperty,))` / `(DataProperty,)`，设置 `.domain`/`.range`、基数与特征（FunctionalProperty 等）经多继承或 `is_a` 赋予；逆属性 `.inverse_property`。
- 约束：向 `cls.is_a` 追加 `prop.some(X)` / `prop.only(X)` / `prop.exactly(n, X)`；删除即从 `is_a` 移除对应 `Restriction`（与现 `get_class_detail` 的读取逻辑对称）。
- 删除：`owlready2.destroy_entity(entity)`（与现 `delete_individual` 一致）。

**Rationale**: 与既有 A-Box 写法（`create_individual`/`_set_properties`）同构，最小认知负担；`get_class_detail` 已能读出 someValues/allValues/exactly，写路径对称即可闭环 §4.0 "restrictions 取到未渲染/不能编辑"。

**Alternatives considered**:
- 直接编辑 TTL 文本：易破坏 720 公理一致性、无运行期校验 → 拒绝。
- 仅在元数据表建模、运行期不入 OWL：会使 SPARQL/一致性校验看不到草稿/已发布变更 → 拒绝（违背"双存储一致"）。

**Risk/Mitigation**: Owlready2 对等价类/互斥（`AllDisjoint`、`equivalent_to`）的编辑 API 较细碎 → 约束编辑器首版支持 some/only/exactly/disjoint/equivalent 五类（覆盖 SLPRA 主要用法），其余约束在 diff 中只读保留（R3 保证不丢失）。

---

## R2 — 双存储一致性写路径（元数据表 ↔ Owlready2 World）

**Decision**: **元数据表为"编辑期真理来源"（草稿可变），Owlready2 World 与 TTL 为"发布期物化目标"**：
1. 分析师的增改删先写 PostgreSQL 元数据表（草稿态，带 `version`/`status=draft`）。
2. 同一请求内，对**结构性变更**同步反映到 Owlready2 World（便于即时 SPARQL/校验），用 `OntologyEngine` 写方法；World 写与表写在一个工作单元内，任一失败则回滚表事务并丢弃 World 变更（World 变更未 `save()` 即不落盘）。
3. 发布（R5）时，将该批已审核变更经 R3 外科式合并导出回 TTL。

**Rationale**: 编辑需要富元数据（confidence/审核态/映射/版本/审计），关系型表最自然；而 SPARQL/一致性校验需要 OWL 表示。以表为草稿真理来源、World 为物化镜像，避免"以 TTL 为编辑态"导致的并发与版本难题。

**Alternatives considered**:
- 以 World 为唯一真理来源、表仅为缓存：无法承载乐观并发/审核/版本字段，且 SQLite World 并发写弱 → 拒绝。
- 仅表 + 发布时一次性重建 World：校验滞后，编辑期无法即时一致性检查 → 部分采纳（World 即时镜像优先，发布再 TTL）。

---

## R3 — TTL 外科式合并 + diff（落实澄清 Q3）

**Decision**: 新增 `services/ttl_merge.py`，基于 **`rdflib`**：
- 维护"受管谓词白名单"+"受管主语判定"（主语 IRI 属于 SLPRA 模块命名空间且其变更源自工作台建模的轴）。
- 导出：载入基线 TTL → 计算工作台拥有的三元组集合（按白名单/主语）→ 从图中移除旧的"受管三元组"、加入新值 → **保留所有非受管三元组**（外部对齐 `rdfs:subClassOf dron:*`、SWRL、注解属性等）→ 以 Turtle 序列化。
- diff：对"基线受管子图"与"新受管子图"做三元组级 added/removed/changed 比较，前端展示 Turtle 片段预览；**写入前必须人工确认**。

**Rationale**: 满足 FR-009a"仅更新建模公理、保留未建模构造、写前 diff"。三元组级合并保证语义层不丢未建模公理；diff 作为安全网。

**Known limitation & mitigation**: RDF 序列化**不保证注释/排版的逐字字节级保真**（rdflib 会重排、丢 `#` 注释）。缓解：
1. 将人工注释尽量建模为 `rdfs:comment`（属受管，可编辑且可序列化）。
2. 对确需逐字保留的头部注释块，采用"文件头注释锚点"在序列化后回贴（`ttl_merge` 保留并重写文件头注释区）。
3. diff 评审 + Git 版本兜底，任何意外变更可见、可回滚。
> 在 quickstart 验证中显式检查"未建模对齐公理与 SWRL 在导出后仍存在"。

**Alternatives considered**:
- Owlready2 `onto.save(format="ntriples"/"rdfxml")`：整体重生成、丢注释、且默认非 Turtle → 拒绝。
- 文本级正则补丁：对复杂 Turtle 语法脆弱 → 拒绝（仅用于文件头注释锚点这一受限场景）。
- 另写独立 TTL 模块（叠加层）：澄清 Q3 已否决 → 拒绝。

---

## R4 — 乐观并发（落实澄清 Q2）

**Decision**: 每张可编辑元数据表含 `version INTEGER NOT NULL DEFAULT 1` 与 `updated_at`。读取返回当前 `version`；保存请求体携带 `expected_version`；服务端 `UPDATE ... WHERE id=? AND version=?`，受影响行数=0 → 返回 **HTTP 409**（含服务端最新值）供前端提示"重新加载后合并"。

**Rationale**: 低并发内部工具，乐观并发零锁管理、实现简单、无死锁；与 FR-011a 一致。

**Alternatives considered**: 悲观行锁/检出锁（运维复杂、易残留锁）；后写覆盖（违反"避免相互覆盖"）→ 均拒绝。

---

## R5 — 批次化发布与版本（落实澄清 Q4）

**Decision**: 引入 `ontology_release`：
- 分析师暂存多项草稿变更（各对象 `status=draft`，并关联当前 open release，或发布时快照所有 draft）。
- 提交审核：release `status: draft → in_review`；审核通过 → `published`，分配 `version_tag`（如 `v1.2.0`）。
- 发布动作（事务化、尽量幂等）：① 校验通过（R9）；② 将该批变更物化到 World；③ R3 导出受影响 TTL 文件；④ 一次 `git commit`（消息含 release tag 与变更摘要）；⑤ 归档 release 快照（变更清单 + diff）；⑥ 写 `audit_log`。
- 失败处理：导出/commit 失败 → 标记 release `failed`，World 物化回滚或重放，TTL 工作树 `git checkout --` 还原。

**Rationale**: 与 720 公理 TTL 基线的演进方式一致（成批、可评审、单提交），版本历史有意义，避免逐字段 churn。满足 FR-008a。

**Alternatives considered**: 逐对象发布（commit 噪声、版本碎片）；每次保存即发布（无审核门、不可评审）→ 澄清 Q4 已否决。

**Git 集成**: 后端在容器内对挂载的 `./ontology` 工作树执行 `git add/commit`（`GitPython` 或 `subprocess`）。**Decision**: 用 `subprocess` 调 `git`（无需新依赖；`GitPython` 为可选）。推送到远程由运维策略决定，非本特性强制。

---

## R6 — Schema 迁移与种子（接线 Alembic + TTL 投影）

**Decision**:
- **接线 Alembic**（已是依赖，但无 `alembic/` 目录、无 `env.py`）：新增 `backend/alembic/` + `alembic.ini`，首迁移 `0001_ontology_meta` 创建全部新表（data-model.md）。运营表（已被代码引用但**当前无 create_all/迁移**）一并纳入该基线迁移，统一表创建路径。
- **启动应用迁移**：backend 容器启动命令前置 `alembic upgrade head`（Dockerfile/compose entrypoint）。
- **TTL 投影补种**：首启迁移后，由 `ontology_meta_store.project_from_ttl()` 读取已加载的 7 模块，将现有 Class/属性投影为元数据表初始行（`status=published`，幂等 upsert by `slpra_iri`），使"只读已有本体"可直接进入可编辑态。
- compose 的 `db/init/`：仅放最小 bootstrap（如扩展/角色），**主表创建走 Alembic**（避免 SQL 与 ORM 双源漂移）。

**Rationale**: 项目当前**完全没有建表机制**（无 `Base.metadata.create_all`、无 alembic 目录），这是先决缺口。Alembic 已是依赖，版本化迁移优于运行期 `create_all`，也优于纯 `db/init` SQL（后者与 ORM 易漂移）。TTL 投影让现状只读资产无缝转为可编辑。

**Alternatives considered**:
- `Base.metadata.create_all` 启动建表：无版本演进、不利于后续列变更 → 拒绝（保留为本地最小回退）。
- 纯 `db/init/*.sql`（源文档 §7.2 设想）：与 SQLAlchemy 模型双维护、易漂移 → 降级为最小 bootstrap。

---

## R7 — 认证与 RBAC（澄清遗留至规划的技术决策）

**Decision**: 引入**最小身份与角色层**，满足 FR-033（编辑/发布限高级分析师）：
- 表 `app_user`(id, username, role) 与角色枚举 `senior_analyst | operator | qa`。
- 鉴权：首版采用**反向代理注入的可信身份头**（如 `X-User`/`X-Role`，内网部署、由前置网关/SSO 提供）+ FastAPI 依赖 `get_current_user`；本地开发用 `.env` 默认用户。所有 T-Box **写/发布**端点加 `require_role("senior_analyst")` 依赖；读端点放开。
- 设计为**可插拔**：身份解析集中在 `dependencies.py`，后续替换为企业 SSO/OIDC 仅改该处。

**Rationale**: 项目当前**完全无鉴权**。内网工具以网关注入身份是常见、低成本且不锁死后续 SSO 的方案；把"谁能编辑/发布"的功能性门禁落地，而不过度投入完整 IdP（属后续合规硬化特性）。

**Alternatives considered**:
- 自建完整登录/JWT/密码体系：超出能力一范围、与后续 SSO 重复 → 拒绝（本特性仅做门禁与可插拔接口）。
- 完全不做 RBAC：违反 FR-033 → 拒绝。

> 注：完整审计哈希链、QA 电子签名（FR-034）随能力三/合规硬化特性，**不在本特性**。本特性的 `audit_log` 记录维护/发布操作（actor/action/diff 摘要）。

---

## R8 — 前端工作台技术栈（澄清"无 infilake-dw 组件"现实）

**Decision**: 在**既有** Next.js 16 / React 19 栈中**自建**所有工作台组件：
- 数据获取/缓存/失效：`@tanstack/react-query`（与读路径一致）。
- 表格/列表：`@tanstack/react-table`。
- 本地编辑态：`zustand`。
- 图谱可视化：**`d3`**（force/dagre 布局，Class 蓝/Action 橙/带基数边）——**不引入 ReactFlow**（不在依赖、避免新框架）。
- 图标：`lucide-react`；样式：Tailwind。
- `lib/api.ts` 扩展写方法（现仅 GET），统一经 `fetchAPI`。

**Rationale**: 源方案文档设想"复用 infilake-dw 前端组件"，但**实际仓库 `src/components/` 为空、依赖中无 infilake-dw、无 ReactFlow**。务实路径是按既有依赖自建，避免引入未安装的组件库造成假设性缺口。

**Alternatives considered**: 引入 ReactFlow（新增依赖、与 d3 重复）；引入 infilake-dw 包（不存在/不可得）→ 均拒绝。

---

## R9 — 模型健康度与一致性校验（FR-007）

**Decision**: `validate` 提供两档：
- **结构校验（快、默认）**：孤立类（无父无子无映射）、未映射字段（无 `slpra_iri`/`bfo_category`）、TTL 漂移（元数据表与 World/基线 TTL 不一致项，经 R3 diff 计算）、命名/重复冲突。
- **逻辑一致性（可选、较慢）**：调用 Owlready2 `sync_reasoner_pellet`/HermiT 做分类与不一致检测（如污染途径互斥被违反、不可满足类）。
- 发布前**强制**跑结构校验；存在阻断级问题（不可满足类/孤立必填映射）则拒绝发布并定位。

**Rationale**: 满足 FR-007 与 SC-002（关键类 100% 映射、无孤立项），并为发布提供质量门。一致性校验置为可选以控制交互时延（reasoner 需 Java/较慢）。

**Alternatives considered**: 每次保存即跑 reasoner（太慢）；完全不校验（违反 FR-007）→ 拒绝。

**Dependency note**: HermiT/Pellet 经 Owlready2 需 JVM。**Decision**: 一致性校验作为**可选能力**，运行环境缺 JVM 时优雅降级为"仅结构校验"并提示。

---

## R10 — Action 作为可编辑本体元数据（FR-004）

**Decision**: 在元数据表 `ontology_action` 建模 Action 的**定义**（actor_class / target_class / preconditions / postconditions / parameters_schema），前端 `action-panel` 维护。本特性**仅维护定义并随 TTL 发布**；**不**改动 `reasoning/rules/*` 的运行期 Python 规则（Action 的实际触发/编排属能力三，范围外）。

**Rationale**: 满足 FR-004"Action 定义增改删"，同时尊重澄清的范围边界（推理/编排是后续特性）。让 Action 成为可治理的本体资产，但不牵动运行期推理。

**Alternatives considered**: 现在就把规则改为数据驱动并接入运行期（牵动能力三、超范围）→ 拒绝。

---

## 汇总：本特性最终技术栈与新增

- **新增后端依赖**：`rdflib`（TTL 合并/diff）。可选：`GitPython`（否则用 `subprocess git`）。
- **新增后端模块**：`services/ontology_meta_store.py`、`services/ttl_merge.py`、`models/ontology_meta.py`、`alembic/`。
- **扩展**：`services/ontology_engine.py`（T-Box 写/导出/校验）、`api/ontology.py`（写/映射/校验/发布/导入导出）、`schemas/ontology.py`、`dependencies.py`、`main.py`、`docker-compose.yml`/`Dockerfile`。
- **前端**：`components/ontology/*`（自建）、`ontology/page.tsx` 改造、`lib/api.ts` 扩展。
- **无 NEEDS CLARIFICATION 残留**：R7（认证）已以"网关注入身份 + 可插拔 SSO"决策收敛；R9（reasoner）以"可选 + 优雅降级"收敛。
