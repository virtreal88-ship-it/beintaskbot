/* Synthetic browser/network fixtures only. No server/provider calls. */
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const root=path.join(__dirname,'..'),read=file=>fs.readFileSync(path.join(root,file),'utf8');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};};
const settle=async()=>{for(let n=0;n<12;n++)await new Promise(resolve=>setImmediate(resolve));};
const profile={success:true,member:{tenant_id:'00000000-0000-0000-0000-000000000101',telegram_id:11,role:'owner',active:true,permissions:['dialogs']},capabilities:{modules:{dialogs:true,ai_agents:true}}};
const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
class CustomEvent extends Event{constructor(type,options={}){super(type);this.detail=options.detail;}}
function coordinator(){
 const requests=[],document=new EventTarget(),window=new EventTarget();let clock=1000,reloads=0;
 const location={href:'https://fixture.test/app',origin:'https://fixture.test',reload:()=>reloads++};
 window.fetch=(url,options)=>{const task=deferred();requests.push({url:String(url),options,...task});return task.promise;};
 vm.runInNewContext(read('docs/tenant-request-coordinator.js'),{window,document,location,URL,Headers,CustomEvent,Date:{now:()=>clock}});
 return {window,document,requests,advance:ms=>clock+=ms,reloads:()=>reloads,emit:data=>document.dispatchEvent(new CustomEvent('tenant-profile',{detail:data}))};
}
async function requestTests(){
 const c=coordinator(),f=(...args)=>c.window.fetch(...args);
 const burst=Array.from({length:12},(_,i)=>f('/api/platform/me',i%2?{headers:{'Content-Type':'application/json'}}:{}));
 assert.equal(c.requests.length,1,'12 independent legacy profile consumers share one request');
 c.requests[0].resolve(response(profile));const bodies=await Promise.all((await Promise.all(burst)).map(r=>r.json()));assert.equal(bodies.length,12);
 c.emit(profile);await (await f('/api/platform/me')).json();assert.equal(c.requests.length,1,'short bootstrap response reuse');
 c.advance(1001);const fresh=f('/api/platform/me');assert.equal(c.requests.length,2);c.requests.at(-1).resolve(response(profile));await fresh;
 const first=f('/api/platform/ai/dialogs?search=a'),same=f('/api/platform/ai/dialogs?search=a');assert.equal(c.requests.length,3);
 c.requests.at(-1).resolve(response({success:true,result:{dialogs:[]}}));await Promise.all([first,same]);
 const again=f('/api/platform/ai/dialogs?search=a');assert.equal(c.requests.length,4,'dialog responses are never cached by fetch');c.requests.at(-1).resolve(response({},500));await again;
 const retry=f('/api/platform/ai/dialogs?search=a');assert.equal(c.requests.length,5);c.requests.at(-1).resolve(response({}));await retry;
 const old=f('/api/platform/ai/dialogs?search=old');const oldCheck=assert.rejects(old,/scope changed/);c.emit({...profile,member:{...profile.member,permissions:[]}});c.requests.at(-1).resolve(response({}));await oldCheck;
 const slow=f('/api/platform/ai/dialogs/history?lead_id=1');const mutation=f('/api/platform/ai/dialogs/read',{method:'POST',body:'{}'});
 c.requests.at(-1).resolve(response({success:true}));await mutation;c.requests.at(-2).resolve(response({success:true}));await (await slow).json();
 const denied=f('/api/platform/me');const deniedCheck=assert.rejects(denied,/scope changed/);c.requests.at(-1).resolve(response({},401));await deniedCheck;
 const reauth=f('/api/platform/me');c.requests.at(-1).resolve(response(profile));await reauth;
 const afterHeaders=f('/api/platform/ai/dialogs/history?lead_id=2');
 let bodyResolve;const stream=new ReadableStream({start(controller){bodyResolve=()=>{controller.enqueue(new TextEncoder().encode('{"success":true}'));controller.close();};}});
 c.requests.at(-1).resolve(new Response(stream));const consumed=(await afterHeaders).json();const consumedCheck=assert.rejects(consumed,/scope changed/);
 c.window.tenantRequestCoordinator.invalidateAuth();bodyResolve();await consumedCheck;
 // A profile body itself can be delayed while logout wins the race.
 const slowProfile=f('/api/platform/me');const slowProfileCheck=assert.rejects(slowProfile,/scope changed/);
 let finishProfile;const profileStream=new ReadableStream({start(controller){finishProfile=()=>{controller.enqueue(new TextEncoder().encode(JSON.stringify(profile)));controller.close();};}});
 c.requests.at(-1).resolve(new Response(profileStream));await settle();c.window.tenantRequestCoordinator.invalidateAuth();finishProfile();await slowProfileCheck;
 // Focus invalidates even an older profile body; it cannot publish while reauthorization is pending.
 c.emit(profile);const prior=f('/api/platform/me'),priorCheck=assert.rejects(prior,/scope changed/),priorRequest=c.requests.at(-1);
 c.window.dispatchEvent(new Event('focus'));const focusRequest=c.requests.at(-1);assert.notEqual(focusRequest,priorRequest);
 priorRequest.resolve(response(profile));await priorCheck;assert.equal(c.window.tenantProfileSnapshot,null);
 focusRequest.resolve(response({...profile,member:{...profile.member,telegram_id:22}}));await settle();assert.equal(c.window.tenantProfileSnapshot.member.telegram_id,22);
 // Header/credentials/cancellation differences must not share a lifetime.
 const start=c.requests.length;
 const variants=[f('/api/platform/me',{headers:new Headers({authorization:'Bearer synthetic-a'})}),f('/api/platform/me',{headers:{authorization:'Bearer synthetic-b'}}),f('/api/platform/me',{credentials:'omit'}),f('/api/platform/me',{signal:new AbortController().signal}),f(new URL('https://fixture.test/api/platform/me'))];
 assert.equal(c.requests.length-start,5);c.requests.slice(start).forEach(r=>r.resolve(response(profile)));await Promise.all(variants);
 c.window.dispatchEvent(new Event('pagehide'));const restored=new Event('pageshow');restored.persisted=true;c.window.dispatchEvent(restored);assert.equal(c.reloads(),1,'BFCache restores require fresh authorization');
 console.log('PASS request sharing: synthetic 12 → 1 profile fetches; failures, headers, epochs, delayed bodies, mutation, BFCache');
}
function browser(view='dialogs',url=''){
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://fixture.test/app?view='+view+url,runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window,requests=[];
 w.Headers=Headers;w.Response=Response;w.fetch=(url,options={})=>{const task=deferred();requests.push({url:String(url),options,...task});return task.promise;};
 w.eval(read('docs/tenant-request-coordinator.js'));w.eval(read('docs/tenant-view-demand.js'));
 w.tenantI18n={language:'en',t:key=>key,source:text=>text,capture:()=>{},date:value=>String(value||'')};
 w.document.querySelectorAll('.view').forEach(node=>node.classList.toggle('active',node.id==='view-'+view));
 const emit=(type,detail)=>w.document.dispatchEvent(new w.CustomEvent(type,{detail}));
 const reply=(request,result,status=200)=>request.resolve(response({success:status<400,result,...(status>=400?{error:'Synthetic failure'}:{})},status));
 return {dom,w,requests,emit,reply};
}
async function agentTests(){
 const b=browser('dialogs'),{w,requests,emit,reply}=b;
 try{
  w.eval(read('docs/tenant-ai-agents.js'));assert.equal(requests.length,1);requests[0].resolve(response(profile));await settle();
  assert.equal(requests.length,1,'hidden agents must not load settings, models, simulation or pilot');
  const show=()=>{w.document.getElementById('view-ai_agents').classList.add('active');emit('tenant-view',{view:'ai_agents'});};show();show();
  assert.equal(requests.length,5,'independent initial agent reads run in parallel and duplicate events join');
  assert.equal(w.document.getElementById('aiAgentList').getAttribute('aria-busy'),'true');assert.doesNotMatch(w.document.getElementById('aiAgentList').textContent,/noAgents/);
  for(const request of requests.slice(1,5))reply(request,request.url.endsWith('/agents')?[]:{});
  await settle();for(const request of requests.slice(5))reply(request,{});await settle();
  assert.match(w.document.getElementById('aiAgentList').textContent,/noAgents/,'only a successful empty response means no agents');
  show();const failed=requests.filter(r=>r.url.endsWith('/agents')).at(-1);reply(failed,{},500);for(const request of requests.slice(-4))if(request!==failed)reply(request,{});await settle();
  assert.match(w.document.getElementById('aiAgentList').textContent,/error/);assert.equal(w.document.getElementById('aiAgentList').getAttribute('aria-busy'),'false');
  show();const pending=requests.slice(-4);emit('tenant-profile',{...profile,member:{...profile.member,tenant_id:'00000000-0000-0000-0000-000000000102'}});
  for(const request of pending)reply(request,request.url.endsWith('/agents')?[{id:'old',name:'OLD TENANT',enabled:false}]:{});await settle();
  assert.doesNotMatch(w.document.getElementById('aiAgentList').textContent,/OLD TENANT/);
  emit('tenant-profile',{...profile,capabilities:{modules:{ai_agents:false}}});assert.doesNotMatch(w.document.getElementById('aiAgentList').textContent,/OLD TENANT|noAgents/);
  assert.equal(w.document.querySelector('[data-ai-overview-create]').disabled,true);
 }finally{w.close();}
 console.log('PASS agents: hidden demand, parallel/coalesced loading, empty/error, tenant/revocation races');
}
const row=n=>({provider:'connect',lead_id:n,channel_id:'00000000-0000-0000-0000-000000000201',conversation_id:'client'+n,customer_kind:'contact',customer_name:'Synthetic '+n,avatar_status:'unavailable',last_message_id:'m'+n,last_message_direction:'incoming',last_message_at:'2026-10-23T10:00:00Z',agent_status:'manual',available_actions:[]});
const list=rows=>({dialogs:rows,channels:[],agents:[],unread_count:0,total_count:rows.length,next_offset:null});
async function dialogTests(){
 const b=browser(),{w,requests,emit,reply}=b;
 try{
  w.tenantProfileSnapshot=profile;w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));
  emit('tenant-profile',profile);emit('tenant-view',{view:'dialogs'});assert.equal(requests.length,1,'profile, initial install, view share one list');
  reply(requests[0],list([row(1)]));await settle();assert.match(w.document.getElementById('operatorRows').textContent,/Synthetic 1/);
  emit('tenant-view',{view:'tasks'});emit('tenant-view',{view:'dialogs'});await settle();assert.equal(requests.length,1,'same-scope recent navigation reuses memory');
  const slow=w.tenantOperator.refresh();assert.equal(requests.length,2,'explicit/poll refresh bypasses completed reuse');
  w.document.getElementById('operatorSearch').value='new';const newer=w.tenantOperator.refresh();reply(requests.at(-1),list([row(2)]));await newer;reply(requests[1],list([row(1)]));await slow;assert.match(w.document.getElementById('operatorRows').textContent,/Synthetic 2/);assert.doesNotMatch(w.document.getElementById('operatorRows').textContent,/Synthetic 1/);
  const a=w.tenantOperator.openDialog(row(1)),b=w.tenantOperator.openDialog(row(2));const historyRequests=requests.filter(r=>r.url.includes('/history?'));
  reply(historyRequests[1],{dialog:row(2),messages:[],has_more:false});await b;reply(historyRequests[0],{dialog:row(1),messages:[],has_more:false});await a;
  assert.match(w.document.getElementById('operatorConversationHeader').textContent,/Synthetic 2/);
  const inflight=w.tenantOperator.refresh();emit('tenant-profile',{...profile,member:{...profile.member,permissions:[],role:'worker'},capabilities:{modules:{dialogs:false}}});reply(requests.at(-1),list([row(1)]));await inflight;
  assert.equal(w.document.getElementById('operatorRows').textContent,'');assert.doesNotMatch(w.document.getElementById('operatorConversationContent').textContent,/Synthetic/);
  const count=requests.length;emit('tenant-view',{view:'dialogs'});assert.equal(requests.length,count,'revoked scope cannot reuse or fetch dialogs');
 }finally{w.close();}
 console.log('PASS dialogs: startup dedupe, warm reuse, forced refresh, filter/history order, revocation');
}
async function bootstrapTests(){
 const b=browser('tasks'),{w,requests}=b;
 try{
  const main=[...w.document.scripts].find(script=>script.textContent.includes('const savedView=sessionStorage')).textContent;
  w.eval(main);assert.equal(requests.length,1);
  w.dispatchEvent(new w.Event('focus'));assert.equal(requests.length,2);
  requests[0].resolve(response(profile));await settle();assert.equal(w.location.pathname,'/app','superseded startup must not redirect to login');
  const full={...profile,capabilities:{modules:{dialogs:true,tasks:true,deals:true,customers:true}}};
  requests[1].resolve(response(full));await settle();
  const paths=requests.slice(2).map(request=>request.url);
  for(const expected of ['/api/platform/crm/tasks?refresh=1','/api/platform/crm/native/tasks?options=1','/api/platform/crm/deals','/api/platform/crm/native/deals?options=1','/api/platform/crm/native/companies','/api/platform/crm/native/contacts'])assert.ok(paths.includes(expected),`independent bootstrap started before any CRM response: ${expected}; got ${paths}`);
  assert.equal(paths.filter(url=>url==='/api/platform/crm/native/companies').length,1,'options and list join the same independent company read');
  assert.equal(w.tenantProfileSnapshot.member.tenant_id,profile.member.tenant_id);assert.equal(w.location.pathname,'/app');
 }finally{w.close();}
 console.log('PASS bootstrap: superseded startup waits for fresh authorization; independent CRM reads start in parallel');
}
async function defaultSupportLandingTests(){
 const boot=async(url='/app',savedView='')=>{
  const b=browser('support'),{w,requests}=b;w.history.replaceState({},'',url);if(savedView)w.sessionStorage.setItem('tenant.app.view',savedView);else w.sessionStorage.removeItem('tenant.app.view');
  const main=[...w.document.scripts].find(script=>script.textContent.includes('const savedView=sessionStorage')).textContent;w.eval(main);assert.equal(requests.length,1);
  const full={...profile,capabilities:{modules:{support:true,tasks:true,dialogs:true,ai_agents:true}}};requests[0].resolve(response(full));await settle();return b;
 };
 const bare=await boot('/app?return_to=%2Fapp%2Fsupport%2Fcase%2F7');
 try{assert.ok(bare.w.document.getElementById('view-support').classList.contains('active'),'bare app opens user-facing tickets/support, not native tasks');assert.equal(new URL(bare.w.location.href).searchParams.get('view'),'support');assert.equal(new URL(bare.w.location.href).searchParams.get('return_to'),'/app/support/case/7','return-to query survives default normalization');assert.ok(bare.w.document.querySelector('#view-support #operatorSupport'),'default target is the support-ticket inbox');assert.equal(bare.w.document.getElementById('supportCases')?.closest('.view')?.id,'view-support_cases','learning cases stay in their own route');
  bare.w.document.querySelector('.nav [data-view="tasks"]').click();assert.ok(bare.w.document.getElementById('view-tasks').classList.contains('active'));bare.w.history.back();await new Promise(resolve=>setTimeout(resolve,100));assert.ok(bare.w.document.getElementById('view-support').classList.contains('active'),'Back restores the previous view');bare.w.history.forward();await new Promise(resolve=>setTimeout(resolve,100));assert.ok(bare.w.document.getElementById('view-tasks').classList.contains('active'),'Forward restores the chosen tasks view');
 }finally{bare.dom.window.close();}
 for(const [url,saved,expected] of [['/app?view=tasks','support','tasks'],['/app','dialogs','dialogs'],['/app?view=support','tasks','support']]){const b=await boot(url,saved);try{assert.ok(b.w.document.getElementById('view-'+expected).classList.contains('active'),`${url} with saved=${saved} preserves explicit or remembered route ${expected}`);}finally{b.dom.window.close();}}
 console.log('PASS default landing: bare app defaults to support tickets; explicit/deep-link and remembered views plus back/forward are preserved');
}
async function focusTests(){
 const b=browser(),{w,requests,emit,reply}=b;
 try{
  w.tenantProfileSnapshot=profile;w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));emit('tenant-profile',profile);
  reply(requests[0],list([row(1)]));await settle();
  const old=w.tenantOperator.refresh();const oldRequest=requests.at(-1);
  w.dispatchEvent(new w.Event('focus'));w.dispatchEvent(new w.Event('focus'));
  assert.equal(w.document.getElementById('operatorDialogs').style.visibility,'hidden','conceal before reauthorization starts');
  const profiles=requests.filter(r=>r.url==='/api/platform/me');assert.equal(profiles.length,1,'focus burst joins profile reauthorization');
  const switched={...profile,member:{...profile.member,tenant_id:'00000000-0000-0000-0000-000000000102',telegram_id:22}};
  profiles[0].resolve(response(switched));await settle();
  assert.equal(w.document.getElementById('operatorRows').textContent,'','previous scope cleared before revealing current view');
  const next=requests.filter(r=>r.url.includes('/dialogs?')).at(-1);
  assert.equal(next.options.headers['X-Tenant-Id'],switched.member.tenant_id);assert.equal(next.options.headers['X-User-Id'],'22');
  reply(next,list([row(9)]));await settle();reply(oldRequest,list([row(1)]));await old;
  assert.match(w.document.getElementById('operatorRows').textContent,/Synthetic 9/);assert.doesNotMatch(w.document.getElementById('operatorRows').textContent,/Synthetic 1/);
  w.dispatchEvent(new w.Event('focus'));requests.at(-1).resolve(response({},401));await settle();
  assert.equal(w.document.getElementById('operatorRows').textContent,'');assert.equal(w.tenantOperator.member,null,'auth failure cannot reveal retained scope');
 }finally{w.close();}
 console.log('PASS focus: coalesced reauthorization, conceal, silent workspace switch, old response rejection, auth failure');
}
async function deepLinkReadPollTests(){
 const target=row(7),b=browser('dialogs',`&open_dialog=1&provider=connect&lead_id=7&channel_id=${target.channel_id}&conversation_id=client7`),{w,requests,emit,reply}=b;
 const timers=new Map();let serial=100000,hidden=false;
 const realSet=w.setTimeout.bind(w),realClear=w.clearTimeout.bind(w);
 w.setTimeout=(fn,delay,...args)=>{if(delay===30000){const id=++serial;timers.set(id,fn);return id;}return realSet(fn,delay,...args);};
 w.clearTimeout=id=>{timers.delete(id);realClear(id);};Object.defineProperty(w.document,'hidden',{configurable:true,get:()=>hidden});
 try{
  w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));
  assert.equal(requests.length,1);assert.equal(requests[0].url,'/api/platform/me');assert.doesNotMatch(w.document.getElementById('operatorDialogs').textContent,/Synthetic/);
  emit('tenant-view',{view:'dialogs'});assert.equal(requests.length,1,'no dialog request before delayed authentication');
  requests[0].resolve(response(profile));await settle();await new Promise(resolve=>realSet(resolve,5));await settle();
  const listing=requests.find(r=>r.url.includes('/dialogs?')),history=requests.find(r=>r.url.includes('/history?'));
  assert.ok(listing);assert.ok(history);assert.equal(requests.filter(r=>r.url.includes('/history?')).length,1);
  assert.match(history.url,/lead_id=7/);assert.match(history.url,/conversation_id=client7/);assert.equal(new URL(w.location.href).searchParams.has('open_dialog'),false);
  reply(listing,list([target]));await settle();
  const message={id:'00000000-0000-0000-0000-000000000707',direction:'incoming',message_type:'text',body:'Synthetic message',author_type:'customer',happened_at:'2026-10-23T10:00:00Z'};
  reply(history,{dialog:target,messages:[message],has_more:false});await settle();
  const mark=requests.find(r=>r.url.endsWith('/dialogs/read'));assert.ok(mark,'visible deep-linked history marks only its last message read');assert.equal(mark.options.method,'POST');assert.equal(JSON.parse(mark.options.body).expected_tenant_id,profile.member.tenant_id);
  const staleRefresh=w.tenantOperator.refresh(),stale=requests.at(-1);reply(mark,{read:true});await settle();
  const fresh=requests.filter(r=>r.url.includes('/dialogs?')).at(-1);assert.notEqual(fresh,stale,'read success must not join a pre-read list');
  reply(fresh,list([{...target,unread_count:0}]));await settle();reply(stale,{...list([{...target,unread_count:1}]),unread_count:1});await staleRefresh;
  assert.equal(requests.filter(r=>r.url.endsWith('/dialogs/read')).length,1,'same last message is not repeatedly marked');
  emit('tenant-view',{view:'dialogs'});emit('tenant-view',{view:'dialogs'});assert.equal(timers.size,1,'view events retain one polling timer');
  const [id,poll]=[...timers][0];timers.delete(id);const polling=poll();await settle();const pollRequest=requests.at(-1);assert.ok(pollRequest.url.includes('/dialogs?'));reply(pollRequest,list([target]));await polling;assert.equal(timers.size,1);
  hidden=true;emit('visibilitychange');assert.equal(timers.size,0,'hidden tabs stop polling');assert.equal(w.document.getElementById('operatorDialogs').style.visibility,'hidden');
  hidden=false;emit('visibilitychange');await settle();const reauth=requests.filter(r=>r.url==='/api/platform/me').at(-1);reauth.resolve(response(profile));
  // Visibility refresh may already be pending; it remains concealed until /me succeeds.
  for(const request of requests.filter(r=>r.url.includes('/dialogs?')))reply(request,list([target]));await settle();assert.equal(w.document.getElementById('operatorDialogs').style.visibility,'');
  assert.ok(requests.every(r=>(r.options.method||'GET')==='GET'||r.url.endsWith('/dialogs/read')),'no provider/send/agent-state mutation is exercised');
 }finally{w.close();}
 console.log('PASS deep link/read/poll: delayed authentication, exact route, readmark race, one timer, hidden-tab pause and reauthorization');
}

