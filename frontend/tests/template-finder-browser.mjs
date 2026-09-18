// Real Finder output + React/Query/WordViewer, isolated HTTP fixtures only.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const frontend = process.env.FINDER_FRONTEND || path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const { build } = await import(process.env.ESBUILD_MODULE || 'esbuild');
const { chromium, expect } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright/test');
const backend = path.resolve(frontend, '../backend');
const prepared = spawnSync(path.join(backend, '.venv/bin/python'), ['-c', `
import json,tempfile
from pathlib import Path
from docx import Document
from tests.test_api.test_template_finder import FinderOntology
from app.services.extraction.word_analysis import analyze_word_core
from app.services.template_finder.policy import Binding,BUILTINS
from app.services.template_finder.runner import run,freeze_ontology,render_source
with tempfile.TemporaryDirectory() as folder:
 path=Path(folder)/'source.docx'
 doc=Document();doc.add_heading('HRS-1234 CMC报告',0)
 doc.add_paragraph('性状：重复值😀');doc.add_heading('产品基本性质',1);doc.add_paragraph('性状：重复值😀');doc.save(path)
 profile=json.loads(BUILTINS['cmc_baseline_v1'].read_text());binding=Binding(profile['root_classes'][0],profile)
 analysis=analyze_word_core(path)
 source=render_source(analysis,path,'source.docx')
 graph=run(analysis,binding,freeze_ontology(FinderOntology(),binding))
 graph.update({key:source[key] for key in ('document_hash','parser_version','structure_hash','analysis_id')})
 print(json.dumps({'source':source,'graph':graph},ensure_ascii=False))
`], { cwd: backend, encoding: 'utf8' });
assert.equal(prepared.status, 0, prepared.stderr);
const artifact = JSON.parse(prepared.stdout);
const entry = `
import {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {FinderWorkspace} from '@/components/analysis/finder-document-graph-panel';
import {ReportRecognitionWorkspace} from '@/components/reports/report-recognition-workspace';
import {DocumentActionsMenu} from '@/components/reports/document-actions-menu';
import {useIdentity} from '@/lib/use-identity';
function App(){
 const [report,setReport]=useState(false);const [template,setTemplate]=useState('a');
 const {identity,setIdentity}=useIdentity();
 return <><button onClick={()=>setReport(!report)}>切换入口</button>
 <button onClick={()=>setTemplate(template==='a'?'b':'a')}>切换模板</button>
 <button onClick={()=>setIdentity({username:identity.username==='analyst'?'second':'analyst',role:'senior_analyst'})}>切换用户</button>
 <p>{report?'报告中心入口':'模板入口'}</p>
 {report?<><DocumentActionsMenu item={{key:'doc',iri:'urn:doc',kind:'uploaded-document',title:'source.docx',type:'CMC'}} templateId={template}/>
 <ReportRecognitionWorkspace key={template} documentIri='urn:doc' templateId={template}/></>:
 <FinderWorkspace key={template} templateId={template} sourceJobId='source'/>}</>;
}
createRoot(document.getElementById('root')).render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><App/></QueryClientProvider>);
`;
const bundle = await build({ stdin: { contents: entry, resolveDir: frontend, loader:'tsx' }, bundle:true, write:false,
 platform:'browser',format:'iife',tsconfig:path.join(frontend,'tsconfig.json'),
 define:{'process.env.NODE_ENV':'"development"','process.env.NEXT_PUBLIC_API_URL':'""'},
 plugins:[{name:'navigation-fixture',setup(build){
 build.onResolve({filter:/^next\/link$/},()=>({path:'link',namespace:'link-fixture'}));
 build.onLoad({filter:/.*/,namespace:'link-fixture'},()=>({contents:`import {createElement} from 'react';export default function Link({href,children,...props}){return createElement('a',{...props,href:typeof href==='string'?href:href.pathname+'?'+new URLSearchParams(href.query)},children);}`,loader:'js',resolveDir:frontend}));
 build.onResolve({filter:/^next\/navigation$/},()=>({path:'navigation',namespace:'fixture'}));
 build.onLoad({filter:/.*/,namespace:'fixture'},()=>({contents:`export const usePathname=()=>'/reports/doc';export const useSearchParams=()=>new URLSearchParams();export const useRouter=()=>({replace(){}});`,loader:'js'}));
 }}] });
