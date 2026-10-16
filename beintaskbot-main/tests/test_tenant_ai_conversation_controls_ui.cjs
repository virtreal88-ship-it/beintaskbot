const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'docs', 'tenant-ai-agents.js'), 'utf8');

assert.match(source, /AI заблокирован/, 'the chat and dialogue list must expose a blocked AI state');
assert.match(source, /resume_allowed/, 'the UI must use the server-provided Resume permission');
assert.match(source, /Resume недоступен/, 'mandatory blocks must be explained instead of offering Resume');
assert.match(source, /Клиент:/, 'each dialogue row must identify the customer');
assert.match(source, /Канал:/, 'each dialogue row must identify the WhatsApp channel');
assert.match(source, /data-open-ai-dialog/, 'each routable dialogue must expose a chat-open action');
assert.match(source, /обработает только следующие новые сообщения клиента/, 'Resume must explain that old messages are not replayed');

console.log('PASS: AI dialogue UI renders state, reason, Resume eligibility, and chat entry point.');
