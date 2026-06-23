# Phase 0 Research: 导航信息架构重构

本特性为前端 IA / 路由重组,无未知技术栈;研究聚焦"如何在既有 Next.js 16 App Router + `lib/api.ts` 约束下,以最小复杂度满足子 Tab 深链、流不丢失、旧链接重定向、qa 门禁四项关键需求"。所有决策不引入新依赖。

---

## D1. 子 Tab 的实现方式(实体管理:实体浏览 / 文档抽取)

**Decision**: 用 **嵌套 layout + 子路由**(`/entities` 与 `/entities/extraction` 两个真实路由,`entities/layout.tsx` 渲染 Tab 栏 + `{children}`)。

**Rationale**:
- 真实 URL → 原生满足"子 Tab 深链"(edge case)与"浏览器前进/后退回到对应子标签"(edge case),无需手写 history 管理。
- 高亮可直接由 `usePathname()` 推导(`/entities` 精确 vs `/entities/extraction` 前缀)。
- 符合 App Router 惯用法,代码量最小(constitution V)。

**应对"切 Tab 不丢进行中抽取流"(US2 AS3)**:子路由切换会卸载另一 Tab 组件。抽取作业是**服务端权威**,SSE 可在重挂载时重订阅。机制:把 `activeJobId` 持久化到 `sessionStorage`;`extraction/page.tsx` 挂载时读取 → 重新 `getExtractionJob` + `getJobCandidates`,若非终态则经既有 `subscribeJobProgress` 重订阅。返回该 Tab 即可继续查看,不丢工作。

**Alternatives considered**:
- *单路由 + 查询参 `?tab=`,两子树常挂载(仅隐藏)*:能完美保留 SSE 订阅,但需手动管理高亮/历史,且常挂载更重。鉴于作业状态服务端权威、重订阅成本低,不采用。
- *Parallel Routes / Intercepting Routes*:能力过剩,复杂度不匹配(constitution V),否决。

---

## D2. 应用分析(推理 + 图谱查询)的页面组织

**Decision**: **单路由 `/analysis` + 页内轻量 Tab**(推理 | 图谱查询),客户端状态切换;把 `reasoning/page.tsx` 的 `PDECalculator`/`MACOCalculator`/`AssessmentPanel`/规则/结论 与 `knowledge-graph/page.tsx` 的 SPARQL/KG 统计聚合于此。

**Rationale**: 这些是无长生命周期流的独立计算器/查询;页内 Tab 足够,避免为弱深链需求增设子路由(constitution V)。`PendingSignaturesPanel` **从此页移除**(迁往审批中心);可保留一个"前往审批中心"的链接。

**Alternatives considered**: 为"推理/查询"设子路由——深链收益低于实体管理场景,不值当;否决。

---

## D3. 旧链接 / 书签重定向(FR-011)

**Decision**: 在 `frontend/next.config.ts` 增加 `async redirects()`,服务端 308 永久重定向:
- `/extraction` → `/entities/extraction`
- `/reasoning` → `/analysis`
- `/knowledge-graph` → `/analysis`
- `/`(可选)→ `/overview`

**Rationale**: Next 原生 `redirects()` 对直接命中/书签生效(SSR 308),在 standalone 构建下可用,零运行时代码、零额外依赖(constitution V)。删除旧路由目录后由此兜底,杜绝硬 404(SC-005)。

**Alternatives considered**:
- *保留旧页做客户端 `redirect()`*:多留空壳文件、客户端跳转,劣于配置式服务端重定向;否决。
- *rewrites 同址*:会掩盖真实 URL,破坏"顶层不再出现旧项"的可验证性;否决。

---

## D4. 角色门禁与"成为 qa"的途径(FR-009 / US3 AS5)

**Decision**: 三层处理:
1. **后端硬约束(既有)**:`/compliance/signatures/pending|signatures|reject` 由 `require_role(ROLE_QA)` 守卫,`/audit*` 由 `senior_analyst|qa` 守卫——无论前端如何都 403/200,纵深防御不变。
2. **前端可见性门禁**:`layout.tsx` 读取 `getIdentity()`,非 `qa` 不渲染"审批中心"导航项;直接命中 `/approvals` 时页面渲染"需 qa 角色"占位而非治理操作。
3. **身份/角色切换器(必要的最小开发态可达性)**:侧栏底部加一个 `select`,复用既有 `setIdentity()` 在 `senior_analyst`/`operator`/`qa` 间切换。

