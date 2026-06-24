# Contract: 设计令牌 (Design Tokens) — Tailwind v3 + shadcn

**Feature**: 005-shadcn-ui-refactor | **Date**: 2026-06-23

本契约固定**令牌名、语义、与配置形态**。实现 MUST 与此一致；任何新增/改名 MUST 回写本契约（宪章 IV 契约优先）。值为 HSL 三元组（shadcn 约定，`hsl(var(--token))` 消费）。

## C1. CSS 变量（`frontend/src/app/globals.css`）

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    --background: 0 0% 100%;
    --foreground: 222 47% 11%;
    --card: 0 0% 100%;
    --card-foreground: 222 47% 11%;
    --popover: 0 0% 100%;
    --popover-foreground: 222 47% 11%;
    --primary: 221 83% 53%;            /* 承接 blue-600 主操作 */
    --primary-foreground: 0 0% 100%;
    --secondary: 210 40% 96%;          /* gray-100 次级表面 */
    --secondary-foreground: 222 47% 11%;
    --muted: 210 40% 96%;
    --muted-foreground: 215 16% 47%;   /* gray-500 辅助文字 */
    --accent: 210 40% 96%;
    --accent-foreground: 222 47% 11%;
    --destructive: 0 72% 51%;          /* red 危险/拒绝 */
    --destructive-foreground: 0 0% 100%;
    --warning: 38 92% 50%;             /* 领域扩展：amber 待办/QA 闸门 */
    --warning-foreground: 26 83% 14%;
    --success: 142 71% 45%;            /* 领域扩展：green 通过/正向 */
    --success-foreground: 0 0% 100%;
    --border: 214 32% 91%;             /* gray-200 描边 */
    --input: 214 32% 91%;
    --ring: 221 83% 53%;               /* 焦点环=主色 */
    --radius: 0.5rem;
  }

  /* 暗色：结构预留，本次不填值（research R4） */
  /* .dark { --background: …; … } */
}
```

**约束**：
- 上表令牌为**完整集合**；组件标记 MUST NOT 直接写 `blue-*/gray-*/red-*/amber-*/green-*` 等原始调色板（SC-003）。
- `warning`/`success` 为本平台领域扩展（审批/风险态），非 shadcn 默认集——属本契约显式部分。

## C2. Tailwind 配置（`frontend/tailwind.config.ts`）

```ts
import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
        popover: { DEFAULT: "hsl(var(--popover))", foreground: "hsl(var(--popover-foreground))" },
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        secondary: { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--secondary-foreground))" },
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        destructive: { DEFAULT: "hsl(var(--destructive))", foreground: "hsl(var(--destructive-foreground))" },
        warning: { DEFAULT: "hsl(var(--warning))", foreground: "hsl(var(--warning-foreground))" },
        success: { DEFAULT: "hsl(var(--success))", foreground: "hsl(var(--success-foreground))" },
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};
export default config;
```

**约束**：
- `darkMode: ["class"]` MUST 存在（为暗色预留），但本次不提供切换器。
- 插件 MUST 为 `tailwindcss-animate`（Tailwind v3），**MUST NOT** 使用 v4 的 `tw-animate-css`。
- `content` glob 保持现值（已覆盖 `src/**`，含 `components/ui`）。

## C3. `cn()` 工具（`frontend/src/lib/utils.ts`）

```ts
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
```

**约束**：复用既有 `clsx@2.1.1` + `tailwind-merge@2.6.1`（无新增依赖）。所有 `components/ui/*` MUST 用 `cn()` 合并类名。

## C4. 验收（对应 SC）

- 任意 `components/ui/*` 与已迁移领域组件中，原始调色板色值出现次数 → **0**（SC-003）。
- 修改单个 `--token` 值，依赖该令牌的所有界面同步变化（FR-004 单点传播）。
- 令牌经 CSS 变量解析，无运行时 JS → SSR/CSR 一致，无水合不一致（FR-009）。
