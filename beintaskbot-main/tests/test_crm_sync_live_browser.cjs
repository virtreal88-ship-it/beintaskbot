/* Actual shell/styles with synthetic API fixture; no provider/network writes. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.CRM_SYNC_PLAYWRIGHT||'../.support-test-deps/node_modules/playwright');
const {appHtml,installFixture}=require('./task_read_save_flow_fixture.cjs');
(async()=>{const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':chromium.executablePath()),args:['--no-sandbox']});try{
 for(const width of [390,1440]){
  const page=await browser.newPage({viewport:{width,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(installFixture,{});
  await page.route('**/*',route=>{if(route.request().isNavigationRequest())return route.fulfill({contentType:'text/html',body:appHtml()});const url=new URL(route.request().url()),file=path.join(__dirname,'../docs',path.basename(url.pathname));if(url.origin==='https://fixture.test'&&url.pathname.startsWith('/assets/')&&file.endsWith('.css')&&fs.existsSync(file))return route.fulfill({path:file});return route.abort();});
  await page.goto('https://fixture.test/app?view=tasks&lang=ru');
  await page.waitForFunction(()=>document.querySelector('#nativeTasksList')?.textContent.includes('PRIVATE task A'));
  await page.addScriptTag({content:fs.readFileSync(__dirname+'/../docs/tenant-crm-sync.js','utf8')});
  await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-profile',{detail:tenantProfileSnapshot})));
  for(const view of ['tasks','deals']){
   await page.evaluate(view=>{document.querySelectorAll('.view.active').forEach(n=>n.classList.remove('active'));document.getElementById('view-'+view).classList.add('active');},view);
   assert.equal(await page.locator('#'+view+'Refresh').isVisible(),false,view+' refresh hidden at '+width);
   assert.equal(await page.locator('#view-'+view+' [data-task-admin-sync]').count(),0);
   assert.equal(await page.locator('#view-'+view+' .crm-toolbar-actions button.refresh:visible').count(),0,'no refresh action remains');
  }
  assert.deepEqual(errors,[]);assert.equal(await page.evaluate(()=>taskFlowFixture.calls.some(c=>c.url.includes('full=1'))),false);
  await page.close();console.log('PASS: hidden task/deal refresh buttons, no administrative button at '+width);
 }
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
