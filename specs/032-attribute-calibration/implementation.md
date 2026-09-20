# 032 实现记录

日期：2026-09-19。代码实现完成；后续部署及在线核验见 [部署记录](deployment.md)。未执行真实文档模型评测。

## 最终行为

确定性值解析先于属性的事实准入校验，输出独立 `parsed_value` 观察。年月、日期、数值、单位、区间、比较符和精度由代码解析；本体字符串属性保留编号前导零。年月不会补日，区间不会变成标量。绑定或语义失败仍不生成可信 `normalized_literal` / quantity。

字段引用唯一、同段同字段且原文精确时，可以把模型引用的“时间：2026年02月”收窄为标签“时间”。原模型响应保留；该修正不证明该字段属于计划生产时间。歧义、跨行、跨字段及越权引用不修正。

原字段及有效原文中的未决属性留在原 `work:record_discovery` 行。候选保存原值、精确原文、解析观察、合法归属选项、检查状态及根因；未知主体可无选项。冻结时拒绝的非法映射不重新准入。技术失败、模型漏报不会清空旧候选；确认对应字段后才移除，主体纠正也能完成清理。

常规记录与关系识别收尾后，比较相关实体、关系及原文输入签名，针对变化项安排最多一次校准。复用原 lineage、当前协议和剩余调用额度；字段保持最多两次消歧限制。相关关系的原文只作核对上下文，不能授权新的属性值。任务等待期间更新关系线索，活动任务恢复核对版本和证明依赖；无变化不重试，预算不足保留未决。

前端新增独立“待校准属性”列表，无正式实体/属性时也可查看。展示原值、解析值/类型/精度、区间或比较符、候选归属和原因；字段及值可通过受权限控制的引用查看原文。列表不参与事实推理、计算或报告。SHACL 未执行通过 `blocked_by` 和工具消息保留具体前置失败原因。

## 主要代码

- `schemas/attribute_value.py`、`ontology_guided/value_observation.py`：未可信解析观察及唯一标签修正。
- `tool_validation/metric.py`、`shacl.py`、`tool_runtime.py`、`tool_model_adapter.py`：解析与事实校验解耦、SHACL 阻断根因。
- `schemas/attribute_calibration.py`、`ontology_guided/attribute_calibration.py`：候选契约、合法来源筛选、候选合并与相关依赖签名。
- `record_model_adapter.py`、`executor.py`：候选生成/保存、收尾校准、继续、成功清理和失败保留。
- `attribute_disambiguation.py`：一般时间字段可匹配本体中的日期类数据属性作为消歧候选，业务含义仍待原文核验。
- `document_analysis/current_state.py`、`public_projection.py`、`execution.py`：当前候选展示分区、从权威工作状态重建、原文 registry 与图重建传播。
- 前后端 API 契约及共享 `attribute-calibration-list.tsx`：独立候选列表，接入普通文档的 `document-relationship-graph.tsx` 和模板的 `template-document-graph-panel.tsx`。

新运行冻结 `attribute_calibration=source-observations-v1`，默认开启。未修改本体、数据库结构或真实运行；已有未提交工作保留。工程实现阶段未部署，后续按用户“请部署”的授权更新本机服务。

## 验证与限制

实际命令和结果见 [quickstart.md](quickstart.md)。工程验证使用真实当前状态仓储、合成原文及受控模型，覆盖关系完成后的暂停/冷继续和一次校准，不代表真实模型质量验收。真实文档的主体归属准确率、属性召回率及耗时尚未实测。