async function hiddenChatActionTests(){
 const b=browser('tasks'),{w,requests,emit,reply}=b;
 try{
  w.tenantProfileSnapshot=profile;w.history.replaceState({leadId:41},'');
  const chat=w.document.createElement('div');chat.className='wa-composer';chat.innerHTML='<textarea id="deal-chat-input"></textarea>';w.document.body.append(chat);
  w.eval(read('docs/tenant-ai-agents.js'));await settle();
  let state='active';
  const completeControl=async()=>{const owned=requests.filter(r=>r.url.includes('/for-lead/')).at(-1);assert.ok(owned);reply(owned,{conversation_id:'client@s.whatsapp.net',provider:'connect',channel_id:row(1).channel_id});await settle();const control=requests.filter(r=>r.url.includes('/state?')).at(-1);reply(control,{state,agent_name:'Synthetic agent',resume_allowed:true});await settle();};
  await completeControl();
  for(const [label,action,nextState] of [['Приостановить AI','pause','paused'],['Возобновить AI','resume','active'],['Передать человеку','handoff','human']]){
   const button=[...w.document.querySelectorAll('#aiAgentChatToggle button')].find(node=>node.textContent===label);assert.ok(button,label);
   const pending=button.onclick();await settle();const mutation=requests.at(-1);assert.equal(mutation.options.method,'POST');assert.ok(mutation.url.endsWith('/'+action));
   const data=JSON.parse(mutation.options.body);assert.equal(data.provider,'connect');assert.equal(data.channel_id,row(1).channel_id);assert.equal(data.expected_tenant_id,profile.member.tenant_id);assert.equal(data.expected_user_id,11);
   state=nextState;reply(mutation,{state});await settle();await completeControl();await pending;
  }
  assert.ok(!requests.some(r=>/\/(agents|simulation|knowledge)(\/|\?|$)/.test(r.url)),'chat actions do not eagerly fetch hidden agent configuration');
  const before=requests.length;await w.document.getElementById('aiSaveSettings').onclick();assert.equal(requests.length,before,'configuration mutations remain blocked until settings are ready');assert.match(w.document.getElementById('aiAgentsNotice').textContent,/loading/);
  const stale=[...w.document.querySelectorAll('#aiAgentChatToggle button')][0];assert.ok(stale);emit('tenant-profile',{...profile,capabilities:{modules:{ai_agents:false}}});await stale.onclick();assert.equal(requests.length,before,'old action closure cannot mutate after revocation');
 }finally{w.close();}
 console.log('PASS hidden chat: real Pause/Resume/Handoff path works without configuration reads; config/revoked actions remain blocked');
}
async function sameScopePendingFocusTests(){
 const b=browser(),{w,requests,emit,reply}=b;
 try{
  w.tenantProfileSnapshot=profile;w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));emit('tenant-profile',profile);
  const oldList=requests[0];const previousOpen=w.tenantOperator.openDialog(row(1));reply(requests.at(-1),{dialog:row(1),messages:[]});await previousOpen;const oldOpen=w.tenantOperator.openDialog(row(3)),oldHistory=requests.at(-1);
  w.dispatchEvent(new w.Event('focus'));const reauth=requests.at(-1);reauth.resolve(response(profile));await settle();
  const newList=requests.filter(r=>r.url.includes('/dialogs?')).at(-1),newHistory=requests.filter(r=>r.url.includes('/history?')).at(-1);
  assert.notEqual(newList,oldList,'same-profile focus starts a replacement list');assert.notEqual(newHistory,oldHistory,'pending selected history resumes after same-profile authorization');
  reply(newList,list([row(3)]));reply(newHistory,{dialog:row(3),messages:[]});await settle();
  reply(oldList,list([row(1)]));reply(oldHistory,{dialog:row(1),messages:[]});await oldOpen;await settle();
  assert.match(w.document.getElementById('operatorRows').textContent,/Synthetic 3/);assert.match(w.document.getElementById('operatorConversationHeader').textContent,/Synthetic 3/);
  assert.doesNotMatch(w.document.getElementById('operatorNotice').textContent,/scope changed/);
 }finally{w.close();}
 const a=browser('ai_agents');
 try{
  a.w.tenantProfileSnapshot=profile;a.w.eval(read('docs/tenant-ai-agents.js'));const obsolete=a.requests.slice();assert.equal(obsolete.length,4);
  a.w.dispatchEvent(new a.w.Event('focus'));a.requests.at(-1).resolve(response(profile));await settle();const replacement=a.requests.slice(5,9);assert.equal(replacement.length,4,'same-profile focus replaces obsolete agent load');
  for(const request of replacement)a.reply(request,request.url.endsWith('/agents')?[]:{});await settle();
  for(const request of obsolete)a.reply(request,request.url.endsWith('/agents')?[{id:'old',name:'OBSOLETE'}]:{});await settle();
  assert.match(a.w.document.getElementById('aiAgentList').textContent,/noAgents/);assert.doesNotMatch(a.w.document.getElementById('aiAgentList').textContent,/OBSOLETE/);assert.equal(a.w.document.getElementById('aiAgentList').getAttribute('aria-busy'),'false');
  a.w.dispatchEvent(new a.w.Event('focus'));a.requests.at(-1).resolve(response(profile));await settle();const warm=a.requests.slice(-4);assert.equal(warm.filter(request=>request.url.endsWith('/agents')).length,1,'warm ready focus starts fresh agent reads');
  for(const request of warm)a.reply(request,request.url.endsWith('/agents')?[]:{});await settle();assert.equal(a.w.document.querySelector('[data-ai-overview-create]').disabled,false,'configuration readiness recovers after warm focus');

 }finally{a.w.close();}
 console.log('PASS same-scope focus: obsolete list/history/agent flights cannot suppress replacement loads');
}


