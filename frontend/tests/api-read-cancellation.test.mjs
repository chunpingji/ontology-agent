import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

const requests = [
  ["document preview", (api, signal) => api.getAnnotatedDocument("job", false, signal)],
  ["evidence", (api, signal) => api.getJobEvidence("job", signal)],
  ["evidence coverage", (api, signal) => api.getEvidenceCoverage("job", "template", signal)],
  ["AST coverage", (api, signal) => api.getAstCoverage("job", "template", signal)],
];

for (const [name, request] of requests) {
  test(`${name} forwards cancellation to fetch`, async () => {
    const controller = new AbortController();
    const api = {};
    const context = vm.createContext({
      exports: api, process: { env: {} },
      fetch: (_url, options) => {
        assert.equal(options.signal, controller.signal);
        return new Promise((_resolve, reject) => {
          options.signal.addEventListener("abort", () => reject(options.signal.reason));
        });
      },
    });
    vm.runInContext(compiled, context);
    const pending = request(api, controller.signal);
    controller.abort(new Error("document selection changed"));
    await assert.rejects(pending, /document selection changed/);
  });
}
