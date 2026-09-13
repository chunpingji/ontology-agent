# 低分剪枝试运行启用记录

日期：2026-09-11（UTC）。用户进一步指令：“请开启低分剪枝”。

已将本机新运行的模式设为 `trial` 并重新部署后端及前端，入口为
http://localhost:8081 。此模式实际执行低分软剪枝，明确标为待专家校准；
`enforce` 的已审核校准门仍保留。本次不是正式质量验收。

## 生效范围

| 项目 | 实际配置 |
| --- | --- |
| 模式 | `DOCUMENT_ANALYSIS_ADAPTIVE_RETRIEVAL_MODE=trial`，本机 `.env` 持久覆盖 |
| 阈值文件 | `/app/models/semantic-ranking/pruning-trial-20260911-v1/profile.json` |
| 质量状态 | `development`；关联专家审核文件为 `pending`，无签字 |
| 精排后淘汰条件 | discover 与 counterevidence 两个原始 logit 都严格小于 -8，视图完整且无证据保护 |
| dense/self 前门 | 不因低分淘汰；先完成精排 |
| 结构组保护 | 互补多记录组正分仍保护；单记录组不因其自身向量正分额外保护 |
| 谓词范围 | 当前报告冻结本体的 205 个明确关系/属性 IRI，未覆盖谓词不剪枝 |
| 公开诊断 | `pruning_quality=unvalidated`，页面展示“剪枝试运行，待专家校准” |

保留原文命中、必要上下文、否定/条件、不可精排视图保护，以及完整 epoch、
付费审计、精确准入、覆盖恒等式和后续重激活。软剪枝仍是 `unattempted` 子集。
更激进的试运行精排阈值、提高组保护门、非 raw-logit 分数和模型/视图失配被拒绝。
其他模式省略新增质量字段，保留既有快照形状。

宿主机阈值及其依据保存在 `backend/models/semantic-ranking/pruning-trial-20260911-v1/`，
由现有模型目录卷提供给容器，未打包进镜像。目录含 `profile.json`、
`sample-manifest.json`、`expert-review-pending.json` 和 `model-smoke.json`。
样本及待审核文件使用实际内容 hash；没有伪造专家身份或 validated 状态。

阈值制品 hash：`c73d0cbdafc088d16e9e06f0b5433dd6428476251880e35b749ef58b5d390607`。

## 验证与部署

1. 本机 `bge-reranker-v2-m3` 使用实际 CUDA/FP16 配置完成 12 对合成短句评分，
   含加载共 70.84 秒。三个明显无关文本的双意图分数约 -11.04～-11.02，均低于 -8；
   产品、否定及条件样例至少一意图高于 -8，均保留。该检查使用短句而非报告增强视图，
   仅证明分数尺度和初始规则有作用，不能据此判断真实报告 recall 或误剪率。
2. 内核自适应、公共投影和请求回执三个模块：48 passed（20.76 秒）；配置冻结和
   API 投影模块：6 passed（3.87 秒）。包含 trial 真正剪枝、无 dense 前置淘汰、
   全局待校准投影、覆盖守恒、恢复及保留 enforce 校准门等反例。
3. 变更 Python 文件 Ruff、前端 TypeScript 类型检查、定向 ESLint、Compose
   配置和 diff 检查通过。首次脚本超过模型每批 4 条限制，以及 stdin 入口无法启动
   CUDA 探测子进程的问题均已修正后重新运行；这些失败不作为模型评分证据。
4. 通过现有运行控制服务暂停活动运行，再执行：

   ```bash
   docker compose up -d --no-deps --no-build --pull never --timeout 60 backend
   docker compose restart --no-deps --timeout 60 frontend
   ```

5. 不传临时模式覆盖的容器内核验确认实际 `trial`、v4、阈值制品与实际模型身份
   一致；数据库 revision 与代码 head 均为 `0035_ranking_budget_control`。
   暂停任务的全部状态、制品头和数量在部署前后严格一致，模型请求数保持 5147。
   模型分数冒烟的新增调度记录发生于该比较基线之前，未隐瞒其费用。
6. 8081 健康接口、登录页、报告页和 OpenAPI 返回 200，schema 含待校准标记；
   未认证图谱请求返回 401。源文件 hash 与工作区一致。

本轮前发现旧 HRS-1597 运行已被用户取消，另有运行
`d485c968-1bbd-43d3-8809-e61158765fc4` 正在执行。本次只对这个活动运行请求
暂停/恢复，未复活取消任务。已有运行沿用自身冻结策略，不自动迁移到 trial；
如需使用剪枝，应创建新的分析运行。
部署后确认该活动运行已恢复为 `running`，运行指纹与部署前一致。

私有部署基线、日志及核验脚本位于 `/tmp/ontology-pruning-deploy-20260911/`。
独立专家校准、未暴露文档质量及真实全文性能验收仍待完成，未触发事实提交或本体变更。

## 回退

将本机 `.env` 中的 `DOCUMENT_ANALYSIS_ADAPTIVE_RETRIEVAL_MODE` 改为 `enhanced`，
重新创建后端容器，新运行仍使用增强视图但不低分剪枝。旧运行保持冻结政策。
阈值文件是审计依据，回退时保留；`enforce` 仅在完成真正的校准审核后单独启用。
