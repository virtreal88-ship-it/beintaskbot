const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const {mount,settle,until,writes,formInput,contactA,company}=require('./contacts_directory_fixture.cjs');
const receipt=(state,extra={})=>({mode:'scoped_scalar',local_saved:true,state,...extra});
const submit=(b,id)=>b.d.getElementById(id).dispatchEvent(new b.w.Event('submit',{bubbles:true,cancelable:true}));
(async()=>{
 const helper=fs.readFileSync('docs/tenant-crm-save-receipt.js','utf8');
 const pure=new JSDOM('<html lang="en"><div id="receipt"></div></html>',{runScripts:'outside-only'});
 try{
  pure.window.eval(helper);const api=pure.window.tenantCrmSaveReceipt,node=pure.window.document.getElementById('receipt');
  for(const [lang,local,review]of [['en','Saved in Beintaskbot','previous value'],['ru','Сохранено в Beintaskbot','Предыдущее значение'],['az','Beintaskbot-da saxlanıldı','Əvvəlki dəyər']]){
   for(const state of ['pending','processing','needs_verification','conflict','superseded','unknown','disabled','off','unchanged','synced','<img src=x onerror=alert(1)>']){
    const text=api.text(receipt(state,{review_required:true,error_code:'PRIVATE-CODE'}),lang);
    assert(text.includes(local)&&text.includes(review));assert(!text.includes('PRIVATE-CODE')&&!text.includes('<img'));
    if(state==='synced')assert(!/externally synced|успешно синхрониз|uğurla sinxron/i.test(text));
   }
  }
  api.show('receipt',receipt('conflict',{superseded_unsent_event_ids:['PRIVATE-ID']}));
  assert.equal(node.getAttribute('role'),'status');assert.equal(node.getAttribute('aria-live'),'polite');assert(!node.textContent.includes('PRIVATE-ID'));assert.equal(node.querySelectorAll('button,a,input').length,0);
  pure.window.document.dispatchEvent(new pure.window.CustomEvent('tenant-profile',{detail:{member:{tenant_id:'A',telegram_id:1}}}));
  pure.window.document.dispatchEvent(new pure.window.CustomEvent('tenant-profile',{detail:{member:{tenant_id:'B',telegram_id:1}}}));assert.equal(node.textContent,'');
  for(const invalid of [null,{},receipt('pending',{local_saved:false}),{mode:'legacy',local_saved:true,state:'synced'}]){api.show('receipt',invalid);assert.equal(node.textContent,'');assert(!node.classList.contains('show'))}
 }finally{pure.window.close()}
 console.log('PASS shared RU/AZ/EN receipt presenter is text-only, local-save-specific and never claims current external sync');
 {
  const b=await mount({syncReceipt:receipt('pending')});
  try{
   await until(()=>b.d.querySelector('[data-contact-edit]'));
   let count=0;
   for(const [state,extra,expected]of [['pending',{},/queued.*not confirmed/],['needs_verification',{},/not sent.*needs verification/],['conflict',{},/conflict.*Review/],['pending',{review_required:true,superseded_unsent_event_ids:['hidden-event']},/previous value.*not sent/]]){
    b.f.syncReceipt=receipt(state,extra);b.d.querySelector('[data-contact-edit]').click();await until(()=>!b.d.getElementById('nativeContactName').disabled);
    formInput(b,'nativeContactName','Saved contact '+(++count));submit(b,'nativeContactForm');
    await until(()=>!b.d.getElementById('nativeContactModal').classList.contains('show'));await settle();
    const node=b.d.getElementById('contactsSyncReceipt');assert(node.classList.contains('show'));assert.match(node.textContent,/Saved in Beintaskbot/);assert.match(node.textContent,expected);
    assert.equal(writes(b).length,count,'receipt display must not issue a retry or recovery write');assert(!node.textContent.includes('hidden-event'));
   }
   const before=writes(b).length;b.w.tenantI18n.setLanguage('ru');await settle();assert.match(b.d.getElementById('contactsSyncReceipt').textContent,/Предыдущее значение.*не было отправлено/);
   b.w.tenantI18n.setLanguage('az');await settle();assert.match(b.d.getElementById('contactsSyncReceipt').textContent,/Əvvəlki dəyər.*göndərilməyib/);assert.equal(writes(b).length,before);
   b.d.dispatchEvent(new b.w.CustomEvent('tenant-auth-pending'));assert.equal(b.d.getElementById('contactsSyncReceipt').textContent,'');assert.equal(b.d.getElementById('nativeContactSyncReceipt').textContent,'');
   assert.deepEqual(b.errors,[]);
  }finally{b.close()}
 }
 console.log('PASS real contact save shows queued/verification/conflict/older-unsent outcomes and survives language changes without extra writes');
 {
  const deal='10000000-0000-4000-8000-000000000901';
  const b=await mount({view:'deals',language:'ru',syncReceipt:receipt('needs_verification'),dealRows:[{id:deal,title:'Deal',pipeline_id:'p',stage_id:'s',contact_id:null,description:''}],dealOptions:{workflows:[{pipeline_id:'p',pipeline_name:'P',stage_id:'s',stage_name:'S'}],contacts:[],companies:[],members:[]}});
  try{
   await until(()=>b.d.querySelector('[data-native-edit="deal:'+deal+'"]'));
   for(const [state,extra,expected]of [['needs_verification',{},/не отправлены.*требуется проверка/],['pending',{review_required:true},/Предыдущее значение.*не было отправлено/]]){
    b.f.syncReceipt=receipt(state,extra);b.d.querySelector('[data-native-edit="deal:'+deal+'"]').click();await until(()=>b.d.getElementById('nativeDealModal').classList.contains('show'));
    formInput(b,'nativeDealTitle','Deal '+state);submit(b,'nativeDealForm');await until(()=>!b.d.getElementById('nativeDealModal').classList.contains('show'));await settle();
    const node=b.d.getElementById('nativeDealsSyncReceipt');assert.match(node.textContent,/Сохранено в Beintaskbot/);assert.match(node.textContent,expected);assert(node.classList.contains('show'));
   }
   assert.equal(writes(b).length,2);assert(writes(b).every(row=>row.path==='/api/platform/crm/native/deals/'+deal));
   b.w.tenantI18n.setLanguage('en');await settle();assert.match(b.d.getElementById('nativeDealsSyncReceipt').textContent,/previous value.*not sent/);assert.equal(writes(b).length,2);
   b.d.dispatchEvent(new b.w.CustomEvent('tenant-auth-pending'));assert.equal(b.d.getElementById('nativeDealsSyncReceipt').textContent,'');assert.deepEqual(b.errors,[]);
  }finally{b.close()}
 }
 console.log('PASS real deal save uses the same receipt helper and never retries or calls verification endpoints');
 {
  const b=await mount({syncReceipt:receipt('pending',{review_required:true})});
  try{
   await until(()=>b.d.querySelector('[data-contact-edit]'));b.d.querySelector('[data-contact-edit]').click();await until(()=>!b.d.getElementById('nativeContactName').disabled);
   formInput(b,'nativeContactName','Late result');b.f.holdWrite=true;submit(b,'nativeContactForm');await until(()=>b.f.pendingWrites.length===1);
   b.d.dispatchEvent(new b.w.CustomEvent('tenant-auth-pending'));b.f.pendingWrites.shift()();await settle();
   assert.equal(b.d.getElementById('contactsSyncReceipt').textContent,'');assert.equal(b.d.getElementById('nativeContactSyncReceipt').textContent,'');assert.equal(writes(b).length,1);
  }finally{b.close()}
 }
 console.log('PASS stale contact save receipt cannot reappear after authorization revalidation');
 {
  const b=await mount({scopedRelations:false,syncReceipt:receipt('needs_verification')});
  try{
   await until(()=>b.d.querySelector('[data-contact-edit]'));b.d.querySelector('[data-contact-edit]').click();await until(()=>!b.d.getElementById('nativeContactName').disabled);
   formInput(b,'nativeContactName','Saved before relation failure');b.d.getElementById('nativeContactCompany').value=company;
   b.f.writeFailure={status:403,code:'forbidden',target:'/companies/'};submit(b,'nativeContactForm');
   await until(()=>b.d.getElementById('nativeContactModalError').textContent.includes('company link was not'));
   assert.match(b.d.getElementById('nativeContactSyncReceipt').textContent,/Saved in Beintaskbot.*not sent.*needs verification/);
   assert.match(b.d.getElementById('contactsSyncReceipt').textContent,/not sent/);assert.equal(writes(b).length,2);
   assert.equal(b.d.getElementById('nativeContactSyncReceipt').querySelectorAll('button,a').length,0);
  }finally{b.close()}
 }
 console.log('PASS partial company-link failure retains the scalar local-save receipt inside the open contact modal');
})().catch(error=>{console.error(error);process.exitCode=1});
