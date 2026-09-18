# Implementation Plan: 报告输出语义化

**Branch**: 020-report-output-semantics | **Date**: 2026-09-06 | **Spec**: [spec.md](spec.md)

## Summary

以严格 TemplateV2 编译计划、字段级 ResolvedInput、冻结 ReportRun 和通用输出 AST 替换取值/业务判断/行文混合。复用 019 发布断言、身份和现有栈。正文审核和独立签署封装分别按不可变内容身份执行。

## Technical Context

**Language/Version**: Python >=3.11、TypeScript、React 19 / Next.js 16。
**Primary Dependencies**: 现有 Pydantic 2、SQLAlchemy 2、FastAPI、rdflib、python-docx、React Query。
**Storage**: PostgreSQL + 内容寻址文件；SQLite 用于契约与独立连接并发测试。
**Testing**: pytest、Node test、tsc、ESLint、build；实际来源/模型/部署另记证据。
**Target Platform**: Linux 内网、默认离线。
**Project Type**: web application。
**Performance Goals**: 按原设计 §20.3 分别测编译/解析/规则/模型/导出，p95 <=同配置基线1.2倍，无基线不声称达标。
**Constraints**: 不新增框架/依赖；不可变源及输出；禁止生产正则/业务 finder；未知契约拒绝。
**Scale/Scope**: 基准3节6组32项；默认预算 hops=16、records=1000、units=256、repeat_depth=4、nodes=10000，超限 incomplete/failed。

## Constitution Check

Phase 0 前：六原则通过。按 specify→clarify→plan→tasks→implement；先契约后实现；TBox 不原位改公理；写/签署有角色、CAS及审计；复用已有栈；离线可运行。
Phase 1 后：六项复查通过。TBox 执行注解退役先提供三元组差异；实际规则/条件审核不由测试代理批准。无法证明的历史家族独立登记。

## Project Structure

- backend/app/services/reporting/: template_v2、template_compiler、binding_resolver、input_resolver、condition_resolver、claim_catalog、report_snapshot、output_ast、output_renderer、narrative_renderer、report_signing、template_migration、report_run_service。
- backend/app/models/reporting.py、backend/alembic/versions/0027_report_output_semantics.py。
- backend/app/api/report_runs.py，现有 ast_templates/extraction/reports 门面。
- backend/app/services/reasoning/rule_migration.py。
- frontend/src/lib/reporting-v2.ts、components/reporting/，现有模板/报告页面。
- backend/tests/test_reporting/test_output_*.py、test_report_signing.py，frontend/tests/reporting-v2.test.mjs。
- scripts/audit_report_semantics.py，本特性 migration/retirement/validation 制品。

## Design decisions

2026-09-09 创建语义调整：向导显式动作顺序复用模板创建与样例附件接口，等待两者成功后路由至持久模板 ID 的定义页签；保存中锁定向导并拦截重复点击，附件失败保留已创建 ID 供重试。移除未保存创建页和跨页面内存草稿传递。详情页通过 URL 初始化定义页签，普通编辑入口仍遵循已有默认页签。验证创建失败、附件失败重试、进入后直接返回/刷新、后续修订保存，不引入数据库迁移或模型任务。

保存等待修复：创建 POST 不再携带解析全文；附件 POST 的 `include_content=false` 在提交后返回 204，原有完整预览响应兼容保留。两次保存请求均设 180 秒等待上限，沿用认证客户端，超时提示结果待核对。附件确认成功后结束保存状态，路由跳转独立进行，并提供普通链接直接打开已存模板；关闭弹窗刷新列表，以覆盖响应丢失但服务端已提交的情况。验证见 [template-save-latency-validation.md](template-save-latency-validation.md)。

本体使用服务端固定 schema/hash；参数/工作流/规则/条件/样式/策略通过不可变注册记录引用。客户端只给资源引用，不能自行宣称已发布。模型逐层 extra=forbid。依赖编译 DAG，Render 无 DB/selector。hash 排除自身标识但包含诊断/发现/预算/契约变化。UseResolution 按消费字段和祖先约束计算，保留强制字段要求。签署 CAS 与事件/审计同事务，finalizing 重试只读既定快照。

## Complexity Tracking

无新增依赖。不可变对象与可变状态分开存储是重放、并发签署和历史保留的直接需求。
