// Exercise the real settings component and report context hook against isolated HTTP fixtures.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { build } = await import(process.env.ESBUILD_MODULE || "esbuild");
const { chromium, expect } = await import(process.env.PLAYWRIGHT_MODULE || "playwright/test");
const entry = `
import {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient,QueryClientProvider,useQuery} from '@tanstack/react-query';
import {TemplateRecognitionEngine} from '@/components/reporting/template-recognition-engine';
import {useRecognitionContext} from '@/components/analysis/use-template-finder';
import {getAstTemplate} from '@/lib/api';
import {useIdentity} from '@/lib/use-identity';
function App(){
 const [id,setId]=useState('a');
 const {role,setIdentity}=useIdentity();
 const template=useQuery({queryKey:['ast-template',id],queryFn:({signal})=>getAstTemplate(id,signal)});
 const context=useRecognitionContext('urn:doc',id);
 return <><button onClick={()=>setId(id==='a'?'b':'a')}>切换模板</button>
 <button onClick={()=>template.refetch()}>刷新模板</button>
 <button onClick={()=>context.refetch()}>刷新报告上下文</button>
 <button onClick={()=>setIdentity({username:role==='qa'?'analyst':'reviewer',role:role==='qa'?'senior_analyst':'qa'})}>切换权限</button>
 <input aria-label='未保存的报告内容' defaultValue='编辑草稿'/>
 <p data-testid='template-mode'>{template.data?.recognition_mode}</p>
 <p data-testid='report-mode'>{context.data?.selected?.recognition_mode}</p>
 <TemplateRecognitionEngine templateId={id} rootClassIri={id==='a'?'CMCReport':'unsupported'}/></>;
}
createRoot(document.getElementById('root')).render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><App/></QueryClientProvider>);
`;
const bundle = await build({ stdin: { contents: entry, resolveDir: frontend, loader: "tsx" },
  bundle: true, write: false, platform: "browser", format: "iife", tsconfig: path.join(frontend, "tsconfig.json"),
  define: { "process.env.NODE_ENV": '"development"', "process.env.NEXT_PUBLIC_API_URL": '""' } });
