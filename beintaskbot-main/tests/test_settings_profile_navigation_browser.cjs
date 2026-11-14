const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'../.support-test-deps/node_modules/playwright');
const {root,docs,installCompanyEmployeesFixture}=require('./company_employees_entry_fixture.cjs');

const inlineAssets=new Set(['tenant-operator-i18n.js','tenant-crm-save-receipt.js','tenant-crm-sync-history.js','tenant-request-coordinator.js','tenant-view-demand.js','tenant-workspace.js','tenant-members-ui.js','tenant-chakra-ui.js']);
function appHtml(){
 const bundle=fs.readFileSync(path.join(docs,'tenant-chakra-ui.js'),'utf8');
 return fs.readFileSync(path.join(docs,'tenant-app.html'),'utf8').replace(/<script\b([^>]*)src="([^"]+)"[^>]*><\/script>/gi,(tag,attrs,url)=>{
  const name=path.basename(url);if(!inlineAssets.has(name))return '';
  const code=name==='tenant-chakra-ui.js'?bundle:fs.readFileSync(path.join(docs,name),'utf8');
  return '<script>'+code.replace(/<\/script/gi,'<\\/script')+'</script>';
 }).replace('<link rel="stylesheet" href="/assets/tenant-members-ui.css">','<style>'+fs.readFileSync(path.join(docs,'tenant-members-ui.css'),'utf8')+'</style>');
}
const html=appHtml();
const screenshots=path.resolve(__dirname,'../../artifacts/settings-profile-navigation');
const words={
 ru:{profile:'Профиль',settings:'Настройки',integrations:'Интеграции',employees:'Сотрудники'},
 az:{profile:'Profil',settings:'Tənzimləmələr',integrations:'İnteqrasiyalar',employees:'Əməkdaşlar'},
 en:{profile:'Profile',settings:'Settings',integrations:'Integrations',employees:'Employees'},
};
const settle=()=>new Promise(resolve=>setTimeout(resolve,120));

