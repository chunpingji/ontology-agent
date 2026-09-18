import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const compiled = ts.transpileModule(readFileSync(new URL("../src/lib/extraction-capabilities.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const api = {};
vm.runInNewContext(compiled, { exports: api });

test("unknown and retired Word sources do not permit legacy reads or recognition", () => {
  for (const job of [undefined, {}, { source_type: "word" }, { source_type: "word", source_mode: "unknown" }]) {
    assert.equal(api.extractionCapabilities(job).preview, false);
    assert.equal(api.extractionCapabilities(job).recognition, false);
  }
});

test("repository Word allows preview only; template Word and Excel retain recognition", () => {
  const repository = api.extractionCapabilities({ source_type: " Word ", source_mode: "doc_repo_preview" });
  assert.equal(repository.preview, true);
  assert.equal(repository.recognition, false);
  for (const job of [{ source_type: "word", source_mode: "template_default" }, { source_type: "excel" }]) {
    assert.equal(api.extractionCapabilities(job).preview, true);
    assert.equal(api.extractionCapabilities(job).recognition, true);
  }
});
