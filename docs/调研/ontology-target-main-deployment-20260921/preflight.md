# 图谱分析主服务部署准备（2026-09-21）

前四节保留主服务部署前检查事实；预检阶段未重启服务、未替换镜像、未启动模型任务。
主服务随后已完成部署，实际时间与结果补记在末节。部署完成与真实识别验收分开记录。

## 部署前服务现状与最小切换

- 主入口：`http://127.0.0.1:8081`，Nginx 将 `/api/` 转发至后端，其余转发至前端。
- 前端容器：`ontology-agent-frontend-1`，Node.js `22.23.1`、Next.js `16.2.9`、React `19.2.7`。
  当前以 `next dev` 运行，宿主 `frontend/` 挂载至 `/app`，`node_modules` 与 `.next`
  使用独立匿名卷。源码已包含默认可见的“图谱分析”Tab；无功能关闭开关。
- 后端容器：`ontology-agent-backend-1`，镜像 `ontology-agent-backend:cuda12-2.7.1`。
  `backend/app/` 与 `backend/alembic/` 为挂载目录，Uvicorn 未启用 reload。
  本次依赖未变，无需重建 CUDA 镜像。
- 本机 Compose 由 `docker-compose.yml` 与 `docker-compose.override.yml` 合并。
  本次保留现有运行方式，不顺带切换前端生产镜像或整栈重建。

本阶段配置修改后，三个模型超时值属于容器环境变量，需要重新创建后端容器，
仅 `docker restart` 不会刷新这些环境变量。以下命令留待全部回归通过后执行：

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d --no-deps --force-recreate backend
```

该操作也会停止同一后端容器中的隔离实证 API（8011）；新实证统一使用主入口。
不执行 `down`、卷清理、整栈 `redeploy.sh` 或依赖服务重建。
如果重建后端导致容器 IP 变化，需验证 Nginx 仍能路由到新容器；有需要时由主任务
在验证配置后 reload Nginx。

## 配置变更

仅修改以下运行配置，原文件其余行与本体识别选项均已逐项或按字节摘要核对保留：

| 路径 | 配置键 | 变更 |
|---|---|---|
| `.env` | `LOCAL_LLM_TOTAL_TIMEOUT_S` | 新增 `900`，原容器采用默认 `600` |
| `.env` | `EVIDENCE_TIMEOUT_S` | 新增 `900`，原容器采用默认 `600` |
| `.env` | `EVIDENCE_TOTAL_TIMEOUT_S` | 新增 `900`，原容器采用默认 `600` |
| `backend/data/ontology-tool-options.env` | `ONTOLOGY_EXTRACTION_OPTIONS.external_sources` | 删除该 JSON 键，关闭新运行的外部模拟资料 |
| `backend/data/ontology-tool-options.env` | `DOCUMENT_ANALYSIS_NO_PROGRESS_TIMEOUT_SECONDS` | 新增 `1200` |

删除前外部模拟源包含 `system-mock-equipment` 211 条、`system-mock-roles` 5 条。
`profile`、`responses`、`gliner2`、`vocabulary_overlay` 保持不变，未改写已有运行冻结配置。
后端容器环境未设置 `ONTOLOGY_EXTRACTION_OPTIONS` 覆盖，因此使用只读挂载的 `/app/.env`。
编辑保留宿主配置文件 inode，确保文件挂载指向当前内容。

已核对 Compose 合成配置中的三个超时均为 `900`；容器中新建的 Python 进程读取到
外部模拟资料关闭、无进展超时 `1200`、词汇覆盖与 GLiNER2 配置保留。
这不代表已经运行的 Uvicorn 进程完成刷新。
配置差异证明见 [config-change-proof.json](config-change-proof.json)，不包含完整配置或凭据。

可逆备份仅保留于宿主 `/tmp`，权限均为 `0600`，不加入仓库：

- 删除外部资料前：`/tmp/ontology-tool-options-before-external-sources-bufaxbg6.env`
- 根配置修改前：`/tmp/ontology-timeout-config-before-6g_dn68q.env`
- 添加无进展超时前：`/tmp/ontology-timeout-config-before-hr72hpcq.env`

## 部署前已完成检查

- 只读数据库查询：文档 `queued/running` 为 0，有效租约下模型 `queued/running` 为 0。
  保留的运行包括 paused 22、blocked_dependency 3、cancelled 8、failed 1。
  执行切换前应再次检查，不能将此快照当作之后仍无任务的保证。
- 数据库 revision 为 `0041_template_engine`，容器代码 `alembic heads` 同为
  `0041_template_engine (head)`。
- `node --test tests/target-graph.test.mjs`：10 项通过。
- `./node_modules/.bin/tsc --noEmit`：通过。
- 定向 ESLint：`analysis-tabs.tsx`、`graph-analysis-panel.tsx`、
  `target-graph-canvas.tsx`、`lib/target-graph.ts`、`lib/api.ts` 全部通过。
- 主入口只读 API：健康接口 200、既有运行详情 200、目标图接口 404。
  404 符合后端尚未重新加载新路由的当前状态。
- 主服务真实浏览器：新 Tab 可见且选中，读取和刷新未产生 POST，零页面异常。
  运行详情、报告列表、原文接口均为 200，目标图首次与刷新均为预期的 404。
  见 [浏览器检查](browser-before-recreate.json) 与 [页面截图](browser-before-recreate.png)。
- 本次没有执行生产构建：主服务保持已核验的 dev 挂载模式，未交付生产镜像；
  不在运行中的 `.next` 卷执行 build，避免影响当前前端。

浏览器采用既有允许域名 `http://sldpr-demo.infilake.com:8081`，通过 Chromium 参数
`--host-resolver-rules=MAP sldpr-demo.infilake.com 127.0.0.1` 指向本机，并设置
`--no-proxy-server` 绕过宿主 Privoxy，所有请求直接进入主服务。
直接使用 `127.0.0.1` 会遭现有 Next.js 开发 origin 规则阻止 HMR；允许域名若未关闭
系统代理则会进入 Privoxy 并返回 503。以上只改变浏览器验证启动参数，未修改服务配置。

