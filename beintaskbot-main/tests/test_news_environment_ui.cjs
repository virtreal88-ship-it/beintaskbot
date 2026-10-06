const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../docs/index.html'), 'utf8');
const news = fs.readFileSync(path.join(__dirname, '../docs/news.html'), 'utf8');
const escapeHtml = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const start = html.indexOf('function linearNewsReviewCard(');
const end = html.indexOf('\nasync function ', start);
const ctx = {escapeHtml};
vm.runInNewContext(html.slice(html.indexOf('function formatLinearTimestamp('), html.indexOf('function linearBoardCardHtml(')), ctx);
vm.runInNewContext(html.slice(start, end), ctx);
assert.match(ctx.linearNewsReviewCard({environment:'BETA'}), /Mühit:<\/b> BETA/);
assert.match(ctx.linearNewsReviewCard({environment:'<script>'}), /&lt;script&gt;/);
assert.match(ctx.linearNewsReviewCard({}), /Mühit:<\/b> Göstərilməyib/);
const state = {}, list = {};
const publicCtx = {Intl, Date, document:{getElementById:id=>id==='state'?state:list, querySelectorAll:()=>[]},
  fetch:async()=>({ok:true,json:async()=>({success:true,items:[{environment:'ONLINE',title:'Title',summary:'Summary'}]})})};
const scripts = [...news.matchAll(/<script>([\s\S]*?)<\/script>/g)];
vm.runInNewContext(scripts[0][1], publicCtx);
setImmediate(()=>{
  assert.match(list.innerHTML, /Mühit:<\/b> ONLINE/);
  console.log('PASS: environment is visible and escaped in review and public news cards.');
});
