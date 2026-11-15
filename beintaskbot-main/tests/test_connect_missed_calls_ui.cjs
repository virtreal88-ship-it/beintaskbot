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