可复用检查脚本：`/tmp/ontology-target-deploy-preflight-20260921/browser-main-readonly.mjs`。
切换后设置 `VALIDATION_RUN_ID=<新运行>`、`EXPECTED_TARGET_STATUS=200` 再执行，
将严格要求目标图首次与刷新均为 200，并检查图谱与实体选择器出现。

## 切换后验收清单（原计划）

1. 重查在途任务，执行仅后端的容器重建；核对启动日志、实际配置和数据库 revision。
   健康接口 200 不能单独证明迁移成功。
2. 从主入口请求 `/api/document-analysis/runs/{id}/target-graph`，确认 200，
   以及页面 `/analysis?tab=graph-analysis` 默认展示新 Tab。
3. 刷新页面、选择报告和查看历史只产生 GET；点击开始或继续才允许模型执行。
4. 用当前实现新建 CMCReport 运行，核对冻结配置没有 `external_sources`，
   输出预算及三个超时与本次约定一致；不改写旧运行冻结输入。
5. 在主服务验证虚线目标、实证后的实线、关系/属性完整度、孤立实体属性访问和原文定位。
   未完成目标保持未完成，处理结束不能替代识别完整度。

## 实际部署后结果（2026-09-21 UTC）

| 时间 | 已核验事实 |
|---|---|
| 04:22:42 | 主后端容器重新创建并启动；容器 `StartedAt` 为 `2026-09-21T04:22:42.543582872Z`。前端继续使用既有 dev 挂载模式。 |
| 04:24:18 | 从主服务新建 CMCReport 运行 `2abc7075-276d-41b6-b8a8-33f25af66465`；创建回执时间为 `2026-09-21T04:24:18.017730Z`。 |
| 04:26:54 | 主服务新运行的初始浏览器检查证据归档；目标图首次与刷新均 200，页面展示 12 条虚线、0 条实线。此时间为证据归档时间。 |
| 04:35 | Quote schema 修复后再次重新创建主后端；健康接口 200，数据库 revision 仍为 `0041_template_engine`，请求/无进展超时仍为 `900/1200` 秒。 |
| 04:35:50 | 从主接口创建修复后的新运行 `8b97fa57-e2a2-477e-b836-1e8094813dc0`；创建回执时间为 `2026-09-21T04:35:50.238279Z`，真实识别验收继续。 |
| 04:36:29 | Quote schema 离线证明归档：原真实请求的 schema 复现 grammar 转换失败，修复后转换成功，当前生成的 5 种 schema 也通过转换。该检查没有发出模型或 HTTP 请求。 |
| 05:02:04 | 第三次重新创建主后端，`StartedAt=2026-09-21T05:02:04.775088351Z`；健康 200，数据库 revision `0041_template_engine`，容器解析版本 9，通用类型证据提示长度 274 字符。 |
| 05:02:40 | 解析器源文件补充严格布尔边界：非法 `tblHeader` 值不允许推断无表头；最后修改时间 `05:02:40.564742037 UTC`。晚于容器启动，不据源码挂载推定运行进程已经加载。 |
| 05:02:51 | 主入口创建新运行 `20bf2d25-98f3-4efe-99f0-1e5dbbae5d05`，回执时间 `2026-09-21T05:02:51.797046Z`。 |
| 05:03:24 | 最终解析器原件重解析复核：结构 hash、analysis_id 与先前 Parser 9 scope 证明一致；全部 114 项解析/引用相关回归通过，无模型调用或运行修改。 |
| 05:04:00 | 新运行真实浏览器检查结果文件写入：目标图首次/刷新 200，12 虚线、0 实线，关系 0/12、属性 0/7；仅读取和刷新。 |

