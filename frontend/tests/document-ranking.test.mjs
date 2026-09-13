import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const reasons = {};
vm.runInNewContext(ts.transpileModule(
  readFileSync(new URL("../src/lib/document-analysis.ts", import.meta.url), "utf8"),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
).outputText, { exports: reasons });
const source = readFileSync(new URL("../src/components/analysis/document-relationship-graph.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
                     jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const exports = {};
vm.runInNewContext(compiled, {
  exports,
  require(name) {
    if (name.startsWith("@/components/ui/")) {
      return new Proxy({}, { get: () => ({ children }) => React.createElement("div", null, children) });
    }
    if (name === "@/lib/utils") return { cn: (...values) => values.filter(Boolean).join(" ") };
    if (name === "@/lib/document-analysis") return reasons;
    return require(name);
  },
  fetch() { assert.fail("rendering ranking diagnostics must never issue requests"); },
});

function render(artifact, runStatus, rankingBudgetEnabled) {
  return renderToStaticMarkup(React.createElement(exports.DocumentRelationshipGraph, {
    artifact, runStatus, rankingBudgetEnabled, projection: "effective_affirmed", selectedSelectionRef: null,
    onProjectionChange() { assert.fail("render caused projection mutation"); },
    onSelectionRef() { assert.fail("render caused source request"); },
  }));
}

function renderTemplateSummary(candidatePolicy) {
  const template = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(
    "../src/components/analysis/template-document-graph-panel.tsx", import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX },
  }).outputText, {
    exports: template,
    require(name) {
      if (name === "@/components/ui/use-document-tree") {
        return { useDocumentTree: () => ({ getItems: () => [] }) };
      }
      if (name.startsWith("@/components/ui/")) {
        return new Proxy({}, { get: () => ({ children }) => React.createElement("div", null, children) });
      }
      if (name === "@/lib/api") return { getIdentity: () => ({ username: "analyst", role: "senior_analyst" }) };
      if (name === "@/lib/document-analysis") return reasons;
      return require(name);
    },
  });
  const coverage = { candidate_policy: candidatePolicy, records_planned: 4,
    records_examined: 4, records_incomplete: 0, records_unattempted: 0, subjects: [] };
  return renderToStaticMarkup(React.createElement(template.TemplateDocumentGraphPanel, { model: {
    run: { status: "finished", available_actions: [], progress: { ...coverage,
      tasks_attempted: 4, model_calls: 4 } },
    graph: { entities: [], relationships: [], properties: [], coverage,
      unresolved: { undetermined: 0, not_checked: 4 } },
    projection: "effective_affirmed", select() {},
  } }));
}

test("finished empty discoveries are not presented as unfinished candidate verification", () => {
  const html = renderTemplateSummary("sparse-candidates-v1");
  assert.match(html, /本轮识别完成/);
  assert.match(html, /技术未完成 0 项/);
  assert.match(html, /未形成判定（含未发现候选） 4 项/);
  assert.doesNotMatch(html, /未完成核验 4 项/);
  assert.match(renderTemplateSummary(undefined), /未完成核验 4 项/);
});

const ranking = {
  requested_mode: "semantic", actual_modes: ["deterministic"], degraded: true,
  reasons: ["ranking_timeout"], committed_epochs: 1,
  cost: { model_calls: 2, input_pairs: 4, input_tokens: 100, retries: 1, elapsed_seconds: 1.5 },
  epochs: [{ epoch_id: "epoch:one", query_id: "query:one", subject_ref: null,
    predicate_iri: "https://example.test/describes", plan_id: "plan:one",
    requested_mode: "semantic", actual_mode: "deterministic", degraded: true,
    reason: "ranking_timeout", records: [{ record_id: "record:low", rank: 1,
      channels: ["dense"], intent_ranks: { counterevidence: 1 }, raw_scores: { counterevidence: -3 } }] }],
};

test("committed ranking remains readable before the first graph snapshot", () => {
  const html = render({ availability: "pending", entities: [], graph_snapshot: null, ranking });
  assert.match(html, /<details/);
  assert.match(html, /排序已降级/);
  assert.match(html, /ranking_timeout/);
  assert.match(html, /counterevidence: -3/);
  assert.match(html, /原始分数不代表事实正确概率/);
  assert.match(html, /关系图谱尚未形成可读快照/);
});

