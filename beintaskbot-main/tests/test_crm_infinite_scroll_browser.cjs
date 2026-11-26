const assert = require('node:assert/strict');
const { chromium } = require('../.support-test-deps/node_modules/playwright');
const { appHtml, installFixture } = require('./task_read_save_flow_fixture.cjs');
const { html: dealHtml, installDeals } = require('./deal_source_picker_fixture.cjs');

const nativeId = id => '10000000-0000-4000-8000-' + String(id).padStart(12, '0');
const taskRows = source => Array.from({ length: 26 }, (_, index) => ({
  id: source === 'native' ? nativeId(index + 1) : String(index + 1),
  source,
  text: 'Example task ' + (index + 1),
  status: index < 23 ? 'open' : 'completed'
}));

async function reachNextPage(page, footer) {
  const position = await page.locator(footer).evaluate(node => {
    if (node.hidden) return null;
    const bounds = node.getBoundingClientRect();
    if (bounds.top>innerHeight || bounds.bottom<0) {
      node.scrollIntoView({block:'end'});
      return null;
    }
    return {x:bounds.left+bounds.width/2, y:Math.min(bounds.top,innerHeight-80)};
  });
  if (position) {
    await page.mouse.move(position.x, position.y);
    await page.mouse.wheel(0, 160);
  }
}

async function checkTasks(browser, width) {
  const page = await browser.newPage({ viewport: { width, height: width>1000 ? 2400 : 800 } });
  let saved = { source: 'native' };
  const start = 'https://fixture.test/app?view=tasks&lang=ru';
  try {
    await page.addInitScript(() => { window.originalFetch = window.fetch; });
    await page.addInitScript(installFixture, { native:taskRows('native'), kommo:taskRows('kommo'), pageSize:10 });
    await page.addInitScript(() => {
      const mock = window.fetch;
      window.fetch = (url, options) => String(url).includes('/task-view-preferences') ?
        window.originalFetch(url, options) : mock(url, options);
    });
    await page.route('**/*', route => {
      const request = route.request();
      if (request.url().includes('/task-view-preferences')) {
        if (request.method() === 'POST') saved = { ...saved, ...request.postDataJSON() };
        return route.fulfill({ json: { success:true, ...saved } });
      }
      return request.isNavigationRequest() ?
        route.fulfill({ contentType:'text/html', body:appHtml() }) : route.abort();
    });
    await page.goto(start);
    await page.waitForFunction(() => nativeTaskCount.textContent === 'Задач: 10 / 10 +');
    assert.equal(await page.locator('#taskStatus').inputValue(), 'open');
    assert.equal(await page.locator('#nativeTasksList [data-task-flow-open]').count(), 10);
    assert.equal(await page.locator('#view-tasks button[id$="More"]').count(), 0);
    if (width>1000) {
      assert(await page.locator('#nativeTasksMore').evaluate(node => node.getBoundingClientRect().bottom<innerHeight),
        'the first page fits without scrolling');
    }
    await page.waitForTimeout(200);
    assert.equal(await page.locator('#nativeTasksList [data-task-flow-open]').count(), 10);
    const orders = await page.evaluate(() => taskFlowFixture.calls.filter(call =>
      call.path==='/api/platform/crm/tasks' && call.url.includes('flow=1')).map(call =>
      new URL(call.url,location.origin).searchParams.get('order')));
    assert.deepEqual([...new Set(orders)], ['recent']);
    await reachNextPage(page, '#nativeTasksMore');
    await page.waitForFunction(() => nativeTaskCount.textContent === 'Задач: 20 / 20 +');
    await reachNextPage(page, '#nativeTasksMore');
    await page.waitForFunction(() => nativeTaskCount.textContent === 'Задач: 23 / 23' && nativeTasksMore.hidden);
    assert.equal(await page.locator('#nativeTasksList [data-task-flow-open]').count(), 23);
    const nativePages = await page.evaluate(() => taskFlowFixture.calls.filter(call => /source=native.*offset=/.test(call.url)));
    assert.equal(nativePages.length, 2);

    await page.selectOption('#taskStatus', 'completed');
    await page.waitForFunction(() => nativeTaskCount.textContent === 'Задач: 3 / 3');
    assert.equal(saved.status, 'completed');
    await page.goto(start); // Start at a URL with no task_status parameter.
    await page.waitForFunction(() => nativeTaskCount.textContent === 'Задач: 3 / 3');
    assert.equal(await page.locator('#taskStatus').inputValue(), 'completed');
    await page.selectOption('#taskStatus', 'open');
    await page.waitForFunction(() => nativeTaskCount.textContent === 'Задач: 10 / 10 +');
    await page.selectOption('#taskFeedSource', 'kommo');
    await page.waitForFunction(() => !taskFeedSource.disabled && !tasksMore.hidden);
    await reachNextPage(page, '#tasksMore');
    await page.waitForFunction(() => kommoTaskCount.textContent === 'Задач: 20 / 20 +');
    assert.equal(saved.source, 'kommo');
    assert.equal(saved.status, 'open');
  } finally {
    await page.close();
  }
}

