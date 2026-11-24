// The legacy Linear screen is retired; current Linear UI behavior is covered
// by test_tenant_linear_board.cjs against the active /app implementation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
assert.equal(fs.existsSync(path.join(root, 'docs', 'index.html')), false,
    'the old bundled WebApp must not be shipped');
const bot = fs.readFileSync(path.join(root, 'bot.py'), 'utf8');
assert.match(bot, /async def serve_webapp\(_request: web\.Request\)[\s\S]*?HTTPFound\("\/app"\)/,
    'old /webapp bookmarks redirect to the current app');
assert.match(bot, /app_web\.router\.add_get\("\/app", auth_api\["app_page"\]\)/,
    'the active tenant app route remains registered');
assert.match(bot, /app_web\.router\.add_get\("\/webapp", serve_webapp\)/,
    'the legacy URL remains a compatibility redirect');
console.log('PASS: legacy WebApp is not shipped; /webapp redirects to /app and current app routes remain active.');
