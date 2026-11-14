const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'../.support-test-deps/node_modules/playwright');
const {nodeHtml,installCompanyEmployeesFixture}=require('./company_employees_entry_fixture.cjs');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
 try{for(const width of [390,1440]){
 const page=await browser.newPage({viewport:{width,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(installCompanyEmployeesFixture,{settings:true,modules:{integrations:true}});
 await page.route('**/*',route=>route.request().isNavigationRequest()?route.fulfill({contentType:'text/html',body:nodeHtml()}):route.abort());
 await page.goto('https://fixture.local/app?view=employees');
 await page.locator('#staffContent .staff-row:not(.head)').first().waitFor();
 assert.equal(await page.locator('#companyEmployeesNav').isVisible(),false);
 assert.equal(await page.locator('[data-staff-tab=invites],[data-staff-tab=requests]').count(),0);
 const select=page.locator('.settings-family .app-tab-picker');
 for(const view of ['integrations','settings','employees']){
 const tab=page.locator(`[data-settings-page=${view}]`);
 if(await select.isVisible())await select.selectOption({label:await tab.textContent()});else await tab.click();
 await page.locator(`#view-${view}.active`).waitFor();assert.equal(new URL(page.url()).searchParams.get('view'),view);
 }
 assert.deepEqual(errors,[]);await page.close();console.log('PASS Settings family browser '+width);
 }}finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
