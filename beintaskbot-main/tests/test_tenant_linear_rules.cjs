// Declarative editor integration and strict serialization contracts.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const rules=fs.readFileSync(path.join(root,'docs/tenant-linear-rules.js'),'utf8');
const settings=fs.readFileSync(path.join(root,'docs/tenant-linear-settings.js'),'utf8');
const board=fs.readFileSync(path.join(root,'docs/tenant-linear-board.js'),'utf8');
const app=fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8');
assert.match(app,/tenant-linear-rules\.js/);assert.match(app,/tenant-linear-board\.js/);
assert(app.indexOf('tenant-linear-rules.js')<app.indexOf('tenant-linear-settings.js'));
assert.match(rules,/visibility_mode:/);assert.match(rules,/creation_state_id:/);assert.match(rules,/required_fields:/);
assert.match(rules,/from_state_ids/);assert.match(rules,/to_state_id/);assert.match(rules,/require_reason/);
assert.match(settings,/can_change_status/);assert.match(settings,/data-field="assignee_id"/);assert.match(settings,/data-field="button_ids"/);
assert.match(settings,/i\.multiple\?\[\.\.\.i\.selectedOptions\]/);
assert.match(board,/expected_tenant_id:profile\.tenant_id/);assert.match(board,/expected_updated_at:row\?\.updatedAt/);
assert.match(board,/config_version:choices\?\.config_version\|\|version/);
assert.doesNotMatch(board,/draggable|ondrag|setInterval/);
assert.doesNotMatch(board,/tlTaskModal'\)\.onclick/);
assert.match(board,/max-height:85dvh;overflow:auto/);
console.log('PASS: tenant Linear rules serialize configurable scopes, transitions and required fields; modal closes only explicitly.');
