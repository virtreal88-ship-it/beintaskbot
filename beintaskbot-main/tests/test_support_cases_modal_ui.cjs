const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const fs=require('node:fs'),assert=require('node:assert/strict');
const html=fs.readFileSync('docs/tenant-app.html','utf8');
const js=fs.readFileSync('docs/tenant-knowledge-candidates.js','utf8');
const dom=new JSDOM('<div id="supportCases"></div><span id="supportCasesPending"></span><nav id="supportCasesNav"></nav>',{url:'https://example.test/app?site_lang=az',runScripts:'outside-only'});
const w=dom.window,d=w.document,calls=[];let historyPage=0;
w.tenantI18n={language:'az'};
w.tenantProfileSnapshot={member:{tenant_id:'tenant-a',telegram_id:7,role:'owner'}};
const card={id:'case-1',version:3,title:'Axtarış nəticəsi haqqında real case title',status:'pending_review',case_kind:'technical',resolution_method:'chat',content_language:'en',symptoms:['Customer sees unrelated records'],operator_actions:['Employee checked search filters'],resolution_steps:['Refresh the search index'],cause:{status:'suspected',text:'The index may be stale'},outcome:{status:'unconfirmed',text:'Waiting for customer confirmation',message_ids:[]},source_message_ids:['message-2'],can_approve:true,approval_block_reason:null,created_at:'2026-10-17T10:12:00Z'};
const route={provider:'connect',lead_id:17,channel_id:'channel-a',conversation_id:'customer@s.whatsapp.net'};
const messages=[
 {id:'message-1',direction:'incoming',author_type:'customer',author_name:'Mamed Saray',body:'Real incoming text',message_type:'text',happened_at:'2026-10-17T10:12:00Z',source:'connect'},
 {id:'message-2',direction:'outgoing',author_type:'operator',author_name:'Leyla Hasanова',body:'Real employee answer',message_type:'text',happened_at:'2026-10-17T10:15:00Z',source:'connect'},
 {id:'message-3',external_id:'provider-image-3',source_lead_id:17,direction:'incoming',author_type:'customer',author_name:'Mamed Saray',body:'',message_type:'image',happened_at:'2026-10-17T10:16:00Z',source:'connect'}
];
w.URL.createObjectURL=()=> 'blob:test-image';w.URL.revokeObjectURL=()=>{};
w.fetch=async(url,opt={})=>{
 calls.push({url,opt});let result={};
 if(url.includes('/ai/dialogs/history')){
   const q=new URL(url,'https://example.test').searchParams;
   assert.equal(q.get('provider'),'connect');assert.equal(q.get('channel_id'),'channel-a');assert.equal(q.get('conversation_id'),route.conversation_id);
   historyPage++;
   result=historyPage===1?{dialog:route,messages,next_before:'older-cursor'}:{dialog:route,messages:[{id:'message-0',direction:'incoming',author_type:'customer',author_name:'Mamed Saray',body:'Older real text',message_type:'text',happened_at:'2026-10-17T10:00:00Z',source:'connect'}],next_before:null};
 }else if(url.includes('/chat/attachment'))return {ok:true,status:200,blob:async()=>new w.Blob(['test image bytes'],{type:'image/jpeg'}),headers:{get:()=> 'image/jpeg'}};
 else if(url.includes('/ai/dialogs'))result={dialogs:[]};
 else if(url.includes('/ai/agents'))result={agents:[{id:'agent-a',name:'Nizam AI'}]};
 else if(url.includes('/ai/channels'))result={channels:[{provider:'connect',channel_id:'channel-a',name:'Texniki Dəstək'}]};
 else if(url.includes('/knowledge-candidates/case-1?'))result={candidate:card,evidence:[{...messages[1],role:'operator',text:messages[1].body}],route};
 else if(url.includes('/knowledge-candidates?'))result={candidates:[card],next_before:null};
 else if(url.endsWith('/knowledge-candidates/case-1')&&opt.method==='PATCH'){
   const body=JSON.parse(opt.body);assert.equal(body.title,'Dəyişdirilmiş başlıq EN');assert.deepEqual(body.symptoms,['Symptom in English']);assert.deepEqual(body.source_message_ids,['message-2']);assert.deepEqual(body.outcome.message_ids,['message-2']);result={candidate:{...card,...body,version:4},evidence:[{...messages[1],role:'operator',text:messages[1].body}],route};
 }else result={};
 return {ok:true,status:200,json:async()=>({success:true,result})};
};
const tick=()=>new Promise(resolve=>setTimeout(resolve,25));
(async()=>{
 w.eval(js);await tick();
 d.querySelector('[data-case-open="case-1"]').click();await tick();await tick();
 const modal=d.getElementById('supportCaseModal');assert(modal,'case modal opens from actual list');
 assert.equal(modal.querySelector('#caseTitle').value,card.title);assert.equal(modal.querySelector('.case-review-status select'),null,'review status is informational');
 assert.equal(modal.querySelectorAll('.case-meta-strip select').length,3,'only case type, resolution method and outcome confirmation are dropdowns');
 assert.equal(modal.querySelector('[data-case-status]').textContent,'Yoxlamada');
 assert.equal(modal.querySelector('[data-case-customer-names]').textContent,'Mamed Saray');assert.equal(modal.querySelector('[data-case-employee-names]').textContent,'Leyla Hasanова');
 assert(modal.textContent.includes('Real incoming text'));assert(modal.textContent.includes('Real employee answer'));
 assert.equal(modal.querySelectorAll('[data-case-improve],[data-case-reject],[data-case-clarify],[data-case-save],[data-case-approve]').length,5);
 assert.equal(modal.querySelector('[data-case-translate]'),null);assert(!modal.textContent.includes('Tərcümə'));
 assert.equal(modal.querySelector('[data-case-history-earlier]').hidden,false);
 const mediaButton=modal.querySelector('[data-case-media-open]');assert(mediaButton);mediaButton.click();await tick();assert(modal.querySelector('.case-media-preview'));assert(calls.some(c=>c.url.includes('message_id=provider-image-3')),'attachment is fetched through the existing protected endpoint only after click');
 modal.querySelector('#caseTitle').value='Dəyişdirilmiş başlıq EN';modal.querySelector('#caseTitle').dispatchEvent(new w.Event('input',{bubbles:true}));
 modal.querySelector('#caseSymptoms').value='Symptom in English';modal.querySelector('#caseSymptoms').dispatchEvent(new w.Event('input',{bubbles:true}));
 const confirmation=modal.querySelector('[data-confirmation]');confirmation.checked=true;confirmation.dispatchEvent(new w.Event('change',{bubbles:true}));
 modal.querySelector('[data-case-save]').click();await tick();await tick();
 assert(calls.some(c=>c.opt.method==='PATCH'),'existing candidate API saves edits');
 const css=html.slice(html.indexOf('<style>',html.indexOf('</style>')));
 assert(css.includes('.case-content-column{min-width:0;min-height:0;overflow-y:auto'));
 assert(css.includes('.case-conversation-messages{display:flex;flex-direction:column;gap:12px;min-height:0;overflow-y:auto'));
 assert(css.includes('grid-template-rows:auto minmax(0,1fr) auto'));
 w.close();console.log('PASS: AZ modal fields, genuine conversation API, names, editable multilingual content, evidence IDs, five actions, and independent scroll layout');
})().catch(error=>{console.error(error);process.exitCode=1;w.close();});
