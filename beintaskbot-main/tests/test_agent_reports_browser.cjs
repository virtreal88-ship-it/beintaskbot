const fs=require('fs'),assert=require('assert/strict');const {chromium}=require(process.env.PLAYWRIGHT_PATH||'../.support-test-deps/node_modules/playwright');
const read=p=>fs.readFileSync(__dirname+'/../'+p,'utf8');
const html=read('docs/tenant-app.html').replace(/<script\b[\s\S]*?<\/script>/gi,'');
const shell=read('ui/src/app-shell.js').replace("import themeCss from './app-theme.css?inline'",'const themeCss='+JSON.stringify(read('ui/src/app-theme.css'))).replace(/import .*mobile-page-header[^\n]*\n/g,'');
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});try{
 for(const width of [320,390,1440]){
  const page=await browser.newPage({viewport:{width,height:860}}),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.setContent(html);
  await page.evaluate(()=>{window.tenantI18n={language:'ru',t:k=>k};for(const id of ['nightReportNav','aiAnalyticsNav','aiDashboardNav']){const e=document.getElementById(id);e.hidden=false;e.style.display='';}});
  await page.addScriptTag({content:read('ui/src/mobile-page-header.js').replace('export function','function')+'\n'+shell});
  assert.equal(await page.locator('.app-nav-group').last().getAttribute('data-group'),'reports');
  if(width<1024){assert.equal(await page.locator('#nightReportNav').isVisible(),false);await page.locator('[aria-controls="appReportsMenu"]').click();assert.equal(await page.locator('#nightReportNav').isVisible(),true);const b=await page.locator('#appReportsMenu').boundingBox();assert(b.x>=0&&b.x+b.width<=width&&b.y+b.height<860);await page.keyboard.press('Escape');assert.equal(await page.locator('#nightReportNav').isVisible(),false);}
  else assert.equal(await page.locator('#nightReportNav').isVisible(),true);
  assert.deepEqual(errors,[]);await page.close();
 }
}finally{await browser.close()}console.log('PASS native report final group/mobile popover/desktop');})().catch(e=>{console.error(e);process.exit(1)});
