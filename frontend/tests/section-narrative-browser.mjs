// Component race and request-shape checks; real API/model acceptance is recorded separately.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const frontend=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const {build}=await import(process.env.ESBUILD_MODULE || 'esbuild');
const {chromium,expect}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright/test');
const bundle=await build({stdin:{contents:`
import {useState} from 'react';import {createRoot} from 'react-dom/client';
import {SectionNarrativeEditor} from '@/components/reporting/section-narrative-editor';
import {emptyTemplate} from '@/lib/reporting-v2';
function App(){
 const [version,setVersion]=useState(1);
 const [template,setTemplate]=useState(()=>({...emptyTemplate(),source_slots:[{source_slot_id:'doc',kind:'document',class_iri:'urn:Report'}],definitions:{bindings:{},inputs:{i:{input_id:'i',label:'产品',binding_ref:'b',projection:{kind:'identity'}}}},sections:[{section_id:'s',title:'产品介绍',groups:[{group_id:'g',units:[{output_id:'u',inputs:[{input_ref:'i'}],render:{kind:'narrative',mode:'assisted',prompt:{instructions:'内容项专用要求'}}}]}]}]}));
 return <><button onClick={()=>setVersion(version+1)}>切换修订</button>
 <pre data-testid='saved'>{JSON.stringify(template)}</pre>
 <SectionNarrativeEditor key={version} sectionId='s' template={template} templateId={'t'+version} sourceJobId='job' recognitionMode='finder_legacy' sampleText='样例中的产品名称不应出现在真实预览中'
 onChange={(change)=>setTemplate(current=>{const next=structuredClone(current);change(next);return next})}/></>;
}createRoot(document.getElementById('root')).render(<App/>);`,resolveDir:frontend,loader:'tsx'},bundle:true,write:false,platform:'browser',format:'iife',tsconfig:path.join(frontend,'tsconfig.json'),define:{'process.env.NODE_ENV':'"development"','process.env.NEXT_PUBLIC_API_URL':'""'}});
const calls=[],errors=[];let hold=false,release;
const json=(res,value,status=200)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
const server=createServer(async(req,res)=>{
 const pathname=new URL(req.url,'http://localhost').pathname;
 if(pathname==='/'){res.end('<html><head><meta charset="utf-8"></head><body><div id="root"></div><script src="/bundle.js"></script></body></html>');return;}
 if(pathname==='/bundle.js'){res.setHeader('Content-Type','text/javascript');res.end(bundle.outputFiles[0].text);return;}
 if(pathname==='/favicon.ico'){res.end();return;}
 let body='';for await(const c of req)body+=c;const data=body?JSON.parse(body):null;
 calls.push({path:pathname,method:req.method,data});
 if(pathname.endsWith('/generate-section-prompt')){if(hold){hold=false;release=()=>json(res,{prompt:'迟到建议'});}else json(res,{prompt:'根据 {{产品}} 生成正式介绍。'});return;}
 if(pathname==='/api/ast-templates/preview-section-narrative'){
  assert.equal(data.draft_schema.sections.length,1);assert.equal(data.draft_schema.sections[0].groups.length,1);
  assert.ok(['手工编辑的章节要求','请求期间手工编辑'].includes(data.prompt));assert.equal(data.job_id,'job');assert.equal(data.section_id,'s');
  json(res,{narrative:'产品输入的自定义正文\n\n缺失项目：（待补充）',warnings:['集合仅含已有部分'],source:{kind:'finder_demo',source_filename:'真实原件.docx'}});return;
 }
 errors.push(pathname);json(res,{},404);
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',args:['--no-sandbox']});
try{
 const page=await browser.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 const prompt=page.getByRole('textbox',{name:'Section 行文 Prompt',exact:true});
 await expect(prompt).toBeVisible();assert.equal(calls.length,0);
 await prompt.fill('写产品介绍');
 await page.getByRole('button',{name:'从样本生成',exact:true}).click();
 await expect(prompt).toHaveValue('根据 {{产品}} 生成正式介绍。');
 assert.equal(calls[0].data.sample_text,'样例中的产品名称不应出现在真实预览中');
 assert.equal(calls[0].data.instructions,'写产品介绍');
 await expect(page.getByText('可用变量：{{产品}}')).toBeVisible();
 await prompt.fill('手工编辑的章节要求');
 await page.getByRole('checkbox',{name:'在报告中生成本节正文',exact:true}).check();
 await page.getByRole('button',{name:'AI 行文预览',exact:true}).click();
 await expect(page.getByRole('article')).toContainText('产品输入的自定义正文');
 await expect(page.getByRole('article')).toContainText('待补充');
 await prompt.fill('进一步编辑');
 await expect(page.getByRole('status')).toContainText('配置已变更');
 let saved=JSON.parse(await page.getByTestId('saved').textContent());
 assert.equal(saved.sections[0].narrative.instructions,'进一步编辑');
 assert.equal(saved.sections[0].groups[0].units[0].render.prompt.instructions,'内容项专用要求');
 hold=true;await page.getByRole('button',{name:'从样本生成',exact:true}).click();
 await expect.poll(()=>!!release).toBe(true);
 await prompt.fill('请求期间手工编辑');release();release=undefined;
 await expect(page.getByRole('button',{name:'采用生成的 Prompt',exact:true})).toBeVisible();
 await expect(prompt).toHaveValue('请求期间手工编辑');
 hold=true;await page.getByRole('button',{name:'从样本生成',exact:true}).click();
 await expect.poll(()=>!!release).toBe(true);
 await page.getByRole('button',{name:'切换修订',exact:true}).click();
 release();release=undefined;
 await expect(page.getByRole('button',{name:'从样本生成',exact:true})).toBeEnabled();
 await expect(page.getByRole('button',{name:'采用生成的 Prompt',exact:true})).toHaveCount(0);
 await expect(prompt).toHaveValue('请求期间手工编辑');
 assert.equal(calls.filter(c=>c.path.endsWith('/preview-section-narrative')).length,1);
 assert.equal(calls.filter(c=>c.path.endsWith('/report-previews')).length,0);
 await page.getByRole('button',{name:'AI 行文预览'}).click();
 await expect(page.getByRole('article')).toBeVisible();
 await page.getByRole('button',{name:'关闭预览'}).click();
 await expect(page.getByRole('article')).toHaveCount(0);
 assert.deepEqual(errors,[]);
 console.log('Section browser: automatic sample, direct Prompt insertion, variables, partial transient preview, child Prompt preservation, stale preview and late-response disposal passed.');
}finally{release?.();await browser.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
