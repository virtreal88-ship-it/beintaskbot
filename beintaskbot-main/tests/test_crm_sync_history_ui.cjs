const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('../.support-test-deps/node_modules/jsdom');
const {mount:contactMount,settle,until,contactA,contactB,tenant,selectSource,writes}=require('./contacts_directory_fixture.cjs');
const {appHtml,installNativeTaskFixture,installNativeCompanyDealFixture}=require('./native_company_deal_fixture.cjs');
const {installSyncHistoryFixture}=require('./crm_sync_history_fixture.cjs');
const event=(state='pending',index=1,extra={})=>({event_id:'20000000-0000-4000-8000-'+String(index).padStart(12,'0'),kind:'scalar',state,attempt_outcome:state==='synced'?'acknowledged':'not_started',created_at:'2026-11-11T08:00:00.000Z',local_revision:'2026-11-11T08:00:00.000Z',recorded_at:'2026-11-11T09:00:00.000Z',issue:'none',...extra});
const history=(events,extra={})=>({success:true,history:{events,has_more:false,next_cursor:null,external_state_checked:false,...extra}});
const install=b=>{b.w.ReadableStream=ReadableStream;b.w.TextEncoder=TextEncoder;b.w.eval('('+installSyncHistoryFixture.toString()+')({})');b.h=b.w.syncHistoryFixture;b.q=s=>b.d.querySelector(s);b.text=()=>b.q('[data-sync-history]')?.textContent||'';b.rows=()=>[...b.d.querySelectorAll('[data-sync-history-event]')];return b};
async function contact(config={}){const b=install(await contactMount(config));b.open=async(id=contactA)=>{await b.w.tenantContactsDirectory.openNative(id);await settle()};return b}
async function native(config={}){const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));const dom=new JSDOM(appHtml(true),{url:'https://fixture.test/app?view=deals&lang=en',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,beforeParse(w){w.Response=Response;w.Headers=Headers;w.Request=Request;w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});w.eval('('+installNativeTaskFixture.toString()+')({})');w.eval('('+installNativeCompanyDealFixture.toString()+')('+JSON.stringify(config)+')')}});await settle();const b=install({w:dom.window,d:dom.window.document,f:dom.window.nativeTaskFixture,errors,close:()=>dom.window.close()});b.open=async(type='deal',id=b.f.ids[type])=>{await b.w.tenantContactsBridge.openEntity(type,id);await settle()};return b}
function empty(b){assert.equal(b.rows().length,0);assert(!b.text().includes('Journal recorded completion'))}
(async()=>{
 // Full history vocabulary and rendering remain shared across all three card types.
 for(const [lang,title,disclaimer,completion,emptyText]of [['en','Synchronization history','not a full state-transition audit','journal recorded completion','does not confirm'],['ru','История синхронизации','не полный журнал переходов','Журнал зафиксировал завершение','не подтверждает'],['az','Sinxronizasiya tarixçəsi','bütün vəziyyət keçidlərinin jurnalı deyil','Jurnalda','təsdiqləmir']]){
  const b=await contact({language:lang});try{b.h.events=['pending','processing','synced','superseded','disabled','conflict','unknown','needs_verification','failed'].map((state,i)=>event(state,i+1,{kind:['scalar','relation','legacy'][i%3],attempt_outcome:['not_started','sending','acknowledged','unknown','unverified'][i%5],issue:['none','needs_verification','conflict','superseded','access_changed','outcome_unknown','not_completed'][i%7],details:'PRIVATE METADATA',external_id:'SECRET EXTERNAL ID',error:'<img src=x onerror=alert(1)>'}));await b.open();assert.equal(b.rows().length,9);assert(b.text().includes(title)&&b.text().includes(disclaimer)&&b.text().includes(completion));assert(!/PRIVATE METADATA|SECRET EXTERNAL ID|onerror/.test(b.text()));assert.equal(b.q('[data-sync-history]').querySelectorAll('img,a,input,select').length,0);assert.equal(b.q('[data-sync-history]').querySelectorAll('button').length,1);assert.equal(b.q('time').dateTime,'2026-11-11T08:00:00.000Z');assert(b.text().includes(event().event_id));const url=new URL(b.h.calls[0].url,'https://fixture.test');assert.equal(url.searchParams.get('expected_tenant_id'),tenant);assert.equal(url.searchParams.get('expected_user_id'),String(b.f.profile.member.telegram_id));assert.equal(url.searchParams.get('limit'),'20');assert.equal(b.h.calls[0].cache,'no-store');assert.equal(b.h.calls[0].credentials,'same-origin');assert.equal(writes(b).length,0);
   b.h.events=[];b.q('[data-sync-history-refresh]').click();await settle();empty(b);assert(b.text().includes(emptyText));assert.deepEqual(b.errors,[]);
  }finally{b.close()}
 }
 console.log('PASS RU/AZ/EN: all journal states/kinds, bounded text-only output, explicit timestamps and empty-state uncertainty');
 {
  const b=await contact();try{const cursor={before:'2026-11-11T08:00:00.123456+00:00',before_id:event().event_id,before_kind:'scalar'};b.h.replies.push({payload:history([event('synced',1,{local_revision:cursor.before,recorded_at:cursor.before}),event('conflict',2,{kind:'relation'})],{has_more:true,next_cursor:cursor})});await b.open();assert.equal(b.rows().length,2);assert.equal(b.rows()[0].querySelectorAll('time')[1].dateTime,cursor.before);assert.equal(b.rows()[0].querySelectorAll('time')[1].title,cursor.before);b.h.replies.push({hold:true,payload:history([event('synced'),event('disabled',1,{kind:'relation'}),event('failed',3,{kind:'legacy'})])});const more=b.q('[data-sync-history-more]');more.click();more.click();await until(()=>b.h.pending.length===1);assert.equal(b.h.calls.length,2);assert.equal(b.q('[data-sync-history-refresh]').disabled,true);const query=new URL(b.h.calls[1].url,'https://fixture.test').searchParams;for(const [key,value]of Object.entries(cursor))assert.equal(query.get(key),value);b.h.pending.shift()();await settle();assert.equal(b.rows().length,4,'same event is deduplicated, same UUID in another journal is distinct');assert.equal(b.q('[data-sync-history-more]'),null);
   const before=b.h.calls.length;b.w.tenantI18n.setLanguage('ru');await settle();assert.match(b.text(),/История синхронизации/);assert.equal(b.h.calls.length,before,'language change does not read or write');assert.equal(writes(b).length,0);assert.deepEqual(b.errors,[]);
  }finally{b.close()}
 }
 console.log('PASS stable cross-journal pagination, duplicate clicks, deduplication and language changes without writes');
 for(const code of [403,404,409,500]){
  const b=await contact();try{await b.open();assert.equal(b.rows().length,1);b.h.replies.push({status:code});b.q('[data-sync-history-refresh]').click();await settle();empty(b);assert.match(b.text(),code===500?/Could not read history/:/unavailable with the current access/);assert(!b.text().includes('PRIVATE PROVIDER ERROR'));assert.equal(writes(b).length,0);}finally{b.close()}
 }
 {
  const b=await contact();try{b.h.replies.push({payload:history([event()],{has_more:true,next_cursor:{before:'bad',before_id:'x',before_kind:'provider secret'}})});await b.open();empty(b);assert.match(b.text(),/Could not read history/);assert.equal(b.q('[data-sync-history-more]'),null);b.h.replies.push({payload:history([event()],{external_state_checked:true})});b.q('[data-sync-history-refresh]').click();await settle();empty(b);assert.match(b.text(),/Could not read history/);}finally{b.close()}
 }
 console.log('PASS access/not-found/context/error responses erase prior rows; malformed pages never render provider text or cursors');
 for(const mode of ['close','newer','auth-pending','auth-invalid','tenant','user','permissions','navigate']){
  const b=await contact();try{b.h.replies.push({hold:true,payload:history([event('synced')])});await b.open();await until(()=>b.h.pending.length===1);
   if(mode==='close')b.q('[data-native-card-close]').click();
   if(mode==='newer'){b.h.events=[event('conflict',2)];await b.open(contactB)}
   if(mode.startsWith('auth-'))b.d.dispatchEvent(new b.w.CustomEvent('tenant-'+mode));
   if(['tenant','user','permissions'].includes(mode)){const profile=JSON.parse(JSON.stringify(b.f.profile));if(mode==='tenant')profile.member.tenant_id='10000000-0000-4000-8000-000000000999';if(mode==='user')profile.member.telegram_id=99;if(mode==='permissions'){profile.member.permissions=[];b.h.sourceFailures['/api/platform/crm/native/contacts/'+contactA]=403}b.d.dispatchEvent(new b.w.CustomEvent('tenant-profile',{detail:profile}))}
   if(mode==='navigate')b.d.dispatchEvent(new b.w.CustomEvent('tenant-view',{detail:{view:'deals'}}));
   await settle();b.h.pending.shift()();await settle();assert(!b.text().includes('journal recorded completion'));if(mode==='newer'){assert.equal(b.rows().length,1);assert.match(b.text(),/conflict/)}else {assert.equal(b.rows().length,0,mode);empty(b)}assert.equal(writes(b).length,0);assert.deepEqual(b.errors,[]);
  }finally{b.close()}
 }
 {
  const b=await contact();try{b.h.replies.push({bodyHold:true,payload:history([event('synced')])});await b.open();await until(()=>b.h.pendingBodies.length===1);b.d.dispatchEvent(new b.w.CustomEvent('tenant-auth-pending'));b.h.pendingBodies.shift()();await settle();empty(b);assert.equal(b.q('[data-sync-history]'),null);}finally{b.close()}
 }
 console.log('PASS delayed responses and JSON bodies cannot restore history after close, newer card, auth, identity, permissions or navigation changes');
 {
  const b=await contact({recordActions:{view:false,update:true}});try{await b.open();assert.equal(b.h.calls.length,0);empty(b);}finally{b.close()}
 }
 {
  const b=await contact();try{b.h.sourceFailures['/api/platform/crm/native/contacts/'+contactA]=403;await b.open();assert.equal(b.h.calls.length,0);empty(b);await selectSource(b,'kommo');const external=b.q('[data-contact-open]');external.click();await settle();assert.equal(b.h.calls.length,0);assert.equal(b.q('[data-sync-history]'),null);}finally{b.close()}
 }
 console.log('PASS no history read for denied/U-only native contacts or saved external contact cards');
 for(const type of ['deal','company']){
  const b=await native({companyUpdate:false,dealUpdate:false});try{b.h.events=[event('synced')];await b.open(type);assert.equal(b.rows().length,1);assert.match(b.text(),/journal recorded completion/);assert.equal(b.h.calls[0].path,'/api/platform/crm/native/'+(type==='deal'?'deals':'companies')+'/'+b.f.ids[type]+'/sync-history');assert.equal(b.q('[data-native-card-edit]').hidden,true,'read-only source can see history');assert.equal(b.f.calls.filter(x=>x.method!=='GET').length,0);b.h.replies.push({hold:true});b.q('[data-sync-history-refresh]').click();await until(()=>b.h.pending.length===1);b.q('[data-native-card-close]').click();await settle();empty(b);await b.open(type);assert.equal(b.rows().length,1);b.h.pending.shift()();await settle();assert.equal(b.rows().length,1);assert.deepEqual(b.errors,[]);}finally{b.close()}
  const blocked=await native({[type+'Read']:false,[type+'Update']:true});try{await blocked.open(type);assert.equal(blocked.h.calls.length,0,'U does not authorize history read');empty(blocked);}finally{blocked.close()}
  const rowBlocked=await native();try{rowBlocked.f[type==='deal'?'deals':'companies'][0].actions.view=false;await rowBlocked.open(type);assert.equal(rowBlocked.h.calls.length,0,'explicit source row R denial is preserved');}finally{rowBlocked.close()}
  const delayed=await native();try{delayed.h.holdSources=true;const pending=delayed.open(type);await until(()=>delayed.h.pendingSources.length===1);delayed.d.dispatchEvent(new delayed.w.CustomEvent('tenant-auth-pending'));delayed.d.dispatchEvent(new delayed.w.CustomEvent('tenant-profile',{detail:delayed.f.profile}));delayed.h.pendingSources.shift()();await pending;await settle();assert.equal(delayed.h.calls.length,0,'source read from an older auth generation cannot mount history');empty(delayed);}finally{delayed.close()}
 }
 console.log('PASS real deal/company read-only cards, exact source URLs, U-only/row denial and delayed source-read auth guards');
})().catch(error=>{console.error(error.stack||error);process.exitCode=1});
