import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const source = readFileSync(new URL(
  "../src/components/analysis/document-analysis-progress.tsx",
  import.meta.url,
), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS,
  target: ts.ScriptTarget.ES2022,
  jsx: ts.JsxEmit.ReactJSX,
} }).outputText;
const exports = {};
const primitive = (tag) => function Primitive({ children, ...props }) {
  return React.createElement(tag, props, children);
};

vm.runInNewContext(compiled, {
  exports,
  require(name) {
    if (name === "lucide-react") return new Proxy({}, { get: () => primitive("svg") });
    if (name === "@/components/ui/badge") return { Badge: primitive("span") };
    if (name === "@/lib/utils") return { cn: (...values) => values.filter(Boolean).join(" ") };
    if (name === "@/lib/api") return {};
    return require(name);
  },
});

const run = {
  progress: {
    tasks_attempted: 24,
    model_calls: 12,
    model_calls_reserved: 14,
    model_calls_unresolved: 2,
    records_planned: 64,
    records_examined: 18,
    records_incomplete: 2,
    records_unattempted: 44,
    decisions: { supported: 8, unsupported: 3, undetermined: 2, prerequisite_failed: 0 },
    record_discovery: {
      policy: "semantic-record-discovery-v1",
      mode: "semantic",
      reading_groups: 60,
      ranked_groups: 47,
      remaining_pairs: 72,
      admitted_pairs: 22,
      unselected_pairs: 26,
      unselected_groups: 13,
      routing_cards: 12,
      metadata_nodes: 27,
      routed_groups: 47,
      unrouted_groups: 13,
      selected_regions: 13,
    },
  },
};

test("current document progress renders schema routing, candidate work and verification", () => {
  const html = renderToStaticMarkup(React.createElement(
    exports.DocumentAnalysisProgressPanel,
    { run },
  ));

  assert.match(html, /本体引导识别进度/);
  assert.match(html, /根关系卡片[\s\S]*12/);
  assert.match(html, /元数据节点[\s\S]*27/);
  assert.match(html, /选中区域[\s\S]*13/);
  assert.match(html, /已路由原文组[\s\S]*47 \/ 60/);
  assert.match(html, /13 组未命中 Schema 区域/);
  assert.match(html, /已调度[\s\S]*22/);
  assert.match(html, /待调度[\s\S]*72/);
  assert.match(html, /26 个原文组与执行卡组合未入选/);
  assert.match(html, /支持[\s\S]*不支持[\s\S]*未决/);
  assert.match(html, /已预留 14/);
  assert.match(html, /待核实调用 2/);
  assert.doesNotMatch(html, /排序预算限制|启用排序预算|禁用排序预算/);
});

test("fully routed documents omit the zero-unrouted warning", () => {
  const fullyRouted = {
    ...run,
    progress: {
      ...run.progress,
      record_discovery: {
        ...run.progress.record_discovery,
        routed_groups: 60,
        unrouted_groups: 0,
      },
    },
  };
  const html = renderToStaticMarkup(React.createElement(
    exports.DocumentAnalysisProgressPanel,
    { run: fullyRouted },
  ));

  assert.match(html, /已路由原文组[\s\S]*60 \/ 60/);
  assert.doesNotMatch(html, /未命中 Schema 区域/);
});
