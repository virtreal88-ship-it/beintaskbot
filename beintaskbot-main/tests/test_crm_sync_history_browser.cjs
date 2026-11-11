/* Browser-capable regression: real /app assets, synthetic same-origin APIs, no provider/network writes. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'../.support-test-deps/node_modules/playwright');
const {docs,assets,appHtml,installNativeTaskFixture,installNativeCompanyDealFixture}=require('./native_company_deal_fixture.cjs');
const {installSyncHistoryFixture}=require('./crm_sync_history_fixture.cjs');
const event=(state='synced',index=1)=>({event_id:'20000000-0000-4000-8000-'+String(index).padStart(12,'0'),kind:'scalar',state,attempt_outcome:'acknowledged',created_at:'2026-11-11T08:00:00.000Z',local_revision:'2026-11-11T08:00:00.000Z',recorded_at:'2026-11-11T09:00:00.000Z',issue:'none'});
(async()=>{const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});try{
 for(const width of [1280,390])for(const type of ['deal','company']){
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage(),errors=[];
  try{
   await context.addInitScript(installNativeTaskFixture,{});await context.addInitScript(installNativeCompanyDealFixture,{dealUpdate:false,companyUpdate:false});await context.addInitScript(installSyncHistoryFixture,{events:[event()]});
   page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',route=>{const u=new URL(route.request().url());if(u.origin!=='http://fixture.test')return route.abort();if(u.pathname==='/app')return route.fulfill({status:200,contentType:'text/html',body:appHtml()});const name=path.basename(u.pathname);return u.pathname.startsWith('/assets/')&&assets.includes(name)?route.fulfill({status:200,contentType:'text/javascript',body:fs.readFileSync(path.join(docs,name),'utf8')}):route.abort()});
   await page.goto('http://fixture.test/app?view='+(type==='deal'?'deals':'companies')+'&lang=en');await page.waitForFunction(()=>window.tenantProfileSnapshot?.member?.tenant_id);
   const id=await page.evaluate(type=>nativeTaskFixture.ids[type],type),open=()=>page.locator(`[data-native-open="${type}:${id}"]`).click();
   await open();await page.locator('[data-sync-history-event]').waitFor();const history=page.locator('[data-sync-history]');assert.match(await history.textContent(),/journal recorded completion/);assert.match(await history.textContent(),/current external state is not verified/);assert.equal(await page.locator('[data-native-card-edit]').isHidden(),true);
   const bounds=await history.boundingBox();assert(bounds&&bounds.x>=0&&bounds.x+bounds.width<=width+1,'history fits viewport');assert.equal(await history.evaluate(node=>node.scrollWidth<=node.clientWidth+1),true,'history does not overflow');await page.screenshot({path:'/tmp/crm-history-'+type+'-'+width+'.png'});
   await page.evaluate(()=>syncHistoryFixture.replies.push({hold:true}));await page.locator('[data-sync-history-refresh]').click();await page.waitForFunction(()=>syncHistoryFixture.pending.length===1);await page.evaluate(()=>document.querySelector('[data-sync-history-refresh]').click());assert.equal(await page.evaluate(()=>syncHistoryFixture.calls.length),2,'repeat click does not duplicate read');
   await page.locator('[data-native-card-close]').first().click();await page.evaluate(()=>syncHistoryFixture.pending.shift()());assert.equal(await page.locator('[data-sync-history-event]').count(),0,'closed card clears rows');assert.equal(await page.locator('#nativeEntityCard').isVisible(),false);
   await open();await page.locator('[data-sync-history-event]').waitFor();await page.evaluate(()=>syncHistoryFixture.replies.push({status:403}));await page.locator('[data-sync-history-refresh]').click();await page.waitForFunction(()=>document.querySelector('[data-sync-history-status]').textContent.includes('unavailable'));assert.equal(await page.locator('[data-sync-history-event]').count(),0);
   await page.locator('[data-sync-history-refresh]').click();await page.locator('[data-sync-history-event]').waitFor();await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-auth-pending')));assert.equal(await page.locator('[data-sync-history]').count(),0,'auth revalidation immediately clears retained history');assert.equal(await page.evaluate(()=>nativeTaskFixture.calls.some(c=>c.method!=='GET')||syncHistoryFixture.calls.some(c=>c.method!=='GET')),false);assert.deepEqual(errors,[]);
   console.log('PASS Chromium '+width+' '+type+': read-only history, viewport fit, duplicate refresh, close, denial and auth clearing');
  }finally{await context.close()}
 }
}finally{await browser.close()}})().catch(error=>{console.error(error.stack||error);process.exitCode=1});
