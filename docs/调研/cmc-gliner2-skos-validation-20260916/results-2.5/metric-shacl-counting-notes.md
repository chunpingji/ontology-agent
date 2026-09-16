# C 组 metric / SHACL 统计口径（只读核验）

统计脚本：`summarize-metric-baseline.py RUN_ROOT ARM`。仅使用 Python 标准库读取保存制品，不导入业务代码、不执行模型或 SHACL。

- 对 C 使用 `python /tmp/cmc-gliner25-public-metadata/summarize-metric-baseline.py <C运行根目录> C > /tmp/cmc-gliner25-public-metadata/c-metric-summary.json`。
- `frozen-candidates.json -> claims[].metric_precheck` 是候选冻结时的预检调用记录。
- `result.json -> metric_checks[].metric` 是语义核验后的正式指标门调用记录。
- 一条属性通常各执行一次，须分别统计；预检 + 最终门不是不同属性数。快照不能恢复此前重算历史的总调用次数；若异常发生在持久化前，记录数也不能保证等于真实调用总数。
- 数值候选分母依据 `subject-cards.json` 中该 candidate 主体/字段的 `datatype_iris`，包括解析失败项；`quantity != null` 只代表数量解析成功。报告中含数字的字符串字段不是数值槽位。
- 真正覆盖且符合 SHACL 的候选须同时满足：`execution_status=completed`、`evaluated=true`、`conforms=true`、`validation_status=passed`、`coverage.complete=true`、expected/actual focus 集合非空且相等、executed_shapes 非空、missing/unexpected focus 为空。空图或未运行不通过。
- focus URI 如 `urn:ontology-agent:claim:c2` 在不同 scope 中会重复，计数须带 `(arm, scope_id, candidate_id)` 或按 focus 访问次数累计，不可全局去重裸 URI。
- 数值 SHACL 与所有字面量 SHACL 分开报告。数值覆盖为 0 就是本轮未验证，不能表述成换算效果达标。
- 正式数值归一化要求指标门 passed 且 normalized_value 非 null。换算再按 conversion_record.mapping_kind 分 identity / alias / conversion；真正数值比例/偏移换算还要求 factor != 1 或 offset != 0，并逐项展示原值、原单位、目标单位、系数、偏移、规范值及原文引用。字符串/date 格式变化不计入单位换算。
- `unit_checks=passed` 可能仍被主体/语义门阻断；不能独立计成功。`fact_eligible` 在工具内故意恒为 false，不能拿它做统计分母或成功判断；程序保留另从 accepted 中按 candidate_id 连接。
- 当前 SHACL 仅验证单字面量表示契约，不证明主体归属、专业语义、关系方向或业务限度合格。

## 旧 B 的成本比较基线（排除全部 plan 请求）

来源：`output/schema-card-qwen-cmc-tools-20260916/checked/B/*/result.json`。每个 scope 的 calls 与阶段 `calls.json` 副本全部吻合，无重复叠加。

| 阶段 | HTTP | 已报告 usage | 缺失 usage | prompt tokens | completion tokens | total tokens | HTTP 累计秒 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| candidates | 8 | 7 | 1 | 105458 | 11792 | 117250 | 861.414 |
| verification | 7 | 7 | 0 | 45606 | 8690 | 54296 | 318.914 |
| 合计 | 15 | 14 | 1 | 151064 | 20482 | 171546 | 1180.328 |

14 次 stop；PDE candidates 超时 239.998 秒，无 usage。缺失用量是未知，不是免费或实际消耗为零。HTTP 累计秒不是端到端墙钟。

B 的 7 个完成片段共 14 次请求、940.330 HTTP 秒；已报告 token 总量与上表一致，因为剩余超时请求的用量未知。C 全 8 片段必须首先与 B 全 8 的执行/失败口径比较；如另作完成片段配对，明确排除 PDE，并对 C 采用相同七片段集合，不能把 B 未执行 verification 解释为质量更高或更省成本。

## 旧 B 的单位/SHACL基线

- 唯一冻结属性候选 24 条；保存预检记录 24 条，正式指标门 24 条，合计 48 条选定快照内 metric 记录。
- 正式 SHACL 执行 18 次，18 次 actual focus 完整且 conforms=true；全部为非数值字面量。
- 数值槽位候选仅 3 条（PDE 的候选阶段技术失败，未进入分母）：简介 plannedBatchSizeMax_kg=6.6、Min_kg=3.8 均因区间/单位绑定前提阻断；工艺 stepOrder=1 因原文不含该 raw 和语义未决阻断。
- 数值 SHACL 0/3，正式数值规范化 0，真实比例/偏移换算案例 0。
- A 交叉核对：48 条正式门、15 个数值槽位、20 次非数值 SHACL；与 B 合计 72 条正式门、18 个数值槽位、38 次 SHACL，吻合现有报告。

机器明细：`b-candidate-verification-baseline.json`、`a-metric-crosscheck.json`。
