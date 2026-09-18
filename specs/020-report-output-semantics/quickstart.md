# Verification

1. backend/.venv/bin/pytest：新编译/解析/签署/API契约测试，再运行 reporting、fact_commit 和规则迁移回归。
2. frontend：node --test tests/*.test.mjs，npx tsc --noEmit，定向ESLint，npm run build。
3. PYTHONPATH=backend backend/.venv/bin/python scripts/audit_report_semantics.py：检查生产AST/配置及调用边；残留不清零不声明退役。
4. 注册服务端本体/参数/工作流/规则/策略，创建V2草稿并编译发布。独立准备真实来源、审核、提交；生成后改变来源，旧inputs/coverage/output/download保持hash。
5. 固定正文并真实审核，账户重认证签署；并发旧revision为409；封装后原输入/body不变。部分签署只可草稿，缺材料/审核/签名阻止正式用途。
6. v18迁移核32项、3节6组/origin和原hash。实际语义审核、模型金标和部署p95写validation.md，不以fixture冒充。

7. 本地生产构建浏览器验证：`npm run start -- -p 3107` 后，以外置 Playwright 运行 `tests/reporting-browser.mjs`；API 全部为合成拦截。配置和最终结果见 [validation.md](validation.md)。

8. 样例创建回归：选择 CMC 报告、上传输出样例并点击「进入模板定义」，等待模板及样例保存后直接进入已保存模板的定义编辑页。立即返回列表或刷新仍能找到草稿；后续修改点击「保存新修订」。创建失败保留向导，附件失败重试不重复创建。选择普通 Word 源文档不请求旧识别接口；选择模板专用源文档仍能打开证据面板。用 `tests/template-creation-browser.mjs` 检查合成浏览器契约，用 `backend/tests/test_api/test_template_sample_creation.py` 检查隔离数据库及真实 Word 文件保存；当前语义及记录见 [template-entry-validation.md](template-entry-validation.md)。
9. 保存等待回归：同一浏览器脚本使用 16 MB 合成解析结构，确认创建请求小于 5 KB、附件仅返回 204；加速模拟 180 秒附件超时并重试，创建成功次数仍为 1。阻断编辑路由响应后应显示「模板和输出样例已保存」，可以关闭弹窗或通过「打开已保存的模板」进入编辑；记录见 [template-save-latency-validation.md](template-save-latency-validation.md)。
