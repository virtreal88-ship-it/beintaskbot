/* EMP01 real built /app, actual cookie authority and isolated PostgreSQL HTTP CRUD. */
const fs = require('node:fs');
const assert = require('node:assert/strict');
const { chromium } = require('../.support-test-deps/node_modules/playwright');
const origin = process.env.EMPLOYEE_CARD_RUNTIME_ORIGIN;
const mode = process.env.EMPLOYEE_CARD_RUNTIME_MODE;
const width = Number(process.env.EMPLOYEE_CARD_RUNTIME_WIDTH);
const language = process.env.EMPLOYEE_CARD_RUNTIME_LANGUAGE;
const tenantA = process.env.EMPLOYEE_CARD_RUNTIME_TENANT_A;
const tenantB = process.env.EMPLOYEE_CARD_RUNTIME_TENANT_B;
const login = process.env.EMPLOYEE_CARD_RUNTIME_LOGIN;
const password = 'Synthetic-initial-password!42';
assert(origin && mode && width && login && tenantA && tenantB);

(async () => {
  const executablePath = process.env.CHROMIUM_EXECUTABLE_PATH || (fs.existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined);
  const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}), args: ['--no-sandbox'] });
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: 'Asia/Baku' });
  await context.route(url => ['http:', 'https:'].includes(url.protocol) && url.origin !== origin, route => route.abort());
  const page = await context.newPage(), errors = [], writes = [], bundles = [];
  page.on('pageerror', error => errors.push(error.stack || error.message));
  page.on('dialog', dialog => dialog.accept());
  page.on('response', response => { if (new URL(response.url()).pathname === '/assets/tenant-chakra-ui.js') bundles.push(response.status()); });
  page.on('request', request => {
    if (!['GET', 'HEAD'].includes(request.method())) {
      const data = request.postDataJSON();
      writes.push({ path: new URL(request.url()).pathname, action: data?.action });
    }
  });
  const control = async path => {
    const response = await page.request.get(origin + '/test/' + path);
    assert.equal(response.status(), 200, path);
    return response.json();
  };
  const waitPending = async () => {
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      if ((await control('state')).pending_write) return;
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    assert.fail('Actual member HTTP write did not reach its transport barrier');
  };
  const settleResponse = async response => {
    await response.finished();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  };
  const postResponse = action => page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/platform/members' && response.request().method() === 'POST' &&
    response.request().postDataJSON().action === action);
  const readRows = async request => {
    const response = await request.get(origin + '/api/platform/members');
    assert.equal(response.status(), 200);
    return (await response.json()).members;
  };
  const closed = async () => page.waitForFunction(() => !document.querySelector('#staffMemberModal')?.classList.contains('show'));
  const openEmployee = async actor => {
    await page.locator(`[data-staff-edit="${actor}"]`).click();
    await page.locator('#staffMemberModal.show #staffEditForm').waitFor();
  };
  const save = async (form, action, expected = 200, double = false) => {
    const pending = postResponse(action);
    if (double) await form.locator('[type=submit]').dblclick();
    else await form.locator('[type=submit]').click();
    const response = await pending;
    assert.equal(response.status(), expected);
    return response.json();
  };
  const fillCreate = async (form, username = login, email = username + '@isolated.example') => {
    await form.locator('[name=first_name]').fill('Synthetic');
    await form.locator('[name=last_name]').fill('Employee');
    await form.locator('[name=login]').fill(username);
    await form.locator('[name=email]').fill(email);
    await form.locator('[name=phone]').fill('+994500001234');
    await form.locator('[name=password]').fill(password);
    await form.locator('[name=confirm_password]').fill(password);
  };
  const readonlyIdentity = async (form, values) => {
    for (const key of ['first_name', 'last_name', 'login', 'email', 'phone', 'avatar_url', 'account_status']) {
      assert.equal(await form.locator(`[name=${key}]`).evaluate(node => node.readOnly), true, key);
      assert((await form.locator('#staffFieldHelp-' + key).innerText()).trim(), key + ' has a human read-only reason');
      assert(!((await form.locator('#staffFieldHelp-' + key).innerText()).includes('global_account_identity')));
      if (Object.hasOwn(values, key)) assert.equal(await form.locator(`[name=${key}]`).inputValue(), values[key]);
    }
    assert.equal(await form.locator('input[type=password]').count(), 0, 'A saved employee never exposes a password field');
    assert.equal(await form.locator('a[href="/auth/password/change"]').count(), 0, 'Another employee has no owner password-reset action');
    assert.equal(await form.evaluate(node => node.scrollWidth <= node.clientWidth + 1), true, 'Card fits ' + width);
  };
  try {
    await control('session?identity=initial');
    await page.goto(origin + '/app?view=employees&lang=' + language);
    await page.locator('#view-employees.active #staffContent [data-staff-edit="62"]').waitFor();
    assert.deepEqual(bundles, [200], 'Actual production bundle is loaded');

    if (mode === 'complete') {
      await page.locator('#staffAdd').click();
      let form = page.locator('#staffInviteForm');
      await fillCreate(form);
      await form.locator('[name=confirm_password]').fill('Different-temporary-password!42');
      await form.locator('[type=submit]').click();
      assert((await form.locator('[data-field-error=confirm_password]').innerText()).trim());
      assert.equal((await control('state')).posts, 0);
      assert.equal(await form.locator('[name=login]').inputValue(), login);
      await form.locator('[name=confirm_password]').fill(password);
      const created = await save(form, 'provision_member_account', 200, true);
      assert(created.applied?.created === true && created.applied.card_version);
      const actor = created.applied.telegram_id;
      await closed();
      assert.equal(await page.locator('input[type=password]').evaluateAll(nodes => nodes.every(node => node.value === '')), true);
      await openEmployee(actor);
      form = page.locator('#staffEditForm');
      const identity = { first_name: 'Synthetic', last_name: 'Employee', login,
        email: login + '@isolated.example', phone: '+994500001234' };
      await readonlyIdentity(form, identity);
      await form.locator('[name=display_name]').fill('Canceled local draft');
      await form.locator('[data-staff-cancel]').click();
      await closed();
      assert.equal((await control('state')).posts, 1, 'Cancel sends no mutation');
      assert.equal((await readRows(page.request)).find(row => row.telegram_id === actor).display_name, 'Synthetic Employee');

      await page.locator(`[data-staff-number="${actor}"]`).click();
      form = page.locator('#staffEditForm');
      await form.locator('#staffNumberAdd').click();
      await form.locator('[data-number]').fill('+994501231234');
      const whatsapp = await save(form, 'update_member_card');
      assert.notEqual(whatsapp.applied.card_version, created.applied.card_version);
      await closed();
      await openEmployee(actor);
      form = page.locator('#staffEditForm');
      await form.locator('[name=display_name]').fill('Local employee alias');
      await form.locator('[name=role]').selectOption('manager');
      await form.locator('[data-permission][value=support]').check();
      await form.locator('[name=active]').uncheck();
      const updated = await save(form, 'update_member_card');
      assert.notEqual(updated.applied.card_version, whatsapp.applied.card_version);
      await closed();
      await page.reload();
      await openEmployee(actor);
      form = page.locator('#staffEditForm');
      await readonlyIdentity(form, identity);
      assert.equal(await form.locator('[name=display_name]').inputValue(), 'Local employee alias');
      assert.equal(await form.locator('[name=role]').inputValue(), 'manager');
      assert.equal(await form.locator('[name=active]').isChecked(), false);
      assert.equal(await form.locator('[data-permission][value=support]').isChecked(), true);
      assert((await form.innerText()).includes('+994501231234'), 'Explicit WA binding is separate from contact phone');
      await page.evaluate(() => window.tenantReloadProfile());
      await page.keyboard.press('Escape');
      await closed();
      await page.waitForFunction(actor => document.activeElement?.dataset.staffEdit === String(actor), actor);

      await page.locator('#staffAdd').click();
      form = page.locator('#staffInviteForm');
      await fillCreate(form, login, 'different.' + login + '@isolated.example');
      await save(form, 'provision_member_account', 409);
      await form.locator('[data-field-error=login]').filter({ hasText: /\S/ }).waitFor();
      assert.equal(await form.locator('[name=login]').inputValue(), login);
      assert.equal(await form.locator('[name=password]').inputValue(), password, 'POST error retains only the current form input');
      await form.locator('[data-staff-cancel]').click();
      await closed();
      assert.equal(await page.locator('input[type=password]').evaluateAll(nodes => nodes.every(node => node.value === '')), true);
      assert.equal((await control('state')).posts, 4, 'One create, one WA update, one local update and one rejected duplicate');
    } else if (mode === 'conflict') {
      await openEmployee(62);
      const form = page.locator('#staffEditForm');
      await form.locator('[name=display_name]').fill('First editor unsaved alias');
      const editor = await browser.newContext();
      try {
        await editor.request.get(origin + '/test/session?identity=editor');
        const row = (await readRows(editor.request)).find(row => row.telegram_id === 62);
        const response = await editor.request.post(origin + '/api/platform/members', { headers: { Origin: origin }, data: {
          action: 'update_member_card', expected_tenant_id: tenantA, expected_user_id: 38,
          telegram_id: 62, expected_version: row.card_version, changes: { display_name: 'Second editor applied alias' }
        } });
        assert.equal(response.status(), 200);
      } finally { await editor.close(); }
      await save(form, 'update_member_card', 409);
      await form.locator('#staffReviewLatest').waitFor();
      assert.equal(await form.locator('[name=display_name]').inputValue(), 'First editor unsaved alias');
      assert.equal((await control('state')).posts, 2, 'Conflict has no automatic token retry');
      await form.locator('#staffReviewLatest').click();
      await page.waitForFunction(() => document.querySelector('#staffEditForm [name=display_name]')?.value === 'Second editor applied alias');
      await page.locator('#staffEditForm [type=submit]').click();
      assert.equal((await control('state')).posts, 2, 'Reviewing the latest card does not save it again');
      await page.locator('#staffEditForm [name=display_name]').fill('Reviewed final alias');
      await save(page.locator('#staffEditForm'), 'update_member_card');
      await closed();
      assert.equal((await control('state')).posts, 3);
    } else {
      await openEmployee(62);
      await page.locator('#staffEditForm [name=display_name]').fill('Applied A alias');
      const pending = postResponse('update_member_card');
      await page.locator('#staffEditForm [type=submit]').click();
      if (mode === 'postwrite_read_denied') {
        const response = await pending;
        assert.equal(response.status(), 200);
        assert((await response.json()).applied.card_version);
        await page.locator('#staffSaveStatus').filter({ hasText: /сохран|saved|yadda/i }).waitFor();
        await closed();
        const retry = page.locator('#staffRetry');
        if (await retry.isVisible()) await retry.click();
        assert.equal((await control('state')).posts, 1, 'An applied receipt followed by denied GET never resends the mutation');
      } else {
        await waitPending();
        if (mode === 'revoked') await control('revoke');
        else await control('session?identity=company_b');
        await page.evaluate(() => window.tenantRequestCoordinator.revalidateProfile());
        if (mode === 'switch_after_write') {
          await page.waitForFunction(tenant => window.tenantProfileSnapshot?.member?.tenant_id === tenant, tenantB);
          await openEmployee(62);
          await page.locator('#staffEditForm [name=display_name]').fill('B private unsaved draft');
        } else {
          await page.locator('#employeesAccessNotice').waitFor();
          assert.equal(await page.locator('#staffMemberModal.show').count(), 0);
        }
        await control('release');
        const response = await pending;
        assert.equal(response.status(), mode === 'revoked' ? 403 : 200);
        await settleResponse(response);
        if (mode === 'switch_after_write') {
          assert.equal(await page.locator('#staffEditForm [name=display_name]').inputValue(), 'B private unsaved draft');
          assert.equal(await page.locator('#staffMemberModal.show').count(), 1);
          assert(!((await page.locator('#staffModalBody').textContent()).includes('Applied A alias')));
        } else {
          assert.equal(await page.locator('#staffMemberModal.show').count(), 0);
          assert(!((await page.locator('#staffManagement').textContent()).includes('A simple worker')));
          assert.equal(await page.locator('#companyEmployeesNav').isVisible(), false);
        }
        assert.equal((await control('state')).posts, 1);
      }
    }
    assert.equal(await page.evaluate(value =>
      JSON.stringify([localStorage, sessionStorage, location.href]).includes(value), password), false, 'No password is persisted in browser storage or URL');
    assert(writes.every(write => write.path === '/api/platform/members' &&
      ['update_member_card', 'provision_member_account'].includes(write.action)), JSON.stringify(writes));
    assert.deepEqual(errors, [], 'Actual app has no uncaught errors');
    console.log(`PASS EMP01 actual PostgreSQL→HTTP→built /app: ${mode}, ${width}px, ${language}; scoped CRUD, receipt/version/errors, current authority and no external actions`);
  } finally { await context.close(); await browser.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
