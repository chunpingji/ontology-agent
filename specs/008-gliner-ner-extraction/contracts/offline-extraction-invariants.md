# Contract: 离线抽取不变量（air-gap 默认 + 云端 opt-in + 优雅降级）

**Feature**: `008-gliner-ner-extraction` | **Modules**: `llm_extractor.py` / `pipeline.py` / `config.py`

界定「离线为正常态、云端为 opt-in、降级不致失败」三组不变量（US1 / FR-002/003/012，research [R2](../research.md)）。

---

## 1. 云端 LLM 触发门控（FR-002）

`extract_with_fallback(source_data, target_class_iri, ...)` 改造后：

```
触发云端 LLM  ⟺  settings.llm_cloud_enabled AND settings.anthropic_api_key
否则          →  return (source_data, None)   # 结构化源原样返回，degraded_reason = None
```

| # | 不变量 | 验证方式 |
|---|--------|----------|
| O1 | **默认关**：`llm_cloud_enabled=False`（默认）→ 永不调用 `anthropic.*`，无任何网络调用 | mock anthropic 客户端，断言 0 次调用 |
| O2 | **opt-in 需双条件**：仅 `llm_cloud_enabled=True` 且 `anthropic_api_key` 非空才触发云端 | 四象限（开关×Key）断言仅一象限触发 |
| O3 | **离线非降级**：未触发云端时 `degraded_reason is None` | 默认设置下断言返回二元组第二项为 `None` |

## 2. 降级语义（`degraded_reason` 何时非空，FR-003）

| 场景 | `llm_cloud_enabled` | `anthropic_api_key` | 调用结果 | `degraded_reason` |
|------|--------------------|---------------------|----------|-------------------|
| air-gap 默认 | False | 任意 | 不调云端 | **None** |
| 云端开启、调用成功 | True | 有 | 成功 | None |
| 云端开启、调用失败/空 | True | 有 | 异常/空 | **非空**（真实降级原因） |
| 云端开启、无 Key | True | 空 | 不调云端 | 非空（配置缺失说明） |

| # | 不变量 | 验证方式 |
|---|--------|----------|
| O4 | `degraded` 仅在**云端被显式开启却无法兑现**时为真；离线默认恒为假 | 上表逐行参数化断言 |
| O5 | 进度事件 `ProgressEvent.degraded` 与 `degraded_reason` 一致（离线作业不广播 `degraded=True`） | 离线跑 pipeline，断言所有 `_emit(..., degraded=False)` |

## 3. NER 不可用优雅降级（FR-012）

pipeline 在 prose（US2）与富化（US3）分支前守卫：

```
ex = get_gliner_extractor()
if ex and ex.is_available():
    ... # NER 路径
# 否则：静默跳过该分支，结构化兜底
```

| # | 不变量 | 验证方式 |
|---|--------|----------|
| O6 | **作业不失败**：NER 不可用（`None`/`is_available()=False`）时 pipeline 正常完成，作业 `status=success` | 注入不可用桩，断言作业成功 |
| O7 | **结构化零回归**：NER 不可用时，结构化候选与改造前**逐字一致**（prose 候选为空、Excel 不富化） | 黄金基线对比（同输入→同结构化候选集合） |
| O8 | **运维可见**：NER 不可用记 `logger.warning`，但不写 `degraded`/不报错给用户 | 断言日志含 WARNING、作业无 error、`degraded=False` |

## 4. 零外发网络（FR-011 / 安全）

| # | 不变量 | 验证方式 |
|---|--------|----------|
| O9 | 整条抽取在**无网络、无 Key**下端到端成功（结构化 + 本地 prose/富化） | air-gap 模拟（断网 / mock 出网调用为异常）跑 quickstart 场景 1，断言成功 |
| O10 | 默认配置下无任何对外 host 的网络尝试（云端关 + 本地模型 + `local_files_only`） | 断言无 anthropic 调用、无 HF 远程解析 |

---

**关联**：[gliner-extractor.md](./gliner-extractor.md)（NER 可用性来源）、[data-model.md §5](../data-model.md)（降级语义表）。
