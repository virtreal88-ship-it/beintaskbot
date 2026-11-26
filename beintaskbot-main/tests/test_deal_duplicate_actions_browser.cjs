const fs=require('node:fs'),assert=require('node:assert/strict');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});try{
for(const width of [390,1024,1440])for(const theme of ['light','dark']){
 const page=await browser.newPage({viewport:{width,height:900}});
 const html=fs.readFileSync('docs/tenant-app.html','utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,'');
 await page.setContent(html);await page.evaluate(theme=>{document.documentElement.dataset.appTheme=theme;window.tenantI18n={language:'ru'};window.calls=[];window.reply={success:true,phone:'+994551234567',deals:[{id:9,name:'Duplicate',contact_name:'<img onerror=alert(1)>',phone:'0551234567'}]};window.fetch=async(url,opts={})=>{calls.push(String(url));if(String(url).includes('/duplicates?')){if(window.delayed)await new Promise(r=>window.finish=r);return {ok:true,json:async()=>reply};}return {ok:true,json:async()=>({success:true,items:[],result:{tickets:[],members:[],channels:[],unresolved_count:0}})};};window.tenantTaskFlow={capture:()=>({}),current:()=>true,read:async()=>({tasks:[],sources:[]}),api:async()=>({notes:[]})};},theme);
 for(const name of ['tenant-operator-i18n.js','tenant-kommo-deal-card.js','tenant-support-inbox.js','deal-phone-duplicates.js'])await page.addScriptTag({content:fs.readFileSync('docs/'+name,'utf8')});
 await page.evaluate(()=>{
 tenantI18n.setLanguage('ru');document.dispatchEvent(new CustomEvent('tenant-profile',{detail:{member:{tenant_id:'fixture',telegram_id:11,role:'manager',active:true,permissions:['deals','support','dialogs']},capabilities:{modules:{deals:true,support:true,dialogs:true}}}}));
 document.getElementById('dealModal').classList.add('show');const host=document.getElementById('modalContent');host.innerHTML='<button data-duplicate-search="7" data-duplicate-tenant="true" data-duplicate-phone="0551234567">Find</button><div class="chat-placeholder"><div id="chatRows">History</div><div><textarea id="chatText"></textarea></div></div>';
 tenantKommoDealCard.mount({host,deal:{kommo_lead_id:7,contact_name:'Client',phone:'0551234567'},isCurrent:()=>true});document.dispatchEvent(new CustomEvent('tenant-chat-open',{detail:{lead_id:7}}));
 });
 await page.locator('#dealModal>.modal-card').evaluate(async n=>{await Promise.all(n.getAnimations().map(a=>a.finished));});
 if(width>=900){
  const chat=await page.locator('.chat-placeholder').boundingBox(),info=await page.locator('#dealInfoPanel').boundingBox();assert(info.x+info.width<=chat.x+1,'data left, chat right');
  const support=page.locator('#supportCreateFromChat'),duplicate=page.locator('[data-duplicate-search]');assert(await support.isVisible());
  assert(await duplicate.evaluate(n=>n.previousElementSibling.id==='supportCreateFromChat'),'adjacent actions');
  await duplicate.click();await page.locator('[data-open="9"]').waitFor();assert.equal(await page.locator('.phone-duplicates-dialog input').count(),0);assert.equal(await page.locator('.phone-duplicates-result img').count(),0);
  assert.equal(await page.evaluate(()=>calls.filter(x=>x.includes('/duplicates?')).length),1);assert(await page.evaluate(()=>calls.some(x=>x.includes('phone=0551234567')&&x.includes('expected_tenant_id=fixture'))));
  await page.locator('[data-close]').click();await page.evaluate(()=>reply={success:true,deals:[]});await duplicate.click();await page.waitForFunction(()=>document.querySelector('.phone-duplicates-dialog [role=status]').textContent.includes('не найдено'));await page.locator('[data-close]').click();
  await duplicate.evaluate(n=>n.dataset.duplicatePhone='');const before=await page.evaluate(()=>calls.length);await duplicate.click();assert((await page.locator('.phone-duplicates-dialog').textContent()).includes('нет номера'));assert.equal(await page.evaluate(()=>calls.length),before);await page.locator('[data-close]').click();
  await duplicate.evaluate(n=>n.dataset.duplicatePhone='0551234567');await page.evaluate(()=>delayed=true);await duplicate.click();await page.waitForFunction(()=>!!window.finish);await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-chat-close')));await page.evaluate(()=>finish());assert.equal(await page.locator('.phone-duplicates-overlay').count(),0);
  await page.setViewportSize({width:390,height:900});await page.waitForFunction(()=>!document.querySelector('[data-duplicate-search]').hasAttribute('data-duplicate-auto'));assert(await page.locator('#dealInfoPanel').isVisible());assert.equal(await duplicate.getAttribute('data-duplicate-auto'),null);assert.equal(await duplicate.evaluate(n=>n.closest('#dealInfoPanel')!==null),true);
 }else{assert(await page.locator('#supportCreateFromChat').isVisible());await page.locator('[data-duplicate-search]').click();assert.equal(await page.locator('.phone-duplicates-dialog input').count(),1);assert.equal(await page.evaluate(()=>calls.filter(x=>x.includes('/duplicates?')).length),0);}
 await page.close();
}
console.log('PASS desktop actions, auto search, missing/empty/escaped results, stale response, mobile and responsive sidebar in both themes');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
