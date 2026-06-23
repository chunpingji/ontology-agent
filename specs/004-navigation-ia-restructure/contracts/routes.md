# UI Contract: 规范路由表与重定向

本特性的"对外接口"是 App Router 的 URL 契约。下表为重构后的**规范路由**与**旧→新重定向**,供实现、测试与 quickstart 校验共用。

## 规范路由 (Canonical Routes)

| 路由 | 页面 | 导航落点 | 角色 | 子标签 |
|------|------|----------|:--:|--------|
| `/overview` | 总览落地页 | 总览(顶层) | * | — |
| `/ontology` | 本体 TBox 工作台 | 本体工作台(顶层) | * | — |
| `/entities` | 实体浏览/检索 | 图谱管理 › 实体管理 | * | 默认 Tab |
| `/entities/extraction` | 文档抽取流水线 | 图谱管理 › 实体管理 | * | 文档抽取 Tab |
| `/analysis` | 应用分析(推理 + 图谱查询) | 图谱管理 › 应用分析 | * | 页内 Tab:推理 / 图谱查询 |
| `/integration` | 事实源 | 事实源(顶层) | * | — |
| `/approvals` | 审批中心 | 审批中心(顶层) | qa | — |

**不变式**:
- 顶层导航 MUST NOT 再出现 `/extraction`、`/reasoning`、`/knowledge-graph` 作为独立平铺项(FR-010)。
- `/approvals` 导航项仅当当前身份角色为 `qa` 时可见(FR-009);直达该 URL 时,非 qa 见角色门禁占位,治理操作不可执行(后端 403 兜底)。
- 当前位置高亮 MUST 覆盖 分组 / 子项 / 子标签三层(FR-012)。

## 重定向契约 (Redirects) — `frontend/next.config.ts`

| source | destination | permanent | 状态码 |
|--------|-------------|:--:|:--:|
| `/extraction` | `/entities/extraction` | true | 308 |
| `/reasoning` | `/analysis` | true | 308 |
| `/knowledge-graph` | `/analysis` | true | 308 |
| `/` | `/overview` | true | 308 |

**验收**:对每个 source 发起导航/直接命中,MUST 解析到 destination,MUST NOT 返回 404(SC-005、FR-011)。

## 高亮判据 (Active State)

| 当前 pathname | 高亮项 |
|---------------|--------|
| `/overview` | 总览 |
| `/ontology` | 本体工作台 |
| `/entities` | 图谱管理(组)+ 实体管理 + 子Tab"实体浏览" |
| `/entities/extraction` | 图谱管理(组)+ 实体管理 + 子Tab"文档抽取" |
| `/analysis` | 图谱管理(组)+ 应用分析 |
| `/integration` | 事实源 |
| `/approvals` | 审批中心 |
