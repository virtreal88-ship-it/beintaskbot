const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const root=path.resolve(__dirname,'..'),read=name=>fs.readFileSync(path.join(root,name),'utf8');
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
  else if(parsed.pathname.endsWith('/dialogs/copilot')&&body.mode==='chat')result={text:'Zəhmət olmasa AnyDesk kodunu göndərin.',mode:'chat',knowledge_sources:[]};
  else if(parsed.pathname.endsWith('/dialogs/copilot'))result={text:'Sifarişi yoxlayıb təsdiqlənmiş məlumatı sizə bildirəcəyəm.',mode:'reply',context_usage:{messages:3,voice_transcriptions:1,images:1,documents:0,skipped:[{kind:'video',reason:'video_not_analyzed'}]},knowledge_sources:[]};
  return{ok:true,status:200,json:async()=>({success:true,result})};};
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));await settle();await w.tenantOperator.openDialog(route);await settle();
 d.querySelector('#operatorAIReply').click();await settle();assert.equal(calls.some(call=>call.path.endsWith('/dialogs/copilot')),false,'declining media consent sends no AI request');assert.equal(digestStarted,false,'declining consent does not prepare a request identity');allowMedia=true;d.querySelector('#operatorAIReply').click();await until(()=>digestStarted,'request identity preparation');
 assert.equal(calls.some(call=>call.path.endsWith('/dialogs/copilot')),false,'pending WebCrypto cannot dispatch early');assert.equal(d.querySelector('#operatorAIReply').disabled,true,'reply stays busy while identity hashing is pending');d.querySelector('#operatorAIReply').click();releaseDigest();
 await until(()=>d.querySelector('#chatText').value==='Sifarişi yoxlayıb təsdiqlənmiş məlumatı sizə bildirəcəyəm.','AI reply completion');
 const request=calls.find(call=>call.path.endsWith('/dialogs/copilot'));
 assert.ok(request,'AI reply uses the existing Copilot endpoint: '+JSON.stringify(calls)+' / '+d.querySelector('#operatorAIReplyStatus')?.textContent);assert.equal(calls.filter(call=>call.path.endsWith('/dialogs/copilot')).length,1,'pending rapid clicks dispatch once');assert.equal(request.body.include_media,true);assert.equal(confirmations.length,2);assert.match(confirmations[0],/OpenAI/);
 assert.equal(d.querySelector('#chatText').value,'Sifarişi yoxlayıb təsdiqlənmiş məlumatı sizə bildirəcəyəm.');
 assert.match(d.querySelector('#operatorAIContextUsage').textContent,/1 фото/);assert.match(d.querySelector('#operatorAIContextUsage').textContent,/1 транскрипция аудио/);assert.match(d.querySelector('#operatorAIContextUsage').textContent,/видео не анализировалось/);
 assert.equal(calls.filter(call=>call.path.endsWith('/messages/send')).length,0,'generated answer is never sent automatically');
 d.querySelector('#operatorCopilotToggle').click();d.querySelector('#operatorCopilotPrompt').value='Попроси AnyDesk';d.querySelector('#operatorCopilotAsk').click();d.querySelector('#operatorCopilotAsk').click();await until(()=>d.querySelector('#operatorCopilotResult').textContent==='Zəhmət olmasa AnyDesk kodunu göndərin.','private helper completion');
 assert.equal(calls.filter(call=>call.path.endsWith('/dialogs/copilot')&&call.body.mode==='chat').length,1,'rapid clicks generate once');
 assert.equal(d.querySelector('#operatorCopilotResult').textContent,'Zəhmət olmasa AnyDesk kodunu göndərin.');
 assert.equal(d.querySelector('#chatText').value,'Sifarişi yoxlayıb təsdiqlənmiş məlumatı sizə bildirəcəyəm.','private command does not replace customer draft');
 assert.equal(calls.filter(call=>call.path.endsWith('/messages/send')).length,0,'private commands never send to customer');
 dom.window.close();console.log('Copilot UI: media confirmation, localized context receipts and manual-only composer insertion passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
