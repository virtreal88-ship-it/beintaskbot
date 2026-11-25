const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = require('node:path').join(__dirname, '..');

const messages = fs.readFileSync(root + '/docs/tenant-operator-dialogs.js', 'utf8');
const begin = messages.indexOf('  function missedCallLabel(');
const end = messages.indexOf('\n  // The case history', begin);
assert(begin >= 0 && end > begin, 'message renderer source is available');

const translations = {messageDeleted:'Это сообщение удалено', edited:'Изменено'};
const context = {
  window: {tenantWhatsAppMessageActions:{t:key=>translations[key] || key}},
  i18n: {language:'ru', date:value=>value},
  t:key=>key, esc:value=>String(value ?? ''), duration:value=>String(value),
  mediaSize:()=>'', messageAuthor:message=>message.author_name || 'Оператор',
  deliveryMarkup:()=>'', copyControlMarkup:()=>'', reactionMarkup:()=>'',
  replyText:key=>key, currentDialog:{provider:'connect',wecall:true,channel_id:'channel',conversation_id:'chat'}, currentPage:null,
  isDirectConnectDialog:()=>false, attachmentLabel:()=>'', attachmentCaption:()=>'', mediaLabel:()=>'',
  mediaAction:()=>'', mediaAnalysisLabel:()=>'', photoPreviewMarkup:()=>'', photoLoadLabel:()=>'',
  quotedMessagePreview:()=>'', missedCallLabel:()=>'', observedCallLabel:()=>'', invalidatedCallLabel:()=>'',
  rejectedCallLabel:()=>'', duration:()=>'',
};
vm.createContext(context);
vm.runInContext(messages.slice(begin,end), context);

const own = {id:'own',external_id:'connect:own',provider_message_id:'SYNTHETIC_OWN',source:'connect',
  source_lead_id:81,source_channel_id:'channel',source_conversation_id:'chat',direction:'outgoing',
  message_type:'text',body:'Operator message',author_type:'operator',author_name:'Оператор',
  happened_at:'2026-11-25T10:00:00Z'};
const deletedClient = {...own,id:'client',external_id:'connect:client',provider_message_id:'SYNTHETIC_CLIENT',
  direction:'incoming',message_type:'audio',body:'',message_deleted:true,message_delete_source:'whatsapp',
  media_url:'/private/media',has_transcription:true,audio_transcript:'private transcript',author_type:'customer'};
let markup = context.messageMarkup([deletedClient]);
assert.match(markup,/Это сообщение удалено/);
for (const hidden of ['private transcript','private/media','data-media-open','data-reply-message','data-quote-jump']) assert(!markup.includes(hidden));
markup = context.messageMarkup([{...own,wa_edited:true}]);
assert.match(markup,/Operator message/);
assert.match(markup,/Изменено/);

const noop = () => {};
const document = {head:{append:noop},createElement:()=>({}),addEventListener:noop};
const window = {tenantI18n:{language:'ru'},addEventListener:noop};
const sessionStorage = {getItem:()=>null,setItem:noop,removeItem:noop};
const actionContext = {document,window,sessionStorage,Date,URLSearchParams};
vm.createContext(actionContext);
vm.runInContext(fs.readFileSync(root + '/docs/tenant-whatsapp-message-actions.js','utf8'), actionContext);
const actions = actionContext.window.tenantWhatsAppMessageActions;
const member={tenant_id:'tenant',telegram_id:11};
const route={wecall:true,can_send:true,channel_id:'channel',conversation_id:'chat',lead_id:81};
const eligible={...own,wa_edit_version:'a'.repeat(64),wa_edit_until:new Date(Date.now()+60000).toISOString(),
  wa_delete_version:'b'.repeat(64),wa_delete_until:new Date(Date.now()+60000).toISOString()};
assert(actions.canEdit(member,route,eligible));
assert(actions.canDelete(member,route,eligible));
assert(!actions.canEdit(member,{...route,owner_overview_read_only:true},eligible));
assert(!actions.canDelete(member,{...route,archive_read_only:true},eligible));
assert(!actions.canEdit(member,route,{...eligible,direction:'incoming'}));
assert(!actions.canDelete(member,route,{...eligible,message_deleted:true}));
assert(!actions.canEdit(member,route,{...eligible,source_channel_id:'other'}));
assert.equal(actions.t('messageDeleted'),'Это сообщение удалено');
console.log('PASS: revoke tombstone is redacted in UI; edit/delete actions obey direction, source and read-only scope');

