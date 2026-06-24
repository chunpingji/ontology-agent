# Contract: 共享 UI 组件与配置 — shadcn/ui on Next 16 + React 19 + Tailwind v3

**Feature**: 005-shadcn-ui-refactor | **Date**: 2026-06-23

固定 `components/ui/` 基础组件的**公共 API、可访问性保证**与 shadcn 工程配置（`components.json`）。实现 MUST 与此一致。

## C1. `components.json`（项目根 `frontend/components.json`）

```json
{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "new-york",
  "rsc": true,
  "tsx": true,
  "tailwind": {
    "config": "tailwind.config.ts",
    "css": "src/app/globals.css",
    "baseColor": "slate",
    "cssVariables": true,
    "prefix": ""
  },
  "aliases": {
    "components": "@/components",
    "utils": "@/lib/utils",
    "ui": "@/components/ui",
    "lib": "@/lib",
    "hooks": "@/lib"
  },
  "iconLibrary": "lucide"
}
```

**约束**：
- `tailwind.config` MUST 指向 `tailwind.config.ts`、`css` MUST 指向 `src/app/globals.css`、`cssVariables: true`（对齐 design-tokens 契约）。
- `aliases` MUST 与 `tsconfig.json` 的 `@/* → ./src/*` 一致（已确认存在）。
- `rsc: true`（App Router）；交互组件文件自带 `"use client"`。
- 不依赖该文件触发自动 init；它仅供 `npx shadcn@latest add <c>` 取数时定位（research R1）。

## C2. 基础组件公共契约（`components/ui/`）

每个组件 MUST 导出与 shadcn 上游一致的命名导出，并以 `cn()` 接受 `className` 透传。关键变体/无障碍约束：

| 组件 | 导出 | 公共 API / 变体 | 无障碍保证（FR-003） |
|------|------|------------------|----------------------|
| `button.tsx` | `Button`, `buttonVariants` | `variant`: default｜secondary｜destructive｜outline｜ghost｜link；`size`: sm｜default｜lg｜icon；`asChild` | 原生 `<button>` 语义；可见焦点环（`ring`）；禁用态 |
| `card.tsx` | `Card`,`CardHeader`,`CardTitle`,`CardDescription`,`CardContent`,`CardFooter` | 纯组合，`className` 透传 | 标题用语义标签 |
| `dialog.tsx` | `Dialog`,`DialogTrigger`,`DialogContent`,`DialogHeader`,`DialogTitle`,`DialogDescription`,`DialogFooter`,`DialogClose` | 受控 `open`/`onOpenChange` | **焦点陷阱 + 关闭后焦点归还**；Esc 关闭；遮罩；`DialogTitle` 必填以供读屏 |
| `tabs.tsx` | `Tabs`,`TabsList`,`TabsTrigger`,`TabsContent` | `value`/`onValueChange` | 方向键切换、`role=tab`/`aria-selected`、`tabpanel` 关联 |
| `table.tsx` | `Table`,`TableHeader`,`TableBody`,`TableRow`,`TableHead`,`TableCell`,`TableCaption` | 纯封装 | 语义 `<table>` 结构 |
| `input.tsx` | `Input` | 透传原生 props | label 关联、焦点环、禁用态 |
| `textarea.tsx` | `Textarea` | 同上 | 同上 |
| `label.tsx` | `Label` | `htmlFor`/嵌套 | 点击聚焦关联控件（Radix Label） |
| `select.tsx` | `Select`,`SelectTrigger`,`SelectValue`,`SelectContent`,`SelectItem`,`SelectGroup`,`SelectLabel` | 受控 `value`/`onValueChange` | 完整键盘导航、`aria-*`、可读选项 |
| `badge.tsx` | `Badge`, `badgeVariants` | `variant`: default｜secondary｜destructive｜outline｜**warning**｜**success** | 仅视觉，必要时配文字 |
| `alert.tsx` | `Alert`,`AlertTitle`,`AlertDescription` | `variant`: default｜destructive｜**warning** | `role=alert` 语义 |
| `separator.tsx` | `Separator` | `orientation` | `role=separator` |
| `skeleton.tsx` | `Skeleton` | `className` | 装饰性，`aria-hidden` |

**扩展约束**：`badge`/`alert` 的 `warning`、`badge` 的 `success` 变体为本平台扩展（对应 design-tokens 的 `warning`/`success` 令牌），MUST 经 `cva` 加入变体表。

## C3. 行为零回归契约（FR-006，最关键）

迁移**仅替换表现层**，下列既有行为 MUST 逐一保持：

- **数据获取**：`lib/api.ts` 的全部调用、参数、URL 与载荷不变；不新增/改动任何后端端点。
- **角色门控**：`useIdentity()`/`getIdentity()` 与 `qa`/`senior_analyst`/`operator` 可见性逻辑不变；审批中心仍 QA-only。
- **Part 11 合规**：签批 / 拒绝弹窗的重认证、原因必填校验、提交流程与 003 行为一致。
- **审计验真**：审计链校验与列表展示逻辑不变。
- **抽取流式**：`activeJobId` 持久化与断点续看（004 行为）不变。
- **本体保真（宪章 II）**：ontology 面板迁移后，TTL 外科式合并、diff 预览、双存储一致性、乐观并发——**逻辑零改动**，仅改外观。

## C4. 安装与门禁契约

- 包管理器 npm；安装 shadcn 依赖与 `add` 组件 MUST 用 `--legacy-peer-deps`（React 19，research R6）。
- 净新增运行时依赖白名单：`class-variance-authority`、所用 `@radix-ui/react-*`、`tailwindcss-animate`。其余（clsx/tailwind-merge/lucide-react）复用既有。
- 门禁：`npm run build` + `eslint` 相对重构前基线**无新增错误**（FR-008 / SC-005）。
