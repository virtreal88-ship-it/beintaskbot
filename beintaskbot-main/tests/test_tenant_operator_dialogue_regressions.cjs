const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=path.join(__dirname,'..');
const ui=fs.readFileSync(path.join(root,'docs','tenant-operator-dialogs.js'),'utf8');
const l10n=fs.readFileSync(path.join(root,'docs','tenant-operator-i18n.js'),'utf8');
const backend=fs.readFileSync(path.join(root,'tenant_operator_dialogs.py'),'utf8');

assert.match(ui,/author_type==='ai'/,'AI messages need their own durable author label');
assert.match(ui,/author_type==='operator'/,'operator messages need their own durable author label');
assert.match(ui,/author_type==='participant'/,'each group participant must be labelled independently');
assert.match(ui,/data-direction="\$\{esc\(m\.direction\)\}"/,'message alignment must use stored direction');
assert.match(ui,/available_actions/,'controls must come from effective server availability');
assert.doesNotMatch(ui,/if\(route\.state==='active'\)add\('pause','pause'\)/,'a blocked active row must not offer Pause');
assert.match(backend,/participant_jid/,'the history API must surface the signed participant JID for diagnostics');
assert.match(backend,/saas_connect_participant_display_names/,'manual participant names need tenant/channel-scoped storage');
assert.match(backend,/manual_display_name/,'manual group and contact names need a persisted override');
assert.match(ui,/dialogs\/display-name/,'the operator UI must save names through the tenant API');
assert.match(l10n,/displayNameHint/,'manual-name guidance remains localized without a permanent editor block');
assert.doesNotMatch(ui,/m\.author_name\|\|m\.participant_jid\|\|t\('groupParticipant'\)/,'a raw participant JID must not be shown as a guessed display name');
assert.match(ui,/operatorEditConversationName/,'contact and group names must be edited from the compact title');
assert.match(ui,/operator-participant-edit/,'group participant editing must be a compact pencil action');
assert.doesNotMatch(ui,/Изменить имя участника/,'the message body must not render the former large participant edit button');
assert.match(ui,/replyMyself/,'an active AI conversation must expose the human handoff as its primary action');
assert.match(ui,/returnAI/,'a human handoff must use a contextual Return AI label');
assert.match(ui,/operator-more-actions/,'secondary controls belong in the compact overflow menu');
assert.match(ui,/data-chat-attachment/,'media stays behind the existing explicit preview control');
assert.doesNotMatch(ui,/\$\{esc\(m\.message_type\)\}<\/small>/,'technical media types must not be rendered in message cards');
assert.match(ui,/@media\(max-width:600px\)/,'the compact dialog has a mobile layout breakpoint');
assert.match(backend,/display_state']='error'/,'job failure must be distinct from a policy block');
for(const code of ['connect_policy_blocked','group_messages_not_allowed','manager_pause','human_handoff'])assert.match(l10n,new RegExp(code),'known reason codes need a localization mapping');
for(const phrase of ['AI остановлен или ограничен','AI dayandırılıb və ya məhdudlaşdırılıb','AI is stopped or restricted'])assert.match(l10n,new RegExp(phrase),'unknown codes need a safe RU/AZ/EN fallback');

const document={documentElement:{lang:''},addEventListener(){},dispatchEvent(){},querySelectorAll(){return[]},getElementById(){return null}};
const window={};
vm.runInNewContext(l10n,{window,document,location:{search:''},localStorage:{getItem(){return null},setItem(){}},NodeFilter:{SHOW_TEXT:4},CustomEvent:function(){}});
assert.match(window.tenantI18n.reason('connect_policy_blocked'),/Политика Connect/);
window.tenantI18n.setLanguage('az');assert.match(window.tenantI18n.reason('connect_policy_blocked'),/Connect siyasəti/);
window.tenantI18n.setLanguage('en');assert.match(window.tenantI18n.reason('unknown_provider_code'),/AI is stopped or restricted/);

console.log('PASS: group authors/direction, localized stop reasons, and availability-gated dialogue controls are covered.');
