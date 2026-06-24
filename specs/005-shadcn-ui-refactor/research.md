# Phase 0 Research: 前端组件系统重构（Tailwind + shadcn/ui）

**Feature**: 005-shadcn-ui-refactor | **Date**: 2026-06-23

本特性无悬而未决的 [NEEDS CLARIFICATION]（spec 已以假设收敛）。Phase 0 聚焦于**技术选型与集成路径**的落地决策，确保在 Next 16.2 + React 19.2 + Tailwind **3.4** 这一具体栈上零意外地引入 shadcn/ui。

---

## R1. shadcn/ui 引入方式：手动初始化（而非 CLI 自动 init）

- **Decision**: 采用**手动初始化**：自行安装依赖、手写 `components.json` / `tailwind.config.ts` / `globals.css` / `lib/utils.ts`，随后用 `npx shadcn@latest add <component>` **仅**逐个拉取基础组件源码到 `components/ui/`。不运行 `npx shadcn init` 让 CLI 改写既有配置。
- **Rationale**:
  - 当前 shadcn 最新 CLI 默认面向 **Tailwind v4**（其 init 会写 `tw-animate-css`、`@theme inline`、`style: "radix-nova"` 等 v4 形态），与本仓 **Tailwind v3.4** 不兼容；让 CLI 全自动 init 有改坏 `globals.css`/`tailwind.config.ts` 的风险。
  - 手动初始化使每一处配置变更**可逐行审查**，契合宪章 III（可追溯）与 V（最小复杂度、受控变更）。
  - `add` 子命令读取我们手写的 `components.json`，按其 `tailwind`/`aliases` 生成与 v3 兼容的组件源码，既享受 CLI 的取数便利，又不放任其改全局配置。
- **Alternatives considered**:
  - *`npx shadcn@latest init` 全自动*：被拒——会按 v4 形态改写配置，回退成本高且偏离受控变更原则。
  - *完全纯手工 copy-paste 组件*：被拒——`add` 在尊重 components.json 的前提下取数更快且与上游一致，无需放弃。

## R2. 依赖增量：最小化、对齐宪章 V

- **Decision**: 净新增运行时依赖仅限：`class-variance-authority`（变体助手）、按所用组件**逐个**引入的 `@radix-ui/react-*` 无头原语（如 `react-dialog`/`react-slot`/`react-tabs`/`react-select`/`react-label`/`react-separator` 等）、以及 Tailwind v3 动画插件 `tailwindcss-animate`。`shadcn` CLI 仅作 devDependency / `npx` 取数，不进运行时。
- **Rationale**:
  - `clsx@2.1.1`、`tailwind-merge@2.6.1`、`lucide-react@0.400` **已在依赖中**（已确认 `node_modules` 存在），`cn()` 与图标集**零新增**即可复用。
  - Radix 是**无头可访问性原语**，非与现栈冲突的并行 UI 框架；shadcn 组件是**拷入仓库的源码**（我们拥有、可审计），构建在既有 Tailwind 之上。满足宪章 V「复用既有栈、依赖最小化、不引并行框架」，新增必要性已在 plan.md 论证。
  - **Tailwind v3 用 `tailwindcss-animate`**，**不是** v4 的 `tw-animate-css`——这是本栈关键差异点。
- **Alternatives considered**:
  - *手写无障碍原语（焦点陷阱/ARIA/键盘）*：被拒——正确实现成本高且易错（FR-003），Radix 是经审计的最小可行底座。
  - *引入 MUI / Mantine 等成品库*：被拒——属并行框架，违反宪章 V，且带来 emotion/自有样式系统与 Tailwind 冲突。

## R3. Tailwind v3 设计令牌策略：CSS 变量 + config 颜色映射

