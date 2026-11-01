const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const settle=async()=>{for(let i=0;i<16;i++)await new Promise(r=>setImmediate(r));};
(async()=>{
 const dom=new JSDOM('<div><textarea id="chatText"></textarea><button id="sendChat"></button></div>',{url:'https://crm.test/app',runScripts:'outside-only'}),w=dom.window,d=w.document;
 w.tenantI18n={language:'ru',t:()=>undefined};const alerts=[];w.alert=t=>alerts.push(t);let uuidCount=0;w.crypto.randomUUID=()=>`00000000-0000-4000-8000-${String(++uuidCount).padStart(12,'0')}`;
 const calls=[];let state='failed';const requestId='00000000-0000-4000-8000-000000000001';
 const response=()=>({quote_protocol:true,request_id:requestId,delivery_state:state,delivery_phase:state==='failed'?'not_sent':'unknown',error_code:state==='failed'?'quoted_message_unavailable':undefined,provider_accepted:false,retry_safe:state==='failed'});
 w.eval(fs.readFileSync('docs/tenant-chat-send.js','utf8'));
 const config={api:async(path,opt)=>{calls.push({path,opt});return response();},identity:'scope',leadId:41,me:{tenant_id:'e09a5c24-093f-4141-992d-532052e00c90',telegram_id:11},provider:'connect',channelId:'96863d75-d272-4c93-a25e-ffb2b16ecae3',isCurrent:()=>true,reload:async()=>{},getQuote:()=>({quote_message_id:'source',quote_channel_id:'96863d75-d272-4c93-a25e-ffb2b16ecae3',quote_conversation_id:'recipient'})};
 w.tenantChatSend.bind(config);assert.equal(d.querySelector('#quoteReceiptDiagnostic'),null,'receipt diagnostics are absent from ordinary composer');d.getElementById('chatText').value='fresh explicit reply';d.getElementById('sendChat').click();await settle();
 assert.equal(calls.length,1);assert.equal(calls[0].opt.method,'POST');assert.match(alerts.at(-1),/Ответ не отправлялся/);assert.equal(d.querySelector('#quoteReceiptDiagnostic'),null,'sending does not insert diagnostics into the conversation');
 const diagnostics=d.createElement('section');diagnostics.id='operatorDiagnosticsBody';w.tenantChatSend.renderDiagnostics(diagnostics);assert.equal(calls.length,1,'opening the diagnostics place performs no network request');const receiptDetails=diagnostics.querySelector('#quoteReceiptDiagnostic');assert.ok(receiptDetails);assert.equal(receiptDetails.open,false,'receipt details start collapsed');receiptDetails.open=true;assert.equal(receiptDetails.querySelector('summary').textContent,'Диагностика ответа');assert.match(receiptDetails.querySelector('output').textContent,/quoted_message_unavailable/);assert.match(receiptDetails.querySelector('output').textContent,/UUID:/);
 const beforeUuid=uuidCount,beforeText=d.getElementById('chatText').value;
 receiptDetails.querySelector('[data-quote-receipt-check]').click();await settle();assert.equal(calls.at(-1).opt.method,'GET');assert.match(calls.at(-1).path,/quote-receipt\?/);assert.equal(uuidCount,beforeUuid);assert.equal(d.getElementById('chatText').value,beforeText);assert.equal(calls.filter(x=>x.opt.method==='POST').length,1);
 // Pending quote uses GET, preserves the UUID/draft and never invokes send again.
 state='unknown';d.getElementById('sendChat').click();await settle();const created=uuidCount;
 d.getElementById('sendChat').click();await settle();assert.equal(uuidCount,created);assert.equal(calls.at(-1).opt.method,'GET');assert.match(calls.at(-1).path,/request_id=/);assert.match(alerts.at(-1),/Повторная отправка отключена/);
 dom.window.close();console.log('PASS: safe quote diagnostics, latest GET after cleared failure, pending GET without POST/new UUID');
})().catch(error=>{console.error(error);process.exitCode=1;});
