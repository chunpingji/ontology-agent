# Phase 1 Data Model: 设计系统模型（Tailwind + shadcn/ui）

**Feature**: 005-shadcn-ui-refactor | **Date**: 2026-06-23

本特性无业务数据实体（纯前端表现层）。此处的"数据模型"是**设计系统的结构模型**：设计令牌分类、主题、组件清单，以及"遗留内联样式 → 语义令牌/组件"的迁移映射。它是 contracts/ 与 quickstart.md 的依据。

## 1. 实体：设计令牌 (Design Token)

集中定义的视觉变量，全站唯一来源。以 CSS 变量承载（`globals.css` `@layer base`），由 `tailwind.config.ts` 暴露为 Tailwind 颜色/圆角工具类。

| 令牌组 | 令牌（语义） | 角色 | 承接的遗留用法（示例） |
|--------|--------------|------|------------------------|
| 表面 | `background` / `foreground` | 页面底色 / 主文本 | `bg-white`、默认黑字 |
| 卡片 | `card` / `card-foreground` | 卡片表面 / 卡面文本 | `bg-white` + `border` 的卡片 |
| 浮层 | `popover` / `popover-foreground` | 弹窗/下拉表面 | dialog/select 容器 |
| 主色 | `primary` / `primary-foreground` | 主操作（按钮/激活态/链接） | `bg-blue-600/700`、`text-blue-600/700` |
| 次色 | `secondary` / `secondary-foreground` | 次级表面/按钮 | `bg-gray-100` |
| 弱化 | `muted` / `muted-foreground` | 弱背景 / 辅助文字 | `bg-gray-50/100`、`text-gray-400/500/600` |
| 强调 | `accent` / `accent-foreground` | 悬停/选中底 | `hover:bg-gray-50` |
| 破坏 | `destructive` / `destructive-foreground` | 危险/拒绝/错误 | `text-red-600/700`、`bg-red-50` |
| 边框 | `border` / `input` / `ring` | 描边 / 输入框边 / 焦点环 | `border-gray-200`、focus ring |
| **领域扩展** | `warning` / `warning-foreground` | 待办/QA 闸门/告警（琥珀） | `bg-amber-50`、`text-amber-700/800` |
| **领域扩展** | `success` / `success-foreground` | 通过/正向（绿） | `bg-green-50/100`、`text-green-700` |
| 圆角 | `--radius` | 统一圆角基准 | 散落的 `rounded`/`rounded-lg` |

**校验规则**：
- 每个语义令牌 MUST 在 `:root` 有浅色取值；`.dark` 选择器位预留但本次不填值（R4）。
- Tailwind 颜色 MUST 经 `hsl(var(--token))` 映射，组件标记 MUST NOT 再写原始调色板色值（`bg-blue-600` 等）——SC-003。

## 2. 实体：主题 (Theme)

一组令牌取值的集合。v1 仅"浅色"一套；通过根元素 class 切换（`darkMode: ["class"]`）的能力在结构上具备但不交付暗色取值。

- **状态**：`light`（默认，本次交付）｜ `dark`（结构预留，未实现）。
- **关系**：Theme 1—N 引用 Design Token；切换 Theme 即整体改变令牌解析值，传播到所有消费组件（FR-004 单点传播）。

## 3. 实体：共享 UI 组件 (Shared UI Component)

落位 `frontend/src/components/ui/`（shadcn 约定），由令牌派生、封装 Radix 无障碍行为。基础原语清单（YAGNI：仅纳入现有界面真实用到者）：

