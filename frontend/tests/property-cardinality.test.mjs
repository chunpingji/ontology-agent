import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

function loadModule(path, context = {}) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(path, import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, { exports, ...context });
  return exports;
}

const { cardinalityForm, cardinalityPayload, changeMultiplicity, cardinalitySummary } =
  loadModule("../src/lib/property-cardinality.ts");
const domain = "https://example.test/Drug";
const plain = (value) => JSON.parse(JSON.stringify(value));

test("single permits absence until a minimum of one is explicitly configured", () => {
  const single = changeMultiplicity(cardinalityForm(), "single");
  assert.deepEqual(plain(cardinalityPayload(single, domain)), {
    multiplicity: "single", min_cardinality: null, max_cardinality: 1,
  });
  assert.equal(cardinalityPayload({ ...single, min_cardinality: "1" }, domain).min_cardinality, 1);
  assert.equal(cardinalityPayload({ ...single, min_cardinality: "0" }, domain).min_cardinality, 0);
  assert.throws(() => cardinalityPayload({ ...single, min_cardinality: "2" }, domain), /最小数量/);
});

test("leaving single removes its automatic upper bound while preserving the required minimum", () => {
  const source = cardinalityForm({ multiplicity: "single", min_cardinality: 1, max_cardinality: 1 });
  for (const next of ["multiple", "unspecified"]) {
    assert.deepEqual(plain(cardinalityPayload(changeMultiplicity(source, next), domain)), {
      multiplicity: next, min_cardinality: 1, max_cardinality: null,
    });
  }
});

test("multiple does not impose a minimum and preserves a separately configured upper bound", () => {
  const multiple = changeMultiplicity(cardinalityForm(), "multiple");
  assert.deepEqual(plain(cardinalityPayload(multiple, domain)), {
    multiplicity: "multiple", min_cardinality: null, max_cardinality: null,
  });
  const bounded = { ...multiple, min_cardinality: "1", max_cardinality: "4" };
  assert.equal(cardinalityPayload(bounded, domain).max_cardinality, 4);
  const unspecified = changeMultiplicity(bounded, "unspecified");
  assert.deepEqual(plain(cardinalityPayload(unspecified, domain)), {
    multiplicity: "unspecified", min_cardinality: 1, max_cardinality: 4,
  });
});

test("existing numeric limits and nonfunctional legacy properties do not become declared multiple", () => {
  assert.equal(cardinalityForm({ is_functional: false }).multiplicity, "unspecified");
  assert.equal(cardinalityForm({ max_cardinality: 1 }).multiplicity, "unspecified");
  assert.equal(cardinalitySummary({ max_cardinality: 1 }), "未指定 · 最多 1");
  assert.equal(cardinalitySummary({ is_functional: true }), "单值 · 最多 1");
  assert.equal(cardinalitySummary({ multiplicity: "multiple" }), "多值 · 未设上限");
});

test("cleared numeric inputs serialize as null and explicit zero remains zero", () => {
  const old = cardinalityForm({ multiplicity: "multiple", min_cardinality: 1, max_cardinality: 3 });
  assert.deepEqual(plain(cardinalityPayload({ ...old, min_cardinality: "", max_cardinality: "" }, domain)), {
    multiplicity: "multiple", min_cardinality: null, max_cardinality: null,
  });
  assert.equal(cardinalityPayload({ ...old, min_cardinality: "0" }, domain).min_cardinality, 0);
});

test("rejects invalid numeric inputs before any save can turn NaN into null", () => {
  for (const invalid of ["-1", "1.5", "NaN", "Infinity", "no", "9007199254740992"]) {
    for (const key of ["min_cardinality", "max_cardinality"]) {
      assert.throws(() => cardinalityPayload({ ...cardinalityForm(), [key]: invalid }, domain), /非负整数/);
    }
  }
  assert.throws(() => cardinalityPayload({ ...cardinalityForm(), min_cardinality: "3", max_cardinality: "2" }, domain), /不能大于/);
  assert.throws(() => cardinalityPayload({ multiplicity: "multiple", min_cardinality: "", max_cardinality: "1" }, domain), /至少为 2/);
  assert.throws(() => cardinalityPayload({ multiplicity: "single", min_cardinality: "", max_cardinality: "2" }, domain), /必须为 1/);
});

test("class-scoped bounds require a domain while a global single property is allowed", () => {
  assert.doesNotThrow(() => cardinalityPayload(changeMultiplicity(cardinalityForm(), "single"), ""));
  assert.doesNotThrow(() => cardinalityPayload(cardinalityForm(), ""));
  assert.throws(() => cardinalityPayload({ ...cardinalityForm(), min_cardinality: "0" }, ""), /定义域/);
  assert.throws(() => cardinalityPayload({ ...cardinalityForm(), max_cardinality: "3" }, ""), /定义域/);
});

for (const [method, endpoint] of [
  ["updateDataProperty", "data-properties"], ["updateLinkType", "link-types"],
]) {
  test(`${method} transmits all three cleared fields with optimistic version and authentication`, async () => {
    let request;
    const api = loadModule("../src/lib/api.ts", {
      process: { env: {} }, Headers,
      fetch: async (url, options) => {
        request = { url, ...options };
        return { ok: true, status: 200, text: async () => "{}" };
      },
    });
    const oldSingle = cardinalityForm({ multiplicity: "single", max_cardinality: 1 });
    const fields = cardinalityPayload(changeMultiplicity(oldSingle, "unspecified"), domain);
    await api[method]("https://example.test/p", { ...fields, expected_version: 7 });
    assert.equal(request.url, `/api/ontology/${endpoint}/https%3A%2F%2Fexample.test%2Fp`);
    assert.equal(request.method, "PUT");
    assert.equal(new Headers(request.headers).get("X-User"), "analyst");
    assert.deepEqual(JSON.parse(request.body), {
      multiplicity: "unspecified", min_cardinality: null, max_cardinality: null, expected_version: 7,
    });
  });
}
