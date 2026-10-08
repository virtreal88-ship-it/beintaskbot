// Static integration contracts, not a visual/browser verification.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const script=fs.readFileSync(path.join(root,'docs','tenant-linear-settings.js'),'utf8');
const app=fs.readFileSync(path.join(root,'docs','tenant-app.html'),'utf8');
assert.match(app,/\/assets\/tenant-linear-settings\.js/);
assert.match(script,/next\.role!=='owner'/);
assert.match(script,/current!==generation/);
assert.match(script,/expected_tenant_id:profile\.tenant_id/);
assert.match(script,/expected_user_id:profile\.telegram_id/);
assert.match(script,/expected_updated_at:snapshot\.updated_at/);
assert.match(script,/type="password" autocomplete="new-password"/);
assert.match(script,/\$\('tlKey'\)\.value=''/);
assert.match(script,/esc\(member\.display_name/);
assert.match(script,/result\.teams\.pageInfo\.hasNextPage/);
assert.doesNotMatch(script,/setInterval|localStorage|sessionStorage|\/api\/linear|mutation/i);
console.log('Tenant Linear settings contracts passed');
