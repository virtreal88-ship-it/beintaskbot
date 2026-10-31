const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const ui = fs.readFileSync(path.join(root, 'docs', 'tenant-ai-agents.js'), 'utf8');
const app = fs.readFileSync(path.join(root, 'docs', 'tenant-app.html'), 'utf8');
const routes = fs.readFileSync(path.join(root, 'tenant_web_routes.py'), 'utf8');
const api = fs.readFileSync(path.join(root, 'tenant_ai_agents_api.py'), 'utf8');
const inbox = fs.readFileSync(path.join(root, 'docs', 'tenant-support-inbox.js'), 'utf8');
const i18n = fs.readFileSync(path.join(root, 'docs', 'tenant-operator-i18n.js'), 'utf8');

assert.match(ui, /const resume=\$\('aiPilotResume'\)/, 'pilot preflight must expose its exact-scope preparation control');
assert.match(ui, /continuation_renewal/,'renewal UI is driven by server verified open CASE continuation state');
assert.match(ui, /KWID təsdiqini yenilə — eyni CASE davamı/,'expired attestation action is labeled as same CASE renewal');
assert.match(ui, /Eyni CASE davamını 15 dəqiqəlik təsdiqlə/,'owner action is distinguished from an initial pilot run');
assert.match(ui, /resume\.hidden=profile\?\.role!=='owner'.*!p\.scope.*!preparationOnly/,
  'only the owner can see preparation resume, and both pilot guards must be off');
assert.match(ui, /preparationOnly=live\.enabled===false&&p\.pilot_guard_enabled===false/,
  'the UI must require both pilot guards to be off');
assert.match(ui, /live\.developer_group_route==='verified'/,
  'preparation resume must wait until the configured test group exists on the selected channel');
assert.match(ui, /api\('agents\/pilot\/resume','POST',\{confirmed:true\}\)/,
  'the visible control must call the server owner-checked pilot action');
assert.match(app, /id="aiPilotResume"[\s\S]*id="aiPilotReset"[\s\S]*Yazışma, fayl və bilik saxlanır/,
  'the panel must explain that reset preserves history, files, and knowledge');
assert.match(routes, /agents\/pilot\/resume'.*'pilot_resume'.*\('POST',\)/,
  'the exact pilot preparation action must be registered as a POST route');
assert.doesNotMatch(ui.slice(ui.indexOf("api('agents/pilot/resume'"), ui.indexOf("api('agents/pilot/kwid-confirmation'")),
  /settings|mode|auto_send/, 'preparation resume must not change company or agent mode settings');
assert.match(app, /id="aiPilotReset"/,'pilot panel must expose the scoped reset action');
assert.match(ui, /reset\.hidden=profile\?\.role!=='owner'\|\|!p\.scope/,
  'only the configured-scope owner can see the reset action');
assert.match(ui, /api\('agents\/pilot\/reset','POST',\{confirmed:true,reset_id:crypto\.randomUUID\(\)\}\)/,
  'reset action must use the dedicated confirmation endpoint');
assert.match(routes, /agents\/pilot\/reset'.*'pilot_reset'.*\('POST',\)/,
  'reset endpoint must be registered only as POST');
assert.match(api, /pilot_reset' and method == 'POST'[\s\S]*reset_pilot_test_dialog/,
  'reset dispatch must call the owner-checked server operation');
assert.match(inbox, /completion_outcome==='test_stopped'\?t\('testStoppedUnresolved'\)/,
  'test-stopped cases must remain visibly unresolved in the support inbox');
assert.match(i18n, /testStoppedUnresolved:\['Тест остановлен; проблема остаётся нерешённой','Test dayandırılıb; problem həll olunmayıb','Test stopped; issue remains unresolved'\]/,
  'test-stopped label must never imply customer resolution');

console.log('PASS: exact pilot preparation resume is owner-only, guard-off, and preserves Manual mode.');
