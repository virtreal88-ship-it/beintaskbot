const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const fs=require('node:fs'),assert=require('node:assert/strict');
const html=fs.readFileSync('docs/tenant-app.html','utf8');
const js=fs.readFileSync('docs/tenant-knowledge-candidates.js','utf8');
const dom=new JSDOM('<div id="supportCases"></div><span id="supportCasesPending"></span><nav id="supportCasesNav"></nav><nav class="nav"><button data-view="dialogs"></button></nav>',{url:'https://example.test/app',runScripts:'outside-only'});
const w=dom.window,d=w.document,calls=[];let historyPage=0;
w.tenantI18n={language:'az'};
w.tenantProfileSnapshot={member:{tenant_id:'tenant-a',telegram_id:7,role:'owner'}};
const card={id:'case-1',version:3,title:'Axtarış nəticəsi haqqında real case title',status:'pending_review',case_kind:'technical',resolution_method:'chat',content_language:'en',symptoms:['Customer sees unrelated records'],operator_actions:['Employee checked search filters'],resolution_steps:['Refresh the search index'],cause:{status:'suspected',text:'The index may be stale'},outcome:{status:'unconfirmed',text:'Waiting for customer confirmation',message_ids:[]},source_message_ids:['message-2'],can_approve:true,approval_block_reason:null,created_at:'2026-10-17T10:12:00Z'};
const route={provider:'connect',channel_id:'channel-a',conversation_id:'12345@g.us',phone:'+994501234567'};
const messages=[
 {id:'message-1',direction:'incoming',author_type:'customer',author_name:'Mamed Saray',body:'Real incoming text',message_type:'text',happened_at:'2026-10-17T10:12:00Z',source:'connect'},
 {id:'message-2',direction:'outgoing',author_type:'operator',author_name:'Leyla Hasanова',body:'Real employee answer',message_type:'text',happened_at:'2026-10-17T10:15:00Z',source:'connect'},
 {id:'message-3',external_id:'provider-image-3',source_lead_id:18,source_channel_id:'channel-b',channel:'connect:channel-b',conversation_id:'12345@g.us',direction:'incoming',author_type:'participant',author_name:'Mamed Saray',body:'',message_type:'image',media_filename:'photo.png',happened_at:'2026-10-17T10:16:00Z',source:'connect'},
 {id:'message-4',external_id:'provider-audio-4',channel:'connect:channel-a',conversation_id:'12345@g.us',direction:'incoming',author_type:'participant',author_name:'Mamed Saray',body:'Routeri yenidən başladım',message_type:'voice',media_filename:'voice.ogg',has_transcription:true,happened_at:'2026-10-17T10:17:00Z',source:'connect'},
 {id:'message-5',external_id:'provider-video-5',source_lead_id:17,source_channel_id:'channel-a',channel:'connect:channel-a',conversation_id:'12345@g.us',direction:'incoming',author_type:'participant',author_name:'Mamed Saray',body:'',message_type:'video',media_filename:'clip.mp4',happened_at:'2026-10-17T10:18:00Z',source:'connect'},
 {id:'message-6',external_id:'provider-doc-6',source_lead_id:17,source_channel_id:'channel-a',channel:'connect:channel-a',conversation_id:'12345@g.us',direction:'incoming',author_type:'participant',author_name:'Mamed Saray',body:'',message_type:'document',media_filename:'manual.pdf',happened_at:'2026-10-17T10:19:00Z',source:'connect'},
 {id:'message-7',external_id:'provider-missing-7',source_lead_id:17,source_channel_id:'channel-a',channel:'connect:channel-a',conversation_id:'12345@g.us',direction:'incoming',author_type:'participant',author_name:'Mamed Saray',body:'',message_type:'document',media_filename:'missing.pdf',happened_at:'2026-10-17T10:20:00Z',source:'connect'}
];
w.URL.createObjectURL=()=> 'blob:test-image';w.URL.revokeObjectURL=()=>{};
w.fetch=async(url,opt={})=>{
 calls.push({url,opt});let result={};
 if(url.includes('/ai/dialogs/history')){
   const q=new URL(url,'https://example.test').searchParams;
   assert.equal(q.get('provider'),'connect');assert.equal(q.get('lead_id'),'17','full history route derives its lead from same-channel evidence');assert.equal(q.get('channel_id'),'channel-a');assert.equal(q.get('conversation_id'),route.conversation_id);
   historyPage++;
   result=historyPage===1?{dialog:route,messages,next_before:'older-cursor'}:{dialog:route,messages:[{id:'message-0',direction:'incoming',author_type:'customer',author_name:'Mamed Saray',body:'Older real text',message_type:'text',happened_at:'2026-10-17T10:00:00Z',source:'connect'}],next_before:null};
 }else if(url.includes('/chat/attachment')){const q=new URL(url,'https://example.test').searchParams,id=q.get('message_id');if(id==='provider-missing-7')return {ok:false,status:404,json:async()=>({error:'Fayl tapılmadı.'})};const mime={'provider-image-3':'image/jpeg','provider-audio-4':'audio/ogg','provider-video-5':'video/mp4','provider-doc-6':'application/pdf'}[id]||'application/octet-stream';return {ok:true,status:200,blob:async()=>new w.Blob(['test media bytes'],{type:mime}),headers:{get:()=>mime}};}
 else if(url.includes('/ai/dialogs'))result={dialogs:[]};
 else if(url.includes('/ai/agents'))result={agents:[{id:'agent-a',name:'Nizam AI'}]};
 else if(url.includes('/ai/channels'))result={channels:[{provider:'connect',channel_id:'channel-a',name:'Texniki Dəstək'},{provider:'connect',channel_id:'channel-b',name:'Satış'}]};
 else if(url.includes('/knowledge-candidates/case-1?'))result={candidate:card,evidence:[{...messages[1],role:'operator',text:messages[1].body,provider:'connect',lead_id:17,channel_id:'channel-a',conversation_id:route.conversation_id}],route};
 else if(url.includes('/knowledge-candidates?'))result={candidates:[card],next_before:null};
 else if(url.endsWith('/knowledge-candidates/case-1')&&opt.method==='PATCH'){
   const body=JSON.parse(opt.body);assert.equal(body.title,'Dəyişdirilmiş başlıq EN');assert.deepEqual(body.symptoms,['Symptom in English']);assert.deepEqual(body.source_message_ids,['message-2']);assert.deepEqual(body.outcome.message_ids,['message-2']);assert.deepEqual(body.cause_evidence,[]);result={candidate:{...card,...body,version:4},evidence:[{...messages[1],role:'operator',text:messages[1].body}],route};
 }else result={};
 return {ok:true,status:200,json:async()=>({success:true,result})};
};
const tick=()=>new Promise(resolve=>setTimeout(resolve,75));
(async()=>{
 w.eval(js);await tick();
 assert(d.querySelector('#casesSearch'),'case list starts with a compact search field');
 assert.equal(d.querySelector('#casesStatus').value,'','rejected cases stay hidden by default');
 assert(d.querySelector('.support-cases-advanced'),'less-frequent analysis controls stay visually secondary');
 d.querySelector('[data-case-open="case-1"]').click();await tick();await tick();
 const modal=d.getElementById('supportCaseModal');assert(modal,'case modal opens from actual list');
 assert(modal.querySelector('.case-header-participants'),'customer and employee are shown in the modal header');
 assert.equal(modal.querySelector('.case-review-column .case-participants'),null,'the right column is reserved for the conversation');
 assert(modal.querySelector('.case-content-column .case-boundary-editor'),'case boundaries stay outside the conversation column');
 assert.equal(modal.querySelector('#caseTitle').value,card.title);assert.equal(modal.querySelector('.case-review-status select'),null,'review status is informational');
 assert.equal(modal.querySelectorAll('.case-meta-strip select').length,1,'only result confirmation stays in the main flow');assert.equal(modal.querySelectorAll('[data-case-tab]').length,6,'the compact case navigation mirrors the employee review layout');assert(modal.querySelector('.case-tech-details #caseKind'));assert(modal.querySelector('.case-tech-details #caseMethod'));assert(modal.querySelector('#caseCauseEvidence'),'cause evidence is selected in the compact additional section');
 assert.equal(modal.querySelector('[data-case-status]').textContent,'Yoxlamada');
 assert.equal(modal.querySelector('[data-case-customer-names]').textContent,'Mamed Saray');assert.equal(modal.querySelector('[data-case-employee-names]').textContent,'Leyla Hasanова');assert.equal(modal.querySelector('[data-case-customer-phone]').textContent,'Qrup dialoqu');
 assert(modal.textContent.includes('Real incoming text'));assert(modal.textContent.includes('Real employee answer'));
 assert(modal.querySelector('.case-history-warning'));assert(modal.textContent.includes('manual.pdf'));assert(modal.textContent.includes('clip.mp4'));assert(modal.textContent.includes('Səsli mesajın transkripsiyası'));assert.equal(modal.querySelectorAll('.case-message-source').length,7);
 assert.equal(modal.querySelectorAll('[data-case-improve],[data-case-reject],[data-case-clarify],[data-case-save],[data-case-approve]').length,5);
 assert.equal(modal.querySelector('[data-case-translate]'),null);assert(!modal.textContent.includes('Tərcümə'));
 assert.equal(modal.querySelector('[data-case-history-earlier]').hidden,false);
 const boundarySelect=modal.querySelector('#caseStartMessage'),boundaryOptions=[...boundarySelect.options].map(option=>option.textContent);assert(boundaryOptions.some(label=>label.includes('Leyla Hasanова')&&label.includes('Real employee answer')));assert(!boundaryOptions.some(label=>/^[0-9a-f-]{32,}$/i.test(label)),'boundary selectors never expose UUIDs');boundarySelect.value='message-2';boundarySelect.dispatchEvent(new w.Event('change',{bubbles:true}));assert(modal.querySelector('[data-message-id="message-2"].case-message-boundary'),'selected boundary highlights its transcript message');
 const mediaButton=modal.querySelector('[data-message-id="message-3"] [data-case-media-open]');assert(mediaButton);mediaButton.click();await tick();assert(modal.querySelector('[data-message-id="message-3"] .case-media-preview'),'media request uses recovered sourceRoute when detail.route has no lead_id');const mediaCall=calls.find(c=>c.url.includes('message_id=provider-image-3'));assert(mediaCall,'attachment is fetched through the existing protected endpoint only after click');const mediaQuery=new URL(mediaCall.url,'https://example.test').searchParams;assert.equal(mediaQuery.get('channel_id'),'channel-b');assert.equal(mediaQuery.get('conversation_id'),route.conversation_id);assert.equal(mediaQuery.get('lead_id'),'18');
 for(const [id,selector] of [['message-4','audio.case-media-player'],['message-5','video.case-media-player'],['message-6','a.case-media-download']]){modal.querySelector(`[data-message-id="${id}"] [data-case-media-open]`).click();await tick();assert(modal.querySelector(`[data-message-id="${id}"] ${selector}`),`${id} renders its supported media player/link`);}const audioCall=calls.find(c=>c.url.includes('message_id=provider-audio-4')),audioQuery=new URL(audioCall.url,'https://example.test').searchParams;assert.equal(audioQuery.get('lead_id'),'17','missing message route fields fall back to evidence-scoped sourceRoute');assert.equal(audioQuery.get('channel_id'),'channel-a');
 const missing=modal.querySelector('[data-message-id="message-7"] [data-case-media-open]');missing.click();await tick();assert(modal.querySelector('[data-message-id="message-7"] [role="status"]').textContent.includes('Fayl tapılmadı.'),'missing media shows the protected API reason and the attachment type remains visible');
 let opened=null;d.addEventListener('tenant-support-open',event=>opened=event.detail);modal.querySelector('#caseTitle').value='Unsaved title';modal.querySelector('#caseTitle').dispatchEvent(new w.Event('input',{bubbles:true}));modal.querySelector('[data-case-route]').click();assert.deepEqual(JSON.parse(JSON.stringify(opened)),{provider:'connect',lead_id:17,channel_id:'channel-a',conversation_id:route.conversation_id});assert.equal(modal.style.display,'none');assert.equal(modal.querySelector('#caseTitle').value,'Unsaved title');
 d.dispatchEvent(new w.CustomEvent('tenant-view',{detail:{view:'support_cases'}}));assert.equal(modal.style.display,'');assert.equal(modal.querySelector('#caseTitle').value,'Unsaved title','return preserves unsaved edits');
 modal.querySelector('#caseTitle').value='Dəyişdirilmiş başlıq EN';modal.querySelector('#caseTitle').dispatchEvent(new w.Event('input',{bubbles:true}));
 modal.querySelector('#caseSymptoms').value='Symptom in English';modal.querySelector('#caseSymptoms').dispatchEvent(new w.Event('input',{bubbles:true}));
 const confirmation=modal.querySelector('[data-confirmation]');confirmation.checked=true;confirmation.dispatchEvent(new w.Event('change',{bubbles:true}));
 modal.querySelector('[data-case-save]').click();await tick();await tick();
 assert(calls.some(c=>c.opt.method==='PATCH'),'existing candidate API saves edits');
 const css=html.slice(html.indexOf('<style>',html.indexOf('</style>')));
 assert(css.includes('.case-content-column{min-width:0;min-height:0;overflow-y:auto'));
 assert(css.includes('.case-conversation-messages{display:flex;flex-direction:column;gap:12px;min-height:0;overflow-y:auto'));
 assert(css.includes('.case-history-warning{'));assert(css.includes('.case-message-source{'));
 assert(css.includes('grid-template-rows:auto minmax(0,1fr) auto'));
 assert(html.includes('support-pilot-compact')&&html.includes('supportPilotModal'),'pilot diagnostics use a compact entry point and modal');
 assert(js.includes('support-cases-filter')&&js.includes('<summary>Filtr</summary>'),'search, loading and analysis controls are grouped under Filter');
 assert(html.includes('Pilot ayarları'),'pilot settings have an explicit compact action');
 modal.querySelector('[data-case-close]').click();route.conversation_id='customer@s.whatsapp.net';await tick();d.querySelector('[data-case-open="case-1"]').click();await tick();await tick();assert.equal(d.querySelector('[data-case-customer-phone]').textContent,'+994501234567','direct case displays the real route phone');
 w.close();console.log('PASS: AZ modal fields, genuine conversation API, names, editable multilingual content, evidence IDs, five actions, and independent scroll layout');
})().catch(error=>{console.error(error);process.exitCode=1;w.close();});
