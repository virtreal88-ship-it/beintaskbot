const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const read=file=>fs.readFileSync(path.join(__dirname,'..',file),'utf8');
const ui=read('docs/tenant-news.js'),settings=read('docs/tenant-news-telegram-settings.js');new vm.Script(ui);new vm.Script(settings);
assert.match(settings,/expected_tenant_id:profile\.tenant_id/);assert.match(settings,/expected_updated_at:version/);
assert.match(settings,/current!==epoch/);assert.match(settings,/type="checkbox"/);assert.doesNotMatch(settings,/sendMessage|beinecosystems/);
assert.match(ui,/confirm_telegram:\$\('tnTelegramConfirm'\)\?\.checked===true/);assert.match(ui,/channel_version:channel\?\.binding_version/);
assert.match(ui,/data-cancel-telegram/);assert.match(ui,/Nəticə naməlum/);assert.match(ui,/data\.telegram_operation\?'\/telegram'/);
assert.match(read('docs/tenant-app.html'),/tenant-news-telegram-settings\.js/);
console.log('PASS: owner channel settings, per-news confirmation, cancellation and unknown-result UI are explicit and tenant-scoped.');