async function checkDeals(browser, width) {
  const page = await browser.newPage({ viewport: { width, height: 800 } });
  try {
    await page.addInitScript(installFixture, {});
    await page.addInitScript(installDeals, {});
    await page.addInitScript(() => {
      const mock = window.fetch;
      window.dealPageReads = [];
      window.fetch = async (url, options) => {
        const request = new URL(url, location.origin);
        const native = request.pathname === '/api/platform/crm/native/deals';
        if (request.pathname !== '/api/platform/crm/deals' && !native || request.searchParams.has('options')) {
          return mock(url, options);
        }
        const offset = Number(request.searchParams.get('offset') || 0), limit = native ? 50 : 100;
        if (offset) window.dealPageReads.push({native, offset});
        const rows = Array.from({length: 112}, (_, index) => native ? {
          id:'10000000-0000-4000-8000-'+String(index+1).padStart(12,'0'),
          title:'Example deal '+(index+1), pipeline_id:'p', stage_id:'s'
        } : {
          kommo_lead_id:index+1, name:'Example deal '+(index+1), pipeline_id:1, status_id:10
        });
        return new Response(JSON.stringify({success:true, deals:rows.slice(offset, offset+limit),
          total:rows.length, next_offset:offset+limit<rows.length?offset+limit:null}),
          {headers:{'Content-Type':'application/json'}});
      };
    });
    await page.route('**/*', route => route.request().isNavigationRequest() ?
      route.fulfill({contentType:'text/html', body:dealHtml()}) : route.abort());
    await page.goto('https://fixture.test/app?view=deals&lang=ru');
    await page.waitForFunction(() => dealCount.textContent === '100 / 112 сделок', null, {timeout:10000}).catch(async error => {
      const state = await page.evaluate(() => ({count:dealCount.textContent, source:dealFeedSource.value,
        notice:dealsNotice.textContent, rows:dealsList.textContent.slice(0, 80)}));
      throw new Error(JSON.stringify(state), {cause:error});
    });
    assert.equal(await page.locator('#view-deals button[id$="More"]').count(), 0);
    await reachNextPage(page, '#dealsMore');
    await page.waitForFunction(() => dealCount.textContent === '112 сделок' && dealsMore.hidden);
    // Scroll to the source control before switching from the bottom of the list.
    await page.locator('#dealFeedSource').scrollIntoViewIfNeeded();
    await page.selectOption('#dealFeedSource', 'native');
    await page.waitForFunction(() => dealCount.textContent === '50 сделок');
    await reachNextPage(page, '#nativeDealsMore');
    await page.waitForFunction(() => ['100 сделок', '112 сделок'].includes(dealCount.textContent));
    await reachNextPage(page, '#nativeDealsMore');
    await page.waitForFunction(() => dealCount.textContent === '112 сделок' && nativeDealsMore.hidden);
    assert.deepEqual(await page.evaluate(() => window.dealPageReads), [
      {native:false, offset:100}, {native:true, offset:50}, {native:true, offset:100}
    ]);
  } finally {
    await page.close();
  }
}

(async () => {
  const browser = await chromium.launch({
    executablePath:process.env.CHROMIUM_EXECUTABLE_PATH || '/usr/bin/chromium', args:['--no-sandbox']
  });
  try {
    for (const width of [390, 1440]) {
      await checkTasks(browser, width);
      await checkDeals(browser, width);
    }
    console.log('PASS saved task status and scroll pagination in both sources at mobile and desktop widths');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
