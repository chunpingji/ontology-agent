# Quickstart: 019 统一证据与通用语义抽取

本指南区分工程契约验证和生产发布验收。默认不开启云端或下载模型。

当前结构、语义抽取及审核/提交的实际结果见 [validation.md](./validation.md)。完整特性仍在实施，尚不能作为发布完成状态。新模板样例分析快照需要先应用 Alembic `0025_template_evidence`；候选、审核、提交和快照需要随后应用 `0026_evidence_facts`。历史模板没有快照时提示重新分析，不猜测来源。

2026-09-05 13:23 UTC：经用户明确授权，本机 Compose 已完成备份、0025/0026 迁移、前后端镜像重建及服务启动，供手工测试。入口仍为服务器 8081 端口，目标模板保持 published v18，未发布隔离 v19 草稿；部署成功不代表模板金标或完整特性通过。备份与只读接口检查记录见 validation.md 的“授权 Compose 部署”小节。

13:43 UTC：修复旧模板首次打开时补建源作业的外键写入顺序并仅重启后端；目标详情接口连续两次 HTTP 200。用户本机 8082 转发到上述服务器 8081，可继续在原地址测试。首次打开已创建默认源作业并启动后台解析，重复打开复用该作业；模板定义仍为原 published v18，未自动审核或提交事实。详见 validation.md 的“手测阻塞修复”小节。

14:06 UTC：已部署正文预览与模型抽取分离、重复段落样式查找优化及读取请求取消。Word `annotated-document` 缓存失效时只解析正文，`refresh=true` 也不启动模型；语义识别由证据面板的显式抽取或已有后台任务执行，正文出现不代表抽取完成。报告的四个接口实测全部 HTTP 200，缓存正文约 0.39 秒，强制解析约 9.31 秒；请刷新原 8082 页面加载新版前端。默认源作业已保存断点并在后端重启后恢复，未自动审核或提交事实。

15:20 UTC：报告中心“重新识别”已改为等待后台 SSE 的 `complete/failed/paused` 后再刷新，运行期间不再用被清空的缓存误报“未抽取到关系”。历史上传作业会从其文档影子记录恢复权威 `doc_class_iri/doc_ref`；目标作业已恢复为 `CMCReport`。当前部署使用 `fair-subject-predicate-v2-relationship-first` 和每任务最多 32 个区域（仍受 32768 精确 token 预算拆分）。真实目标文档在 17 个任务后检查点暂停，当前缓存展示 1 条 passed 关系及其 passed 属性 `注册分类=新分子`；页面会标为未完成的部分结果。候选仍为 pending review，未审核、提交或发布快照，不能据此生成正式金标报告。

## 本轮可验证的审核链路

Word 作业的文档抽屉已增加“逐值证据审核与提交”：填写理由，逐项确认，再选择已确认断言显式提交；提交失败可以按原不可变清单重试。实体归并限定同作业、同类型、已确认目标，修改/归并使依赖重新待审。值来源和绑定依据分别显示，文档锚点可定位至原文，外部/人工来源显示保留记录。旧关系图仅是预览。

`POST /api/extraction/jobs/{id}/evidence/extract` 调用共享 IR 和通用 runner，结果入候选库，不自动审核或提交。`GET .../evidence/snapshot` 只返回发布快照，`GET /api/extraction/evidence/assertions/{id}/provenance` 回读发布断言来源。所有写接口要求 senior_analyst；旧字典候选的确认即提交入口返回 `409 legacy_unverified`，不能把它当成实例写入成功。

本地语义抽取需 `LOCAL_LLM_ENABLED=true`、固定 `LOCAL_LLM_MODEL_REVISION`、配套本地 `LOCAL_LLM_TOKENIZER_PATH`（tokenizer.json）以及已有本地模型服务；离线环境预先交付 `llm` 可选依赖和权重，运行期不下载。缺配置时结构可用，语义任务明确 incomplete。独立 World 写入 `EVIDENCE_WORLD_DIR`，目录和 SQL 库需要一起备份；不可单独把 staging/commit World 挂入公共图。

外部记录目前提供明确标为 `mock_equipment/equipment_archive` 的版本化适配器；不是已对接真实设备主数据服务。其余来源仍需接入。正式 report/coverage 已改用发布快照和 FactSelector；无发布快照时报告返回 422，覆盖显示未满足。历史报告仍可读取。旧预览富化及其他退役任务未完成，生产发布门禁尚未通过。

## 风险评估模板专项

目标为 `dea037a2-f4b5-478a-8e0e-d5471fc45cbc`，输入 CMCReport。`risk-template-upgrade.json` 固定原 v18 的 schema 哈希，生成 v19 草稿；原 v18 保持不变。主要章节 12 项覆盖、14 个明确快照插槽及 7 个冻结规则列已验证，但洁净级别值域缺失和输出报告会签仍明确待补。

模板源文档页可使用逐值证据面板：审核→提交→查看实例覆盖；`all`/最大基数需要授权人员在完成源处理后逐项确认对象集合。补抽需当前 manifest_id 与理由，最多两轮，同输入不重推理；新候选返回待审。正式生成要求已发布事实快照及已发布模板，返回 202 后可读取/下载该次冻结报告。

