const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const root=path.resolve(__dirname,'..'),legacyUi=process.env.TEST_LEGACY_COPILOT_UI,
  read=name=>fs.readFileSync(name==='docs/tenant-operator-dialogs.js'&&legacyUi?legacyUi:path.join(root,name),'utf8');
const settle=async()=>{for(let i=0;i<20;i++)await new Promise(resolve=>setImmediate(resolve));};
const until=async(predicate,label)=>{const deadline=Date.now()+5000;while(!predicate()){assert.ok(Date.now()<deadline,'Timed out waiting for '+label);await new Promise(resolve=>setTimeout(resolve,5));}};
(async()=>{
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app?site_lang=ru',runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[],confirmations=[];
 const member={tenant_id:'tenant-a',telegram_id:11,role:'owner',permissions:['dialogs'],active:true};
 let releaseDigest,digestStarted=false;const digestGate=new Promise(resolve=>{releaseDigest=resolve;});
 w.TextEncoder=TextEncoder;Object.defineProperty(w.crypto,'subtle',{value:{digest:async(...args)=>{digestStarted=true;await digestGate;return require('node:crypto').webcrypto.subtle.digest(...args);}}});w.tenantProfileSnapshot={member,capabilities:{modules:{dialogs:true},chat_ai:true}};
 const route={provider:'connect',agent_id:'assigned-agent',channel_id:'channel-a',conversation_id:'conversation-a',lead_id:41,customer_name:'Customer',channel_name:'Support',state:'active',display_state:'manual',controls_allowed:true,can_send:true,available_actions:[]};
 const messages=[{external_id:'photo-1',direction:'incoming',message_type:'image',body:'Error light is blinking',happened_at:'1'},
  {external_id:'voice-1',direction:'incoming',message_type:'voice',body:'Printer is ready?',has_transcription:true,happened_at:'2'},
  {external_id:'question-1',direction:'incoming',message_type:'text',body:'Hazırdı?',happened_at:'3'}];
 let allowMedia=false;w.confirm=message=>{confirmations.push(message);return allowMedia;};
 w.fetch=async(url,options={})=>{const parsed=new URL(url,w.location),body=options.body?JSON.parse(options.body):null;calls.push({path:parsed.pathname,body});let result={};
  if(parsed.pathname.endsWith('/dialogs/history'))result={dialog:route,messages,next_before:null};
  else if(parsed.pathname.endsWith('/dialogs'))result={dialogs:[route],channels:[],agents:[],attention_count:0};
  else if(parsed.pathname.endsWith('/dialogs/copilot')){const count=calls.filter(x=>x.path.endsWith('/dialogs/copilot')).length;const keep=count<3;return{ok:false,status:409,json:async()=>({success:false,error:keep?'Контекст очищен. Ответ не показан и не сохранён.':'Другая ошибка',keep_request:keep})};}
  return{ok:true,status:200,json:async()=>({success:true,result})};};
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));await settle();await w.tenantOperator.openDialog(route);await settle();
 allowMedia=true;d.querySelector('#chatText').value='Черновик клиента';
 d.querySelector('#operatorAIReply').click();await until(()=>digestStarted,'request identity');releaseDigest();
 await until(()=>d.querySelector('#operatorAIReplyStatus').textContent.includes('Контекст очищен'),'first failure');
 assert.equal(d.querySelector('#chatText').value,'Черновик клиента','error never enters customer composer');
 d.querySelector('#operatorAIReply').click();await until(()=>calls.filter(c=>c.path.endsWith('/dialogs/copilot')).length===2,'retry');await settle();
 let requests=calls.filter(c=>c.path.endsWith('/dialogs/copilot'));
 assert.equal(requests[0].body.request_id,requests[1].body.request_id,'retry uses same request');
 assert.equal(requests[0].body.history_notice_version,legacyUi?undefined:1);
 assert.equal(d.querySelector('#chatText').value,'Черновик клиента');
 d.querySelector('#operatorAIReply').click();await until(()=>d.querySelector('#operatorAIReplyStatus').textContent.includes('Другая ошибка'),'other 409');
 d.querySelector('#operatorAIReply').click();await until(()=>calls.filter(c=>c.path.endsWith('/dialogs/copilot')).length===4,'retry other 409');await settle();
 requests=calls.filter(c=>c.path.endsWith('/dialogs/copilot'));
 assert.notEqual(requests[2].body.request_id,requests[3].body.request_id,'other 409 clears request identity');
 assert.equal(d.querySelector('#chatText').value,'Черновик клиента');
 dom.window.close();console.log('Copilot UI error, retry and composer isolation passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
