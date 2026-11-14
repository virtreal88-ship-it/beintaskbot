const {chromium}=require('../.support-test-deps/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
const html=fs.readFileSync('docs/tenant-app.html','utf8');
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});try{
for(const width of [320,360,390,430,1280])for(const theme of ['light','dark']){
 const page=await browser.newPage({viewport:{width,height:900}});
 const styles=[...html.matchAll(/<style[^>]*>[\s\S]*?<\/style>/g)].map(m=>m[0]).join('');
 const sections=['tasks','deals'].map(v=>html.slice(html.indexOf('<section class="view'+(v==='tasks'?' active':'')+'" id="view-'+v+'"'),html.indexOf('</section>',html.indexOf('id="view-'+v+'"'))+10)).join('');
 await page.setContent(`<html data-app-theme="${theme}"><head>${styles}</head><body><main style="padding:16px;max-width:100%;box-sizing:border-box">${sections}</main></body></html>`);
 await page.evaluate(()=>{window.tenantI18n={language:'ru'};for(const view of ['tasks','deals']){const refresh=document.getElementById(view+'Refresh'),sync=document.createElement('button');sync.textContent='Административная синхронизация Kommo';refresh.after(sync);refresh.onclick=()=>window.refreshed=(window.refreshed||0)+1;sync.onclick=()=>window.synced=true;}document.getElementById('taskFeedSource').value='kommo';document.getElementById('taskStatus').value='open';document.getElementById('taskSearch').oninput=()=>window.searched=document.getElementById('taskSearch').value;document.getElementById('nativeTaskCreate').onclick=()=>window.created=true;});
 await page.addScriptTag({content:fs.readFileSync('docs/tenant-chakra-ui.js','utf8')});
 await page.evaluate(()=>{window.fetch=async()=>new Response(JSON.stringify({success:true,catalog:[],hidden_stage_keys:[]}));window.tenantChakraCRM.renderTasks([],{source:'native',target:'nativeTasksList',onOpen:()=>{}});window.tenantChakraCRM.renderDeals('dealsList',[],false,()=>{});});
 await page.addScriptTag({content:fs.readFileSync('docs/tenant-mobile-crm-toolbar.js','utf8')});
 await page.locator('[data-task-view-controls]').waitFor();
 const mobile=width<=700;
 assert.equal(await page.locator('.crm-mobile-toolbar').count(),mobile?2:0);
 if(mobile){
   const panel=page.locator('#view-tasks .crm-mobile-toolbar');assert((await panel.boundingBox()).height<=130,'compact two-row toolbar');
   assert.equal(await page.locator('#taskSearch').isVisible(),false);await panel.locator('.crm-mobile-search-toggle').click();await page.locator('#taskSearch').fill('клиент');assert.equal(await page.evaluate(()=>window.searched),'клиент');
   await page.keyboard.press('Escape');assert.equal(await page.locator('#taskSearch').isVisible(),false);assert.equal(await page.locator('#taskSearch').inputValue(),'клиент');
   await panel.locator('.crm-mobile-search-toggle').click();assert.equal(await page.locator('#taskSearch').inputValue(),'клиент');
   await page.locator('#tasksRefresh').click();assert.equal(await page.evaluate(()=>window.refreshed),1);await page.locator('#nativeTaskCreate').click();assert.equal(await page.evaluate(()=>window.created),true);
   await page.evaluate(()=>{document.getElementById('tasksRefresh').textContent='Обновить список';document.getElementById('tasksRefresh').disabled=true;});await page.waitForTimeout(20);assert.equal(await page.locator('#tasksRefresh').getAttribute('aria-label'),'Обновить список');assert(await page.locator('#tasksRefresh').isDisabled());
   for(const id of ['nativeTaskCreate','tasksRefresh']){const b=await page.locator('#'+id).boundingBox();assert(b.width>=36&&b.height>=44);}
   await page.setViewportSize({width:1280,height:900});await page.waitForFunction(()=>document.querySelectorAll('.crm-mobile-toolbar').length===0);assert.equal(await page.locator('.crm-mobile-toolbar').count(),0);assert.equal(await page.locator('#taskSearch').inputValue(),'клиент');assert.equal(await page.locator('#taskFeedSource').inputValue(),'kommo');assert.equal(await page.locator('#taskStatus').inputValue(),'open');
   await page.setViewportSize({width,height:900});await page.waitForFunction(()=>document.querySelectorAll('.crm-mobile-toolbar').length===2);assert.equal(await page.locator('#taskSearch').isVisible(),true);assert.equal(await page.locator('#taskSearch').inputValue(),'клиент');
 }
 for(const v of ['tasks','deals']){await page.evaluate(v=>{for(const id of ['tasks','deals'])document.getElementById('view-'+id).classList.toggle('active',id===v);},v);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${width} ${v} no overflow`);if(mobile){const p=page.locator('#view-'+v+' .crm-mobile-toolbar');if(v==='deals')assert((await p.boundingBox()).height<=130,'deals compact');
 const geometry=await p.evaluate(el=>({buttons:[...el.querySelectorAll('button')].filter(b=>b.getClientRects().length).map(b=>{const r=b.getBoundingClientRect(),s=b.querySelector('svg'),sr=s?.getBoundingClientRect();return{x:r.x,y:r.y,w:r.width,h:r.height,svg:sr?{x:sr.x,y:sr.y,w:sr.width,h:sr.height}:null};}),selects:[...el.querySelectorAll('select')].filter(s=>s.getClientRects().length).map(s=>({y:s.getBoundingClientRect().y}))}));
 assert(geometry.buttons.length>=6, 'all actions visible');
 for(const b of geometry.buttons){assert(Math.abs(b.y-geometry.buttons[0].y)<1,`${v} all buttons in first row`);assert(b.w>=36&&b.h>=44,'usable tap target');if(b.svg){assert(Math.abs(b.svg.x+b.svg.w/2-b.x-b.w/2)<1,'icon horizontally centered');assert(Math.abs(b.svg.y+b.svg.h/2-b.y-b.h/2)<1,'icon vertically centered');}}
 for(const s of geometry.selects)assert(s.y>=geometry.buttons[0].y+44,`${v} dropdowns below actions`);
 }}
 fs.mkdirSync('tests/artifacts/mobile-crm-toolbar',{recursive:true});await page.screenshot({path:`tests/artifacts/mobile-crm-toolbar/${width}-${theme}.png`});await page.close();
}console.log('PASS compact CRM toolbars, real page styles, React controls, breakpoint/state/keyboard/callback preservation');}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
