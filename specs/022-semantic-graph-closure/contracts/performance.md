# 性能增量契约

日期：2026-09-10。继承 document-analysis-runs-v1 和 022 排序契约。

## 公共读取

- run.identities 增加可空 structure_snapshot_id / ranking_summary_id。graph_snapshot_id
  在图或覆盖批次更新时变化；状态/预算仍使用 run_revision。
- GET `/api/document-analysis/runs/{id}/ranking-summary` 返回既有 graph.ranking 的对象，
  含当前 budget_enabled；完整排序详情保持按需读取。
  可选 expected_summary_id（空串表示尚无摘要）和 expected_budget_enabled 绑定客户端
  缓存身份；与当前运行不符返回409 RUN_REVISION_CONFLICT，客户端同步run后重新取摘要。
- GET `/api/document-analysis/runs/{id}/source-selection?selection_ref=...` 返回
  contract_version、recognition_run_id、analysis_id、document_hash、structure_hash、selection、
  anchors，不返回全文。旧 source 端口兼容 content+selection，全文按文档结构身份缓存。
- 所有端口先 owner/删除/过期检查；图 ETag 绑定投影、图版本、摘要版本及运行可见状态，
  304 前同样鉴权。排序/控制不变图时刷新摘要和状态，不触发全图轮询。
- compact graph 保留原投影必要 GraphSnapshot、依赖失效、selection registry 和冻结菜单，
  不含排序向量、完整检索计划、队列。旧运行缺新制品只读走旧 reader，无隐式写入。

## 持久化与恢复

- 新大状态编码为 schema_version=2 的精确引用树；引用包含 artifact_id/content_hash/
  schema_version；大缓存条目、冻结计划、已完成轮次及重复状态块运行内不可变复用。
- 引用通过运行制品索引绑定运行，禁止跨运行读取；每条引用重验 hash 与 schema，
  遇到缺块、篡改、未知格式或范围漂移整体拒绝恢复。绝不查最新块代替引用。
- 保留全量v1 reader；新格式开关只决定后续运行写策略。全部块、图、候选、证明、
  检查点和batch receipt在同一事务提交；回滚不留下可见悬空引用。
- 运行引用未释放不得删除块；沿用运行清理索引统一回收，不另加无所有权缓存。
- 排序费用单调性校验与不可变块插入在同一执行写锁内；等待锁后重验租约，读取
  当前持久head再验账。同内容确认幂等，旧费用状态拒绝，回滚不残留块。

## 调度/模型

- 单个识别任务在途；工作线程提交 before_model 请求并等待协调器持久确认，线程
  不更新图、队列、费用或共享Session。提交失败/取消/失租唤醒所有等待者；结果返回
  后由协调器核对主体与依赖，再逐任务提交，暂停保存合法完成批次。
- 新 semantic-ranking-v2 的 discover/counterevidence 各基础1次并各有限技术重试；
  槽位记录总额2*(1+technical_retry_limit)，运行/槽位tokens限制照旧；v1冻结不变。
- exact count_tokens_batch 与逐文本相同，缓存绑定运行权限/模型/完整输入/特殊token；
  计量仍逐条累计，批量仅减少调度开销。
- scheduler.peek 不改计数或复制任务；逻辑前沿计入 pending/覆盖。任务身份、公平、
  探索与恢复顺序等价；模板增强策略另冻结，低分/尾部仍有实际执行机会。

## 回退与门禁

用户后续要求仅测试新方案，停止与原方案的性能提升对照，体感由用户主观评价。
已写新格式运行须保留兼容 reader/writer。真实 batch4/8/16、首池及并发实验分别登记；
未有硬件余量证据时默认单任务执行。性能/正确性回放不代替T017专家质量或T044真实模板。
