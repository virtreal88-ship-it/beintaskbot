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
 dom.window.fetch=fetch;dom.window.tenantOperator={hasRight:typeof rights==='function'?rights:()=>rights};dom.window.eval(fs.readFileSync(path.join(__dirname,'../docs/tenant-operator-i18n.js'),'utf8'));dom.window.eval(source);return dom;
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
 let opened=null;doc.addEventListener('tenant-support-open',e=>opened=e.detail.lead_id);
 doc.querySelector('#supportOpenChat').click();assert.equal(opened,41);
 assert.ok(doc.querySelector('#consultCreate'),'ticket detail has the internal developer consultation action');
 doc.querySelector('#consultReason').value='Проверить логи';doc.querySelector('#consultCreate').click();await settle();
 const consultation=calls.find(x=>x.url==='/api/platform/ai/internal-consultations'&&x.init.method==='POST');
 assert.equal(JSON.parse(consultation.init.body).case_id,'ticket-1');assert.equal(JSON.parse(consultation.init.body).expected_tenant_id,'company-a');
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
 const cid='4bfc7540-38e8-4e14-a571-057817c6ca6b',gid='120363411822115409@g.us',synthetic=[];let delivery=null;
 const cycle=setup(async(url,init={})=>{
   const body=init.body?JSON.parse(init.body):{};synthetic.push({url,method:init.method||'GET',body});
   if(url==='/api/platform/me')return{ok:true,json:async()=>({member:profile})};
   if(url==='/api/platform/ai/internal-consultations?case_id=ticket-1')return response({consultations:[{id:cid,reason:'50 → 49,9992',created_at:'2026-10-16T00:00:00Z',status:'waiting_specialist',recipient_name:'Developer'}]});
   if(url==='/api/platform/ai/internal-consultations/groups')return response({groups:[{channel_id:'channel-a',conversation_id:gid,contact_name:'Test group'}]});
   if(url==='/api/platform/ai/internal-consultations/'+cid+'/group'){
     if(init.method==='POST'&&body.action==='prepare'){delivery={state:'ready',request_id:body.request_id,destination:'Test group',prepared_text:body.summary,replies:[],customer_draft:''};return response({state:'ready'});}
     if(init.method==='POST'&&body.action==='send'){delivery.state='sent';delivery.replies=[{message_id:'reply-1',body:'We are investigating'}];return response({state:'sent'});}
     if(init.method==='POST'&&body.action==='draft'){delivery.draft_reply_id=body.message_id;delivery.customer_draft=body.draft;return response({customer_message_sent:false});}
     if(delivery)return response(delivery);
     return {ok:false,status:404,json:async()=>({success:false,error:'Not prepared'})};
   }
   if(url==='/api/platform/ai/support-tickets/ticket-1')return response({ticket,messages:[{id:messageId,message_type:'image',direction:'incoming',happened_at:'2026-10-16T00:00:00Z'}],expert_results:[],next_before:null});
   return response({tickets:[ticket],members,next_offset:null});
 });
 await settle();cycle.window.document.querySelector('[data-support-id]').click();await settle();
 const panel=cycle.window.document.querySelector('[data-consult-id] details');panel.open=true;panel.dispatchEvent(new cycle.window.Event('toggle'));await settle();
 panel.querySelector('select').value='0';panel.querySelector('textarea').value='50 → 49,9992';panel.querySelector('input[type=checkbox]').checked=true;panel.querySelector('button').click();await settle();
 assert.equal(synthetic.filter(x=>x.body.action==='prepare').length,1);assert.equal(synthetic.filter(x=>x.body.action==='send').length,0);
 assert.deepEqual(synthetic.find(x=>x.body.action==='prepare').body.attachment_ids,[messageId]);
 assert.match(panel.textContent,/Test group/);panel.querySelector('button').click();await settle();
 assert.equal(synthetic.filter(x=>x.body.action==='send').length,1);assert.match(panel.textContent,/We are investigating/);
 panel.querySelector('textarea').value='We are checking.';panel.querySelector('[data-group-draft]').click();await settle();
 assert.equal(delivery.customer_draft,'We are checking.',JSON.stringify(synthetic));assert.ok(!synthetic.some(x=>/\/dialogs\/send/.test(x.url)));cycle.window.close();
 console.log('PASS: inbox renders safely, opens history read-only, filters, assigns, creates manually and rejects stale tenant responses.');
})().catch(error=>{console.error(error);process.exitCode=1;});