主部署操作已核对健康接口 200、真实进程配置及数据库迁移。数据库与代码 head
在部署前后均为 `0041_template_engine`；没有用健康接口代替迁移核验。
新运行发现输出上限为 `8192`、核验输出上限为 `16384`，三个请求超时为 `900` 秒，
无进展超时为 `1200` 秒，`external_sources` 关闭。
旧运行 `e3763123-4c23-4cbf-8276-19825b0782d5` 的目标图读取返回 200，
revision 保持 `106`；没有重写该运行或其冻结输入。

真实浏览器通过主入口读取和刷新新运行，没有产生 POST，所有观测 API 均 200，
`pageerror` 为 0。初始关系完整度为 `0/12`、属性为 `0/7`，均为 `0.0%`，
实证计数均为 0，待展开 12 项。详见[初始浏览器验收](browser-main-initial.md)。

**真实识别验收尚未完成。** 首次主运行 `2abc7075...` 的首个 discovery 请求约 `0.303` 秒
即失败，错误为 `model_request_failed`；离线复现确认 llama.cpp 的 schema 转换器不支持
Quote 中未锚定的 `pattern=\S`。修复移除模型 schema 的该 pattern、保留 `minLength=1`，
由本地字段校验器继续拒绝纯空白引用；67 项相关回归通过。修复后的运行进程也已确认当前
Quote schema 无 pattern 且 `minLength=1`。

该离线检查本身只证明工程修复及已安装 Python 转换器接受 schema。随后 `8b97fa57...`
确已通过真实模型提供方并验证暂停继续，但仍暴露类型误判，04:51 UTC 再次安全暂停；
问题结果保留，不能当作实体、属性、关系和完整路径已通过验收。
首个失败的调用和覆盖记录保存在[失败证据](first-discovery-failure-2abc7075.json)，
修复证明见 [quote-schema-proof.json](quote-schema-proof.json)；后续结果不覆盖原失败。

当前运行改为 `20bf2d25...`。第三次部署确认了 Parser 9 及通用类型提示，
但 `05:02:40` 的最后布尔处理收紧晚于运行进程启动；它对本次原件的重解析 hash 与范围
无影响，不因此声称进程已加载所有最终源码。后续加载或安全边界处理由主任务另记。
新运行初始浏览器验收见 [browser-main-parser9.md](browser-main-parser9.md)，
解析范围变化见 [parser-header-scope-proof.json](parser-header-scope-proof.json)。
固定目标类型探针及新运行真实识别尚未通过，服务部署与初始展示不能替代质量验收。

## 最终解析边界加载（05:09 UTC）

当前运行 `20bf2d25...` 在 2 个已确认调用后安全暂停（revision 78）。
部署前只读数据库检查：queued/running/pausing 运行 0，有效 queued/running 模型请求 0。
后端于 `2026-09-21T05:09:53.654260102Z` 重新创建；健康接口 200、模块 10、
Parser 9、数据库 `0041_template_engine`。最终解析源码 SHA256 为
`6c431f42eee6786d0f250c3b0155f028b0d8e1111530146c0452c95c2f30486a`。
公开 resume 已接受（revision 80）；新区域发现开始，原两个确认调用未重发。
此后才能确认主服务已加载最后布尔边界收紧。

## 缺失实体指称补修加载（05:42 UTC）

预算暂停点 revision333/event24，34 次模型调用全确认。部署前活动文档运行、
有效模型请求与标注执行均为 0；旧 pending 作业创建于 2026-09-14 且无标注执行，未修改。
后端 StartedAt=`2026-09-21T05:42:11.45484107Z`；健康 200、Parser 9、
数据库 `0041_template_engine`。claim_freeze.py SHA256=
`ca2fd464e40a53f2be249670c76bf1566d79c5ca926e5b713dc7d9ec020ee25b`。
随后公开继续当前运行。原N/A失败结果仍保留；补修独立验证见[修复说明](missing-entity-fix.md)。

## 06:08:38 UTC 来源角色和精确核验Schema上线

- 06:08:30 UTC：active_runs=[]、有效模型请求0、有效标注执行0。
- 同一已授权Compose命令仅重新创建backend，未停止数据库/前端或删除卷。
- 容器StartedAt为2026-09-21T06:08:38.480314194Z；健康API200/modules_loaded=true。
- alembic current与heads均0041_template_engine。源码hash及回归见
  [来源角色修复](endpoint-source-fix.md)；完整Schema提供方证明见
  [结构验收](exact-verification-schema-review.md)。
- 06:09:42 UTC 创建新运行3d563507-90da-495c-998e-62cb83244521，旧v3未恢复。
