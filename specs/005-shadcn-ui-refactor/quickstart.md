# Quickstart 验证指南: 前端组件系统重构（Tailwind + shadcn/ui）

**Feature**: 005-shadcn-ui-refactor | **Date**: 2026-06-23

端到端验证场景。前端无自动化测试框架，故以**构建/lint 门禁 + 人工 e2e（视觉一致 / 行为零回归 / 基础可访问性）**为判据。详尽令牌/组件契约见 [contracts/](./contracts/)，迁移映射见 [data-model.md](./data-model.md)。

## 前置

- 已在 `005-shadcn-ui-refactor` 分支（基于 004 之上）。
- 全栈可起（quickstart 行为校验需后端）：`docker compose up`，前端经 nginx 同源 `/api`（localhost:8081，沿 004 约定）。
- 包管理器 npm；React 19 → 安装/取数加 `--legacy-peer-deps`。

## 初始化（一次性，US1 基座）

```bash
cd frontend
# 1) 净新增依赖（白名单见 contracts/components.md C4）
npm install class-variance-authority tailwindcss-animate --legacy-peer-deps
# 2) 手写 components.json / globals.css 令牌 / tailwind.config.ts / lib/utils.ts（按 contracts/）
# 3) 逐个拉取基础组件（示例）
npx shadcn@latest add button card dialog tabs table input textarea label select badge alert separator skeleton --legacy-peer-deps
```

**期望**：`components/ui/*` 生成；`lib/utils.ts` 含 `cn()`；无 v4 残留（无 `tw-animate-css`、无 `@theme`）。

## 质量门禁（每个故事完成后必跑）

```bash
cd frontend && npm run build && npm run lint
```

**期望**：构建 GREEN；lint **不引入新错误**（对比重构前基线，FR-008 / SC-005）。

---

## US1 — 设计系统基座 + 应用外壳

1. `npm run dev`，访问任意路由（如 `/overview`）。
   - **期望**：侧栏、导航项、身份切换器、Logo、页面容器均由统一组件/令牌渲染；观感与重构前**基本一致**（FR-010）。
2. 键盘 Tab 遍历侧栏与身份切换器；用方向键/Enter 操作身份 `select`。
   - **期望**：焦点可见、可达；身份切换为可访问的 `Select`；读屏可朗读（FR-003）。
3. 切换身份为 `qa` / `senior_analyst`，访问 `/extraction`（旧路径）。
   - **期望**：308 重定向与导航高亮行为与 004 **完全一致**（FR-005）；审批中心项仅 QA 可见（FR-007）。
4. 改 `globals.css` 中 `--primary` 一个值并刷新。
   - **期望**：全站主色同步变化（FR-004 单点传播 / SC-006 复用）。

## US2 — 高频交互控件

1. `/overview`：概览卡、QA 待签卡、快捷入口卡均为 `Card`+`Badge`；空/加载态用 `Skeleton`。
2. `/analysis`：用鼠标与键盘切换「推理 / 图谱查询」。
   - **期望**：为可访问 `Tabs`（方向键、`aria-selected`）；内容切换行为与 004 一致。
3. `/approvals`（QA 身份）：待签与审计为 `Table`；点「签批」打开 `Dialog`。
   - **期望**：弹窗**陷阱焦点**、**Esc 关闭**、**关闭后焦点回到「签批」按钮**；Part 11 重认证与原因必填校验**行为不变**（FR-006 / C3）。
4. 「拒绝」弹窗同样校验：原因必填、提交走既有 `rejectConclusion`，载荷不变。
5. 点「校验审计链」：结果展示与 004 一致（通过/篡改文案与配色经 `success`/`destructive` 令牌）。

## US3 — 领域重屏收敛

1. `/ontology`：编辑某面板→保存/校验/触发冲突。
   - **期望**：表单/按钮/对话框为统一组件；**TTL 外科式合并、diff 预览、双存储一致、乐观并发逻辑零改动**（宪章 II / FR-006）。
2. `/integration`：连接器管理与实时推理面板控件全部统一，无遗留临时样式。
3. `/entities/extraction`：创建作业→进度→对齐复核，控件统一；`activeJobId` 断点续看不变。
4. `graph-visualization`：d3 内部渲染照常，仅外围 chrome 统一（不在组件库范畴）。

---

## 全局收尾校验

```bash
# 残留原始调色板色值应趋近 0（SC-003）
cd frontend && grep -rE "(bg|text|border|ring)-(blue|gray|amber|red|green|slate|zinc|neutral)-[0-9]{2,3}" src | grep -v "components/ui/" | wc -l
```

- **SC-001**：9 条路由 + 高频控件 100% 经统一组件系统渲染。
- **SC-002**：所有浮层（dialog/select/tabs）键盘可操作、焦点陷阱、Esc、ARIA —— 严重可访问性问题 = 0。
- **SC-003**：上述 grep 计数 → 0（`components/ui/` 内部除外）。
- **SC-004**：004 全部用户旅程行为零回归。
- **SC-005**：build + lint 无新增错误。
- **SC-006**：新增任一共享控件被 ≥2 界面复用而无需重写样式。
