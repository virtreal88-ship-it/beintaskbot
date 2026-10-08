const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'docs/index.html'), 'utf8');
const js = fs.readFileSync(path.join(root, 'docs/deal-tags.js'), 'utf8');
// Parse every inline script, including the legacy editor handlers.
for(const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
const context = vm.createContext({
    document:{createElement:()=>({}), head:{appendChild() {}}},
    escapeHtml:value=>String(value).replace(/[&<>"']/g, char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]))
});
vm.runInContext(js, context);
assert.match(context.dealTagsHtml({tags:[{id:1,name:'<img onerror="bad">'}]}), /&lt;img/);
assert.equal(context.dealTagsHtml({}), '');
assert.doesNotMatch(context.dealTagsDetailsHtml({id:4,tags:[]}, true), /onclick/);
assert.match(context.dealTagsDetailsHtml({id:4,tags:[]}, false), /openDealTagsEditor\('',4\)/);
assert.match(context.dealTagsFieldHtml('create-deal-tags'), /data-tags="\[\]"/);
assert.doesNotMatch(html, /create-deal-partner|function partnerFieldHtml|function addPartnerOption/);
assert.match(html, /body: JSON.stringify\(\{action:'create_deal'.*note, tags,/);
assert.match(html, /deal-tags-row">\$\{dealTagsHtml\(deal\)\}/);
assert.match(js, /role="dialog" aria-modal="true"/);
assert.match(js, /token !== epoch/);
assert.match(js, /...Object.values\(samilDealsByStage/);
console.log('Deal tags: syntax, chips, escaping, read-only, creation and kanban checks passed');
