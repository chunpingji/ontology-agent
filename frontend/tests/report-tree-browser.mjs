// Real ReUI adapter/Headless Tree, React and Tailwind 3; synthetic documents and graphs.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createServer } from "node:http";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { build } = await import(process.env.ESBUILD_MODULE || "esbuild");
const { chromium, expect } = await import(process.env.PLAYWRIGHT_MODULE || "playwright/test");
const output = process.env.REPORT_TREE_OUTPUT || "/tmp/report-tree-browser";
await mkdir(output, { recursive: true });
execFileSync(path.join(frontend, "node_modules/.bin/tailwindcss"),
  ["-i", "src/app/globals.css", "-o", path.join(output, "styles.css"), "--minify"], { cwd: frontend });
const css = await readFile(path.join(output, "styles.css"));
const entry = `
import { StrictMode, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { ReportDocumentOutline } from "@/components/reports/report-word-workspace";
import { TemplateGraphTree } from "@/components/analysis/template-document-graph-panel";
const refs = { subject: ["subject-ref"], object: [], value: [], predicate_bridge: ["bridge-ref"],
  condition: ["condition-ref"], counterevidence: ["counter-ref"] };
const proof = { policy_eligible: true, structural_valid: true, model_supported: true,
  polarity: "affirmed", source_selection_refs: refs };
const node = (id, children = [], level = 1) => ({ node_id: id, heading: id === "chapter" ? "生产信息" : id,
  level, children, source_range: { anchor_block_id: id + "-anchor", start_block_id: id + "-start", end_block_id: id + "-end" } });
function fixture(version, removed, reparented) {
  const ids = ["root", "left", "right", "shared", "tail", "cycle-a", "cycle-b", ...(version ? ["new"] : [])]
    .filter(id => !removed || !["shared", "tail"].includes(id));
  const entities = ids.map(entity_id => ({ entity_id, label: entity_id === "root" ? "报告 " + version : entity_id,
    class_label: "实体", source_selection_refs: [entity_id + "-source"],
    ...(entity_id === "root" ? { predicate_menu: [{kind: "property", predicate_iri: "field", predicate_label: "已有属性"},
      {kind: "property", predicate_iri: "empty", predicate_label: "待检查属性"},
      {kind: "relationship", predicate_iri: "rel", predicate_label: "关系"}] } : {}) }));
  const relationships = [["root", "left"], ["root", "right"], ["left", "shared"], ["right", "shared"],
    ["shared", "tail"], ["tail", "root"], ["cycle-a", "cycle-b"], ["cycle-b", "cycle-a"],
    ...(version ? [["left", "new"]] : [])].filter(([a,b]) => ids.includes(a) && ids.includes(b)
      && !(reparented && a === "left" && b === "shared"))
    .map(([a,b]) => ({ ...proof, candidate_id: a+"-"+b, subject_ref:{entity_id:a}, object_ref:{entity_id:b},
      predicate_iri:"rel", predicate_label:"关系" }));
  return { recognition_run_id:"run", projection:"effective_affirmed", graph_snapshot:{root_ref:{entity_id:"root"}},
    entities, relationships, properties:[{...proof, candidate_id:"value", subject_ref:{entity_id:"root"},
      predicate_iri:"field", predicate_label:"已有属性", raw_value:"17 mg", source_selection_refs:{...refs,value:["value-ref"]}}],
    coverage:{subjects:[{subject_ref:{entity_id:"root"},predicate_iri:"field",records_planned:5,
      records_examined:2,records_incomplete:1,records_unattempted:2}]}, unresolved:{} };
}
function App() {
  const [scope, setScope] = useState(0), [version, setVersion] = useState(0), [removed, setRemoved] = useState(false);
  const [reparented, setReparented] = useState(false);
  const [location, setLocation] = useState(null), [selections, setSelections] = useState([]);
  const chapters = useMemo(() => node("document", [node("chapter", [node("section", [node("subsection", [node("detail", [], 4)], 3)], 2)]),
    node("很长的章节标题用于检查狭窄目录中的自动换行和完整阅读"), ...(version ? [node("new-chapter")] : [])]), [version]);
  const graph = useMemo(() => fixture(version, removed, reparented), [version, removed, reparented]);
  return <><div style={{display:"flex", gap:16, padding:16}}>
    <button onClick={() => setVersion(v => v+1)}>更新结果</button>
    <button onClick={() => setRemoved(true)}>删除共享实体</button>
    <button onClick={() => setReparented(true)}>更新实体归属</button>
    <button onClick={() => setScope(v => v+1)}>切换身份</button>
    <button onClick={() => document.documentElement.classList.toggle("dark")}>切换主题</button></div>
    <div style={{display:"grid",gridTemplateColumns:"200px 420px",gap:24,padding:16,alignItems:"start"}}>
      <ReportDocumentOutline key={"outline-"+scope} tree={chapters} onNavigate={n => setLocation(n.source_range)} />
      <TemplateGraphTree key={"graph-"+scope} graph={graph} select={ref => setSelections(p => [...p,ref])} />
    </div><output id="location">{JSON.stringify(location)}</output><output id="selections">{JSON.stringify(selections)}</output></>;
}
createRoot(document.getElementById("root")).render(<StrictMode><App /></StrictMode>);
`;
const bundle = await build({ stdin: { contents: entry, resolveDir: frontend, loader: "tsx" },
  bundle: true, write: false, platform: "browser", format: "iife", tsconfig: path.join(frontend, "tsconfig.json"),
  define: { "process.env.NODE_ENV": '"development"', "process.env.NEXT_PUBLIC_API_URL": '""' } });
