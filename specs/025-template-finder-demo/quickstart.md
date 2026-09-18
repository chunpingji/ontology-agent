# 验证入口

1. 部署本次版本并完成 `0041_template_engine` 迁移（新增两个可空模板字段）。使用高级分析师账号进入报告模板编辑页，点击右上角齿轮，在「关系图谱识别引擎配置」浮层中选择「本体指引1.0」及适用配置，点击「保存引擎设置」；选择「文档结构解析＋本体指引」可切回。V1/V2 均可设置，不受草稿/发布状态影响；静态演示模板不适用。
2. 指定模板选择 Word，检查上传/刷新不启动任务。
3. 显式识别，展开节点/属性，逐项核对出处。
4. 报告中心选相同模板/源，确认 execution ID 相同，无重复派发。
5. 正常模板继续当前内核，历史报告仍可读，owner 隔离。

当前首个内置 profile 是 `cmc_baseline_v1`，仅支持根类 `https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport`，来自 `b6f9bd4e55822e20dce0a2ef0fa8f6d9fdd472c1`。它支持产品基本性质、设备/残留/共线参数 Profile，以及工艺、生产计划、风险原文、清洗、储存、降解 Finder；不补充外部车间/设备档案，不执行 PDE 计算或报告生成。其他根类须按实际演示需求增加内置 profile，不能把配置路径当作脚本入口。

页面设置按当前模板修订持久保存，优先于部署绑定文件；新修订不继承，需单独设置。保存不启动/取消任务，也不清除结果；切回同一引擎可查看其仍有效的已有结果。并发编辑冲突时使用「刷新设置」读取最新配置后再保存。

未在页面保存设置的模板继续使用服务端 `TEMPLATE_FINDER_CONFIG_PATH` 指定的绑定 JSON；默认文件为空。以下保留为部署预配置方式：

```json
{
  "bindings": [{
    "template_id": "填写已经确认的模板修订 UUID",
    "root_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport",
    "finder_profile": "cmc_baseline_v1"
  }]
}
```

这是配置格式示例，不是可直接启用的绑定。配置按请求读取，修改现有绑定文件后不需要为此重启服务；首次部署代码与环境变量按现有部署流程进行。`TEMPLATE_FINDER_STORAGE_DIR` 默认为 `backend/data/template-finder`，每个内部作业只有 `latest.json`。新模板修订不会继承旧 ID 的 Finder 绑定。

模板页 V2 源文档区、V1 只读页 Finder 源文档区均支持显示；报告中心深链携带 `template_id`。没有唯一登记关联时不自动选 Finder；多个登记关联需要选择模板。普通报告中心创建接口的显式模板参数为 `?template_id=<UUID>`。

工程命令和实际结果见 [validation.md](validation.md)。已部署至现有开发环境并验证页面选择器；未绑定实际模板，尚未执行真实演示原件验收。
