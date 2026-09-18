// Real V2 editor and WordViewer; isolated APIs and an actual parsed synthetic sample.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { build } = await import(process.env.ESBUILD_MODULE || "esbuild");
const { chromium, expect } = await import(process.env.PLAYWRIGHT_MODULE || "playwright/test");
const backend = path.resolve(frontend, "../backend");
const prepared = spawnSync(path.join(backend, ".venv/bin/python"), ["-c", `
import json,tempfile
from pathlib import Path
from docx import Document
from app.services.extraction.document_annotator import parse_word_to_tiptap
from app.services.extraction.slot_suggester import suggest_slots
with tempfile.TemporaryDirectory() as folder:
    path=Path(folder)/'sample.docx'
    doc=Document();doc.add_heading('产品信息',1);doc.add_paragraph('规格：250 mg𠀀')
    doc.add_paragraph('642 车间设备见下表：')
    table=doc.add_table(rows=1,cols=2)
    table.cell(0,0).text='设备编号';table.cell(0,1).text='设备名称'
    doc.save(path)
    content=parse_word_to_tiptap(path)
    analysis=content.pop('analysis')
    result=suggest_slots(None,'',content_json=content,analysis=analysis)
    print(json.dumps({'content':content,'analysis':analysis,'result':result}))
`], { cwd: backend, encoding: "utf8" });
assert.equal(prepared.status, 0, prepared.stderr);
const fixture = JSON.parse(prepared.stdout);
const entry = `
import {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {OutputTemplateEditor} from '@/components/reporting/output-template-editor';
import {emptyTemplate} from '@/lib/reporting-v2';
const root='https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport';
function App(){
 const [mode,setMode]=useState('finder_legacy');const [generation,setGeneration]=useState(0);
 const [sample,setSample]=useState(true);const [saved,setSaved]=useState(null);
 const [schema]=useState(()=>({...emptyTemplate(),source_slots:[{source_slot_id:'source',kind:'document',class_iri:root}]}));
 return <><button onClick={()=>setMode(mode==='finder_legacy'?'ontology_guided':'finder_legacy')}>切换测试引擎</button>
 <button onClick={()=>{setSample(true);setSaved(null);setGeneration(generation+1)}}>空白测试模板</button>
 <button onClick={()=>{setSample(false);setGeneration(generation+1)}}>移除测试样例</button>
 <pre data-testid='saved'>{saved?JSON.stringify(saved):''}</pre>
 <OutputTemplateEditor key={generation} schema={schema} templateId='template' schemaHash='hash'
 initialTab='template' recognitionMode={mode} onSave={setSaved} onCancel={()=>{}}
 sampleContentJson={sample?window.fixture.content:null} sampleAnalysis={sample?window.fixture.analysis:null}/></>;
}
createRoot(document.getElementById('root')).render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><App/></QueryClientProvider>);
`;
const bundle = await build({ stdin: { contents: entry, resolveDir: frontend, loader: "tsx" },
  bundle: true, write: false, platform: "browser", format: "iife", tsconfig: path.join(frontend, "tsconfig.json"),
  define: { "process.env.NODE_ENV": '"development"', "process.env.NEXT_PUBLIC_API_URL": '""' },
  plugins: [{ name: "navigation-fixture", setup(build) {
    build.onResolve({ filter: /^next\/link$/ }, () => ({ path: "link", namespace: "fixture-link" }));
    build.onLoad({ filter: /.*/, namespace: "fixture-link" }, () => ({ contents: `import {createElement} from 'react';export default function Link({href,children,...props}){return createElement('a',{...props,href:typeof href==='string'?href:href.pathname},children);}`, loader: "js", resolveDir: frontend }));
    build.onResolve({ filter: /^next\/navigation$/ }, () => ({ path: "navigation", namespace: "fixture-navigation" }));
    build.onLoad({ filter: /.*/, namespace: "fixture-navigation" }, () => ({ contents: `export const usePathname=()=>'/settings/ast-templates/template';export const useSearchParams=()=>new URLSearchParams();export const useRouter=()=>({replace(){},push(){}});`, loader: "js" }));
  } }],
});
const requests = [], errors = [];
let failNext = true, holdNext = false, release;
const json = (response, data, status = 200) => { response.writeHead(status, { "Content-Type": "application/json" }); response.end(JSON.stringify(data)); };
const server = createServer(async (request, response) => {
  const url = new URL(request.url, "http://localhost");
  if (url.pathname === "/") { response.end(`<html><head><meta charset="utf-8"><style>.hidden,[hidden],[data-state=inactive][role=tabpanel]{display:none}button{margin:3px}aside{min-width:450px}.tiptap{border:1px solid}</style></head><body><div id="root"></div><script>window.fixture=${JSON.stringify(fixture)}</script><script src="/bundle.js"></script></body></html>`); return; }
  if (url.pathname === "/bundle.js") { response.setHeader("Content-Type", "application/javascript"); response.end(bundle.outputFiles[0].text); return; }
  if (url.pathname === "/favicon.ico") { response.end(); return; }
  let body = ""; for await (const chunk of request) body += chunk;
  requests.push({ path: url.pathname, method: request.method, body });
  if (url.pathname === "/api/ast-templates/suggest-slots") {
    const payload = JSON.parse(body);
    assert.deepEqual(payload.sample_content_json, fixture.content);
    assert.deepEqual(payload.analysis, fixture.analysis);
    assert.equal(payload.job_id, undefined);
    assert.equal(payload.doc_class_iri, "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport");
    if (failNext) { failNext = false; json(response, { detail: "分析服务暂不可用" }, 503); return; }
    if (holdNext) { holdNext = false; release = () => json(response, fixture.result); return; }
    json(response, fixture.result); return;
  }
  if (url.pathname.endsWith("/semantic-sources")) { json(response, { options: [] }); return; }
  if (url.pathname.endsWith("/suggest-semantics")) { json(response, { options: [], patches: [], diagnostics: [{ code: "MODEL_UNAVAILABLE", message: "语义模型未启用" }], completion: "incomplete" }); return; }
  if (url.pathname === "/api/entities") { json(response, { items: [] }); return; }
  if (url.pathname.endsWith("/training-pairs") || url.pathname === "/api/report-contracts") { json(response, []); return; }
  if (url.pathname === "/api/report-model-context") { json(response, { definition: { classes: {} } }); return; }
  if (url.pathname === "/api/ast-templates/coverage-doc-classes") { json(response, { capable: [] }); return; }
  errors.push(`Unexpected ${request.method} ${url.pathname}`); json(response, {}, 404);
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch({ executablePath: "/usr/bin/google-chrome", args: ["--no-sandbox"] });
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("http://127.0.0.1:" + server.address().port);
  const panel = page.getByLabel("输出模板定义", { exact: true });
  const analyze = panel.getByRole("button", { name: "AI 自动分析结构", exact: true });
  const count = () => requests.filter(r => r.path.endsWith("/suggest-slots")).length;
  await expect(analyze).toBeEnabled();
  assert.equal(count(), 0);
  await analyze.click();
  await expect(panel.getByRole("alert")).toContainText("分析服务暂不可用");
  await expect(analyze).toBeEnabled();
  await analyze.click();
  await expect(panel.getByRole("navigation", { name: "报告章节" }).getByRole("textbox", { name: "章节标题" })).toHaveCount(fixture.result.sections.length);
  await expect(panel.getByRole("status").filter({ hasText: "结构分析未全部完成" })).toContainText("结构分析未全部完成");
  await expect(panel.getByRole("status").filter({ hasText: "结构分析未全部完成" })).toContainText("语义模型未启用");
  assert((await panel.getByRole("textbox", { name: "分组标题", exact: true }).evaluateAll(inputs => inputs.map(input => input.value))).includes("642 车间设备表"));
  await panel.getByRole("button", { name: "规格", exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.getSelection()?.toString())).toBe("规格");
  await page.getByRole("button", { name: "保存新修订", exact: true }).click();
  const saved = JSON.parse(await page.getByTestId("saved").textContent());
  const candidate = fixture.result.sections.flatMap(s => s.groups.flatMap(g => g.candidates)).find(c => c.label === "规格");
  const unit = saved.sections.flatMap(s => s.groups.flatMap(g => g.units)).find(u => u.title === "规格");
  assert.equal(saved.schema_version, 2);
  assert(saved.sections.some(section => section.groups.some(group => group.title === "642 车间设备表")));
  assert.deepEqual(unit.origin, candidate.origin);
  assert.deepEqual(unit.render.nodes, []);
  await panel.getByRole("textbox", { name: "章节标题" }).first().fill("人工章节");
  await analyze.click();
  await expect(analyze).toBeEnabled();
  await expect(panel.getByRole("textbox", { name: "章节标题" }).first()).toHaveValue("人工章节");
  const beforeSwitch = count();
  await page.getByRole("button", { name: "切换测试引擎" }).click();
  await expect(analyze).toBeEnabled();
  assert.equal(count(), beforeSwitch);
  await page.getByRole("button", { name: "空白测试模板" }).click();
  holdNext = true;
  await analyze.click();
  await expect.poll(() => !!release).toBe(true);
  await expect(page.getByRole("button", { name: "保存新修订", exact: true })).toBeDisabled();
  await panel.getByRole("button", { name: "＋章节", exact: true }).click();
  await panel.getByRole("textbox", { name: "章节标题" }).fill("分析期间编辑");
  release(); release = undefined;
  await expect(analyze).toBeEnabled();
  await expect(panel.getByRole("textbox", { name: "章节标题" })).toHaveCount(1);
  await expect(panel.getByRole("textbox", { name: "章节标题" })).toHaveValue("分析期间编辑");
  await page.getByRole("button", { name: "移除测试样例" }).click();
  await expect(analyze).toBeDisabled();
  assert(requests.filter(r => r.method !== "GET").every(r => r.path.endsWith("/suggest-slots") || r.path.endsWith("/suggest-semantics") || r.path.endsWith("/coverage-doc-classes")));
  assert.deepEqual(errors, []);
  console.log("Template structure browser checks passed: Finder/V2 entry, explicit analysis, retry, source selection, V2 save, partial result, repeated/late response draft preservation and no implicit recognition.");
} finally {
  release?.(); await browser.close(); await new Promise(resolve => server.close(resolve));
}
