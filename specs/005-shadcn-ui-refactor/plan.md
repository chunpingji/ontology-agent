# Implementation Plan: 前端组件系统重构（Tailwind + shadcn/ui）

**Branch**: `005-shadcn-ui-refactor` | **Date**: 2026-06-23 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/005-shadcn-ui-refactor/spec.md`

## Summary

承接 004 推迟的「视觉/组件体系」工作：在**既有 Tailwind 3.4** 之上引入 **shadcn/ui**（拷入式、基于 Radix 无头原语的组件），把当前**逐屏手写、硬编码**的样式收敛为**一套集中令牌 + 可复用、可访问的共享组件**。**纯前端表现层**：不改 004 的信息架构/路由/重定向（FR-005），不改任何后端端点或 `lib/api.ts` 的调用与载荷（FR-006），不动推理内核/合规哈希链/Part 11/本体保真逻辑。技术路径（research.md）：**手动初始化**（避开最新 CLI 的 Tailwind v4 默认）→ 令牌走 CSS 变量 + `tailwind.config` 颜色映射 + `tailwindcss-animate`（v3 专用）→ 按 US 优先级自底向上、新旧共存式增量迁移（US1 基座+外壳 → US2 高频控件 → US3 领域重屏）。

## Technical Context

**Language/Version**: TypeScript 5，Next.js 16.2.9（App Router，`output: "standalone"`），React 19.2.7

**Primary Dependencies**:
- 既有复用（**零新增**）：Tailwind CSS 3.4.19、`clsx@2.1.1`、`tailwind-merge@2.6.1`（→ `cn()`）、`lucide-react@0.400`（shadcn 图标集）、`lib/api.ts`（原生 fetch 封装）
- 净新增（白名单，宪章 V 已论证）：`class-variance-authority`、按需 `@radix-ui/react-*` 无头原语、`tailwindcss-animate`（Tailwind **v3** 动画插件，**非** v4 的 `tw-animate-css`）；`shadcn` CLI 仅 `npx` 取数、不进运行时
- 后端：**0 改动**（FastAPI + SQLAlchemy + Postgres，经 nginx 同源 `/api`）

**Storage**: 前端无持久化；身份/角色沿用 `localStorage`（`slpra.identity`）；主题为 CSS 变量（无运行时 JS）

**Testing**: 前端无既有自动化测试框架 → 门禁 = `npm run build` + `eslint`（**不引入新错误**）+ `quickstart.md` 端到端人工验证（视觉一致 / 行为零回归 / 基础可访问性：键盘·焦点陷阱·Esc·ARIA）；后端 0 改动 → 既有 pytest 不回归、不新增后端契约测试

**Target Platform**: Web（容器化，nginx 单边入口同源 `/api`，localhost:8081）

**Project Type**: Web application（frontend + backend）；**本特性仅触及 `frontend/`**

**Performance Goals**: 不引入运行时主题 JS（CSS 变量解析）；Radix 按组件 tree-shake，包体增量有界；无新增重型查询；导航/标签切换即时

**Constraints**: 视觉**基本一致非品牌改版**（FR-010）；不改 IA/路由/重定向（FR-005）；不改 API（FR-006）；无 SSR 水合不一致（FR-009）；**Tailwind v3**（非 v4）形态；增量新旧共存、任一时刻可构建可运行（FR-011）；浮层可访问（FR-003）；npm + React 19 → `--legacy-peer-deps`（research R6）

**Scale/Scope**: 9 条路由 + 20 个领域组件；约 13 个基础 shadcn 原语（按需附加少量）；实测待收敛硬编码色值密集（`text-gray-500`×65、`bg-blue-600`×22、red/amber/green 成簇）；内网小并发、长生命周期

## Constitution Check

*GATE: Phase 0 前与 Phase 1 后各评估一次。基于 constitution v1.0.0 五原则。*

| 原则 | 适用性与结论 |
|------|--------------|
| **I. 规范驱动开发** | ✅ 遵循 specify→plan 流程；spec 以假设收敛三处歧义、0 个 NEEDS CLARIFICATION；设计制品（research/data-model/contracts/quickstart）与规范一致。 |
| **II. 本体权威性与保真（NON-NEGOTIABLE）** | ✅ **不触发写入路径**。US3 仅重排本体工作台各面板的**外观**；TTL 外科式合并、三元组级 diff 预览、双存储一致性、BFO/外部对齐、乐观并发——**逻辑零改动**（contracts/components.md C3 列为硬不变量）。 |
| **III. 可追溯与审计** | ✅ 签批/拒绝/审计验真仅迁移表现层，仍经既有 `transition` 守卫与哈希链落点；shadcn 组件为**拷入仓库的源码**（可审、可 diff），增强而非削弱可追溯。 |
| **IV. 测试纪律与契约优先** | ✅ 先有 UI 契约（contracts/design-tokens.md、components.md）再实现；quickstart 提供可执行判据。无新增对外后端契约；后端不动 → pytest 不回归。前端无测试框架，自动化测试列为未来增强（YAGNI）。 |
| **V. 最小复杂度与复用** | ⚠️→✅ **有依赖新增，已论证**（见 Complexity Tracking）。复用既有 Tailwind/clsx/tailwind-merge/lucide；shadcn 非并行框架而是构建在现栈之上的拷入源码；新增项最小化且列白名单。结论：合规。 |
| **安全与合规** | ✅ 角色门控（QA-only 审批等）行为不变；身份经既有机制；无密钥入库；纯表现层不触合规判定逻辑。 |

**结论**：无未论证违例。Principle V 的依赖新增在 Complexity Tracking 中论证通过。**Phase 1 设计后复评**：令牌/组件契约与增量策略未引入新的跨层耦合或并行框架，结论维持不变。

## Project Structure

### Documentation (this feature)

```text
specs/005-shadcn-ui-refactor/
├── plan.md              # 本文件
├── research.md          # Phase 0：引入方式/依赖/v3 令牌/暗色预留/共存/peer-dep/门禁 七项决策
├── data-model.md        # Phase 1：令牌分类 + 主题 + 组件清单 + 遗留→令牌/组件迁移映射(按 US)
├── quickstart.md        # Phase 1：US1–US3 e2e 验证 + 门禁命令 + SC 收尾校验
├── contracts/
│   ├── design-tokens.md     # 令牌契约：CSS 变量 + tailwind.config + cn()
│   └── components.md        # 组件契约：components.json + 基础组件 API/无障碍 + 行为零回归不变量
└── checklists/
    └── requirements.md  # 规格质量检查单（已通过）
