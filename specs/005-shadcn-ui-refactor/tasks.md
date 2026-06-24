---
description: "Task list for 005-shadcn-ui-refactor"
---

# Tasks: 前端组件系统重构（Tailwind + shadcn/ui）

**Input**: Design documents from `/specs/005-shadcn-ui-refactor/`

**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/{design-tokens,components}.md ✓, quickstart.md ✓

**Tests**: 前端**无自动化测试框架**（plan.md Testing）。规范未请求 TDD，故**不生成自动化测试任务**；每个故事以「`npm run build` + `lint` 不引入新错误 + quickstart 人工 e2e（视觉一致 / 行为零回归 / 基础可访问性）」的**验证任务**收尾。

**Organization**: 任务按用户故事分组，每个故事可独立实现与验证。所有路径相对仓库根；本特性仅触及 `frontend/`。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无未完成依赖）
- **[Story]**: US1 / US2 / US3（映射 spec.md 用户故事）
- 每条含精确文件路径

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 净新增依赖与工程配置就位（不触组件实现）。

- [X] T001 安装净新增依赖（白名单，contracts/components.md C4）：在 `frontend/` 运行 `npm install class-variance-authority tailwindcss-animate --legacy-peer-deps`；确认 `frontend/package.json` 出现 `class-variance-authority` 与 `tailwindcss-animate`，且 `clsx`/`tailwind-merge`/`lucide-react` 复用既有版本（无重复新增）。
- [X] T002 记录质量门禁基线（FR-008 / SC-005）：在 `frontend/` 运行 `npm run build` 与 `npm run lint`，将当前 lint 告警/错误数留档为「重构前基线」（写入 `specs/005-shadcn-ui-refactor/quickstart.md` 末尾或 PR 描述），后续每个故事须对比此基线不新增错误。
- [X] T003 创建 shadcn 工程配置 `frontend/components.json`，逐字对齐 contracts/components.md C1（`style: new-york`、`rsc: true`、`tsx: true`、`tailwind.config → tailwind.config.ts`、`css → src/app/globals.css`、`baseColor: slate`、`cssVariables: true`、aliases `@/components`·`@/lib/utils`·`@/components/ui`、`iconLibrary: lucide`）；校验 aliases 与 `frontend/tsconfig.json` 的 `@/* → ./src/*` 一致。

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 设计令牌 + `cn()` + 基础 UI 原语。**⚠️ 阻塞所有用户故事**——US1/US2/US3 在本阶段完成前不得开始。

- [X] T004 创建 `frontend/src/lib/utils.ts` 的 `cn()`（contracts/design-tokens.md C3：`twMerge(clsx(inputs))`，复用既有 `clsx@2.1.1` + `tailwind-merge@2.6.1`，零新增依赖）。
- [X] T005 改写 `frontend/src/app/globals.css`：在 `@layer base :root` 注入全部语义 CSS 变量，逐值对齐 contracts/design-tokens.md C1（含领域扩展 `--warning`/`--success` 及其 `-foreground`、`--radius`）；保留 `.dark { … }` 注释占位但**不填暗色值**（research R4）；**不得**出现 v4 的 `@theme`。
- [X] T006 改写 `frontend/tailwind.config.ts`，对齐 contracts/design-tokens.md C2：`darkMode: ["class"]`、`hsl(var(--token))` 颜色映射（含 warning/success）、`borderRadius` 基于 `--radius`、`plugins: [require("tailwindcss-animate")]`；保持现有 `content` glob；**禁止**引入 v4 的 `tw-animate-css`。
- [X] T007 生成基础 UI 原语到 `frontend/src/components/ui/`：在 `frontend/` 运行 `npx shadcn@latest add button card dialog tabs table input textarea label select badge alert separator skeleton --legacy-peer-deps`；核对每个文件导出与 contracts/components.md C2 表一致，且各文件用 `cn()`、交互件带 `"use client"`。
- [X] T008 扩展变体（contracts/components.md C2 扩展约束）：在 `frontend/src/components/ui/badge.tsx` 的 `badgeVariants` 增加 `warning`、`success`；在 `frontend/src/components/ui/alert.tsx` 的变体表增加 `warning`——均经 `cva` 映射到 `bg-warning`/`text-warning-foreground`/`bg-success`/… 令牌类（**不得**写原始调色板）。
- [X] T009 基座门禁：在 `frontend/` 运行 `npm run build` + `npm run lint`，确认构建 GREEN、无 v4 残留（grep 确认无 `tw-animate-css`、无 `@theme`）、相对 T002 基线无新增错误。

