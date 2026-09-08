# Specification Quality Checklist: 本体指引的文档结构与摘要关系图谱识别

**Purpose**: 验证 021 规范是否完整、无实现泄漏并忠实覆盖设计；本清单不代表工程、真实模型、治理或发布门禁已经通过。

**Created**: 2026-09-08

**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] CHK001 规范以用户旅程、可观察行为、状态和验收结果为中心，模块、数据库表、框架及代码路径留给 plan/data-model/contracts。
- [x] CHK002 五个用户故事均有优先级、业务价值、独立测试和 Given/When/Then 场景。
- [x] CHK003 输入来源、状态、范围、非目标和直接替换决定已经明确，不含模板占位符或未解释的“应该”。
- [x] CHK004 术语区分运行、解析、元数据、图、候选、证明、审核和业务事实，没有把模型验证等同专家确认。
- [x] CHK005 成功标准可测量，并把工程机制、真实语义质量、性能观测、治理和发布资格分开。

## Requirement Completeness

- [x] CHK006 显式文件/根类型提交、无自动运行、幂等、新运行不可重标、默认 document_graph 和两个产物均有要求。
- [x] CHK007 Word IR、记录索引、冻结 MetadataSnapshot、摘要三种模式、降级标识和摘要非事实边界均有要求。
- [x] CHK008 OntologySnapshot、直接 LocalMenu、继承/range 约束、未知 IRI、未解析约束和禁止预展开二跳均有要求。
- [x] CHK009 两阶段检索、真实拒绝续检、公平调度、上下文闭包、预算超限和 RecallLedger 守恒均有要求。
- [x] CHK010 mention、局部指称、类型解释、身份、字段角色、值存在和极性均为独立语义，未被总布尔值合并。
- [x] CHK011 VerificationTarget、四态 Decision、PredicateEvidence、允许/禁止桥接、精确引用与独立语义核验均有要求。
- [x] CHK012 归并/拆分、竞争 owner、AND/OR 证明依赖、RootSeedPolicy、真实 revision 和完整图水位均有要求。
- [x] CHK013 状态维度、completion 语义、fingerprint、checkpoint、唯一租约、pause/resume/cancel/delete、保留和晚到 worker 均有要求。
- [x] CHK014 唯一新 API 的创建、查询、产物、source、SSE、控制、删除、契约版本、只读 GET 和授权边界均有要求。
- [x] CHK015 页面恰好两个结果 Tab、现有元数据完整收纳、图筛选/详情/覆盖、跨 Tab 来源定位、URL 恢复和迟到响应隔离均有要求。
- [x] CHK016 旧入口/runner/断点退役、不导入旧结果、保护非 Word 业务与共享资源、保留离线证据均有要求。
- [x] CHK017 T01—T31 均在规范内保留稳定编号和对应断言，tasks 与验证报告可逐项追踪。
- [x] CHK018 独立人工标注、关键正反例、至少三次运行、开发/保留集隔离、指标边界及未批准 SLO 的发布阻断均有要求。
- [x] CHK029 AC-T31 已明确路线/步骤到最终产品/API 的 union domain/range、中间体字符串标识、产品/API 到中间体的 0..* 非 functional 可推导关系及两条属性链；顺序只认 `stepOrder`/`nextStep`，无 reasoner 时不冒充物化事实，匿名 union/属性链及 RDF list 必须往返保真。

## Prior-Spec and Constitution Consistency

- [x] CHK019 018 的可复用结构/摘要/定位能力与被替换的同步、自动上传、页面内存和无图契约已逐项说明。
- [x] CHK020 017 的可复用 Word/声明式能力与被替换的无效类型自动回退、旧 Word runner 及本体改动边界已说明；只允许 FR-087—FR-092 经批准的有限 CMC 权威扩展，其他本体专用改动仍禁止。
- [x] CHK021 019 的可复用 IR/引用/执行能力与本工具不接 CandidateStore、审核、提交和中央事实库的边界已说明。
- [x] CHK022 Constitution I、II、IV、V、VI 的规范驱动、本体保真、契约/测试优先、复用和离线要求均可在后续 plan 中形成明确检查项。
- [x] CHK023 Constitution III 与设计中旧已发布数据物理删除的冲突已显式暴露，没有在规范中虚假声明合规通过。
- [x] CHK024 当前宪章下已发布内容采用追加撤销/失效/隔离并保留历史；物理删除被设置为治理前置条件。

## Safety and Release Boundaries

- [x] CHK025 清理必须先生成只读 manifest、确认独占/共享归属、撤销旧写入和租约，再在维护窗口按精确目标执行。
- [x] CHK026 未知归属、跨业务引用、全局审计、共享本体/原文/模型/配置和离线评测证据均列入保护边界。
- [x] CHK027 没有把桩测试、旧银标、图连通、全部拒绝或单次模型运行描述为真实质量完成。
- [x] CHK028 严重语义、版本、权限或事实泄漏的切后行为是停止接单、保留审计并修复唯一实现，不是恢复旧实现或篡改历史。

## External Gates — Not Specification Defects

以下项目故意保持未完成；它们不阻止设计与实现继续，但对应动作不得被标记完成：

- [ ] GATE001 在物理删除任何已确认、已提交或已发布内容前，取得 Constitution III 修订或正式治理批准；否则只执行追加撤销、失效和在线隔离。
- [ ] GATE002 冻结独立人工标注包、保留集、批准的质量 SLO 及评分协议，并完成至少三个独立真实运行。
- [ ] GATE003 完成旧域只读 manifest、共享引用闭包、保护对象基线和维护窗口复核。
- [ ] GATE004 T01—T31、权威 CMC TTL/schema/local menu/往返保真、数据库/API/前端集成、权限、清理演练和真实语义正反例全部通过。

## Notes

- `[x]` 表示规范已明确表达该要求，不表示实现已经存在。
- `GATE001—GATE004` 由后续 tasks/validation 跟踪；未满足时可以完成非破坏性工程工作，但不得物理清理受保护历史或正式切换。
