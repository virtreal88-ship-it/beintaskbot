const assert = require('node:assert/strict');
const {chromium} = require('../.support-test-deps/node_modules/playwright');
const {html, installFixture, installDeals} = require('./deal_source_picker_fixture.cjs');

function installSearchServer() {
  const base = window.fetch;
  const native = Array.from({length: 55}, (_, index) => ({
    id: '10000000-0000-4000-8000-' + String(index + 1).padStart(12, '0'),
    title: 'Internal deal ' + (index + 1), stage_id: 's', pipeline_id: 'p',
    company_name: index === 54 ? 'Juniper' : '',
  }));
  const external = {kommo_lead_id: 55, name: 'External deal 55', pipeline_id: 1, status_id: 10, stage_name: 'New'};
  window.searchRequests = [];
  window.fetch = async (url, options) => {
    const {pathname, searchParams} = new URL(url, location.origin);
    const nativeRead = pathname === '/api/platform/crm/native/deals';
    if (!nativeRead && pathname !== '/api/platform/crm/deals') return base(url, options);
    const search = (searchParams.get('search') || '').toLowerCase();
    window.searchRequests.push({pathname, search});
    if (window.failDealRead) return new Response('{}', {status: 503});
    if (search === 'delay') await new Promise(resolve => { window.finishOldSearch = resolve; });
    // A matched Kommo company can be present only in the provider snapshot.
    const candidates = nativeRead ? native : [{...external, company_name: 'Juniper'}];
    const rows = candidates.filter(row => row.company_name.toLowerCase().includes(search)).slice(0,50);
    const deals = !nativeRead && search ? rows.map(() => external) : rows;
    return new Response(JSON.stringify({success: true, total: rows.length, deals}), {
      headers: {'Content-Type': 'application/json'},
    });
  };
}

(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROMIUM_EXECUTABLE_PATH || '/usr/bin/chromium', args: ['--no-sandbox']});
  try {
    for (const source of ['native', 'kommo']) {
      const page = await browser.newPage();
      await page.addInitScript(installFixture, {});
      await page.addInitScript(installDeals, {saved: source});
      await page.addInitScript(installSearchServer);
      await page.route('**/*', route => route.request().isNavigationRequest()
        ? route.fulfill({contentType: 'text/html', body: html()}) : route.abort());
      await page.goto('https://fixture.test/app?view=deals&lang=ru');
      await page.waitForFunction(s => !dealFeedSource.disabled && dealFeedSource.value === s &&
        dealCount.textContent.includes(s === 'native' ? '50' : '1'), source);
      const title = source === 'native' ? 'Internal deal 55' : 'External deal 55';
      const result = page.getByText(title, {exact: true}).first();
      assert.equal(await result.isVisible(), source === 'kommo');
      await page.fill('#dealSearch', 'UniPeR');
      const path = '/api/platform/crm/' + (source === 'native' ? 'native/deals' : 'deals');
      await page.waitForFunction(p => searchRequests.some(r => r.pathname === p && r.search === 'uniper'), path, {timeout: 5000});
      await result.waitFor();
      await page.waitForFunction(() => dealCount.textContent.includes('1'));
      if (source === 'kommo') {
        for (const name of ['Таблица', 'Kanban']) {
          await page.getByRole('button', {name, exact:true}).click();
          await result.waitFor();
        }
      }
      await page.fill('#dealSearch', 'delay');
      await page.waitForFunction(() => typeof finishOldSearch === 'function');
      await page.fill('#dealSearch', 'Juniper');
      await page.waitForFunction(p => searchRequests.some(r => r.pathname === p && r.search === 'juniper'), path);
      await result.waitFor();
      await page.evaluate(() => finishOldSearch());
      const notice = source === 'native' ? '#nativeDealsNotice' : '#dealsNotice';
      await page.evaluate(source => {
        window.failDealRead = true;
        document.dispatchEvent(new CustomEvent(source === 'native' ? 'tenant-crm-cache-refresh' : 'tenant-crm-sync-complete', {
          detail: {tenant_id: taskFlowFixture.A, expected_user_id: String(taskFlowFixture.profile.member.telegram_id),
                   resource: 'leads', resources: ['leads']},
        }));
      }, source);
      await page.waitForFunction(selector => document.querySelector(selector)?.textContent.trim(), notice, {timeout: 5000});
      assert(await result.isVisible(), 'failed refresh must retain the previously matched row');
      await page.evaluate(() => { window.failDealRead = false; });
      const kept = await page.evaluate(() => {
        Object.defineProperty(document, 'hidden', {configurable: true, value: true});
        document.dispatchEvent(new Event('visibilitychange'));
        const count = dealCount.textContent, search = dealSearch.value;
        delete document.hidden;
        document.dispatchEvent(new Event('visibilitychange'));
        return {count, search};
      });
      assert.match(kept.count, /1/);
      assert.equal(kept.search, 'Juniper');
      await result.waitFor();
      console.log('PASS', source, 'server query and visible matched deal', title);
      await page.close();
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