test("candidate completion separates admitted tasks from unchecked search scope", () => {
  const html = render({ availability: "ready", graph_snapshot: {}, entities: [],
    properties: [], relationships: [], ranking,
    coverage: { candidate_policy: "sparse-candidates-v1", records_planned: 2,
      records_examined: 2, records_incomplete: 0, records_unattempted: 0,
      pending_frontiers: 0, stop_reason: "candidate_search_exhausted", subjects: [],
      retrieval_diagnostics: { records_soft_pruned: 100, records_reactivatable: 0 } },
    unresolved: { undetermined: 1, unsupported: 2 },
  }, "finished");
  assert.match(html, /计划候选任务/);
  assert.match(html, /本轮识别完成说明/);
  assert.match(html, /搜索范围中 100 项/);
  assert.match(html, /未入选原文未核验/);
  assert.match(html, /不表示全文事实已穷尽/);
  assert.match(html, /语义待定/);
  assert.doesNotMatch(html, /未尝试范围中 100|覆盖未完成说明/);
  assert.equal(reasons.DOCUMENT_ANALYSIS_STATUS_LABELS.finished, "本轮识别完成");
  assert.equal(reasons.DOCUMENT_ANALYSIS_STATUS_LABELS.paused, "已暂停");
  assert.equal(reasons.DOCUMENT_ANALYSIS_STATUS_LABELS.retryable_failure, "可恢复失败");
});

test("candidate technical and semantic failures remain visible before completion", () => {
  const html = render({ availability: "partial", graph_snapshot: {}, entities: [],
    properties: [], relationships: [], ranking,
    coverage: { candidate_policy: "sparse-candidates-v1", records_planned: 3,
      records_examined: 1, records_incomplete: 1, records_unattempted: 1,
      pending_frontiers: 0, stop_reason: "task_budget_exhausted", subjects: [] },
    unresolved: { undetermined: 1, unsupported: 0 },
  }, "paused");
  assert.match(html, /技术未完成/);
  assert.match(html, /语义待定/);
  assert.match(html, /覆盖未完成说明/);
  assert.doesNotMatch(html, /本轮识别完成说明/);
});

test("coverage distinguishes retained phase plans from actual dispatch", () => {
  const html = render({ availability: "partial", graph_snapshot: {}, entities: [],
    properties: [], relationships: [], ranking,
    coverage: { records_planned: 9, records_examined: 1, records_incomplete: 0,
      records_unattempted: 8, pending_frontiers: 0, stop_reason: "task_budget_exhausted",
      subjects: [{ subject_ref: { entity_id: "root", revision: 1 },
        predicate_iri: "https://example.test/describes", predicate_label: "描述",
        phase_counts: { phase1: 4, phase2: 5 }, executed_phase_counts: { phase1: 1, phase2: 0 },
        records_examined: 1, records_incomplete: 0, records_unattempted: 8, pending_frontiers: 0 }] },
    unresolved: { undetermined: 0, unsupported: 1 },
  });
  assert.match(html, /第一阶段计划/);
  assert.match(html, /第二阶段实际执行/);
  assert.match(html, /实际执行包含已启动但技术未完成/);
  assert.match(html, /本轮任务预算已用完，仍有原文待检查/);
  assert.match(html, /<summary[^>]*>技术诊断<\/summary>[\s\S]*?task_budget_exhausted/);
  assert.match(html, />4<\/td><td[^>]*>1<\/td><td[^>]*>5<\/td><td[^>]*>0<\/td>/);
});

test("a paused ranking explains the current budget before diagnostics, excluding historical failures", () => {
  const html = render({ availability: "pending", graph_snapshot: null, entities: [], ranking: {
    ...ranking, paused: true, reasons: ["ranking_timeout", "ranking_call_budget_exhausted"],
    epochs: [...ranking.epochs, { ...ranking.epochs[0], epoch_id: "epoch:paused",
      status: "paused", reason: "ranking_call_budget_exhausted", records: [] }],
  } }, "paused");
  const visibleNotice = html.split("<details")[0];
  assert.match(visibleNotice, /语义排序已暂停/);
  assert.match(visibleNotice, /排序请求预算已用完，恢复不会重置已用额度/);
  assert.doesNotMatch(visibleNotice, /超时|ranking_call_budget_exhausted|ranking_timeout/);
  assert.match(html, /技术码：[\s\S]*ranking_call_budget_exhausted/);
});

test("paused timeouts and technical errors are readable without opening diagnostics", () => {
  for (const [code, explanation] of [
    ["ranking_timeout", /排序请求超时/],
    ["ranking_technical_failure:DataError", /排序发生技术故障/],
    ["ranking_token_budget_exhausted", /排序输入 tokens 预算已用完/],
  ]) {
    const html = render({ availability: "pending", graph_snapshot: null, entities: [], ranking: {
      ...ranking, paused: true, reasons: [code], epochs: [],
    } }, "paused");
    assert.match(html.split("<details")[0], explanation);
    assert.ok(html.includes(code), "The exact technical cause remains available for diagnosis");
  }
});

