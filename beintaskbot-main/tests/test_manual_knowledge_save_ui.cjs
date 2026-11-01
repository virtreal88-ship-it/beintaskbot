/* Real app DOM/scripts with a synthetic persisted store and controllable HTTP outcomes. */
const assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const read=p=>fs.readFileSync(p,'utf8'),settle=async()=>{for(let i=0;i<30;i++)await new Promise(r=>setImmediate(r));};
const baseline=process.argv.includes('--baseline');
(async()=>{
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app?site_lang=ru',runScripts:'outside-only'}),w=dom.window,d=w.document;
 const member={tenant_id:'tenant',telegram_id:11,role:'owner',active:true};
 const agents=[{id:'primary',name:'Primary',role:'primary',model:'test',mode:'manual',version:1,scopes:[],schedule:{}},{id:'expert',name:'Expert',role:'expert',model:'test',mode:'manual',version:1,scopes:[],schedule:{}}];
 const stored=[],calls=[];let failPost=false,failGet=false,failAfterCommit=false,gate=null;
 d.getElementById('view-ai_agents').classList.add('active');w.tenantProfileSnapshot={member,capabilities:{modules:{ai_agents:true}}};w.confirm=()=>true;w.HTMLElement.prototype.scrollIntoView=function(){};
 w.fetch=async(url,options={})=>{const path=new URL(url,w.location).pathname,method=options.method||'GET';let body=options.body instanceof w.FormData?Object.fromEntries(options.body):options.body?JSON.parse(options.body):{};calls.push({path,method,body});let result={};
  if(path.endsWith('/agents/settings'))result={enabled:true,operating_mode:'manual',company_instructions:''};
  else if(path.endsWith('/agents/options'))result={pipelines:[],stages:[],channels:[],connect_channels:[],models:['test']};
  else if(path.endsWith('/agents'))result=agents;
  else if(path.includes('/knowledge')){
   if(method==='POST'){
    if(failPost)return {ok:false,status:400,json:async()=>({success:false,error:'Validation failed'})};
    result={id:'source-'+(stored.length+1),title:body.title,content:body.content||'Synthetic file text',agent_id:body.agent_id||null,active:true,ai_available:true,material_status:'available',origin_type:'knowledge',chunks:1};stored.push(result);if(failAfterCommit)throw new Error('Connection lost after commit');if(gate)await gate;
   }else{if(failGet)return {ok:false,status:503,json:async()=>({success:false,error:'Reload failed'})};result=stored.map(x=>({...x}));}
  }else if(path.endsWith('/triggers')||path.endsWith('/drafts'))result=[];
  return {ok:true,status:200,json:async()=>({success:true,result})};};
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-ai-agents.js'));await settle();
 assert.equal(d.querySelectorAll('[data-edit-agent]').length,2,'agent bootstrap');
 d.querySelector('[data-edit-agent="primary"]').click();await settle();d.querySelector('[data-ai-section-nav="knowledge"]').click();await settle();
 const title=d.getElementById('aiKnowledgeTitle'),content=d.getElementById('aiKnowledgeContent'),add=d.getElementById('aiAddKnowledge');
 title.value='First';content.value='Confirmed content';let release;gate=new Promise(r=>release=r);
 const first=add.onclick(),duplicate=add.onclick();await settle();assert.equal(stored.length,baseline?2:1,'rapid clicks create one persisted source');release();await Promise.all([first,duplicate]);gate=null;
 assert.equal(title.value,'');assert.equal(content.value,'');assert.equal(stored[0].agent_id,null,'primary manual knowledge remains company-wide');
 title.value='Second';content.value='Keep the entered text';failGet=true;await add.onclick();
 const notice=d.getElementById('aiAgentsNotice').textContent;
 if(baseline){assert.match(notice,/əlavə edildi/);assert.doesNotMatch(notice,/Reload failed/);console.log('REPRODUCED baseline: double POST and success overwrites failed readback');dom.window.close();return;}
 assert.match(notice,/сохран|saved/i);assert.match(notice,/обнов|refresh/i);assert.match(notice,/source-/);assert.equal(title.value,'');assert.equal(content.value,'');
 failGet=false;d.querySelector('[data-ai-section-nav="knowledge"]').click();await settle();assert.equal(d.querySelectorAll('#aiKnowledgeList [data-source-id]').length,2,'later readback finds persisted object');
 failPost=true;title.value='Draft';content.value='Unsaved input';await add.onclick();assert.equal(content.value,'Unsaved input');assert.equal(title.value,'Draft');assert.equal(stored.length,2);
 failPost=false;gate=new Promise(r=>release=r);const pending=add.onclick();await settle();content.value='Newer employee text';release();await pending;gate=null;assert.equal(content.value,'Newer employee text','success cannot clear edits made while POST was in flight');
 // File uploads share the duplicate guard and preserve failed input.
 const file=d.getElementById('aiKnowledgeFile'),fileTitle=d.getElementById('aiKnowledgeFileTitle'),upload=d.getElementById('aiUploadKnowledge');
 Object.defineProperty(file,'files',{configurable:true,value:[new w.File(['Document text'],'fixture.txt',{type:'text/plain'})]});fileTitle.value='File draft';failPost=true;await upload.onclick();assert.equal(fileTitle.value,'File draft');assert.equal(file.files.length,1);
 failPost=false;gate=new Promise(r=>release=r);const a=upload.onclick(),b=upload.onclick();await settle();assert.equal(stored.length,4);release();await Promise.all([a,b]);gate=null;
 assert.equal(stored[3].agent_id,null);assert.equal(fileTitle.value,'');
 assert.equal(d.getElementById('aiSaveAgent').hidden,true,'knowledge has its own explicit save actions');
 // Stale completion after switching agent must not erase a new agent's input.
 title.value='Old target';content.value='For primary';gate=new Promise(r=>release=r);const old=add.onclick();await settle();d.querySelector('[data-edit-agent="expert"]').click();await settle();title.value='Expert draft';content.value='Expert new text';release();await old;gate=null;assert.equal(title.value,'Expert draft');assert.equal(content.value,'Expert new text');
 await add.onclick();assert.equal(stored.at(-1).agent_id,'expert','expert target captured explicitly');
 title.value='Uncertain outcome';content.value='Retain this input';failAfterCommit=true;const beforeUnknown=stored.length;await add.onclick();
 assert.equal(stored.length,beforeUnknown+1);assert.equal(content.value,'Retain this input');assert.match(d.getElementById('aiAgentsNotice').textContent,/не подтверждено/);assert.match(d.getElementById('aiAgentsNotice').textContent,/проверьте список/);
 failAfterCommit=false;d.querySelector('[data-ai-section-nav="knowledge"]').click();await settle();assert.equal(stored.length,beforeUnknown+1,'readback does not retry ambiguous write');
 assert.equal(calls.filter(c=>/publish|operator-submit|copilot|send/.test(c.path)).length,0);
 dom.window.close();console.log('PASS manual knowledge persistence/readback/failure/duplicate/stale-target/file/explicit-save semantics');
})().catch(e=>{console.error(e);process.exitCode=1;});
