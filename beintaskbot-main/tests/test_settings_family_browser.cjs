/* Real Chrome with synthetic tenant/member API responses and no production writes. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'../.support-test-deps/node_modules/playwright');
const {root,docs,installCompanyEmployeesFixture}=require('./company_employees_entry_fixture.cjs');
const assets=['tenant-operator-i18n.js','tenant-crm-save-receipt.js','tenant-crm-sync-history.js','tenant-request-coordinator.js','tenant-view-demand.js','tenant-workspace.js','tenant-members-ui.js','tenant-chakra-ui.js'];
const html=fs.readFileSync(path.join(docs,'tenant-app.html'),'utf8').replace(/<script\b([^>]*)src="([^"]+)"[^>]*><\/script>/gi,(tag,attrs,url)=>{const name=path.basename(url);if(!assets.includes(name))return '';const code=fs.readFileSync(path.join(docs,name),'utf8');return '<script>'+code.replace(/<\/script/gi,'<\\/script')+'</script>';});
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe',args:['--no-sandbox']});
 try{
  for(const width of [390,1440]){
   const page=await browser.newPage({viewport:{width,height:900}}),errors=[];page.on('pageerror',error=>errors.push(error.message));
   await page.addInitScript(installCompanyEmployeesFixture,{role:'owner',settings:true,pages:{profile:true,settings:true,integrations:true,employees:true},modules:{integrations:true}});
   await page.route('**/*',route=>route.request().isNavigationRequest()?route.fulfill({contentType:'text/html',body:html}):route.abort());
   await page.goto('https://fixture.local/app?view=settings');await page.locator('#view-profile.active').waitFor();
   await page.waitForFunction(()=>window.companyEmployeesFixture.calls.some(call=>call.path==='/api/platform/me'),null,{timeout:5000}).catch(async error=>{
    const state=await page.evaluate(()=>({fixture:!!window.companyEmployeesFixture,calls:window.companyEmployeesFixture?.calls,profile:document.querySelector('#view-profile')?.className,errors:window.__testErrors||[]}));
    throw new Error(error.message+' '+JSON.stringify(state));
   });
   assert.equal(new URL(page.url()).searchParams.get('view'),'profile');
   assert.equal(await page.locator('#settingsFamilyNav').isVisible(),false);
   assert.equal(await page.locator('[data-settings-page=settings]').count(),0);
   if(width===390){await page.locator('.app-mobile-menu').click();assert.equal(await page.locator('.nav [data-view=settings]').isVisible(),false);await page.locator('.app-sidebar-backdrop').evaluate(el=>el.click());await page.locator('.app-avatar-button').click();await page.locator('#appUserMenu [data-app-open=settings]').click();}
   else {await page.locator('.app-avatar-button').click();await page.locator('#appUserMenu [data-app-open=settings]').click();}
   await page.locator('#view-integrations.active').waitFor();
   const tablist=page.locator('.settings-family .app-tab-picker'),employeeTab=page.locator('[data-settings-page=employees]');
   if(await tablist.isVisible())await tablist.selectOption({label:(await employeeTab.textContent()).trim()});
   else await employeeTab.click();
   await page.locator('#view-employees.active').waitFor();assert.equal(new URL(page.url()).searchParams.get('view'),'employees');
   assert.deepEqual(errors,[]);await page.close();console.log('PASS Settings family without General tab: '+width);
  }
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
