# Data Model

## 证据

DocumentIR 包含规范文件/原文件身份、parser/结构策略版本、nodes、blocks、recursive tables、evidence units、pagination、structure hash 和 diagnostics。段落/真实单元格原文不做破坏性规范化。Anchor 使用 Unicode 码点半开区间，文档/parser/structure 身份必校验。OffsetMap 禁止跨合成分隔符生成原文 span。

内部模型 `SpanProposal` 支持显式坐标和逐字引用两种模式。引用模式须同时省略或置空 start/end；服务端仅在指定 evidence 的允许范围内唯一完全匹配时生成非空 Unicode Anchor。重复引用、范围外引用、空引用或改写文本拒绝，无模糊匹配、空白归一化或最近实体选择。模型提供坐标时严格回放，不因引用可找到而纠正错误坐标。两种模式最终产生相同的完整持久化 Anchor；引用定位不能替代独立语义绑定或人工审核。当前执行版本为 `generic-semantic-v6`。

本地结构化模型使用 `closed-model-references-v2`：每次请求内的 e/t/c 短编号一一对应原始证据、菜单类型和候选身份，输出 schema 的 enum 限定已登记编号。关系提案要求非空对象编号及至少一个证据片段，属性提案要求值引用；两者均要求显式极性，服务端拒绝缺失的必填字段，不用默认肯定补齐。关系独立绑定的对象限定为当前被验证提案的对象。空断言列表/拒答仍合法，不强迫输出事实。编号还原只查本次映射表，未知或直接输出完整 ID 均拒绝，不做近似匹配；原文、字面量和断言极性不参与字符串替换。完整 IR/候选/schema/版本仍留在服务端上下文和缓存身份中；前后端持久化契约不使用临时短编号。

## 任务

SubjectRef 包含 candidate_id/revision/class_iri/证据及身份。Scope 保存主体版本、有序证据集合、排除区间、构建证据和扩展历史。ContextEnvelope 的查找身份包含目标、主体/竞争主体、scope、有效分类、本体、预算、tokenizer、模型和策略，最终 context hash 用于回放。

初始实体阶段之后使用 `fair-subject-predicate-v1`：主体流轮转，主体内部交替安排属性/关系谓词并轮转其证据窗口，已验证正向边的下游任务加入同一队列，保留路径依赖及跳数/环限制。关系对象按 `max_objects_per_task`（默认 8，1—128）分批；完整召回请求仍超出精确 token 预算时递归拆分对象或源窗口集合，不丢弃对象、竞争主体或证据。不可再分的超限任务仍显式失败。调度版本和对象预算进入 run identity，旧 checkpoint 不能冒充新策略；模板声明优先级及全文任务成本优化尚未完成。

Checkpoint 保存成功任务、失败任务及其已独立验证实体、累计 attempt_count/model_calls 和共享值联合验证结果。普通恢复保留失败诊断，先推进尚未尝试任务；retry_failed 才重新执行失败项，不重置总预算。部分实体引用失败时仅保留其通过完整校验的同批实体，任务仍 incomplete；不能把部分成功当成全文处理完成。pause_after 仅限制本次执行量，不改变输入身份。脚本通过 checkpoint 回调逐任务落盘；Web 当前在单批调用结束时持久化，批次内进程崩溃/并发预约仍需后续完善，不将其称为已完成的跨进程事务恢复。

## 候选

Candidate.kind 为 entity/property/relationship；属性一次只保存一个 LiteralValue。公共字段为 id/revision、class/predicate、subject/object refs、断言 polarity/modality/conditions、scope、provenance、binding、validation/review/commit 状态。
节点可以无属性独立存在。property 必须有主体；relationship 必须有两端、完整谓词和绑定证据。否定/条件可 validation passed，positive eligibility 独立计算。
provenance 为 document/external_record/manual/derived 可区分联合：分别引用原文、记录版本和字段、人工记录、推导公式及输入快照。外部身份匹配与值来源分开；非文档来源不伪造 Word scope。

## 持久化

- evidence_candidates：当前 revision 与状态、kind、job、稳定创建 key、payload；每个值独立一行。
- evidence_candidate_revisions：候选/version 唯一、不可变 payload 及状态证据。
- evidence_reviews：actor、expected_revision、decision、reason、timestamp。
- evidence_commits：job/idempotency key 唯一、内容 hash、不可变 items/端点映射、状态、尝试次数/错误/outbox 状态。
- evidence_assertions：稳定 assertion ID、commit、kind、两端/谓词/字面量、polarity/condition/provenance 与独立正向资格。
- evidence_snapshots：job、前一快照、已发布断言引用及版本；后续提交继承仍有效事实，历史不改写。作业快照头有 revision，发布使用 CAS；不同提交并发冲突时重新合并最新快照，最终包含两批有效事实。
- document_analyses：文件/结构/版本唯一及 IR 快照、角色。
- evidence_coverage：snapshot/template/discovery/selector 身份、实例 tasks、manifest、补抽历史。
- AstTemplate：sample analysis/hash/parser/IR/结构策略版本。
- GeneratedReport：snapshot/manifest/discovery/selector 引用；历史报告没有快照则保持只读存档。

## 状态

validation pending → passed/rejected/conflict；review pending → confirmed/rejected；commit not_requested → queued/applying → succeeded/failed。
编辑产生新 revision、重验并失效下游依赖。确认不提交，提交失败不发布。否定/条件断言成功提交不要求存在正向边。

## Coverage

目标类型声明展开到具体主体及对象集合。存在/全部、min/max、完整路径和对象范围显式保存。filled、missing、confirmed_absent、not_applicable、pending_review、conflict、incomplete 独立；开放集合不能满足 all/max 完备性。只有匹配范围的已发布否定支持 absent，否定 E 不推出不存在任何对象。