async function caseResolverNavigationTests(){
 const caseId='00000000-0000-4000-8000-000000000777',messageId='00000000-0000-4000-8000-000000000778',target=row(7);
 const link=`&case_id=${caseId}&message_id=${messageId}`;
 function setup(query=link){
  const b=browser('dialogs',query),{w}=b;
  w.sessionStorage.setItem(`tenant.operator.route.${profile.member.tenant_id}.${profile.member.telegram_id}.`,JSON.stringify(row(3)));
  w.tenantProfileSnapshot=profile;w.tenantAppNavigate=(view)=>b.emit('tenant-view',{view});
  w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));
  b.histories=()=>b.requests.filter(r=>r.url.includes('/history?'));
  b.resolvers=()=>b.requests.filter(r=>r.url.includes('/conversation?'));
  b.resolve=(request=b.resolvers().at(-1))=>b.reply(request,{case_id:caseId,message_id:messageId,route:target});
  return b;
 }
 const tick=async()=>{await new Promise(r=>setTimeout(r,5));await settle();};
 for(const outcome of ['success','failure','invalid']){
  const b=setup(outcome==='invalid'?'&case_id=invalid':link);
  try{
   await tick();assert.equal(b.histories().length,0,'explicit links suppress saved route while resolving, including invalid links');
   if(outcome==='invalid'){assert.equal(b.resolvers().length,0);continue;}
   if(outcome==='failure')b.reply(b.resolvers()[0],{},500);else b.resolve();
   await settle();assert.equal(b.histories().length,outcome==='success'?1:0,'failed resolver cannot silently show a different saved conversation');
   if(outcome==='success'){
    const req=b.histories()[0],query=new URL(req.url,'https://fixture.test').searchParams;
    assert.equal(query.get('lead_id'),'7');assert.equal(query.get('anchor'),messageId);
    b.reply(req,{dialog:target,messages:[],has_more:false});await settle();
    b.emit('tenant-profile',profile);await tick();assert.equal(b.histories().length,1,'repeated profile does not replay a resolved case');
   }
  }finally{b.w.close();}
 }
 const ordinary=setup('');try{await tick();assert.equal(ordinary.histories().length,1);assert.match(ordinary.histories()[0].url,/lead_id=3/,'ordinary navigation still restores saved route');}finally{ordinary.w.close();}
 for(const cancel of ['close','escape','leave','select','revoke','switch']){
  const b=setup();try{
   await tick();const pending=b.resolvers()[0];
   if(cancel==='close')b.w.tenantOperator.closeDialog();
   if(cancel==='escape')b.w.document.dispatchEvent(new b.w.KeyboardEvent('keydown',{key:'Escape'}));
   if(cancel==='leave')b.emit('tenant-view',{view:'tasks'});
   if(cancel==='select')void b.w.tenantOperator.openDialog(row(9));
   if(cancel==='revoke')b.emit('tenant-profile',{...profile,capabilities:{modules:{dialogs:false}}});
   if(cancel==='switch')b.emit('tenant-profile',{...profile,member:{...profile.member,tenant_id:'00000000-0000-0000-0000-000000000102'}});
   const count=b.histories().length;b.resolve(pending);await settle();assert.equal(b.histories().length,count,`late case resolver must not override ${cancel}`);
  }finally{b.w.close();}
 }
 const back=setup();try{
  await tick();const first=back.resolvers()[0];
  back.w.history.pushState({},'','/app?view=tasks');back.w.dispatchEvent(new back.w.PopStateEvent('popstate'));back.resolve(first);await settle();assert.equal(back.histories().length,0);
  back.w.history.pushState({},'','/app?view=dialogs'+link);back.w.dispatchEvent(new back.w.PopStateEvent('popstate'));assert.equal(back.resolvers().length,2);back.resolve();await settle();assert.equal(back.histories().length,1);
 }finally{back.w.close();}
 const focus=setup();try{
  await tick();const old=focus.resolvers()[0];focus.w.dispatchEvent(new focus.w.Event('focus'));focus.resolve(old);await settle();assert.equal(focus.histories().length,0);
  focus.requests.find(r=>r.url==='/api/platform/me').resolve(response(profile));await settle();assert.equal(focus.resolvers().length,2,'pending case restarts only after fresh authorization');focus.resolve();await settle();assert.equal(focus.histories().length,1);
 }finally{focus.w.close();}
 console.log('PASS case resolver: saved-route suppression, anchor, errors, ordinary restore, cancellation, navigation, scope changes and focus retry');
}

(async()=>{await requestTests();await agentTests();await dialogTests();await bootstrapTests();await defaultSupportLandingTests();await focusTests();await deepLinkReadPollTests();await hiddenChatActionTests();await sameScopePendingFocusTests();await caseResolverNavigationTests();})().catch(error=>{console.error(error);process.exitCode=1;});
