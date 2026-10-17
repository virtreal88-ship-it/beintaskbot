const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const root=path.join(__dirname,'..'),read=file=>fs.readFileSync(path.join(root,file),'utf8');
const A='00000000-0000-0000-0000-000000000101',B='00000000-0000-0000-0000-000000000102',CA='00000000-0000-0000-0000-000000000201',CB='00000000-0000-0000-0000-000000000202';
const tick=async()=>{for(let i=0;i<15;i++)await new Promise(r=>setImmediate(r));};
function channel(id,name,extra={}){return {id:'connect:'+id,provider:'connect',channel_id:id,type:'whatsapp',name,connection:{state:'connected',source:'cached',checked_at:null},ai:{supported:true,available:true,reason_codes:[]},capabilities:{text_replies:true,drafts:true,groups:true,incoming_voice:true,voice_replies:false,crm_actions:false},scope:{provider:'connect',channel_id:id},requirements:[],...extra};}
function setup({role='owner',channels,denied=false}={}){
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app?site_lang=ru',runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[],errors=[];
 w.alert=()=>{};w.confirm=()=>true;w.HTMLElement.prototype.scrollIntoView=function(){};w.addEventListener('error',e=>errors.push(e.error));
 const member={tenant_id:A,telegram_id:11,role,active:true},company={enabled:true,operating_mode:'test',auto_send_enabled:false,emergency_stop:false,max_replies_per_chat:20,company_instructions:'Company rules'};
 let rows=channels||[channel(CA,'Основной номер'),channel(CB,'Техническая поддержка'),channel('off','Неактивный номер',{connection:{state:'disabled'},ai:{supported:true,available:false,reason_codes:['channel_inactive']}}),channel('instagram','Instagram',{type:'instagram'}),{id:'kommo:wa',provider:'kommo',channel_id:'wa',type:'unknown',name:'CRM — продажи',connection:{state:'connected'},ai:{supported:true,available:true,reason_codes:[]},capabilities:{text_replies:true,drafts:true},scope:{provider:'kommo',channel_id:'wa'},requirements:['pipeline_id','stage_id']}];
 const agents=new Map(),triggers=new Map(),knowledge=new Map();let failAgent=false,failUpload=false,delayAgent=false,release,failCatalog=false;
 const result=value=>({ok:true,status:200,json:async()=>({success:true,result:structuredClone(value)})});
 w.fetch=async(url,init={})=>{
  const u=new URL(String(url),'https://crm.test'),method=init.method||'GET',data=init.body instanceof w.FormData?Object.fromEntries(init.body):init.body?JSON.parse(init.body):null;calls.push({path:u.pathname,url:String(url),method,data});
  if(u.pathname==='/api/platform/me')return {ok:true,status:200,json:async()=>({success:true,member,capabilities:{modules:{ai_agents:role==='owner'||role==='admin'}}})};
  if(u.pathname.endsWith('/channels')){
   if(denied)return {ok:false,status:403,json:async()=>({success:false,error:'No access'})};
   if(failCatalog)throw Error('Каталог подключений недоступен');
   return result({schema_version:1,channels:rows});
  }
  if(u.pathname.endsWith('/agents/settings'))return result(company);
  if(u.pathname.endsWith('/agents/options'))return result({connect_channels:[],pipelines:[{pipeline_id:'10',name:'Поддержка'}],stages:[{pipeline_id:'10',stage_id:'11',name:'Новое обращение'}],channels:[],models:[{id:'fixture-model',label:'Модель компании'}]});
  if(u.pathname.endsWith('/agents/test-mode'))return result({connect:{enabled:false},enabled:false});
  if(u.pathname.endsWith('/knowledge/upload')){
   const saved=knowledge.get(data.setup_key)||{id:'source-'+(knowledge.size+1),agent_id:data.agent_id,title:data.title,active:true};knowledge.set(data.setup_key,saved);
   if(failUpload){failUpload=false;throw Error('Ответ загрузки потерян');}return result(saved);
  }
  if(u.pathname.endsWith('/knowledge'))return result([...knowledge.values()]);
  if(u.pathname.endsWith('/agents')&&method==='GET')return result([...agents.values()]);
  if(u.pathname.endsWith('/agents')&&method==='POST'){
   const key=data.setup_key;const saved=agents.get(key)||{...data,id:'00000000-0000-0000-0000-'+String(agents.size+501).padStart(12,'0'),tenant_id:A,version:1};agents.set(key,saved);
   if(failAgent){failAgent=false;throw Error('Ответ создания потерян');}
   if(delayAgent){delayAgent=false;return await new Promise(resolve=>release=()=>resolve(result(saved)));}return result(saved);
  }
  if(u.pathname.endsWith('/triggers')&&method==='POST'){const saved=triggers.get(data.setup_key)||{id:'trigger-'+(triggers.size+1),...data};triggers.set(data.setup_key,saved);return result(saved);}
  if(u.pathname.includes('/agents/')&&method==='PATCH'){const agent=[...agents.values()].find(a=>u.pathname.endsWith(a.id));Object.assign(agent,data,{version:agent.version+1});return result(agent);}
  if(u.pathname.endsWith('/dialogs'))return result({dialogs:[],next_offset:null});
  if(u.pathname.endsWith('/triggers')||u.pathname.endsWith('/drafts'))return result([]);
  throw Error('Unexpected request '+url);
 };
 for(const file of ['tenant-operator-i18n.js','tenant-ai-agents.js','tenant-night-l10n.js','tenant-agent-catalog.js','tenant-night-agent-template.js','tenant-agent-library.js'])w.eval(read('docs/'+file));
 return {w,d,calls,errors,agents,triggers,knowledge,member,company,dom,setChannels(value){rows=value},failAgent(){failAgent=true},failUpload(){failUpload=true},delayAgent(){delayAgent=true},release(){release()},failCatalog(value){failCatalog=value}};
}
function choose(app,id){const box=app.d.querySelector(`[name=agentCreationChannel][value="${id}"]`);assert.ok(box,'real connection exists');assert.equal(box.disabled,false);box.checked=true;box.dispatchEvent(new app.w.Event('change'));}
function setFile(app,name='manual.txt'){const file=new app.w.File(['Router manual'],name,{type:'text/plain',lastModified:123});Object.defineProperty(app.d.getElementById('nightAgentKnowledgeFile'),'files',{configurable:true,value:[file]});return file;}
async function nextAll(app){for(let i=0;i<9;i++){app.d.getElementById('nightAgentNext').click();await tick();}assert.equal(app.d.getElementById('nightAgentWizard').dataset.step,'9',app.d.getElementById('nightAgentNotice').textContent);}
async function open(app){await tick();app.d.querySelector('[data-ai-overview-create]').click();await tick();assert.equal(app.d.getElementById('agentTemplateLibrary').hidden,false);}
async function run(){
 const app=setup();await open(app);const {d,w}=app;
 assert.equal(d.querySelectorAll('#aiNightTemplateQuick').length,0,'no new header action');assert.equal(d.querySelectorAll('[data-ai-overview-create]').length,1);
 assert.equal(d.querySelectorAll('[data-library-category]').length,5);assert.equal(d.querySelectorAll('[data-agent-template]').length,1);assert.match(d.getElementById('agentLibraryCards').textContent,/Оператор технической поддержки/);assert.equal(d.getElementById('agentLibraryBlank').disabled,false);
 d.querySelector('[data-library-category=sales]').click();assert.equal(d.querySelectorAll('[data-agent-template]').length,0);assert.match(d.getElementById('agentLibraryCards').textContent,/пока нет/);d.querySelector('[data-library-category=all]').click();
 d.getElementById('agentLibraryChannelFilter').value='kommo:wa';d.getElementById('agentLibraryChannelFilter').dispatchEvent(new w.Event('change'));assert.match(d.getElementById('agentLibraryCards').textContent,/CRM — продажи/);assert.doesNotMatch(d.getElementById('agentLibraryCards').textContent,/Основной номер/);
 d.getElementById('agentLibraryChannelFilter').value='all';d.getElementById('agentLibraryChannelFilter').dispatchEvent(new w.Event('change'));
 d.querySelector('[data-agent-template]').click();await tick();assert.equal(d.getElementById('nightAgentTemplateModal').hidden,false);assert.equal(d.getElementById('nightAgentName').value,'Оператор технической поддержки');assert.doesNotMatch(d.getElementById('nightAgentInstructions').value,/ночной/);assert.equal(d.getElementById('nightAgentMode').value,'manual');assert.equal(d.getElementById('nightAgentLaunch').checked,false);
 assert.equal(d.querySelector('[name=agentCreationChannel][value="connect:instagram"]'),null);assert.equal(d.querySelector('[name=agentCreationChannel][value="connect:off"]').disabled,true);
 choose(app,'connect:'+CA);choose(app,'connect:'+CB);setFile(app);d.getElementById('nightAgentStart').value='19:00';d.getElementById('nightAgentEnd').value='09:00';await nextAll(app);d.getElementById('nightAgentCreate').click();await tick();
 assert.equal(app.agents.size,1);const agent=[...app.agents.values()][0];assert.equal(agent.enabled,false);assert.equal(agent.mode,'manual');assert.equal(agent.schedule.start,'19:00');assert.equal(agent.schedule.end,'09:00');assert.deepEqual(agent.scopes.map(s=>s.channel_id),[CA,CB]);assert.ok(agent.scopes.every(s=>s.allow_group_messages===false));assert.equal(app.knowledge.size,1);assert.equal([...app.knowledge.values()][0].agent_id,agent.id);assert.equal(app.triggers.size,1);assert.match(d.getElementById('nightAgentNotice').textContent,/Готово/);
 d.getElementById('nightAgentCreate').click();await tick();assert.equal(app.agents.size,1);assert.equal(app.calls.filter(c=>c.path.endsWith('/agents')&&c.method==='POST').length,1);
 assert.ok(!app.calls.some(c=>c.path.endsWith('/agents/settings')&&c.method!=='GET'));assert.equal(app.company.operating_mode,'test');assert.equal(app.company.auto_send_enabled,false);
 d.getElementById('nightAgentOpenEditor').click();await tick();assert.equal(d.getElementById('aiAgentId').value,agent.id);assert.equal(d.getElementById('aiAgentsModal').hidden,false);
 // Blank creation uses the same idempotent wizard and no unsolicited handoff or shared upload.
 await open(app);d.getElementById('agentLibraryBlank').click();await tick();assert.equal(d.getElementById('nightAgentName').value,'');assert.equal(d.getElementById('nightAgentInstructions').value,'');assert.equal(d.getElementById('nightAgentHandoff').value,'');assert.equal(d.getElementById('nightAgentScheduleEnabled').checked,false);
 Object.defineProperty(d.getElementById('nightAgentKnowledgeFile'),'files',{configurable:true,value:[]});d.getElementById('nightAgentName').value='Собственный агент';d.getElementById('nightAgentInstructions').value='Мои инструкции';choose(app,'kommo:wa');assert.equal(d.getElementById('nightAgentKommoFields').hidden,false);d.getElementById('nightAgentPipeline').value='10';d.getElementById('nightAgentPipeline').dispatchEvent(new w.Event('change'));d.getElementById('nightAgentStage').value='11';await nextAll(app);d.getElementById('nightAgentCreate').click();await tick();assert.equal(app.agents.size,2);const blank=[...app.agents.values()][1];assert.equal(blank.enabled,false);assert.deepEqual(blank.schedule,{});assert.equal(blank.scopes[0].channel_id,'wa');assert.equal(blank.scopes[0].provider,'kommo');assert.equal(blank.scopes[0].pipeline_id,'10');assert.equal(blank.scopes[0].stage_id,'11');assert.equal(app.triggers.size,1);assert.equal(app.knowledge.size,1);
 assert.equal(app.errors.length,0,String(app.errors[0]));w.close();
 // Each actual WhatsApp choice is carried to the saved scope, for both available creation paths.
 for(const [id,blank] of [[CA,false],[CB,true]]){
  const scoped=setup();await open(scoped);const filter=scoped.d.getElementById('agentLibraryChannelFilter');
  assert.ok([...filter.options].some(o=>o.value==='connect:'+CA));assert.ok([...filter.options].some(o=>o.value==='connect:'+CB));
  assert.ok(![...filter.options].some(o=>['connect','kommo'].includes(o.value)));
  filter.value='connect:'+id;filter.dispatchEvent(new scoped.w.Event('change'));
  scoped.d.querySelector(blank?'#agentLibraryBlank':'[data-agent-template]').click();await tick();
  assert.deepEqual([...scoped.d.querySelectorAll('[name=agentCreationChannel]:checked')].map(b=>b.value),['connect:'+id]);
  if(blank){scoped.d.getElementById('nightAgentName').value='My scoped agent';scoped.d.getElementById('nightAgentInstructions').value='Answer using verified facts';}
  if(!blank)setFile(scoped);await nextAll(scoped);scoped.d.getElementById('nightAgentCreate').click();await tick();
  const saved=[...scoped.agents.values()][0];assert.deepEqual(saved.scopes.map(s=>[s.provider,s.channel_id]),[['connect',id]]);assert.equal(saved.enabled,false);assert.equal(saved.mode,'manual');
  assert.ok(!scoped.calls.some(c=>c.path.endsWith('/agents/settings')&&c.method!=='GET'));scoped.w.close();
 }
 // A channel lost between library and wizard must not silently select another connection.
 const staleChoice=setup();await open(staleChoice);const filter=staleChoice.d.getElementById('agentLibraryChannelFilter');filter.value='connect:'+CB;filter.dispatchEvent(new staleChoice.w.Event('change'));
 staleChoice.setChannels([channel(CA,'First')]);staleChoice.d.querySelector('[data-agent-template]').click();await tick();
 assert.equal(staleChoice.d.getElementById('nightAgentNext').disabled,true);assert.equal(staleChoice.agents.size,0);assert.match(staleChoice.d.getElementById('nightAgentNotice').textContent,/недоступ/);staleChoice.w.close();
 // Lost first POST response: the key exists before the response, survives close/reopen and deduplicates.
 const lost=setup();await open(lost);lost.d.querySelector('[data-agent-template]').click();await tick();choose(lost,'connect:'+CA);setFile(lost);await nextAll(lost);lost.failAgent();lost.d.getElementById('nightAgentCreate').click();await tick();assert.equal(lost.agents.size,1);const first=lost.calls.find(c=>c.path.endsWith('/agents')&&c.method==='POST').data.setup_key;
 lost.d.querySelector('#nightAgentTemplateModal [data-night-close]').click();await lost.w.tenantAgentWizard.open({templateId:'technical-support'});setFile(lost);await nextAll(lost);lost.failUpload();lost.d.getElementById('nightAgentCreate').click();await tick();assert.equal(lost.agents.size,1);assert.equal(lost.triggers.size,1);assert.equal(lost.knowledge.size,1);lost.d.getElementById('nightAgentCreate').click();await tick();assert.equal(lost.agents.size,1);assert.equal(lost.triggers.size,1);assert.equal(lost.knowledge.size,1);assert.ok(lost.calls.filter(c=>c.path.endsWith('/agents')&&c.method==='POST').every(c=>c.data.setup_key===first));assert.match(lost.d.getElementById('nightAgentNotice').textContent,/Готово/);lost.w.close();
 // Permission and missing-contract failures never expose an available template or submit a write.
 for(const options of [{role:'worker'},{denied:true},{channels:[]}]){const denied=setup(options);await open(denied);assert.equal(denied.d.getElementById('agentLibraryBlank').disabled,true);assert.ok(denied.d.getElementById('agentTemplateLibrary').contains(denied.d.activeElement));assert.ok([...denied.d.querySelectorAll('[data-agent-template]')].every(b=>b.disabled));assert.equal(denied.calls.filter(c=>c.method==='POST').length,0);denied.w.close();}
 // A late response cannot create a trigger/upload/activation in another company.
 const late=setup();await open(late);late.d.getElementById('agentLibraryBlank').click();await tick();late.d.getElementById('nightAgentName').value='Pending';late.d.getElementById('nightAgentInstructions').value='Rules';choose(late,'connect:'+CA);await nextAll(late);late.delayAgent();late.d.getElementById('nightAgentCreate').click();await tick();late.d.dispatchEvent(new late.w.CustomEvent('tenant-profile',{detail:{member:{tenant_id:B,telegram_id:22},capabilities:{modules:{ai_agents:true}}}}));late.release();await tick();assert.equal(late.d.getElementById('nightAgentTemplateModal').hidden,true);assert.equal(late.d.getElementById('agentTemplateLibrary').hidden,true);assert.ok(!late.calls.some(c=>c.method==='PATCH'||c.path.endsWith('/knowledge/upload')||c.path.endsWith('/triggers')&&c.method==='POST'));late.w.close();
 // Automatic and activation both require explicit choices; company flags remain untouched.
 const automatic=setup();await open(automatic);automatic.d.getElementById('agentLibraryBlank').click();await tick();automatic.d.getElementById('nightAgentName').value='Explicit auto';automatic.d.getElementById('nightAgentInstructions').value='Verified facts';choose(automatic,'connect:'+CA);automatic.d.getElementById('nightAgentMode').value='automatic';automatic.d.getElementById('nightAgentLaunch').checked=true;await nextAll(automatic);automatic.d.getElementById('nightAgentCreate').click();await tick();assert.equal([...automatic.agents.values()][0].mode,'automatic');assert.equal([...automatic.agents.values()][0].enabled,true);const initial=automatic.calls.find(c=>c.path.endsWith('/agents')&&c.method==='POST');assert.equal(initial.data.enabled,false);assert.equal(automatic.calls.filter(c=>c.method==='PATCH').length,1);assert.ok(!automatic.calls.some(c=>c.path.endsWith('/agents/settings')&&c.method!=='GET'));automatic.w.close();
 // A disconnected connection cannot slip through a catalog cached before the final review.
 const changed=setup();await open(changed);changed.d.getElementById('agentLibraryBlank').click();await tick();changed.d.getElementById('nightAgentName').value='Scoped';changed.d.getElementById('nightAgentInstructions').value='Rules';choose(changed,'connect:'+CA);await nextAll(changed);changed.setChannels([channel(CA,'Offline',{ai:{supported:true,available:false}})]);changed.d.getElementById('nightAgentCreate').click();await tick();assert.equal(changed.agents.size,0);assert.equal(changed.d.getElementById('nightAgentName').disabled,false);changed.w.close();
 // API names are data. Unknown schemas fail closed, and an absent endpoint never uses legacy options.
 const unsafe=setup({channels:[channel(CA,'<img src=x onerror=alert(1)>')]});await open(unsafe);assert.equal(unsafe.d.querySelector('#agentLibraryCards img'),null);unsafe.d.querySelector('[data-agent-template]').click();await tick();assert.equal(unsafe.d.querySelector('#nightAgentChannelChoices img'),null);unsafe.w.close();
 const missing=setup();missing.failCatalog(true);await open(missing);assert.equal(missing.d.getElementById('agentLibraryBlank').disabled,true);assert.ok(!missing.calls.some(c=>c.method==='POST'));missing.w.close();
 // Storage failure blocks the very first mutation; retries never lose their idempotency key.
 const storage=setup();await open(storage);storage.d.getElementById('agentLibraryBlank').click();await tick();storage.d.getElementById('nightAgentName').value='No storage';storage.d.getElementById('nightAgentInstructions').value='Rules';choose(storage,'connect:'+CA);await nextAll(storage);storage.w.Storage.prototype.setItem=function(){throw Error('Storage blocked')};storage.d.getElementById('nightAgentCreate').click();await tick();assert.equal(storage.agents.size,0);assert.ok(!storage.calls.some(c=>c.method==='POST'));assert.match(storage.d.getElementById('nightAgentNotice').textContent,/локальное хранилище/);storage.w.close();
 // Another editor changing a recovered agent prevents stale triggers, uploads and activation.
 const edited=setup();await open(edited);edited.d.querySelector('[data-agent-template]').click();await tick();choose(edited,'connect:'+CA);setFile(edited);edited.d.getElementById('nightAgentLaunch').checked=true;await nextAll(edited);edited.failAgent();edited.d.getElementById('nightAgentCreate').click();await tick();[...edited.agents.values()][0].instructions='Updated in another editor';edited.d.getElementById('nightAgentCreate').click();await tick();assert.equal(edited.agents.size,1);assert.equal(edited.triggers.size,0);assert.equal(edited.knowledge.size,0);assert.ok(!edited.calls.some(c=>c.method==='PATCH'));assert.match(edited.d.getElementById('nightAgentNotice').textContent,/изменён вне мастера/);assert.equal(edited.d.getElementById('nightAgentAfterCreate').hidden,false);edited.w.close();
 // An old night-wizard record keeps its original server identity and existing knowledge/trigger receipts.
 const legacy=setup();await tick();const old=legacy.w.BEINTASKBOT_NIGHT_AGENT_TEMPLATE.nightAgentPayload({name:'Ночная поддержка (сохранено)',instructions:'Original rules',channelId:CA,timezone:'Asia/Baku',days:[0],start:'22:00',end:'06:00',model:'fixture-model',mode:'manual',confirmedLaunch:false,setupKey:'night-existing-retry-key'});const previous={...old,id:'00000000-0000-0000-0000-000000000999',version:1};legacy.agents.set(old.setup_key,previous);legacy.w.localStorage.setItem('beintaskbot.night-agent-template.v1.'+A+'.11',JSON.stringify({setup_key:old.setup_key,agent:previous,trigger_saved:true,uploaded_file_signature:'already-uploaded'}));await legacy.w.tenantAgentWizard.open({templateId:'technical-support'});assert.equal(legacy.d.getElementById('nightAgentName').value,old.name);await nextAll(legacy);legacy.d.getElementById('nightAgentCreate').click();await tick();assert.equal(legacy.agents.size,1);assert.equal(legacy.triggers.size,0);assert.equal(legacy.knowledge.size,0);assert.match(legacy.d.getElementById('nightAgentNotice').textContent,/Готово/);legacy.w.close();
 console.log('PASS: library, categories/filters, actual connections, support/blank flows, mixed-safe scopes, disabled Manual defaults, night schedules, scoped knowledge, lost responses, retries, permissions and tenant switch.');
}
if(require.main===module)run().catch(error=>{console.error(error);process.exitCode=1});
module.exports={setup,channel,tick,A,B,CA,CB};
