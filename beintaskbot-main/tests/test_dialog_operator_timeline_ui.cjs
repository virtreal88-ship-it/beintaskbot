const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const read=f=>fs.readFileSync(path.join(__dirname,'..','docs',f),'utf8');
const tenant='00000000-0000-0000-0000-000000000101',channel='00000000-0000-4000-8000-000000000201';
const first={provider:'connect',lead_id:41,channel_id:channel,conversation_id:'client',customer_kind:'contact',
 customer_name:'Synthetic contact',display_state:'human',state:'human',ai_availability:'stopped',
 available_actions:[],can_send:false,lifecycle_enabled:true,lifecycle:{state:'accepted',revision:8},last_message_id:'m3'};
const other={...first,lead_id:42,conversation_id:'other',customer_name:'Other contact',last_message_id:'m4'};
const msg=(id,at)=>({id,direction:'incoming',source:'connect',message_type:'text',happened_at:at,body:'synthetic '+id});
const m1=msg('m1','2030-01-01T09:00:00Z'),m2=msg('m2','2030-01-01T10:00:00Z'),m3=msg('m3','2030-01-01T12:00:00Z');
const m4=msg('m4','2030-01-01T14:00:00Z');
const accept={id:'accepted-a',kind:'accept',at:'2030-01-01T08:45:00Z',employee_name:'Owner'};
const handoff={id:'transferred-a',kind:'transfer',at:'2030-01-01T11:00:00Z',employee_name:'<a>Employee</a>'};
const anonymous={id:'transferred-b',kind:'transfer',at:'2030-01-01T13:00:00Z',employee_name:''};
const started={id:'started-c',kind:'start',at:'2030-01-01T13:15:00Z',employee_name:'Employee'};
const tick=async()=>{for(let i=0;i<18;i++)await new Promise(resolve=>setImmediate(resolve));};
(async()=>{
 const w=new JSDOM(read('tenant-app.html'),{url:'https://timeline.test/app?view=dialogs',runScripts:'outside-only'}).window;
 w.document.querySelector('#view-dialogs').classList.add('active');
 w.tenantProfileSnapshot={success:true,member:{tenant_id:tenant,telegram_id:33,role:'owner',active:true,permissions:['dialogs']},capabilities:{modules:{dialogs:true}}};
 const queries=[];let release,holdOlder=false,exact=first,cooldown=false,dense=false,serverDefersOlder=false;
 const clock=w.Date.now();let now=clock;w.Date.now=()=>now;
 const denseNotes=Array.from({length:220},(_,i)=>({id:'dense-'+String(i).padStart(3,'0'),kind:'transfer',at:'2030-01-01T09:20:00Z',employee_name:'Synthetic employee'}));
 const res=result=>({ok:true,json:async()=>({success:true,result})});
 w.fetch=async (url,options={})=>{
  const u=new URL(url,w.location.origin),q=u.searchParams;
  assert.ok(!options.method||options.method==='GET','operator events never trigger a write or a provider action');
  if(u.pathname==='/api/platform/me')return {ok:true,json:async()=>w.tenantProfileSnapshot};
  if(u.pathname.endsWith('/dialogs/preferences'))return res({preferences:{}});
  if(u.pathname.endsWith('/dialogs'))return res({dialogs:[exact,other],channels:[],agents:[],total_count:2,unread_count:0,next_offset:null});
  if(u.pathname.endsWith('/dialogs/history')){
   queries.push(Object.fromEntries(q));
   if(q.get('operator_events')==='1'){
    assert.equal(q.get('expected_tenant_id'),tenant);assert.equal(q.get('expected_user_id'),'33');
   }else assert.equal(cooldown,true,'operator event opt-in may only be omitted during backoff');
   if(q.get('lead_id')==='42')return res({dialog:other,messages:[m4],next_before:null,operator_events:[],operator_events_revision:8});
   if(q.has('before')){
    assert.equal(q.get('before'),'m2');assert.equal(q.has('operator_events_revision'),false);
    const optional=!q.has('operator_events')?{}:serverDefersOlder?{operator_events_retry_after_ms:12000}:
      {operator_events:dense?denseNotes:[accept],operator_events_revision:8};
    const result={dialog:exact,messages:[m1],next_before:null,...optional};
    if(holdOlder)return new Promise(done=>{release=()=>done(res(result));});
    return res(result);
   }
   if(cooldown)return res({dialog:exact,messages:[m2,m3],next_before:'m2',...(q.has('operator_events')?{operator_events_retry_after_ms:12000}:{})});
   const sameRevision=q.get('operator_events_revision')===String(exact.lifecycle.revision);
   return res({dialog:exact,messages:[m2,m3],next_before:'m2',...(sameRevision?{}:
      {operator_events:exact.lifecycle.revision===8?[handoff]:[started,handoff,anonymous],operator_events_revision:exact.lifecycle.revision})});
  }
  throw new Error('Unexpected synthetic request '+u.pathname);
 };
 w.eval(read('tenant-operator-i18n.js'));w.eval(read('tenant-operator-dialogs.js'));await tick();
 const view=()=>[...w.document.querySelectorAll('#chatRows > [data-message-id],#chatRows > [data-operator-timeline-event]')]
     .map(n=>n.dataset.messageId||n.dataset.operatorTimelineEvent);
 const find=id=>w.document.querySelector('[data-operator-timeline-event="'+id+'"]');
 await w.tenantOperator.openDialog(first,false,'',{suppressRead:true});await tick();
 assert.deepEqual(view(),['m2','transferred-a','m3']);
 assert.ok(find('transferred-a').textContent.includes('<a>Employee</a>'),'employee name is literal text');
 assert.equal(find('transferred-a').querySelector('a,button,[data-message-id]'),null);
 assert.equal(w.document.querySelector('#chatRows [data-operator-timeline-event] [datetime]').getAttribute('datetime'),handoff.at);
 assert.equal(w.document.querySelectorAll('#chatRows [data-message-id]').length,2);
 assert.equal(find('accepted-a'),null,'older action waits for its message page');
 w.document.getElementById('operatorEarlier').click();await tick();
 assert.deepEqual(view(),['accepted-a','m1','m2','transferred-a','m3']);
 await w.tenantOperator.openDialog(first,true,'',{suppressRead:true});await tick();
 assert.equal(queries.at(-1).operator_events_revision,'8');
 assert.deepEqual(view(),['accepted-a','m1','m2','transferred-a','m3'],'same-route refresh preserves earlier messages and does not duplicate notes');
 exact={...first,lifecycle:{...first.lifecycle,revision:9}};
 await w.tenantOperator.openDialog(first,true,'',{suppressRead:true});await tick();
 assert.deepEqual(view(),['m2','transferred-a','m3','transferred-b','started-c']);
 assert.match(find('transferred-b').textContent,/to an employee|передан сотруднику|əməkdaşa ötürüldü/);
 assert.match(find('started-c').textContent,/Started dialog|Начал\(а\) диалог|Dialoqa başladı/);
 assert.equal(w.document.getElementById('operatorNewMessages').hidden,true,'an audit note is not a new client message');
 cooldown=true;
 exact={...first,lifecycle:{...first.lifecycle,revision:10}};
 await w.tenantOperator.openDialog(first,true,'',{suppressRead:true});await tick();
 assert.equal(queries.at(-1).operator_events,'1');
 now+=3000;
 // Incoming message/row revision revalidation still loads normal history,
 // but it cannot invoke the optional audit query during its backoff window.
 exact={...first,lifecycle:{...first.lifecycle,revision:11}};
 await w.tenantOperator.openDialog(first,true,'',{suppressRead:true});await tick();
 assert.equal(queries.at(-1).operator_events,undefined);
 const assertOlderOmission=()=>{
  assert.equal(w.document.getElementById('operatorEarlier').hidden,true,'normal paging cursor still advances');
  assert.deepEqual([...w.document.querySelectorAll('#chatRows [data-message-id]')].map(n=>n.dataset.messageId),['m1','m2','m3']);
  const note=w.document.querySelector('#chatRows [data-operator-timeline-omissions]');
  assert.ok(note,'an omitted older audit window is announced');
  assert.equal(note.previousElementSibling.dataset.messageId,'m1');
  assert.equal(note.nextElementSibling.dataset.messageId,'m2','the note is placed at the actual page boundary');
  assert.equal(find('accepted-a'),null,'no omitted action is invented');
 };
 w.document.getElementById('operatorEarlier').click();await tick();
 assert.equal(queries.at(-1).before,'m2');assert.equal(queries.at(-1).operator_events,undefined);
 assertOlderOmission();
 now+=10000;cooldown=false;
 exact={...first,lifecycle:{...first.lifecycle,revision:12}};
 await w.tenantOperator.openDialog(first,true,'',{suppressRead:true});await tick();
 assert.equal(queries.at(-1).operator_events,'1');
 serverDefersOlder=true;
 w.document.getElementById('operatorEarlier').click();await tick();
 assert.equal(queries.at(-1).operator_events,'1');
 assertOlderOmission();
 serverDefersOlder=false;now+=13000;
 exact={...first,lifecycle:{...first.lifecycle,revision:13}};
 await w.tenantOperator.openDialog(first,true,'',{suppressRead:true});await tick();
 holdOlder=true;w.document.getElementById('operatorEarlier').click();await tick();
 await w.tenantOperator.openDialog(other,false,'',{suppressRead:true});release();await tick();
 assert.deepEqual(view(),['m4'],'late older response from another route is discarded');
 holdOlder=false;dense=true;
 await w.tenantOperator.openDialog(first,false,'',{suppressRead:true});await tick();
 w.document.getElementById('operatorEarlier').click();await tick();
 assert.equal(w.document.querySelectorAll('#chatRows [data-operator-timeline-event]').length,180);
 assert.deepEqual([...w.document.querySelectorAll('#chatRows [data-message-id]')].map(n=>n.dataset.messageId),['m1','m2','m3']);
 const omissions=w.document.querySelectorAll('#chatRows [data-operator-timeline-omissions]');
 assert.equal(omissions.length,1);
 assert.match(omissions[0].textContent,/Some system events are not shown|Часть системных событий не показана|Sistem hadisələrinin bir hissəsi göstərilmir/);
 w.document.dispatchEvent(new w.CustomEvent('tenant-auth-invalid'));assert.deepEqual(view(),[]);
 w.close();
 console.log('PASS: durable employee notes, order/paging/dedup, literal names and stale route/session fencing.');
})().catch(error=>{console.error(error);process.exitCode=1;});
