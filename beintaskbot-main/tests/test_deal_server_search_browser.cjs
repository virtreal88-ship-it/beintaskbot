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

async function verifyTableReturn(browser) {
  const page = await browser.newPage({viewport:{width:1280, height:720}});
  await page.addInitScript(installFixture, {});
  await page.addInitScript(installDeals, {saved:'kommo'});
  await page.addInitScript(() => {
    const original = window.fetch;
    let version = 0;
    window.focusTest = {holdList:true, holdProfile:false, deny:false, refresh:()=>++version};
    window.fetch = async (url, options) => {
      const path = new URL(url,location.origin).pathname, f = focusTest;
      if (path === '/api/platform/me' && f.holdProfile) await new Promise(r => { f.profileReady=r; });
      if (path !== '/api/platform/crm/deals') return original(url,options);
      if (f.holdList) await new Promise(r => { (f.listWaiters ||= []).push(r); });
      const rows = Array.from({length:80}, (_,i)=>({kommo_lead_id:i+1, name:'Scope Deal '+(i+1),
        pipeline_id:1,status_id:10, stage_name:'New',contact_name:i===0 && version ? 'Fresh contact '+version : 'Contact'}));
      return new Response(JSON.stringify(f.deny ? {success:false,error:'forbidden'} : {success:true,deals:rows,total:rows.length}),
                          {status:f.deny?403:200,headers:{'Content-Type':'application/json'}});
    };
  });
  await page.route('**/*', route=>route.request().isNavigationRequest()
    ? route.fulfill({contentType:'text/html',body:html()}) : route.abort());
  await page.goto('https://fixture.test/app?view=deals&lang=ru');
  await page.waitForFunction(()=>focusTest.listWaiters?.length);
  assert.match(await page.locator('#dealCount').innerText(),/Загрузка/);
  const releaseList=()=>page.evaluate(()=>{focusTest.holdList=false;focusTest.listWaiters.splice(0).forEach(r=>r());});
  await releaseList();
  await page.getByRole('button',{name:'Таблица',exact:true}).click();
  await page.fill('#dealSearch','Scope');
  await page.waitForFunction(()=>dealCount.textContent.includes('80'));
  await page.locator('#dealsList').getByText('Scope Deal 60',{exact:true}).first().scrollIntoViewIfNeeded();
  await page.evaluate(()=>{
    const list=document.getElementById('dealsList');
    const row=Array.from(list.querySelectorAll('*')).find(e=>!e.childElementCount&&e.textContent==='Scope Deal 60');
    let scroller=row;
    while(scroller && scroller.scrollTop<1) scroller=scroller.parentElement;
    if(!scroller) throw Error('Expected a scrolled production table or its page');
    focusTest.scroll=scroller; focusTest.top=scroller.scrollTop;
    focusTest.holdProfile=true;
    Object.defineProperty(document,'hidden',{value:true,configurable:true});
    document.dispatchEvent(new Event('visibilitychange'));
  });
  const retained=()=>page.evaluate(()=>({count:dealCount.textContent,query:dealSearch.value,
      present:dealsList.textContent.includes('Scope Deal 60'), scroll:focusTest.scroll.scrollTop, before:focusTest.top}));
  const check=async()=>{const r=await retained();assert.match(r.count,/80/);assert.equal(r.present,true);
    assert.equal(r.query,'Scope');assert.equal(r.scroll,r.before);};
  await check();
  await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'));});
  await page.waitForFunction(()=>typeof focusTest.profileReady==='function');
  await check();
  await page.evaluate(()=>{focusTest.refresh();focusTest.holdList=true;focusTest.holdProfile=false;focusTest.profileReady();});
  await page.waitForFunction(()=>focusTest.listWaiters.length>0);
  await check();
  await releaseList();
  await page.waitForFunction(()=>document.getElementById('dealsList').textContent.includes('Fresh contact'));
  await check();
  await page.evaluate(()=>{
    document.querySelector('.nav [data-view="profile"]').click();
    focusTest.refresh(); focusTest.holdList=true;
    document.querySelector('.nav [data-view="deals"]').click();
  });
  await page.waitForFunction(()=>focusTest.listWaiters.length>0);
  await check();
  await releaseList();
  await page.waitForFunction(()=>dealsList.textContent.includes('Fresh contact 2'));
  await check();
  await page.evaluate(()=>{focusTest.deny=true;document.dispatchEvent(new CustomEvent('tenant-crm-reauthorized'));});
  await page.waitForFunction(()=>!document.getElementById('dealsList').textContent.includes('Scope Deal 60'));
  assert.equal(await page.locator('#dealsList').getByText('Scope Deal 60',{exact:true}).count(),0);
  await page.close();
  console.log('PASS cached Kommo table, filter and scroll through delayed profile + list, fresh data and revocation');
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
    await verifyTableReturn(browser);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
