# Research: 统一证据与通用语义抽取

日期：2026-09-05。主代理已阅读本地 Spec Kit 技能和项目宪章，并按 plan 技能并行开展两项只读仓库研究。

## R1 统一解析

Decision：扩展现有 DocStructure，递归采集表格及真实源单元格，在 document_ir.py 序列化；三个 API 共享 word_analysis.py。
Rationale：当前 018 已提供块、树、分页。新增平行解析器会产生坐标分叉。
Alternatives：二次 python-docx 扫描只限样式；不允许重新推断章节或私有嵌套路径。

## R2 通用抽取

Decision：复用本地 chat_with_schema，经 Pydantic 严格验证模型结构、证据 ID、span、主体和断言极性；GLiNER 仅作经过验证的实体召回能力。
Rationale：当前 markerV0 的 GLiNER checkpoint 不支持现成主体关系绑定；BGE 仅能排序。
Alternatives：业务正则、Profile 执行规则、按类分派 finder 全部退役。模型不可用明确 incomplete。

## R3 候选和提交

Decision：独立候选行表达一个实体/属性值/关系，JSON 保留类型化 payload 和来源；不可变 revision、审核、提交项、断言和发布快照分开保存。
Rationale：extraction.py 的 _persist_ner_triples 压字典；_commit_candidate 提前 committed 且 project_entities 是 T-Box 投影，不能承接事实。
Alternatives：不使用吞未知属性异常的 _set_properties 作为严格 writer；使用真实临时 World 验证隔离提交、typed literal 和对象引用。
Migration：当前 head=0024_mock_schedule_override；先以 0025_template_evidence 随 US1 交付模板字段，再以 0026_evidence_facts 随 US3 交付事实库，保证迁移先于访问新列。

## R4 乐观并发与幂等

Decision：SQLAlchemy 条件 UPDATE(id, revision) 检查 rowcount；审核和编辑均 CAS，幂等键由数据库唯一约束保护，IntegrityError 后 rollback 再查询同键。
Rationale：先读后比和仅 FOR UPDATE 无法在 SQLite 契约测试下证明并发安全。
Documentation：Context7 /websites/sqlalchemy_en_20，versioning、ORM DML、Session transactions；Pydantic /pydantic/pydantic，discriminator、extra=forbid、JSON serialization，查询日期 2026-09-05。

## R5 覆盖与否定

Decision：发布快照的 FactSelector 精确匹配主体/完整谓词/对象/属性；否定和条件为合法断言，但不计入正向图遍历。absent 引用范围匹配的已发布否定。
Rationale：现有覆盖按目标类型和任意对象属性判断，会让 A 遮盖 B。
Alternatives：报告阶段的设备/车间富化必须提前转候选，不能在快照外重新生成事实。

## R6 规则退役和验收

Decision：全仓自有代码/配置静态盘点结合动态旧入口禁止测试；第三方审计独立清单；人工金标、消融和性能报告保持未实测状态直到有实际数据。
Rationale：扫描几个文件、候选数不减少或旧模型输出不能证明质量。
Baseline：本轮运行三个现有测试模块，101 passed；4 条既有依赖警告及 1 条 pytest 缓存目录权限警告。后续测试禁用缓存插件，不需要改目录权限。
Spec Kit：Context7 /github/spec-kit 确认完整 specify/clarify/plan/tasks/analyze/implement 流程；本地安装 0.11.3 模板为执行基准。
