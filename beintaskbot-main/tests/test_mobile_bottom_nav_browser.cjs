const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'../.support-test-deps/node_modules/playwright');
const root=path.resolve(__dirname,'..'),read=p=>fs.readFileSync(path.join(root,p),'utf8');
const html=read('docs/tenant-app.html').replace(/<script\b[\s\S]*?<\/script>/gi,'');
const shell=read('ui/src/app-shell.js').replace("import themeCss from './app-theme.css?inline'",'const themeCss='+JSON.stringify(read('ui/src/app-theme.css')));
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});try{
for(const width of [320,360,390,430,1023,1440])for(const theme of ['light','dark']){
 const page=await browser.newPage({viewport:{width,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.setContent(html);await page.evaluate(()=>document.querySelectorAll('.nav [data-view],.nav [data-report-link]').forEach(el=>{el.hidden=false;el.style.removeProperty('display')}));await page.evaluate(theme=>{window.tenantI18n={language:'ru',t:key=>key};localStorage?.setItem?.('tenant-app-theme',theme)},theme).catch(()=>page.evaluate(()=>window.tenantI18n={language:'ru',t:key=>key}));
 await page.addScriptTag({content:shell});await page.evaluate(theme=>document.documentElement.dataset.appTheme=theme,theme);
 await page.waitForTimeout(80);
 const sidebar=page.locator('.shell>aside'),nav=sidebar.locator('.nav');
 if(width<=1023){
  assert.equal(await page.locator('.app-mobile-menu').isVisible(),false);
  let box=await sidebar.boundingBox();assert(box.y>790&&box.y+box.height<=901,'bottom fixed');
  assert.equal(await sidebar.evaluate(el=>el.inert),false);
  assert(await nav.evaluate(el=>el.scrollWidth>el.clientWidth),'overflow scrolls within bar');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no page overflow');
  const items=nav.locator('[data-view]:visible');const count=await items.count();assert(count>5,'every permitted route is retained');
  await items.last().focus();await page.keyboard.press('Home');assert(await items.first().evaluate(el=>el===document.activeElement));
  await page.keyboard.press('End');assert(await items.last().evaluate(el=>el===document.activeElement));
  let last=await items.last().boundingBox();assert(last.x>=0&&last.x+last.width<=width+1,'last route reachable horizontally');
  await page.evaluate(()=>{const b=document.querySelector('.nav [data-view=tasks]');b.addEventListener('click',()=>window.routeClicks=(window.routeClicks||0)+1);b.click();b.click()});assert.equal(await page.evaluate(()=>window.routeClicks),2,'original listeners retained');
  // Permission updates, including hidden inline styles, remain authoritative.
  await page.evaluate(()=>{document.querySelector('.nav [data-view=finance]').hidden=true;document.querySelector('.nav [data-view=deals]').style.display='none'});
  assert.equal(await nav.locator('[data-view=finance]').isVisible(),false);assert.equal(await nav.locator('[data-view=deals]').isVisible(),false);
  // Last document element can be scrolled entirely above the menu, even when it grows.
  await page.evaluate(()=>{const main=document.querySelector('main');main.innerHTML='<div style="height:1600px"></div><button id="lastAction" style="height:50px">Last action</button>';document.querySelector('.nav [data-view=tasks] .app-nav-label').textContent='Очень длинное название раздела';});
  await page.waitForTimeout(80);await page.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight));await page.waitForTimeout(80);
  const end=await page.locator('#lastAction').boundingBox();box=await sidebar.boundingBox();assert(end.y+end.height<=box.y,'last document content above nav');
  // Dialog overlays get the whole viewport; menu is absent, restored on dismissal.
  await page.evaluate(()=>{const d=document.createElement('div');d.id='testModal';d.className='modal show';d.setAttribute('role','dialog');d.innerHTML='<div class="modal-card"><input type="date" id="date"><button id="cancel">Close</button></div>';document.body.append(d)});
  await page.waitForTimeout(50);assert.equal(await sidebar.isVisible(),false);await page.locator('#date').focus();assert.equal(await sidebar.isVisible(),false);
  await page.evaluate(()=>document.getElementById('testModal').remove());await page.waitForTimeout(50);assert.equal(await sidebar.isVisible(),true);
  // Date/select/keyboard editor outside modal also do not sit under menu.
  await page.evaluate(()=>{document.querySelector('main').innerHTML='<textarea id="keyboard"></textarea>';window.scrollTo(0,0)});await page.locator('#keyboard').focus();await page.waitForTimeout(50);assert.equal(await sidebar.isVisible(),false);
  await page.setViewportSize({width,height:450});await page.locator('#keyboard').fill('draft');await page.locator('#keyboard').blur();await page.waitForTimeout(50);assert.equal(await sidebar.isVisible(),true);await page.setViewportSize({width,height:900});
  // Viewport-sized dialog chat must reserve measured bottom menu, not hard-coded pixels.
  await page.evaluate(()=>{document.querySelector('main').innerHTML='<section id="view-dialogs" class="view active" style="display:flex;flex-direction:column"><div style="overflow:auto;flex:1;min-height:0"><div style="height:2000px">history</div></div><button id="composer" style="height:44px;flex:none">Send</button></section>';window.scrollTo(0,0)});await page.waitForTimeout(50);
  const composer=await page.locator('#composer').boundingBox();box=await sidebar.boundingBox();assert(composer.y+composer.height<=box.y,'composer clear of bottom bar');
  await page.setViewportSize({width:1440,height:900});await page.waitForTimeout(50);assert.equal(await sidebar.evaluate(el=>getComputedStyle(el).position),'sticky');assert.equal(await sidebar.evaluate(el=>el.inert),false);
  await page.setViewportSize({width,height:900});await page.waitForTimeout(50);assert((await sidebar.boundingBox()).y>790);
 }else{assert.equal(await sidebar.evaluate(el=>getComputedStyle(el).position),'sticky');assert((await sidebar.boundingBox()).width>=200);assert.equal(await nav.evaluate(el=>getComputedStyle(el).flexDirection),'column');}
 assert.deepEqual(errors,[]);fs.mkdirSync(path.join(root,'artifacts/mobile-bottom-nav'),{recursive:true});await page.screenshot({path:path.join(root,`artifacts/mobile-bottom-nav/${width}-${theme}.png`)});await page.close();console.log('PASS',width,theme);
}
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
