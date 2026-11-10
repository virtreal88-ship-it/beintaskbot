/* Current app/router/coordinator/demand/member modules, isolated HTTP only.
   A synthetic clock advances the real lifecycle beyond its stale-view TTL. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require('jsdom');
const {nodeHtml, installCompanyEmployeesFixture} = require('./company_employees_entry_fixture.cjs');
const settle = async () => { for (let n = 0; n < 16; n++) await new Promise(resolve => setImmediate(resolve)); };
const memberReads = b => b.f.calls.filter(call => call.path === '/api/platform/members').length;
const profileReads = b => b.f.calls.filter(call => call.path === '/api/platform/me').length;
const staff = b => b.d.getElementById('staffManagement');
const modal = b => b.d.getElementById('staffMemberModal');

async function mount() {
  let now = 1000;
  const errors = [], timers = [], vc = new VirtualConsole();
  vc.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM(nodeHtml(), {
    url: 'https://fixture.test/app?view=employees&lang=en', runScripts: 'dangerously',
    pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.Date.now = () => now;
      w.Response = Response; w.Headers = Headers; w.Request = Request;
      w.matchMedia = () => ({matches: false, addEventListener() {}, removeEventListener() {}});
      w.eval('(' + installCompanyEmployeesFixture.toString() + ')({role:"owner"})');
    },
  });
  const w = dom.window, d = w.document;
  await settle();
  // Include the actual global minute poll, without waiting a real minute.
  w.setInterval = (callback, delay) => { timers.push({callback, delay}); return timers.length; };
  w.eval(fs.readFileSync(path.join(__dirname, '../docs/tenant-dialog-consultations.js'), 'utf8'));
  await settle();
  const b = {w, d, f: w.companyEmployeesFixture, errors, advance: ms => now += ms,
    pollMinute: () => timers.find(timer => timer.delay === 60000).callback(), close: () => w.close()};
  assert(d.querySelector('[data-staff-edit="22"]'), 'real employee controller loaded');
  assert.equal(memberReads(b), 1);
  assert.equal(timers.filter(timer => timer.delay === 60000).length, 1);
  return b;
}

function openCard(b, dirty = false) {
  const opener = b.d.querySelector('[data-staff-edit="22"]');
  opener.focus(); opener.click();
  const form = b.d.getElementById('staffEditForm');
  const input = form.elements.namedItem('display_name');
  if (dirty) {
    input.value = 'Unsaved employee draft';
    input.dispatchEvent(new b.w.Event('input', {bubbles: true}));
  }
  input.focus(); input.setSelectionRange(2, 5);
  const panel = form.querySelector('[data-staff-pane="details"]');
  panel.scrollTop = 37;
  return {opener, form, input, panel, content: b.d.getElementById('staffContent'), value: input.value};
}

function assertRetained(b, card) {
  assert.equal(b.d.getElementById('staffContent'), card.content, 'list DOM is not replaced');
  assert.equal(b.d.querySelector('[data-staff-edit="22"]'), card.opener, 'original opener survives');
  assert.equal(b.d.getElementById('staffEditForm'), card.form, 'original card DOM survives');
  assert.equal(modal(b).classList.contains('show'), true);
  assert.equal(modal(b).hasAttribute('inert'), false);
  assert.equal(modal(b).style.display, '');
  assert.equal(staff(b).hidden, false);
  assert.equal(card.input.value, card.value);
  assert.equal(card.panel.scrollTop, 37);
  assert.equal(b.d.activeElement, card.input);
  assert.equal(card.input.selectionStart, 2);
  assert.equal(card.input.selectionEnd, 5);
}

async function reauthorize(b, change = () => {}) {
  change(b.f);
  await b.w.tenantRequestCoordinator.revalidateProfile();
  await settle();
}

(async () => {
  for (const dirty of [false, true]) {
    const b = await mount();
    try {
      const card = openCard(b, dirty), reads = memberReads(b), me = profileReads(b);
      b.advance(60001);
      const notices = b.f.calls.filter(call => call.path === '/api/platform/notifications').length;
      b.pollMinute(); await settle();
      assert.equal(b.f.calls.filter(call => call.path === '/api/platform/notifications').length, notices + 1);
      assert.equal(memberReads(b), reads, 'minute notification poll does not reload employees');
      assert.equal(profileReads(b), me, 'minute notification poll does not reauthorize the app');
      assertRetained(b, card);

      b.f.holdMe = true;
      const pending = b.w.tenantRequestCoordinator.revalidateProfile();
      await settle();
      assert.equal(staff(b).hidden, true, 'employee data remains curtained during access verification');
      assert.equal(modal(b).hasAttribute('inert'), true);
      assert.equal(modal(b).style.display, 'none');
      assert.equal(card.form.isConnected, true, 'curtain retains but conceals the card');
      assert.equal(b.f.pendingMe.length, 1);
      b.f.holdMe = false; b.f.pendingMe.shift()();
      await pending; await settle();
      assert.equal(memberReads(b), reads, 'same-authority resume after a minute does not reload employees');
      assertRetained(b, card);

      b.advance(60001);
      await reauthorize(b);
      assert.equal(memberReads(b), reads, 'repeated access checks do not expire a continuously open card');
      assertRetained(b, card);
      assert.equal(b.f.calls.some(call => call.method !== 'GET'), false);
      assert.deepEqual(b.errors, []);
    } finally { b.close(); }
  }
  console.log('PASS clean/dirty employee DOM, draft, focus, selection and scroll survive minute poll and repeated delayed reauthorization');

  {
    const b = await mount();
    try {
      const card = openCard(b), reads = memberReads(b);
      b.w.tenantAppNavigate('profile', true); await settle();
      b.advance(60001);
      b.w.tenantAppNavigate('employees', true); await settle();
      assert.equal(memberReads(b), reads + 1, 'real stale route return still reloads a clean employee view');
      assert.equal(card.form.isConnected, false);
      assert.equal(modal(b).classList.contains('show'), false);
      assert.deepEqual(b.errors, []);
    } finally { b.close(); }
  }
  console.log('PASS actual route leave and stale return still refresh clean employee data');

  for (const change of ['revoke', 'permissions', 'tenant', 'actor', 'invalid']) {
    const b = await mount();
    try {
      const card = openCard(b, true), reads = memberReads(b);
      b.advance(60001);
      if (change === 'invalid') { b.w.tenantRequestCoordinator.invalidateAuth(); await settle(); }
      else await reauthorize(b, f => {
        if (change === 'revoke') f.profile.capabilities.can_manage_employees = false;
        if (change === 'permissions') f.profile.member.permissions = ['employees:manage', 'tasks'];
        if (change === 'actor') f.profile.member.telegram_id = 33;
        if (change === 'tenant') {
          f.profile.member.tenant_id = '10000000-0000-4000-8000-000000000102';
          f.members = [{telegram_id:44, display_name:'New company employee', role:'worker', active:true}];
        }
      });
      assert.equal(card.form.isConnected, false, change + ' still clears old private editor');
      assert.equal(modal(b).classList.contains('show'), false);
      assert.equal(b.d.getElementById('staffModalBody').textContent, '');
      if (change === 'revoke' || change === 'invalid') {
        assert.equal(staff(b).hidden, true);
        assert.equal(staff(b).textContent, '');
        assert.equal(memberReads(b), reads, 'denied identity cannot reload employee data');
      } else {
        assert.equal(memberReads(b), reads + 1, 'changed authority obtains a fresh authorized list');
        if (change === 'tenant') {
          assert.match(staff(b).textContent, /New company employee/);
          assert.doesNotMatch(staff(b).textContent, /PRIVATE EMPLOYEE A/);
        }
      }
      assert.equal(b.f.calls.some(call => call.method !== 'GET'), false);
      assert.deepEqual(b.errors, []);
    } finally { b.close(); }
  }
  console.log('PASS revoked access, permission/tenant/actor changes and invalid auth still clear old employee state');
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
