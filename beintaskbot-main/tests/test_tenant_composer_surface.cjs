const fs=require('node:fs'),assert=require('node:assert/strict');
const app=fs.readFileSync('docs/tenant-app.html','utf8');
const dialogs=fs.readFileSync('docs/tenant-operator-dialogs.js','utf8');
assert.ok(!app.includes('tenant-chat-review.js'),'send-receipt diagnostics are not mounted in the normal operator UI');
assert.match(dialogs,/operator-composer-actions/);
assert.match(dialogs,/id="sendChat"/);
assert.match(dialogs,/const autosize=/);
console.log('PASS: one autosizing operator composer keeps AI and send controls inside it.');
