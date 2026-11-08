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
  return route.fulfill({contentType:'text/html',body:'<!doctype html><html lang="ru"><head><style>*,::before,::after{box-sizing:border-box}body{margin:0;font-family:system-ui,sans-serif;background:#eef2f1}h1,h2,h3,p{font-size:inherit;font-weight:inherit;margin:0}a{color:inherit;text-decoration:inherit}button{font:inherit}</style></head><body><button id="outside">Outside</button></body></html>'});
 });
 await page.goto('https://risk.test/app?risk_alert_id=01a1b1af-4d2c-71f2-aaeb-f01eccac0031');
 await page.evaluate(()=>{window.tenantI18n={language:'ru'};window.tenantProfileSnapshot={member:{tenant_id:'tenant-a',telegram_id:101,role:'owner',active:true}};});
 await page.addStyleTag({content:fs.readFileSync(path.join(__dirname,'../docs/tenant-risk-alert-detail.css'),'utf8')});
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
 const checkLayout=async()=>{
  const result=await dialog.evaluate(el=>{
   const body=el.querySelector('.risk-alert-body'),head=el.querySelector('.risk-alert-head'),footer=el.querySelector('.risk-alert-footer');
   const h=el.querySelector('h2'),section=el.querySelector('h3'),text=el.querySelector('.risk-alert-quote p:last-child'),note=el.querySelector('.risk-alert-note');
   const rect=node=>node.getBoundingClientRect(),size=node=>parseFloat(getComputedStyle(node).fontSize);
   return {overflow:el.scrollWidth>el.clientWidth||body.scrollWidth>body.clientWidth,bodyHeight:body.clientHeight,
    bounds:{x:rect(el).x,right:rect(el).right,y:rect(el).y,bottom:rect(el).bottom},
    separated:rect(head).bottom<=rect(body).top+1&&rect(body).bottom<=rect(footer).top+1,
    titleSize:size(h),bodySize:size(text),noteSize:size(note),headingWeight:parseInt(getComputedStyle(section).fontWeight),
    linkHeight:rect(el.querySelector('a')).height,closeHeight:rect(el.querySelector('button')).height};
  });
  assert(!result.overflow,'risk detail must not overflow horizontally');
  assert(result.bodyHeight>80,'scrollable body retains usable height');
  assert(result.separated,'header/footer must not cover source text or verification');
  assert(result.titleSize>result.bodySize&&result.noteSize<result.bodySize,'title/body/tertiary hierarchy survives global CSS reset');
  assert(result.headingWeight>=700,'section headings stay prominent');
  assert(result.linkHeight>=44&&result.closeHeight>=44,'touch targets are large enough');
  const vp=page.viewportSize();assert(result.bounds.x>=0&&result.bounds.right<=vp.width&&result.bounds.y>=0&&result.bounds.bottom<=vp.height);
 };
 const checkContrast=async()=>{
  const pairs=await dialog.evaluate(el=>{
   const color=node=>getComputedStyle(node).color,bg=node=>getComputedStyle(node).backgroundColor;
   return [[color(el),bg(el)],[color(el.querySelector('.risk-alert-note')),bg(el)],
    [color(el.querySelector('a')),bg(el.querySelector('a'))],
    [color(el.querySelector('.risk-alert-assignment')),bg(el)]];
  });
  const luminance=rgb=>rgb.match(/[\d.]+/g).slice(0,3).map(Number).map(x=>x/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4).reduce((sum,x,i)=>sum+x*[.2126,.7152,.0722][i],0);
  pairs.forEach(([a,b])=>{const x=luminance(a),y=luminance(b);assert((Math.max(x,y)+.05)/(Math.min(x,y)+.05)>=4.5,'text contrast must meet WCAG AA');});
 };
 await checkLayout();await checkContrast();
 if(process.env.RISK_SCREENSHOT)await page.screenshot({path:process.env.RISK_SCREENSHOT});
 await page.locator('.risk-alert-body').focus();
 await page.keyboard.press('End');
 await page.waitForFunction(()=>document.querySelector('.risk-alert-body').scrollTop>0);
 await page.locator('.risk-alert-body').evaluate(el=>el.scrollTop=el.scrollHeight);
 assert(await page.getByText('Задача повторно прочитана',{exact:false}).isVisible());
 if(process.env.RISK_SCREENSHOT)await page.screenshot({path:process.env.RISK_SCREENSHOT.replace('.png','-bottom.png')});
 await page.locator('.risk-alert-body').evaluate(el=>el.scrollTop=0);
 await page.evaluate(()=>document.documentElement.dataset.appTheme='dark');
 await checkContrast();await checkLayout();
 if(process.env.RISK_SCREENSHOT)await page.screenshot({path:process.env.RISK_SCREENSHOT.replace('.png','-dark.png')});
 await page.setViewportSize({width:1280,height:900});
 await checkLayout();await checkContrast();
 if(process.env.RISK_SCREENSHOT)await page.screenshot({path:process.env.RISK_SCREENSHOT.replace('.png','-desktop.png')});
 await page.evaluate(()=>document.documentElement.dataset.appTheme='light');
 await checkContrast();
 await page.setViewportSize({width:320,height:568});await checkLayout();
 await page.setViewportSize({width:844,height:390});await checkLayout();
 await page.setViewportSize({width:390,height:844});

 for(const lang of ['az','en']){
  await page.evaluate(lang=>{window.tenantI18n.language=lang;document.dispatchEvent(new CustomEvent('tenant-language'));},lang);
  assert((await dialog.innerText()).includes(lang==='az'?'Nə baş verib':'What happened'));await checkLayout();await checkContrast();
 }
 const fullTitle='Очень длинный заголовок '.repeat(10);
 await page.evaluate(fullTitle=>{document.querySelector('#riskAlertTitle').textContent=fullTitle;document.querySelector('.risk-alert-source-title').textContent=fullTitle;},fullTitle);
 await page.setViewportSize({width:320,height:568});await checkLayout();
 assert.equal(await page.locator('.risk-alert-source-title').textContent(),fullTitle,'full title remains available in scrollable source');
 await page.setViewportSize({width:844,height:390});await checkLayout();
 await page.keyboard.press('Escape');assert.equal(await dialog.count(),0);
 assert(!page.url().includes('risk_alert_id'));
 await browser.close();console.log('PASS: responsive light/dark hierarchy, WCAG text contrast, unobscured scrolling, focus trap, three locales, Escape.');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{await browser?.close();});
