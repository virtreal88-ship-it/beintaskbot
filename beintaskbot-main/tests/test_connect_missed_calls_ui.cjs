const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('docs/tenant-operator-dialogs.js','utf8');
const begin=source.indexOf('  function missedCallLabel('),end=source.indexOf('\n  // The case history',begin);
const sharedBegin=source.indexOf('  const sharedMessageCopy='),sharedEnd=source.indexOf('  const dialogPreview=',sharedBegin);
const cardBegin=source.indexOf('  function sharedMessageMarkup('),cardEnd=source.indexOf('\n  }',cardBegin)+'\n  }'.length;
const context={i18n:{language:'ru',date:x=>x},esc:x=>String(x),t:x=>x,
  mediaSize:()=>'',messageAuthor:()=> 'WhatsApp',deliveryMarkup:()=>'',copyControlMarkup:()=>'',
 currentDialog:{provider:'connect',channel_id:'channel',conversation_id:'direct',wecall:true},currentPage:null};
vm.createContext(context);vm.runInContext([source.slice(sharedBegin,sharedEnd),source.slice(cardBegin,cardEnd),source.slice(begin,end)].join('\n'),context);
for(const [language,audio,video] of [['ru','Пропущенный звонок','Пропущенный видеозвонок'],['az','Buraxılmış zəng','Buraxılmış videozəng'],['en','Missed call','Missed video call']]){
 context.i18n.language=language;
 for(const [isVideo,label] of [[false,audio],[true,video]]){
  const markup=context.messageMarkup([{id:'call',external_id:'connect:call:1',source:'connect',direction:'system',message_type:'call_missed',call_is_video:isVideo,happened_at:'2026-11-15T09:00:00Z'}]);
  assert(markup.includes(label));assert(markup.includes('2026-11-15T09:00:00Z'));
  assert(!markup.includes('data-media-open'));assert(!markup.includes('data-reply-message'));
  assert(!markup.includes('data-quote-jump'));
 }
}
console.log('missed call presentation RU/AZ/EN passed');
for(const [language,audio,video] of [['ru','Отклонённый входящий звонок','Отклонённый входящий видеозвонок'],['az','Rədd edilmiş daxil olan zəng','Rədd edilmiş daxil olan videozəng'],['en','Rejected incoming call','Rejected incoming video call']]){
 context.i18n.language=language;
 for(const [isVideo,label] of [[false,audio],[true,video]]){
  const markup=context.messageMarkup([{id:'call',external_id:'connect:call:2',source:'connect',direction:'system',message_type:'call_rejected',call_is_video:isVideo,happened_at:'2026-11-16T10:13:21Z'}]);
  assert(markup.includes(label));assert(markup.includes('data-rejected-call'));
  assert(!markup.includes('data-missed-call'));assert(!markup.includes('data-media-open'));
  assert(!markup.includes('data-reply-message'));assert(!markup.includes('data-quote-jump'));
 }
}
console.log('rejected incoming call presentation RU/AZ/EN passed');
for(const [language,audio,video] of [['ru','Статус звонка уточнён','Статус видеозвонка уточнён'],['az','Zəngin statusu dəqiqləşdirildi','Videozəngin statusu dəqiqləşdirildi'],['en','Call status corrected','Video call status corrected']]){
 context.i18n.language=language;
 for(const [isVideo,label] of [[false,audio],[true,video]]){
  const markup=context.messageMarkup([{id:'call',external_id:'connect:call:2',source:'connect',direction:'system',message_type:'call_invalidated',call_is_video:isVideo,happened_at:'2026-11-16T10:13:21Z'}]);
  assert(markup.includes(label));assert(markup.includes('data-invalidated-call'));
  for(const prohibited of ['data-missed-call','data-rejected-call','data-media-open','data-reply-message','data-quote-jump'])assert(!markup.includes(prohibited));
 }
}
console.log('neutral corrected call presentation RU/AZ/EN passed');
for(const [language,audio,video] of [['ru','Входящий звонок · результат неизвестен','Входящий видеозвонок · результат неизвестен'],['az','Daxil olan zəng · nəticə məlum deyil','Daxil olan videozəng · nəticə məlum deyil'],['en','Incoming call · outcome unknown','Incoming video call · outcome unknown']]){
 context.i18n.language=language;
 for(const [isVideo,label] of [[false,audio],[true,video]]){
  const markup=context.messageMarkup([{id:'call',external_id:'connect:call:3',source:'connect',direction:'system',message_type:'call_observed',call_is_video:isVideo,happened_at:'2026-11-15T09:00:00Z'}]);
  assert(markup.includes(label));assert(markup.includes('data-observed-call'));
  for(const prohibited of ['data-missed-call','data-rejected-call','data-invalidated-call','data-media-open','data-reply-message','data-quote-jump','operator-audio-duration'])assert(!markup.includes(prohibited));
 }
}
console.log('unknown incoming call presentation RU/AZ/EN passed');
context.member={tenant_id:'tenant-one',telegram_id:11};context.URLSearchParams=URLSearchParams;
context.i18n.language='ru';
const recorded={happened_at:'2026-11-18T00:54:08Z',created_at:'2026-11-18T01:00:00Z',id:'recording-message',source:'connect',direction:'system',message_type:'call_recording',
 call_recording:{account_number:'994123456789',id:'a'.repeat(32),duration_ms:27183,complete:false,expires_at:new Date(Date.now()+60000).toISOString(),archived_owner_binding:true}};
