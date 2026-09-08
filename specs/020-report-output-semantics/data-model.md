# Data model

TemplateV2 固定 family/revision/schema_hash/source_slots；definitions 注册 Binding/Input，Section/Group 组织 OutputUnit。TypeSpec 为封闭递归类型，Projection/Render/表达式严格判别。编译固定契约、类型、DAG、Requirements、消费路径和诊断。

ContractRevision 固定 ontology/parameter/workflow/rule/condition/claim/policy/style 语义、hash和审核；SourceBundle 固定模板/发布快照/根/参数/工作流/发现审核版本/适用时间/预算。请求只能引用服务端记录。

ResolvedValue 逐字段/items 保存 state/issues/value/type/subject/fact/provenance/derivation；优先级 invalid>conflict>unavailable>pending_review>incomplete>missing，不把字段 absence 提升为整行。UseResolution 保存消费路径、祖先约束和强制要求。ReportInputSnapshot 固定条件/规则/声明/Coverage/blocking_issue_refs。

ReportRun 的 source_bundle/request_hash 冻结，phase/三轴状态可变。ReportOutputResult 唯一(run,output,scope,attempt)，完成后不可变。ReportArtifact 固定 AST/style/bytes hash 与目的，临时写入、校验后原子登记。

ReportContentVersion 固定输入/attempt/body AST/区域/策略；ContentReview 精确绑定 content_hash。SigningSession open→finalizing→sealed/cancelled，revision CAS；Signature/Event 追加。SignatureSnapshot 冻结指定日志与角色/审核/工作流。Envelope 引原 body AST 加独立签署子树，final_ast/artifact hashes 不回参与 content_hash。新正文不继承旧签名。

TemplateMigrationPlan/RuleMigrationPlan 固定旧 payload/hash、逐项映射/问题/差异。旧 published 不覆盖，修正新 draft。AstTemplate 增 family/revision/schema_version/hash；GeneratedReport 仅投影 run/artifact 引用。所有表经 Alembic，唯一/外键约束及冻结对象不可变守卫。