| 组件 | 底层原语 | 变体/要点 | 替代的遗留实现 |
|------|----------|-----------|----------------|
| `button` | cva + (Slot) | variant: default/secondary/destructive/outline/ghost/link；size | 各处手写 `<button className="rounded bg-blue-600…">` |
| `card` | div 组合 | Card/Header/Title/Content/Footer | `rounded-lg border bg-white p-4` 容器 |
| `dialog` | `@radix-ui/react-dialog` | 焦点陷阱/Esc/遮罩/可访问标题 | qa-signature/reject/conflict 弹窗 |
| `tabs` | `@radix-ui/react-tabs` | 键盘可达、`aria-selected` | analysis 页内标签（`useState` + 手写按钮） |
| `table` | 语义化 `<table>` 封装 | Table/Header/Body/Row/Cell/Head | approvals 待签/审计表 |
| `input` | `<input>` 封装 | 统一边框/焦点环/禁用态 | 各表单输入 |
| `textarea` | `<textarea>` 封装 | 同上 | 拒绝原因、SPARQL、抽取文本 |
| `label` | `@radix-ui/react-label` | 点击聚焦关联控件 | `field.tsx` 的 `<label>` |
| `select` | `@radix-ui/react-select` | 可访问下拉、键盘导航 | 身份切换 `<select>`、连接器/筛选 |
| `badge` | cva | variant: default/secondary/destructive/outline + warning/success | 风险等级/状态/模块计数小标签 |
| `alert` | div 组合 | variant: default/destructive + warning | 「需要 QA 角色」琥珀提示等 |
| `separator` | `@radix-ui/react-separator` | 水平/垂直分隔 | 手写 `border-b`/`border-t` |
| `skeleton` | div + animate-pulse | 加载占位 | 统计/列表的 `"—"` 与裸加载态 |

**按需附加（非基线，触及对应面时再 `add`）**：`dropdown-menu`（身份/操作菜单）、`tooltip`、`scroll-area`（审计长表）、`sonner`（操作反馈 toast）。纳入与否 MUST 以"当前界面是否真实需要"为准（YAGNI）。

**组合关系**：领域包装组件（`components/{analysis,approvals,ontology,extraction,integration}/…`）MUST 仅**组合** `components/ui/` 原语，不再各自手写样式；`field.tsx` 重写为 `label` + 控件的薄组合。

## 4. 关系图（概念）

```text
Theme(light) ──N──> DesignToken ──used-by──> components/ui/* (Button, Card, Dialog, …)
                                                   │ composed-by
                                                   ▼
                          领域组件 (approvals/analysis/ontology/…) ──render──> 9 条路由界面
```

## 5. 迁移映射（遗留 → 目标）— US 归属

| 遗留样式/控件 | 目标 | 故事 |
|----------------|------|------|
| `(dashboard)/layout.tsx` 侧栏、导航项、身份 `<select>`、Logo | shell + `button`/`select`/令牌 | US1 |
| `globals.css` / `tailwind.config.ts`（空主题） | 令牌 + darkMode 位 + animate 插件 | US1 |
| overview 卡片、快捷入口、QA 计数卡 | `card` + `badge` + 令牌 | US2 |
| entities 子 Tab（route layout 内联高亮）| 令牌化的 tab-link（路由型，非 Radix Tabs） | US2 |
| analysis 页内标签（手写按钮 Tab） | `tabs`（Radix） | US2 |
| approvals 待签/审计表、签批/拒绝按钮、空态 | `table`/`button`/`badge`/`skeleton` | US2 |
| qa-signature-dialog / reject-dialog | `dialog` + `input`/`textarea`/`label` | US2 |
| ontology 各面板、`field.tsx`、ttl-toolbar、conflict-dialog | `card`/`input`/`select`/`dialog`/`button`/`label` | US3 |
| integration 连接器/实时推理面板 | `card`/`table`/`button`/`badge` | US3 |
| extraction 表单/进度/对齐复核 | `card`/`input`/`button`/`badge`/`skeleton` | US3 |
| `graph-visualization`（d3 内部） | **不迁移**，仅外围 chrome | US3 |

**不变量**：所有迁移 MUST 保持数据获取（`lib/api.ts`）、角色门控、Part 11 签批/拒绝、审计验真、抽取流式行为**逐一不变**（FR-006），仅替换表现层。
