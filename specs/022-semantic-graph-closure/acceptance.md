# T017 本次验收执行记录

日期：2026-09-09 UTC。**正式质量验收尚不能完成，评分状态为
`pending_expert_reference / formal_quality_gate=not_run`。** 这是材料准入阻断，
不是已测 precision/recall 低于阈值，也不是质量通过。T017 保持未勾选。

## 本次实际完成

1. 核验现有参考资格：9 份参考字节相同，均为未经专家批准的 `assistant_silver`；
   同一份开发文档的多份副本不是独立测试文档。8 份历史协议没有本轮获批质量/成本阈值。
   逐项来源见 [材料审计](acceptance-materials.md)。
2. 重算 `integration-03` 的 12 项最终输出 SHA256，全部匹配。在新目录复制只供评分的
   `run.json/result.json`，没有写回原运行或恢复任何任务。
3. 实际调用正式评分入口，得到上述 `not_run`。输出见
   [metrics.json](../../evaluations/022-t017-acceptance-20260909/scoring-attempt/metrics.json)，
   汇总见 [assessment.json](../../evaluations/022-t017-acceptance-20260909/assessment.json)。
4. 导出完整、已提交且未降级的固定池：8 条记录、2 个意图，内容 hash 为
   `bc87ca95e22fa3579020fdd753a2e16ba352a6db4bd27ab17023da5ad2d24e99`。
   它是开发文档中的共同排序输入，不能代替全文召回或独立文档样本。
5. 准备可复核的 [审阅材料包](../../evaluations/022-t017-acceptance-20260909/review/README.md)
   与 [正式运行协议草案](acceptance-protocol.md)。原文只保存在本地审阅包，不复制到本页。

审阅包提供 86 行全文裁决表、447 个证据单元目录、16 行分意图和 8 行融合相关性表，
两份合法的图谱参考草稿、当前 JSON Schema 与待填阈值。没有使用模型预测预填裁决。
652 处源引用回放通过；未知 ID、负偏移、越界、空跨度和错引文 5 类反例全部拒绝。
实际将格式及文档/本体/scope 均匹配的草稿交给正式评分器，仍按预期拒绝未批准参考，
见 [草稿门禁结果](../../evaluations/022-t017-acceptance-20260909/draft-reference-gate.json)。
可下载 [expert-review.zip](../../evaluations/022-t017-acceptance-20260909/expert-review.zip)，
396,107 bytes，SHA256：`ce5bbf8efa61972b8ffb197c4e8a8c5c65ee1b1da0a477a0043cdc3be1f5f238`。

本次评分命令在 `backend/` 执行：

```bash
.venv/bin/python -m app.evaluation.cmc_benchmark score \
  --prepared ../evaluations/022-independent-upload-23c872fb-20260908/prepared-04 \
  --run ../evaluations/022-t017-acceptance-20260909/scoring-attempt
```

本次新增模型调用 **0**，未执行新的正式三轮 A–D、未计算事实/路径质量分数。
材料就绪前重复诊断不能补足“预测前冻结参考与阈值”的要求，因此没有用新的昂贵诊断
冒充正式运行。已有 CPU 与两条系统有效设备关系仍按 [原实测](independent-verification.md)
报告；原运行覆盖仅 4/1,462 个主体—谓词—记录机会，不能据此推断整图质量。

## 完成正式验收所需输入

以下三个包可以提供一个统一受控目录：

- 获准独立真实 DOCX 清单、路径/内容 hash、显式根和验收范围；不能仅重复开发文档。
- 对应文档的完整图谱及检索参考，经实际专家裁决并保留复核人、时间和标注包 hash。
- 预测前批准的质量/成本协议：主指标、增量/非劣界限、样本规模、三轮 A–D 矩阵及预算。

这些条件来自 [T017](tasks.md) 和 [FR-014](spec.md)，不是模型环境安装的额外限制。
审阅包中的空白表、`draft` 参考和 `null` 阈值只用于填写，不能直接改成 `approved` 当作
专家审签。材料齐备后须冻结新协议与新运行身份，再执行完整对照、隔离评分和最终裁决。