const recordingMarkup=context.messageMarkup([recorded]);
assert(recordingMarkup.includes('Возможны пропуски аудио'));
assert(recordingMarkup.includes('<time datetime="2026-11-18T00:54:08.000Z">2026-11-18T00:54:08Z</time>'));
assert(!recordingMarkup.includes(recorded.created_at),'call start, not import time');
assert(!context.messageMarkup([{...recorded,happened_at:'invalid',created_at:null}]).includes('<time'),'no invented timestamp');
assert(context.messageMarkup([{...recorded,happened_at:null}]).includes(recorded.created_at),'legacy timestamp fallback');
assert(recordingMarkup.includes('<strong>Звонок WhatsApp</strong>'));
assert(recordingMarkup.includes('Источник +994123456789'));
assert(recordingMarkup.includes('Привязано владельцем к архивному диалогу'));
assert(recordingMarkup.includes('expected_tenant_id=tenant-one'));
assert(recordingMarkup.includes('expected_user_id=11'));
assert(recordingMarkup.includes('preload="none"'));
for(const forbidden of ['data-media-open','data-reply-message','data-media-download'])assert(!recordingMarkup.includes(forbidden));
for(const forbidden of ['27 сек.','28 сек.','Удаление аудио','Запись звонка','Внутренняя запись','клиенту не отправляется'])assert(!recordingMarkup.includes(forbidden));
for(const [language,inbound,outbound,unknown] of [
 ['ru','Входящий звонок WhatsApp','Исходящий звонок WhatsApp','Звонок WhatsApp'],
 ['az','Daxil olan WhatsApp zəngi','Gedən WhatsApp zəngi','WhatsApp zəngi'],
 ['en','Incoming WhatsApp call','Outgoing WhatsApp call','WhatsApp call']]){
 context.i18n.language=language;
 for(const [direction,label] of [['inbound',inbound],['outbound',outbound],['unknown',unknown],[undefined,unknown],['incoming',unknown]]){
  const markup=context.messageMarkup([{...recorded,call_recording:{...recorded.call_recording,direction,complete:true,archived_owner_binding:false}}]);
  assert(markup.includes(`<strong>${label}</strong>`));assert(markup.includes('<audio'));
  assert(!markup.includes('<p>'));assert(!markup.includes('<small>'));
  assert(!markup.includes('994123456789'));assert(!markup.includes(recorded.call_recording.expires_at));
 }
}
context.i18n.language='ru';
recorded.call_recording.expires_at='2000-01-01T00:00:00Z';
const expired=context.messageMarkup([recorded]);assert(expired.includes('Срок хранения записи истёк'));assert(!expired.includes('<audio'));
console.log('compact RU/AZ/EN recording directions, private playback, incomplete status and expiry passed');

