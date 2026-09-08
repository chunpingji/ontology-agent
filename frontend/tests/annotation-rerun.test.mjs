import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const compiled = ts.transpileModule(
  readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8"),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
).outputText;

function apiWithResponse(response, templateId) {
  const api = {};
  vm.runInContext(compiled, vm.createContext({
    exports: api, process: { env: {} },
    fetch: async (url, options) => {
      assert.equal(url, "/api/extraction/jobs/source-job/annotation/rerun" +
        (templateId ? `?template_id=${encodeURIComponent(templateId)}` : ""));
      assert.equal(options.method, "POST");
      return response;
    },
  }));
  return api;
}

for (const [status, detail] of [
  [409, "该作业正在识别，请等待当前任务完成"],
  [422, "源文档不可用，无法重新识别"],
  [404, "作业不存在"],
  [401, "未认证：缺少有效令牌"],
]) {
  test(`rerun preserves the actual ${status} error`, async () => {
    const api = apiWithResponse({ ok: false, status, json: async () => ({ detail }) });
    await assert.rejects(api.rerunAnnotation("source-job"), { message: detail });
  });
}

test("rerun handles a non-JSON proxy error without displaying HTML", async () => {
  const api = apiWithResponse({ ok: false, status: 502, json: async () => {
    throw new SyntaxError("Unexpected token <");
  } });
  await assert.rejects(api.rerunAnnotation("source-job"), { message: "重新识别请求失败（502）" });
});

test("a successful rerun is accepted", async () => {
  const receipt = { job_id: "source-job", run_id: "execution-1", status: "restarted" };
  const result = await apiWithResponse({ ok: true, status: 202, json: async () => receipt }).rerunAnnotation("source-job");
  assert.deepEqual(result, receipt);
});

test("rerun carries the active template so its fact paths can be prioritized", async () => {
  await apiWithResponse({ ok: true, json: async () => ({ run_id: "execution-2" }) }, "active-template")
    .rerunAnnotation("source-job", "active-template");
});
