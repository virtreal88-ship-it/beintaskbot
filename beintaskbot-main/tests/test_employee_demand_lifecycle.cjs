/* Synthetic profile events only. No account, provider, or production requests. */
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const {JSDOM} = require('jsdom');
const script = fs.readFileSync(path.join(__dirname, '../docs/tenant-view-demand.js'), 'utf8');
const initial = {member: {tenant_id: 'a', telegram_id: 1, active: true, role: 'owner', permissions: ['read', 'write'], display_name: 'Owner', updated_at: 'old'}, capabilities: {can_manage_employees: true, pages: {employees: true}, modules: {linear: true}}, role_permissions: {owner: ['read', 'write']}, integrations: [], members: [{telegram_id: 2, display_name: 'Employee', updated_at: 'old'}]};
function fixture({view = 'employees', host = 'staffManagement', visible = true} = {}) {
 const dom = new JSDOM(`<section id="view-${view}" class="${visible ? 'active' : ''}"><div id="${host}"></div></section><div id="staffMemberModal"><input value="original"></div>`, {runScripts: 'outside-only', url: 'https://fixture.test'});
 const w = dom.window; let clock = 1000, resets = 0, loads = 0, prepares = 0, prepared = false;
 w.Date.now = () => clock; w.tenantProfileSnapshot = structuredClone(initial); w.eval(script);
 w.tenantViewDemand.watch({view, host, editable: '#staffMemberModal', prepare: () => {prepares++; prepared = true;}, reset: () => {resets++; prepared = false; w.document.querySelector('input').value = 'reset';}, activate: () => {assert.equal(prepared, true, 'activation requires freshly prepared profile'); loads++;}});
 const emit = (type, detail) => w.document.dispatchEvent(new w.CustomEvent(type, {detail}));
 return {w, dom, emit, counts: () => [resets, loads], prepares: () => prepares, profile: change => {const data = structuredClone(initial); change?.(data); emit('tenant-profile', data);}, dirty: () => {const input = w.document.querySelector('input'); input.value = 'draft'; input.dispatchEvent(new w.Event('input', {bubbles: true}));}, show: value => {w.document.getElementById('view-' + view).classList.toggle('active', value); emit('tenant-view');}, advance: ms => clock += ms};
}
for (const view of ['employees', 'settings']) {
 const f = fixture({view});
 f.emit('tenant-auth-pending'); f.profile();
 assert.deepEqual(f.counts(), [0, 1], 'same-authority revalidation retains clean employee form');
 f.profile(); f.profile(d => {d.member.updated_at = 'new'; d.member.last_login_at = 'now'; d.members[0].updated_at = 'new';});
 f.profile(d => {d.integrations = [{provider: 'linear', status: 'connected', metadata: {settings: {members: {'1': {can_review_news: true}}}}}];});
 f.profile(d => {d.member.permissions.reverse(); d.role_permissions.owner.reverse();});
 assert.deepEqual(f.counts(), [0, 1], 'same profile, timestamps, unrelated integration and permission ordering retain employees');
 f.dirty(); f.emit('tenant-auth-pending'); f.profile();
 assert.deepEqual(f.counts(), [0, 1], 'same-authority revalidation retains dirty employee form');
 f.profile(d => {d.member.display_name = 'New owner'; d.members[0].display_name = 'New employee';});
 assert.deepEqual(f.counts(), [0, 1]); assert.equal(f.w.document.querySelector('input').value, 'draft');
 f.show(false); f.advance(60001); f.show(true);
 assert.deepEqual(f.counts(), [0, 1], 'dirty draft survives expired return');
 f.w.tenantViewDemand.clean('#staffMemberModal'); f.profile(d => {d.members[0].display_name = 'New employee';});
 assert.deepEqual(f.counts(), [1, 2], 'meaningful roster change refreshes once clean');
 setImmediate(() => f.dom.window.close());
}
for (const change of [d => {d.member.tenant_id = 'b';}, d => {d.member.telegram_id = 3;}, d => {d.member.active = false;}, d => {d.member.role = 'worker';}, d => {d.member.permissions = [];}, d => {d.capabilities.can_manage_employees = false;}, d => {d.capabilities.pages.employees = false;}, d => {d.role_permissions.owner = [];}]) {
 const f = fixture(); f.dirty(); f.profile(change);
 assert.equal(f.counts()[0], 1, 'identity and authority changes reset even dirty cards');
 assert.equal(f.w.document.querySelector('input').value, 'reset'); setImmediate(() => f.dom.window.close());
}
{
 const f = fixture({visible: false}); f.profile(d => {d.member.updated_at = 'new';});
 assert.deepEqual(f.counts(), [0, 0]); f.show(true); assert.deepEqual(f.counts(), [0, 1]);
 f.show(false); f.advance(1000); f.show(true); assert.deepEqual(f.counts(), [0, 1], 'warm view retained');
 f.show(false); f.advance(30001); f.show(true); assert.deepEqual(f.counts(), [1, 2], 'expired clean return reloads');
 f.show(false); f.profile(d => {d.members[0].display_name = 'Changed';}); assert.deepEqual(f.counts(), [2, 2], 'hidden changes invalidate without loading');
 f.show(true); assert.deepEqual(f.counts(), [2, 3]); setImmediate(() => f.dom.window.close());
}
{
 const f = fixture({view: 'settings', host: 'otherSettings'}); f.profile(d => {d.integrations = [{provider: 'other'}];});
 assert.deepEqual(f.counts(), [1, 2], 'other demand consumers retain existing integration freshness'); setImmediate(() => f.dom.window.close());
}
for (const [view, host] of [['employees', 'staffManagement'], ['settings', 'otherSettings']]) {
 const f = fixture({view, host});
 assert.equal(f.prepares(), 1);
 f.emit('tenant-auth-invalid');
 assert.deepEqual(f.counts(), [1, 1]);
 f.profile();
 assert.equal(f.prepares(), 2, 'same actor after invalidation must prepare again');
 assert.deepEqual(f.counts(), [2, 2], 'same actor after invalidation resets and activates');
 setImmediate(() => f.dom.window.close());
}
console.log('PASS: employee demand ignores incidental freshness, preserves drafts, refreshes real data and resets authority.');
