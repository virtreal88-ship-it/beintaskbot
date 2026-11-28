const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const settle=async()=>{for(let i=0;i<12;i++)await new Promise(r=>setImmediate(r));};
(async()=>{
 const dom=new JSDOM('<button id="sendChat"></button><textarea id="chatText"></textarea>',{url:'https://crm.test/app',runScripts:'outside-only'}),w=dom.window,d=w.document;
 const alerts=[],commands=[],reads=[];let accepted=0,discarded=0,current=true,sequence=0;
 w.alert=text=>alerts.push(text);w.tenantI18n={language:'ru',t:()=>undefined};w.crypto.randomUUID=()=>`00000000-0000-4000-8000-${String(++sequence).padStart(12,'0')}`;
 const statusError=Object.assign(Error('source scope changed'),{keepRequest:false});
 const outcomes=[{delivery_state:'pending',quote_protocol:true,provider_accepted:false},statusError,{delivery_state:'unknown',quote_protocol:true,provider_accepted:false},{delivery_state:'failed',quote_protocol:true,provider_accepted:false,retry_safe:false},{delivery_state:'sent',quote_protocol:true,provider_accepted:true,delivered:false},{delivery_state:'failed',quote_protocol:true,provider_accepted:false,retry_safe:true},{delivery_state:'delivered',quote_protocol:true,provider_accepted:true,delivered:true}];
 w.eval(fs.readFileSync('docs/tenant-chat-send.js','utf8'));
 const config={identity:'tenant:33',leadId:41,me:{tenant_id:'tenant',telegram_id:33},provider:'connect',isCurrent:()=>current,reload:async()=>{},onAccepted:()=>accepted++,onPending:()=>{},onDiscard:()=>discarded++,getQuote:()=>({quote_message_id:'source',quote_channel_id:'channel',quote_conversation_id:'client'}),api:async(url,opts)=>{
  if(opts.method==='GET'){
   const requestId=new URL(url,w.location.origin).searchParams.get('request_id');
   assert.ok(requestId);assert.equal(opts.body,undefined);reads.push(requestId);
   if(url.includes('/send-status?'))return {state:'unknown'};
  }else commands.push(JSON.parse(opts.body));
  const outcome=outcomes.shift();if(outcome instanceof Error)throw outcome;return {state:'accepted',message_id:'queue',...outcome};}};
 w.tenantChatSend.bind(config);const input=d.getElementById('chatText'),button=d.getElementById('sendChat');input.value='reply';
 for(let i=0;i<4;i++){button.click();await settle();assert.equal(accepted,0);assert.equal(input.value,'reply');assert.ok(w.sessionStorage.getItem('tenant-chat-send:tenant:33:41'));}
 assert.equal(commands.length,1,'status checks do not POST');assert.deepEqual(new Set(reads),new Set([commands[0].request_id]),'status checks preserve the original UUID');
 assert.match(alerts[0],/Принято в очередь/);assert.equal(alerts[1],'source scope changed');assert.match(alerts[2],/Повторная отправка отключена/);assert.equal(discarded,0);
 button.click();await settle();assert.equal(accepted,1);assert.equal(input.value,'');assert.equal(w.sessionStorage.getItem('tenant-chat-send:tenant:33:41'),null);assert.match(d.getElementById('tenantChatSendStatus').textContent,/Провайдер принял/);assert.notEqual(d.getElementById('tenantChatSendStatus').textContent,'Доставка подтверждена.');
 input.value='retry explicitly';button.click();await settle();assert.equal(discarded,1);assert.equal(accepted,1);assert.equal(commands.length,2,'known failure does not auto-send a new request');assert.equal(w.sessionStorage.getItem('tenant-chat-send:tenant:33:41'),null);
 button.click();await settle();assert.equal(accepted,2);assert.equal(d.getElementById('tenantChatSendStatus').textContent,'Доставка подтверждена.');assert.notEqual(commands[1].request_id,commands[2].request_id,'new request requires a separate employee click after definite no-send');
 const alertCount=alerts.length;const ordinary={...config,getQuote:()=>null,api:async()=>({state:'accepted'})};w.tenantChatSend.bind(ordinary);input.value='ordinary';button.click();await settle();assert.equal(accepted,3,'ordinary acceptance behavior is preserved');assert.equal(alerts.length,alertCount,'success must not block the audio thread with an alert');assert.equal(d.getElementById('tenantChatSendStatus').textContent,'','ordinary acceptance clears the previous notice without adding a banner');
 const key='tenant-chat-send:tenant:33:41';
 for(const state of ['accepted','blocked','unknown','unavailable']){
  const requestId='00000000-0000-4000-8000-000000009999';
  w.sessionStorage.setItem(key,JSON.stringify({request_id:requestId,text:'Previous answer'}));
  const calls=[];
  w.tenantChatSend.bind({...ordinary,api:async(url,opt)=>{
   calls.push(opt.method);
   if(opt.method==='POST'){
    assert.equal(state,'unavailable');
    assert.equal(url,'/api/platform/crm/chat/send-status/close-unsubmitted');
    assert.deepEqual(JSON.parse(opt.body),{lead_id:41,request_id:requestId,expected_tenant_id:'tenant',expected_user_id:33});
    return {state:'blocked'};
   }
   assert.match(url,/send-status\?/);assert.equal(new URL(url,w.location.origin).searchParams.get('request_id'),requestId);
   return {state};
  }});
  input.value='A new draft';
  button.click();await settle();
  assert.deepEqual(calls,state==='unavailable'?['GET','POST']:['GET']);
  assert.equal(input.value,'A new draft');
  assert.equal(w.sessionStorage.getItem(key)===null,['accepted','blocked','unavailable'].includes(state));
 }
 dom.window.close();console.log('PASS: quoted queued/unknown/failed/sent/delivered states, same-UUID read checks and no automatic retry');
})().catch(error=>{console.error(error);process.exitCode=1;});
