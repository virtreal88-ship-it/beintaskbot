const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('docs/tenant-operator-dialogs.js','utf8');
const begin=source.indexOf('  function missedCallLabel('),end=source.indexOf('\n  // The case history',begin);
const context={i18n:{language:'ru',date:x=>x},esc:x=>String(x),t:x=>x,
  mediaSize:()=>'',messageAuthor:()=> 'WhatsApp',deliveryMarkup:()=>'',copyControlMarkup:()=>'',
  currentDialog:{provider:'connect',channel_id:'channel',conversation_id:'direct'},currentPage:null};
vm.createContext(context);vm.runInContext(source.slice(begin,end),context);
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
