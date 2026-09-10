// Real UI against a disposable FastAPI backend; no intercepted generation responses.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const backend = process.env.BATCH_DEMO_TEST_BACKEND;
assert.ok(backend, 'Set BATCH_DEMO_TEST_BACKEND to a fresh isolated backend');
const iri = process.env.BATCH_DEMO_TEST_IRI || 'urn:demo:browser:hrs5592';
const output = process.env.BATCH_DEMO_BROWSER_OUTPUT || '/tmp/cmc-batch-demo-browser';
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const { build } = await import(process.env.ESBUILD_MODULE || 'esbuild');
const { chromium, expect } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright/test');
const data = await (await fetch(`${backend}/api/reports/batch-demo?document_iri=${encodeURIComponent(iri)}`)).json();
assert.equal(data.available, true);
assert.equal(data.latest_report, null, 'Use a fresh disposable backend for each run');
const item = { key: iri, iri, kind: 'uploaded-document', title: data.graph.source_filename, type: 'CMCReport', category: 'CMCReport', date: null, size: null };
const entry = `
import { createRoot } from 'react-dom/client';
import { useState } from 'react';
import { ReadingPane } from '@/components/reports/reading-pane';
import { getBatchDemo } from '@/lib/api';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DocumentActionsMenu } from '@/components/reports/document-actions-menu';
import { BatchDemoWorkspaceGate } from '@/components/reports/batch-demo-workspace';
import { BatchDemoTemplate } from '@/components/reports/batch-demo-template';
const item = ${JSON.stringify(item)};
const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
function App() { const [saved, setSaved] = useState(null); const [template, setTemplate] = useState(false); if (template) return <main className='flex h-screen flex-col bg-background text-foreground'><button onClick={() => setTemplate(false)}>返回报告演示</button><div className='min-h-0 flex-1'><BatchDemoTemplate templateId='8542466b-d6e6-4052-ae7e-05ca9c99a1c3' /></div></main>; return <main className='flex h-screen flex-col gap-4 bg-background p-6 text-foreground'>
<button onClick={() => setTemplate(true)}>打开演示模板</button>
<header className='flex items-center justify-between gap-6'><div><p className='text-xs text-muted-foreground'>报告中心 / CMCReport</p><h1 className='text-lg font-semibold'>{item.title}</h1></div><button onClick={async () => { const d = await getBatchDemo(item.iri); if (d.available && d.latest_report) { const r = d.latest_report; setSaved({key:r.id,kind:'generated-report',title:'批记录报告（演示）',category:'batch_record_demo',type:'批记录报告（演示）',jobId:r.job_id,reportId:r.id,date:r.created_at,size:r.file_size}); } }}>查看保存结果</button><DocumentActionsMenu item={item} /></header>
{saved ? <div className='min-h-0 flex-1 overflow-auto'><ReadingPane item={saved} /></div> : <BatchDemoWorkspaceGate documentIri={item.iri} />}</main>; }
createRoot(document.getElementById('root')).render(<QueryClientProvider client={client}><App /></QueryClientProvider>);`;
const bundle = await build({ stdin: { contents: entry, resolveDir: frontend, loader: 'tsx' }, bundle: true, write: false, platform: 'browser', format: 'iife', tsconfig: path.join(frontend, 'tsconfig.json'), banner: { js: 'var process = { env: {} };' }, define: { 'process.env.NODE_ENV': JSON.stringify('development'), 'process.env.NEXT_PUBLIC_API_URL': JSON.stringify('') } });
const css = process.env.BATCH_DEMO_BROWSER_CSS ? await readFile(process.env.BATCH_DEMO_BROWSER_CSS) : '';
const requests = [], errors = [];
const server = createServer(async (req, res) => {
 try {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/') { res.end('<html><head><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/style.css"></head><body><div id="root"></div><script src="/bundle.js"></script></body></html>'); return; }
  if (url.pathname === '/bundle.js') { res.setHeader('Content-Type', 'application/javascript'); res.end(bundle.outputFiles[0].text); return; }
  if (url.pathname === '/style.css') { res.setHeader('Content-Type', 'text/css'); res.end(css); return; }
  if (!url.pathname.startsWith('/api/')) { res.writeHead(404); res.end(); return; }
  let body = ''; for await (const chunk of req) body += chunk;
  requests.push({ method: req.method, path: url.pathname });
  const upstream = await fetch(backend + req.url, { method: req.method, headers: { 'Content-Type': 'application/json' }, ...(body ? { body } : {}) });
  res.writeHead(upstream.status, Object.fromEntries([...upstream.headers].filter(([name]) => !['content-length', 'content-encoding', 'transfer-encoding'].includes(name))));
  res.end(Buffer.from(await upstream.arrayBuffer()));
 } catch (error) { errors.push(String(error)); res.writeHead(500); res.end(String(error)); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({ executablePath: process.env.DOCUMENT_BROWSER_CHROME || '/usr/bin/google-chrome', args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
await page.addInitScript(() => Object.defineProperty(Crypto.prototype, 'randomUUID', { value: undefined }));
page.on('pageerror', error => errors.push(String(error)));
await mkdir(output, { recursive: true });
try {
 await page.goto(`http://127.0.0.1:${server.address().port}`);
 // Wait for the initial source context before switching to the template context.
 await expect(page.getByRole('button', { name: '工艺链', exact: true })).toBeVisible({ timeout: 30000 });
 await page.getByRole('button', { name: '打开演示模板', exact: true }).click();
 await expect(page.getByRole('heading', { name: /HRS-5592 批记录（演示专用）/ })).toBeVisible({ timeout: 30000 });
 await page.getByRole('tab', { name: '模板契约', exact: true }).click();
 await expect(page.getByText(/10\/10 项通过/)).toBeVisible();
 assert.ok((await page.getByRole('heading', { name: '批记录内容与输入校验' }).boundingBox()).y < 250, 'Inactive source tab must not occupy layout space');
 await page.getByRole('tab', { name: '报告预览', exact: true }).click();
 await expect(page.getByRole('heading', { name: '批生产记录', exact: true })).toBeVisible();
 await page.screenshot({ path: path.join(output, 'template-preview.png') });
 await page.getByRole('button', { name: '返回报告演示', exact: true }).click();
 await expect(page.getByRole('button', { name: '工艺链', exact: true })).toBeVisible();
 await page.getByRole('button', { name: '工艺链', exact: true }).click();
 await page.getByRole('button', { name: /SM5592-A14的合成/ }).click();
 await expect(page.getByRole('button', { name: /SM5592-A14 ProcessIntermediate/ })).toBeVisible();
 await expect(page.locator('.tiptap')).toBeVisible({ timeout: 60000 });
 await page.screenshot({ path: path.join(output, 'graph-desktop.png') });
 assert.equal(requests.filter(r => r.method === 'POST').length, 0);
 await page.getByRole('button', { name: '操作', exact: true }).click();
 await page.getByRole('menuitem', { name: /生成批记录报告/ }).click();
 const drawer = page.getByRole('dialog', { name: /批记录报告/ });
 await expect(drawer.getByText('图谱输入校验 · 10/10 项通过')).toBeVisible();
 await page.screenshot({ path: path.join(output, 'input-contract.png') });
 await drawer.getByRole('button', { name: '开始生成', exact: true }).click();
 await expect(drawer.getByText('生成完成 · 已保存')).toBeVisible({ timeout: 60000 });
 await expect(drawer.getByText('已完成', { exact: true })).toHaveCount(5);
 await drawer.getByRole('button', { name: '预览', exact: true }).click();
 const preview = page.getByRole('dialog', { name: '批记录报告预览（演示草稿）', exact: true });
 await expect(preview.getByRole('heading', { name: '批生产记录', exact: true })).toBeVisible();
 await expect(preview.getByText('QA审核/日期', { exact: true })).toBeVisible();
 await expect(preview.getByText('待填写', { exact: false })).toHaveCount(0);
 await expect(preview.locator('[data-form=operations]')).toHaveCount(13);
 await expect(preview.locator('[data-form=weighing]')).toHaveCount(5);
 await expect(preview.locator('[data-form=tlc]')).toHaveCount(2);
 await expect(preview.getByText('SM5592-A14的制备', { exact: true })).toHaveCount(2);
 await expect(preview.locator('[data-form=header]').nth(1)).toContainText('642/646车间');
 const firstOperation = preview.locator('[data-form=operations]').first();
 await expect(firstOperation.locator('tr').nth(1).locator('td[rowspan="3"]')).toHaveCount(3);
 await expect(firstOperation.locator('tr').nth(1).locator('td').first()).toContainText('1、500L反应釜');
 assert.deepEqual(await firstOperation.locator('tr').nth(2).locator('td').allTextContents().then(cells => cells.map(v => v.trim())), ['1，4-二氧六环', 'kg']);
 await expect(firstOperation.locator('tr')).toHaveCount(23);
 assert.match(await firstOperation.locator('tr').nth(1).locator('td').first().locator('span').first().evaluate(el => getComputedStyle(el).fontFamily), /Times New Roman/);
 await firstOperation.locator('tr').nth(1).scrollIntoViewIfNeeded();
 await page.screenshot({ path: path.join(output, 'operation-parameters.png') });
 await expect(preview.locator('[data-form=cover] td[colspan="2"]')).toHaveCount(6);
 assert.equal(await preview.getByRole('heading', { name: '批生产记录', exact: true }).evaluate(el => getComputedStyle(el).fontSize), '29.3333px');
 await page.screenshot({ path: path.join(output, 'report-preview.png') });
 await preview.getByRole('button', { name: 'Close', exact: true }).click();
 await expect(preview).toBeHidden();
 await page.screenshot({ path: path.join(output, 'after-preview.png') });
 const [file] = await Promise.all([page.waitForEvent('download'), drawer.getByRole('button', { name: '下载报告', exact: true }).click()]);
 await file.saveAs(path.join(output, 'batch-record-demo.docx'));
 assert.match(file.suggestedFilename(), /批记录报告_演示草稿/);
 await page.reload();
 await page.getByRole('button', { name: '操作', exact: true }).click();
 await page.getByRole('menuitem', { name: /生成批记录报告/ }).click();
 await expect(page.getByText('生成完成 · 已保存')).toBeVisible();
 assert.equal(requests.filter(r => r.method === 'POST').length, 1);
 assert.equal(requests.some(r => /\/runs|annotated-document|risk-report/.test(r.path)), false);
 await page.setViewportSize({ width: 390, height: 844 });
 await page.screenshot({ path: path.join(output, 'drawer-mobile.png') });
 assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
 await page.getByRole('dialog', { name: /批记录报告/ }).getByRole('button', { name: '关闭', exact: true }).click();
 await page.setViewportSize({ width: 1440, height: 1000 });
 await page.getByRole('button', { name: '查看保存结果', exact: true }).click();
 await expect(page.getByRole('heading', { name: '批生产记录', exact: true })).toBeVisible({ timeout: 30000 });
 await expect(page.getByText('AI 生成', { exact: true })).toHaveCount(0);
 await expect(page.getByRole('button', { name: '下载批记录报告', exact: true })).toBeAttached();
 await page.screenshot({ path: path.join(output, 'saved-report.png') });
 await page.getByRole('button', { name: '打开演示模板', exact: true }).click();
 await page.getByRole('tab', { name: '模板契约', exact: true }).click();
 await page.screenshot({ path: path.join(output, 'shared-template-contract.png') });
 await page.getByRole('tab', { name: '报告预览', exact: true }).click();
 await expect(page.getByText('已保存的最新生成结果，与报告中心和下载 Word 一致。')).toBeVisible();
 await page.getByRole('button', { name: '生成批记录报告', exact: true }).click();
 await expect(page.getByText('生成完成 · 已保存')).toBeVisible();
 const templateData = await (await fetch(`${backend}/api/reports/batch-demo/templates/8542466b-d6e6-4052-ae7e-05ca9c99a1c3`)).json();
 const reportData = await (await fetch(`${backend}/api/reports/batch-demo?document_iri=${encodeURIComponent(iri)}`)).json();
 assert.deepEqual(templateData, reportData);
 assert.equal(requests.filter(r => r.method === 'POST').length, 1);
 assert.equal(requests.some(r => /\/runs|annotated-document|risk-report/.test(r.path)), false);
 assert.deepEqual(await (await fetch(`${backend}/verification`)).json(), { reports: 1, analyses: 0, annotations: 0 });
 assert.deepEqual(errors, []);
 console.log(JSON.stringify({ passed: true, requests, output }));
} catch (error) { console.error(errors); await page.screenshot({ path: path.join(output, 'failure.png') }); throw error; }
finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
