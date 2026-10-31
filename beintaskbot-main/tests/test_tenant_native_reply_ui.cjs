const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.SUPPORT_CASES_JSDOM||'jsdom');
const read=p=>fs.readFileSync(p,'utf8'),tick=()=>new Promise(resolve=>setTimeout(resolve,0));
(async()=>{
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://example.test/app?view=dialogs',runScripts:'outside-only'}),w=dom.window,d=w.document;
 const tenant='00000000-0000-4000-8000-000000000001',channel='00000000-0000-4000-8000-000000000002',id='00000000-0000-4000-8000-000000000004';
 const route={provider:'connect',lead_id:41,channel_id:channel,conversation_id:'client',state:'human',can_send:true,controls_allowed:true,available_actions:[]};
 const profile={member:{tenant_id:tenant,telegram_id:33,role:'owner',active:true},capabilities:{modules:{dialogs:true}}};
 const msg={id,external_id:'connect:message',provider_message_id:'PROVIDER123',direction:'incoming',message_type:'text',body:'Please help',author_name:'Customer',author_type:'customer'};
 let failSend=true,hideSource=false;const calls=[];w.tenantProfileSnapshot=profile;w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});w.alert=()=>{};w.crypto.randomUUID=()=> '00000000-0000-4000-8000-000000000010';
 w.fetch=async(url,options={})=>{url=String(url);calls.push({url,options});let result;
 if(url.includes('/chat/send')){if(failSend){failSend=false;throw new Error('network lost');}result={state:'accepted'};}
 else if(url.includes('/dialogs/history'))result={dialog:route,messages:hideSource?[]:[msg,{...msg,id:'00000000-0000-4000-8000-000000000005',provider_message_id:undefined,body:'Older message'}],next_before:null};
 else if(url.includes('/dialogs?'))result={dialogs:[route],channels:[],agents:[],attention_count:0,next_offset:null};
 else if(url.includes('/api/platform/me'))return{ok:true,json:async()=>profile};
 else throw Error('Unexpected request '+url);
 return {ok:true,json:async()=>({success:true,result})};};
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-chat-send.js'));w.eval(read('docs/tenant-operator-dialogs.js'));await tick();await tick();await w.tenantOperator.openDialog(route);await tick();
 const replyButtons=[...d.querySelectorAll('[data-reply-message]')];assert.equal(replyButtons.length,2);assert(replyButtons.every(b=>b.getAttribute('aria-label')&&b.querySelector('[aria-hidden]')));replyButtons[1].click();assert(d.getElementById('operatorReplyQuote').hidden);assert(d.getElementById('operatorChatError').textContent);assert.equal(calls.filter(x=>x.url.includes('/chat/send')).length,0);
 d.querySelector('[data-reply-message]').click();assert.equal(d.getElementById('operatorReplyQuote').hidden,false);assert(d.getElementById('operatorReplyQuote').textContent.includes('Please help'));
 d.getElementById('operatorReplyQuote').querySelector('button').click();assert(d.getElementById('operatorReplyQuote').hidden);d.querySelector('[data-reply-message]').click();d.getElementById('chatText').value='Answer';d.getElementById('sendChat').click();d.getElementById('sendChat').click();await tick();await tick();
 let sent=calls.filter(x=>x.url.includes('/chat/send'));assert.equal(sent.length,1);assert.equal(d.getElementById('operatorReplyQuote').hidden,false);assert.equal(d.getElementById('operatorReplyQuote').querySelector('button'),null);d.querySelector('[data-reply-message]').click();assert.equal(d.getElementById('operatorReplyQuote').querySelector('button'),null);hideSource=true;await w.tenantOperator.openDialog(route);await tick();assert.equal(d.getElementById('operatorReplyQuote').hidden,false);assert.equal(d.querySelector('[data-reply-message]'),null);assert.equal(d.getElementById('operatorReplyQuote').querySelector('button'),null);d.getElementById('sendChat').click();await tick();await tick();sent=calls.filter(x=>x.url.includes('/chat/send'));assert.equal(sent.length,2);assert.deepEqual(JSON.parse(sent[0].options.body),JSON.parse(sent[1].options.body));const data=JSON.parse(sent[0].options.body);assert.equal(data.quote_message_id,id);assert.equal(data.quote_channel_id,channel);assert.equal(data.quote_conversation_id,'client');
 dom.window.close();console.log('PASS: reply selection, cancel and single send');
})().catch(error=>{console.error(error);process.exitCode=1});
