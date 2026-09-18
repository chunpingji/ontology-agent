import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const compiled = ts.transpileModule(readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

function setup(fetchImpl) {
  const api = {};
  const timers = new Map();
  let timerId = 0;
  class TestFormData {
    values = new Map();
    append(key, value) { this.values.set(key, value); }
  }
  vm.runInContext(compiled, vm.createContext({
    exports: api, process: { env: {} }, FormData: TestFormData, Headers, AbortController,
    fetch: fetchImpl,
    setTimeout(callback, delay) { timers.set(++timerId, { callback, delay }); return timerId; },
    clearTimeout(id) { timers.delete(id); },
  }));
  return { api, timers };
}

const response = { content_json: { type: "doc", content: [] }, plain_text: "样例" };
const ok = () => ({ ok: true, status: 200, text: async () => JSON.stringify(response) });
const untilAborted = (signal) => new Promise((_resolve, reject) => {
  if (signal.aborted) reject(signal.reason);
  else signal.addEventListener("abort", () => reject(signal.reason), { once: true });
});

test("sample parse preserves authenticated multipart and clears timeout on success", async () => {
  const file = { name: "sample.docx" };
  const { api, timers } = setup(async (url, options) => {
    assert.equal(url, "/api/ast-templates/parse-sample");
    assert.equal(options.method, "POST");
    assert.equal(options.body.values.get("file"), file);
    assert.equal(new Headers(options.headers).get("X-User"), "analyst");
    assert.equal(new Headers(options.headers).has("Content-Type"), false);
    return ok();
  });
  assert.equal((await api.parseSample(file)).plain_text, response.plain_text);
  assert.equal(timers.size, 0);
});

test("changing the sample cancels the old request and a retry remains usable", async () => {
  let requests = 0;
  const { api, timers } = setup(async (_url, { signal }) => {
    requests += 1;
    return requests === 1 ? untilAborted(signal) : ok();
  });
  const controller = new AbortController();
  const pending = api.parseSample({}, controller.signal);
  controller.abort(new Error("selection changed"));
  await assert.rejects(pending, /selection changed/);
  assert.equal(timers.size, 0);
  assert.equal((await api.parseSample({})).plain_text, response.plain_text);
});

test("timeout aborts a stalled upload and returns a retryable user message", async () => {
  const { api, timers } = setup((_url, { signal }) => untilAborted(signal));
  const pending = api.parseSample({});
  const timer = [...timers.values()][0];
  assert.equal(timer.delay, 180_000);
  timer.callback();
  await assert.rejects(pending, /解析超时.*重试/);
  assert.equal(timers.size, 0);
});

test("timeout covers a response body that stalls after HTTP headers", async () => {
  let bodyStarted;
  const started = new Promise((resolve) => { bodyStarted = resolve; });
  const { api, timers } = setup(async (_url, { signal }) => ({
    ok: true, status: 200,
    text() { bodyStarted(); return untilAborted(signal); },
  }));
  const pending = api.parseSample({});
  await started;
  [...timers.values()][0].callback();
  await assert.rejects(pending, /解析超时/);
  assert.equal(timers.size, 0);
});

test("sample save requests an authenticated empty acknowledgement without parsing a preview", async () => {
  const file = { name: "output.docx" };
  const { api, timers } = setup(async (url, options) => {
    assert.equal(url, "/api/ast-templates/saved-template/sample?include_content=false");
    assert.equal(options.method, "POST");
    assert.equal(options.body.values.get("file"), file);
    assert.equal(new Headers(options.headers).get("X-User"), "analyst");
    assert.equal(new Headers(options.headers).has("Content-Type"), false);
    return { ok: true, status: 204, text() { assert.fail("204 must not parse sample content"); } };
  });
  assert.equal(await api.saveTemplateSample("saved-template", file), undefined);
  assert.equal(timers.size, 0);
});

for (const action of ["create", "sample"]) {
  test(`${action} save times out without automatic resubmission or claiming a rollback`, async () => {
    let calls = 0;
    const { api, timers } = setup((_url, { signal }) => { calls += 1; return untilAborted(signal); });
    const pending = action === "create" ? api.createAstTemplate({ name: "draft", schema_json: {} })
      : api.saveTemplateSample("saved-template", {});
    const timer = [...timers.values()][0];
    assert.equal(timer.delay, 180_000);
    timer.callback();
    await assert.rejects(pending, /保存等待超时.*服务端可能仍在处理.*勿重复创建/);
    assert.equal(calls, 1);
    assert.equal(timers.size, 0);
  });
}

test("sample save reports a server rejection and clears its timer", async () => {
  const { api, timers } = setup(async () => ({ ok: false, status: 503, text: async () => "upload failed" }));
  await assert.rejects(api.saveTemplateSample("saved-template", {}), /503.*upload failed/);
  assert.equal(timers.size, 0);
});
