const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const vm=require('node:vm');

const source=fs.readFileSync(path.join(__dirname,'../docs/tenant-support-inbox.js'),'utf8');

const helpers=source.split('\n').filter(line=>/^  (const safeIdentityName=|function displayName\()/.test(line)).join('\n');
const displayName=vm.runInNewContext(helpers+'\ndisplayName',{t:()=> 'Клиент'});
assert.equal(displayName({contact_name:'  Учебный клиент  '}),'Учебный клиент');
assert.equal(displayName({contact_name:'120363411822115409@g.us',phone:'+15550001111'}),'Клиент');
assert.equal(displayName({contact_name:'123456789@lid'}),'Клиент');
assert.equal(displayName({conversation_id:'120363411822115409@g.us'}),'Клиент');
assert.equal(displayName({phone:'+15550001111'}),'Клиент','unverified phone is not a display name');
assert.match(source,/esc\(displayName\(ticket\)\)/,
  'both list and detail must render the normalized display name');
assert.doesNotMatch(source,/contact_name\|\|ticket\.phone\|\|ticket\.conversation_id/,
  'a JID/conversation identifier must never become the visible support name');

console.log('PASS: approved display name or localized fallback; no transport IDs or unverified phone fallback.');
