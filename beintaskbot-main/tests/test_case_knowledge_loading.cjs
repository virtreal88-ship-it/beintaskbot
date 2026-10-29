// Real shipped controllers; all HTTP and mutations are synthetic, never production.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{execFileSync}=require('node:child_process');
const {JSDOM}=require('jsdom');
const baseline=process.argv.includes('--baseline-ref')?process.argv[process.argv.indexOf('--baseline-ref')+1]:null;
const source=name=>baseline?execFileSync('git',['show',`${baseline}:beintaskbot-main/docs/${name}`],{encoding:'utf8'}):fs.readFileSync(path.join(__dirname,'../docs',name),'utf8');
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
async function settle(){for(let i=0;i<8;i++)await tick();}
const profile=(tenant='tenant-a',user=11)=>({member:{tenant_id:tenant,telegram_id:user,role:'owner',active:true,permissions:['read','write']},capabilities:{modules:{ai_agents:true}}});
const candidate=(id='case-a',title='Case A')=>({id,title,version:1,status:'pending_review',symptoms:[],operator_actions:[],resolution_steps:['Restart'],cause:{status:'unknown',text:''},outcome:{status:'unconfirmed',text:'',message_ids:[]},source_message_ids:[],can_approve:false});
const base='/api/platform/ai/knowledge-candidates',knowledge='/api/platform/ai/knowledge';
function fixture(view,{campaign=true,auto=true}={}){
 const w=new JSDOM(`<nav class="nav"><button id="knowledgeCenterNav" data-view="knowledge_center"></button><button data-view="support_cases"></button></nav><section class="view ${view==='support_cases'?'active':''}" id="view-support_cases"><div id="supportCases"></div></section><span id="supportCasesPending"></span><section class="view ${view==='knowledge_center'?'active':''}" id="view-knowledge_center"><div id="knowledgeCenter"></div></section>`,{url:`https://fixture.test/app?view=${view}`,runScripts:'outside-only'}).window;
 const f={w,d:w.document,calls:[],holds:[],rows:[candidate()],sources:[{id:'source-a',title:'Source A',active:true,origin_type:'knowledge_source'}],fail:new Set(),hold:null};
 w.tenantI18n={language:'en'};w.confirm=()=>true;w.URL.revokeObjectURL=()=>{};
 if(auto)w.tenantProfileSnapshot=profile();
 f.emit=(name,detail)=>w.document.dispatchEvent(new w.CustomEvent(name,{detail}));
 f.response=(result,status=200)=>({ok:status===200,status,json:async()=>status===200?{success:true,result}:{success:false,error:'Некорректные параметры.'}});
 f.reply=(u,opt)=>{
  if(f.fail.has(u.pathname))return f.response(null,f.failStatus||400);
  if(opt.method&&opt.method!=='GET'){
   if(u.pathname===knowledge+'/source-a'){f.sources=[];return f.response({archived:true});}
   const id=u.pathname.split('/')[5];
   if(u.pathname.endsWith('/delete')){f.rows=f.rows.filter(row=>row.id!==id);return f.response({deleted:true});}
   const row=f.rows.find(row=>row.id===id);if(row){Object.assign(row,opt.method==='PATCH'?JSON.parse(opt.body):{status:'approved'});row.version++;}
   return f.response({candidate:row,evidence:[],route:{}});
  }
  if(u.pathname===base)return f.response({candidates:f.rows.filter(row=>!u.searchParams.get('status')||u.searchParams.get('status')==='all'||row.status===u.searchParams.get('status')),next_before:null});
  if(u.pathname===base+'/campaigns')return f.response({campaign:null});
  if(u.pathname.startsWith(base+'/'))return f.response({candidate:f.rows.find(row=>row.id===u.pathname.split('/').pop()),evidence:[],route:{}});
  if(u.pathname===knowledge)return f.response(f.sources);
  if(u.pathname==='/api/platform/instructions')return f.response({items:[],next_offset:null});
  if(u.pathname==='/api/platform/chat/ai/settings')return f.response({photo_model:'fixture-model'});
  if(u.pathname==='/api/platform/ai/dialogs')return f.response({dialogs:[]});
  if(u.pathname==='/api/platform/ai/channels')return f.response({channels:[]});
  if(u.pathname==='/api/platform/ai/agents')return f.response([]);
  throw Error('Unexpected HTTP '+u.pathname);
 };
 w.fetch=(input,opt={})=>{const u=new URL(input,w.location.href);f.calls.push({url:u,opt});if(f.hold?.(u,opt))return new Promise(resolve=>f.holds.push({u,opt,resolve}));return Promise.resolve(f.reply(u,opt));};
 if(campaign)w.eval(source('tenant-knowledge-campaigns.js'));else w.TenantKnowledgeCampaigns={mount:async(root,ctx)=>{f.ctx=ctx;}};
 f.start=()=>w.eval(source(view==='support_cases'?'tenant-knowledge-candidates.js':'tenant-knowledge-center.js'));
 f.close=()=>w.close();return f;
}
async function metrics(){
 for(const view of ['support_cases','knowledge_center']){
  const f=fixture(view);f.start();await settle();const first=f.calls.length;f.calls=[];
  f.hold=()=>true;f.emit('tenant-view',{view});const immediate=f.d.querySelector(view==='support_cases'?'#casesList':'#kcList').textContent;await tick();
  // Release each dependency wave so baseline and current execute their real graph.
  for(let i=0;i<12;i++){const pending=f.holds.splice(0);for(const h of pending)h.resolve(f.reply(h.u,h.opt));await tick();}
  const repeat=f.calls.length;console.log(JSON.stringify({view,firstGET:first,repeatGET:repeat,warmRowsBeforeResponse:immediate.includes(view==='support_cases'?'Case A':'Source A')}));
  if(!baseline){assert.equal(first,view==='support_cases'?6:1);assert.equal(repeat,view==='support_cases'?3:1);assert.match(immediate,view==='support_cases'?/Case A/:/Source A/);}
  f.close();
 }
}
async function knowledgeTests(){
 const f=fixture('knowledge_center');f.fail.add(base);f.start();await settle();assert.match(f.d.querySelector('#kcList').textContent,/Source A/);assert.equal(f.calls.length,1,'Sources has no candidate/instruction/agent dependency');
 f.d.querySelector('[data-kc-tab="support_case"]').click();await settle();assert.match(f.d.querySelector('#kcNotice').textContent,/Некорректные/);assert(f.d.querySelector('#kcNotice').classList.contains('show'),'Cases error is still visible on Cases');
 f.d.querySelector('[data-kc-tab="knowledge_source"]').click();await settle();f.fail.add(knowledge);f.d.querySelector('#kcRefresh').click();await settle();assert(f.d.querySelector('#kcNotice').classList.contains('show'));assert.match(f.d.querySelector('#kcNotice').textContent,/Некорректные/,'Source endpoint errors are not suppressed');assert.match(f.d.querySelector('#kcList').textContent,/Source A/,'failed background refresh retains loaded rows with error');f.fail.clear();
 f.hold=u=>u.pathname===knowledge;f.d.querySelector('#kcRefresh').click();await tick();const old=f.holds.shift();
 f.emit('tenant-auth-pending');assert.equal(f.d.querySelector('#knowledgeCenter').style.visibility,'hidden');
 f.emit('tenant-profile',profile('tenant-b',12));await tick();assert.doesNotMatch(f.d.querySelector('#kcList').textContent,/Source A/);
 old.resolve(f.response([{id:'old',title:'OLD TENANT',active:true,origin_type:'knowledge_source'}]));await settle();assert.doesNotMatch(f.d.querySelector('#knowledgeCenter').textContent,/OLD TENANT/);
 const current=f.holds.shift();current.resolve(f.response([{id:'new',title:'New tenant',active:true,origin_type:'knowledge_source'}]));await settle();assert.match(f.d.querySelector('#kcList').textContent,/New tenant/);
 f.emit('tenant-auth-invalid');assert.equal(f.d.querySelector('#knowledgeCenter').textContent,'');const count=f.calls.length;f.emit('tenant-view',{view:'knowledge_center'});await settle();assert.equal(f.calls.length,count);f.close();
 const m=fixture('knowledge_center');m.start();await settle();m.hold=u=>u.pathname===knowledge;m.d.querySelector('#kcRefresh').click();await tick();const stale=m.holds.shift();m.d.querySelector('[data-kc-archive]').click();await settle();assert.doesNotMatch(m.d.querySelector('#kcList').textContent,/Source A/);stale.resolve(m.response([{id:'source-a',title:'Source A',active:true,origin_type:'knowledge_source'}]));await settle();assert.doesNotMatch(m.d.querySelector('#kcList').textContent,/Source A/,'pre-delete response cannot resurrect source');m.holds.shift().resolve(m.response([]));await settle();m.close();
 console.log('PASS Knowledge: required-endpoint errors, warm rows, tenant/user/logout and delete races');
}
async function caseTests(){
 const f=fixture('support_cases',{campaign:false});f.start();await settle();f.calls=[];
 f.hold=u=>u.pathname===base;f.emit('tenant-view',{view:'support_cases'});f.emit('tenant-view',{view:'support_cases'});await tick();assert.equal(f.calls.length,1,'coalesce repeated entry');assert.match(f.d.querySelector('#casesList').textContent,/Case A/);f.holds.shift().resolve(f.response({candidates:f.rows,next_before:'page2'}));await settle();
 f.d.querySelector('#casesMore').click();await tick();assert.equal(f.holds[0].u.searchParams.get('before'),'page2');f.holds.shift().resolve(f.response({candidates:[candidate('case-b','Case B')],next_before:null}));await settle();assert.equal(f.d.querySelectorAll('[data-case-open]').length,2);
 f.d.querySelector('#casesStatus').value='approved';f.d.querySelector('#casesLoad').click();await tick();assert.equal(f.d.querySelectorAll('[data-case-open]').length,0,'filter changes clear incompatible rows');const approved=f.holds.shift();f.d.querySelector('#casesReset').click();await tick();const reset=f.holds.shift();reset.resolve(f.response({candidates:f.rows,next_before:null}));await settle();approved.resolve(f.response({candidates:[candidate('wrong','Wrong filter')],next_before:null}));await settle();assert.doesNotMatch(f.d.querySelector('#casesList').textContent,/Wrong filter/);
 f.hold=null;f.rows.push(candidate('case-b','Case B'));f.hold=u=>u.pathname===base+'/case-a'||u.pathname===base+'/case-b';
 const a=f.w.tenantKnowledgeCases.openCard('case-a'),b=f.w.tenantKnowledgeCases.openCard('case-b');await tick();f.holds[1].resolve(f.reply(f.holds[1].u,{}));await b;f.holds[0].resolve(f.reply(f.holds[0].u,{}));await a;assert.equal(f.d.querySelector('#caseTitle').value,'Case B','latest selected detail wins');f.holds=[];
 const later=f.w.tenantKnowledgeCases.openCard('case-a');await tick();f.d.querySelector('[data-case-close]').click();f.holds.shift().resolve(f.response({candidate:f.rows[0],evidence:[],route:{}}));await later;assert.equal(f.d.querySelector('#supportCaseModal'),null,'Close cancels delayed detail');
 f.hold=null;await f.w.tenantKnowledgeCases.openCard('case-a');await settle();f.d.querySelector('#caseTitle').value='Unsaved';f.d.querySelector('#caseTitle').dispatchEvent(new f.w.Event('input',{bubbles:true}));
 f.emit('tenant-auth-pending');assert.equal(f.d.querySelector('#supportCaseModal').style.visibility,'hidden');f.emit('tenant-profile',profile());await settle();assert.equal(f.d.querySelector('#caseTitle').value,'Unsaved','same-user reauth preserves editor');
 const metadata=profile();metadata.member.permissions.reverse();metadata.capabilities.deal_board={pipeline:'Renamed'};metadata.capabilities.notification_events=['new-display-label'];f.emit('tenant-profile',metadata);await settle();assert.equal(f.d.querySelector('#caseTitle').value,'Unsaved','display metadata and reordered permissions retain dirty editor');
 f.w.confirm=()=>false;await f.w.tenantKnowledgeCases.openCard('case-b');assert.equal(f.d.querySelector('#caseTitle').value,'Unsaved','Cancel keeps editor');f.w.confirm=()=>true;f.d.querySelector('[data-case-close]').click();
 await f.w.tenantKnowledgeCases.openCard('case-a');await settle();f.hold=u=>u.pathname===base;f.d.querySelector('#casesLoad').click();await tick();const preDelete=f.holds.shift();f.d.querySelector('[data-case-delete]').click();await settle();assert.equal(f.d.querySelectorAll('[data-case-open]').length,0,'mutation invalidates retained lists immediately');preDelete.resolve(f.response({candidates:[candidate()],next_before:null}));await settle();assert.equal(f.d.querySelectorAll('[data-case-open]').length,0,'pre-delete GET cannot revive case');f.holds.shift().resolve(f.reply(new URL(base,f.w.location.href),{}));await settle();assert.doesNotMatch(f.d.querySelector('#casesList').textContent,/Case A/);
 f.d.querySelector('#casesLoad').click();await tick();const old=f.holds.shift();f.emit('tenant-profile',profile('tenant-b',12));await tick();old.resolve(f.response({candidates:[candidate('old','OLD TENANT')],next_before:null}));await settle();assert.doesNotMatch(f.d.querySelector('#supportCases').textContent,/OLD TENANT/);f.emit('tenant-auth-invalid');const calls=f.calls.length;f.emit('tenant-view',{view:'support_cases'});await settle();assert.equal(f.calls.length,calls);assert.equal(f.d.querySelector('#supportCases').textContent,'');for(const h of f.holds)h.resolve(f.response({candidates:[candidate()],next_before:null}));await settle();assert.equal(f.d.querySelector('#supportCases').textContent,'');f.close();
 console.log('PASS Cases: request coalescing, filters/pages, latest detail/Close/Cancel, dirty reauth, delete/tenant/logout races');
}
async function tabRaces(){
 const f=fixture('knowledge_center');f.start();await settle();
 f.hold=u=>u.pathname==='/api/platform/instructions';f.d.querySelector('[data-kc-tab="instruction"]').click();await settle();const slow=f.holds.shift();
 f.sources=[{id:'fresh',title:'Fresh source',active:true,origin_type:'knowledge_source'}];f.d.querySelector('[data-kc-tab="knowledge_source"]').click();await settle();assert.match(f.d.querySelector('#kcList').textContent,/Fresh source/);
 slow.resolve(f.response({items:[{id:'lesson-1',title:'Lesson one',state:'published'}],next_offset:1}));await settle();f.d.querySelector('#kcSearch').dispatchEvent(new f.w.Event('input'));assert.match(f.d.querySelector('#kcList').textContent,/Fresh source/,'slow unrelated tab cannot overwrite newer source rows');
 f.d.querySelector('[data-kc-tab="instruction"]').click();await tick();f.holds.shift().resolve(f.response({items:[{id:'lesson-1',title:'Lesson one',state:'published'}],next_offset:1}));await settle();f.d.querySelector('#kcMore').click();await tick();assert.equal(f.holds[0].u.searchParams.get('offset'),'1');
 f.holds.shift().resolve(f.response({items:[{id:'lesson-1',title:'Lesson one updated',state:'published'},{id:'lesson-2',title:'Lesson two',state:'published'}],next_offset:null}));await settle();assert.equal(f.d.querySelectorAll('.kc-card').length,2,'paging deduplicates IDs');
 f.d.querySelector('#kcRefresh').click();await tick();assert.equal(f.holds[0].u.searchParams.get('offset'),'0','Refresh restarts the active tab page');f.holds.shift().resolve(f.response({items:[],next_offset:null}));await settle();assert.equal(f.d.querySelectorAll('.kc-card').length,0);f.close();
 console.log('PASS Knowledge: cross-tab resource ordering, page cursor, deduplication and refresh reset');
}
async function mutationAndBatchTests(){
 const f=fixture('support_cases',{campaign:false});f.start();await settle();
 for(const endpoint of ['campaigns','campaigns/campaign-1/consent','case-a/improve']){await f.ctx.api(base+'/'+endpoint,{method:'POST',body:'{}'});await settle();assert.match(f.d.querySelector('#casesList').textContent,/Case A/,'non-case writes do not empty loaded list');}
 for(const [method,suffix] of [['PATCH',''],['POST','/approve'],['POST','/publish-guidance'],['POST','/boundaries'],['POST','/archive'],['POST','/delete']]){
  f.hold=u=>u.pathname===base;f.d.querySelector('#casesLoad').click();await tick();const old=f.holds.shift();
  await f.ctx.api(base+'/case-a'+suffix,{method,body:JSON.stringify({title:'Updated'})});await tick();assert.equal(f.d.querySelectorAll('[data-case-open]').length,0,method+suffix+' invalidates retained rows');
  old.resolve(f.response({candidates:[candidate('old','STALE STATUS')],next_before:null}));await settle();assert.doesNotMatch(f.d.querySelector('#casesList').textContent,/STALE STATUS/);
  f.holds.shift().resolve(f.reply(new URL(base,f.w.location.href),{}));await settle();f.hold=null;
 }
 f.close();
 const b=fixture('support_cases',{campaign:false}),scheduled=[];const original=b.w.setTimeout.bind(b.w);b.w.setTimeout=(fn,delay,...args)=>delay===2500?(scheduled.push(fn),scheduled.length):original(fn,delay,...args);
 const reply=b.reply;b.reply=(u,opt)=>u.pathname.startsWith(base+'/batches')?b.response({batch:{id:'batch-1',status:'running',params:{},summary:{},limits:{}}}):reply(u,opt);
 b.start();await settle();b.d.querySelector('#casesChannel').innerHTML='<option value="channel-a">Channel</option>';b.d.querySelector('#casesFrom').value='2026-10-01';b.d.querySelector('#casesTo').value='2026-10-02';b.d.querySelector('#casesAnalyse').click();await settle();assert.equal(scheduled.length,1);
 b.hold=u=>u.pathname===base+'/batches/batch-1';scheduled.shift()();await tick();const poll=b.holds.shift();assert(poll,'running batch poll begins');await b.ctx.api(base+'/case-a/boundaries',{method:'POST',body:'{}'});poll.resolve(b.reply(poll.u,poll.opt));await settle();assert.equal(scheduled.length,1,'case mutation cannot kill running batch polling');
 b.emit('tenant-auth-pending');b.emit('tenant-profile',profile());await settle();assert.equal(scheduled.length,2,'same-user reauth resumes batch status monitoring');b.close();
 console.log('PASS case writes: edit/publish/boundaries/archive/delete invalidate; campaign/proposal preserve rows; batch polling survives');
}
async function firstListAndDetailTests(){
 const f=fixture('support_cases');f.hold=u=>['/api/platform/ai/dialogs','/api/platform/ai/agents','/api/platform/ai/channels'].includes(u.pathname);f.start();await settle();assert.match(f.d.querySelector('#casesList').textContent,/Case A/,'list no longer waits for metadata');for(const h of f.holds)h.resolve(f.reply(h.u,h.opt));f.holds=[];f.hold=null;await settle();
 const reply=f.reply;f.reply=(u,opt)=>u.pathname===base+'/case-a'?f.response({candidate:f.rows[0],evidence:[],route:{provider:'connect',channel_id:'channel-a',lead_id:1,conversation_id:'conversation-a'}}):u.pathname==='/api/platform/ai/dialogs/history'?f.response({messages:[],next_before:null}):reply(u,opt);
 f.calls=[];await f.w.tenantKnowledgeCases.openCard('case-a');await settle();assert.equal(f.calls.length,2,'fresh detail plus its real route history');f.d.querySelector('[data-case-close]').click();f.calls=[];await f.w.tenantKnowledgeCases.openCard('case-a');await settle();assert.equal(f.calls.length,2,'repeat detail remains fresh, no persisted detail cache');f.d.querySelector('[data-case-close]').click();
 f.calls=[];f.hold=u=>u.pathname===base+'/case-a';const one=f.w.tenantKnowledgeCases.openCard('case-a'),two=f.w.tenantKnowledgeCases.openCard('case-a');await tick();assert.equal(f.holds.length,1,'overlapping detail opens coalesce');f.holds.shift().resolve(f.reply(new URL(base+'/case-a',f.w.location.href),{}));await Promise.all([one,two]);await settle();assert.equal(f.calls.length,2);f.hold=null;
 f.calls=[];const now=f.w.Date.now();f.w.Date.now=()=>now+31000;f.emit('tenant-view',{view:'support_cases'});await settle();assert.equal(f.calls.length,6,'expired metadata is revalidated');f.close();
 console.log('PASS first-list dependency: rows precede metadata; fresh detail/history 2+2 GET, overlapping reads shared, metadata TTL');
}
async function authorizationFailureTests(){
 for(const view of ['knowledge_center','support_cases']){
  const f=fixture(view,{campaign:false});f.start();await settle();if(view==='support_cases')await f.w.tenantKnowledgeCases.openCard('case-a');
  f.failStatus=403;f.fail.add(view==='support_cases'?base:knowledge);f.emit('tenant-view',{view});await settle();
  assert.doesNotMatch(f.d.querySelector(view==='support_cases'?'#supportCases':'#knowledgeCenter').textContent,/Case A|Source A/,'denied refresh discards retained data');assert.equal(f.d.querySelector('#supportCaseModal'),null);f.close();
 }
 console.log('PASS denied refresh: cached list and modal discarded on authorization failure');
}
async function retryAfterContractTest(){
 const f=fixture('support_cases',{campaign:false});f.start();await settle();
 f.w.fetch=async()=>({ok:false,status:429,headers:{get:name=>name.toLowerCase()==='retry-after'?'13':null},json:async()=>({success:false,error:'rate limit'})});
 await assert.rejects(f.ctx.api(base+'/campaigns/campaign/current-media-check',{method:'POST',body:'{}'}),error=>error.status===429&&error.retryAfter===13);
 f.close();console.log('PASS campaign API propagates Retry-After');
}
(async()=>{await metrics();if(!baseline){await knowledgeTests();await caseTests();await tabRaces();await mutationAndBatchTests();await authorizationFailureTests();await firstListAndDetailTests();await retryAfterContractTest();}})().catch(error=>{console.error(error);process.exitCode=1;});