const requests = [], errors = [];
const server = createServer((request, response) => {
  if (request.url === "/") { response.end('<html lang="zh"><head><link rel="stylesheet" href="/styles.css"></head><body><div id="root"></div><script src="/bundle.js"></script></body></html>'); }
  else if (request.url === "/bundle.js") { response.setHeader("Content-Type", "application/javascript"); response.end(bundle.outputFiles[0].text); }
  else if (request.url === "/styles.css") { response.setHeader("Content-Type", "text/css"); response.end(css); }
  else { requests.push(request.url); response.writeHead(404); response.end(); }
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch({ executablePath: process.env.DOCUMENT_BROWSER_CHROME || "/usr/bin/google-chrome", args: ["--no-sandbox"] });
const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
page.on("pageerror", error => errors.push(error.message));
page.on("console", message => { if (message.type() === "error" && !message.text().includes("404")) errors.push(message.text()); });
const check = [], entity = id => page.locator('[data-entity-id="'+id+'"]');
const toggle = row => row.locator('[data-tree-action="toggle"]');
try {
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const outline = page.getByRole("tree", { name:"文档章节", exact:true });
  const chapter = outline.getByRole("treeitem", { name:"生产信息", exact:true });
  await expect(outline.getByRole("treeitem")).toHaveCount(5);
  await toggle(chapter).click();
  await expect(outline.getByRole("treeitem")).toHaveCount(2);
  await expect(page.locator("#location")).toHaveText("null");
  await chapter.click();
  await expect(chapter).toHaveAttribute("aria-selected", "true");
  await expect(chapter).toHaveAttribute("aria-expanded", "false");
  assert.equal(JSON.parse(await page.locator("#location").textContent()).anchor_block_id, "chapter-anchor");
  check.push("outline toggle and exact source range are independent");
  await chapter.press("ArrowRight");
  await expect(outline.getByRole("treeitem")).toHaveCount(5);
  await chapter.press("ArrowDown");
  const section = outline.getByRole("treeitem", { name:"section", exact:true });
  await expect(section).toBeFocused();
  await section.press("Enter");
  await expect(page.locator("#location")).toContainText("section-start");
  assert.equal(JSON.parse(await page.locator("#location").textContent()).start_block_id, "section-start");
  await section.press("ArrowLeft");
  await expect(section).toHaveAttribute("aria-expanded", "false");
  await section.press("ArrowLeft");
  await expect(chapter).toBeFocused();
  check.push("keyboard navigation, collapse, parent focus and Enter activation");

  const graph = page.getByRole("tree", { name:"关系图谱树", exact:true });
  await expect(entity("shared")).toHaveCount(1);
  await expect(entity("tail")).toHaveCount(0);
  await toggle(entity("left")).click();
  await expect(entity("shared")).toHaveCount(0);
  await page.locator('[data-entity-reference="shared"]').click();
  await expect(entity("shared")).toBeFocused();
  await expect(entity("shared")).toHaveAttribute("aria-selected","true");
  await expect(entity("tail")).toHaveCount(1);
  await toggle(entity("tail")).click();
  await page.locator('[data-entity-reference="root"]').click();
  await expect(entity("root")).toBeFocused();
  await expect(entity("root")).toHaveCount(1);
  const unassociated = graph.getByRole("treeitem", {name:"未关联实体（1 组）",exact:true});
  await toggle(unassociated).click();
  await expect(entity("cycle-a")).toHaveCount(1);
  await expect(entity("cycle-b")).toHaveCount(1);
  await page.locator('[data-entity-reference="cycle-a"]').click();
  await expect(entity("cycle-a")).toBeFocused();
  check.push("shared references, rooted and disconnected cycles expand canonical path and focus");

  await expect(graph).toContainText("1 条处理未完成");
  await expect(graph.getByRole("treeitem",{name:"待检查属性",exact:true})).toHaveCount(0);
  await toggle(graph.getByRole("treeitem",{name:"待检查及暂无结果的属性",exact:true})).click();
  await expect(graph.getByRole("treeitem",{name:"待检查属性",exact:true})).toContainText("尚无有效属性值");
  const value = graph.getByRole("treeitem", {name:"17 mg",exact:true});
  for (const [label, ref] of [["属性值原文","value-ref"],["属性依据原文","bridge-ref"],
    ["主体归属原文","subject-ref"],["条件原文","condition-ref"],["反证原文","counter-ref"]]) {
    const button = value.getByRole("button",{name:label,exact:true});
    await button.focus();
    await button.press("Enter");
    await expect.poll(async () => JSON.parse(await page.locator("#selections").textContent()).at(-1)).toBe(ref);
    assert.equal(JSON.parse(await page.locator("#selections").textContent()).at(-1), ref);
    await expect(entity("cycle-a")).toHaveAttribute("aria-selected","true");
  }
  await entity("left").getByRole("button",{name:"关系依据原文",exact:true}).click();
  await entity("root").getByRole("button",{name:"实体名称原文",exact:true}).click();
  await expect(entity("root")).toHaveAttribute("aria-expanded","true");
  await expect(page.locator("button button")).toHaveCount(0);
  check.push("coverage and every proof action retained; keyboard buttons do not select or toggle rows");

  await entity("shared").click();
  await toggle(entity("right")).click();
  await page.getByRole("button",{name:"更新结果",exact:true}).click();
  await expect(entity("root")).toContainText("报告 1");
  await expect(entity("new")).toHaveCount(1);
  await expect(entity("right")).toHaveAttribute("aria-expanded","false");
  await expect(entity("shared")).toHaveAttribute("aria-selected","true");
  await expect(section).toHaveAttribute("aria-expanded","false");
  await expect(outline.getByRole("treeitem",{name:"new-chapter",exact:true})).toHaveCount(1);
  check.push("incremental node and label updates retain expansion and selection");
  await entity("shared").click();
  await page.getByRole("button",{name:"更新实体归属",exact:true}).click();
  await expect(entity("shared")).toHaveCount(0);
  await expect(entity("right")).toHaveAttribute("tabindex","0");
  await toggle(entity("right")).click();
  await expect(entity("shared")).toHaveAttribute("aria-selected","true");
  await entity("shared").click();
  check.push("reparented entities retain selection and a visible keyboard entry");
  await page.getByRole("button",{name:"删除共享实体",exact:true}).click();
  await expect(entity("shared")).toHaveCount(0);
  await expect(entity("tail")).toHaveCount(0);
  await expect(graph.locator('[role="treeitem"][tabindex="0"]')).toHaveCount(1);
  await expect(graph.locator('[aria-selected="true"]')).toHaveCount(0);
  check.push("removed focused/selected nodes leave a valid keyboard entry");
  await page.getByRole("button",{name:"切换身份",exact:true}).click();
  await expect(section).toHaveAttribute("aria-expanded","true");
  await expect(entity("right")).toHaveAttribute("aria-expanded","true");
  await expect(graph.locator('[aria-selected="true"]')).toHaveCount(0);
  check.push("new identity resets local tree state");

  const long = outline.getByRole("treeitem",{name:"很长的章节标题用于检查狭窄目录中的自动换行和完整阅读",exact:true});
  assert.ok(await long.evaluate(node => node.scrollWidth <= node.clientWidth));
  const rootPadding = await chapter.evaluate(node => getComputedStyle(node).paddingLeft);
  const childPadding = await section.evaluate(node => getComputedStyle(node).paddingLeft);
  assert.equal(parseFloat(childPadding)-parseFloat(rootPadding),12);
  await chapter.focus();
  await chapter.press("Space");
  assert.notEqual(await chapter.evaluate(node => getComputedStyle(node).backgroundColor),"rgba(0, 0, 0, 0)");
  await page.screenshot({path:path.join(output,"light.png"),fullPage:true});
  await page.getByRole("button",{name:"切换主题",exact:true}).click();
  await page.screenshot({path:path.join(output,"dark.png"),fullPage:true});
  check.push("Tailwind 3 indentation, selection, long labels and light/dark themes");
  assert.equal(requests.filter(url => url.startsWith("/api/")).length,0);
  assert.deepEqual(errors,[]);
  await writeFile(path.join(output,"result.json"),JSON.stringify({status:"passed",scope:"synthetic data; real components and CSS",checks:check},null,2));
  console.log(`PASS: ${check.length} tree browser checks. ${output}`);
} catch(error) {
  await writeFile(path.join(output,"failure.json"),JSON.stringify({errors,completed:check,body:await page.locator("body").innerText()},null,2));
  throw error;
} finally {
  await browser.close();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}
