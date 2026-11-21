const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const root=path.join(__dirname,'..'),read=file=>fs.readFileSync(path.join(root,file),'utf8');
const tenant='00000000-0000-0000-0000-000000000101',channel='00000000-0000-4000-8000-000000000201';
const row={provider:'connect',lead_id:41,channel_id:channel,conversation_id:'client',customer_kind:'contact',customer_name:'Synthetic client',display_state:'human',state:'human',ai_availability:'stopped',available_actions:[],can_send:false,last_message_id:'m1',unread_count:0,last_message_direction:'incoming',last_message_text:'Synthetic request'};
const tick=async()=>{for(let i=0;i<18;i++)await new Promise(resolve=>setImmediate(resolve));};
(async()=>{
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app?view=dialogs',runScripts:'outside-only'}),w=dom.window;
 const profile={member:{tenant_id:tenant,telegram_id:33,role:'worker',permissions:['dialogs'],active:true},capabilities:{modules:{dialogs:true}}};
 let status=null,routingEnforced=false,canOffline=false,getCalls=0,postBody=null;
 w.tenantProfileSnapshot=profile;
 w.fetch=async(url,options={})=>{
  const value=String(url);
  if(value.includes('/api/platform/me'))return{ok:true,json:async()=>profile};
  if(value.includes('/dialogs/presence')){
   if(options.method==='POST'){postBody=JSON.parse(options.body);status=postBody.status;return{ok:true,json:async()=>({success:true,result:{available:true,status,configured:true,routing_enforced:routingEnforced,can_set_offline:canOffline}})};}
   const query=new URL(value,w.location.origin).searchParams;
   assert.equal(query.get('expected_tenant_id'),tenant);assert.equal(query.get('expected_user_id'),'33');
   getCalls++;return{ok:true,json:async()=>({success:true,result:{available:true,status:status??'online',configured:status!==null,can_update:true,routing_enforced:routingEnforced,can_set_offline:canOffline}})};
  }
  if(value.includes('/dialogs?'))return{ok:true,json:async()=>({success:true,result:{dialogs:[row],channels:[{id:'connect:'+channel,name:'WhatsApp'}],agents:[],attention_count:0,unread_count:0,total_count:1,next_offset:null}})};
  throw Error('Unexpected request '+value);
 };
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));await tick();await w.tenantOperator.refresh();await tick();
 const button=w.document.getElementById('operatorPresenceOpen');
 assert.ok(button&&button.hidden,'status is not fetched before the user opens tools');
 assert.equal(getCalls,0,'status is not read until the tools menu opens');
 const menu=w.document.getElementById('operatorToolsMenu'),trigger=menu.querySelector('summary');
 async function openTools(){
  const previousGets=getCalls;
  // JSDOM queues toggle on a timer; setImmediate polling can see the open
  // attribute before that event. Wait for the native event instead of firing a
  // second synthetic event that can race the production event listener.
  await new Promise((resolve,reject)=>{
   const timeout=setTimeout(()=>{menu.removeEventListener('toggle',opened);reject(Error('Opening tools did not emit toggle'));},3000);
   function opened(){if(!menu.open)return;clearTimeout(timeout);menu.removeEventListener('toggle',opened);resolve();}
   menu.addEventListener('toggle',opened);
   trigger.click();
  });
  await tick();
  assert.equal(getCalls,previousGets+1,'each native menu opening fetches one tenant-scoped status');
 }
 await openTools();
 assert.equal(menu.open,true,'clicking the tools summary opens its menu');
 assert.ok(button&&!button.hidden,'current employee status is available in the tools menu');
 assert.equal(getCalls,1,'the menu loads one tenant-scoped status snapshot');
 assert.match(button.textContent,/Online|В сети|Onlayn/);
 assert.equal(postBody,null,'displaying default Online does not save it');
 button.click();await tick();
 assert.ok(w.document.getElementById('operatorPresenceModal'),'status control opens an accessible dialog');
 assert.match(w.document.getElementById('operatorPresenceModal').textContent,/does not affect|не влияет|təsir etmir/);
 const select=w.document.getElementById('operatorPresenceSelect');assert.equal(select.value,'online');
 assert.equal(select.querySelector('option[value=offline]'),null,'legacy three-state responses hide Offline');
 assert.equal(postBody,null,'opening the picker does not save default status');select.value='lunch';
 w.document.getElementById('operatorPresenceModal').querySelector('form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();
 assert.equal(postBody.status,'lunch');
 assert.equal(postBody.expected_tenant_id,tenant);assert.equal(postBody.expected_user_id,33);
 assert.equal(Object.keys(postBody).sort().join(','),'expected_tenant_id,expected_user_id,status','only the selected local status and identity scope are sent');
 assert.match(button.textContent,/Lunch|Обед|Nahar/);
 assert.equal(w.document.getElementById('operatorPresenceModal'),null,'successful update closes the dialog');
 assert.ok(getCalls>=1,'status is read from the tenant-scoped server endpoint');
 routingEnforced=true;await openTools();
 assert.match(button.textContent,/Lunch|Обед|Nahar/,'reopening tools shows stored manual choice');
 button.click();await tick();
 assert.match(w.document.getElementById('operatorPresenceModal').textContent,/Only operators|доступны операторам|operatorlara açıqdır/);
 w.document.getElementById('operatorPresenceModal').querySelector('button[type=button]').click();
 status=null;await openTools();button.click();await tick();
 assert.match(w.document.getElementById('operatorPresenceModal').textContent,/before receiving new dialogues|текущем режиме|Cari rejimdə/);
 w.document.getElementById('operatorPresenceModal').querySelector('button[type=button]').click();
 canOffline=true;status='offline';routingEnforced=false;await openTools();
 assert.match(button.textContent,/Offline|Не в сети|Oflayn/);
 button.click();await tick();
 const expanded=w.document.getElementById('operatorPresenceSelect');
 assert.equal(expanded.value,'offline');assert.ok(expanded.querySelector('option[value=offline]'));

 const denied=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app?view=dialogs',runScripts:'outside-only'}),dw=denied.window;
 dw.tenantProfileSnapshot={member:{tenant_id:tenant,telegram_id:55,role:'worker',permissions:[],active:true},capabilities:{modules:{dialogs:false}}};
 dw.fetch=async()=>({ok:true,json:async()=>dw.tenantProfileSnapshot});
 dw.eval(read('docs/tenant-operator-i18n.js'));dw.eval(read('docs/tenant-operator-dialogs.js'));await tick();
 assert.equal(dw.document.getElementById('operatorPresenceOpen'),null,'the status control is not mounted without dialogue access');
 dom.window.close();denied.window.close();
 console.log('PASS: presence control reads and saves only the authenticated employee status; no message or provider request is sent.');
})().catch(error=>{console.error(error);process.exitCode=1;});
