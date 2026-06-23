# Implementation Plan: 导航信息架构重构 —— 按"本体→实体→应用→治理"分层

**Branch**: `004-navigation-ia-restructure` | **Date**: 2026-06-23 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/004-navigation-ia-restructure/spec.md`

## Summary

把前端左侧导航从 6 项纯平铺重组为按领域心智模型分层的结构(**总览 / 本体工作台 / 图谱管理〔实体管理 · 应用分析〕/ 事实源 / 审批中心**),并据此重组 App Router 路由:文档抽取并入实体管理作一级子 Tab(子路由)、推理控制台与知识图谱合并为"应用分析"、把埋在推理页弹窗里的 QA 签批/拒绝/审计能力抽出为独立"审批中心"页。**纯前端 IA 与路由层改造**:复用既有后端契约(仅为 `审批中心` 在 `lib/api.ts` 新增 2 个客户端绑定指向**已存在**的后端端点 `POST /compliance/reject`、`GET /compliance/audit`),不新增任何后端能力,不触碰推理内核、合规哈希链与 Part 11 签名机制。

## Technical Context

**Language/Version**: TypeScript 5,Next.js 16.2(App Router),React 19.2

**Primary Dependencies**: Next.js(`output: "standalone"`)、Tailwind CSS 3.4、`clsx`、`lib/api.ts`(原生 `fetch` 封装,既有页面未用 React Query/d3 于本特性范围);后端不改动(FastAPI + SQLAlchemy + Postgres,经 nginx 边缘代理同源 `/api`)

**Storage**: 前端无持久化;身份/角色沿用 `localStorage`(`slpra.identity`)既有机制;后端 Postgres 不变

**Testing**: 前端无既有自动化测试框架——本特性以 `quickstart.md` 端到端手动验证 + `npm run build`/`lint` 作为质量门禁;**后端 0 改动 → 既有 pytest 套件保持不变、不新增后端契约测试**(FR-014)

**Target Platform**: Web(容器化,nginx 单边入口同源路由 `/api`)

**Project Type**: Web application(frontend + backend);**本特性仅触及 `frontend/`**

**Performance Goals**: 标准内网 Web 应用;导航/子 Tab 切换即时;不引入新的重型查询;总览页仅聚合既有轻量端点(KG 统计、待签计数)

**Constraints**: 不新增后端 API(FR-014);不改推理内核/哈希链/Part 11(FR-015);旧路径以重定向保书签防 404(FR-011);实体管理子 Tab 间切换 MUST 不丢失进行中的抽取实时流(US2 AS3)

**Scale/Scope**: 6 个平铺路由 → 5 个分层顶层入口 + 2 个子路由 + 2 个新页(总览、审批中心);内网小并发、长生命周期

## Constitution Check

*GATE: Phase 0 前与 Phase 1 后各评估一次。基于 constitution v1.0.0 五原则。*

| 原则 | 适用性与结论 |
|------|--------------|
| **I. 规范驱动开发** | ✅ 遵循 specify→plan 流程;命名定稿写入 spec Assumptions;无规范缺口下私自决断。 |
| **II. 本体权威性与保真(NON-NEGOTIABLE)** | ✅ **不触发**。本特性不写 T-Box、不回写 TTL、不动 BFO/外部对齐。`本体工作台`仅是`本体编辑器`的导航重命名,既有 `/ontology` 编辑能力与双存储一致性逻辑原样保留。 |
| **III. 可追溯与审计** | ✅ `审批中心`仅**呈现/触发**既有审计链与签批端点;签批/拒绝仍经既有 `transition` 守卫与 `audit.append` 哈希链落点,审计机制零改动。 |
| **IV. 测试纪律与契约优先** | ✅ 无新增对外后端契约;新增的 2 个 `lib/api.ts` 绑定指向 **003 已交付且已测**的端点。`quickstart.md` 提供可执行 e2e 判据。后端不动 → pytest 不回归。 |
| **V. 最小复杂度与复用** | ✅ 复用 `lib/api.ts`/Tailwind/既有组件;移动而非重写 `QaSignatureDialog`;身份切换复用既有 `setIdentity()`;不引入并行框架。 |
| **安全与合规** | ✅ `审批中心`及签批/拒绝/验真复用既有 `qa` 角色门禁(后端 `require_role(ROLE_QA)` 为硬约束,前端做可见性门禁为纵深防御);`senior_analyst` 编辑权不受影响;无密钥入库。 |

**结论**:无违例,Complexity Tracking 留空。Phase 1 设计后复评同上(纯前端 IA,未引入新复杂度)。

## Project Structure

### Documentation (this feature)

```text
specs/004-navigation-ia-restructure/
├── plan.md              # 本文件
├── research.md          # Phase 0:路由/子Tab/重定向/角色门禁 决策
├── data-model.md        # Phase 1:IA 模型(导航树、路由表、能力归属映射、角色可见性)
├── quickstart.md        # Phase 1:对应 US1–US4 的 e2e 验证脚本
├── contracts/
│   ├── routes.md            # 规范路由表 + 旧→新重定向契约(UI 契约)
│   └── compliance-client.md # 审批中心复用的既有后端端点的客户端绑定契约
└── checklists/
    └── requirements.md  # 规格质量检查单(已通过)
