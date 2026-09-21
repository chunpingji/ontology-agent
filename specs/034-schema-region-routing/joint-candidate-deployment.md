# 联合候选方案主服务部署（2026-09-21）

主后端已于 **09:55:58 UTC** 重启并加载联合候选及本体硬编码清理后的代码。
本次沿用主服务源码挂载方式，无依赖或镜像变更；前端保持既有开发模式。

执行命令：

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml restart --timeout 30 backend
```

- 部署前：活动文档运行、有效文档/标注租约和在途模型请求均为 0。
  旧抽取作业中的运行标记均创建于 2026-09-05 或更早，没有对应活动标注执行，未改写这些记录。
- 部署后：主入口 `/api/health` 为 200、`status=ok`；数据库 revision 与容器内
  Alembic head 均为 `0041_template_engine`。
- 容器内实际配置/编译检查：`graph_phase=evidence_review`；八类型区域不再被强制为
  entity-only；同一卡可编译属性与关系菜单，核验输入支持属性候选。指定 CMC 常量、
  类型收窄配置与清洗专用模块均已移除。发现/核验输出上限保持 8192/16384。
- 浏览器通过主入口 `/analysis?tab=graph-analysis` 读取及刷新暂停运行
  `b915fc1d-e4d7-4fa8-976e-038805ce9dcd`：图谱 Tab 可见且选中，两次目标图 GET 均为 200，
  观测 API 全部 200、无非 GET 请求、无页面运行异常。控制台仍记录一条资源 404，
  原始浏览器记录保留于 `/tmp/ontology-joint-main-deploy-20260921/`。
- 部署后活动运行和模型请求仍为 0；上述运行保持 `paused`、revision 420。

本次未新建真实模型运行，未更新旧运行的冻结配置。页面可用及编译检查不代表真实报告
质量或速度评测完成。此前工程验证见 [联合候选工程验收](quickstart.md#联合候选与组合证据工程验收2026-09-21)。