test("incomplete work on a running snapshot is coverage information, preserving counts", () => {
  const html = render({ availability: "partial", graph_snapshot: {}, entities: [],
    properties: [], relationships: [], coverage: { records_planned: 9, records_examined: 4,
      records_incomplete: 2, records_unattempted: 5, pending_frontiers: 3, subjects: [],
      stop_reason: "attempted_incomplete" }, unresolved: { undetermined: 1, unsupported: 0 },
  }, "running");
  assert.match(html, /当前覆盖说明/);
  assert.match(html, /运行仍在继续/);
  assert.match(html, /部分原文已尝试处理，但尚未完成识别或验证/);
  assert.doesNotMatch(html, /停止原因|全文没有关系/);
  assert.match(html, /已检查<\/span><strong[^>]*>4<\/strong>/);
  assert.match(html, /技术未完成<\/span><strong[^>]*>2<\/strong>/);
  assert.match(html, /未尝试<\/span><strong[^>]*>5<\/strong>/);
  assert.match(html, /技术诊断<\/summary>[\s\S]*attempted_incomplete/);
});

test("a resumed run labels paused ranking evidence as its latest submitted snapshot", () => {
  const html = render({ availability: "pending", graph_snapshot: null, entities: [],
    ranking: { ...ranking, paused: true, reasons: ["ranking_timeout"], epochs: [] },
  }, "running");
  assert.match(html.split("<details")[0], /最近提交的排序快照/);
  assert.match(html.split("<details")[0], /运行正在继续/);
  assert.doesNotMatch(html.split("<details")[0], /语义排序已暂停/);
  assert.doesNotMatch(html, /停止原因/);
});

test("disabled ranking budgets expose frozen accounting and mark unaccounted epochs without hiding models", () => {
  const artifact = { availability: "pending", graph_snapshot: null, entities: [], ranking: {
    ...ranking, budget_enabled: false, epochs: [{ ...ranking.epochs[0], budget_accounted: false }],
  } };
  const html = render(artifact, "paused");
  assert.match(html, /排序预算限制：已禁用/);
  assert.match(html, /预算统计已暂停（显示启用期间累计值）/);
  assert.match(html, /排序模型仍可运行/);
  assert.match(html, /从关闭前的累计量继续/);
  assert.match(html, /预算未计账/);
  assert.match(html, /不代表没有模型资源消耗/);
  assert.match(html, /预留 tokens 100/);
  const reenabled = render(artifact, "paused", true);
  assert.match(reenabled, /排序预算限制：已启用/);
  assert.doesNotMatch(reenabled, /预算统计已暂停/);
  assert.match(reenabled, /预算未计账/);
});

test("disabled run control explains that an old budget pause can resume despite the enabled snapshot", () => {
  for (const code of ["ranking_call_budget_exhausted", "ranking_token_budget_exhausted"]) {
    const artifact = { availability: "partial", graph_snapshot: {}, entities: [],
      properties: [], relationships: [], coverage: { records_planned: 2, records_examined: 0,
        records_incomplete: 0, records_unattempted: 2, pending_frontiers: 0, subjects: [],
        stop_reason: "ranking_paused" }, unresolved: { undetermined: 0, unsupported: 0 },
      ranking: { ...ranking, paused: true, budget_enabled: true, reasons: [code], epochs: [] },
    };
    const html = render(artifact, "paused", false);
    const notice = html.split("<details")[0];
    assert.match(notice, /此前因排序预算耗尽暂停；预算限制现已禁用，可显式恢复运行，禁用期间不计账/);
    assert.doesNotMatch(notice, /恢复不会重置已用额度/);
    const coverage = html.slice(html.indexOf("覆盖未完成说明"));
    assert.match(coverage, /预算限制现已禁用，可显式恢复运行/);
    assert.doesNotMatch(coverage, /恢复不会重置已用额度/);
    assert.ok(html.includes(code), "The historical cause remains available for diagnosis");

    const continuing = render(artifact, "running", false).split("<details")[0];
    assert.match(continuing, /此前因排序预算耗尽暂停；预算限制现已禁用，运行正在继续，禁用期间不计账/);
    assert.doesNotMatch(continuing, /可显式恢复运行|恢复不会重置已用额度/);

    const reenabled = render({ ...artifact, ranking: { ...artifact.ranking, budget_enabled: false } },
      "paused", true).split("<details")[0];
    assert.match(reenabled, /预算已用完，恢复不会重置已用额度/);
    assert.doesNotMatch(reenabled, /可显式恢复运行|预算限制现已禁用/);
  }
});

test("disabling the budget preserves explanations of technical pauses", () => {
  const html = render({ availability: "pending", graph_snapshot: null, entities: [], ranking: {
    ...ranking, paused: true, budget_enabled: true,
    reasons: ["ranking_technical_failure:DataError"], epochs: [],
  } }, "paused", false).split("<details")[0];
  assert.match(html, /排序发生技术故障，本轮排序尚未完成/);
  assert.doesNotMatch(html, /此前因排序预算耗尽暂停|可显式恢复运行/);
});
