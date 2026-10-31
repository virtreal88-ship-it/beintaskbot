const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const settle=async()=>{for(let i=0;i<12;i++)await new Promise(r=>setImmediate(r));};
(async()=>{
 const dom=new JSDOM('<button id="sendChat"></button><textarea id="chatText"></textarea>',{url:'https://crm.test/app',runScripts:'outside-only'}),w=dom.window,d=w.document;
 const alerts=[],commands=[];let accepted=0,discarded=0,current=true,sequence=0;
 w.alert=text=>alerts.push(text);w.tenantI18n={language:'ru',t:()=>undefined};w.crypto.randomUUID=()=>`00000000-0000-4000-8000-${String(++sequence).padStart(12,'0')}`;
 const statusError=Object.assign(Error('source scope changed'),{keepRequest:false});
 const outcomes=[{delivery_state:'pending',quote_protocol:true,provider_accepted:false},statusError,{delivery_state:'unknown',quote_protocol:true,provider_accepted:false},{delivery_state:'failed',quote_protocol:true,provider_accepted:false,retry_safe:false},{delivery_state:'sent',quote_protocol:true,provider_accepted:true,delivered:false},{delivery_state:'failed',quote_protocol:true,provider_accepted:false,retry_safe:true},{delivery_state:'delivered',quote_protocol:true,provider_accepted:true,delivered:true}];
 w.eval(fs.readFileSync('docs/tenant-chat-send.js','utf8'));
 const config={identity:'tenant:33',leadId:41,me:{tenant_id:'tenant',telegram_id:33},provider:'connect',isCurrent:()=>current,reload:async()=>{},onAccepted:()=>accepted++,onPending:()=>{},onDiscard:()=>discarded++,getQuote:()=>({quote_message_id:'source',quote_channel_id:'channel',quote_conversation_id:'client'}),api:async(_url,opts)=>{commands.push(JSON.parse(opts.body));const outcome=outcomes.shift();if(outcome instanceof Error)throw outcome;return {state:'accepted',message_id:'queue',...outcome};}};
 w.tenantChatSend.bind(config);const input=d.getElementById('chatText'),button=d.getElementById('sendChat');input.value='reply';
 for(let i=0;i<4;i++){button.click();await settle();assert.equal(accepted,0);assert.equal(input.value,'reply');assert.ok(w.sessionStorage.getItem('tenant-chat-send:tenant:33:41'));}
 assert.equal(new Set(commands.map(x=>x.request_id)).size,1,'status checks preserve the original UUID');
 assert.match(alerts[0],/Принято в очередь/);assert.equal(alerts[1],'source scope changed');assert.match(alerts[2],/Повторная отправка отключена/);assert.equal(discarded,0);
 button.click();await settle();assert.equal(accepted,1);assert.equal(input.value,'');assert.equal(w.sessionStorage.getItem('tenant-chat-send:tenant:33:41'),null);assert.match(alerts.at(-1),/Провайдер принял/);assert.notEqual(alerts.at(-1),'Доставка подтверждена.');
 input.value='retry explicitly';button.click();await settle();assert.equal(discarded,1);assert.equal(accepted,1);assert.equal(commands.length,6,'known failure does not auto-send a new request');assert.equal(w.sessionStorage.getItem('tenant-chat-send:tenant:33:41'),null);
 button.click();await settle();assert.equal(accepted,2);assert.equal(alerts.at(-1),'Доставка подтверждена.');assert.notEqual(commands[5].request_id,commands[6].request_id,'new request requires a separate employee click after definite no-send');
 const ordinary={...config,getQuote:()=>null,api:async()=>({state:'accepted'})};w.tenantChatSend.bind(ordinary);input.value='ordinary';button.click();await settle();assert.equal(accepted,3,'ordinary acceptance behavior is preserved');
 dom.window.close();console.log('PASS: quoted queued/unknown/failed/sent/delivered states, same-UUID read checks and no automatic retry');
})().catch(error=>{console.error(error);process.exitCode=1;});
