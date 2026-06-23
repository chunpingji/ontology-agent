# Phase 1 Data Model: 导航信息架构

本特性无数据库实体。"数据模型"即前端**信息架构配置**:导航树、路由表、能力归属映射与角色可见性规则。这些是 `layout.tsx` 与路由组织的单一真理来源。

---

## 1. 导航树 (Nav Tree)

顶层 5 项,其中"图谱管理"为分组(含 2 子项),其余为可点击导航项。

```
NavTree
├─ 总览            (item)   href=/overview        role=*        icon=🏠
├─ 本体工作台       (item)   href=/ontology        role=*        icon=🧬
├─ 图谱管理         (group)                         role=*
│   ├─ 实体管理     (item)   href=/entities        role=*        icon=📦  (含子 Tab)
│   └─ 应用分析     (item)   href=/analysis        role=*        icon=⚙️  (含页内 Tab)
├─ 事实源           (item)   href=/integration     role=*        icon=🔌
└─ 审批中心         (item)   href=/approvals       role=qa       icon=✅  (qa 可见)
```

### 类型(用于 `layout.tsx` 配置化渲染)

| 概念 | 字段 | 说明 |
|------|------|------|
| **NavGroup** | `title`, `items: NavItem[]` | 顶层分组,仅"图谱管理";渲染为分区标题 + 子项缩进 |
| **NavItem** | `href`, `label`, `icon?`, `requiredRole?: Role` | 可点击项;`requiredRole` 存在时按当前身份过滤可见性 |
| **NavNode** | `NavGroup \| NavItem` | 顶层数组元素的联合类型 |

`Role = "senior_analyst" | "operator" | "qa"`(对齐后端 `VALID_ROLES`)。`requiredRole` 仅"审批中心"= `qa`;其余为 `*`(不设)。

---

## 2. 分区子标签 (Section Tabs)

### 2.1 实体管理(子路由实现)

| Tab | 路由 | 内容来源 | 高亮判据 |
|-----|------|----------|----------|
| 实体浏览/检索 | `/entities` | 既有 `entities/page.tsx` | `pathname === "/entities"` |
| 文档抽取 | `/entities/extraction` | 自 `extraction/page.tsx` 迁入 | `pathname.startsWith("/entities/extraction")` |

- 由 `entities/layout.tsx` 渲染 Tab 栏 + `{children}`。
- **流不丢失状态**:`sessionStorage["slpra.extraction.activeJobId"]` —— 文档抽取 Tab 挂载时读取并重订阅进行中作业(见 research D1)。

### 2.2 应用分析(页内 Tab,客户端状态)

| Tab | 内容来源 | 既有 API |
|-----|----------|----------|
| 推理 | reasoning 页的 PDE/MACO/评估/规则/结论面板 | `calculatePDE` `calculateMACO` `runAssessment` `getRules` `getConclusionTrace` |
| 图谱查询 | knowledge-graph 页的 SPARQL/统计 | `runSPARQL` `getKGStats` `getKGGraph` |

`PendingSignaturesPanel` 不在此页(迁往审批中心)。

---

## 3. 能力归属映射 (Capability Mapping) —— 零能力丢失矩阵

重构前 → 重构后,用于保证 SC-004(100% 能力可达)与 FR-011 重定向。

| 重构前能力 / 旧路由 | 重构后落点 | 旧路由处理 |
|---------------------|------------|------------|
| 本体编辑器 `/ontology` | 本体工作台 `/ontology`(仅改称) | 路由不变 |
| 实体管理 `/entities` | 图谱管理 › 实体管理 › 实体浏览 `/entities` | 路由不变 |
| 文档抽取 `/extraction` | 图谱管理 › 实体管理 › 文档抽取 `/entities/extraction` | 308 → 新路由 |
| 推理控制台 `/reasoning`(PDE/MACO/评估/规则/结论) | 图谱管理 › 应用分析 `/analysis`(推理 Tab) | 308 → `/analysis` |
| 推理控制台内 QA 签批弹窗 | 审批中心 `/approvals`(签批) | 经 `/analysis` 链接或直达 |
| 知识图谱 `/knowledge-graph`(SPARQL/统计) | 图谱管理 › 应用分析 `/analysis`(图谱查询 Tab) | 308 → `/analysis` |
| 事实源 `/integration` | 事实源 `/integration` | 路由不变 |
| (首页仅 Logo) | 总览 `/overview`(一级可达) | `/` → `/overview` |
| (无独立治理入口) | 审批中心 `/approvals`(待签/签批/拒绝/审计验真+列表) | 新增 |

**新增 vs 移动 vs 删除**:
- 新增页:`/overview`、`/analysis`、`/approvals`、`entities/layout.tsx`、`entities/extraction/page.tsx`
- 移动:`extraction/page.tsx` → `entities/extraction/`;`components/reasoning/qa-signature-dialog.tsx` → `components/approvals/`
- 删除(由重定向兜底):`/extraction`、`/reasoning`、`/knowledge-graph` 路由目录

---

## 4. 路由 → 重定向表(FR-011,契约见 contracts/routes.md)

| 旧 source | 新 destination | permanent |
|-----------|----------------|-----------|
| `/extraction` | `/entities/extraction` | true (308) |
| `/reasoning` | `/analysis` | true (308) |
| `/knowledge-graph` | `/analysis` | true (308) |
| `/` | `/overview` | true (308) |

---

## 5. 角色可见性规则 (Role Visibility)

| 资源 | senior_analyst | operator | qa | 强制层 |
|------|:--:|:--:|:--:|--------|
| 导航:总览/本体工作台/实体管理/应用分析/事实源 | ✓ | ✓ | ✓ | 前端均可见 |
| 导航:审批中心 | ✗ hidden | ✗ hidden | ✓ visible | 前端 `requiredRole=qa` |
| `/approvals` 页治理操作(签批/拒绝) | gate 提示 | gate 提示 | ✓ 可执行 | **后端 `require_role(qa)` 硬约束** + 前端门禁 |
| 审计验真/列表 | ✓(后端 reader) | ✗ | ✓ | 后端 `senior_analyst\|qa` |

**身份切换器**:侧栏底部 `select`(senior_analyst / operator / qa),`onChange` → `setIdentity()` 写 `localStorage` → 触发本地状态刷新(导航可见性随之更新)。开发态可达性,不改后端身份机制。

---

## 6. 状态转移(本特性不引入)

签批/拒绝引发的结论生命周期迁移(`pending_signature → effective | rejected`)由**后端既有 `transition` 守卫**完成,前端仅触发与展示,无新增状态机(见 003)。