```

### Source Code (repository root) —— 仅 `frontend/`

```text
frontend/
├── components.json                      # 新：shadcn 工程配置（v3 形态，对齐 tsconfig 别名）
├── tailwind.config.ts                   # 改：darkMode class + 令牌颜色映射 + borderRadius + animate 插件
├── package.json                         # 改：+ cva / @radix-ui/react-* / tailwindcss-animate
└── src/
    ├── app/
    │   ├── globals.css                  # 改：@layer base 注入语义 CSS 变量（浅色 + .dark 预留位）
    │   └── (dashboard)/…                # 改：各路由界面逐屏改用 ui 原语 + 令牌（US1→US3）
    ├── components/
    │   ├── ui/                          # 新：拷入的 shadcn 基础原语（button/card/dialog/tabs/table/
    │   │                                #     input/textarea/label/select/badge/alert/separator/skeleton）
    │   ├── {analysis,approvals,ontology,extraction,integration}/…  # 改：组合 ui 原语，去手写样式
    │   └── ontology/field.tsx           # 改：重写为 Label + 控件的薄组合
    └── lib/
        └── utils.ts                     # 新：cn()（复用 clsx + tailwind-merge）
```

**Structure Decision**: Web 应用 `frontend/` 单体；新增 `components/ui/` 承载拷入原语、`lib/utils.ts` 承载 `cn()`，其余为**就地表现层替换**——不新增路由、不挪动 004 既定结构。迁移自底向上分三故事，新旧 Tailwind 类共存直至逐屏替换完成（FR-011）。

## Complexity Tracking

> 宪章 V 要求「新增第三方依赖 MUST 最小化并在 plan.md 说明必要性」。下列新增已论证：

| 新增依赖 | 为何需要 | 被拒的更简方案 |
|-----------|----------|----------------|
| `@radix-ui/react-*`（无头原语，按组件引入） | FR-003 要求浮层可访问：焦点陷阱/归还、ARIA、键盘导航。正确实现复杂且易错，Radix 是经审计的最小底座 | 手写无障碍原语——成本高、易引入 a11y 缺陷，且会在多处重复同一逻辑（违反复用） |
| `class-variance-authority` | shadcn 组件的变体（按钮/徽标/告警 variant）声明所需的极轻量助手 | 手写条件类名拼接——分散、易错、与 shadcn 上游源码不一致，增大维护负担 |
| `tailwindcss-animate` | Tailwind **v3** 下 shadcn 组件动画（弹窗/下拉过渡）所需插件 | 手写 keyframes——重复造轮子；v4 的 `tw-animate-css` 与本仓 v3 不兼容 |
| `shadcn` CLI（仅 `npx`，非运行时） | 按 components.json 取数生成与上游一致的组件源码，避免手抄漂移 | 纯手工 copy-paste——更易抄错、版本漂移 |

**说明**：以上均**非并行 UI 框架**（无 emotion/styled 等自有样式运行时），构建在既有 Tailwind 之上，且 shadcn 组件为拷入仓库、可审计的源码——与宪章 V「复用既有栈、不引并行框架」一致；新增面已最小化并白名单化（contracts/components.md C4）。
