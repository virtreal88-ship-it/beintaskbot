const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
let browser;
(async()=>{
 browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.CHROMIUM_EXECUTABLE_PATH}:{})});
 const page=await browser.newPage({viewport:{width:390,height:844}});
 await page.route('https://risk.test/**',route=>{
  if(route.request().url().includes('/api/platform/risks/alerts/'))return route.fulfill({json:{success:true,alert:{
   source_kind:'linear',source:'Linear',source_title:'Qiymətə dəyişiklik',rule_id:'high_priority_unassigned',
   source_url:'https://linear.app/example/issue/BS-1513',checked_at:'2026-11-08T08:00:00Z',
   context:{identifier:'BS-1513',title:'Qiymətə dəyişiklik',excerpt:'Кассир, по сообщению команды, может менять цену при отключённом праве.',project:'Baytash',priority:1,assigned:false,status:'Todo',topic:'price_access',source_mode:'live'}
  }}});
  return route.fulfill({contentType:'text/html',body:'<!doctype html><html lang="ru"><body><button id="outside">Outside</button></body></html>'});
 });
 await page.goto('https://risk.test/app?risk_alert_id=01a1b1af-4d2c-71f2-aaeb-f01eccac0031');
 await page.evaluate(()=>{window.tenantI18n={language:'ru'};window.tenantProfileSnapshot={member:{tenant_id:'tenant-a',telegram_id:101,role:'owner',active:true}};});
 await page.addScriptTag({content:fs.readFileSync(path.join(__dirname,'../docs/tenant-risk-alert-detail.js'),'utf8')});
 await page.getByText('BS-1513',{exact:false}).waitFor();
 const dialog=page.getByRole('dialog');
 assert((await dialog.innerText()).includes('правила цен, оплаты и доступа'));
 assert((await dialog.innerText()).includes('Срочный'));
 assert(!((await dialog.innerText()).includes('уверенность')));
 const bounds=await dialog.boundingBox();assert(bounds.width<=358&&bounds.x>=16);
 assert(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth));
 await page.getByRole('button',{name:'Закрыть'}).focus();
 await page.keyboard.press('Shift+Tab');assert.equal(await page.evaluate(()=>document.activeElement.tagName),'A');
 await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.activeElement.textContent),'Закрыть');
 if(process.env.RISK_SCREENSHOT)await page.screenshot({path:process.env.RISK_SCREENSHOT});
 for(const lang of ['az','en']){
  await page.evaluate(lang=>{window.tenantI18n.language=lang;document.dispatchEvent(new CustomEvent('tenant-language'));},lang);
  assert((await dialog.innerText()).includes(lang==='az'?'Nə baş verib':'What happened'));
 }
 await page.keyboard.press('Escape');assert.equal(await dialog.count(),0);
 assert(!page.url().includes('risk_alert_id'));
 await browser.close();console.log('PASS: mobile risk detail, overflow, focus trap, three locales, Escape.');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{await browser?.close();});
