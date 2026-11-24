// The old Linear editing modal and its date shim belonged to the removed
// WebApp. Tenant task screens use their own routes and scripts.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
assert.equal(fs.existsSync(path.join(root, 'docs', 'index.html')), false,
    'the retired WebApp bundle must not be present');
assert.equal(fs.existsSync(path.join(root, 'docs', 'task-date-fields.js')), false,
    'the legacy task-date helper must not be present');
const assets = fs.readFileSync(path.join(root, 'tenant_web_assets.py'), 'utf8');
assert(!assets.includes('/assets/task-date-fields.js'), 'the legacy task-date helper must not be served');
console.log('PASS: removed the legacy task edit modal date helper from the active app asset catalogue.');
