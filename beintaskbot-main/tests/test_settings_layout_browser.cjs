/* Real production bundle with synthetic authorized API data; no production writes. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'../.support-test-deps/node_modules/playwright');
const {installCompanyEmployeesFixture}=require('./company_employees_entry_fixture.cjs');
const docs=path.join(__dirname,'../docs'),assets=['tenant-operator-i18n.js','tenant-request-coordinator.js','tenant-view-demand.js','tenant-workspace.js','tenant-members-ui.js','tenant-chakra-ui.js'];
const html=fs.readFileSync(path.join(docs,'tenant-app.html'),'utf8').replace(/<script\b([^>]*)src="([^"]+)"[^>]*><\/script>/gi,(tag,attrs,url)=>{const name=path.basename(url);return assets.includes(name)?'<script>'+fs.readFileSync(path.join(docs,name),'utf8').replace(/<\/script/gi,'<\\/script')+'</script>':'';});
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});try{
for(const width of [390,1440])for(const language of ['ru','az','en']){
 const page=await browser.newPage({viewport:{width,height:900}}),errors=[],writes=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(installCompanyEmployeesFixture,{role:'owner',settings:true,modules:{integrations:true}});
 await page.addInitScript(()=>{const original=window.fetch;window.fetch=(input,options={})=>{if(String(input).includes('/notification-preferences'))return Promise.resolve(new Response(JSON.stringify({success:true,events:[{key:'task_assigned',label:'Yeni tapşırıq',channels:['internal','push','telegram']},{key:'task_completion_requested',label:'Tapşırıq tamamlanması üçün təsdiq',channels:['internal','push','telegram']}],channels_available:['internal','push','telegram'],preferences:{task_assigned:{internal:true}}}),{status:200,headers:{'Content-Type':'application/json'}}));return original(input,options)};});
 await page.route('**/*',route=>route.request().isNavigationRequest()?route.fulfill({contentType:'text/html',body:html}):route.abort());
 await page.goto('https://fixture.local/app?view=settings&lang='+language);
 await page.locator('.notification-preference-row').first().waitFor();
 await page.locator('.notification-channels input[type=checkbox]').first().waitFor({state:'attached'});
 assert.equal(await page.locator('.notification-channels input[type=checkbox]').count(),6);
 const visibleTitle=page.locator('#view-settings > .top');assert.equal(await visibleTitle.isVisible(),false);
 for(const view of ['integrations','employees','settings']){
 const tab=page.locator('[data-settings-page='+view+']');assert.equal(await tab.isVisible(),true);await tab.click();await page.locator('#view-'+view+'.active').waitFor();
 assert(await page.locator('.settings-family').evaluate((el,view)=>!!(el.compareDocumentPosition(document.getElementById('view-'+view))&Node.DOCUMENT_POSITION_FOLLOWING),view));
 }
 await page.locator('.notification-preference-row').first().waitFor();
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'page must not overflow');
 for(const label of await page.locator('.notification-channels label').all()){
 const box=await label.boundingBox();assert(box&&box.x>=0&&box.x+box.width<=width+1,'notification controls fit within viewport');
 }
 const text=await page.locator('.notification-preferences-card').innerText();if(language==='ru'){assert(text.includes('Новая задача'));assert(!text.includes('Bildiriş'));}
 fs.mkdirSync(path.join(__dirname,'artifacts/settings-layout'),{recursive:true});await page.screenshot({path:path.join(__dirname,`artifacts/settings-layout/${language}-${width}.png`),fullPage:true});
 assert.deepEqual(errors,[]);await page.close();console.log('PASS Settings visual layout '+language+' '+width);
}
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
