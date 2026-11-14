/* Real production bundle with synthetic authorized API data; no production writes. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'../.support-test-deps/node_modules/playwright');
const {installCompanyEmployeesFixture}=require('./company_employees_entry_fixture.cjs');
const docs=path.join(__dirname,'../docs'),assets=['tenant-operator-i18n.js','tenant-request-coordinator.js','tenant-view-demand.js','tenant-workspace.js','tenant-members-ui.js','tenant-chakra-ui.js'];
const html=fs.readFileSync(path.join(docs,'tenant-app.html'),'utf8').replace(/<script\b([^>]*)src="([^"]+)"[^>]*><\/script>/gi,(tag,attrs,url)=>{const name=path.basename(url);return assets.includes(name)?'<script>'+fs.readFileSync(path.join(docs,name),'utf8').replace(/<\/script/gi,'<\\/script')+'</script>':'';});
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});try{
for(const width of [390,1440])for(const language of ['ru','az','en']){
 const page=await browser.newPage({viewport:{width,height:900}}),errors=[],writes=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(installCompanyEmployeesFixture,{role:'owner',settings:true,pages:{profile:true,settings:true,integrations:true,employees:true},modules:{integrations:true}});
 await page.addInitScript(()=>{const original=window.fetch;window.fetch=(input,options={})=>{if(String(input).includes('/notification-preferences'))return Promise.resolve(new Response(JSON.stringify({success:true,events:[{key:'task_assigned',label:'Yeni tapşırıq',channels:['internal','push','telegram']},{key:'task_completion_requested',label:'Tapşırıq tamamlanması üçün təsdiq',channels:['internal','push','telegram']}],channels_available:['internal','push','telegram'],preferences:{task_assigned:{internal:true}}}),{status:200,headers:{'Content-Type':'application/json'}}));return original(input,options)};});
 await page.route('**/*',route=>route.request().isNavigationRequest()?route.fulfill({contentType:'text/html',body:html}):route.abort());
 await page.goto('https://fixture.local/app?view=settings&lang='+language);
 await page.locator('#view-profile.active').waitFor();
 await page.locator('[data-app-language]').selectOption(language);
 await page.locator('.notification-preference-row').first().waitFor();
 await page.locator('.notification-channels input[type=checkbox]').first().waitFor({state:'attached'});
 assert.equal(await page.locator('.notification-channels input[type=checkbox]').count(),6);
 const visibleTitle=page.locator('#view-settings > .top');assert.equal(await visibleTitle.isVisible(),false);
 assert.equal(new URL(page.url()).searchParams.get('view'),'profile','old General route is redirected');
 assert.equal(await page.locator('[data-settings-page=settings]').count(),0,'General is removed from Settings');
 await page.locator('.app-avatar-button').click();await page.locator('#appUserMenu [data-app-open=settings]').click();await page.locator('#view-integrations.active').waitFor();
 for(const view of ['integrations','employees']){
 const tab=page.locator('[data-settings-page='+view+']');assert.equal(await tab.isVisible(),true);await tab.click();await page.locator('#view-'+view+'.active').waitFor();
 assert(await page.locator('.settings-family').evaluate((el,view)=>!!(el.compareDocumentPosition(document.getElementById('view-'+view))&Node.DOCUMENT_POSITION_FOLLOWING),view));
 }
 await page.evaluate(()=>window.tenantAppNavigate('settings',true));await page.locator('#view-profile.active').waitFor();
 await page.locator('.notification-preference-row').first().waitFor();
 const overflow=await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,nodes:[...document.querySelectorAll('body *')].filter(n=>{const r=n.getBoundingClientRect();return r.width>0&&(r.right>innerWidth+2||r.left< -2)}).slice(0,12).map(n=>({text:(n.innerText||n.textContent||'').slice(0,120),tag:n.tagName,id:n.id,cls:String(n.className||''),left:Math.round(n.getBoundingClientRect().left),right:Math.round(n.getBoundingClientRect().right),width:Math.round(n.getBoundingClientRect().width),css:{width:getComputedStyle(n).width,minWidth:getComputedStyle(n).minWidth,maxWidth:getComputedStyle(n).maxWidth,boxSizing:getComputedStyle(n).boxSizing},parent:n.parentElement&&{tag:n.parentElement.tagName,id:n.parentElement.id,cls:String(n.parentElement.className||''),left:Math.round(n.parentElement.getBoundingClientRect().left),right:Math.round(n.parentElement.getBoundingClientRect().right),text:(n.parentElement.innerText||n.parentElement.textContent||'').slice(0,160),width:Math.round(n.parentElement.getBoundingClientRect().width),css:{width:getComputedStyle(n.parentElement).width,minWidth:getComputedStyle(n.parentElement).minWidth,maxWidth:getComputedStyle(n.parentElement).maxWidth,boxSizing:getComputedStyle(n.parentElement).boxSizing}}}))}));assert(overflow.scrollWidth<=overflow.width+1,'page must not overflow: '+JSON.stringify(overflow));
 for(const label of await page.locator('.notification-channels label').all()){
 const box=await label.boundingBox();assert(box&&box.x>=0&&box.x+box.width<=width+1,'notification controls fit within viewport');
 }
 const text=await page.locator('.notification-preferences-card').innerText();if(language==='ru'){assert(text.includes('Новая задача'));assert(!text.includes('Bildiriş'));}
 fs.mkdirSync(path.join(__dirname,'artifacts/settings-layout'),{recursive:true});await page.screenshot({path:path.join(__dirname,`artifacts/settings-layout/${language}-${width}.png`),fullPage:true});
 assert.deepEqual(errors,[]);await page.close();console.log('PASS Settings visual layout '+language+' '+width);
}
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
