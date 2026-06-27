# Contract: GlinerExtractor（本地零样本 NER 提取器）

**Feature**: `008-gliner-ner-extraction` | **Module**: `backend/app/services/extraction/gliner_extractor.py`

逐字镜像 `semantic.py:SentenceTransformerEmbedder` + `get_embedder` 的可插拔/惰性/单例/降级范式（research [R7](../research.md)）。本契约定义对外形态与不变量，**先于实现**。

---

## 接口

### `GlinerExtractor`（可插拔协议 + 默认实现）

```
is_available() -> bool
    # 惰性触发 _ensure_model()；模型成功加载返回 True，否则 False（绝不抛出）。

extract_text(text: str, labels: list[str], threshold: float | None = None) -> dict[str, str | list[str]]
    # 对 text 跑零样本 NER，返回 {label: value | [values]}。
    # 同一 label 多命中 → list（research R9）；无命中的 label 不出现在结果中。
    # 不可用 / 空 text / 空 labels → 返回 {}（绝不抛出）。
```

默认实现 `_ensure_model()` 行为（镜像 semantic.py）：
- try-import `gliner` → `GLiNER.from_pretrained(settings.gliner_model_path, local_files_only=True)`。
- 任意异常（缺包 / 缺权重 / 加载失败）→ 置 `self._failed = True`、记 `logger.warning(...)`、**不抛出**。
- 已 `_failed` → 直接返回（不重试加载）。

### `get_gliner_extractor() -> GlinerExtractor | None`（`@lru_cache(maxsize=1)` 进程级单例）

- `settings.gliner_extraction_enabled is False` → 返回 `None`（功能关闭）。
- 否则返回单例 `GlinerExtractor()`（首次 `is_available()` 才真正加载）。

---

## 不变量

| # | 不变量 | 验证方式 |
|---|--------|----------|
| C1 | **绝不抛出**：`is_available()`/`extract_text()` 在缺包/缺权重/加载失败下均不向调用方抛异常 | 注入「import 失败」桩 → 调用不抛、`is_available()=False`、`extract_text()={}` |
| C2 | **进程级单例**：多次 `get_gliner_extractor()` 返回同一实例，模型仅加载一次 | `@lru_cache` + 加载计数桩断言 1 |
| C3 | **功能开关**：`gliner_extraction_enabled=False` → `get_gliner_extractor()` 返回 `None` | 设置翻转后断言 `None` |
| C4 | **强制离线**：加载路径恒用 `local_files_only=True` 且为本地 `gliner_model_path`，无远程 repo id 解析 | 断言 `from_pretrained` 入参含 `local_files_only=True` 且路径为本地目录 |
| C5 | **多值聚合**：同 label 多命中聚合为 `list`，单命中为标量 | 桩返回 2 个同 label 实体 → 结果该键为 `list` 长度 2 |
| C6 | **标签驱动**：仅返回 `labels` 中声明的标签键，无额外键 | 断言结果键 ⊆ 入参 `labels` |

> GLiNER 推理为 CPU 同步阻塞，pipeline 侧经 `asyncio.to_thread(ex.extract_text, …)` 调用（不阻塞事件循环）；本契约定义同步 `extract_text` 形态，异步包装是调用方职责。

## 测试桩约定

契约/集成测试以**确定性桩**替换真实权重（同 `Embedder` 协议测试桩做法）：桩实现 `is_available()`/`extract_text()`，按输入文本返回预置实体，断言聚合/降级/单例行为，**不下载真实模型**。
