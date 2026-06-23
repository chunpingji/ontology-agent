---
description: "Task list for 004-navigation-ia-restructure"
---

# Tasks: 导航信息架构重构 —— 按"本体→实体→应用→治理"分层

**Input**: Design documents from `/specs/004-navigation-ia-restructure/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: 规格未要求自动化测试;前端无既有测试框架。验证以 `quickstart.md` 端到端 + `npm run lint`/`npm run build` 为门禁。**故不生成 test 任务**。

**Organization**: 任务按用户故事分组,每组可独立实现与验证。范围仅 `frontend/`,后端 0 改动。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行(不同文件、无未决依赖)
- **[Story]**: 所属用户故事(US1–US4);Setup/Foundational/Polish 无标签

## Path Conventions

Web app:前端根 `frontend/`,源码 `frontend/src/`。下列路径均相对仓库根。

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 建立改造基线

- [X] T001 在 `frontend/` 执行 `npm install` 并确认改造前 `npm run lint` 与 `npm run build` 均通过(记录基线;standalone 构建可用)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 所有故事共用的身份/角色读取能力

**⚠️ CRITICAL**: 完成后方可开始各用户故事

- [X] T002 [P] 新增可响应的身份/角色 hook `frontend/src/lib/use-identity.ts`:封装既有 `getIdentity()` 的响应式读取与 `setIdentity()` 写入(供 US1 导航 qa 可见性门禁与 US3 审批中心页门禁共用;SSR 安全,挂载后再读 localStorage 避免水合不一致)

**Checkpoint**: 身份 hook 就绪 — 用户故事可开始

---

## Phase 3: User Story 1 - 分层导航与总览入口 (Priority: P1) 🎯 MVP

**Goal**: 左侧导航重组为 5 个分层顶层入口(总览 / 本体工作台 / 图谱管理〔实体管理·应用分析〕/ 事实源 / 审批中心),新增"总览"一级落地页。

**Independent Test**: 侧栏显示 5 顶层 + "图谱管理"下 2 子项;无"文档抽取/推理控制台/知识图谱"平铺项;点击"总览"进入 `/overview`;当前位置高亮(分组+子项)正确。

### Implementation for User Story 1

- [X] T003 [US1] 重写 `frontend/src/app/(dashboard)/layout.tsx`:分层导航(顶层 5 项 + "图谱管理"分组含 实体管理/应用分析 两子项),按 `contracts/routes.md` 实现 分组/子项/子标签三层高亮(`usePathname`),"本体编辑器"改称"本体工作台",经 `use-identity`(T002)对非 `qa` 隐藏"审批中心"项,侧栏底部加 senior_analyst/operator/qa 身份切换器(调用 `setIdentity`)
- [X] T004 [P] [US1] 新建 `frontend/src/app/(dashboard)/overview/page.tsx` 总览落地页:聚合 `getKGStats()`(实体/模块计数),qa 身份额外显示 `getPendingSignatures().conclusions.length` 待签计数,提供到各分区的快捷入口卡片

> 注:US1 阶段导航已链向 `/entities/extraction`、`/analysis`、`/approvals`(由 US2/US3/US4 创建)。如需严格"仅 US1"演示,可暂将这些链接指向既有 `/extraction`、`/reasoning` 路由,待后续阶段切到新路由。

**Checkpoint**: 导航骨架 + 总览可独立验证(US1 可交付)

---

## Phase 4: User Story 2 - 文档抽取归入实体管理(一级子 Tab) (Priority: P1)

**Goal**: 实体管理内以子路由形式提供"实体浏览/检索"与"文档抽取"两个一级子 Tab,抽取保留完整流水线且进行中流不丢失。

**Independent Test**: `/entities` 默认在实体浏览;`/entities/extraction` ≤1 次点击可达且能完成 抽取→复核→持久化;进行中作业切 Tab 返回仍可续看;深链与浏览器前进/后退正确。

### Implementation for User Story 2

- [X] T005 [P] [US2] 新建 `frontend/src/app/(dashboard)/entities/layout.tsx`:子 Tab 栏(实体浏览/检索 ↔ 文档抽取),按 `data-model.md §2.1` 用 `usePathname` 判定高亮,渲染 `{children}`
- [X] T006 [P] [US2] 将 `frontend/src/app/(dashboard)/extraction/page.tsx` 内容迁至新建 `frontend/src/app/(dashboard)/entities/extraction/page.tsx`,并删除旧目录 `frontend/src/app/(dashboard)/extraction/`
- [X] T007 [US2] 在 `frontend/src/app/(dashboard)/entities/extraction/page.tsx` 增加进行中流持久化(research D1):`activeJobId` 存 `sessionStorage["slpra.extraction.activeJobId"]`,挂载时读取并 `getExtractionJob`+`getJobCandidates`,非终态则经 `subscribeJobProgress` 重订阅(依赖 T006,同文件)
- [X] T008 [P] [US2] 调整 `frontend/src/app/(dashboard)/entities/page.tsx`:标题让位于子 Tab 语境(去除与 Tab 栏重复的页头),保持实体浏览/检索功能不变

**Checkpoint**: 实体管理双子 Tab 可独立验证(US2 可交付)

---

## Phase 5: User Story 3 - 独立审批中心(QA 治理工作台) (Priority: P1)

**Goal**: 独立 `/approvals` 页聚合 待签列表 + Part 11 电子签批 + QA 拒绝 + 审计链验真/列表,受 qa 角色门禁;签批不再仅靠推理页弹窗。

**Independent Test**: 非 qa 不见"审批中心"且直达 `/approvals` 见门禁占位、治理操作不可执行;qa 在单页完成 签批/拒绝/验真,≤2 次点击到达。

### Implementation for User Story 3

- [X] T009 [P] [US3] 在 `frontend/src/lib/api.ts` 新增 `rejectConclusion()`(`POST /api/compliance/reject`)与 `getComplianceAudit()`(`GET /api/compliance/audit`)及其类型,严格按 `contracts/compliance-client.md`(指向既有后端端点,勿与既有 `getAudit()`/`/ontology/audit` 混用)
- [X] T010 [P] [US3] 将 `frontend/src/components/reasoning/qa-signature-dialog.tsx` 移动至 `frontend/src/components/approvals/qa-signature-dialog.tsx`(内容不变),并更新引用
- [X] T011 [P] [US3] 新建 `frontend/src/components/approvals/reject-dialog.tsx`:重认证(用户名/密码)+ 拒绝原因,调用 `rejectConclusion()`(模式参照 qa-signature-dialog)
- [X] T012 [US3] 新建 `frontend/src/app/(dashboard)/approvals/page.tsx`:经 `use-identity` 做 qa 门禁(非 qa 显示"需 qa 角色"占位);qa 下展示 待签列表(`getPendingSignatures`)、签批(QaSignatureDialog)、拒绝(RejectDialog)、审计验真(`verifyAudit`)+ 审计列表(`getComplianceAudit`)(依赖 T002、T009、T010、T011)

**Checkpoint**: 审批中心可独立验证(US3 可交付)。US1–US3(全部 P1)构成首发重构闭环。

---

## Phase 6: User Story 4 - 应用分析整合(推理 + 图谱查询) (Priority: P2)

**Goal**: `/analysis` 页内 Tab 同时承载 推理(PDE/MACO/评估/规则/结论)与 图谱查询/统计(SPARQL/KG stats),取代两个独立顶层项。

**Independent Test**: `/analysis` 同分区内完成一次推理计算/评估与一次 SPARQL 查询/看 KG 统计,功能与重构前等价;页内不再内嵌 QA 签批弹窗。

### Implementation for User Story 4

- [X] T013 [P] [US4] 将 `frontend/src/app/(dashboard)/reasoning/page.tsx` 的 `PDECalculator`/`MACOCalculator`/`AssessmentPanel` 及规则/结论展示抽出为 `frontend/src/components/analysis/` 下组件(`PendingSignaturesPanel` 不迁出——它归审批中心)
- [X] T014 [US4] 新建 `frontend/src/app/(dashboard)/analysis/page.tsx`:页内 Tab 推理|图谱查询;推理 Tab 用 T013 组件,图谱查询 Tab 迁入 `knowledge-graph/page.tsx` 的 SPARQL + KG 统计;可加"前往审批中心"链接(依赖 T013)
- [X] T015 [US4] 删除旧路由目录 `frontend/src/app/(dashboard)/reasoning/` 与 `frontend/src/app/(dashboard)/knowledge-graph/`(内容已迁移;旧链接由 Polish 的重定向兜底)(依赖 T014)

**Checkpoint**: 应用分析整合可独立验证(US4 可交付)

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 旧链接重定向、清理与全量验收(需各目标路由已存在)

- [X] T016 [P] 在 `frontend/next.config.ts` 增加 `async redirects()`:`/extraction→/entities/extraction`、`/reasoning→/analysis`、`/knowledge-graph→/analysis`、`/→/overview`(均 permanent 308,FR-011;依赖 T004/T006/T014 目标存在)
- [X] T017 [P] 更新 `frontend/src/app/page.tsx`:标签随导航定稿更新(本体编辑器→本体工作台 等),或确认 `/→/overview` 重定向已使其不可达后做最小清理
- [X] T018 [P] 检查并修正前端对已迁移路由的内部链接:`grep -rn "/extraction\|/reasoning\|/knowledge-graph" frontend/src`,将组件内残留链接改为新路由,确保顶层无旧平铺项、全站 0 死链(SC-004)
- [X] T019 执行 `quickstart.md` 场景 1–5 端到端验证,并跑 `npm run lint` + `npm run build`(均须通过;确认后端未改动、pytest 无需回归 — FR-014/015)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (T001)**:无依赖,先行
- **Foundational (T002)**:依赖 Setup;阻塞所有用户故事
- **User Stories (Phase 3–6)**:均依赖 Foundational(T002)
  - US1/US2/US3/US4 之间相互独立,可并行(若人力允许)或按 P1→P1→P1→P2 顺序
- **Polish (Phase 7)**:依赖目标路由就绪 —— T016 依赖 T004/T006/T014;T018/T019 依赖全部故事完成

### User Story Dependencies

- **US1 (P1)**:仅依赖 T002;导航链接的新目标由后续阶段补齐(见 T003 注)
- **US2 (P1)**:仅依赖 T002;可独立测试(直达 `/entities`、`/entities/extraction`)
- **US3 (P1)**:仅依赖 T002;可独立测试(直达 `/approvals`)
- **US4 (P2)**:仅依赖 T002;可独立测试(直达 `/analysis`)

### Within Each User Story

- US1:T003 与 T004 不同文件可并行;
- US2:T005、T006 并行;T007 依赖 T006(同文件);T008 并行
- US3:T009、T010、T011 并行;T012 依赖三者 + T002
- US4:T013 先行;T014 依赖 T013;T015 依赖 T014

### Parallel Opportunities

- T002(Foundational)单任务
- US1:T004 ∥ T003
- US2:T005 ∥ T006 ∥ T008(T007 串在 T006 后)
- US3:T009 ∥ T010 ∥ T011 →(汇入)T012
- US4:T013 →（串行）T014 → T015
- Polish:T016 ∥ T017 ∥ T018 →（最后）T019
- 跨故事:US1–US4 在 T002 后可由不同人并行

---

## Parallel Example: User Story 3

```bash
# T002 完成后,US3 内部可并行启动:
Task: "在 lib/api.ts 新增 rejectConclusion()/getComplianceAudit() + 类型"        # T009
Task: "移动 qa-signature-dialog.tsx 到 components/approvals/ 并更新引用"          # T010
Task: "新建 components/approvals/reject-dialog.tsx"                              # T011
# 三者完成后:
Task: "新建 (dashboard)/approvals/page.tsx 聚合签批/拒绝/验真,qa 门禁"          # T012
```

---

## Implementation Strategy

### MVP First

- **最小 MVP**:Setup + Foundational + **US1**(分层导航 + 总览)即可独立交付 IA 价值(顶层认知负担下降),其余子页可暂沿用既有路由。
- **首发重构闭环(推荐)**:US1 + US2 + US3(全部 P1)—— 抽取归位、治理工作台独立,构成"真正的重构"主体。
- **完整交付**:再加 US4(P2)应用分析整合 + Polish 重定向/验收。

### Incremental Delivery

1. Setup + Foundational → 基线就绪
2. US1 → 验证导航/总览 → 演示(MVP)
3. US2 → 验证实体管理子 Tab → 演示
4. US3 → 验证审批中心 → 演示
5. US4 → 验证应用分析 → 演示
6. Polish → 重定向 + 全量 quickstart + 构建门禁

---

## Notes

- 范围仅 `frontend/`;后端契约与 pytest 0 改动(FR-014/015)。
- `[P]` = 不同文件、无未决依赖。
- 每个故事可独立完成与验证;在任一 Checkpoint 可停下验收。
- 删除旧路由(T006/T015)务必在对应新页就绪后,并由 T016 重定向兜底书签。
- 提交建议:每完成一个故事(或逻辑组)提交一次。
