const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const tick = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
const response = data => ({ ok: true, json: async () => structuredClone(data) });
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
async function setup() {
  const dom = new JSDOM('<!doctype html><div id="integrationSettingsHost"></div>', { url: 'https://crm.example.test/app', runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window, d = w.document, requests = [];
  let settings = [
    { entity_type: 'contact', provider: 'kommo', sync_direction: 'from_external', allowed_fields: ['name', 'phone'] },
    { entity_type: 'deal', provider: 'beinsystems', sync_direction: 'off', allowed_fields: ['title', 'description'] },
  ];
  let member = { telegram_id: 20, display_name: 'Huseyn', active: true, role: 'worker', card_version: 'card-1', kommo_settings: { version: 'scope-1', pipelines: [{ pipeline_id: '10', name: 'Support' }, { pipeline_id: '11', name: 'Sales' }], values: { pipeline_ids: ['10'], kommo_user_id: '42' } } };
  const p = { member: { tenant_id: 'tenant-a', telegram_id: 1, role: 'owner', active: true }, capabilities: { modules: {} } };
  const hooks = {};
  w.tenantProfileSnapshot = p;
  w.tenantI18n = { language: 'ru' };
  w.tenantSharedIntegrations = { scoped: () => false, can: () => true, connected: () => true, command: (_a, _b, data) => data, record() {} };
  w.fetch = async (url, options = {}) => {
    const request = { url, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null };
    requests.push(request);
    const hook = hooks[request.method + ' ' + url];
    if (hook) return hook(request);
    if (url === '/api/platform/integrations/kommo/test') return response({ success: true, auth_mode: 'oauth', account: { name: 'Example' } });
    if (url === '/api/platform/members') {
      if (request.method === 'POST') {
        Object.assign(member.kommo_settings.values, request.body.changes.kommo_settings.changes);
        member.card_version = 'card-2'; member.kommo_settings.version = 'scope-2';
        return response({ success: true, applied: { telegram_id: 20 } });
      }
      return response({ success: true, members: [member] });
    }
    if (url === '/api/platform/crm/external-sync') {
      if (request.method === 'POST') settings = request.body.settings;
      return response({ success: true, result: { settings, integrations: { kommo: 'connected' }, outbox: [], conflicts: [] } });
    }
    throw new Error('Unexpected request ' + request.method + ' ' + url);
  };
  for (const file of ['tenant-view-demand.js', 'tenant-crm-sync.js', 'tenant-kommo-settings.js']) w.eval(fs.readFileSync(path.join(__dirname, '../docs', file), 'utf8'));
  const event = (type, detail) => d.dispatchEvent(new w.CustomEvent(type, { detail }));
  event('tenant-profile', p);
  const open = () => event('tenant-integration-panel', { open: true, provider: 'kommo' });
  const close = () => event('tenant-integration-panel', { open: false, provider: 'kommo' });
  open(); await tick();
  return { dom, w, d, requests, hooks, p, event, open, close };
}
(async () => {
  const s = await setup();
  try {
    const { d, w, requests } = s;
    const panel = d.querySelector('#tenantKommoPanel');
    assert(panel?.contains(d.querySelector('#externalCrmSyncPanel')), 'sync controls mount inside Kommo');
    const contact = () => d.querySelector('[data-entity="contact"]');
    assert.equal(contact()?.querySelector('[data-direction]')?.value, 'from_external', 'saved sync direction is rendered');
    assert(contact().querySelector('[data-field="phone"]').checked, 'saved allowed fields are rendered');
    assert.match(d.querySelector('#externalCrmSyncPanel').textContent, /Настройте направление обмена/, 'description is explanatory, not the field label');
    const employee = () => d.querySelector('[data-kommo-employee="20"]');
    assert(employee(), 'employee is loaded inside integration');
    assert.equal(employee().querySelector('[data-kommo-pipelines]').selectedOptions[0].value, '10');
    employee().querySelector('[data-kommo-user-id]').value = '43';
    employee().querySelector('[data-kommo-save]').click(); await tick();
    const savedMember = requests.find(r => r.url === '/api/platform/members' && r.method === 'POST');
    assert.deepEqual(savedMember.body, { action: 'update_member_card', telegram_id: 20, expected_tenant_id: 'tenant-a', expected_user_id: 1, expected_version: 'card-1', changes: { kommo_settings: { expected_version: 'scope-1', changes: { kommo_user_id: '43' } } } });
    assert.equal(employee().querySelector('[data-kommo-user-id]').value, '43', 'updated member receipt is reloaded');
    contact().querySelector('[data-direction]').value = 'bidirectional';
    contact().querySelector('[data-field="email"]').checked = true;
    w.tenantI18n.language = 'en'; s.event('tenant-language', {});
    assert.equal(contact().querySelector('[data-direction]').value, 'bidirectional', 'language change preserves draft');
    assert(contact().querySelector('[data-field="email"]').checked);
    d.querySelector('#externalCrmSyncSave').click(); await tick();
    const savedSync = requests.find(r => r.url === '/api/platform/crm/external-sync' && r.method === 'POST');
    assert(savedSync, 'sync save issues a POST');
    assert.deepEqual(savedSync.body.settings[0], { entity_type: 'contact', provider: 'kommo', sync_direction: 'bidirectional', mode: 'selective', primary_provider: 'kommo', allowed_fields: ['name', 'phone', 'email'] });
    assert.equal(savedSync.body.settings[1].provider, 'beinsystems', 'unmodified provider retained');
    assert.equal(savedSync.body.settings[1].sync_direction, 'off', 'unmodified disabled policy retained');
    assert.equal(d.querySelector('#externalCrmSyncSave').disabled, false, 'save completes with controls usable for a second edit');

    // A pending old panel save must not reload or mutate a newly opened panel.
    const waitSave = deferred();
    s.hooks['POST /api/platform/crm/external-sync'] = () => waitSave.promise;
    const postsBefore = requests.filter(r => r.url === '/api/platform/crm/external-sync' && r.method === 'POST').length;
    d.querySelector('#externalCrmSyncSave').click(); await tick();
    assert.equal(requests.filter(r => r.url === '/api/platform/crm/external-sync' && r.method === 'POST').length, postsBefore + 1, 'second save actually starts');
    d.querySelector('#externalCrmSyncSave').click(); await tick();
    assert.equal(requests.filter(r => r.url === '/api/platform/crm/external-sync' && r.method === 'POST').length, postsBefore + 1, 'repeated clicks do not duplicate a pending save');
    s.close(); s.open(); await tick();
    const newPanel = d.querySelector('#externalCrmSyncPanel');
    const readCount = requests.filter(r => r.url === '/api/platform/crm/external-sync' && r.method === 'GET').length;
    waitSave.resolve(response({ success: true })); await tick();
    assert.equal(requests.filter(r => r.url === '/api/platform/crm/external-sync' && r.method === 'GET').length, readCount, 'late save does not reload the new panel');
    assert.equal(d.querySelector('#externalCrmSyncPanel'), newPanel);
    assert.equal(d.querySelectorAll('#externalCrmSyncPanel').length, 1, 'close/reopen does not duplicate sync controls');

    // Standard profile refresh must retain a usable sync panel and status.
    s.event('tenant-profile', structuredClone(s.p)); await tick();
    assert(d.querySelector('[data-entity="contact"] [data-direction]'), 'profile refresh retains sync settings');
    // A rejected load from a closed panel must not replace a newly loaded employee list.
    const oldMembers = deferred();
    s.close(); s.hooks['GET /api/platform/members'] = () => oldMembers.promise; s.open(); await tick();
    s.close(); delete s.hooks['GET /api/platform/members']; s.open(); await tick();
    oldMembers.resolve(response({ success: false, error: 'old members request failed' })); await tick();
    assert(d.querySelector('[data-kommo-employee="20"]'), 'stale failed members load does not overwrite reopened employee controls');
    assert(!d.querySelector('#tkEmployees').textContent.includes('old members request failed'));
    const readsBeforeWorker = requests.filter(r => r.url === '/api/platform/members' || r.url === '/api/platform/crm/external-sync').length;
    s.event('tenant-profile', { member: { tenant_id: 'tenant-b', telegram_id: 2, role: 'worker', active: true }, capabilities: { modules: {} } }); await tick();
    assert.equal(d.querySelector('#tenantKommoPanel'), null, 'owner settings are removed after switching to worker');
    s.open(); await tick();
    assert.equal(requests.filter(r => r.url === '/api/platform/members' || r.url === '/api/platform/crm/external-sync').length, readsBeforeWorker, 'worker does not request owner employee or synchronization settings');
    console.log('PASS: Kommo relocation runtime loads/saves employees and sync policies, preserves drafts, and ignores stale close/reopen saves.');
  } finally { s.dom.window.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