const modes = new Map([["a", "ontology_guided"], ["b", "ontology_guided"]]);
const requests = [], errors = [];
let rejectNext = false, holdNext = false, releaseSave;
let holdTemplateRead = false, holdContextRead = false, releaseTemplate, releaseContext;
const json = (res, data, status = 200) => {
  res.writeHead(status, { "Content-Type": "application/json" }); res.end(JSON.stringify(data));
};
const configuration = (id) => ({ recognition_mode: modes.get(id),
  finder_profile_id: modes.get(id) === "finder_legacy" ? "cmc_baseline_v1" : null,
  finder_profiles: id === "a" ? [{ id: "cmc_baseline_v1", label: "CMC（本体指引1.0）" }] : [],
});
const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  if (url.pathname === "/") { res.end('<html><body><div id="root"></div><script src="/bundle.js"></script></body></html>'); return; }
  if (url.pathname === "/bundle.js") { res.end(bundle.outputFiles[0].text); return; }
  if (url.pathname === "/favicon.ico") { res.end(); return; }
  let body = ""; for await (const chunk of req) body += chunk;
  requests.push({ path: url.pathname, method: req.method, body });
  if (url.pathname === "/api/ast-templates/recognition-context") {
    const id = url.searchParams.get("template_id");
    if (holdContextRead) {
      releaseContext = () => json(res, { detail: "temporary context refresh failure" }, 502);
      return;
    }
    json(res, { selected: { template_id: id, ...configuration(id) } }); return;
  }
  const match = url.pathname.match(/^\/api\/ast-templates\/([ab])(\/recognition-engine)?$/);
  if (!match) { json(res, { detail: "unexpected " + url.pathname }, 404); return; }
  const id = match[1];
  if (req.method === "PATCH") {
    const data = JSON.parse(body);
    assert.equal(data.expected_recognition_mode, modes.get(id));
    assert.equal(data.expected_finder_profile_id, configuration(id).finder_profile_id);
    if (rejectNext) { rejectNext = false; json(res, { detail: { message: "识别引擎设置已变更，请刷新后重试" } }, 409); return; }
    const save = () => { modes.set(id, data.recognition_mode); json(res, configuration(id)); };
    if (holdNext) { holdNext = false; releaseSave = save; return; }
    save(); return;
  }
  const payload = { id, ...configuration(id) };
  if (holdTemplateRead && !match[2]) {
    releaseTemplate = () => json(res, payload);
    return;
  }
  json(res, payload);
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch({ executablePath: process.env.DOCUMENT_BROWSER_CHROME || "/usr/bin/google-chrome", args: ["--no-sandbox"] });
try {
  const page = await browser.newPage();
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("http://127.0.0.1:" + server.address().port);
  const selector = page.getByRole("combobox", { name: "关系图谱识别引擎", exact: true });
  const save = page.getByRole("button", { name: "保存引擎设置", exact: true });
  await expect(selector).toBeEnabled();
  await expect(save).toBeDisabled();
  await page.getByRole("textbox", { name: "未保存的报告内容" }).fill("保留这个草稿");
  await selector.selectOption("finder_legacy");
  await expect(page.getByRole("combobox", { name: "本体指引1.0配置" })).toHaveValue("cmc_baseline_v1");
  assert.equal(requests.filter(r => r.method === "PATCH").length, 0);
  // A large stale template read and an unavailable report-context refresh must
  // not keep a committed save pending or overwrite the server's saved mode.
  holdTemplateRead = true;
  await page.getByRole("button", { name: "刷新模板", exact: true }).click();
  await expect.poll(() => !!releaseTemplate).toBe(true);
  const detailReads = requests.filter(r => r.path === "/api/ast-templates/a").length;
  holdContextRead = true;
  await save.click();
  await expect.poll(() => !!releaseContext).toBe(true);
  await expect(page.getByTestId("template-mode")).toHaveText("finder_legacy");
  await expect(page.getByText("引擎设置已保存。", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "正在保存…", exact: true })).toHaveCount(0);
  assert.equal(requests.filter(r => r.path === "/api/ast-templates/a").length, detailReads,
    "saving the engine must not download the entire template again");
  holdTemplateRead = false;
  releaseTemplate(); releaseTemplate = undefined;
  holdContextRead = false;
  releaseContext(); releaseContext = undefined;
  await expect(page.getByTestId("template-mode")).toHaveText("finder_legacy");
  await expect(page.getByText("引擎设置已保存。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "刷新报告上下文", exact: true }).click();
  await expect(page.getByTestId("report-mode")).toHaveText("finder_legacy");
  await expect(page.getByRole("textbox", { name: "未保存的报告内容" })).toHaveValue("保留这个草稿");
  await expect(save).toBeDisabled();
  await selector.selectOption("ontology_guided");
  rejectNext = true;
  await save.click();
  await expect(page.getByRole("alert")).toContainText("识别引擎设置已变更");
  await expect(page.getByTestId("report-mode")).toHaveText("finder_legacy");
  await page.getByRole("button", { name: "刷新设置" }).click();
  await expect(selector).toHaveValue("finder_legacy");
  await selector.selectOption("ontology_guided");
  await save.click();
  await expect(page.getByTestId("report-mode")).toHaveText("ontology_guided");
  await expect(save).toBeDisabled();
  await page.getByRole("button", { name: "切换权限" }).click();
  await expect(selector).toBeDisabled();
  await expect(save).toHaveCount(0);
  await page.getByRole("button", { name: "切换权限" }).click();
  await expect(selector).toBeEnabled();
  // A response for the old template must not set the newly opened template's selector.
  await selector.selectOption("finder_legacy");
  holdNext = true;
  await save.click();
  await expect.poll(() => !!releaseSave).toBe(true);
  await page.getByRole("button", { name: "切换模板" }).click();
  await expect(page.getByText("当前文档类型暂无可用的本体指引1.0配置。")).toBeVisible();
  releaseSave(); releaseSave = undefined;
  await expect.poll(() => modes.get("a")).toBe("finder_legacy");
  await expect(selector).toHaveValue("ontology_guided");
  await expect(selector.locator('option[value="finder_legacy"]')).toHaveJSProperty("disabled", true);
  await expect(page.getByTestId("report-mode")).toHaveText("ontology_guided");
  assert(requests.every(r => r.method === "GET" || (r.method === "PATCH" && r.path.endsWith("/recognition-engine"))));
  assert.deepEqual(errors, []);
  console.log("Template engine browser checks passed: saved state independent of slow/failed refresh, stale read cancellation, explicit save, shared context, draft retention, conflict, permissions and late response isolation.");
} finally {
  releaseSave?.();
  releaseTemplate?.(); releaseContext?.();
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
