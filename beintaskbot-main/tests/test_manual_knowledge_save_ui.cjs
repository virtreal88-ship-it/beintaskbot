/* Real DOM/scripts; synthetic durable HTTP store, browser storage and Web Locks. No provider calls. */
const assert=require('node:assert/strict'),fs=require('node:fs'),{webcrypto}=require('node:crypto');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const read=p=>fs.readFileSync(p,'utf8'),settle=async()=>{for(let i=0;i<40;i++)await new Promise(r=>setImmediate(r));};
const persisted=new Map(),storage=new Map(),calls=[];let failPost=false,failList=false,failRead=false,failAfterCommit=false,badJsonAfterCommit=false,gate=null,wrongReceipt=false;
const agents=[{id:'primary',name:'Primary',role:'primary',model:'test',mode:'manual',version:1,scopes:[],schedule:{}},{id:'primary2',name:'Primary 2',role:'primary',model:'test',mode:'manual',version:1,scopes:[],schedule:{}},{id:'expert',name:'Expert',role:'expert',model:'test',mode:'manual',version:1,scopes:[],schedule:{}},{id:'expert2',name:'Expert 2',role:'expert',model:'test',mode:'manual',version:1,scopes:[],schedule:{}}];
const waitFor=async predicate=>{for(let i=0;i<500;i++){if(predicate())return;await new Promise(r=>setTimeout(r,10));}throw new Error('Fixture condition timed out');};
let lockTail=Promise.resolve();const locks={request:async(key,fn)=>{const previous=lockTail;let release;lockTail=new Promise(r=>release=r);await previous;try{return await fn();}finally{release();}}};
async function boot(tenant='tenant',actor=11,agent='primary'){
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app?site_lang=ru',runScripts:'outside-only'}),w=dom.window,d=w.document;
 const member={tenant_id:tenant,telegram_id:actor,role:'owner',active:true};
 let selectedFiles=[];const picker=d.getElementById('aiKnowledgeFile');Object.defineProperty(picker,'files',{configurable:true,get:()=>selectedFiles,set:value=>{selectedFiles=[...value];}});Object.defineProperty(picker,'value',{configurable:true,get:()=>'',set:value=>{if(value==='')selectedFiles=[];}});w.DataTransfer=class{constructor(){this.files=[];this.items={add:file=>this.files.push(file)};}};
 Object.defineProperty(w,'crypto',{value:webcrypto});w.TextEncoder=TextEncoder;Object.defineProperty(w.navigator,'locks',{value:locks});
 Object.defineProperty(w,'localStorage',{value:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)}});
 d.getElementById('view-ai_agents').classList.add('active');w.tenantProfileSnapshot={member,capabilities:{modules:{ai_agents:true}}};w.confirm=()=>true;w.HTMLElement.prototype.scrollIntoView=function(){};
 w.fetch=async(url,options={})=>{const parsed=new URL(url,w.location),path=parsed.pathname,method=options.method||'GET';const body=options.body instanceof w.FormData?Object.fromEntries(options.body):options.body?JSON.parse(options.body):{};calls.push({path,method,body,tenant,actor,query:parsed.search});let result={};
  if(path.endsWith('/agents/settings'))result={enabled:true,operating_mode:'manual',company_instructions:''};
  else if(path.endsWith('/agents/options'))result={pipelines:[],stages:[],channels:[],connect_channels:[],models:['test']};
  else if(path.endsWith('/agents'))result=agents;
  else if(path.includes('/knowledge')){
   if(method==='POST'){
    if(failPost)return {ok:false,status:400,json:async()=>({success:false,error:'Validation failed'})};
    const id=body.create_request_id;assert.match(id,/^[0-9a-f-]{36}$/);
    assert.equal(persisted.has(id),false,'UI never resends a dispatched operation');
    result={id,create_request_id:id,tenant_id:tenant,created_by:actor,kind:body.kind||'txt',title:body.title,content:body.content||'Synthetic file text',agent_id:body.agent_id||null,active:true,ai_available:true,material_status:'available',origin_type:'knowledge',chunks:1};persisted.set(id,result);
    if(failAfterCommit)throw new Error('Connection lost after commit');
    if(badJsonAfterCommit)return {ok:true,status:200,json:async()=>{throw new SyntaxError('Truncated response');}};
    if(gate)await gate;
   }else if(parsed.searchParams.has('id')){
    if(failRead)return {ok:false,status:503,json:async()=>({success:false,error:'Read unavailable'})};
    assert.equal(parsed.searchParams.get('create_request_id'),parsed.searchParams.get('id'));result=persisted.get(parsed.searchParams.get('id'));
    if(!result||result.tenant_id!==tenant)return {ok:false,status:404,json:async()=>({success:false,error:'Missing'})};
    if(wrongReceipt)result={...result,create_request_id:'unrelated'};
   }else{if(failList)return {ok:false,status:503,json:async()=>({success:false,error:'Reload failed'})};result=[...persisted.values()].filter(x=>x.tenant_id===tenant).map(x=>({...x}));}
  }else if(path.endsWith('/triggers')||path.endsWith('/drafts'))result=[];
  return {ok:true,status:200,json:async()=>({success:true,result})};};
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-ai-agents.js'));await settle();
 assert.equal(d.querySelectorAll('[data-edit-agent]').length,4,'agent bootstrap');
 const select=async id=>{d.querySelector('[data-edit-agent="'+id+'"]').click();await settle();d.querySelector('[data-ai-section-nav="knowledge"]').click();await settle();};await select(agent);
 return {dom,w,d,select,title:d.getElementById('aiKnowledgeTitle'),content:d.getElementById('aiKnowledgeContent'),add:d.getElementById('aiAddKnowledge'),notice:()=>d.getElementById('aiAgentsNotice').textContent};
}
const postCount=()=>calls.filter(x=>x.method==='POST'&&x.path.includes('/knowledge')).length;
(async()=>{
 let ui=await boot();const fill=(u,title,content)=>{u.title.value=title;u.content.value=content;};
 fill(ui,'First','Confirmed content');let release;gate=new Promise(r=>release=r);const first=ui.add.onclick(),duplicate=ui.add.onclick();await waitFor(()=>persisted.size===1);assert.equal(persisted.size,1);release();await Promise.all([first,duplicate]);gate=null;
 assert.equal(ui.content.value,'');assert.equal([...persisted.values()][0].agent_id,null);assert.equal(ui.d.getElementById('aiSaveAgent').hidden,true);
 // Successful POST, failed list: source is proven via exact detail; repeated input only reads.
 fill(ui,'Second','Known persisted');failList=true;await ui.add.onclick();assert.match(ui.notice(),/сохранён/);assert.match(ui.notice(),/заблокирована/);assert.equal(ui.content.value,'');failList=false;
 let before=postCount();fill(ui,'Second','Known persisted');await ui.add.onclick();assert.equal(postCount(),before);
 // Successful POST but failed detail leaves an accepted lock and input, never another POST.
 fill(ui,'Detail unavailable','Retain accepted input');failRead=true;await ui.add.onclick();before=postCount();await ui.add.onclick();assert.equal(postCount(),before);assert.equal(ui.content.value,'Retain accepted input');assert.match(ui.notice(),/сохранён/);failRead=false;await ui.add.onclick();assert.equal(postCount(),before);assert.equal(ui.content.value,'');
 // Definite failed POST still cannot be blindly resent; changed input is a new operation.
 failPost=true;fill(ui,'Validation','Invalid draft');await ui.add.onclick();before=postCount();failPost=false;await ui.add.onclick();assert.equal(postCount(),before);assert.equal(ui.content.value,'Invalid draft');assert.match(ui.notice(),/Validation failed/);assert.match(ui.notice(),/Исправьте/);
 ui.content.value='Corrected different draft';await ui.add.onclick();assert.equal(postCount(),before+1);
 // Later edits survive a known success.
 fill(ui,'In flight','Sent text');gate=new Promise(r=>release=r);const pending=ui.add.onclick();await settle();ui.content.value='Newer employee text';release();await pending;gate=null;assert.equal(ui.content.value,'Newer employee text');
 // Stale completion after navigation cannot clear another editor or issue its readback there.
 fill(ui,'Old editor','For primary');gate=new Promise(r=>release=r);const staleBefore=postCount(),stale=ui.add.onclick();await waitFor(()=>postCount()>staleBefore);await ui.select('expert');assert.equal(ui.title.value,'');assert.equal(ui.content.value,'');fill(ui,'Expert','Expert new text');release();await stale;gate=null;assert.equal(ui.content.value,'Expert new text');await ui.add.onclick();assert.equal([...persisted.values()].at(-1).agent_id,'expert');
 // Commit + transport loss, repeated clicks and wrong/missing receipts stay GET-only.
 fill(ui,'Unknown','Keep unknown input');failAfterCommit=true;await ui.add.onclick();failAfterCommit=false;before=postCount();assert.match(ui.notice(),/не подтверждено/);wrongReceipt=true;await ui.add.onclick();assert.equal(postCount(),before);assert.equal(ui.content.value,'Keep unknown input');wrongReceipt=false;
 // Full JS restart keeps metadata-only lock; text is recovered only by authorized server GET.
 ui.dom.window.close();ui=await boot('tenant',11,'expert');assert.equal(ui.content.value,'Keep unknown input');await ui.add.onclick();assert.equal(postCount(),before);assert.equal(ui.content.value,'');
 // Receipt survives another restart; same content cannot become a fresh write.
 ui.dom.window.close();ui=await boot('tenant',11,'expert');fill(ui,'Unknown','Keep unknown input');await ui.add.onclick();assert.equal(postCount(),before);
 // Commit + malformed HTTP 200 is also locked, and a different edited operation is allowed.
 fill(ui,'Malformed','Keep malformed-response draft');badJsonAfterCommit=true;await ui.add.onclick();badJsonAfterCommit=false;before=postCount();assert.match(ui.notice(),/не подтверждено/);await ui.add.onclick();assert.equal(postCount(),before);fill(ui,'Malformed','New independent edited draft');await ui.add.onclick();assert.equal(postCount(),before+1);
 // File identity hashes bytes: loss/restart/reselect performs only receipt readback.
 const setFile=u=>{const f=u.d.getElementById('aiKnowledgeFile');f.files=[new u.w.File(['Document text'],'fixture.txt',{type:'text/plain'})];u.d.getElementById('aiKnowledgeFileTitle').value='File draft';return u.d.getElementById('aiUploadKnowledge');};
 let upload=setFile(ui);failAfterCommit=true;await upload.onclick();failAfterCommit=false;before=postCount();ui.dom.window.close();ui=await boot('tenant',11,'expert');assert.equal(ui.d.getElementById('aiKnowledgeFileTitle').value,'','file name/title is not restored from browser storage');upload=setFile(ui);await upload.onclick();assert.equal(postCount(),before);
 // Primary editors/channels share company-wide scope, while expert/tenant/actor do not.
 await ui.select('primary2');fill(ui,'First','Confirmed content');before=postCount();await ui.add.onclick();assert.equal(postCount(),before,'another primary editor must reuse company receipt');
 const other=await boot('other-tenant',11);fill(other,'First','Confirmed content');await other.add.onclick();assert.equal(postCount(),before+1);assert.equal([...persisted.values()].at(-1).tenant_id,'other-tenant');
 const actor=await boot('tenant',22);fill(actor,'First','Confirmed content');await actor.add.onclick();assert.equal(postCount(),before+2);assert.equal([...persisted.values()].at(-1).created_by,22);
 // Concurrent tabs serialize the same operation before POST via Web Locks.
 const tab=await boot();fill(ui,'Two tabs','One operation');fill(tab,'Two tabs','One operation');before=postCount();await Promise.all([ui.add.onclick(),tab.add.onclick()]);assert.equal(postCount(),before+1);
 // A tenant change while POST is in flight leaves the old operation scoped and
 // cannot clear the new tenant's editor or read back old evidence in that context.
 const switched=await boot('tenant-flight');fill(switched,'Tenant flight','Old tenant input');gate=new Promise(r=>release=r);const switchBefore=postCount(),switchPending=switched.add.onclick();await waitFor(()=>postCount()>switchBefore);const oldId=[...persisted.keys()].at(-1);
 switched.d.dispatchEvent(new switched.w.CustomEvent('tenant-profile',{detail:{member:{tenant_id:'tenant-after',telegram_id:11,role:'owner',active:true},capabilities:{modules:{ai_agents:true}}}}));await settle();await switched.select('primary');fill(switched,'New tenant','Do not clear');const readBefore=calls.filter(c=>c.query.startsWith('?id='+oldId+'&')).length;release();await switchPending;gate=null;assert.equal(switched.content.value,'Do not clear');assert.equal(calls.filter(c=>c.query.startsWith('?id='+oldId+'&')).length,readBefore);
 switched.dom.window.close();const restored=await boot('tenant-flight');assert.equal(restored.content.value,'Old tenant input');before=postCount();await restored.add.onclick();assert.equal(postCount(),before);restored.dom.window.close();
 // Editor/tenant/actor scopes own their in-memory text and File objects.
 const scoped=await boot('scope-only',11,'expert');fill(scoped,'Expert A title','Expert A private draft');setFile(scoped);gate=new Promise(r=>release=r);const scopedBefore=postCount(),scopedPending=scoped.add.onclick();await waitFor(()=>postCount()>scopedBefore);await scoped.select('expert2');assert.equal(scoped.content.value,'');assert.equal(scoped.title.value,'');assert.equal(scoped.d.getElementById('aiKnowledgeFileTitle').value,'');assert.equal(scoped.d.getElementById('aiKnowledgeFile').files.length,0);fill(scoped,'Expert B title','Expert B draft');release();await scopedPending;gate=null;assert.equal(scoped.content.value,'Expert B draft');await scoped.select('expert');assert.equal(scoped.content.value,'Expert A private draft');assert.equal(scoped.d.getElementById('aiKnowledgeFile').files[0].name,'fixture.txt');
 const changeProfile=async(tenantId,actorId)=>{scoped.d.dispatchEvent(new scoped.w.CustomEvent('tenant-profile',{detail:{member:{tenant_id:tenantId,telegram_id:actorId,role:'owner',active:true},capabilities:{modules:{ai_agents:true}}}}));await settle();};
 await changeProfile('scope-other',22);assert.equal(scoped.content.value,'');assert.equal(scoped.d.getElementById('aiKnowledgeFile').files.length,0);await scoped.select('expert');assert.equal(scoped.content.value,'');fill(scoped,'Other tenant','Other actor draft');await changeProfile('scope-only',11);await scoped.select('expert');assert.equal(scoped.content.value,'Expert A private draft');assert.equal(scoped.d.getElementById('aiKnowledgeFile').files[0].name,'fixture.txt');
 scoped.d.dispatchEvent(new scoped.w.CustomEvent('tenant-auth-invalid'));assert.equal(scoped.content.value,'');assert.equal(scoped.d.getElementById('aiKnowledgeFile').files.length,0);scoped.dom.window.close();
 // Unknown operation with no committed source cannot recover plaintext on restart.
 const absent=await boot('absent-source');fill(absent,'Never committed title','Never committed private input');failPost=true;await absent.add.onclick();failPost=false;const absentPosts=postCount();absent.dom.window.close();const absentReload=await boot('absent-source');assert.equal(absentReload.content.value,'');assert.match(absentReload.notice(),/черновик недоступен/);fill(absentReload,'Never committed title','Never committed private input');await absentReload.add.onclick();assert.equal(postCount(),absentPosts);absentReload.dom.window.close();
 // Persisted footprint is metadata-only, including after rejection and file upload.
 const allowed=['id','fingerprint','tenant','actor','agentId','editorId','state','createdAt'];for(const value of storage.values()){for(const row of Object.values(JSON.parse(value))){assert.deepEqual(Object.keys(row).sort(),[...allowed].sort());assert.equal(typeof row.createdAt,'number');}assert.doesNotMatch(value,/private draft|fixture\.txt|Validation failed|Document text|Never committed/);}
 // Storage failure is fail-closed before dispatch.
 const denied=await boot('storage-denied');Object.defineProperty(denied.w,'localStorage',{value:{getItem:()=>null,setItem:()=>{throw new Error('quota');}}});fill(denied,'Storage','Do not send');before=postCount();await denied.add.onclick();assert.equal(postCount(),before);assert.match(denied.notice(),/Отправка не выполнена/);
 assert.equal(calls.filter(c=>/publish|operator-submit|copilot|send/.test(c.path)).length,0);
 for(const item of [ui,other,actor,tab,denied])item.dom.window.close();
 console.log('PASS manual knowledge stable identity, GET-only uncertain reconciliation, restart, edited operation, tenant/actor/audience fencing, concurrent tabs, file and storage failure');
})().catch(e=>{console.error(e);process.exitCode=1;});
