const assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const {installOperatorCaseFixture}=require('./operator_case_ui_fixture.cjs');
const html=fs.readFileSync('docs/tenant-app.html','utf8'),source=name=>fs.readFileSync('docs/'+name,'utf8');
const settle=async()=>{for(let i=0;i<16;i++)await new Promise(resolve=>setImmediate(resolve));};
const actualSupportOpen=html.split('\n').find(line=>line.includes("document.addEventListener('tenant-support-open',event=>"));
async function setup(options={}){
 const dom=new JSDOM(html,{url:'https://crm.test/app?view=dialogs',runScripts:'outside-only'}),w=dom.window,d=w.document;
 w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});w.eval('('+installOperatorCaseFixture.toString()+')('+JSON.stringify(options)+')');
 w.eval(source('tenant-operator-i18n.js'));w.tenantI18n.setLanguage('ru');w.eval(source('tenant-chat-send.js'));w.eval(source('tenant-operator-dialogs.js'));w.eval(actualSupportOpen);await settle();
 return{dom,w,d,state:w.caseUI,posts:()=>w.caseUI.calls.filter(c=>c.method==='POST'&&c.path==='/api/platform/ai/support-tickets'),sends:()=>w.caseUI.calls.filter(c=>c.method==='POST'&&c.path==='/api/platform/crm/chat/send'),close:()=>dom.window.close()};
}
async function openCreate(ctx){ctx.d.querySelector('[data-operator-create-ticket]').click();await settle();const modal=ctx.d.querySelector('#operatorCreateCaseModal');assert(modal);return modal;}
const submit=(ctx,form)=>form.dispatchEvent(new ctx.w.Event('submit',{bubbles:true,cancelable:true}));
const selectDeveloper=modal=>{assert.equal(modal.querySelector('select'),null);assert.equal(modal.querySelector('[type=checkbox]'),null);};
function closeCreate(ctx,modal){modal.dispatchEvent(new ctx.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));assert.equal(ctx.d.querySelector('#operatorCreateCaseModal'),null);}
async function mountDraft(ctx){
 ctx.w.eval(source('tenant-support-consultations.js'));const host=ctx.d.createElement('div');ctx.d.body.append(host);
 ctx.w.tenantCaseConsultations.mount({host,ticket:{id:'case-with-reply',status:'new',lead_id:41,channel_id:'client-channel',conversation_id:'client-conversation',agent_id:'agent'},member:ctx.w.tenantProfileSnapshot.member,getMessages:()=>[],isCurrent:()=>true});await settle();
 const review=[...host.querySelectorAll('button')].find(button=>button.textContent=== 'Проверить черновик и отправить вручную');assert(review,'real case reply card exposes Review draft: '+host.textContent);return review;
}
(async()=>{
 for(const mode of ['manual','automatic','human','paused','off']){
  const ctx=await setup({mode});try{
   await ctx.w.tenantOperator.openDialog(ctx.state.route);let modal=await openCreate(ctx);assert.equal(ctx.posts().length,0,'opening is not confirmation');selectDeveloper(modal);closeCreate(ctx,modal);
   modal=await openCreate(ctx);selectDeveloper(modal);ctx.state.createOutcome='lost';submit(ctx,modal.querySelector('form'));submit(ctx,modal.querySelector('form'));await settle();assert.equal(ctx.posts().length,1);const immutable=JSON.stringify(ctx.posts()[0].body);assert.equal(modal.querySelector('select'),null);closeCreate(ctx,modal);
   modal=await openCreate(ctx);assert.equal(modal.querySelector('select'),null,'reopened unknown request retains original request without a recipient picker');ctx.state.createOutcome='ok';submit(ctx,modal.querySelector('form'));await settle();assert.equal(ctx.posts().length,2);assert.equal(JSON.stringify(ctx.posts()[1].body),immutable,'same full identity and UUID across retry/reopen');assert.equal(ctx.d.querySelector('#operatorCreateCaseModal'),null);assert.equal(ctx.sends().length,0);
  }finally{ctx.close();}
 }
 for(const [field,value]of Object.entries({id:'',tenant_id:'other-tenant',create_request_id:'other-request',lead_id:42,channel_id:'wrong-channel',conversation_id:'wrong-client'})){
  const ctx=await setup();try{await ctx.w.tenantOperator.openDialog(ctx.state.route);let modal=await openCreate(ctx);selectDeveloper(modal);ctx.state.responseChange={[field]:value};submit(ctx,modal.querySelector('form'));await settle();assert.equal(modal.querySelector('[type=submit]').disabled,true,'wrong '+field+' must hold');closeCreate(ctx,modal);modal=await openCreate(ctx);assert.equal(modal.querySelector('[type=submit]').disabled,true);submit(ctx,modal.querySelector('form'));await settle();assert.equal(ctx.posts().length,1,'mismatched identity is never silently retried');}finally{ctx.close();}
 }
 // Saved pre-upgrade payloads keep their original request and validated recipient.
 const old=await setup();try{
  await old.w.tenantOperator.openDialog(old.state.route);
  const payload={request_id:'legacy-saved-request',lead_id:41,channel_id:'client-channel',conversation_id:'client-conversation',expected_tenant_id:'ui-tenant',expected_user_id:11,assigned_developer_channel_id:'developer-channel',assigned_developer_conversation_id:'developer-a',developer_confirmed:true};
  old.w.sessionStorage.setItem('tenant.operator.manual-case.ui-tenant:11.41',JSON.stringify({payload,recipient:'Developer A'}));
  const modal=await openCreate(old);assert.equal(modal.querySelector('select'),null);submit(old,modal.querySelector('form'));await settle();
  assert.deepEqual(JSON.parse(JSON.stringify(old.posts()[0].body)),payload);assert.equal(old.d.querySelector('#operatorCreateCaseModal'),null);
  assert.equal(old.state.calls.some(c=>c.path.endsWith('/dialog-consultations/destinations')),false,'recovery needs no recipient catalog');
 }finally{old.close();}
 // The legacy CRM chat action resolves a server source route and uses the same ledger.
 const legacy=await setup();try{
  legacy.w.eval(source('tenant-support-inbox.js'));await settle();const host=legacy.d.getElementById('modalContent');host.innerHTML='';legacy.d.dispatchEvent(new legacy.w.CustomEvent('tenant-chat-open',{detail:{lead_id:41}}));legacy.d.getElementById('supportCreateFromChat').click();await settle();let modal=legacy.d.getElementById('operatorCreateCaseModal');assert(modal);selectDeveloper(modal,'1');legacy.state.createOutcome='lost';submit(legacy,modal.querySelector('form'));await settle();const payload=JSON.stringify(legacy.posts()[0].body);closeCreate(legacy,modal);legacy.d.dispatchEvent(new legacy.w.CustomEvent('tenant-chat-close'));host.innerHTML='';legacy.d.dispatchEvent(new legacy.w.CustomEvent('tenant-chat-open',{detail:{lead_id:41}}));legacy.d.getElementById('supportCreateFromChat').click();await settle();modal=legacy.d.getElementById('operatorCreateCaseModal');legacy.state.createOutcome='ok';submit(legacy,modal.querySelector('form'));await settle();assert.equal(JSON.stringify(legacy.posts()[1].body),payload);assert.equal(legacy.posts()[1].body.assigned_developer_conversation_id,undefined);assert.equal(legacy.posts()[1].body.channel_id,'client-channel');
 }finally{legacy.close();}
 // Exercise the real support-card event, openDialog cache/server replacement, and normal sender.
 for(const cached of [false,true]){
  const ctx=await setup();try{
   if(cached){await ctx.w.tenantOperator.openDialog(ctx.state.route);ctx.w.tenantOperator.closeDialog(false,true);}
   const review=await mountDraft(ctx);if(cached)ctx.state.holdHistory=true;review.click();await settle();if(cached){assert.equal(ctx.d.getElementById('chatText').value,'Private developer follow-up draft','cached composer receives draft before server refresh');ctx.state.holdHistory=false;ctx.state.releaseHistory();await settle();}
   assert.equal(ctx.d.getElementById('chatText').value,'Private developer follow-up draft','authoritative history preserves transferred draft');assert.equal(ctx.sends().length,0,'review only inserts a private draft');
   ctx.w.tenantOperator.closeDialog(false,true);ctx.w.history.replaceState({view:'dialogs',operatorDialog:ctx.state.route,operatorDialogOwner:ctx.w.tenantProfileSnapshot.member.tenant_id+':'+ctx.w.tenantProfileSnapshot.member.telegram_id},'');ctx.w.dispatchEvent(new ctx.w.PopStateEvent('popstate',{state:ctx.w.history.state}));await settle();assert.equal(ctx.d.getElementById('chatText').value,'Private developer follow-up draft','history roundtrip retains private composer text');assert.equal(ctx.sends().length,0);
   ctx.state.holdSend=true;ctx.d.getElementById('sendChat').click();ctx.d.getElementById('sendChat').click();await settle();assert.equal(ctx.sends().length,1,'only explicit normal Send submits once');assert.equal(ctx.sends()[0].body.text,'Private developer follow-up draft');ctx.state.holdSend=false;ctx.state.releaseSend();await settle();assert.equal(ctx.d.getElementById('chatText').value,'');ctx.w.tenantOperator.closeDialog(false,true);await ctx.w.tenantOperator.openDialog(ctx.state.route);assert.equal(ctx.d.getElementById('chatText').value,'','cache/history cannot resurrect a sent draft');
  }finally{ctx.close();}
 }
 const employee=await setup();try{
  await employee.w.tenantOperator.openDialog(employee.state.route);const input=employee.d.getElementById('chatText');input.value='Employee draft stays intact';input.dispatchEvent(new employee.w.Event('input',{bubbles:true}));const review=await mountDraft(employee);review.click();await settle();assert.equal(employee.d.getElementById('chatText').value,'Employee draft stays intact');assert.match(employee.d.getElementById('operatorCaseDraftReview').textContent,/Private developer follow-up draft/);assert.equal(employee.d.querySelector('#operatorCaseDraftReview button').disabled,true);await employee.w.tenantOperator.openDialog(employee.state.route,true);assert.equal(employee.d.getElementById('chatText').value,'Employee draft stays intact');assert.equal(employee.d.querySelectorAll('#operatorCaseDraftReview').length,1,'same-dialog refresh preserves a single draft conflict panel');assert.equal(employee.sends().length,0);
 }finally{employee.close();}
 console.log('PASS: actual compact and legacy Create identity recovery; real case Review draft -> openDialog cache/server/history -> explicit normal Send once; employee drafts preserved');
})().catch(error=>{console.error(error);process.exitCode=1;});
