# GLiNER、Mock 与实验词表启用

2026-09-17 约 01:10 UTC，按用户明确授权在当前开发部署中启用三项能力，并重建后端容器。新建非模板文档分析运行将冻结这些配置；已有运行继续使用原冻结配置。

| 配置 | 实际启用内容 |
|---|---|
| GLiNER | GLiNER2.5，`/app/models/gliner2.5-multi-v1-20260916`，CPU 离线推理；revision `aaecfe45db1d828c963717054ccb868e8ad1f1d5`，加载前核对六个制品文件 |
| Mock | `system-mock-equipment` 211 条设备、`system-mock-roles` 5 条角色；只使用已有 class IRI 和属性映射 |
| 词表 | 25 条显式手工定义/同义词；权威本体定义与直接 skos:altLabel 优先，不修改 T-Box |

配置由原 [运行清单](../runtime/manifest-cmc-mock03.json) 的 options、[Mock 快照](../system-mock-sources.json) 和 [手工词表](../manual-vocabulary-overlay.json) 组合而成。配置内容 hash 为 `ed9654044bed08c1564d5001e7b3872d39e591630cb195b252c9fda6745cf581`。启用前只读核对当前数据库，两个来源的逐记录版本摘要均与快照一致。该 reader 读取冻结快照，后续 Mock 数据变更不会自动改写已冻结运行。

## 本机配置入口

实际文件为 `backend/data/ontology-tool-options.env`，权限 0600，仅包含本次 `ONTOLOGY_EXTRACTION_OPTIONS` JSON。本机 `docker-compose.override.yml` 将其只读挂载到 `/app/.env`，复用 Settings 已有 dotenv 支持；没有新增配置加载器、数据库表或运行状态。配置约 248 KB，不通过 Docker 单个环境变量传入，不读取或覆盖项目原有密钥文件。

上述两个部署文件属于本机忽略内容，当前主机重启时仍可使用；在其他主机部署需显式准备同样的文件与挂载。本次没有修改基础 Compose 的默认启用范围。

执行命令：

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d --no-deps --no-build --force-recreate backend
docker compose -f docker-compose.yml -f docker-compose.override.yml exec -T web nginx -t
docker compose -f docker-compose.yml -f docker-compose.override.yml exec -T web nginx -s reload
```

## 实测与修复

首次实际工具预检暴露完整定义分批缺陷：[编码长度检查](encoder-input-preflight.json) 中八标签批次为 790–828 tokens，超过实际 512 上限。`propose_vocabulary_mentions` 默认改为逐标签分批，与此前已验证的 GLiNER2.5 实验一致；仍处理全部标签，单标签超限继续失败，不截断定义/原文或提高模型上限。只修改应用源码，当前开发容器由已有源码挂载加载，无依赖变更。

先以行为反例复现，再运行以下四文件：**134 passed**，3.83 秒；定向 Ruff 通过。

```bash
cd backend
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_tool_mentions.py \
  tests/test_extraction/test_tool_runtime_recall.py \
  tests/test_extraction/test_tool_engine_configuration.py \
  tests/test_extraction/test_gliner2_extractor.py
```

[真实工具集成结果](integration-check.json) 由 [检查脚本](check-enabled-tools.py) 生成，使用与上线一致的配置和实际应用装配器：

- GLiNER2.5 实际加载并完整执行一个授权原文单元，`no_match`，0 个提及、0 个未执行单元、0 个遗漏。该样本没有识别成功，不以完整执行冒充质量通过。
- 当前卡片缺实体定义为 0，Reactor 使用的直接同义词包含“反应釜”。
- 原文精确定位取得真实 mention_ref 后，Mock 查询返回 1 个候选，身份保持 `not_checked`；候选不构成关系证明。
- NER 和实例查询均进入 discovery 工具定义；没有 Qwen 请求或图写入，不是新的自主协作/F1 评测。

上线后再核对容器读取的配置 hash 与预检一致、只读挂载成立、数据库 revision 仍为 `0041_template_engine`。统一入口 `/api/health` 与 `/extraction` 均 HTTP 200，后端 running、重启次数为 0。
