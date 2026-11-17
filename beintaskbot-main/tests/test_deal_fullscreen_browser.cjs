const fs=require('node:fs'),assert=require('node:assert/strict');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});try{
for(const width of [320,390,768,1024,1440])for(const theme of ['light','dark']){
 const page=await browser.newPage({viewport:{width,height:900}});
 const html=fs.readFileSync('docs/tenant-app.html','utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,'');
 await page.setContent(html);await page.addStyleTag({content:fs.readFileSync('ui/src/app-theme.css','utf8')});
 await page.evaluate(theme=>{document.body.dataset.appUi='kwid';document.documentElement.dataset.appTheme=theme;window.tenantI18n={language:'ru'};window.tenantTaskFlow={capture:()=>({}),current:()=>true,read:async()=>({tasks:[],sources:[]}),api:async()=>({notes:[]})};},theme);
 await page.addScriptTag({content:fs.readFileSync('docs/tenant-kommo-deal-card.js','utf8')});
 const card=page.locator('#dealModal>.modal-card');
 const assertViewport=async()=>{await card.evaluate(async n=>{await Promise.all(n.getAnimations().map(a=>a.finished));});const b=await card.boundingBox();assert(Math.abs(b.x)<1&&Math.abs(b.y)<1&&Math.abs(b.width-width)<1&&Math.abs(b.height-900)<1,'edge-to-edge '+width+' '+theme+' '+JSON.stringify(b));assert.equal(await card.evaluate(n=>getComputedStyle(n).borderRadius),'0px');assert.equal(await card.evaluate(n=>getComputedStyle(n).borderTopWidth),'0px');assert(await card.evaluate(n=>n.scrollWidth<=n.clientWidth+1),'no card horizontal overflow');};
 await page.evaluate(()=>{document.getElementById('modalContent').textContent='Loading…';document.getElementById('dealModal').classList.add('show');});
 assert.equal(await card.evaluate(n=>getComputedStyle(n).animationName),'deal-slide-in');
 const frames=await card.evaluate(n=>n.getAnimations()[0]?.effect.getKeyframes().map(f=>f.transform));assert.deepEqual(frames,['translateX(100%)','translateX(0px)'],'opens from right to left');
 await assertViewport();
 await page.evaluate(()=>{const host=document.getElementById('modalContent');host.innerHTML='<div class="chat-placeholder"><div id="chatRows">History</div><div><textarea id="chatText"></textarea></div></div>';tenantKommoDealCard.mount({host,deal:{kommo_lead_id:7,contact_name:'Client',phone:'0551234567'},isCurrent:()=>true});});
 await assertViewport();
 if(width>=1024){const chat=await page.locator('.chat-placeholder').boundingBox(),info=await page.locator('#dealInfoPanel').boundingBox();assert(chat.x+chat.width<=info.x+1,'chat left and info right retained');}else{assert(await page.locator('#dealInfoPanel').evaluate(n=>n.hidden));await page.locator('.deal-info-toggle').click();assert(await page.locator('.chat-placeholder').evaluate(n=>n.hidden));await page.locator('.deal-info-toggle').click();assert(!(await page.locator('.chat-placeholder').evaluate(n=>n.hidden)));}
 await page.evaluate(()=>document.getElementById('dealModal').classList.remove('show'));assert(!(await card.isVisible()));
 await page.evaluate(()=>document.getElementById('dealModal').classList.add('show'));await assertViewport();
 await page.emulateMedia({reducedMotion:'reduce'});assert.equal(await card.evaluate(n=>getComputedStyle(n).animationName),'none');
 await page.evaluate(()=>{document.getElementById('modalContent').textContent='Failed to load';});await assertViewport();
 await page.evaluate(()=>{document.getElementById('dealModal').classList.remove('show');document.getElementById('nativeContactModal').classList.add('show');});assert.equal(await page.locator('#nativeContactModal>.modal-card').evaluate(n=>getComputedStyle(n).borderRadius),'12px','unrelated dialogs retain shell style');
 await page.close();
}console.log('PASS fullscreen deal: loading/content/error/reopen, slide right-to-left, reduced motion, desktop sidebar/mobile Info, both themes and 5 widths');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
