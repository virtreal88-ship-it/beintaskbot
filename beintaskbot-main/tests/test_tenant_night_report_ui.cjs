const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const ROOT=path.join(__dirname,'..'),read=file=>fs.readFileSync(path.join(ROOT,file),'utf8');
const A='00000000-0000-0000-0000-000000000101',B='00000000-0000-0000-0000-000000000102',CA='00000000-0000-0000-0000-000000000201',CC='00000000-0000-0000-0000-000000000901',ID='00000000-0000-0000-0000-000000000401';
const settle=async()=>{for(let n=0;n<8;n++)await new Promise(resolve=>setImmediate(resolve));};
async function waitFor(check,label){for(let n=0;n<100;n++){if(check())return;await settle();}throw new Error('Not ready: '+label);}
function setup(url='https://crm.test/app/night-report',options={}){
 const dom=new JSDOM(read('docs/tenant-night-report.html'),{url,runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[],errors=[];
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};w.matchMedia=()=>({matches:false});w.addEventListener('error',e=>errors.push(e.error));
 const member={tenant_id:A,telegram_id:11,role:'owner',active:true};let deny=false,deferred=null;
 const ticket={id:ID,channel_id:CA,conversation_id:'same-jid',lead_id:41,contact_name:'Customer <img src=x onerror=alert(1)>',phone:'+994111',channel_name:'Night WhatsApp',description:'No internet <script>bad()</script>',summary:'AI suggested restarting router',suggestions:'Restart router',next_steps:'Check line',status:'waiting',assigned_to:null,created_in_shift:true,created_at:'2026-10-15T21:00:00Z',updated_at:'2026-10-16T01:00:00Z'};
 const messages=[{id:'message-1',body:'<img src=x onerror=alert(1)>',direction:'incoming',message_type:'text',happened_at:'2026-10-15T21:00:00Z'}];
 function payload(tenant=A,offset='0') {return {tenant_id:tenant,company_name:tenant===A?'Company A':'Company B',scope:'connect',period:{date:'2026-10-15',timezone:'Asia/Baku',start:'19:00',end:'09:00',starts_at:'2026-10-15T19:00:00+04:00',ends_at:'2026-10-16T09:00:00+04:00',state:'completed'},defaults:{timezone_source:'company',multiple_schedules:false},metrics:{dialogs:3,ai_dialogs:2,handed_off:1,unresolved:2,ai_errors:1,connect_errors:0,taken_in_work:1,attention_count:2},tickets:offset==='0'?[{...ticket}]:[{...ticket,id:'other-ticket',contact_name:'Older backlog'}],failures:[{kind:'ai',code:'ai_failed',channel_id:CC,conversation_id:'same-jid',contact_name:'Second channel',channel_name:'Second WhatsApp',lead_id:43}],generated_at:'2026-10-16T09:01:00+04:00',next_offset:offset==='0'?50:null};}
 const result=value=>({ok:true,status:200,json:async()=>structuredClone({success:true,result:value})});
 w.fetch=async(url,init={})=>{const u=new URL(String(url),'https://crm.test');calls.push({url:u,method:init.method||'GET'});if(u.pathname==='/api/platform/me')return{ok:true,status:200,json:async()=>({member})};
   const tenant=u.searchParams.get('expected_tenant_id');
   if(u.pathname==='/api/platform/ai/night-report'){
     if(deny)return{ok:false,status:403,json:async()=>({success:false,error:'Access revoked'})};
     if(deferred){const fn=deferred;deferred=null;return await fn();}
     return result(payload(tenant,u.searchParams.get('offset')));
   }
   const channel=u.searchParams.get('channel_id')||CA;
   return result({ticket:u.pathname.includes('/tickets/')?ticket:null,dialog:{channel_name:channel===CA?'Night WhatsApp':'Second WhatsApp',channel_id:channel,conversation_id:'same-jid',contact_name:'Customer',lead_id:channel===CA?41:43},messages:messages.map(m=>({...m,id:u.searchParams.has('before')?'older':m.id})),next_before:u.searchParams.has('before')?null:'message-1'});
 };
 w.eval(read('docs/tenant-night-report.js'));
 return {w,d,calls,errors,member,payload,result,ticket,get deny(){return deny;},set deny(value){deny=value;},set defer(value){deferred=value;}};
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
 console.log('PASS: report metrics, filters/date, ticket/history/channel links, pagination, safe text, read-only GETs, language and stale tenant isolation.');
})().catch(error=>{console.error(error);process.exitCode=1;});
