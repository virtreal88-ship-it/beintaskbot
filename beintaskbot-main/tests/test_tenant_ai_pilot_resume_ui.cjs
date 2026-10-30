const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const ui = fs.readFileSync(path.join(root, 'docs', 'tenant-ai-agents.js'), 'utf8');
const app = fs.readFileSync(path.join(root, 'docs', 'tenant-app.html'), 'utf8');
const routes = fs.readFileSync(path.join(root, 'tenant_web_routes.py'), 'utf8');

assert.match(ui, /const resume=\$\('aiPilotResume'\)/, 'pilot preflight must expose its exact-scope preparation control');
assert.match(ui, /resume\.hidden=profile\?\.role!=='owner'.*!p\.scope.*!preparationOnly/,
  'only the owner can see preparation resume, and both pilot guards must be off');
assert.match(ui, /preparationOnly=live\.enabled===false&&p\.pilot_guard_enabled===false/,
  'the UI must require both pilot guards to be off');
assert.match(ui, /live\.developer_group_route==='verified'/,
  'preparation resume must wait until the configured test group exists on the selected channel');
assert.match(ui, /api\('agents\/pilot\/resume','POST',\{confirmed:true\}\)/,
  'the visible control must call the server owner-checked pilot action');
assert.match(app, /id="aiPilotResume"[\s\S]*agent Manual qalır[\s\S]*əvvəlki mesajlar təkrar emal olunmur/,
  'the action must explain that it preserves Manual and does not replay old messages');
assert.match(routes, /agents\/pilot\/resume'.*'pilot_resume'.*\('POST',\)/,
  'the exact pilot preparation action must be registered as a POST route');
assert.doesNotMatch(ui.slice(ui.indexOf("api('agents/pilot/resume'"), ui.indexOf("api('agents/pilot/kwid-confirmation'")),
  /settings|mode|auto_send/, 'preparation resume must not change company or agent mode settings');

console.log('PASS: exact pilot preparation resume is owner-only, guard-off, and preserves Manual mode.');
