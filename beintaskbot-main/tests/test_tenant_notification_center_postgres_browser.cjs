// Actual local HTTP endpoint and production SQL driver, not a mocked listing.
const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
(async()=>{
 const origin=process.env.NOTIFICATION_RUNTIME_ORIGIN;assert.ok(/^http:\/\/127\.0\.0\.1:\d+$/.test(origin));
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':chromium.executablePath()),args:['--no-sandbox']});
 try{for(const width of [1280,390]){
  const page=await browser.newPage({viewport:{width,height:844}}),reads=[];page.setDefaultTimeout(10000);
  await page.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
  page.on('response',response=>{if(new URL(response.url()).pathname==='/api/platform/notifications')reads.push(response.status());});
  await page.goto(origin+'/app?view=tasks');await page.locator('[data-app-language]').selectOption('ru');
  await page.locator('#dialogConsultationBell .notification-unread-badge').getByText('3',{exact:true}).waitFor();
  await page.locator('#dialogConsultationBell').click();await page.locator('[data-notification-id]').first().waitFor();
  assert.equal(await page.locator('[data-notification-id]').count(),3);assert.equal(await page.locator('#dialogConsultationMenu [role=alert]').count(),0);
  await page.locator('[data-notification-filter=all]').click();await page.locator('[data-notification-id]').first().waitFor();assert.equal(await page.locator('[data-notification-id]').count(),3);
  assert.equal((await page.request.get(origin+'/api/platform/notifications?status=all&limit=20',{headers:{'X-Tenant-Id':'00000000-0000-0000-0000-000000000102','X-User-Id':'11'}})).status(),409,'actual HTTP identity gate');
  await page.request.get(origin+'/test/offline');await page.locator('[data-notification-filter=unread]').click();const error=page.locator('#dialogConsultationMenu [role=alert]');await error.waitFor();assert.match(await error.innerText(),/Уведомления временно недоступны/);assert.equal(await page.locator('#dialogConsultationBell .notification-unread-badge').textContent(),'3','error never becomes a false empty ledger');
  await page.request.get(origin+'/test/online');await page.getByRole('button',{name:'Повторить',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('#dialogConsultationMenu [role=alert]'));assert.equal(await page.locator('[data-notification-id]').count(),3);assert.ok(reads.includes(200)&&reads.includes(503));
  await page.screenshot({path:'/tmp/bein-bell-native-postgres-'+width+'.png'});
  console.log('PASS real HTTP/psycopg/PostgreSQL bell '+width+': list/count, identity, honest 503, recovery, no mutations');await page.close();
 }}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