**Rationale**: 既有身份仅经 `localStorage` 程序化设置,**当前无任何 UI 可切到 qa**——没有切换器则审批中心与 US3 AS5 无法被演示/验证。切换器复用既有 `setIdentity()`,不是新增后端能力、不是 SSO(SSO 仍按既有"可插拔后续接入"假设),属最小且必要的开发态可达性(constitution V + 安全章"身份经可信网关注入、SSO 可插拔")。

**注意(既有行为)**:当前 `reasoning` 页在默认 `senior_analyst` 身份下调用 qa 门禁的 `signatures/pending` 会 403,被 `catch` 吞掉而静默显示"暂无"。迁入审批中心后,该页对非 qa 显式给出角色门禁提示,优于静默空列表。

**Alternatives considered**: *始终显示审批中心、仅靠后端 403*——非 qa 会看到一堆失败操作,违背 FR-009"看到或执行...MUST NOT";否决可见但不门禁的方案。

---

## D5. 审批中心所需的客户端绑定(FR-014 复用边界)

**Decision**: 在 `lib/api.ts` 新增 **2 个绑定,均指向已存在的后端端点**,不新增后端:
- `rejectConclusion({conclusion_id, username, password, reason})` → `POST /api/compliance/reject`(后端 `reject_conclusion`,003 已交付)
- `getComplianceAudit(params?)` → `GET /api/compliance/audit`(后端 `list_audit`)

已存在可直接复用:`verifyAudit`、`getPendingSignatures`、`signConclusion`。

**Rationale**: 审批中心是既有端点的**组合/编排**,符合 FR-014"仅以既有端点的组合实现";后端契约与 pytest 不变(constitution IV)。

**Alternatives considered**: 新增后端聚合端点——违反 FR-014 与"后端 0 改动"目标;否决。

---

## D6. 总览(总览页)的内容来源

**Decision**: `/overview` 仅聚合**既有轻量端点**:`getKGStats()`(实体/模块计数)、对 qa 额外显示 `getPendingSignatures().conclusions.length`(待签计数),并提供到各分区的快捷入口卡片。

**Rationale**: 满足"总览作为一级可达落地页"(FR-002),复用既有数据,零新端点、零重型查询。现有 `app/page.tsx` 营销式卡片改为重定向至 `/overview`,统一入口。

**Alternatives considered**: 重型仪表盘(整合 integration dashboard、图表)——超出本特性"IA 重组"范围(Out of Scope:不新增能力),留待后续;本期从简。

---

## D7. QaSignatureDialog 的归属

**Decision**: 将 `components/reasoning/qa-signature-dialog.tsx` **移动**到 `components/approvals/qa-signature-dialog.tsx`,内容不变;新增 `components/approvals/reject-dialog.tsx`(重认证 + 拒绝原因,调用 `rejectConclusion`)。

**Rationale**: 组件物理归位到其唯一新使用者(审批中心),消除"埋在推理页"的耦合(spec 主旨);移动而非重写,保留既有签批交互(constitution V)。

---

## 决策汇总

| 编号 | 决策 | 关键理由 |
|------|------|----------|
| D1 | 实体管理:嵌套 layout + 子路由,`activeJobId` 持久化重订阅 | 原生深链/前进后退;服务端权威流可重订阅 |
| D2 | 应用分析:单路由 + 页内 Tab | 无长流,从简 |
| D3 | `next.config.ts` `redirects()` 服务端 308 | 零代码兜底书签,防硬 404 |
| D4 | 三层角色门禁 + 复用 `setIdentity` 的切换器 | 可演示/可验证 qa 门禁,不引入 SSO |
| D5 | `lib/api.ts` 增 2 绑定指向既有端点 | 复用边界,后端 0 改动 |
| D6 | 总览聚合既有轻量端点 | 一级落地页,不新增能力 |
| D7 | QaSignatureDialog 移入 approvals + 新增 reject-dialog | 组件归位,移动不重写 |