const requests=[],errors=[],heads=new Map();
let posts=0,holdGraph=false,held=[];
const json=(response,data,status=200)=>{response.writeHead(status,{'Content-Type':'application/json'});response.end(JSON.stringify(data));};
const server=createServer(async(request,response)=>{
 const url=new URL(request.url,'http://localhost');
 if(url.pathname==='/'){response.end('<html><body><div id="root"></div><script src="/bundle.js"></script></body></html>');return;}
 if(url.pathname==='/bundle.js'){response.setHeader('Content-Type','application/javascript');response.end(bundle.outputFiles[0].text);return;}
 let body='';for await(const chunk of request)body+=chunk;
 const owner=request.headers['x-user'];requests.push({path:url.pathname,method:request.method,owner,body});
 if(url.pathname==='/api/ast-templates/recognition-context'){
 const id=url.searchParams.get('template_id')||'a';const item={template_id:id,name:'模板'+id,schema_version:2,recognition_mode:'finder_legacy',finder_profile_id:'cmc_baseline_v1'};
 json(response,{source_job_id:'source',document_iri:'urn:doc',selected:item,templates:[item],selection_required:false});return;
 }
 const match=url.pathname.match(/^\/api\/ast-templates\/([^/]+)\/sources\/source\/finder(.*)$/);
 if(!match){json(response,{detail:'unexpected '+url.pathname},404);return;}
 const template=match[1],part=match[2],key=owner+':'+template;
 if(request.method==='POST'){
 assert(!part);posts++;const eid='execution-'+posts;heads.set(key,eid);
 json(response,{mode:'finder_legacy',template_id:template,source_job_id:'source',execution_id:eid,status:'queued',stage:'queued',has_result:false,stale:false,error:null,counts:{},input:{source_hash:artifact.source.document_hash}},202);return;
 }
 const eid=heads.get(key)||null;
 if(!part){json(response,{mode:'finder_legacy',template_id:template,source_job_id:'source',execution_id:eid,status:eid?'completed':'not_started',stage:eid?'completed':null,input:{source_hash:artifact.source.document_hash},has_result:!!eid,stale:false,error:null,counts:eid?artifact.graph.counts:{}});return;}
 const payload={...(part==='/graph'?artifact.graph:artifact.source),template_id:template,source_job_id:'source',execution_id:url.searchParams.get('execution_id')?eid:null};
 if(part==='/graph'&&holdGraph){held.push(()=>json(response,payload));return;}
 json(response,payload);
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({executablePath:process.env.DOCUMENT_BROWSER_CHROME||'/usr/bin/google-chrome',args:['--no-sandbox']});
try{
 const page=await browser.newPage({viewport:{width:1600,height:1000}});
 page.on('pageerror',error=>{errors.push(error.message);console.error(error.stack);});
 await page.goto('http://127.0.0.1:'+server.address().port);
 await expect(page.getByRole('button',{name:'开始本体指引1.0识别'})).toBeEnabled();
 assert.equal(posts,0);
 await page.getByRole('button',{name:'开始本体指引1.0识别'}).click();
 await expect(page.getByText('性状',{exact:false}).last()).toBeVisible();
 await expect(page.getByRole('button',{name:'查看出处',exact:true})).toHaveCount(2);
 await page.getByRole('button',{name:'查看出处',exact:true}).last().click();
 await expect.poll(()=>page.evaluate(()=>window.getSelection()?.toString())).toBe('重复值😀');
 const expectedId=artifact.graph.relationships[0].object_data_properties[0].source.anchors[0].evidence_id;
 const selectedId=await page.evaluate(()=>window.getSelection()?.anchorNode?.parentElement?.closest('[data-evidence-id]')?.getAttribute('data-evidence-id'));
 assert.equal(selectedId,expectedId);
 await page.getByRole('button',{name:'切换入口'}).click();
 await expect(page.getByRole('button',{name:'重新识别',exact:true})).toHaveCount(2);
 assert.equal(posts,1,'opening report center must reuse the template execution');
 assert(requests.every(r=>!r.path.includes('/document-analysis/')&&!r.path.includes('/batch-demo')&&!r.path.includes('/pde')&&!r.path.includes('/report-runs')));
 await page.getByRole('button',{name:'切换用户'}).click();
 await expect(page.getByRole('button',{name:'开始本体指引1.0识别',exact:true})).toHaveCount(2);
 assert.equal(await page.getByRole('button',{name:'查看出处',exact:true}).count(),0);
 holdGraph=true;
 await page.getByRole('button',{name:'开始本体指引1.0识别',exact:true}).first().click();
 await expect.poll(()=>held.length).toBe(1);
 await page.getByRole('button',{name:'切换模板'}).click();
 await expect(page.getByRole('button',{name:'开始本体指引1.0识别',exact:true})).toHaveCount(2);
 for(const release of held)release();held=[];
 await page.waitForTimeout(100);
 assert.equal(await page.getByRole('button',{name:'查看出处',exact:true}).count(),0,'late graph must not populate new template');
 assert.equal(posts,2);
 assert.deepEqual(errors,[]);
 console.log('Finder browser checks passed: exact source, shared entry execution, caller/template isolation, late response cancellation.');
}finally{
 for(const release of held)release();await browser.close();await new Promise(resolve=>server.close(resolve));
}