GGUF 服务可显式设置 `LOCAL_LLM_TOKENIZER_BACKEND=llama_server`、`LOCAL_LLM_SERVER_MODEL_PATH` 和实际制品 SHA256 `LOCAL_LLM_MODEL_REVISION`；会验证模型路径并调用精确 `/tokenize`。文件 tokenizer 方式仍为默认。两种方式均不允许字符长度估算 token 或自动下载。

`scripts/run_template_acceptance.py --help` 提供隔离诊断参数。真实源/样例副本、临时目录、模型 URL/路径/实测版本都必须指向已有本地资产。`--reuse-run` 只重用同身份记录，`--review-file` 只接受显式 actor、源/模型身份及逐条 revision/decision/reason；不自动确认任何事实。当前脚本还未完成模板审批/规则导入/最终报告阶段，失败验收返回 exit 2。运行结果与具体阻塞见 [validation.md](./validation.md)，不能把有 DOCX 工程测试误称为该模板真实源验收通过。

分段诊断建议保持 `--max-tasks` 不变并使用 `--pause-after 8`：下一次同参数运行恢复成功和失败状态，继续未尝试窗口；失败诊断不会消失。`--retry-failed` 才显式重试失败项，总 attempt_count/model_calls 不重置；达到原预算仍停止。取消 pause-after 可继续调度至总预算上限。模型提示/策略、tokenizer、输入类和预算变化均会使旧 checkpoint 失效，`--reuse-run` 也不会绕过这些检查。

当前本地模型使用封闭短编号协议和可见的枚举输出契约，编号只能还原为已登记的原始身份，精简上下文不会替代完整证据校验。`--capture-model-responses` 可在隔离工作目录的 `model-calls/` 保存请求、响应和映射，用于本地失败回放；其中包含保密原文，默认关闭，不得提交仓库或上传外部服务。

当前调度为 `fair-subject-predicate-v2-relationship-first`、传输为 `closed-model-references-v2`。实体召回结束后按主体/谓词轮转；对已有候选对象的关系先于同主体首个数据属性任务，空关系任务自动落到属性，不改变独立绑定校验。关系对象默认每批最多 8 个，可用 `EVIDENCE_MAX_OBJECTS_PER_TASK` 配置（1—128）；文档区域当前每批最多 32 个。对象/区域批次过大时按精确上下文 token 再拆分，不省略竞争主体或源窗口。属性/关系输出均要求显式极性，关系必须指定非空对象和证据，但允许空结果和拒答。策略或预算变化使旧 checkpoint 失效；不要覆盖原验收目录后混算前后两种策略的结果。当前仍未实现模板声明优先级。

证据面板提供“继续未处理任务”和“重试失败任务”，每批最多 8 项，重试必须填写理由，仍需等待该批同步完成。Web 在批次结束时保存状态，批次内进程崩溃/并发预约尚未闭合；不要把脚本逐任务落盘测试当作 Web 全生命周期保障。新按钮和 API 需新代码及 0025/0026 迁移；本机已按上述授权记录部署，其他环境仍需独立执行迁移。

模型可显式选择逐字引用定位，只有指定 evidence/允许范围内唯一完全匹配才会生成原文坐标。重复/改写/越界引用或模型主动给出的错误坐标会拒绝，不回退模糊匹配；该模式不会自动确认断言，也不跳过属性/关系的独立绑定验证。

## 工程验证

在 backend 运行定向 pytest（任务实现后执行对应测试文件）：

```bash
.venv/bin/pytest -p no:cacheprovider -q tests/test_extraction/test_document_ir.py tests/test_extraction/test_evidence_contracts.py tests/test_extraction/test_semantic_binding.py tests/test_api/test_evidence_api.py tests/test_reporting/test_fact_selector.py
```

测试应覆盖：相同文档三个入口、嵌套/合并与偏移、标题主体/正文值、同段多主体、否定/条件可提交、乐观并发、幂等/失败恢复、真实 World 回读、实例覆盖及模型关闭。

前端在 frontend 运行 npm run lint、npx tsc --noEmit 和 npm run build。手动验证模板来源定位、换样例失效、逐值来源/断言极性、审核与提交分离、失败重试及实例缺口。

## 端到端

1. 上传含主体标题和规格正文、同段两个主体、嵌套表的 Word，在分析/模板/抽取中核对同一结构身份。
2. 使用已配置本地模型抽取；关闭模型时应返回结构与 incomplete，不运行旧 finder。
3. 审核独立节点和属性/关系候选；故意提交过期 revision，应 409。
4. 用同一幂等键提交两次；查询相同快照且不重复事实。注入 writer 失败，确认报告不可见；恢复后 retry。
5. 提交可信否定/条件断言；来源可回放，无无条件正向边，匹配范围否定可支持 confirmed_absent。
6. A 完整/B 缺失和错谓词样本中 B 缺口保留；补抽有界。报告引用相同 snapshot/selector。
7. 运行 scripts/audit_extraction_rules.py；审计最终自有代码、配置、动态加载和生产产物，残留不得当成已退役。

## 生产验收

按设计 §17 提交独立人工金标、隔离划分、固定模型/策略、消融、未知类型/版式及 p95/内存结果。运行 scripts/evaluate_extraction.py 时缺少真实性证据必须不通过发布 gate。不能把本指南中的桩测试通过作为真实模型效果证明。
