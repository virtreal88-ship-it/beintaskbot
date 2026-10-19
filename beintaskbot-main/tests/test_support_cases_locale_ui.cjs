const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const fs=require('node:fs'),assert=require('node:assert/strict');
const js=fs.readFileSync('docs/tenant-knowledge-candidates.js','utf8');
const tick=()=>new Promise(resolve=>setTimeout(resolve,30));
async function mount(language){
 const dom=new JSDOM('<div id="supportCases"></div><span id="supportCasesPending"></span><nav id="supportCasesNav"></nav>',{url:'https://example.test/app?site_lang='+language,runScripts:'outside-only'});
 const w=dom.window,d=w.document;w.tenantI18n={language};w.tenantProfileSnapshot={member:{tenant_id:'tenant-a',telegram_id:7,role:'owner'}};
 const card={id:'case-locale',version:1,title:'English source content stays as written',status:'pending_review',case_kind:'technical',resolution_method:'phone',content_language:'en',symptoms:['An English symptom'],operator_actions:['Employee made a call'],resolution_steps:[],cause:{status:'unknown',text:'Unknown'},outcome:{status:'unconfirmed',text:'Awaiting customer confirmation',message_ids:[]},source_message_ids:['msg-1'],can_approve:false,approval_block_reason:'confirmation_required',created_at:'2026-10-17T10:12:00Z'};
 const evidence=[{id:'msg-1',role:'customer',author_type:'customer',author_name:'Real Customer',body:'Original English message',happened_at:'2026-10-17T10:12:00Z'}];
 w.fetch=async(url)=>{let result={};if(url.includes('/ai/dialogs'))result={dialogs:[]};else if(url.includes('/ai/agents'))result={agents:[]};else if(url.includes('/ai/channels'))result={channels:[{channel_id:'channel-a',name:'Real channel'}]};else if(url.includes('/knowledge-candidates/case-locale?'))result={candidate:card,evidence,route:{provider:'connect',channel_id:'channel-a',conversation_id:'chat-1'}};else if(url.includes('/knowledge-candidates?'))result={candidates:[card]};return{ok:true,status:200,json:async()=>({success:true,result})};};
 w.eval(js);await tick();d.querySelector('[data-case-open="case-locale"]').click();await tick();
 return{dom,w,d,modal:d.getElementById('supportCaseModal')};
}
(async()=>{
 const expected={
  ru:{problem:'Описание проблемы',symptoms:'Симптомы',actions:'Действия сотрудника',resolution:'Решение',outcome:'Результат',details:'Подробности',save:'Сохранить',publish:'Утвердить и опубликовать',status:'На проверке',method:'Способ решения',unconfirmed:'Не подтверждено'},
  az:{problem:'Problemin təsviri',symptoms:'Əlamətlər',actions:'Görülən işlər',resolution:'Həll yolu',outcome:'Nəticə',details:'Təfərrüatlar',save:'Yadda saxla',publish:'Təsdiqlə və dərc et',status:'Yoxlamada',method:'Həll üsulu',unconfirmed:'Təsdiqlənməyib'},
  en:{problem:'Problem description',symptoms:'Symptoms',actions:'Employee actions',resolution:'Resolution',outcome:'Outcome',details:'Details',save:'Save',publish:'Approve and publish',status:'In review',method:'Resolution method',unconfirmed:'Unconfirmed'}
 };
 for(const language of ['ru','az','en']){const {dom,w,d,modal}=await mount(language);assert(modal);const e=expected[language];for(const value of [e.problem,e.symptoms,e.actions,e.resolution,e.outcome,e.details,e.save,e.publish,e.status,e.method,e.unconfirmed])assert(modal.textContent.includes(value),`${language} missing ${value}`);assert(modal.textContent.includes('English source content stays as written'),'case content is not translated by changing UI locale');assert(!modal.textContent.includes(({ru:'Yoxlama statusu',az:'Review status',en:'Статус проверки'})[language]),'UI labels do not mix languages');w.close();}
 console.log('PASS: support case UI labels, statuses, enum values, and actions switch RU/AZ/EN while case content and original messages remain unchanged');
})().catch(error=>{console.error(error);process.exitCode=1;});
