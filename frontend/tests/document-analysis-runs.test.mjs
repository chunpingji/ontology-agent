import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const apiSource = readFileSync(
  new URL("../src/lib/api.ts", import.meta.url),
  "utf8",
);
const panelSource = readFileSync(
  new URL(
    "../src/components/analysis/document-analysis-panel.tsx",
    import.meta.url,
  ),
  "utf8",
);
const graphSource = readFileSync(
  new URL(
    "../src/components/analysis/document-relationship-graph.tsx",
    import.meta.url,
  ),
  "utf8",
);
const pageSource = readFileSync(
  new URL("../src/app/(dashboard)/analysis/page.tsx", import.meta.url),
  "utf8",
);

const compiled = ts.transpileModule(apiSource, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;

class TestFormData {
  values = new Map();
  append(key, value) {
    this.values.set(key, value);
  }
  get(key) {
    return this.values.get(key);
  }
}

function response(payload = {}) {
  return {
    ok: true,
    status: 200,
    text: async () => JSON.stringify(payload),
  };
}

function loadApi(fetchImpl, extraGlobals = {}) {
  const exports = {};
  vm.runInContext(
    compiled,
    vm.createContext({
      exports,
      process: { env: {} },
      fetch: fetchImpl,
      FormData: TestFormData,
      Headers,
      URLSearchParams,
      ...extraGlobals,
    }),
  );
  return exports;
}

test("the retired synchronous Word client and endpoint are absent", () => {
  assert.doesNotMatch(apiSource, /analyzeWordDocument/);
  assert.doesNotMatch(apiSource, /document-analysis\/word/);
  assert.doesNotMatch(
    panelSource,
    /即时分析|页面内临时分析|关闭页面后结果即丢弃/,
  );
  assert.doesNotMatch(pageSource, /Word 即时结构分析/);
});

test("create submits the complete persistent-run multipart contract", async () => {
  const requests = [];
  const api = loadApi(async (url, options) => {
    requests.push({ url, options });
    return response({ recognition_run_id: "run-1" });
  });
  const signal = new AbortController().signal;
  const file = { name: "report.docx" };

  await api.createDocumentAnalysisRun(
    file,
    "https://ontology.example/CMCReport",
    "browser-request-1",
    "generate_summary",
    signal,
  );

  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/document-analysis/runs");
  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.signal, signal);
  assert.equal(requests[0].options.body.get("file"), file);
  assert.equal(
    requests[0].options.body.get("root_class_iri"),
    "https://ontology.example/CMCReport",
  );
  assert.equal(
    requests[0].options.body.get("request_key"),
    "browser-request-1",
  );
  assert.equal(
    requests[0].options.body.get("metadata_mode"),
    "generate_summary",
  );
});

test("status, metadata, graph and source functions are read-only GETs", async () => {
  const requests = [];
  const api = loadApi(async (url, options) => {
    requests.push({ url, options });
    return response({});
  });
  const signal = new AbortController().signal;
  await api.getDocumentAnalysisRun("run / one", signal);
  await api.getDocumentAnalysisMetadata("run / one", signal);
  await api.getDocumentAnalysisGraph("run / one", "all_candidates", signal);
  await api.getDocumentAnalysisSource("run / one", "selection:row/1", signal);

  assert.deepEqual(
    requests.map(({ url }) => url),
    [
      "/api/document-analysis/runs/run%20%2F%20one",
      "/api/document-analysis/runs/run%20%2F%20one/metadata",
      "/api/document-analysis/runs/run%20%2F%20one/graph?projection=all_candidates",
      "/api/document-analysis/runs/run%20%2F%20one/source?selection_ref=selection%3Arow%2F1",
    ],
  );
  for (const { options } of requests) {
    assert.equal(options.method, undefined);
    assert.equal(options.signal, signal);
  }
});

test("durable run events use EventSource reconnect ids and reject cross-run frames", () => {
  const seen = [];
  class TestEventSource {
    static instance;
    listeners = new Map();
    closed = false;
    constructor(url) {
      this.url = url;
      TestEventSource.instance = this;
    }
    addEventListener(type, listener) {
      this.listeners.set(type, listener);
    }
    removeEventListener(type) {
      this.listeners.delete(type);
    }
    close() {
      this.closed = true;
    }
    emit(type, data) {
      this.listeners.get(type)?.({ data: JSON.stringify(data) });
    }
  }
  const api = loadApi(async () => response({}), {
    EventSource: TestEventSource,
  });
  const unsubscribe = api.subscribeDocumentAnalysisEvents(
    "run / one",
    (event, type) => seen.push([event.event_head, type]),
  );
  const source = TestEventSource.instance;

  assert.match(
    source.url,
    /^\/api\/document-analysis\/runs\/run%20%2F%20one\/events\?/,
  );
  assert.match(source.url, /x_user=analyst/);
  assert.match(source.url, /x_role=senior_analyst/);
  source.emit("progress", {
    contract_version: "document-analysis-runs-v1",
    recognition_run_id: "another-run",
    event_head: 4,
  });
  source.emit("artifact", {
    contract_version: "document-analysis-runs-v1",
    recognition_run_id: "run / one",
    event_head: 5,
    status: "running",
  });
  assert.deepEqual(seen, [[5, "artifact"]]);
  assert.equal(source.closed, false);
  unsubscribe();
  assert.equal(source.closed, true);
  assert.equal(source.listeners.size, 0);
});

