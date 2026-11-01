/* Native Chromium FileList/DataTransfer and real agent console; APIs are local fixtures. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'../.support-test-deps/node_modules/playwright');
const root=path.join(__dirname,'..');
(async()=>{const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined,args:['--no-sandbox']});try{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('https://test.local/**',r=>r.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')}));await page.goto('https://test.local/app');
 await page.evaluate(()=>{
  document.getElementById('view-ai_agents').classList.add('active');window.confirm=()=>true;
  window.testProfile=(tenant='tenant-a',actor=11)=>({member:{tenant_id:tenant,telegram_id:actor,role:'owner',active:true},capabilities:{modules:{ai_agents:true}}});window.tenantProfileSnapshot=window.testProfile();window.posts=[];window.sources={};
  const agents=['expert-a','expert-b'].map(id=>({id,name:id,role:'expert',model:'fixture',version:1,mode:'manual',scopes:[],schedule:{}}));
  window.fetch=async(url,options={})=>{const u=new URL(url,location.href);let result={};
   if(u.pathname.endsWith('/agents/settings'))result={enabled:true,operating_mode:'manual'};
   else if(u.pathname.endsWith('/agents/options'))result={pipelines:[],stages:[],channels:[],connect_channels:[],models:['fixture']};
   else if(u.pathname.endsWith('/agents'))result=agents;
   else if(u.pathname.includes('/knowledge')){
    if(options.method==='POST'){const body=JSON.parse(options.body);window.posts.push(body);result={...body,id:body.create_request_id,tenant_id:body.expected_tenant_id,created_by:body.expected_user_id,origin_type:'knowledge',ai_available:true};window.sources[result.id]=result;await new Promise(resolve=>window.releaseSave=resolve);}
    else result=u.searchParams.has('id')?window.sources[u.searchParams.get('id')]:Object.values(window.sources);
   }else if(u.pathname.endsWith('/triggers')||u.pathname.endsWith('/drafts'))result=[];
   return {ok:true,status:200,json:async()=>({success:true,result})};
  };
 });
 for(const file of ['tenant-operator-i18n.js','tenant-ai-agents.js'])await page.addScriptTag({path:path.join(root,'docs',file)});
 await page.waitForFunction(()=>document.querySelectorAll('[data-edit-agent]').length===2);
 const select=async id=>{await page.evaluate(id=>{document.querySelector(`[data-edit-agent="${id}"]`).click();document.querySelector('[data-ai-section-nav="knowledge"]').click();},id);};
 const fields=()=>page.evaluate(()=>({title:document.getElementById('aiKnowledgeTitle').value,content:document.getElementById('aiKnowledgeContent').value,fileTitle:document.getElementById('aiKnowledgeFileTitle').value,files:[...document.getElementById('aiKnowledgeFile').files].map(f=>f.name)}));
 const empty={title:'',content:'',fileTitle:'',files:[]};
 await select('expert-a');await page.evaluate(()=>{document.getElementById('aiKnowledgeTitle').value='Private title A';document.getElementById('aiKnowledgeContent').value='Private draft A';document.getElementById('aiKnowledgeFileTitle').value='Private file title A';});
 await page.setInputFiles('#aiKnowledgeFile',{name:'private-a.txt',mimeType:'text/plain',buffer:Buffer.from('Private file bytes A')});const original=await fields();
 await page.evaluate(()=>{window.savePromise=document.getElementById('aiAddKnowledge').onclick();});await page.waitForFunction(()=>window.posts.length===1);
 await select('expert-b');assert.deepEqual(await fields(),empty);await page.evaluate(()=>document.getElementById('aiKnowledgeContent').value='Draft B');
 await page.evaluate(async()=>{window.releaseSave();await window.savePromise;});assert.equal((await fields()).content,'Draft B');
 await select('expert-a');assert.deepEqual(await fields(),original);assert.equal(await page.evaluate(()=>document.getElementById('aiKnowledgeFile').files[0].text()),'Private file bytes A');
 for(const [tenant,actor] of [['tenant-b',11],['tenant-a',22]]){
  await page.evaluate(([t,a])=>document.dispatchEvent(new CustomEvent('tenant-profile',{detail:window.testProfile(t,a)})),[tenant,actor]);await page.waitForFunction(()=>document.querySelectorAll('[data-edit-agent]').length===2);await select('expert-a');assert.deepEqual(await fields(),empty);
  await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-profile',{detail:window.testProfile()})));await page.waitForFunction(()=>document.querySelectorAll('[data-edit-agent]').length===2);await select('expert-a');assert.deepEqual(await fields(),original);
 }
 await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-auth-invalid')));assert.deepEqual(await fields(),empty);
 const storage=await page.evaluate(()=>JSON.stringify(Object.fromEntries(Object.entries(localStorage))));assert.doesNotMatch(storage,/Private|private-a|Draft B/);assert.equal(await page.evaluate(()=>window.posts.length),1);assert.deepEqual(errors,[]);
 console.log('PASS native knowledge draft/file scope: expert, tenant, actor, in-flight completion, return, logout, metadata-only storage; one synthetic POST');
 await page.close();
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
