# Implementation Plan: 统一文档证据与通用语义抽取

**Branch**: `019-evidence-semantic-extraction` | **Date**: 2026-09-05 | **Spec**: [spec.md](./spec.md)

## Summary

落实已评审设计的完整范围：共享 Word IR 和模板来源，通用主体条件抽取，独立逐值候选及类型化来源，审核/幂等实例提交/发布快照，实例级 coverage 和受控补抽，最终自有链路规则退役。已有 018 结构基础继续复用；实现按故事与依赖推进，发布金标不与代码完成混淆。

## Technical Context

**Language/Version**: Python 3.12（兼容 3.11+）、TypeScript 5。
**Primary Dependencies**: 现有 FastAPI、Pydantic v2、SQLAlchemy 2、Owlready2、python-docx、React 19/Next.js 16/Tiptap 3、本地结构化 LLM 客户端。
**Storage**: PostgreSQL 生产、SQLite 隔离测试、Owlready2 World、不可变文档及事实快照。
**Testing**: pytest 契约/集成/真实临时 World，前端 ESLint/tsc/build，静态及运行规则审计。
**Target Platform**: 离线 Linux CPU 及浏览器。
**Project Type**: 现有前后端 Web 应用，无新框架。
**Performance Goals**: 一次文档解析；有界任务/窗口/补抽；生产 p95 不高于同桶基线 1.2 倍且满足已冻结 SLO。
**Constraints**: 不默认出网、不上传模型或正文、审核前不发布、不执行遗留业务规则、逐值来源可回放。
**Scale/Scope**: 六个故事、FR-001—FR-028；完整设计覆盖结构到报告的生产链。

## Constitution Check

设计前及设计后检查：

- I：specify → clarify → plan → tasks → analyze → implement；规格层为可测试用户需求，技术放在本文件及 contracts。
- II：不改变类/属性语义、BFO 和外部对齐；退役执行型注解先提供三元组 diff，再外科式移除对应语句，保留其他 TTL 内容。
- III：候选版本、CAS 审核、追加日志、不可变提交和快照；不篡改已发布事实。
- IV：先写 contracts 和失败测试，再实现；真实 World 验证实例写入，不能用 no-op FakeOntologyEngine 证明提交。
- V：复用现有服务和 ORM；候选主表统一 kind，逐值候选独立记录，版本及来源有不可变副本；不建立第二套业务 finder。
- VI：默认离线/云端关闭；本地模型关闭时 `completion=incomplete` 是任务状态，`degraded=false` 保持离线正常态。结构化外部记录映射仍可用，Word 语义抽取不回退正则或旧 finder。用户明确批准的最终降级语义优先于历史算法零回归约定。

**Result**: 可进入实现；真实金标/性能/第三方全栈审计是发布条件，不作为已经完成的工程验证。需要修改的项目既有假设已在 spec 澄清与本节显式记录。

## Architecture

1. `parse_docx_structure` 采集原文块、递归表格、真实合并单元格与章节；无正则结构 scanner。`analyze_word_core` 产生 DocumentIR 和预览兼容视图。
2. Pydantic evidence schema 统一 Anchor、Scope、SubjectRef、Candidate、BindingEvidence 和 provenance；稳定哈希不依赖进程对象身份。
3. `GenericExtractionRunner` 先实体召回再主体属性/关系任务，本体定义提供菜单，结构化本地模型输出经严格字段/引用/来源/绑定校验。主体及谓词窗口轮转，关系对象有界分批并按真实 token 预算进一步拆分；已验证正向边的路径任务加入同一队列。调度和传输协议版本纳入恢复身份，所有结果保留显式断言极性。
4. 候选库保存逐候选当前状态和不可变 revision；审核使用条件 UPDATE 的 CAS。实体归一化显式更新依赖。
5. 提交清单/outbox 先落库，独立 writer 在提交隔离的 World 中写入实例、断言和合格正向投影并回读，成功才发布快照；默认业务读取使用发布快照。作业当前快照使用版本 CAS 推进，不同幂等键并发提交发生冲突时重新读取最新快照并合并重试，不能丢掉另一批已发布事实。
6. FactSelector 按实际实例和完整路径遍历，coverage 与报告共享选择；否定断言只支持相应范围 absent，补抽受预算和去重约束。
7. 最终删除生产可达 finder/正则配置与隐式回退；历史记录仅只读显示，审计工具明确残留范围。

## Project Structure

```text
specs/019-evidence-semantic-extraction/
  spec.md plan.md research.md data-model.md tasks.md quickstart.md
  checklists/requirements.md contracts/evidence-api.md
backend/app/
  services/extraction/{document_ir,word_analysis,evidence_scope,hierarchical_context,
    extraction_tasks,semantic_binding,literal_normalizer,candidate_store}.py
  services/{fact_commit,ontology_instance_writer,fact_selector}.py
  schemas/evidence.py models/evidence.py api/evidence.py
  services/reporting/{coverage_validator,ast_template,risk_report_generator}.py
backend/alembic/versions/0025_template_evidence.py
backend/alembic/versions/0026_evidence_facts.py
backend/tests/test_extraction/ backend/tests/test_reporting/ backend/tests/test_api/
frontend/src/{lib/api.ts,components/extraction/,components/ontology/,components/data-mapping/}
scripts/audit_extraction_rules.py scripts/evaluate_extraction.py
```

## Delivery and Validation

按 tasks 的前置依赖执行；每完成一项附测试证据才标记。模板字段迁移 0025 与 US1 一同交付，事实基础迁移 0026 在 US3 交付，避免先访问尚不存在的列。研究中识别的旧错误测试应按新契约更新，不保留会静默造事实的兼容路径。本体执行型注解移除前先生成 diff，并验证既有元数据/World 同步发布、健康校验和失败回滚，记录批次/actor/提交身份及非目标三元组不变。不可获取独立人工金标或真实模型质量结果时，工具与代码可完成，但发布验收任务保持未完成并报告具体外部依赖；质量阈值、分桶、生产 SLO 和内存预算须有权威来源配置，缺失时发布 gate 失败。

## Complexity Tracking

| 必要复杂度 | 原因 | 被排除的简化 |
|---|---|---|
| 不可变候选版本、断言/快照及 outbox | 跨 SQL/World 的失败恢复与逐值审计 | best-effort 图写入、单个属性字典、提前 committed |
| 新通用候选接口并迁移旧 UI | 旧响应无法表达否定、多值和独立提交 | 给旧 finder 加 scope 参数后永久保留 |
| 发布态与自动验证分离 | 审核及真实来源要求 | 高模型分直接入图 |