(async()=>{
 fs.mkdirSync(screenshots,{recursive:true});
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--no-sandbox','--disable-gpu']});
 try{
  for(const width of [390,1440])for(const language of ['ru','az','en']){
   const page=await browser.newPage({viewport:{width,height:900}}),errors=[];
   page.on('pageerror',error=>errors.push(error.message));
   const account={first_name:'Lale',last_name:'Owner',phone:'+994501234567',login:'lale.owner',avatar_url:'',email:'lale@example.test',verified:true};
   await page.addInitScript(installCompanyEmployeesFixture,{role:'owner',settings:true,pages:{profile:true,settings:true,integrations:true,employees:true},modules:{integrations:true},account});
   await page.route('**/*',route=>route.request().isNavigationRequest()?route.fulfill({contentType:'text/html',body:html}):route.abort());
   await page.goto('https://fixture.local/app?view=settings');
   await page.locator('#view-profile.active').waitFor();
   await page.locator('[data-app-language]').selectOption(language);
   await page.locator('#profileName').waitFor();
   await page.locator('#profile-account-settings input').first().waitFor();
   assert.equal(await page.locator('#profilePageTitle').textContent(),words[language].profile);
   assert.equal(await page.locator('#profile-account-settings input').nth(0).inputValue(),'Lale','saved account values stay bound to the original API');
   assert.equal(await page.locator('#profile-account-settings input').nth(1).inputValue(),'Owner');
   assert.equal(new URL(page.url()).searchParams.get('view'),'profile','legacy General URL redirects to Profile');
   assert.equal(await page.locator('.nav [data-view=settings]').isVisible(),false,'Settings is absent from the desktop and mobile navigation');
   assert.equal(await page.locator('#settingsFamilyNav').isVisible(),false,'hidden legacy entry is not exposed in the sidebar');
   assert.equal(await page.locator('[data-settings-page=settings]').count(),0,'Settings no longer has a General tab');
   assert.equal(await page.locator('#chakra-settings-root').evaluate(node=>node.closest('#view-profile')!==null),true);
   assert.equal(await page.locator('#chakra-notification-settings-root').evaluate(node=>node.closest('#view-profile')!==null),true);
   assert.equal(await page.locator('#integrationInfo').evaluate(node=>node.closest('#legacyIntegrationSettings')!==null),true);
   assert.equal(await page.locator('#inviteName').evaluate(node=>node.closest('#legacyEmployeeSettings')!==null),true);
   if(width===390)await page.locator('#profile-account-settings').evaluate(node=>{const top=node.getBoundingClientRect().top+scrollY;document.documentElement.style.scrollBehavior='auto';scrollTo(0,Math.max(0,top-80));});
   else await page.locator('#profile-account-settings input').first().evaluate(node=>node.scrollIntoView({block:'center'}));
   if(language==='ru'&&width===1440)await page.screenshot({path:path.join(screenshots,'owner-profile-desktop.png')});
   if(language==='az'&&width===390)await page.screenshot({path:path.join(screenshots,'owner-profile-mobile.png')});
   const companySettingsPicker=page.locator('#profile-company-settings .app-tab-picker');
   if(await companySettingsPicker.isVisible())await companySettingsPicker.selectOption({label:language==='ru'?'Компании':language==='en'?'Companies':'Şirkətlər'});
   else await page.locator('#profile-company-settings [data-settings-tab=companies]').click();
   await page.locator('#companyManagement').waitFor({state:'visible'});

   await page.evaluate(()=>{document.documentElement.style.scrollBehavior='auto';window.scrollTo(0,0)});await page.waitForTimeout(80);await page.locator('.app-avatar-button').click();await page.waitForTimeout(250);
   const menu=page.locator('#appUserMenu');await menu.waitFor({state:'visible'});
   assert.equal(await menu.locator('[data-app-open=settings]').textContent().then(text=>text.trim()),words[language].settings);
   if(language==='en'&&width===1440)await page.screenshot({path:path.join(screenshots,'avatar-menu-desktop.png')});
   await menu.locator('[data-app-open=settings]').click();
   await page.locator('#view-integrations.active').waitFor();
   if(language==='ru'&&width===1440)await page.screenshot({path:path.join(screenshots,'settings-desktop.png')});
   if(language==='az'&&width===390)await page.screenshot({path:path.join(screenshots,'settings-mobile.png')});
   assert.equal(new URL(page.url()).searchParams.get('view'),'integrations','avatar Settings opens first permitted tab');
   assert.equal(await page.locator('[data-settings-page=integrations]').getAttribute('aria-selected'),'true');
   assert.equal(await page.locator('[data-settings-page=integrations]').textContent(),words[language].integrations);
   await page.locator('[data-settings-page=employees]').click();
   await page.locator('#view-employees.active').waitFor();
   assert.equal(new URL(page.url()).searchParams.get('view'),'employees','Employees route remains deep-linkable');
   assert.equal(await page.locator('[data-settings-page=employees]').textContent(),words[language].employees);
   await page.goBack();await page.locator('#view-integrations.active').waitFor();
   await page.goForward();await page.locator('#view-employees.active').waitFor();
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'page fits viewport');
   assert.deepEqual(errors,[],'no browser errors for owner UI '+language+' '+width);
   await page.close();
   console.log('PASS owner Profile/Settings navigation '+language+' '+width);
  }

  for(const width of [390,1440]){
   const page=await browser.newPage({viewport:{width,height:900}}),errors=[];page.on('pageerror',error=>errors.push(error.message));
   await page.addInitScript(installCompanyEmployeesFixture,{role:'worker',canManage:false,canViewEmployees:true,settings:false,pages:{profile:true,settings:false,integrations:false,employees:true},modules:{integrations:false}});
   await page.route('**/*',route=>route.request().isNavigationRequest()?route.fulfill({contentType:'text/html',body:html}):route.abort());
   await page.goto('https://fixture.local/app?view=settings');await page.locator('#view-profile.active').waitFor();
   assert.equal(await page.locator('#profile-company-settings [data-settings-tab=companies]').isVisible(),false,'employee cannot see company controls through Profile');
   assert.equal(await page.locator('#companyManagement').count(),0,'owner-only company UI is removed for employee');
   assert.equal(await page.locator('.nav [data-view=settings]').isVisible(),false);
   assert.equal(await page.locator('[data-settings-page=integrations]').isVisible(),false);
   assert.equal(await page.locator('[data-settings-page=employees]').evaluate(node=>!node.hidden),true,'employee retains only the permitted settings tab');
   assert.equal(await page.evaluate(()=>window.tenantOpenSettingsSection('companies')),false,'profile cannot be used to enter a company settings section without the existing permission');
   assert.equal(await page.evaluate(()=>window.tenantAppNavigate('integrations',true)),false,'legacy direct integration route remains denied');
   assert.equal(await page.locator('#view-profile').evaluate(node=>node.classList.contains('active')),true,'denied route does not move the employee to an unauthorized page');
   await page.locator('.app-avatar-button').click();await page.locator('#appUserMenu [data-app-open=settings]').click();
   await page.locator('#view-employees.active').waitFor();
   assert.equal(new URL(page.url()).searchParams.get('view'),'employees','avatar Settings opens the first allowed tab for employee');
   if(width===390)await page.screenshot({path:path.join(screenshots,'employee-settings-mobile.png'),fullPage:true});
   else await page.screenshot({path:path.join(screenshots,'employee-settings-desktop.png'),fullPage:true});
   assert.deepEqual(errors,[],'no browser errors for employee UI '+width);await page.close();
   console.log('PASS employee isolation and first permitted Settings tab '+width);
  }

  const restrictedAdmin=await browser.newPage({viewport:{width:1440,height:900}});
  await restrictedAdmin.addInitScript(installCompanyEmployeesFixture,{role:'admin',canManage:false,canViewEmployees:true,settings:false,pages:{profile:true,settings:false,integrations:false,employees:true},modules:{integrations:false}});
  await restrictedAdmin.route('**/*',route=>route.request().isNavigationRequest()?route.fulfill({contentType:'text/html',body:html}):route.abort());
  await restrictedAdmin.goto('https://fixture.local/app?view=settings');await restrictedAdmin.locator('#view-profile.active').waitFor();
  await restrictedAdmin.waitForFunction(()=>document.getElementById('integrationInfo')?.textContent!=='Yüklənir…');
  assert.equal(await restrictedAdmin.locator('#companyManagement').count(),0,'admin without company-settings permission has no company editor');
  assert.equal(await restrictedAdmin.locator('[data-settings-page=integrations]').isVisible(),false);
  const restrictedCalls=await restrictedAdmin.evaluate(()=>window.companyEmployeesFixture.calls.map(call=>call.path));
  assert.equal(restrictedCalls.some(path=>path.startsWith('/api/platform/integrations/')||path==='/api/platform/connect/access'||path==='/api/platform/companies'),false,'Profile does not fetch restricted integration or company settings');
  await restrictedAdmin.close();console.log('PASS admin profile loads without unauthorized company settings requests');

  const savePage=await browser.newPage({viewport:{width:1440,height:900}});
  await savePage.addInitScript(installCompanyEmployeesFixture,{role:'owner',settings:true,pages:{profile:true,settings:true,integrations:true,employees:true},modules:{integrations:true},account:{first_name:'Lale',last_name:'Owner',phone:'+994501234567',login:'lale.owner',email:'lale@example.test'}});
  await savePage.route('**/*',route=>route.request().isNavigationRequest()?route.fulfill({contentType:'text/html',body:html}):route.abort());
  await savePage.goto('https://fixture.local/app?view=profile');await savePage.locator('#profile-account-settings input').first().waitFor();
  await savePage.locator('#profile-account-settings input').first().fill('Updated');
  await savePage.getByRole('button',{name:'Profili saxla'}).click();
  await savePage.waitForFunction(()=>window.companyEmployeesFixture.calls.some(call=>call.path==='/api/platform/account'&&call.method==='POST'));
  await savePage.reload();await savePage.locator('#profile-account-settings input').first().waitFor();
  assert.equal(await savePage.locator('#profile-account-settings input').first().inputValue(),'Updated','profile saves through the existing account API and survives refresh');
  await savePage.close();
  console.log('PASS existing profile API save and refresh');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
