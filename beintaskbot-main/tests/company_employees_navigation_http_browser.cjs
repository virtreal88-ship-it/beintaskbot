/* NAV01 actual built /app against isolated PostgreSQL HTTP APIs. No authority API interception. */
const fs = require('node:fs');
const assert = require('node:assert/strict');
const { chromium } = require('../.support-test-deps/node_modules/playwright');

const origin = process.env.COMPANY_EMPLOYEES_RUNTIME_ORIGIN;
const mode = process.env.COMPANY_EMPLOYEES_RUNTIME_MODE || 'ready';
const actor = Number(process.env.COMPANY_EMPLOYEES_RUNTIME_ACTOR);
const width = Number(process.env.COMPANY_EMPLOYEES_RUNTIME_WIDTH || 390);
const language = process.env.COMPANY_EMPLOYEES_RUNTIME_LANGUAGE || 'ru';
const tenantA = process.env.COMPANY_EMPLOYEES_RUNTIME_TENANT_A;
const tenantB = process.env.COMPANY_EMPLOYEES_RUNTIME_TENANT_B;
assert(origin && actor && tenantA && tenantB, 'The real HTTP fixture must supply the current synthetic identity');

(async () => {
  const executablePath = process.env.CHROMIUM_EXECUTABLE_PATH || (fs.existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined);
  const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}), args: ['--no-sandbox'] });
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: 'Asia/Baku' });
  const page = await context.newPage();
  const writes = [], errors = [], bundles = [], memberRequests = [], notificationRequests = [], documents = [];
  // Only external network transports are blocked. Local me/members/task APIs,
  // cookies, profile events and capabilities are the application's real paths.
  await context.route(url => ['http:', 'https:'].includes(url.protocol) && url.origin !== origin, route => route.abort());
  page.on('pageerror', error => errors.push(error.stack || error.message));
  page.on('request', request => {
    if (new URL(request.url()).pathname === '/api/platform/members') memberRequests.push(request);
    if (new URL(request.url()).pathname === '/api/platform/notifications') notificationRequests.push(request);
    if (request.isNavigationRequest() && request.frame() === page.mainFrame()) documents.push(request);
    if (!['GET', 'HEAD'].includes(request.method())) writes.push({ method: request.method(), path: new URL(request.url()).pathname });
  });
  page.on('response', response => {
    if (new URL(response.url()).pathname === '/assets/tenant-chakra-ui.js') bundles.push(response.status());
  });
  const control = async path => {
    const response = await page.request.get(origin + '/test/' + path);
    assert.equal(response.status(), 200, path);
    return response.json();
  };
  const waitState = async expression => {
    const deadline = Date.now() + 10000;
    let state;
    while (Date.now() < deadline) {
      state = await control('state');
      const complete = expression === 'pending_me' ? state.pending_me > 0 :
        expression === 'pending_members' ? state.pending_members > 0 :
          state.pending_members > 0 && state.released_members === state.pending_members;
      if (complete) return state;
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    assert.fail('The real HTTP barrier did not reach ' + expression + ': ' + JSON.stringify(state));
  };
  const current = async (tenant = tenantA, expectedActor = actor) => {
    await page.waitForFunction(({ tenant, expectedActor }) =>
      window.tenantProfileSnapshot?.member?.tenant_id === tenant &&
      Number(window.tenantProfileSnapshot?.member?.telegram_id) === expectedActor,
    { tenant, expectedActor });
  };
  const openSidebar = async () => {
    const mobileMenu = page.locator('.app-mobile-menu');
    if (await mobileMenu.isVisible() && await mobileMenu.getAttribute('aria-expanded') !== 'true') await mobileMenu.click();
  };
  const openProfile = async () => {
    await page.locator('.app-avatar-button').click();
    await page.locator('[data-app-open=profile]').click();
  };
  const visibleStaff = async tenant => {
    await page.locator('#view-employees.active').waitFor();
    await page.locator('#employeesManagementHost #staffManagement #staffContent').filter({
      hasText: tenant === tenantA ? 'A-PRIVATE-EMPLOYEE' : 'B-PRIVATE-EMPLOYEE'
    }).waitFor();
    assert.equal(await page.locator('#staffManagement').count(), 1, 'One existing employee controller/root is reused');
    assert.equal(await page.locator('#staffManagement').evaluate(node => node.parentElement.id), 'employeesManagementHost');
    const text = await page.locator('#staffContent').innerText();
    assert(!text.includes(tenant === tenantA ? 'B-PRIVATE-EMPLOYEE' : 'A-PRIVATE-EMPLOYEE'));
    assert.equal(new URL(page.url()).searchParams.get('view'), 'employees');
    assert.equal(await page.locator('#staffManagement').evaluate(node => node.scrollWidth <= node.clientWidth + 1), true);
  };
  const accessDenied = async () => {
    await page.locator('#employeesAccessNotice').waitFor();
    assert.equal(await page.locator('#companyEmployeesNav').isVisible(), false);
    assert.equal(await page.locator('#staffManagement').evaluate(node => node.hidden || !node.children.length), true);
    assert.equal(await page.locator('#staffMemberModal.show').count(), 0);
    assert(!((await page.locator('#view-employees').innerText()).includes('PRIVATE-EMPLOYEE')));
    assert(!((await page.locator('#staffManagement').textContent()).includes('PRIVATE-EMPLOYEE')), 'Denied rows are cleared, including hidden DOM');
    assert(!((await page.locator('#staffModalBody').textContent()).includes('PRIVATE-EMPLOYEE')), 'Old employee modal data is cleared');
  };
  try {
    await control('session?identity=initial');
    await page.goto(origin + '/app?view=' + (mode === 'denied' || mode === 'initial_pending' ? 'employees' : 'profile') + '&lang=' + language);
    if (mode === 'initial_pending') {
      await waitState('pending_me');
      assert.equal(await page.locator('#companyEmployeesNav').isVisible(), false);
      assert.equal(await page.locator('#staffContent [data-staff-edit]').count(), 0);
      assert.equal(await page.locator('#staffMemberModal.show').count(), 0);
      await control('release');
    }
    await current();
    assert.deepEqual(bundles, [200], 'The actual built Chakra bundle is loaded');
    if (mode === 'denied') {
      await accessDenied();
      const response = await page.request.get(origin + '/api/platform/members');
      assert.equal(response.status(), 403);
      assert.equal('members' in await response.json(), false);
    } else {
      await openSidebar();
      const entry = page.locator('#settingsFamilyNav');
      const group = page.locator('[data-group=company]');
      if (!(await entry.isVisible())) await group.locator('.app-nav-group-title').click();
      await entry.click();
      const staffTab = page.locator('[data-settings-page=employees]');
      if (await staffTab.isVisible()) await staffTab.click();
      else if (!(await page.locator('#view-employees').evaluate(node => node.classList.contains('active')))) {
        await page.locator('.settings-family .app-tab-picker').selectOption({ label:await staffTab.textContent() });
      }
      assert.equal(await page.locator('#companyEmployeesNav').isVisible(),false);
      await visibleStaff(tenantA);
      if (actor !== 11) {
        assert.equal(await page.evaluate(() => tenantProfileSnapshot.capabilities.modules.settings), false);
        await page.locator('.app-avatar-button').click();
        assert.equal(await page.locator('[data-app-open=settings]').isVisible(), false, 'Employee access grants no Settings access');
        await page.keyboard.press('Escape');
      }
      if (mode === 'minute_resume') {
        // Use wall-clock time and the real built app, not synthetic profile
        // events alone: exercise two actual global notification poll cycles.
        const opener = page.locator('[data-staff-edit="41"]');
        await opener.click();
        await page.locator('#staffMemberModal.show').waitFor();
        const form = await page.locator('#staffEditForm').elementHandle();
        const list = await opener.elementHandle();
        const reads = memberRequests.length, notifications = notificationRequests.length;
        const navigations = documents.length;
        await page.waitForTimeout(125000);
        assert(notificationRequests.length >= notifications + 2, 'Two real minute notification polls ran');
        assert.equal(documents.length, navigations, 'Minute polling never reloads the document');
        assert.equal(memberRequests.length, reads, 'Minute polling does not refetch employees');
        assert.equal(await form.evaluate(node => node.isConnected), true);
        assert.equal(await list.evaluate(node => node.isConnected), true);
        await page.locator('#staffMemberModal.show').waitFor();

        // Focus/visibility revalidation applies a privacy curtain. The clean
        // card must survive it even though its demand cache is now >30s old.
        await control('hold-me');
        await page.evaluate(() => { void window.tenantRequestCoordinator.revalidateProfile().catch(() => {}); });
        await waitState('pending_me');
        assert.equal(await page.locator('#staffManagement').isVisible(), false);
        assert.equal(await page.locator('#staffMemberModal').isVisible(), false);
        await control('release');
        await visibleStaff(tenantA);
        await page.locator('#staffMemberModal.show').waitFor();
        assert.equal(await form.evaluate(node => node === document.getElementById('staffEditForm')), true);
        assert.equal(await list.evaluate(node => node.isConnected), true);
        assert.equal(memberRequests.length, reads, 'Unchanged authority restores the same clean card without a list read');
        assert.equal(documents.length, navigations);
      } else if (mode === 'auth_pending') {
        await page.locator('#staffAdd').click();
        await page.locator('#staffMemberModal.show').waitFor();
        const retainedForm = await page.locator('#staffInviteForm').elementHandle();
        const draftName = 'UNSAVED-PENDING-EMPLOYEE';
        await page.locator('#staffInviteForm [name=first_name]').fill(draftName);
        await control('hold-me');
        await page.evaluate(() => { void window.tenantRequestCoordinator.revalidateProfile().catch(() => {}); });
        await waitState('pending_me');
        // Pending verification is a privacy curtain, not a destructive denial.
        await page.locator('#employeesAccessNotice').waitFor();
        assert.equal(await page.locator('#employeesAccessNotice').getAttribute('aria-busy'), 'true');
        assert.equal(await page.locator('#companyEmployeesNav').isVisible(), false);
        assert.equal(await page.locator('#staffManagement').isVisible(), false);
        assert.equal(await page.locator('#staffMemberModal').isVisible(), false);
        assert.equal(await page.locator('#staffMemberModal').evaluate(node => node.inert), true);
        assert(!((await page.locator('#view-employees').innerText()).includes('PRIVATE-EMPLOYEE')));
        assert.equal(await retainedForm.evaluate(node => node === document.getElementById('staffInviteForm')), true);
        assert.equal(await page.locator('#staffInviteForm [name=first_name]').inputValue(), draftName);
        assert.equal(await page.locator('#staffInviteForm [name=first_name]').evaluate(node => {
          node.focus(); return document.getElementById('staffMemberModal').contains(document.activeElement);
        }), false, 'The retained form cannot receive focus while authority is pending');
        await control('release');
        await visibleStaff(tenantA);
        await page.locator('#staffMemberModal.show').waitFor();
        assert.equal(await page.locator('#staffMemberModal').evaluate(node => node.inert), false);
        assert.equal(await retainedForm.evaluate(node => node === document.getElementById('staffInviteForm')), true);
        assert.equal(await page.locator('#staffInviteForm [name=first_name]').inputValue(), draftName);
      } else if (['revoked', 'tenant_switch', 'actor_switch'].includes(mode)) {
        await page.locator('[data-staff-edit="41"]').click();
        await page.locator('#staffMemberModal.show').waitFor();
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('#staffMemberModal.show').count(), 0);
        const membersBeforeHold = memberRequests.length;
        await control('hold-members');
        // Selecting Members explicitly reloads its list. Same-profile reads now
        // correctly preserve it, so they must not be used to manufacture a read.
        await page.locator('[data-staff-tab=members]').click();
        await waitState('pending_members');
        await page.locator('#staffAdd').click();
        await page.locator('#staffInviteForm').waitFor();
        await page.locator('#staffInviteForm [name=first_name]').fill('A-PRIVATE-DRAFT');
        const oldForm = await page.locator('#staffInviteForm').elementHandle();
        const heldRequests = memberRequests.slice(membersBeforeHold);
        assert(heldRequests.length > 0, 'The server barrier holds an observed real member request');
        if (mode === 'revoked') await control('revoke');
        else await control('session?identity=' + (mode === 'tenant_switch' ? 'company_b' : 'ordinary'));
        await page.evaluate(() => window.tenantRequestCoordinator.revalidateProfile());
        if (mode === 'tenant_switch') {
          await current(tenantB, 37);
          await visibleStaff(tenantB);
          assert.equal(await page.locator('#staffMemberModal.show').count(), 0);
          assert(!((await page.locator('#staffModalBody').textContent()).includes('A-PRIVATE-EMPLOYEE')));
        } else {
          if (mode === 'actor_switch') await current(tenantA, 32);
          await accessDenied();
        }
        assert.equal(await oldForm.evaluate(node => node.isConnected), false, 'Changed authority removes the old form, not just its curtain');
        assert.equal(await page.locator('#staffModalBody').textContent(), '', 'Changed authority clears all old form fields');
        await control('release');
        await waitState('released_members');
        await Promise.all(heldRequests.map(async request => {
          const response = await request.response();
          if (response) await response.finished(); // A legitimate aborted old request may have no response.
        }));
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        if (mode === 'tenant_switch') await visibleStaff(tenantB);
        else await accessDenied();
        assert.equal(await page.locator('#staffMemberModal.show').count(), 0);
        assert.equal(await page.locator('#staffModalBody').textContent(), '', 'The released stale read cannot recreate the old form');
      } else {
        if (actor === 34 && width === 1440) {
          await openProfile();
          await page.locator('.app-sidebar-header .app-icon-button').click();
          assert.equal(await page.locator('body').getAttribute('data-sidebar'), 'collapsed');
          await entry.click();
          await visibleStaff(tenantA);
        }
        await page.reload();
        await current();
        await visibleStaff(tenantA);
        await openProfile();
        await page.goBack();
        await visibleStaff(tenantA);
        await openProfile();
        await page.evaluate(() => window.tenantOpenSettingsSection('employees'));
        await visibleStaff(tenantA);
        const opener = page.locator('[data-staff-edit="41"]');
        const originalOpener = await opener.elementHandle();
        await opener.click();
        await page.locator('#staffMemberModal.show').waitFor();
        // A same-identity profile read preserves both the form and its opener.
        // Closing must restore focus without a needless employee list redraw.
        await page.evaluate(() => window.tenantReloadProfile());
        await page.locator('#staffMemberModal.show').waitFor();
        assert.equal(await originalOpener.evaluate(node => node.isConnected), true);
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('#staffMemberModal.show').count(), 0);
        assert.equal(await opener.evaluate(node => node === document.activeElement), true,
          JSON.stringify({ original: await originalOpener.evaluate(node => ({ connected: node.isConnected })),
            active: await page.evaluate(() => ({ tag: document.activeElement?.tagName, id: document.activeElement?.id,
              edit: document.activeElement?.dataset.staffEdit })) }));
        if (actor === 11 || actor === 38) {
          const tasks = page.locator('.nav [data-view=tasks]');
          await openSidebar();
          const crmGroup = page.locator('.app-nav-group[data-group=crm] .app-nav-group-title');
          if (await crmGroup.getAttribute('aria-expanded') === 'false') await crmGroup.click();
          await tasks.click();
          await page.locator('#nativeTaskCreate').click();
          // The single CRM02 entry deliberately asks for a source first.
          // Exercise that real choice before the unchanged native Staff link.
          await page.locator('#taskSourceModal.show').waitFor();
          assert.equal(await page.locator('#nativeTaskModal.show').count(), 0);
          await page.locator('[data-task-create-source="native"]').click();
          await page.locator('#nativeTaskStaff:not([hidden])').waitFor();
          await page.locator('#nativeTaskStaff').click();
          await visibleStaff(tenantA);
          assert.equal(await page.locator('#nativeTaskModal.show').count(), 0);
        }
      }
    }
    assert.deepEqual(writes, [], 'Navigation, retries and profile changes send no mutation');
    assert.deepEqual(errors, [], 'Actual app/built bundle has no page errors');
    console.log(`PASS NAV01 actual PostgreSQL HTTP → built /app Chromium ${mode}, actor ${actor}, ${width}px, ${language}; current authority and read-only isolation`);
  } finally {
    await context.close();
    await browser.close();
  }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