const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
for(const [language,contact,location,map] of [
 ['ru','Контакт','Геолокация','Открыть на карте'],['az','Kontakt','Məkan','Xəritədə aç'],['en','Contact','Location','Open in Maps']
]){
 context.i18n.language=language;
 const base={source:'connect',direction:'incoming',happened_at:'2026-11-15T09:00:00Z'};
 const rows=[
  {...base,id:'shared-contact',external_id:'connect:contacts',message_type:'contact',contacts:[
   {name:'Synthetic',phones:['+15550000003','javascript:alert(1)']},{name:'Second',phones:[]}]},
  {...base,id:'shared-location',external_id:'connect:location',message_type:'location',location:{latitude:0,longitude:-48.3,name:'Point'}},
  {...base,id:'invalid-location',external_id:'connect:invalid',message_type:'location',location:{latitude:true,longitude:0}},
 ];
 const dom=new JSDOM(context.messageMarkup(rows),{url:'https://test.invalid'});
 const doc=dom.window.document;
 const contactCard=doc.querySelector('[data-message-id="shared-contact"] [data-shared-message]');
 assert.equal(contactCard.querySelector('strong').textContent,contact);
 assert(contactCard.textContent.includes('Second'));
 assert.deepEqual([...contactCard.querySelectorAll('a')].map(a=>a.getAttribute('href')),['tel:+15550000003']);
 const locationCard=doc.querySelector('[data-message-id="shared-location"] [data-shared-message]');
 assert.equal(locationCard.querySelector('strong').textContent,location);
 assert.equal(locationCard.querySelector('a').textContent,map);
 assert.equal(new URL(locationCard.querySelector('a').href).searchParams.get('query'),'0, -48.3');
 assert.equal(locationCard.querySelector('a').rel,'noopener noreferrer');
 assert(!doc.querySelector('[data-message-id="invalid-location"] a'));
 assert(!doc.querySelector('[data-media-open],[data-chat-attachment]'));
 dom.window.close();
}
console.log('shared WhatsApp contacts and locations render in RU/AZ/EN without fetching media');
const readFile=p=>fs.readFileSync(p,'utf8');
const settle=async()=>{for(let i=0;i<30;i++)await new Promise(resolve=>setImmediate(resolve));};
(async()=>{
 const dom=new JSDOM(readFile('docs/tenant-app.html'),{url:'https://crm.test/app?view=dialogs',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,member={tenant_id:'tenant',telegram_id:11,role:'owner',active:true};
 w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});
 w.tenantProfileSnapshot={member,capabilities:{modules:{dialogs:true}}};
 const missedId='80000000-0000-0000-0000-000000000501';
 const route={provider:'connect',channel_id:'channel',conversation_id:'customer',lead_id:41,
   customer_name:'Customer',display_state:'manual',state:'human',can_send:false,available_actions:[],unread_count:1};
 const calls=[];let readId=null;
 w.fetch=async(url,options={})=>{
  const parsed=new URL(url,w.location),path=parsed.pathname;calls.push({path,method:options.method||'GET',body:options.body});let result={};
  if(path.endsWith('/platform/me'))result=w.tenantProfileSnapshot;
  else if(path.endsWith('/dialogs/history'))result={dialog:route,messages:[{id:missedId,external_id:'connect:call:501',source:'connect',source_channel_id:'channel',source_lead_id:41,channel:'connect:channel',direction:'system',message_type:'call_missed',body:'',call_is_video:false,happened_at:'2026-11-18T10:00:00Z'}],read_through_message_id:missedId,next_before:null};
  else if(path.endsWith('/dialogs/read')&&options.method==='POST'){readId=JSON.parse(options.body).message_id;result={updated:true,read_through_message_id:readId};}
  else if(path.endsWith('/dialogs'))result={dialogs:[{...route,unread_count:readId?0:1}],channels:[],agents:[],attention_count:0,unread_count:readId?0:1,total_count:1,next_offset:null};
  return{ok:true,status:200,json:async()=>({success:true,result})};
 };
 w.eval(readFile('docs/tenant-operator-i18n.js'));w.eval(readFile('docs/tenant-operator-dialogs.js'));
 await settle();d.getElementById('view-dialogs')?.classList.add('active');await w.tenantOperator.refresh();await settle();await w.tenantOperator.openDialog(route);await settle();
 assert(d.querySelector('[data-message-id="'+missedId+'"] [data-missed-call]'),'the missed call remains a visible timeline item when the chat opens');
 assert.equal(readId,missedId,'opening the chat sends its missed-call message ID to the normal read endpoint');
 assert.deepEqual(JSON.parse(calls.find(c=>c.path.endsWith('/dialogs/read')).body),{
   provider:'connect',lead_id:41,channel_id:'channel',conversation_id:'customer',message_id:missedId,
   expected_tenant_id:'tenant',expected_user_id:11
 });
 dom.window.close();console.log('PASS opening a chat automatically marks the missed-call timeline item read');
})().catch(error=>{console.error(error);process.exitCode=1;});

