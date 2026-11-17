const {chromium}=require(process.env.TEST_DEPS ? process.env.TEST_DEPS+'/playwright' : '../.support-test-deps/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
const base=process.env.TEST_BASE||'.',html=fs.readFileSync(base+'/docs/tenant-app.html','utf8');
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});try{
for(const width of [320,360,390,430,1280])for(const theme of ['light','dark']){
 const page=await browser.newPage({viewport:{width,height:900}});
 const styles=[...html.matchAll(/<style[^>]*>[\s\S]*?<\/style>/g)].map(m=>m[0]).join('');
 const sections=['tasks','deals'].map(v=>html.slice(html.indexOf('<section class="view'+(v==='tasks'?' active':'')+'" id="view-'+v+'"'),html.indexOf('</section>',html.indexOf('id="view-'+v+'"'))+10)).join('');
 await page.setContent(`<html data-app-theme="${theme}"><head>${styles}</head><body><main style="padding:16px;max-width:100%;box-sizing:border-box">${sections}</main></body></html>`);
 await page.evaluate(()=>{window.tenantI18n={language:'ru'};for(const view of ['tasks','deals']){const refresh=document.getElementById(view+'Refresh'),sync=document.createElement('button');sync.textContent='Синхронизация Kommo';refresh.after(sync);refresh.onclick=()=>window.refreshed=(window.refreshed||0)+1;sync.onclick=()=>window.synced=true;}document.getElementById('taskFeedSource').value='kommo';document.getElementById('taskStatus').value='open';document.getElementById('taskSearch').oninput=()=>window.searched=document.getElementById('taskSearch').value;document.getElementById('nativeTaskCreate').onclick=()=>window.created=true;});
 await page.addScriptTag({content:fs.readFileSync(base+'/docs/tenant-chakra-ui.js','utf8')});
 await page.evaluate(()=>{window.fetch=async()=>new Response(JSON.stringify({success:true,catalog:[],hidden_stage_keys:[]}));window.tenantChakraCRM.renderTasks([],{source:'native',target:'nativeTasksList',onOpen:()=>{}});window.tenantChakraCRM.renderDeals('dealsList',[],false,()=>{});});
 await page.addScriptTag({content:fs.readFileSync(process.env.TEST_SCRIPT||base+'/docs/tenant-mobile-crm-toolbar.js','utf8')});
 await page.locator('[data-task-view-controls]').waitFor({state:'attached'});
 const mobile=width<=700;
 assert.equal(await page.locator('.crm-mobile-toolbar').count(),mobile?2:0);
 if(mobile){
   const panel=page.locator('#view-tasks .crm-mobile-toolbar'),filter=panel.locator('.crm-mobile-filter'),more=panel.locator('.crm-mobile-more');
   assert((await panel.boundingBox()).height<=46,'one compact row');assert.equal(await page.locator('#taskSearch').isVisible(),true,'search visible before disclosure');assert.equal(await panel.locator(':scope > button:visible').count(),3);assert(await page.locator('#nativeTaskCreate').evaluate(async el=>{const url=el.style.getPropertyValue('--crm-create-icon').match(/url\("(.*)"\)/)?.[1];return await new Promise(resolve=>{const image=new Image();image.onload=()=>resolve(image.naturalWidth>0);image.onerror=()=>resolve(false);image.src=url})}),'create SVG mask is a decodable image');
   await filter.click();assert.equal(await page.locator('#taskSearch').isVisible(),true);assert.equal(await page.evaluate(()=>document.activeElement.id==='taskSearch'),false,'no keyboard on open');
   await page.locator('#taskSearch').focus();await page.locator('#taskSearch').fill('клиент');assert.equal(await page.evaluate(()=>window.searched),'клиент');await page.keyboard.press('Escape');assert.equal(await page.locator('#taskSearch').isVisible(),true);assert.equal(await filter.evaluate(el=>el===document.activeElement),true);
   await more.click();await page.locator('#tasksRefresh').click();assert.equal(await page.evaluate(()=>window.refreshed),1);assert.equal(await more.getAttribute('aria-expanded'),'false');await page.locator('#nativeTaskCreate').click();assert.equal(await page.evaluate(()=>window.created),true);
   await more.click();assert.equal(await page.locator('[data-task-view-controls] button').first().isVisible(),true);
   await page.locator('[data-task-view-controls] button').first().click();assert.equal(await more.getAttribute('aria-expanded'),'false');
   await filter.click();await more.click();assert.equal(await filter.getAttribute('aria-expanded'),'false');await more.click();assert.equal(await more.getAttribute('aria-expanded'),'false');
   await page.evaluate(()=>{document.getElementById('tasksRefresh').disabled=true;document.getElementById('nativeTaskCreate').hidden=true;});assert.equal(await page.locator('#nativeTaskCreate').isVisible(),false);await more.click();assert(await page.locator('#tasksRefresh').isDisabled());await page.keyboard.press('Escape');await page.evaluate(()=>document.getElementById('nativeTaskCreate').hidden=false);
   for(let i=0;i<2;i++){await page.setViewportSize({width:1280,height:900});await page.waitForFunction(()=>document.querySelectorAll('.crm-mobile-toolbar').length===0);assert.equal(await page.locator('#taskSearch').inputValue(),'клиент');assert.equal(await page.locator('#taskFeedSource').inputValue(),'kommo');assert.equal(await page.locator('#taskStatus').inputValue(),'open');assert.equal(await page.locator('#view-tasks .top #tasksRefresh').count(),1);await page.setViewportSize({width,height:900});await page.waitForFunction(()=>document.querySelectorAll('.crm-mobile-toolbar').length===2);}
 }
 for(const v of ['tasks','deals']){
   await page.evaluate(v=>{for(const id of ['tasks','deals'])document.getElementById('view-'+id).classList.toggle('active',id===v);document.dispatchEvent(new Event('tenant-view'));},v);
   if(mobile){const p=page.locator('#view-'+v+' .crm-mobile-toolbar');assert.equal(await p.locator(':scope > button:visible').count(),3);const buttons=await p.locator(':scope > button').all();const search=await p.locator('input').boundingBox();const first=await buttons[0].boundingBox();assert(search.x<first.x&&Math.abs(search.y-first.y)<1,'search left, actions right on same row');let previous;for(const button of buttons){const b=await button.boundingBox();assert(b.width>=44&&b.height>=44);if(previous){assert(Math.abs(b.y-previous.y)<1);assert(b.x-previous.x-previous.width>=7);}previous=b;}
     for(const key of ['filter','more']){await p.locator('.crm-mobile-'+key).click();const box=await p.locator('.crm-mobile-'+key+'-panel').boundingBox();assert(box.x>=0&&box.x+box.width<=width);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.keyboard.press('Escape');}
   }
 }
 if(mobile){await page.locator('#view-deals .crm-mobile-more').click();await page.evaluate(()=>document.getElementById('kommoDealSourcePanel').hidden=true);assert.equal(await page.locator('[data-deal-view-controls] button').first().isVisible(),false,'native source does not expose Kommo controls');}
 fs.mkdirSync('tests/artifacts/mobile-crm-toolbar',{recursive:true});await page.screenshot({path:`tests/artifacts/mobile-crm-toolbar/${width}-${theme}.png`});await page.close();
}console.log('PASS 3-button toolbars, real React roots, state/callbacks, keyboard, permissions, viewport and dark/light');}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