**Checkpoint**: 令牌 + `cn()` + ui 原语就绪——US1/US2/US3 可开始（如有多人可并行）。

---

## Phase 3: User Story 1 - 设计系统基座 + 应用外壳统一 (Priority: P1) 🎯 MVP

**Goal**: 把始终在场的应用外壳（侧栏领域分层导航、激活高亮、身份切换器、Logo、页面容器骨架）迁移到统一令牌 + ui 原语。

**Independent Test**: 加载任意路由，外壳全部由统一组件渲染、视觉一致；键盘可遍历、读屏可识别；004 导航行为（高亮规则、分组、身份切换、旧链接 308 重定向）零回归；改 `--primary` 全站主色同步。

- [X] T010 [US1] 迁移 `frontend/src/app/(dashboard)/layout.tsx` 外壳 chrome：侧栏容器、领域分层导航项、激活高亮、Logo、页面标题/容器骨架 → 令牌类 + `Button`(ghost/link 变体)/`Separator`/`Card`；移除该文件中的硬编码 `bg-*/text-*/border-*` 调色板类。**保持** 004 的导航高亮规则、分组展开、QA-only 审批中心可见性（FR-005/FR-007）。
- [X] T011 [US1] 在 `frontend/src/app/(dashboard)/layout.tsx` 内将身份切换 `<select>` 替换为可访问的 `Select` 原语；**复用** `frontend/src/lib/use-identity.ts` 的读写逻辑与 `slpra.identity` 持久化不变（FR-006）。（同文件，依赖 T010）
- [X] T012 [P] [US1] 迁移 `frontend/src/app/(dashboard)/entities/layout.tsx` 的实体子标签为**令牌化的路由型 tab-link**（非 Radix Tabs，data-model §5）：激活态由令牌驱动，保留 Next `<Link>` 路由语义与高亮。
- [X] T013 [P] [US1] 令牌化根壳：`frontend/src/app/layout.tsx`（全局字体/`bg-background`/`text-foreground`）与 `frontend/src/app/page.tsx`（重定向占位）改用令牌，去除硬编码色值；确认无 SSR 水合不一致（FR-009）。
- [X] T014 [US1] **US1 验证**（依赖 T010–T013）：`npm run build` + `lint` 无新增错误；按 quickstart.md「US1」走查——外壳统一渲染、键盘 Tab/方向键可达、身份 `Select` 读屏可朗读、`/extraction` 等旧路径 308 与高亮同 004、改 `--primary` 全站传播。

**Checkpoint**: US1 独立可用——构成可演示 MVP。

---

## Phase 4: User Story 2 - 高频交互控件统一 (Priority: P2)

**Goal**: 总览卡/快捷入口、应用分析页内标签、审批中心待签/审计表、所有按钮、QA 签批/拒绝弹窗、表单输入与下拉迁移到统一组件。

**Independent Test**: 走查总览/应用分析/审批中心——卡片/标签页/表格/按钮/弹窗均为统一组件；弹窗陷阱焦点 + Esc 关闭 + 关闭后焦点归还；标签页键盘可切换；Part 11 重认证与校验、审计验真、数据获取与角色门控行为不变。