```

### Source Code (repository root) —— 仅 `frontend/`

```text
frontend/src/
├── app/
│   ├── page.tsx                          # 改:重定向/精简为入口(经 next.config 重定向 → /overview)
│   ├── (dashboard)/
│   │   ├── layout.tsx                    # 重写:分层导航(分组+子项)、高亮、qa 门禁的审批中心项、身份/角色切换器(复用 setIdentity)
│   │   ├── overview/page.tsx             # 新:总览落地页(既有统计/快捷入口聚合)
│   │   ├── ontology/page.tsx             # 不变(导航改称"本体工作台")
│   │   ├── entities/
│   │   │   ├── layout.tsx                # 新:子 Tab 栏(实体浏览/检索 · 文档抽取)+ children
│   │   │   ├── page.tsx                  # 沿用(实体浏览/检索;标题让位于子 Tab 语境)
│   │   │   └── extraction/page.tsx       # 移动自 (dashboard)/extraction;activeJobId 持久化以续看进行中流
│   │   ├── analysis/page.tsx             # 新/合并:推理(PDE/MACO/评估/规则/结论)+ 图谱查询/统计(SPARQL/stats),页内 Tab
│   │   ├── approvals/page.tsx            # 新:qa 门禁;待签列表 + 签批 + 拒绝 + 审计验真/列表
│   │   └── integration/page.tsx          # 不变(事实源)
│   ├── (dashboard)/extraction/           # 删除(内容移入 entities/extraction);旧路径由重定向兜底
│   ├── (dashboard)/reasoning/            # 删除(内容并入 analysis);旧路径重定向
│   └── (dashboard)/knowledge-graph/      # 删除(内容并入 analysis);旧路径重定向
├── components/
│   ├── approvals/
│   │   ├── qa-signature-dialog.tsx       # 移动自 components/reasoning/
│   │   └── reject-dialog.tsx             # 新:QA 拒绝(重认证 + 原因)
│   └── analysis/                         # 新:从 reasoning 页抽出的计算器/评估/规则面板(可选拆分)
├── lib/api.ts                            # 增:rejectConclusion()、getComplianceAudit() + 类型(均指向既有端点)
└── ../next.config.ts                     # 增:async redirects() 旧→新 路径(FR-011)
```

**Structure Decision**: Web 应用的 `frontend/` 单体;沿用 App Router 的 `(dashboard)` 路由组与持久 `layout.tsx` 侧栏。实体管理采用**嵌套 layout + 子路由**承载子 Tab(真实 URL,原生 深链/前进后退);应用分析用**单路由 + 页内 Tab**(无长流,从简)。旧路由删除后由 `next.config.ts` 的 `redirects()` 服务端 308 兜底书签。

## Complexity Tracking

> 无 Constitution 违例,无需论证。
