const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const source=fs.readFileSync(path.join(__dirname,'../docs/tenant-support-inbox.js'),'utf8');
const ticket={id:'ticket-1',contact_name:'<img src=x onerror=alert(1)>',channel_name:'Night WhatsApp',conversation_id:'client',lead_id:41,description:'Нет связи',summary:'Проверить линию',summary_state:'completed',status:'waiting',source:'ai_handoff',reason:'Не помогла перезагрузка',created_at:'2026-10-16T00:00:00Z',last_message_at:'2026-10-16T01:00:00Z'};
const profile={tenant_id:'company-a',telegram_id:11,role:'owner'};
const members=[{telegram_id:11,display_name:'Support'}];
const response=value=>({ok:true,json:async()=>({success:true,result:value})});
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));};
function setup(fetch,url='https://crm.test/app',rights=true){
 const dom=new JSDOM('<section id="view-support"><div id="operatorSupport"></div></section><div id="modalContent"></div>',{url,runScripts:'outside-only'});
 dom.window.HTMLElement.prototype.scrollIntoView=function(){};
 dom.window.fetch=fetch;dom.window.tenantOperator={hasRight:typeof rights==='function'?rights:()=>rights};dom.window.eval(fs.readFileSync(path.join(__dirname,'../docs/tenant-operator-i18n.js'),'utf8'));dom.window.eval(fs.readFileSync(path.join(__dirname,'../docs/tenant-support-consultations.js'),'utf8'));dom.window.eval(source);return dom;
}
(async()=>{
 const calls=[];
 const dom=setup(async(url,init={})=>{
   calls.push({url,init});
   if(url==='/api/platform/me')return{ok:true,json:async()=>({member:profile})};
   if(url.includes('?'))return response({tickets:[ticket],members,next_offset:null});
   const body=init.body?JSON.parse(init.body):{};
   if(init.method==='PATCH')Object.assign(ticket,body);
   return response({ticket,expert_results:[{id:'internal-expert',expert_name:'Expert <img src=x>',model:'approved',status:'completed',result:{cause:'Требуется проверка линии',evidence:['API не подключён'],confidence:'low',safe_actions:['Уточнить код ошибки'],additional_information:'Версия приложения',requires_human:true,ticket_summary:'Проблема не подтверждена'}}],messages:[{id:'message-1',direction:'incoming',body:'Не работает интернет',message_type:'text'}],next_before:null});
 });
 await settle();const doc=dom.window.document;
 assert.match(doc.querySelector('#supportRows').textContent,/Нет связи/);
 assert.equal(doc.querySelector('#supportRows img'),null,'client names must be escaped');
 doc.querySelector('[data-support-id]').click();await settle();
 assert.match(doc.querySelector('#supportHistory').textContent,/Не работает интернет/);
 assert.match(doc.querySelector('[data-expert-diagnostics]').textContent,/Диагностика завершена/);
 assert.match(doc.querySelector('[data-expert-diagnostics]').textContent,/Требуется проверка линии/);
 assert.equal(doc.querySelector('[data-expert-diagnostics] img'),null);
 assert.equal(calls.filter(x=>x.init.method&&x.init.method!=='GET').length,0,'viewing must be read-only');
 assert.equal(doc.querySelectorAll('#consultCreate').length,1,'one developer request entry point');
 assert.equal(doc.querySelector('#consultRecipient'),null,'recipient selection opens only when requested');
 assert.equal(doc.querySelector('#publicCaseShare'),null,'no duplicate primary public-link form');
 assert.equal(doc.querySelector('[data-consult-answer]'),null,'manual developer answer is not an unexplained initial field');
 let opened=null;doc.addEventListener('tenant-support-open',e=>opened=e.detail.lead_id);
 doc.querySelector('#supportOpenChat').click();assert.equal(opened,41);
 doc.querySelector('[data-support-id]').click();await settle();
 doc.querySelector('#supportTake').click();await settle();
 const take=calls.find(x=>x.init.method==='PATCH');const data=JSON.parse(take.init.body);
 assert.equal(data.assigned_to,11);assert.equal(data.status,'in_progress');assert.equal(data.expected_tenant_id,'company-a');
 doc.querySelector('#supportStatus').value='waiting';doc.querySelector('#supportStatus').dispatchEvent(new dom.window.Event('change'));await settle();
 assert.ok(calls.some(x=>x.url.includes('status=waiting')));
 doc.dispatchEvent(new dom.window.CustomEvent('tenant-chat-open',{detail:{lead_id:41}}));
 doc.querySelector('#supportCreateFromChat').click();await settle();
 const creation=calls.find(x=>x.url==='/api/platform/ai/support-tickets'&&x.init.method==='POST');assert.equal(JSON.parse(creation.init.body).lead_id,41);assert.ok(JSON.parse(creation.init.body).request_id);
 assert.ok(!calls.some(x=>/handoff|resume|pause/.test(x.url)),'queue must not change AI state');
 dom.window.close();
 // A delayed response for company A must never be rendered after switching to company B.
 let release;let company='a';
 const stale=setup(async(url)=>{
   if(url==='/api/platform/me')return{ok:true,json:async()=>({member:profile})};
   if(company==='a')return new Promise(resolve=>{release=()=>resolve(response({tickets:[ticket],members,next_offset:null}));});
   return response({tickets:[],members:[],next_offset:null});
 });
 await settle();company='b';
 stale.window.document.dispatchEvent(new stale.window.CustomEvent('tenant-profile',{detail:{member:{tenant_id:'company-b',telegram_id:22,role:'owner'}}}));await settle();
 release();await settle();assert.ok(!stale.window.document.querySelector('#supportRows').textContent.includes('Нет связи'));assert.equal(stale.window.document.querySelector('#supportDetail').hidden,true);
 stale.window.close();
 // An authenticated recipient can open one exact ticket without a shared
 // actor-specific attachment URL or a list search.
 const id='a20d59f3-821b-4fcf-a37e-528252d126b5',deepCalls=[];
 const deep=setup(async(url,init={})=>{
   deepCalls.push({url,init});
   if(url==='/api/platform/me')return{ok:true,json:async()=>({member:profile})};
   if(url.startsWith('/api/platform/ai/internal-consultations'))return response({consultations:[]});
   if(url==='/api/platform/ai/support-tickets/'+id)return response({ticket:{...ticket,id},messages:[],expert_results:[],next_before:null});
   return response({tickets:[],members,next_offset:null});
 },'https://crm.test/app?view=support&support_ticket_id='+id);
 await settle();
 assert.equal(deepCalls.filter(x=>x.url==='/api/platform/ai/support-tickets/'+id).length,1);
 assert.match(deep.window.document.querySelector('#supportDetail').textContent,/Нет связи/);
 assert.equal(deepCalls.filter(x=>x.init.method&&x.init.method!=='GET').length,0);
 deep.window.close();
 const messageId='93f466f9-767b-4f02-8005-1842cbdbef27',focusCalls=[];
 const focused=setup(async(url,init={})=>{
   focusCalls.push({url,init});
   if(url==='/api/platform/me')return{ok:true,json:async()=>({member:profile})};
   if(url.startsWith('/api/platform/ai/internal-consultations'))return response({consultations:[]});
   if(url==='/api/platform/ai/support-tickets/'+id+'?message_id='+messageId)
     return response({ticket:{...ticket,id,channel_id:'channel-a'},messages:[],expert_results:[],focus_message:{id:messageId,external_id:'connect:synthetic',message_type:'image',media_evidence_context:'<img src=x onerror=alert(1)>'},next_before:null});
   return response({tickets:[],members,next_offset:null});
 },'https://crm.test/app?view=support&support_ticket_id='+id+'&support_ticket_message_id='+messageId);
 let attachmentUrl='';focused.window.tenantDownloadAttachment=(_button,url)=>{attachmentUrl=url;};
 await settle();focused.window.document.querySelector('[data-focused-attachment] button').click();
 assert.equal(focused.window.document.querySelector('[data-focused-attachment] img'),null);
 assert.match(focused.window.document.querySelector('[data-focused-attachment] details').textContent,/img src/);
 assert.match(focused.window.document.querySelector('[data-focused-attachment] details').textContent,/Текущая версия файла не проверялась/);
 assert.match(attachmentUrl,/expected_user_id=11/);assert.match(attachmentUrl,/message_id=connect%3Asynthetic/);
 assert.equal(focusCalls.filter(x=>x.init.method&&x.init.method!=='GET').length,0);focused.window.close();
 const noDialogs=setup(async(url)=>{
   if(url==='/api/platform/me')return{ok:true,json:async()=>({member:profile})};
   if(url.startsWith('/api/platform/ai/internal-consultations'))return response({consultations:[]});
   if(url.startsWith('/api/platform/ai/support-tickets/'+id+'?message_id='))return response({ticket:{...ticket,id,channel_id:'channel-a'},messages:[],expert_results:[],focus_message:{id:messageId,external_id:'connect:synthetic',message_type:'image'},next_before:null});
   return response({tickets:[],members,next_offset:null});
 },'https://crm.test/app?view=support&support_ticket_id='+id+'&support_ticket_message_id='+messageId,permission=>permission==='support');
 await settle();assert.equal(noDialogs.window.document.querySelector('[data-focused-attachment] button'),null);noDialogs.window.close();
 const deniedCalls=[];
 const denied=setup(async(url)=>{deniedCalls.push(url);return {ok:true,json:async()=>({member:profile})};},'https://crm.test/app?view=support&support_ticket_id='+id,false);
 await settle();assert.ok(!deniedCalls.some(url=>url.includes('/support-tickets')));denied.window.close();
 // The main card delegates one bounded request to the unified controller.
 const cid='4bfc7540-38e8-4e14-a571-057817c6ca6b',gid='120363411822115409@g.us',synthetic=[];let delivery=null;
 const base='/api/platform/ai/dialog-consultations';
 const caseMessages=[{id:'safe-text-id',message_type:'text',direction:'incoming',body:'Issue persists'},{id:messageId,message_type:'image',direction:'incoming',body:'Attachment',happened_at:'2026-10-16T00:00:00Z'}];
 const cycle=setup(async(url,init={})=>{
   const body=init.body?JSON.parse(init.body):{};synthetic.push({url,method:init.method||'GET',body});
   if(url==='/api/platform/me')return{ok:true,json:async()=>({member:profile})};
   if(url.startsWith(base+'/replies?'))return response({consultations:delivery?[delivery]:[],replies:[]});
   if(url===base+'/destinations')return response({destinations:[{channel_id:'developer-channel',conversation_id:gid,customer_name:'Developer group'}]});
   if(url===base+'/preview')return response({messages:caseMessages,has_earlier:false});
   if(url===base&&init.method==='POST'){delivery={id:cid,case_id:ticket.id,request_id:body.request_id,state:'ready',destination_channel:body.destination_channel_id,destination_conversation:body.destination_conversation_id,recipient:'Developer group',reason:body.summary,can_dispatch:true,can_manual_reply:true};return response({id:cid,state:'ready'});}
   if(url===base+'/'+cid+'/dispatch'){delivery.state='pending';delivery.can_dispatch=false;return response({state:'pending'});}
   if(url==='/api/platform/ai/support-tickets/ticket-1')return response({ticket:{...ticket,channel_id:'channel-a'},messages:caseMessages,expert_results:[],next_before:null});
   return response({tickets:[ticket],members,next_offset:null});
 });
 await settle();const cycleDoc=cycle.window.document;cycleDoc.querySelector('[data-support-id]').click();await settle();
 assert.equal(synthetic.some(call=>call.method==='POST'),false,'opening case is read-only');cycleDoc.querySelector('#consultCreate').click();await settle();
 const form=cycleDoc.querySelector('#caseDeveloperForm');assert.ok(form);assert.equal(form.querySelectorAll('#consultRecipient').length,1);assert.equal(form.querySelector('[data-consultation-confirm]'),null);
 form.querySelector('#consultReason').value='50 → 49,9992';form.querySelector('#caseConsultFirst').value='safe-text-id';form.querySelector('#caseConsultLast').value=messageId;form.querySelector('input[type=checkbox]').checked=true;form.dispatchEvent(new cycle.window.Event('input',{bubbles:true}));
 form.dispatchEvent(new cycle.window.Event('submit',{cancelable:true}));await settle();
 const prepared=synthetic.filter(call=>call.url===base&&call.method==='POST'),dispatched=synthetic.filter(call=>call.url.endsWith('/dispatch'));
 assert.equal(prepared.length,1);assert.equal(dispatched.length,1);assert.deepEqual(prepared[0].body.message_ids,['safe-text-id',messageId]);assert.deepEqual(prepared[0].body.media_ids,[messageId]);assert.equal(prepared[0].body.expected_tenant_id,'company-a');assert.equal(dispatched[0].body.request_id,prepared[0].body.request_id);
 assert.match(cycleDoc.querySelector('#consultRows').textContent,/принял запрос в очередь/);assert.equal(cycleDoc.querySelector('#publicCaseShare'),null);assert.equal(cycleDoc.querySelector('[data-consult-answer]'),null);
 assert.ok(!synthetic.some(call=>call.method==='POST'&&/internal-consultations|public-share|messages\/send|dialogs\/send/.test(call.url)),'one request creates its public snapshot through the unified service and never sends to the customer');cycle.window.close();
 const oldMediaId='afdf2239-babc-44cc-a703-3d1df394e099',historyCalls=[],attachCalls=[];
 const picker=setup(async(url,init={})=>{
   const body=init.body?JSON.parse(init.body):{};historyCalls.push({url,init,body});
   if(url==='/api/platform/me')return{ok:true,json:async()=>({member:profile})};
   if(url.startsWith('/api/platform/ai/internal-consultations'))return response({consultations:[]});
   if(url==='/api/platform/ai/support-tickets/ticket-1')return response({ticket:{...ticket,channel_id:'channel-a'},messages:[{id:'case-text',direction:'incoming',body:'Ticket message',message_type:'text'}],attached_media:[],expert_results:[],next_before:null});
   if(url.startsWith('/api/platform/ai/support-tickets/ticket-1/messages')&&init.method!=='POST')return response(url.includes('before=')?{messages:[{id:oldMediaId,external_id:'connect:old-photo',message_type:'image',direction:'incoming',created_at:'2026-09-01T00:00:00Z'}],next_before:null}:{messages:Array.from({length:50},(_,i)=>({id:`00000000-0000-4000-8000-${String(i+1).padStart(12,'0')}`,message_type:'image',direction:'incoming'})),next_before:'history-49'});
   if(url==='/api/platform/ai/support-tickets/ticket-1/messages'&&init.method==='POST'){attachCalls.push({url,init,body});return response({ticket_id:'ticket-1',attached_message_ids:[oldMediaId],attached_count:1});}
   if(url==='/api/platform/support-cases/ticket-1/public-shares')return response([]);
   return response({tickets:[ticket],members,next_offset:null});
 },'https://crm.test/app?view=support&support_ticket_id=ticket-1');
 let downloaded='';picker.window.tenantDownloadAttachment=(_button,url)=>{downloaded=url;};await settle();const pickerDoc=picker.window.document;
 pickerDoc.querySelector('[data-support-id]').click();await settle();
 assert.equal(pickerDoc.querySelector('#supportDetail').getAttribute('role'),'dialog');assert.equal(pickerDoc.querySelector('#supportDetail').getAttribute('aria-modal'),'true');assert.equal(pickerDoc.querySelector('#supportDetail').parentElement,pickerDoc.body);assert.ok(pickerDoc.querySelector('#supportRows'),'the ticket modal leaves the filtered list mounted');assert.ok(pickerDoc.querySelector('#supportExistingMedia'),pickerDoc.querySelector('#supportDetail').textContent);assert.ok(pickerDoc.querySelector('#caseMediaLoad'));
 pickerDoc.querySelector('#caseMediaLoad').click();await settle();assert.equal(pickerDoc.querySelectorAll('#supportExistingMedia input').length,50);assert.equal(pickerDoc.querySelector(`#supportExistingMedia input[value="${oldMediaId}"]`),null);
 pickerDoc.querySelector('#caseMediaEarlier').click();await settle();
 const oldCheck=pickerDoc.querySelector(`#supportExistingMedia input[value="${oldMediaId}"]`);assert.ok(oldCheck,'media older than the first 50 history rows is selectable');
 oldCheck.checked=true;oldCheck.dispatchEvent(new picker.window.Event('change'));
 const attachButton=pickerDoc.querySelector('#supportExistingMedia [data-case-media-attach]');
 assert.ok(attachButton);const beforeAttach=historyCalls.length;attachButton.click();await settle();
 assert.equal(attachCalls.length,1);assert.deepEqual(attachCalls[0].body.message_ids,[oldMediaId]);assert.deepEqual(historyCalls.filter(call=>call.init.method==='POST').map(call=>call.url),['/api/platform/ai/support-tickets/ticket-1/messages'],'attach makes no consultation, share, model or delivery call');
 assert.ok(!historyCalls.some(call=>/\/dialogs\/send|group$|public-share/.test(call.url)&&call.init.method==='POST'));
 const openButton=pickerDoc.querySelector('#supportExistingMedia [data-case-media-open]');
 assert.ok(openButton);openButton.click();assert.match(downloaded,/conversation_id=client/);assert.equal(attachCalls.length,1);picker.window.close();

 const ticketA='10000000-0000-4000-8000-000000000001',ticketB='20000000-0000-4000-8000-000000000002',pendingCalls=[];let finishAttach;
 const raced=setup(async(url,init={})=>{
   const body=init.body?JSON.parse(init.body):{};pendingCalls.push({url,init,body});
   if(url==='/api/platform/me')return{ok:true,json:async()=>({member:profile})};
   if(url.startsWith('/api/platform/ai/internal-consultations'))return response({consultations:[]});
   if(url==='/api/platform/ai/support-tickets?status=unresolved&assignee=&search=&channel_id=&offset=0')return response({tickets:[{...ticket,id:ticketA,contact_name:'Case A'},{...ticket,id:ticketB,contact_name:'Case B'}],members,next_offset:null});
   if(url==='/api/platform/ai/support-tickets/'+ticketA)return response({ticket:{...ticket,id:ticketA,contact_name:'Case A',channel_id:'channel-a'},messages:[],attached_media:[],expert_results:[],next_before:null});
   if(url==='/api/platform/ai/support-tickets/'+ticketB)return response({ticket:{...ticket,id:ticketB,contact_name:'Case B',channel_id:'channel-a'},messages:[],attached_media:[],expert_results:[],next_before:null});
   if(url==='/api/platform/ai/support-tickets/'+ticketA+'/messages'&&init.method==='POST')return new Promise(resolve=>{finishAttach=()=>resolve(response({ticket_id:ticketA,attached_message_ids:[oldMediaId],attached_count:1}));});
   if(url==='/api/platform/ai/support-tickets/'+ticketA+'/messages')return response({messages:[{id:oldMediaId,external_id:'connect:old-photo',message_type:'image',direction:'incoming'}],next_before:null});
   return response({tickets:[],members,next_offset:null});
 });
 await settle();const raceDoc=raced.window.document;raceDoc.querySelector(`[data-support-id="${ticketA}"]`).click();await settle();raceDoc.querySelector('#caseMediaLoad').click();await settle();
 const raceCheck=raceDoc.querySelector('#supportExistingMedia input');assert.ok(raceCheck);raceCheck.checked=true;raceCheck.dispatchEvent(new raced.window.Event('change'));raceDoc.querySelector('[data-case-media-attach]').click();await settle();assert.equal(typeof finishAttach,'function');
 raceDoc.querySelector(`[data-support-id="${ticketB}"]`).click();await settle();assert.equal(raceDoc.querySelector('#supportTicketTitle').textContent,'Case B');finishAttach();await settle();
 assert.equal(raceDoc.querySelector('#supportTicketTitle').textContent,'Case B');assert.equal(pendingCalls.filter(x=>x.url==='/api/platform/ai/dialog-consultations/replies?case_id='+ticketA).length,1,'late attach completion must not redraw A consultation into B');raced.window.close();

 console.log('PASS: queue, identity, deep links, unified bounded developer request, media picker and late attachment isolation.');
})().catch(error=>{console.error(error);process.exitCode=1;});
