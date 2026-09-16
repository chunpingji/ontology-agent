# 只读复核脚本

在仓库根目录运行，参数使用完整 D 制品目录；这些脚本不调用 NER 或 Qwen，不修改冻结输入。`check-masked-equivalence.py` 使用当前仓库的纯重建函数，并逐项核对代码与冻结哈希；代码发生变化时应恢复匹配环境，不绕过哈希检查。

```bash
python docs/调研/cmc-summary-retrieval-validation-20260916/scripts/check-artifacts.py \
  output/schema-card-qwen-cmc-summary-retrieval-20260916/completed \
  --ner output/schema-card-qwen-cmc-summary-retrieval-20260916/ner

backend/.venv/bin/python docs/调研/cmc-summary-retrieval-validation-20260916/scripts/check-masked-equivalence.py \
  output/schema-card-qwen-cmc-summary-retrieval-20260916/completed

python docs/调研/cmc-summary-retrieval-validation-20260916/scripts/summarize-metric-baseline.py \
  output/schema-card-qwen-cmc-summary-retrieval-20260916/completed D
```

原始 Schema 检查使用本次系统 Python 的 `jsonschema==4.23.0`；不通过联网解析外部 Schema。D 的真实核验输出截断，所以 `check-artifacts.py` 本轮预期退出 1，并列出模型合约失败；不能为获得退出 0 而跳过该返回。`failure_classification` 区分模型返回与制品一致性问题。

`cost-metric-d.json` 的基础计数由统计脚本生成，`comparison_to_C`、`token_usage_totals_including_unknown_calls`、`numeric_calibration_status` 为基于统计的分析补充。未知失败用量不记零，数值分母为零时通过率保持 null。`request-footprint.py` 只读请求、sources 和 tools，并按已冻结 C/D 本地路径统计字节／字符；不估算 token 或 HTTP 失败根因。

`make-review-packs.py RUN_ROOT REVIEW_INPUT_DIR` 只输出原文、卡片和原始候选。独立审阅后，运行 `match-reviews.py RUN_ROOT REVIEW_INPUT_DIR OUTPUT REVIEW_JSON...`，按包 SHA、scope、ID、path、主体、字段及完整 proposal 核对归宿。已存候选但核验失败且没有任何最终处理时标为 `not_finalized_model_failure`，不能当成语义拒绝或程序保留。
