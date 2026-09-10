# 新方案测试指标

按2026-09-10用户后续要求，仅将新方案的正确性、稳定性和绝对运行指标作为验收。
相对原方案的提升与体感不在本报告裁决范围内。

- `state-replay.json`：138个冻结状态的新格式恢复、累计逻辑体积与服务读取指标。
- `final-boundary.json`：最后写锁修复后的三制品无模型恢复补验。
- `scheduler.json`：新调度器6000个逻辑机会的规模诊断。
- `batch-comparison.json`：新批量接口在batch4/8/16下的实际运行、输入/分词/排名一致性；
  文件名沿用原实验输出，不含旧实现运行，不能据其时长评价相对原方案提速。
- `batch-evidence-manifest.json`：原始batch实验文件哈希；含原文输入的文件仅留在本机临时目录。
- `browser-result.json`：真实组件使用模拟API的9项行为检查与请求记录。
- `template-new.json`：新方案8任务真实模板诊断、绝对耗时和已知模型费用小计；完整模板未通过。
- `template-validation.json`：同身份恢复、任务不重做、零新增模型请求、候选问题及费用缺测说明。
- `regression-files.txt`：449通过/9项PG待环境的广泛定向测试清单；PG后续另有实际通过结果。

完整范围、复现入口和未完成门见[performance-validation.md](../performance-validation.md)。
原文、向量、SQLite、真实源作业和模型权重不加入此目录。原始临时证据路径见上级报告。