- [X] T015 [P] [US2] 迁移 `frontend/src/app/(dashboard)/overview/page.tsx`：概览卡 / QA 待签计数卡 / 快捷入口卡 → `Card` + `Badge`；空/加载态 → `Skeleton`；数据获取（`lib/api.ts`）与角色门控不变（FR-006）。
- [X] T016 [P] [US2] 迁移 `frontend/src/app/(dashboard)/analysis/page.tsx` 的页内「推理 / 图谱查询」标签为 Radix `Tabs`（`role=tab`/`aria-selected`/方向键），激活态令牌驱动，内容切换行为同 004。
- [X] T017 [P] [US2] 迁移 `frontend/src/components/analysis/graph-query-panel.tsx` → `Card`/`Textarea`(SPARQL)/`Input`/`Button`/`Table`(结果)；查询调用与载荷不变（FR-006）。
- [X] T018 [P] [US2] 迁移 `frontend/src/components/analysis/reasoning-panels.tsx` → `Card`/`Badge`/`Button`；推理结果展示逻辑不变。
- [X] T019 [P] [US2] 迁移 `frontend/src/app/(dashboard)/approvals/page.tsx`：待签/审计列表 → `Table`、操作 → `Button`、状态 → `Badge`、空/加载 → `Skeleton`；「校验审计链」结果用 `success`/`destructive` 令牌（通过/篡改文案配色），审计验真逻辑不变。
- [X] T020 [P] [US2] 迁移 `frontend/src/components/approvals/qa-signature-dialog.tsx` → `Dialog` + `Input`/`Label`（焦点陷阱 / Esc / 焦点归还「签批」按钮）；**21 CFR Part 11 重认证与原因必填校验、提交走既有 `lib/api.ts` 调用与载荷——逐一不变**（FR-006 / contracts/components.md C3）。
- [X] T021 [P] [US2] 迁移 `frontend/src/components/approvals/reject-dialog.tsx` → `Dialog` + `Textarea`/`Label`；原因必填校验不变、提交走既有 `rejectConclusion` 调用与载荷不变（FR-006）。
- [X] T022 [US2] **US2 验证**（依赖 T015–T021）：`npm run build` + `lint` 无新增错误；按 quickstart.md「US2」走查——弹窗焦点陷阱/Esc/归还、`Tabs` 键盘可切换、表格与空态统一、Part 11 与审计行为零回归。

**Checkpoint**: US1 + US2 各自独立可用。

---

## Phase 5: User Story 3 - 领域重屏控件收敛 (Priority: P3)

**Goal**: 本体工作台各面板、事实源连接器/实时推理、文档抽取创建/进度/对齐复核迁移到统一组件，淘汰最后一批硬编码样式。

**Independent Test**: 本体/事实源/实体-抽取各屏完全由统一组件与令牌渲染，无残留硬编码调色板类；编辑/保存/校验/diff 预览/断点续看等领域行为与重构前一致。

- [X] T023 [P] [US3] 重写 `frontend/src/components/ontology/field.tsx` 为 `Label` + 控件的薄组合（data-model §3），去除 `text-gray-400/500` 等硬编码，改令牌。
- [X] T024 [P] [US3] 迁移 `frontend/src/components/ontology/class-panel.tsx` → `Card`/`Input`/`Select`/`Button`/`Label`；本体写入逻辑（外科式合并/diff）零改动（宪章 II / FR-006）。
- [X] T025 [P] [US3] 迁移 `frontend/src/components/ontology/link-type-panel.tsx` → `Card`/`Input`/`Select`/`Button`/`Label`；领域逻辑不变。
- [X] T026 [P] [US3] 迁移 `frontend/src/components/ontology/data-property-panel.tsx` → `Card`/`Input`/`Select`/`Button`/`Label`；领域逻辑不变。
- [X] T027 [P] [US3] 迁移 `frontend/src/components/ontology/action-panel.tsx` → `Card`/`Input`/`Select`/`Button`/`Label`；领域逻辑不变。
- [X] T028 [P] [US3] 迁移 `frontend/src/components/ontology/restriction-editor.tsx` → `Card`/`Input`/`Select`/`Button`；基数/约束编辑逻辑不变。
- [X] T029 [P] [US3] 迁移 `frontend/src/components/ontology/risk-attribute-wizard.tsx` → `Card`/`Input`/`Button`/`Badge`（分步向导）；逻辑不变。
- [X] T030 [P] [US3] 迁移 `frontend/src/components/ontology/ontology-mapping-panel.tsx` → `Card`/`Table`/`Input`/`Select`/`Button`；外部对齐映射逻辑不变。
- [X] T031 [P] [US3] 迁移 `frontend/src/components/ontology/ttl-toolbar.tsx` → `Button`/`Separator`/`Badge`；导出/发布触发逻辑不变。
- [X] T032 [P] [US3] 迁移 `frontend/src/components/ontology/conflict-dialog.tsx` → `Dialog` + `Button`/`Table`(三元组级 diff)；**复用** `frontend/src/components/ontology/use-version-conflict.ts` 乐观并发逻辑不变（宪章 II/III）。
- [X] T033 [P] [US3] `frontend/src/components/ontology/graph-visualization.tsx`：**仅迁移外围 chrome**（容器/工具条/按钮 → 令牌 + `Button`）；**d3 SVG/canvas 内部渲染不动**（spec Edge Cases）。
- [X] T034 [P] [US3] 令牌化 `frontend/src/app/(dashboard)/ontology/page.tsx` 容器/分栏骨架 → `Card`/`Separator`/令牌（仅外观，面板组合不变）。
- [X] T035 [P] [US3] 迁移 `frontend/src/components/integration/connector-manager.tsx` → `Card`/`Table`/`Button`/`Badge`/`Select`；连接器管理调用与载荷不变。
- [X] T036 [P] [US3] 迁移 `frontend/src/components/integration/realtime-inference-panel.tsx` → `Card`/`Button`/`Badge`；实时推理逻辑不变。
- [X] T037 [P] [US3] 令牌化 `frontend/src/app/(dashboard)/integration/page.tsx` 容器骨架 → `Card`/令牌（仅外观）。
- [X] T038 [P] [US3] 迁移 `frontend/src/components/extraction/job-create-form.tsx` → `Card`/`Input`/`Textarea`/`Select`/`Button`/`Label`；创建作业调用与载荷不变。
- [X] T039 [P] [US3] 迁移 `frontend/src/components/extraction/job-progress.tsx` → `Card`/`Badge`/`Skeleton`；**`activeJobId` 持久化与断点续看（004 行为）不变**（FR-006）。
- [X] T040 [P] [US3] 迁移 `frontend/src/components/extraction/alignment-review.tsx` → `Card`/`Table`/`Button`/`Badge`；对齐复核逻辑不变。
- [X] T041 [P] [US3] 令牌化 `frontend/src/app/(dashboard)/entities/extraction/page.tsx` 与 `frontend/src/app/(dashboard)/entities/page.tsx` 容器/列表骨架 → `Card`/`Table`/`Badge`/令牌（仅外观；浏览与抽取入口逻辑不变）。
- [X] T042 [US3] **US3 验证**（依赖 T023–T041）：`npm run build` + `lint` 无新增错误；按 quickstart.md「US3」走查——各领域屏统一渲染、本体编辑/保存/校验/冲突 diff/抽取续看行为零回归、无残留硬编码调色板类。