(async()=>{
 const dom=new JSDOM(readFile('docs/tenant-app.html'),{url:'https://crm.test/app?view=dialogs',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,member={tenant_id:'tenant',telegram_id:17,role:'operator',active:true};
 try{
  w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});
  w.tenantProfileSnapshot={member,capabilities:{modules:{dialogs:true}}};
  const stamp='2026-11-18T10:00:00Z';
  const waiting={state:'waiting',revision:0,can_accept:true,has_inbound:false,last_inbound_id:null};
  const accepted={state:'accepted',revision:1,id:'episode',operator_id:17,can_close:true};
  const base={provider:'connect',channel_id:'channel',conversation_id:'call-only',lead_id:41,
   customer_name:'Call-only',display_state:'manual',can_send:false,available_actions:[],lifecycle_enabled:true,
   lifecycle:waiting,last_message_id:'missed',last_message_at:stamp,last_message_type:'call_missed',last_message_direction:'system',missed_call_pending:true};
  const sibling={...base,channel_id:'sibling-source',lead_id:42,customer_name:'Other source',last_message_id:'other-missed'};
  const ordinary={...base,channel_id:'ordinary-source',lead_id:43,customer_name:'Ordinary line',last_message_id:'text',last_message_type:'text',last_message_direction:'incoming',last_message_text:'Ordinary message',missed_call_pending:undefined};
  let read=false,authority=waiting,acceptFailure=true,releaseAccept,staleList=true,corrected=false,listCount=0;
  const success=result=>({ok:true,status:200,json:async()=>({success:true,result})});
  const row=name=>[...d.querySelectorAll('.operator-row')].find(el=>el.querySelector('.operator-row-title')?.textContent===name);
  const red=name=>!!row(name)?.querySelector('.operator-row-preview-missed-pending');
  const unread=name=>row(name)?.querySelector('.operator-unread-count')?.textContent||null;
  w.fetch=async(url,opt={})=>{
   const parsed=new URL(url,w.location),path=parsed.pathname;
   if(path.endsWith('/platform/me'))return success(w.tenantProfileSnapshot);
   if(path.endsWith('/dialogs_lifecycle')){
    const payload=JSON.parse(opt.body);
    assert.equal(payload.action,'accept');assert.equal(payload.channel_id,'channel');assert.equal(payload.conversation_id,'call-only');
    if(acceptFailure)return {ok:false,status:500,json:async()=>({success:false,error:'Simulated refusal'})};
    await new Promise(resolve=>{releaseAccept=resolve;});authority=accepted;
    return success({lifecycle:accepted});
   }
   if(path.endsWith('/dialogs/history')){
    const selected=[base,sibling,ordinary].find(r=>r.channel_id===parsed.searchParams.get('channel_id'));
    assert(selected);
    return success({dialog:{...selected,lifecycle:selected===base?authority:waiting},
     messages:[{id:selected.last_message_id,external_id:selected.last_message_id,source:'connect',source_channel_id:selected.channel_id,source_lead_id:selected.lead_id,
       channel:'connect:'+selected.channel_id,direction:selected.last_message_direction,message_type:selected.last_message_type,
       body:'',happened_at:stamp}],read_through_message_id:selected.last_message_id,next_before:null});
   }
   if(path.endsWith('/dialogs/read')){
    const payload=JSON.parse(opt.body);
    if(payload.channel_id==='channel'){assert.equal(payload.message_id,'missed');read=true;}
    return success({updated:true});
   }
   if(path.endsWith('/dialogs')){
    listCount++;
    return success({dialogs:[{...base,lifecycle:staleList?waiting:authority,
      missed_call_pending:staleList||authority===waiting,unread_count:read?0:1},
      {...sibling,unread_count:1,...(corrected?{last_message_type:'call_rejected',missed_call_pending:undefined}:{})},
      {...ordinary,unread_count:1}],channels:[],agents:[],attention_count:0,unread_count:3,total_count:3,next_offset:null});
   }
   return success({});
  };
  w.eval(readFile('docs/tenant-operator-i18n.js'));w.eval(readFile('docs/tenant-operator-dialogs.js'));
  await settle();d.getElementById('view-dialogs')?.classList.add('active');await w.tenantOperator.refresh();await settle();
  assert(red('Call-only')&&red('Other source'));assert.equal(unread('Call-only'),'1');
  assert.equal(red('Ordinary line'),false);assert.equal(unread('Ordinary line'),'1');
  row('Call-only').click();await settle();
  assert.equal(read,true);assert.equal(unread('Call-only'),null);assert(red('Call-only'),'opening and automatic read do not clear red');
  assert(d.querySelector('[data-missed-call]'),'normal call history remains visible');
  assert(!d.querySelector('[data-operator-lifecycle="dismiss"]'),'call-only does not grant No reply');
  const action=()=>d.querySelector('[data-operator-lifecycle="accept"]');
  assert(action(),'authorized non-owner can accept a visible call-only route');
  action().click();await settle();assert(red('Call-only'),'a failed accept keeps the red preview');assert(action());
  acceptFailure=false;
  action().click();await settle();assert.equal(typeof releaseAccept,'function');
  assert(red('Call-only'),'submitting accept is not a confirmed acceptance');
  row('Other source').click();await settle();
  assert(red('Call-only'),'switching dialogues while accept is in flight retains attention');
  releaseAccept();await settle();
  assert.equal(red('Call-only'),false,'the exact accepted route loses red even after switching');
  assert(red('Other source'),'another route is untouched');
  const before=listCount;
  await w.tenantOperator.refresh();await settle();
  assert(listCount>before);assert.equal(red('Call-only'),false,'a stale waiting snapshot cannot reapply red after the accepted receipt');
  staleList=false;corrected=true;
  await w.tenantOperator.refresh();await settle();
  assert.equal(red('Other source'),false,'provider correction is not presented as a missed call');
  assert.equal(unread('Other source'),'1','a corrected preview does not change its existing unread badge');
  assert.equal(unread('Ordinary line'),'1');
  console.log('PASS managed missed-call highlight: still red after opening or failed claim; clears upon exact accepted receipt, including stale list');
 }finally{dom.window.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
