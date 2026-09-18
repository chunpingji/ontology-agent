import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

function moduleAt(path, extra = {}) {
  const source = readFileSync(new URL(path, import.meta.url), "utf8");
  const exports = {};
  vm.runInContext(ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText, vm.createContext({ exports, process: { env: {} }, Headers, URLSearchParams, ...extra }));
  return exports;
}

test("Finder artifacts from different inputs or executions never pair", () => {
  const { finderArtifactsMatch } = moduleAt("../src/lib/template-finder.ts");
  const graph = { template_id: "template", source_job_id: "source", execution_id: "run",
    document_hash: "hash", structure_hash: "structure", parser_version: "4", analysis_id: "analysis" };
  assert.equal(finderArtifactsMatch(graph, { ...graph }), true);
  for (const key of Object.keys(graph)) {
    assert.equal(finderArtifactsMatch(graph, { ...graph, [key]: "different" }), false, key);
  }
  assert.equal(finderArtifactsMatch(graph, undefined), false);
  assert.equal(finderArtifactsMatch({ ...graph, execution_id: null }, { ...graph, execution_id: null }), false);
});

test("Finder GET clients preserve cancellation and create only through explicit POST", async () => {
  const calls = [];
  const api = moduleAt("../src/lib/api.ts", { fetch: async (url, init) => {
    calls.push({ url, ...init });
    return new Response(JSON.stringify({ execution_id: "run" }));
  } });
  const signal = new AbortController().signal;
  await api.getAstTemplate("template", signal);
  await api.getTemplateFinder("template", "source", signal);
  await api.getTemplateFinderGraph("template", "source", "run", signal);
  await api.getTemplateFinderSource("template", "source", "run", signal);
  await api.getRecognitionContext("urn:doc", "template", signal);
  assert(calls.every((call) => (!call.method || call.method === "GET") && call.signal === signal));
  await api.startTemplateFinder("template", "source", { request_key: "key", expected_execution_id: "run" });
  const post = calls.at(-1);
  assert.equal(post.method, "POST");
  assert.deepEqual(JSON.parse(post.body), { request_key: "key", expected_execution_id: "run" });
  await api.createReportDocumentRun("urn:doc", "normal", signal, "template");
  assert(calls.at(-1).url.includes("template_id=template"));
});
