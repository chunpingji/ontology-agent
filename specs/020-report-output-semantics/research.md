# Research

- Decision: 复用 FactCommitService.published_snapshot、FactSelector、evidence_hash，扩展根属性和字段冲突，不放宽 positive_eligible。
  Rationale: 019 已有发布边界。Alternatives: 候选缓存和 finder 不能证明来源及范围。
- Decision: 严格递归 Pydantic 类型、有限操作 AST、稳定引用及 SQLAlchemy 原子 revision CAS。
  Rationale: 已用 Context7 核对 Pydantic v2 discriminator/extra forbid、SQLAlchemy 2 UPDATE rowcount；未知字段不可丢弃。
- Decision: 按 schema_version 分流，React 受控状态不可变更新，ID 作 key。
  Rationale: 旧 slots 编辑器与 units 不兼容，共用 fetchAPI 需修复 headers 覆盖身份。
- Decision: 报告签署使用真实账户身份及 verify_password，不复用 conclusion_id 或共享口令。
  Rationale: 正文 hash、签署区域及独立封装是不同证明。
- Decision: 旧规则 payload/hash 冻结后只生成待审修订，停止 seed 原位升级。
  Rationale: R-RA1～5 的前提不足以证明 consequent 的既成声明；仅版本化不解决真实性。

## Read-only findings

两条风险路径漏 published，FALSE→低及 postconditions 降级需一起移除。模板详情会创建来源，资产清理只计模板引用，需修复。旧家族按名称且 PATCH 可改 published。019 最终退役未完成，不能只扫 reporting。基准 fixture hash 为 78721d33afb40f12e77d2e048b5d613945c3442fafe759256561fbb4bce6b8d1；部署来源/规则另查。

## Docs

Context7 /github/spec-kit、/pydantic/pydantic、/websites/sqlalchemy_en_20、/react/react/v19.2.7，2026-09-06。