test("durable event subscriptions close in every quiescent run state", () => {
  const instances = [];
  class TestEventSource {
    listeners = new Map();
    closed = false;
    constructor(url) {
      this.url = url;
      instances.push(this);
    }
    addEventListener(type, listener) {
      this.listeners.set(type, listener);
    }
    removeEventListener(type) {
      this.listeners.delete(type);
    }
    close() {
      this.closed = true;
    }
    emit(type, data) {
      this.listeners.get(type)?.({ data: JSON.stringify(data) });
    }
  }
  const api = loadApi(async () => response({}), {
    EventSource: TestEventSource,
  });
  const quiescent = [
    "paused",
    "retryable_failure",
    "blocked_dependency",
    "finished",
    "cancelled",
    "deleted",
    "expired",
  ];

  for (const [index, status] of quiescent.entries()) {
    const seen = [];
    const unsubscribe = api.subscribeDocumentAnalysisEvents(
      "run-quiet",
      (event) => seen.push(event.status),
    );
    const source = instances[index];
    source.emit("run_state", {
      contract_version: "document-analysis-runs-v1",
      recognition_run_id: "run-quiet",
      event_head: index + 1,
      status,
    });
    assert.deepEqual(seen, [status]);
    assert.equal(
      source.closed,
      true,
      `${status} must stop native EventSource reconnects`,
    );
    assert.equal(source.listeners.size, 0);
    unsubscribe();
  }
});

test("event subscription policy stays open only for autonomously progressing states", () => {
  const api = loadApi(async () => response({}));
  for (const status of ["queued", "running", "deleting"]) {
    assert.equal(
      api.shouldSubscribeDocumentAnalysisEvents(status),
      true,
      status,
    );
  }
  for (const status of [
    null,
    "paused",
    "retryable_failure",
    "blocked_dependency",
    "finished",
    "cancelled",
    "deleted",
    "expired",
  ]) {
    assert.equal(
      api.shouldSubscribeDocumentAnalysisEvents(status),
      false,
      String(status),
    );
  }
});

test("a resume receipt makes the same run eligible for a fresh event subscription", () => {
  const api = loadApi(async () => response({}));
  const paused = {
    recognition_run_id: "run-resume",
    run_revision: 4,
    event_head: 9,
    artifact_revision: 2,
    status: "paused",
    stage: "extracting",
    available_actions: ["resume", "cancel", "delete"],
  };
  const resumed = api.mergeDocumentAnalysisControlReceipt(paused, {
    recognition_run_id: "run-resume",
    run_revision: 5,
    event_head: 10,
    artifact_revision: 2,
    status: "queued",
    stage: "extracting",
    operation: "resume",
    operation_status: "accepted",
    available_actions: ["pause", "cancel", "delete"],
  });

  assert.equal(api.shouldSubscribeDocumentAnalysisEvents(paused.status), false);
  assert.equal(resumed.status, "queued");
  assert.equal(resumed.run_revision, 5);
  assert.equal(api.shouldSubscribeDocumentAnalysisEvents(resumed.status), true);
  assert.match(panelSource, /\[activeRunId, eventStreamShouldConnect\]/);
  assert.match(
    panelSource,
    /mergeDocumentAnalysisControlReceipt\(previous, receipt\)/,
  );
});

test("lifecycle writes carry revision CAS and operation idempotency keys", async () => {
  const requests = [];
  const api = loadApi(async (url, options) => {
    requests.push({ url, options });
    return response({});
  });

  await api.controlDocumentAnalysisRun(
    "run-2",
    "pause",
    8,
    "pause-key",
    "检查当前结果",
  );
  await api.deleteDocumentAnalysisRun("run-2", 9, "delete-key");

  assert.equal(requests[0].url, "/api/document-analysis/runs/run-2/pause");
  assert.equal(requests[0].options.method, "POST");
  assert.deepEqual(JSON.parse(requests[0].options.body), {
    expected_revision: 8,
    request_key: "pause-key",
    reason: "检查当前结果",
  });
  assert.equal(
    requests[1].url,
    "/api/document-analysis/runs/run-2?expected_revision=9&request_key=delete-key",
  );
  assert.equal(requests[1].options.method, "DELETE");
});

test("the panel has exactly two result tabs and restores by documentRun", () => {
  assert.equal((panelSource.match(/<TabsTrigger\b/g) || []).length, 2);
  assert.match(panelSource, /value="metadata"[^>]*>[\s\S]*?分层元数据/);
  assert.match(panelSource, /value="graph"[^>]*>[\s\S]*?关系图谱/);
  assert.match(panelSource, /searchParams\.get\("documentRun"\)/);
  assert.match(panelSource, /params\.set\("documentRun", runId\)/);
  assert.match(
    panelSource,
    /disabled=\{!sourceFile \|\| !rootClassIri \|\| isCreating\}/,
  );
  assert.match(panelSource, /Promise\.allSettled\(\[/);
  assert.match(panelSource, /subscribeDocumentAnalysisEvents\(runId/);
  assert.match(
    panelSource,
    /event\.event_head <= eventHeadRef\.current\.value/,
  );
});

test("graph evidence is role-specific and only opaque selection refs cross the API", () => {
  for (const role of [
    "subject",
    "object",
    "value",
    "predicate_bridge",
    "condition",
    "counterevidence",
  ]) {
    assert.match(apiSource, new RegExp(`${role}: string\\[\\]`));
  }
  assert.match(graphSource, /关系方向/);
  assert.match(graphSource, /对象→主体/);
  assert.match(graphSource, /onSelectionRef\(selectionRef\)/);
  assert.match(
    panelSource,
    /getDocumentAnalysisSource\(runId, selectionRef, controller\.signal\)/,
  );
  assert.doesNotMatch(panelSource, /getDocumentAnalysisSource\([^\n]*anchor/);
});
