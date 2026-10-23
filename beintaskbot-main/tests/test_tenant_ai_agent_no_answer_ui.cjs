const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'docs', 'tenant-app.html'), 'utf8');
const js = fs.readFileSync(path.join(root, 'docs', 'tenant-ai-agents.js'), 'utf8');

assert.match(html, /id="aiNoAnswerEnabled"/);
for (const action of ['stop', 'handoff', 'create_task', 'clarify', 'call_expert']) {
  assert.match(html, new RegExp(`<option value="${action}">`), `Missing no-answer action: ${action}`);
}
assert.match(html, /id="aiNoAnswerFallback"[\s\S]*?value="stop"[\s\S]*?value="handoff"/);
assert.match(html, /id="aiNoAnswerLimit"[\s\S]*?value="1"[\s\S]*?value="2"[\s\S]*?value="3"/);
assert.match(js, /response_policy:\{enabled:\$\('aiNoAnswerEnabled'\)\.checked,action:/,
  'Agent save must persist the selected no-answer policy.');
assert.match(js, /responsePolicy\.enabled===true/,
  'Existing agents must remain opt-out until explicitly enabled.');
for (const lang of ['ru', 'az', 'en']) {
  assert.match(js, new RegExp(`${lang}:\\{title:`), `Missing ${lang} translation for the policy section.`);
}
assert.match(js, /No-answer policy is invalid|aiNoAnswerAction/);

console.log('PASS: no-answer editor options, safe defaults, persistence, and RU/AZ/EN labels.');