- **Decision**: 在 `globals.css` 的 `@layer base` 下定义语义 CSS 变量（HSL 三元组，shadcn 约定），`tailwind.config.ts` 设 `darkMode: ["class"]`、`theme.extend.colors` 映射到 `hsl(var(--token))`、`theme.extend.borderRadius` 绑 `--radius`，并启用 `tailwindcss-animate` 插件。语义令牌集采用 shadcn 标准集（`background/foreground/card/popover/primary/secondary/muted/accent/destructive/border/input/ring`）**并扩展两个领域令牌** `warning`（承接 amber 簇）与 `success`（承接 green 簇）。
- **Rationale**:
  - 现状 `tailwind.config.ts` 的 `theme.extend` 为空、`globals.css` 仅三条 `@tailwind`，硬编码色值横行（实测 `text-gray-500`×65、`bg-blue-600`×22、red/amber/green 各成簇）。集中令牌正是 FR-004 / SC-003 的落点。
  - 现有调色板可干净映射：blue→`primary`、gray-500/400/600→`muted-foreground`、gray-50/100→`muted`/`secondary`、red→`destructive`、border-gray-200→`border`；amber/green 在 shadcn 默认集无对应 → 增 `warning`/`success` 两个语义令牌（审批/风险态需要）。
  - CSS 变量驱动主题 = **无运行时 JS 主题逻辑** → 规避 SSR 水合不一致（FR-009）。
- **Alternatives considered**:
  - *仅用 Tailwind 调色板别名、不引 CSS 变量*：被拒——无法单点切主题、不为暗色预留、偏离 shadcn 约定。
  - *Tailwind v4 `@theme` 令牌*：被拒——需升级 Tailwind 大版本，超出本特性范围且风险外溢。

## R4. 暗色/多主题：令牌结构预留，但本次不交付

- **Decision**: 令牌按 shadcn 约定组织（`:root` 浅色 + 预留 `.dark` 选择器位 + `darkMode: ["class"]`），但**本迭代不实现暗色取值、不加切换器**。
- **Rationale**: 与 spec 假设一致；token 体系天然支持后续无重写地补暗色，符合 YAGNI（宪章 V）——结构留位成本近零，实装取值/切换器属未批准范围，不提前构建。
- **Alternatives considered**: *本次直接做暗色*：被拒——超范围，且品牌/视觉策略尚停在「视觉一致非改版」（FR-010）。

## R5. 增量迁移与新旧共存策略

- **Decision**: 按 spec 故事优先级**自底向上**迁移：先建令牌+原语并迁应用外壳（US1），再迁高频控件（US2），最后收敛领域重屏（US3）。迁移期间未迁移页面继续用既有硬编码工具类——因二者都最终编译为 Tailwind 原子类，**可无缝共存**；令牌引入后旧 `gray-500` 等仍有效，逐屏替换即可，任一时刻可构建可运行（FR-011）。
- **Rationale**: shadcn 组件与遗留内联标记同属 Tailwind 产物，无样式运行时冲突；分故事交付让每步独立可测、可演示（US 独立验收）。
- **Alternatives considered**: *一次性大重写*：被拒——不可独立验收、回归面巨大、违反增量交付与 MVP 切片原则。

## R6. React 19 + npm 的对等依赖处理

- **Decision**: 包管理器为 **npm**（仓库存在 `package-lock.json`）。安装 shadcn 依赖与 `add` 组件时使用 `--legacy-peer-deps`（或 `--force`）以消解 React 19 对等依赖告警。
- **Rationale**: shadcn 官方《React 19》指南明确：npm 用户在 React 19 下，CLI 会提示选 `--force` 或 `--legacy-peer-deps`；Radix 近版已支持 React 19，告警属对等声明滞后而非真实不兼容。
- **Alternatives considered**: *切换到 pnpm/bun*：被拒——更换包管理器超范围且影响 CI/容器构建，非本特性所需。

## R7. 测试与质量门禁（前端无测试框架）

- **Decision**: 沿用 004 既定门禁：`npm run build` + `eslint` **不引入新错误**（FR-008），叠加 `quickstart.md` 的端到端**视觉一致 + 行为零回归 + 基础可访问性（键盘/焦点陷阱/Esc/ARIA）**人工验证场景。后端 0 改动 → pytest 套件不受影响、不新增后端契约测试。
- **Rationale**: 仓库前端无自动化测试栈（与 004 一致）；本特性纯前端表现层，契约即「UI 组件/令牌契约 + 行为parity」，以 contracts/ + quickstart 承载（宪章 IV）。
- **Alternatives considered**: *引入 Vitest/RTL/axe 自动化*：被记为**未来增强**——价值高但属新测试基建，超出本次已批准范围（YAGNI）；本次以可执行 quickstart 判据兜底。

---

**Phase 0 结论**：技术路径全部落定，无残留 NEEDS CLARIFICATION。可进入 Phase 1 设计（令牌模型 / 组件契约 / quickstart）。
