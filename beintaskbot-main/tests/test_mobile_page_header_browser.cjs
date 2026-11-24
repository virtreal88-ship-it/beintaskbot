// Run with real Chromium in CI. No authenticated endpoint or production data.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'../.support-test-deps/node_modules/playwright');
const root=path.resolve(__dirname,'..'),read=p=>fs.readFileSync(path.join(root,p),'utf8');
const html=read('docs/tenant-app.html').replace(/<script\b[\s\S]*?<\/script>/gi,'');
const moduleSource=read('ui/src/mobile-page-header.js').replace('export function','function');
const shell=read('ui/src/app-shell.js').replace("import themeCss from './app-theme.css?inline'",'const themeCss='+JSON.stringify(read('ui/src/app-theme.css'))).replace(/import .*mobile-page-header[^\n]*\n/g,'');
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});try{
 for(const width of [320,390,430,1023,1440])for(const theme of ['light','dark']){
  const page=await browser.newPage({viewport:{width,height:860}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('https://layout.test/**',route=>route.request().isNavigationRequest()?route.fulfill({contentType:'text/html',body:html}):route.abort());
  await page.goto('https://layout.test/app');await page.evaluate(()=>{window.tenantI18n={language:'ru',t:k=>({dialogs:'Диалоги',tasks:'Задачи',deals:'Сделки'}[k]||k)};window.tenantOpenSettingsHub=()=>window.settingsOpened=true;});
  await page.addScriptTag({content:moduleSource+'\n'+shell});await page.addScriptTag({content:read('docs/tenant-mobile-crm-toolbar.js')});await page.evaluate(theme=>document.documentElement.dataset.appTheme=theme,theme);
  for(const route of ['tasks','deals','dialogs','profile']){
   await page.evaluate(route=>{document.querySelectorAll('main>.view').forEach(v=>v.classList.toggle('active',v.id==='view-'+route));},route);await page.waitForTimeout(80);
   if(width<=1023){
    assert.equal(await page.locator('.app-mobile-title-row').count(),1);
    if(['tasks','deals'].includes(route)){assert.equal(await page.locator('#view-'+route+' h1:visible').count(),1,'one visible title');if(width<=700)assert.equal(await page.locator('#view-'+route+' .crm-mobile-toolbar>button:visible').count(),3,'create/filter/more only');}
    const h=await page.locator('.app-mobile-title-row h1').boundingBox(),a=await page.locator('.app-avatar-button').boundingBox();assert(h.x<a.x);assert(Math.abs(h.y+h.height/2-a.y-a.height/2)<2,'title and avatar align');assert(a.x+a.width<=width,'avatar fits');
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no horizontal overflow');
    await page.locator('.app-avatar-button').click();assert(await page.locator('.app-user-menu').isVisible());
    assert(await page.locator('.app-user-menu [data-app-language]').isVisible());assert(await page.locator('.app-user-menu [data-app-instructions]').isVisible());const link=await page.locator('.app-user-menu [data-app-instructions]').boundingBox(),label=await page.locator('.app-user-menu [data-app-instructions] span').boundingBox();assert(label.x>=link.x+8&&label.x+label.width<=link.x+link.width,'instruction text stays within left padding');
    await page.keyboard.press('Escape');assert.equal(await page.locator('.app-user-menu').isVisible(),false);assert(await page.locator('.app-avatar-button').evaluate(n=>n===document.activeElement));
   }else{assert.equal(await page.locator('.app-mobile-title-row').count(),0);assert.equal(await page.locator('.app-topbar').evaluate(n=>getComputedStyle(n).position),'fixed');assert(await page.locator('.app-topbar-actions [data-app-language]').isVisible());}
  }
  const brandHeader=page.locator('.app-sidebar-header'),topbar=page.locator('.app-topbar');
  const assertDesktopHeaderAlignment=async()=>{
   const [left,main]=await Promise.all([brandHeader.boundingBox(),topbar.boundingBox()]);
   assert.equal(left.y,main.y,'desktop headers start on the same row');
   assert.equal(left.height,main.height,'desktop brand and main header heights match');
   assert.equal(left.y+left.height,main.y+main.height,'desktop horizontal dividers meet exactly');
  };
  if(width>1023){
   assert.equal((await topbar.boundingBox()).height,64,'main top bar height is unchanged');
   assert.match(await brandHeader.locator('.brand').textContent(),/CRM Smart Assistant/);
   await assertDesktopHeaderAlignment();
   const chevron=brandHeader.getByRole('button');
   await chevron.click();await page.waitForFunction(()=>document.body.dataset.sidebar==='collapsed');
   await assertDesktopHeaderAlignment();
   await chevron.click();await page.waitForFunction(()=>document.body.dataset.sidebar==='expanded');
   await assertDesktopHeaderAlignment();
  }else assert.equal(await brandHeader.isVisible(),false,'mobile bottom navigation does not show the desktop branding header');
  assert.deepEqual(await page.locator('.app-nav-group[data-group=crm] [data-view]').evaluateAll(nodes=>nodes.map(n=>n.dataset.view).filter(v=>['tasks','deals','customers','companies'].includes(v))),['tasks','deals','customers','companies']);
  if(width===320){await page.evaluate(()=>document.querySelectorAll('.nav [data-view]').forEach(n=>{n.hidden=false;n.style.display=''}));await page.waitForTimeout(80);const tiles=await page.locator('.nav [data-view]:visible').evaluateAll(ns=>ns.map(n=>n.getBoundingClientRect()).filter(r=>r.width>0));assert(tiles.every(r=>r.width===64),'compact 64px navigation tiles');assert(tiles.filter(r=>r.x>=0&&r.right<=320).length>=5,'five items fit at320');}
  if(width<=1023){
   await page.evaluate(()=>{const b=document.createElement('button');b.id='dialogConsultationBell';b.innerHTML='bell<span class="notification-unread-badge">3</span>';document.querySelector('[data-app-language]').before(b)});await page.waitForTimeout(80);assert(await page.locator('.app-avatar-unread').isVisible());assert.equal(await page.locator('.app-avatar-unread').innerText(),'3');const badge=await page.locator('.app-avatar-unread').boundingBox(),avatar=await page.locator('.app-avatar-button').boundingBox();assert(badge.y>=avatar.y&&badge.x+badge.width<=avatar.x+avatar.width,'badge is not clipped outside avatar bounds');
   await page.locator('.app-avatar-button').click();const menu=await page.locator('.app-user-menu').boundingBox();assert(menu.x>=0&&menu.x+menu.width<=width&&menu.y+menu.height<=860,'menu inside viewport');
   await page.waitForTimeout(180);fs.mkdirSync(path.join(root,'artifacts/mobile-page-header'),{recursive:true});await page.screenshot({path:path.join(root,`artifacts/mobile-page-header/${width}-${theme}-menu.png`)});await page.setViewportSize({width:1440,height:860});await page.waitForTimeout(80);assert(await page.locator('.app-topbar-actions #dialogConsultationBell').isVisible());assert.equal(await page.locator('.app-mobile-title-row').count(),0);
  }
  await page.waitForTimeout(180);fs.mkdirSync(path.join(root,'artifacts/mobile-page-header'),{recursive:true});await page.screenshot({path:path.join(root,`artifacts/mobile-page-header/${width}-${theme}.png`)});assert.deepEqual(errors,[]);await page.close();
 }
 console.log('PASS browser mobile title/menu/layout and desktop regression');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
