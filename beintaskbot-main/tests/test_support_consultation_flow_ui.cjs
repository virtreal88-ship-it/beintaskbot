const assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const settle=async()=>{for(let i=0;i<20;i++)await new Promise(resolve=>setImmediate(resolve));};
const source=fs.readFileSync('docs/tenant-support-consultations.js','utf8');
const base='/api/platform/ai/dialog-consultations',ok=result=>({ok:true,json:async()=>({success:true,result})});
const ticket={id:'case-a',lead_id:41,channel_id:'source-channel',conversation_id:'customer',status:'new',agent_id:'assigned-agent'};
const messages=['text','audio','video'].map((kind,index)=>({id:'message-'+index,public_sender_version:'b'.repeat(64),source_version:'v'+index,external_id:'connect:'+index,message_type:kind,direction:'incoming',public_speaker:'Müştəri',public_text:kind==='text'?'Printer problem':'',media_shareable:kind!=='text',body:kind==='text'?'Printer problem':'',happened_at:'2026-11-01T12:0'+index+':00Z'}));
async function setup({dialogs=true,agent=true,language='ru'}={}){
 const dom=new JSDOM('<div id="host"></div><p id="notice"></p>',{url:'https://crm.test/app',runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[];
 const member={tenant_id:'company-a',telegram_id:11,role:dialogs?'owner':'operator'};
 w.tenantI18n={language,date:value=>String(value)};w.tenantOperator={hasRight:right=>right==='dialogs'?dialogs:true};
 const state={autoDispatches:[],consultations:[],replies:[],prepareError:null,losePrepareResponse:false,dispatchState:'pending'};
 w.fetch=async(url,options={})=>{
  const path=new URL(url,w.location).pathname,body=options.body?JSON.parse(options.body):undefined;calls.push({path,body,options});
  if(path===base+'/preview')return ok({public_header:{version:1,customer_name:'Synthetic customer',verified_phone:'+15550001111',phone_status:'verified',company_name:'Synthetic client company',operator_name:'Synthetic operator'},public_header_hash:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',messages,case_id:ticket.id,has_earlier:false,customer_identity_preview:{name:'Synthetic customer',verified_phone:'+15550001111',phone_status:'verified'}});
  if(path===base+'/destinations')return ok({destinations:[{channel_id:'developer-channel',conversation_id:'developers@g.us',customer_name:'Developers',channel_name:'Support WhatsApp'}]});
  if(path===base+'/replies')return ok({auto_dispatches:state.autoDispatches,consultations:state.consultations,replies:state.replies});
  if(path===base&&options.method==='POST'){
   if(state.prepareError)return{ok:false,status:409,json:async()=>({success:false,error:state.prepareError})};
   state.consultations=[{id:'consult-a',case_id:ticket.id,request_id:body.request_id,source_channel:ticket.channel_id,source_conversation:ticket.conversation_id,destination_channel:body.destination_channel_id,destination_conversation:body.destination_conversation_id,recipient:'Developers',reason:body.summary,state:'ready',can_dispatch:true,can_send:true,can_manual_reply:true,created_at:'2026-11-01T13:00:00Z',expires_at:'2099-11-08T13:00:00Z'}];
   if(state.losePrepareResponse)throw Error('Network response lost');
   return ok({id:'consult-a',request_id:body.request_id,state:'ready'});
  }
  if(path===base+'/consult-a/dispatch'){state.consultations[0].state=state.dispatchState;state.consultations[0].can_dispatch=false;return ok({state:state.dispatchState});}
  if(path===base+'/consult-a/manual-reply'){state.replies.push({id:'manual-reply',consultation_id:'consult-a',body:body.response,author:'Owner',source_kind:'manual',draft_state:'not_requested',can_draft:true,happened_at:'2026-11-01T14:00:00Z'});return ok({id:'manual-reply'});}
  if(path===base+'/replies/provider-reply/draft'){state.replies[0].draft='Customer-ready private draft';state.replies[0].draft_state='completed';return ok({});}
  if(path==='/api/platform/ai/internal-consultations'&&options.method!=='POST')return ok({consultations:[{id:'old-consult',recipient_id:11,recipient_name:'Developer employee',response:'Preserved old answer',status:'answered',reason:'Old request'}]});
  if(path==='/api/platform/ai/internal-consultations/old-consult/reply')return ok({});
  if(path.endsWith('/public-shares'))return ok([]);
  return ok({});
 };
 w.eval(source);const controller=w.tenantCaseConsultations.mount({host:d.querySelector('#host'),ticket:{...ticket,agent_id:agent?ticket.agent_id:null},member,getMessages:()=>messages,isCurrent:()=>true,notice:value=>d.querySelector('#notice').textContent=value});
 await settle();return{dom,w,d,calls,state,controller,close(){controller.dispose();dom.window.close();}};
}
async function form(ctx){ctx.d.querySelector('#consultCreate').click();await settle();return ctx.d.querySelector('#caseDeveloperForm');}
function fill(ctx,node){const summary=node.querySelector('#consultReason');summary.value='Printer cannot connect';node.querySelector('#caseConsultFirst').value='message-0';node.querySelector('#caseConsultLast').value='message-2';node.dispatchEvent(new ctx.w.Event('change',{bubbles:true}));for(const toggle of node.querySelectorAll('[data-case-media-check]'))if(toggle.getAttribute('aria-pressed')!=='true')toggle.click();summary.dispatchEvent(new ctx.w.Event('input',{bubbles:true}));}
(async()=>{
 // Persisted sent is a provider submission receipt, never proof of delivery.
 for(const [language,label] of Object.entries({ru:'Передано провайдеру; доставка не подтверждена.',az:'Provayderə ötürülüb; çatdırılma təsdiqlənməyib.',en:'Submitted to provider; delivery unconfirmed.'})){
  const receipt=await setup({language});
  receipt.state.consultations=[{id:'legacy-sent',state:'sent',recipient:'Developer',created_at:'2026-11-01',can_send:false}];
  receipt.state.autoDispatches=[{id:'auto-sent',state:'sent',recipient:'Developer'}];
  receipt.controller.refreshMaterials();await settle();
  assert.equal(receipt.d.querySelector('#consultRows').textContent.split(label).length-1,2,language+' manual and automatic receipts distinguish submission from delivery');
  assert.equal(receipt.calls.filter(call=>call.options.method==='POST').length,0,'rendering legacy receipts never retries or mutates history');
  receipt.close();
 }
 const ctx=await setup();
 assert.equal(ctx.calls.filter(call=>call.options.method==='POST').length,0,'reading does not send or run AI');
 const request=await form(ctx);assert.ok(request);assert.equal(request.querySelectorAll('#consultRecipient').length,1,'one Developer recipient');assert.equal(request.querySelector('#caseConsultIdentityConsent'),null,'public identity control is removed');assert.match(request.textContent,/имена отправителей и клиента.*7 дней/);
 assert.equal(request.querySelector('#caseConsultFirst').value,'message-2','a public fragment starts with only the last message selected');
 assert.equal(request.querySelectorAll('input[type=checkbox]').length,0,'no redundant checkboxes');assert.equal(request.querySelectorAll('[data-case-media-check]').length,0,'no file selection controls');
 assert.equal(request.querySelector('#caseConsultRecipientConfirm'),null,'Send is the explicit confirmation');assert.equal(request.querySelector('#caseConsultSend').disabled,true,'preselected or single recipient cannot send without explicit confirmation');
 fill(ctx,request);assert.equal(request.querySelector('[data-case-preview-text]'),null);assert.equal(request.querySelector('.case-consult-preview'),null);request.dispatchEvent(new ctx.w.Event('submit',{cancelable:true}));await settle();assert.equal(ctx.calls.filter(call=>call.path===base&&call.options.method==='POST').length,0,'implicit submit does not send');assert.match(request.querySelector('[data-case-send-recipient]').textContent,/Developers/);assert.equal(ctx.calls.filter(call=>call.path===base&&call.options.method==='POST').length,0,'editing never sends');request.dispatchEvent(new ctx.w.SubmitEvent('submit',{cancelable:true,submitter:request.querySelector('#caseConsultSend')}));request.dispatchEvent(new ctx.w.SubmitEvent('submit',{cancelable:true,submitter:request.querySelector('#caseConsultSend')}));await settle();
 const prepared=ctx.calls.filter(call=>call.path===base&&call.options.method==='POST'),sent=ctx.calls.filter(call=>call.path.endsWith('/dispatch'));
 assert.equal(prepared.length,1);assert.equal(sent.length,1);assert.equal(prepared[0].body.public_dialog_version,1);assert.equal(Object.keys(prepared[0].body.public_sender_versions).length,3);assert.equal(prepared[0].body.case_id,ticket.id);assert.deepEqual(prepared[0].body.message_ids,['message-0','message-1','message-2']);assert.deepEqual(prepared[0].body.media_ids,['message-1','message-2']);assert.equal(prepared[0].body.request_id,sent[0].body.request_id);assert.equal(prepared[0].body.include_customer_identity,false);
 assert.match(ctx.d.querySelector('#consultRows').textContent,/принял запрос в очередь/);assert.equal(ctx.d.querySelector('#consultCreate').disabled,true);assert.equal(ctx.d.querySelector('textarea'),null,'no manual answer field until explicitly opened');
 assert.equal(ctx.calls.some(call=>/attachment|public-share/.test(call.path)),false,'sending uses the bounded snapshot service and does not load media');
 ctx.state.consultations[0].state='sent';ctx.state.replies=[{id:'provider-reply',consultation_id:'consult-a',body:'Replace the cable',author:'Developer',source_kind:'provider',draft_state:'not_requested',can_draft:true,happened_at:'2026-11-01T13:30:00Z',url:'/app?view=dialogs&open_dialog=1&lead_id=41'}];
 [...ctx.d.querySelectorAll('.support-heading button')].find(node=>node.textContent==='Обновить').click();await settle();
 assert.match(ctx.d.querySelector('#consultRows').textContent,/Replace the cable/);assert.equal(ctx.calls.some(call=>call.path.endsWith('/draft')),false,'a late response never starts paid AI in the case UI');
 [...ctx.d.querySelectorAll('#consultRows button')].find(node=>node.textContent==='Подготовить AI-черновик клиенту').click();await settle();assert.match(ctx.d.querySelector('#consultRows').textContent,/Customer-ready private draft/);
 [...ctx.d.querySelectorAll('#consultRows button')].find(node=>node.textContent==='Добавить ответ вручную').click();const manual=ctx.d.querySelector('textarea[data-consult-answer]');manual.value='Developer called: restart the printer';manual.closest('form').dispatchEvent(new ctx.w.Event('submit',{cancelable:true}));await settle();assert.match(ctx.d.querySelector('#consultRows').textContent,/Ответ записан сотрудником/);
 assert.equal(ctx.calls.some(call=>/messages\/send|dialogs\/send|support-tickets/.test(call.path)),false,'the case consultation never sends to the customer or creates a ticket');ctx.close();

 const lost=await setup();lost.state.losePrepareResponse=true;const lostForm=await form(lost);fill(lost,lostForm);lostForm.dispatchEvent(new lost.w.SubmitEvent('submit',{cancelable:true,submitter:lostForm.querySelector('#caseConsultSend')}));await settle();
 assert.match(lost.d.querySelector('#consultRows').textContent,/Запрос подготовлен/);assert.equal(lost.calls.filter(call=>call.path.endsWith('/dispatch')).length,0,'lost prepare response does not silently dispatch');
 [...lost.d.querySelectorAll('#consultRows button')].find(node=>node.textContent==='Отправить подготовленный запрос').click();await settle();assert.equal(lost.calls.filter(call=>call.path.endsWith('/dispatch')).length,1);assert.equal(lost.calls.filter(call=>call.path===base&&call.options.method==='POST').length,1,'recovery reuses the saved request');lost.close();

 const denied=await setup();denied.state.prepareError='Recipient is no longer marked Developer';const deniedForm=await form(denied);fill(denied,deniedForm);deniedForm.dispatchEvent(new denied.w.SubmitEvent('submit',{cancelable:true,submitter:deniedForm.querySelector('#caseConsultSend')}));await settle();assert.match(deniedForm.textContent,/Recipient is no longer marked Developer/);assert.equal(deniedForm.querySelector('#consultReason').value,'Printer cannot connect');denied.close();

 const statuses=await setup();statuses.state.autoDispatches=[{id:'a',recipient:'Developer A',state:'reply_received'},{id:'b',recipient:'Developer B',state:'blocked',reason:'developer_route_blocked'},{id:'c',recipient:'Developer C',state:'blocked',reason:'automatic_dispatch_runtime_or_agent_scope_changed'},{id:'d',recipient:'Developer D',state:'blocked',reason:'source_dialog_state_or_assignment_changed'},{id:'e',recipient:'Developer E',state:'blocked',reason:'future_private_reason'}];[...statuses.d.querySelectorAll('.support-heading button')].find(button=>button.textContent==='Обновить').click();await settle();assert.match(statuses.d.querySelector('[data-auto-dispatch-recipient=a]').textContent,/Developer A.*ответ получен/);assert.match(statuses.d.querySelector('[data-auto-dispatch-recipient=b]').textContent,/Маршрут к разработчику недоступен/);assert.match(statuses.d.querySelector('[data-auto-dispatch-recipient=c]').textContent,/область доступа/);assert.match(statuses.d.querySelector('[data-auto-dispatch-recipient=d]').textContent,/Состояние диалога/);assert.doesNotMatch(statuses.d.querySelector('#consultRows').textContent,/developer_route_blocked|future_private_reason/);statuses.close();

 const legacy=await setup({dialogs:false});assert.match(legacy.d.querySelector('#consultRows').textContent,/Preserved old answer/);assert.equal(legacy.d.querySelector('#consultCreate').hidden,true);assert.equal(legacy.calls.some(call=>call.path===base+'/replies'),false,'support-only staff retain their previous internal-request access');legacy.close();
 console.log('PASS: one case developer request, explicit bounded files, pending receipts, late answers, manual records, recovery and legacy rights');
})().catch(error=>{console.error(error);process.exitCode=1;});