**Checkpoint**: 三故事均独立可用，全站迁移完成。

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 全站收尾校验（对应 Success Criteria）。

- [X] T043 残留色值清零（SC-003）：在 `frontend/` 运行 quickstart.md 的 grep（`grep -rE "(bg|text|border|ring)-(blue|gray|amber|red|green|slate|zinc|neutral)-[0-9]{2,3}" src | grep -v "components/ui/" | wc -l`），计数应趋近 0；逐一令牌化剩余命中（`components/ui/` 内部除外）。
- [X] T044 [P] 复用度核验（SC-006）：确认至少有一个新增共享控件（如 `Card`/`Button`/`Dialog`）被 ≥2 个界面直接复用而无需重写样式；在 PR 描述列举复用点。
- [X] T045 [P] 基础可访问性扫查（SC-002）：对全部已迁移浮层（`Dialog`/`Select`/`Tabs`）人工核验键盘可操作、焦点陷阱、Esc 关闭、ARIA 角色——严重问题数 = 0。
- [X] T046 全量回归门禁（SC-004/SC-005）：`npm run build` + `lint` 相对 T002 基线零新增错误；按 quickstart.md「全局收尾校验」跑通 004 全部用户旅程（导航/总览/实体浏览·抽取/分析标签/QA 签批·拒绝/审计验真）行为零回归。

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，立即开始。
- **Foundational (Phase 2)**: 依赖 Setup 完成——**阻塞所有用户故事**。
- **User Stories (Phase 3–5)**: 均依赖 Phase 2 完成；之后可并行（多人）或按 P1→P2→P3 顺序。
- **Polish (Phase 6)**: 依赖期望交付的用户故事全部完成。

### User Story Dependencies

- **US1 (P1)**: Phase 2 后即可开始——不依赖其他故事；为 MVP。
- **US2 (P2)**: Phase 2 后即可开始——消费 US1 的令牌/原语但可独立验证。
- **US3 (P3)**: Phase 2 后即可开始——消费令牌/原语但可独立验证。

### Within Each User Story

- 同一文件的任务顺序执行（如 T010→T011 同为 `(dashboard)/layout.tsx`）。
- 故事内 [P] 任务（不同组件文件）可并行。
- 每个故事以验证任务收尾，依赖该故事全部实现任务。

### Parallel Opportunities

