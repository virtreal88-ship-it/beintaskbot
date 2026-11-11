const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const ROOT=path.join(__dirname,'..'),read=file=>fs.readFileSync(path.join(ROOT,file),'utf8');
const A='00000000-0000-0000-0000-000000000101',B='00000000-0000-0000-0000-000000000102',CA='00000000-0000-0000-0000-000000000201',CC='00000000-0000-0000-0000-000000000901',ID='00000000-0000-0000-0000-000000000401';
const settle=async()=>{for(let n=0;n<8;n++)await new Promise(resolve=>setImmediate(resolve));};
async function waitFor(check,label){for(let n=0;n<100;n++){if(check())return;await settle();}throw new Error('Not ready: '+label);}
function setup(url='https://crm.test/app/night-report',options={}){
 const dom=new JSDOM(read('docs/tenant-night-report.html'),{url,runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[],errors=[];
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};w.matchMedia=()=>({matches:false});w.addEventListener('error',e=>errors.push(e.error));
 const member={tenant_id:A,telegram_id:11,role:'owner',active:true};let deny=false,deferred=null,detailDeny=0,detailDeferred=null;
 const ticket={id:ID,channel_id:CA,conversation_id:'same-jid',lead_id:41,contact_name:'Customer <img src=x onerror=alert(1)>',phone:'+994111',channel_name:'Night WhatsApp',description:'No internet <script>bad()</script>',summary:'AI suggested restarting router',suggestions:'Restart router',next_steps:'Check line',status:'waiting',assigned_to:null,created_in_shift:true,created_at:'2026-10-15T21:00:00Z',updated_at:'2026-10-16T01:00:00Z'};
 const messages=[{id:'message-1',body:'<img src=x onerror=alert(1)>',direction:'incoming',message_type:'text',happened_at:'2026-10-15T21:00:00Z'}];
 function payload(tenant=A,offset='0') {return {tenant_id:tenant,company_name:tenant===A?'Company A':'Company B',scope:'connect',access_scope:options.scope||null,access_version:options.missingVersion?null:options.scope?'access-v1':'legacy-v1',period:{date:'2026-10-15',timezone:'Asia/Baku',start:'19:00',end:'09:00',starts_at:'2026-10-15T19:00:00+04:00',ends_at:'2026-10-16T09:00:00+04:00',state:'completed'},defaults:{timezone_source:'company',multiple_schedules:false},metrics:{dialogs:3,ai_dialogs:2,handed_off:1,unresolved:2,ai_errors:1,connect_errors:0,taken_in_work:1,attention_count:2},tickets:offset==='0'?[{...ticket}]:[{...ticket,id:'other-ticket',contact_name:'Older backlog'}],failures:[{kind:'ai',code:'ai_failed',channel_id:CC,conversation_id:'same-jid',contact_name:'Second channel',channel_name:'Second WhatsApp',lead_id:43}],generated_at:'2026-10-16T09:01:00+04:00',next_offset:offset==='0'?50:null};}
 const result=value=>({ok:true,status:200,json:async()=>structuredClone({success:true,result:value})});
 w.fetch=async(url,init={})=>{const u=new URL(String(url),'https://crm.test');calls.push({url:u,method:init.method||'GET'});if(u.pathname==='/api/platform/me')return{ok:true,status:200,json:async()=>({member})};
   const tenant=u.searchParams.get('expected_tenant_id');
   if(u.pathname==='/api/platform/ai/night-report'){
     if(deny)return{ok:false,status:403,json:async()=>({success:false,error:'Access revoked'})};
     if(deferred){const fn=deferred;deferred=null;return await fn();}
     return result(payload(tenant,u.searchParams.get('offset')));
   }
   if(detailDeny)return{ok:false,status:detailDeny,json:async()=>({success:false,error:'Detail access revoked'})};
   if(detailDeferred){const fn=detailDeferred;detailDeferred=null;return await fn();}
   const channel=u.searchParams.get('channel_id')||CA;
   return result({access_scope:options.scope||null,access_version:options.missingVersion?null:options.scope?'access-v1':'legacy-v1',history_version:options.scope?'history-v1':'legacy-history-v1',history_scope:options.scope?'ticket_messages':'conversation',ticket:u.pathname.includes('/tickets/')?ticket:null,dialog:{channel_name:channel===CA?'Night WhatsApp':'Second WhatsApp',channel_id:channel,conversation_id:'same-jid',contact_name:'Customer',lead_id:channel===CA?41:43},messages:messages.map(m=>({...m,id:u.searchParams.has('before')?'older':m.id})),next_before:u.searchParams.has('before')?null:'message-1'});
 };
 w.eval(read('docs/tenant-night-report.js'));
 return {w,d,calls,errors,member,payload,result,ticket,get deny(){return deny;},set deny(value){deny=value;},set defer(value){deferred=value;},set detailDeny(value){detailDeny=value;},set detailDefer(value){detailDeferred=value;}};
}
(async()=>{
 const app=setup(),{w,d,calls}=app;await waitFor(()=>d.querySelector('[data-metric=dialogs]'),'initial report');
 assert.equal(d.querySelector('[data-metric=dialogs]').textContent,'3');assert.equal(d.querySelectorAll('[data-metric]').length,8);assert.equal(d.querySelector('#nrDate').value,'2026-10-15');assert.equal(d.querySelector('#nrZone').value,'Asia/Baku');assert.equal(d.querySelector('#nrResult img'),null);assert.equal(d.querySelector('#nrResult script'),null);assert.ok(d.querySelector('[data-nr-ticket]').href.includes('tenant_id='+A));
 d.querySelector('[data-nr-ticket]').click();await waitFor(()=>d.querySelector('#nrHistory'),'ticket detail');assert.equal(d.querySelector('#nrDetail').open,true);assert.match(d.querySelector('#nrDetailBody').textContent,/AI suggested restarting router/);assert.equal(d.querySelector('#nrHistory img'),null);assert.ok(calls.some(c=>c.url.pathname.endsWith('/tickets/'+ID)));
 d.querySelector('#nrEarlier').click();await settle();assert.ok(calls.some(c=>c.url.searchParams.get('before')==='message-1'));assert.equal(d.querySelectorAll('#nrHistory article').length,2);d.querySelector('#nrClose').click();assert.equal(d.querySelector('#nrDetail').open,false);
 d.querySelector('[data-nr-dialog]').click();await settle();assert.ok(calls.some(c=>c.url.pathname.endsWith('/dialog')&&c.url.searchParams.get('channel_id')===CA&&c.url.searchParams.get('conversation_id')==='same-jid'));d.querySelector('#nrClose').click();
 d.querySelector('[data-nr-error]').click();await settle();assert.match(d.querySelector('#nrDetailBody').textContent,/Second WhatsApp/);d.querySelector('#nrClose').click();
 d.querySelector('#nrMore').click();await settle();assert.equal(d.querySelectorAll('.nr-ticket').length,2);assert.equal(d.querySelector('#nrMore').hidden,true);
 d.querySelector('#nrDate').value='2026-10-14';d.querySelector('#nrStart').value='20:00';d.querySelector('#nrEnd').value='08:00';d.querySelector('#nrForm').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await settle();assert.ok(calls.some(c=>c.url.searchParams.get('date')==='2026-10-14'&&c.url.searchParams.get('start')==='20:00'&&c.url.searchParams.get('end')==='08:00'));
 const handled=[];w.tenantNightReportNavigation={openTicket:r=>handled.push(r),openDialog:r=>handled.push(r)};d.querySelector('[data-nr-ticket]').click();d.querySelector('[data-nr-dialog]').click();assert.equal(handled[0].id,ID);assert.equal(handled[1].channel_id,CA);assert.ok(handled.every(r=>r.tenant_id===A));delete w.tenantNightReportNavigation;
 d.querySelector('#nrLanguage').value='en';d.querySelector('#nrLanguage').dispatchEvent(new w.Event('change'));await settle();assert.equal(d.querySelector('h1').textContent,'Night shift report');d.querySelector('#nrLanguage').value='az';d.querySelector('#nrLanguage').dispatchEvent(new w.Event('change'));await settle();assert.equal(d.querySelector('h1').textContent,'Gecə növbəsi hesabatı');
 app.deny=true;d.querySelector('#nrForm').dispatchEvent(new w.Event('submit',{cancelable:true}));await settle();assert.equal(d.querySelector('#nrResult').children.length,0);assert.match(d.querySelector('#nrNotice').textContent,/Access revoked/);assert.ok(calls.every(c=>c.method==='GET'),'No state or send mutations from report');assert.equal(app.errors.length,0,String(app.errors[0]));w.close();
 const stale=setup();await waitFor(()=>stale.d.querySelector('[data-metric]'),'stale setup');let resolveOld;stale.defer=()=>new Promise(resolve=>{resolveOld=resolve;});stale.d.querySelector('#nrForm').dispatchEvent(new stale.w.Event('submit',{cancelable:true}));await waitFor(()=>resolveOld,'deferred request');stale.w.document.dispatchEvent(new stale.w.CustomEvent('tenant-profile',{detail:{member:{...stale.member,tenant_id:B}}}));await waitFor(()=>stale.d.querySelector('#nrCompany').textContent==='Company B','new company');resolveOld(stale.result({...stale.payload(A),company_name:'SECRET stale company'}));await settle();assert.equal(stale.d.querySelector('#nrCompany').textContent,'Company B');assert.ok(!stale.d.body.textContent.includes('SECRET'));stale.w.close();
 const deep=setup('https://crm.test/app/night-report?tenant_id='+B+'&ticket_id='+ID);await settle();assert.equal(deep.calls.filter(c=>c.url.pathname.includes('/night-report')).length,0);assert.equal(deep.d.querySelector('#nrResult').children.length,0);deep.w.close();
 const linked=setup('https://crm.test/app/night-report?tenant_id='+A+'&ticket_id='+ID);await waitFor(()=>linked.d.querySelector('#nrHistory'),'direct ticket URL');assert.equal(linked.d.querySelector('#nrDetail').open,true);linked.w.close();
 for(const [lang,scope,scopeText] of [['ru','self','свои источники'],['az','department','şöbənizin'],['en','all','all company sources']]){
   const scoped=setup('https://crm.test/app/night-report?site_lang='+lang,{scope});await waitFor(()=>scoped.d.querySelector('.nr-scope'),'scope label '+lang);
   assert.ok(scoped.d.querySelector('.nr-scope').textContent.toLowerCase().includes(scopeText));
   const escaped=[];scoped.w.tenantNightReportNavigation={openTicket:r=>escaped.push(r),openDialog:r=>escaped.push(r)};
   assert.ok(scoped.d.querySelector('[data-nr-dialog]').href.includes('ticket_id='+ID),'Ticket history deep link retains ticket authority');
   scoped.d.querySelector('[data-nr-dialog]').click();await waitFor(()=>scoped.d.querySelector('#nrHistory'),'bounded ticket history');
   assert.equal(escaped.length,0,'Scoped report never escapes into general dialog/support navigation');
   assert.ok(scoped.calls.at(-1).url.pathname.endsWith('/tickets/'+ID));
   assert.equal(scoped.calls.at(-1).url.searchParams.get('expected_access_version'),'access-v1');
   scoped.d.querySelector('#nrEarlier').click();await settle();assert.equal(scoped.calls.at(-1).url.searchParams.get('expected_history_version'),'history-v1');
   scoped.d.querySelector('#nrClose').click();assert.equal(scoped.d.querySelector('#nrDetailBody').children.length,0);
   scoped.d.querySelector('[data-nr-error]').click();await waitFor(()=>scoped.d.querySelector('#nrHistory'),'scoped source history');
   assert.ok(scoped.calls.at(-1).url.pathname.endsWith('/dialog'));assert.equal(escaped.length,0);
   scoped.d.querySelector('#nrClose').click();scoped.d.querySelector('#nrMore').click();await settle();assert.equal(scoped.calls.at(-1).url.searchParams.get('expected_access_version'),'access-v1');
   scoped.d.querySelector('#nrForm').dispatchEvent(new scoped.w.Event('submit',{cancelable:true}));await settle();assert.equal(scoped.calls.at(-1).url.searchParams.has('expected_access_version'),false,'Full refresh accepts current authorization');
   scoped.w.close();
 }
 for(const status of [403,404,409]){
   const revoked=setup('https://crm.test/app/night-report',{scope:'self'});await waitFor(()=>revoked.d.querySelector('[data-metric]'),'revocation report');
   revoked.d.querySelector('[data-nr-ticket]').click();await waitFor(()=>revoked.d.querySelector('#nrEarlier'),'revocation detail');
   revoked.detailDeny=status;revoked.d.querySelector('#nrEarlier').click();await settle();
   assert.equal(revoked.d.querySelector('#nrDetail').open,false);assert.equal(revoked.d.querySelector('#nrDetailBody').textContent,'');
   assert.equal(revoked.d.querySelector('#nrDetailTitle').textContent,'');assert.equal(revoked.d.querySelector('#nrResult').textContent,'');
   assert.equal(revoked.d.querySelector('#nrCompany').textContent,'');assert.match(revoked.d.querySelector('#nrNotice').textContent,/revoked/);revoked.w.close();
 }
 const detailRace=setup('https://crm.test/app/night-report',{scope:'department'});await waitFor(()=>detailRace.d.querySelector('[data-metric]'),'detail race');
 let resolveDetail;detailRace.detailDefer=()=>new Promise(resolve=>{resolveDetail=resolve;});
 detailRace.d.querySelector('[data-nr-ticket]').click();await waitFor(()=>resolveDetail,'pending detail');
 detailRace.deny=true;detailRace.d.querySelector('#nrForm').dispatchEvent(new detailRace.w.Event('submit',{cancelable:true}));await settle();
 resolveDetail(detailRace.result({ticket:detailRace.ticket,dialog:{contact_name:'SECRET stale detail'},messages:[],next_before:null}));await settle();
 assert.equal(detailRace.d.querySelector('#nrDetail').open,false);assert.ok(!detailRace.d.body.textContent.includes('SECRET'));assert.equal(detailRace.d.querySelector('#nrResult').textContent,'');detailRace.w.close();
 const sameMember=setup('https://crm.test/app/night-report',{scope:'self'});await waitFor(()=>sameMember.d.querySelector('[data-metric]'),'same-member revoke');
 let resolveMember;sameMember.defer=()=>new Promise(resolve=>{resolveMember=resolve;});sameMember.d.querySelector('#nrForm').dispatchEvent(new sameMember.w.Event('submit',{cancelable:true}));await waitFor(()=>resolveMember,'old permission response');
 sameMember.deny=true;sameMember.d.dispatchEvent(new sameMember.w.CustomEvent('tenant-profile',{detail:{member:{...sameMember.member,access:{pages:{night_report:false}}}}}));await settle();
 resolveMember(sameMember.result({...sameMember.payload(A),company_name:'SECRET old scope'}));await settle();assert.ok(!sameMember.d.body.textContent.includes('SECRET'));assert.equal(sameMember.d.querySelector('#nrResult').textContent,'');sameMember.w.close();
 // A success response from an older deployment must not mix authority sets,
 // even if it ignores expected_access_version or omits the version entirely.
 for(const transition of [
   {label:'legacy to self',initial:{},scope:'self',version:'new-self'},
   {label:'self to legacy',initial:{scope:'self'},scope:null,version:'new-legacy'},
   {label:'old null legacy to self',initial:{missingVersion:true},scope:'self',version:'new-self'},
   {label:'self to old null legacy',initial:{scope:'self'},scope:null,version:null},
   {label:'unversioned legacy continuation',initial:{missingVersion:true},scope:null,version:null},
   {label:'scope changed with unchanged version',initial:{},scope:'self',version:'legacy-v1'}
 ])for(const operation of ['append','detail','earlier']){
   const changed=setup('https://crm.test/app/night-report',transition.initial);await waitFor(()=>changed.d.querySelector('[data-metric]'),transition.label);
   if(operation==='earlier'){
     // Seed an old-version detail as a standalone compatibility scenario. The
     // old null-version report cannot open any continuation, so assert that now.
     changed.d.querySelector('[data-nr-ticket]').click();await settle();
     if(transition.initial.missingVersion){assert.equal(changed.d.querySelector('#nrResult').textContent,'');changed.w.close();continue;}
     await waitFor(()=>changed.d.querySelector('#nrEarlier'),'history transition');
   }
   changed.w.fetch=async(url)=>{
     const u=new URL(String(url),'https://crm.test');
     if(u.pathname==='/api/platform/ai/night-report')return changed.result({...changed.payload(A,'50'),access_scope:transition.scope,access_version:transition.version,
       tickets:[{...changed.ticket,id:'new-authority-ticket',contact_name:'SECRET new response'}],next_offset:null});
     return changed.result({access_scope:transition.scope,access_version:transition.version,history_version:'different-history',history_scope:transition.scope?'ticket_messages':'conversation',
       ticket:changed.ticket,dialog:{channel_name:'Changed',contact_name:'SECRET new detail'},messages:[{body:'SECRET new history'}],next_before:null});
   };
   changed.d.querySelector(operation==='append'?'#nrMore':operation==='detail'?'[data-nr-ticket]':'#nrEarlier').click();await settle();
   assert.equal(changed.d.querySelector('#nrResult').textContent,'',transition.label+' '+operation+' clears old report');
   assert.equal(changed.d.querySelector('#nrDetailBody').textContent,'');assert.equal(changed.d.querySelector('#nrDetailTitle').textContent,'');
   assert.equal(changed.d.querySelector('#nrDetail').open,false);assert.equal(changed.d.querySelector('#nrCompany').textContent,'');
   assert.ok(!changed.d.body.textContent.includes('SECRET'));changed.w.close();
 }
 for(const historyVersion of [null,'changed-history']){
   const historyRace=setup('https://crm.test/app/night-report',{scope:'self'});await waitFor(()=>historyRace.d.querySelector('[data-metric]'),'history version setup');
   historyRace.d.querySelector('[data-nr-ticket]').click();await waitFor(()=>historyRace.d.querySelector('#nrEarlier'),'history version loaded');
   historyRace.w.fetch=async()=>historyRace.result({access_scope:'self',access_version:'access-v1',history_version:historyVersion,
     ticket:historyRace.ticket,dialog:{contact_name:'Customer'},messages:[{body:'SECRET stale history'}],next_before:null});
   historyRace.d.querySelector('#nrEarlier').click();await settle();assert.equal(historyRace.d.querySelector('#nrResult').textContent,'');
   assert.equal(historyRace.d.querySelector('#nrDetailBody').textContent,'');assert.ok(!historyRace.d.body.textContent.includes('SECRET'));historyRace.w.close();
 }
 console.log('PASS: report metrics, filters/date, ticket/history/channel links, pagination, safe text, read-only GETs, language, scoped navigation, exact ticket links, permission revocation, legacy/scoped transitions, stale detail and tenant isolation.');
})().catch(error=>{console.error(error);process.exitCode=1;});
