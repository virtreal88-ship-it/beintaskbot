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
  const readonlyIdentity = async (form, values, managed = false) => {
    for (const key of ['first_name', 'last_name', 'login', 'email', 'phone', 'avatar_url', 'account_status']) {
      const editable = managed && ['first_name','last_name','login','phone','avatar_url'].includes(key);
      assert.equal(await form.locator(`[name=${key}]`).evaluate(node => node.readOnly), !editable, key);
      if (!editable) {
        assert((await form.locator('#staffFieldHelp-' + key).innerText()).trim(), key + ' has a human read-only reason');
        assert(!((await form.locator('#staffFieldHelp-' + key).innerText()).includes('global_account_identity')));
      }
      if (Object.hasOwn(values, key)) assert.equal(await form.locator(`[name=${key}]`).inputValue(), values[key]);
    }
    assert.equal(await form.locator('input[type=password]').evaluateAll(nodes=>nodes.every(n=>n.value==='')), true, 'No stored password is exposed');
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
      await readonlyIdentity(form, identity, true);
      await form.locator('[name=display_name]').fill('Canceled local draft');
      await form.locator('[data-staff-cancel]').click();
      await closed();
      assert.equal((await control('state')).posts, 1, 'Cancel sends no mutation');
      assert.equal((await readRows(page.request)).find(row => row.telegram_id === actor).display_name, 'Synthetic Employee');

      await openEmployee(actor);
      form = page.locator('#staffEditForm');
      await form.locator('[name=display_name]').fill('Unsaved local alias');
      identity.email = 'new.' + login + '@isolated.example';
      await form.locator('[name=new_email]').fill(identity.email);
      let pendingAccount = postResponse('assign_member_email');
      await form.locator('#staffAssignEmail').click();
      let responseAccount = await pendingAccount;
      assert.equal(responseAccount.status(), 200, await responseAccount.text());
      await settleResponse(responseAccount);
      assert.equal(await form.locator('[name=display_name]').inputValue(), 'Unsaved local alias', 'Partial email save preserves local draft');
      assert.equal(await form.locator('[name=email]').inputValue(), identity.email);
      const replacement = 'Synthetic-replaced-password!43';
      await form.locator('[name=password]').fill(replacement);
      await form.locator('[name=confirm_password]').fill(replacement);
      pendingAccount = postResponse('assign_member_password');
      await form.locator('#staffAssignPassword').click();
      responseAccount = await pendingAccount;
      assert.equal(responseAccount.status(), 200, await responseAccount.text());
      await settleResponse(responseAccount);
      assert.equal(await form.locator('[name=password]').inputValue(), '');
      assert.equal(await form.locator('[name=display_name]').inputValue(), 'Unsaved local alias');
      assert.equal((await control('state')).posts, 3);
      await form.locator('[data-staff-cancel]').click();
      await closed();

      await page.locator(`[data-staff-number="${actor}"]`).click();
      form = page.locator('#staffEditForm');
      await form.locator('#staffNumberAdd').click();
      await form.locator('[data-number]').fill('+994501231234');
      const whatsapp = await save(form, 'update_member_card');
      assert.notEqual(whatsapp.applied.card_version, created.applied.card_version);
      await closed();
      await openEmployee(actor);
      form = page.locator('#staffEditForm');
      // Native operation overrides do not rewrite integration grants.
      const beforeAccess=(await readRows(page.request)).find(row=>row.telegram_id===actor);
      assert.deepEqual(beforeAccess.permissions,[],'Creation starts with explicitly empty grants');
      assert.equal(beforeAccess.access_settings.actions.tasks.view,false,'First native access save is a real change');
      await form.locator('[data-staff-pane-tab=rights]').click();
      await form.locator('[data-access-action=view][data-access-module=tasks]').check();
      await save(form,'update_member_card');await closed();
      const afterActions=(await readRows(page.request)).find(row=>row.telegram_id===actor);
      assert.deepEqual(afterActions.permissions,beforeAccess.permissions,'Native CRUD does not rewrite legacy/provider grants');
      assert.equal(afterActions.access_settings.actions.tasks.view,true);
      await openEmployee(actor);form=page.locator('#staffEditForm');
      await form.locator('[data-staff-pane-tab=settings]').click();
      await form.locator('[data-access-page=tasks]').selectOption('false');
      await save(form,'update_member_card');await closed();
      const afterPage=(await readRows(page.request)).find(row=>row.telegram_id===actor);
      assert.deepEqual(afterPage.permissions,afterActions.permissions,'Page hide does not revoke grants');
      assert.deepEqual(afterPage.access_settings.actions,afterActions.access_settings.actions,'Page hide does not revoke operations');
      assert.equal(afterPage.access_settings.page_visibility.tasks,false);
      await openEmployee(actor);form=page.locator('#staffEditForm');
      await form.locator('[data-staff-pane-tab=settings]').click();
      assert.equal(await form.locator('[data-access-page=tasks]').inputValue(),'false','Saved visibility reopens accurately');
      if(process.env.EMPLOYEE_SETTINGS_SCREENSHOT_DIR){
        for(const tab of ['details','rights','settings']){
          await form.locator('[data-staff-pane-tab='+tab+']').click();
          const layout=await form.evaluate((node,tab)=>{
            const footer=node.querySelector('.staff-modal-actions').getBoundingClientRect();
            const panel=node.querySelector('[data-staff-pane="'+tab+'"]');
            const boxes=[...panel.querySelectorAll('[data-access-operations]>fieldset:not(.staff-rights)')].map(group=>[...group.querySelectorAll('input')].map(input=>{const r=input.getBoundingClientRect();return {x:r.x,y:r.y}}));
            const pageHeights=[...panel.querySelectorAll('[data-access-page]')].map(input=>input.closest('label').getBoundingClientRect().height);
            return {footer:{top:footer.top,bottom:footer.bottom},height:innerHeight,overflow:node.scrollWidth>node.clientWidth+1,boxes,pageHeights};
          },tab);
          assert(!layout.overflow,'Employee tab fits viewport');
          assert(layout.footer.top>=0&&layout.footer.bottom<=layout.height,'Save footer remains visible on every tab');
          for(const boxes of layout.boxes){assert.equal(boxes.length,4);assert(Math.max(...boxes.map(box=>box.y))-Math.min(...boxes.map(box=>box.y))<3,'Native CRUD is one compact checkbox row');assert(new Set(boxes.map(box=>Math.round(box.x))).size===4);}
          assert(layout.pageHeights.every(height=>height<110),'Page preferences stay compact');
          await page.screenshot({path:process.env.EMPLOYEE_SETTINGS_SCREENSHOT_DIR+'/employee-'+tab+'-'+width+'-'+language+'.png'});
        }
      }
      await form.locator('[data-access-page=tasks]').selectOption('inherit');
      await form.locator('[data-staff-pane-tab=details]').click();
      await form.locator('[name=display_name]').fill('Local employee alias');
      await form.locator('[data-staff-pane-tab=rights]').click();
      await form.locator('[name=role]').selectOption('manager');
      await form.locator('[data-permission][value=support]').check();
      await form.locator('[data-permission][value=finance]').check();
      await form.locator('[data-staff-pane-tab=settings]').click();
      if(!(await form.locator('details.staff-section').evaluate(node=>node.open)))await form.locator('details.staff-section > summary').click();
      await form.locator('[data-work-setting=completion_requires_admin]').selectOption('true');
      await form.locator('[data-work-event=task_assigned][data-work-channel=push]').check();
      assert.equal(await form.evaluate(node => node.scrollWidth <= node.clientWidth + 1), true, 'Expanded settings fit viewport');
      if (process.env.EMPLOYEE_SETTINGS_SCREENSHOT_DIR) await page.screenshot({path:process.env.EMPLOYEE_SETTINGS_SCREENSHOT_DIR+'/employee-settings-'+width+'.png'});
      await form.locator('[data-staff-pane-tab=details]').click();
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
      assert.equal(await form.locator('[data-permission][value=finance]').isChecked(), true);
      await form.locator('[data-staff-pane-tab=settings]').click();
      assert.equal(await form.locator('[data-work-setting=completion_requires_admin]').inputValue(), 'true');
      assert.equal(await form.locator('[data-work-event=task_assigned][data-work-channel=push]').isChecked(), true);
      await form.locator('[data-staff-pane-tab=details]').click();
      assert((await form.innerText()).includes('+994501231234'), 'Explicit WA binding is separate from contact phone');
      await page.evaluate(() => window.tenantReloadProfile());
      await page.keyboard.press('Escape');
      await closed();
      await page.waitForFunction(actor => document.activeElement?.dataset.staffEdit === String(actor), actor);

      // A real separate employee cookie sees native Tasks without a coarse Kommo module grant.
      const granular=(await readRows(page.request)).find(row=>row.telegram_id===62);
      const granted=await page.request.post(origin+'/api/platform/members',{headers:{Origin:origin},data:{
        action:'update_member_card',telegram_id:62,expected_tenant_id:tenantA,expected_user_id:11,
        expected_version:granular.card_version,changes:{access_settings:{expected_version:granular.access_settings.version,
          changes:{actions:{tasks:{view:true}}}}}}});
      assert.equal(granted.status(),200,await granted.text());
      const employeeContext=await browser.newContext({viewport:{width,height:900}});
      try{
        await employeeContext.route(url=>['http:','https:'].includes(url.protocol)&&url.origin!==origin,route=>route.abort());
        const session=await employeeContext.request.get(origin+'/test/session?identity=granular');assert.equal(session.status(),200);
        const employee=await employeeContext.newPage();await employee.goto(origin+'/app?view=tasks&lang='+language);
        await employee.locator('#view-tasks.active').waitFor();
        await employee.locator('.nav [data-view=tasks]').waitFor({state:'visible'});
        const actual=await (await employeeContext.request.get(origin+'/api/platform/me')).json();
        assert.equal(actual.capabilities.modules.tasks,false);assert.equal(actual.capabilities.pages.tasks,true);
        assert.equal(actual.capabilities.actions.tasks.view,true);
      }finally{await employeeContext.close();}

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
      assert.equal((await control('state')).posts, 9, 'Create, email, password, WA, native actions, page visibility, local update, granular employee grant and one rejected duplicate');
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
      ['update_member_card', 'provision_member_account', 'assign_member_email', 'assign_member_password'].includes(write.action)), JSON.stringify(writes));
    assert.deepEqual(errors, [], 'Actual app has no uncaught errors');
    console.log(`PASS EMP01 actual PostgreSQL→HTTP→built /app: ${mode}, ${width}px, ${language}; scoped CRUD, receipt/version/errors, current authority and no external actions`);
  } finally { await context.close(); await browser.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
