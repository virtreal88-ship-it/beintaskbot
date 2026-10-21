const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const root=path.resolve(__dirname,'..'),read=name=>fs.readFileSync(path.join(root,name),'utf8');
const settle=async()=>{for(let i=0;i<20;i++)await new Promise(resolve=>setImmediate(resolve));};
(async()=>{
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app?site_lang=ru',runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[],confirmations=[];
 const member={tenant_id:'tenant-a',telegram_id:11,role:'owner',permissions:['dialogs'],active:true};
 w.tenantProfileSnapshot={member,capabilities:{modules:{dialogs:true},chat_ai:true}};
 const route={provider:'connect',channel_id:'channel-a',conversation_id:'conversation-a',lead_id:41,customer_name:'Customer',channel_name:'Support',state:'active',display_state:'manual',controls_allowed:true,can_send:true,available_actions:[]};
 const messages=[{external_id:'photo-1',direction:'incoming',message_type:'image',body:'Error light is blinking',happened_at:'1'},
  {external_id:'voice-1',direction:'incoming',message_type:'voice',body:'Printer is ready?',has_transcription:true,happened_at:'2'},
  {external_id:'question-1',direction:'incoming',message_type:'text',body:'Hazırdı?',happened_at:'3'}];
 let allowMedia=false;w.confirm=message=>{confirmations.push(message);return allowMedia;};
 w.fetch=async(url,options={})=>{const parsed=new URL(url,w.location),body=options.body?JSON.parse(options.body):null;calls.push({path:parsed.pathname,body});let result={};
  if(parsed.pathname.endsWith('/dialogs/history'))result={dialog:route,messages,next_before:null};
  else if(parsed.pathname.endsWith('/dialogs'))result={dialogs:[route],channels:[],agents:[],attention_count:0};
  else if(parsed.pathname.endsWith('/dialogs/copilot'))result={text:'Sifarişi yoxlayıb təsdiqlənmiş məlumatı sizə bildirəcəyəm.',mode:'reply',context_usage:{messages:3,voice_transcriptions:1,images:1,documents:0,skipped:[{kind:'video',reason:'video_not_analyzed'}]},knowledge_sources:[]};
  return{ok:true,status:200,json:async()=>({success:true,result})};};
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));await settle();await w.tenantOperator.openDialog(route);await settle();
 d.querySelector('#operatorAIReply').click();await settle();assert.equal(calls.some(call=>call.path.endsWith('/dialogs/copilot')),false,'declining media consent sends no AI request');allowMedia=true;d.querySelector('#operatorAIReply').click();await settle();
 const request=calls.find(call=>call.path.endsWith('/dialogs/copilot'));
 assert.ok(request,'AI reply uses the existing Copilot endpoint: '+JSON.stringify(calls)+' / '+d.querySelector('#operatorAIReplyStatus')?.textContent);assert.equal(request.body.include_media,true);assert.equal(confirmations.length,2);assert.match(confirmations[0],/OpenAI/);
 assert.equal(d.querySelector('#chatText').value,'Sifarişi yoxlayıb təsdiqlənmiş məlumatı sizə bildirəcəyəm.');
 assert.match(d.querySelector('#operatorAIContextUsage').textContent,/1 фото/);assert.match(d.querySelector('#operatorAIContextUsage').textContent,/1 транскрипция аудио/);assert.match(d.querySelector('#operatorAIContextUsage').textContent,/видео не анализировалось/);
 assert.equal(calls.filter(call=>call.path.endsWith('/messages/send')).length,0,'generated answer is never sent automatically');
 dom.window.close();console.log('Copilot UI: media confirmation, localized context receipts and manual-only composer insertion passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
