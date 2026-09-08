# Verification

1. backend/.venv/bin/pytest：新编译/解析/签署/API契约测试，再运行 reporting、fact_commit 和规则迁移回归。
2. frontend：node --test tests/*.test.mjs，npx tsc --noEmit，定向ESLint，npm run build。
3. PYTHONPATH=backend backend/.venv/bin/python scripts/audit_report_semantics.py：检查生产AST/配置及调用边；残留不清零不声明退役。
4. 注册服务端本体/参数/工作流/规则/策略，创建V2草稿并编译发布。独立准备真实来源、审核、提交；生成后改变来源，旧inputs/coverage/output/download保持hash。
5. 固定正文并真实审核，账户重认证签署；并发旧revision为409；封装后原输入/body不变。部分签署只可草稿，缺材料/审核/签名阻止正式用途。
6. v18迁移核32项、3节6组/origin和原hash。实际语义审核、模型金标和部署p95写validation.md，不以fixture冒充。

7. 本地生产构建浏览器验证：`npm run start -- -p 3107` 后，以外置 Playwright 运行 `tests/reporting-browser.mjs`；API 全部为合成拦截。配置和最终结果见 [validation.md](validation.md)。