- Phase 1 中 T001 完成后，T003 可与 T002 并行。
- Phase 2 中 T004/T005/T006 可并行（不同文件）；T007 依赖 T003+T004+T005+T006；T008 依赖 T007；T009 依赖全部。
- Phase 2 完成后，US1/US2/US3 可由不同成员并行推进。
- 故事内：US1 的 T012/T013 与 T010 并行（不同文件）；US2 的 T015–T021 全部 [P]；US3 的 T023–T041 全部 [P]。

---

## Parallel Example: User Story 2

```bash
# Phase 2 完成后，US2 的高频控件迁移可并行启动（均不同文件）：
Task: "迁移 overview/page.tsx → Card/Badge/Skeleton"          # T015
Task: "迁移 analysis/page.tsx 页内标签 → Radix Tabs"           # T016
Task: "迁移 analysis/graph-query-panel.tsx"                     # T017
Task: "迁移 analysis/reasoning-panels.tsx"                      # T018
Task: "迁移 approvals/page.tsx → Table/Button/Badge/Skeleton"  # T019
Task: "迁移 approvals/qa-signature-dialog.tsx → Dialog"        # T020
Task: "迁移 approvals/reject-dialog.tsx → Dialog"              # T021
# 完成后跑 T022 验证（依赖以上全部）
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. 完成 Phase 1 Setup（T001–T003）。
2. 完成 Phase 2 Foundational（T004–T009，**阻塞**）。
3. 完成 Phase 3 US1（T010–T014）。
4. **STOP & VALIDATE**：独立验证 US1（外壳统一、可访问、004 导航零回归、令牌单点传播）。
5. 可演示/交付 MVP。

### Incremental Delivery

1. Setup + Foundational → 基座就绪。
2. + US1 → 独立验证 → 演示（MVP）。
3. + US2 → 独立验证 → 演示。
4. + US3 → 独立验证 → 演示。
5. 每个故事新旧 Tailwind 类可短期共存（FR-011），任一时刻可构建可运行。

### Parallel Team Strategy

Phase 2 完成后：开发 A 接 US1、B 接 US2、C 接 US3——三故事独立完成并集成；故事内 [P] 任务进一步并行。

---

## Notes

- 全程**仅替换表现层**：`lib/api.ts` 调用/载荷、角色门控、Part 11 签批·拒绝、审计验真、抽取流式、本体保真逻辑**逐一不变**（FR-006 / contracts/components.md C3 / 宪章 II）。
- 安装/取数 MUST 用 `--legacy-peer-deps`（React 19，research R6）。
- 组件标记 MUST NOT 出现原始调色板（`bg-blue-*` 等）——全部走令牌（SC-003）。
- [P] = 不同文件、无未完成依赖；[Story] 标签用于可追溯。
- 建议每个任务或逻辑组完成后提交；可在任一 Checkpoint 停下独立验证。

---

## Phase 7: Convergence

> 由 `/speckit-converge` 追加（仅追加，不改动 T001–T046）。下列为代码现状相对 spec/plan 仍存在的缺口，全部为 `partial`（表现层未完全收敛到共享组件/令牌）；无 CRITICAL、无 missing/contradicts/unrequested，无宪章冲突。按 HIGH→MEDIUM→LOW 排序。

- [X] T047 将本体工作台页内编辑器标签（`frontend/src/app/(dashboard)/ontology/page.tsx` 的 `setTab` 切换，原始 `<button>`）迁移为可访问的 Radix Tabs（`role=tab`/`aria-selected`/方向键导航） per FR-003 (partial)
- [X] T048 将事实源页 dashboard/connectors 标签（`frontend/src/app/(dashboard)/integration/page.tsx`，原始 `<button>` + `border-b-2 border-primary` 激活态）迁移为 Radix Tabs per FR-003 (partial)
- [X] T049 将 `frontend/src/components/analysis/reasoning-panels.tsx` 的 PDE/MACO/评估表单原生控件（`<input>`×7、`<select>`、`<textarea>`）迁移为共享 Input/Select/Textarea 原语 per FR-002 (partial)
- [X] T050 将 `frontend/src/app/(dashboard)/entities/page.tsx` 模块筛选原生 `<select>`（含空字符串"全部模块"项）迁移为 Select 原语（用哨兵值表示"全部模块"） per FR-002 (partial)
- [X] T051 令牌化剩余 2 处 purple 分类徽标（`reasoning-panels.tsx:210` CFDI 情景标签、`ontology-mapping-panel.tsx:97` 孤立映射徽标，`bg-purple-100 text-purple-700`）改用语义令牌 per FR-004 / SC-003 (partial)
