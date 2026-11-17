const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.CHROMIUM_EXECUTABLE_PATH}:{}),args:['--no-sandbox']});try{
 for(const width of [320,390,768,1440])for(const theme of ['light','dark']){
 const page=await browser.newPage({viewport:{width,height:900}});await page.setContent(`<html data-app-theme="${theme}"><body data-app-ui="kwid"><div id="integrationSettingsHost" style="max-width:850px;padding:12px"></div></body></html>`);
 await page.addStyleTag({content:fs.readFileSync(path.join(__dirname,'../ui/src/app-theme.css'),'utf8')});
 await page.evaluate(()=>{window.watchers=[];window.calls=[];window.tenantI18n={language:'en'};window.tenantViewDemand={watch:x=>watchers.push(x)};window.fetch=async(url,options)=>{calls.push({url,...options});if(url.startsWith('/api/platform/browser-calls?'))return{ok:true,json:async()=>({result:{sources:[{channel_id:'one',number:'15550000001',online:false,permanent:true},{channel_id:'two',number:'15550000002',online:true,permanent:true}]}})};return{ok:true,json:async()=>({success:true,channels:[{id:'one',name:'Example Contact One',phone:'15550000001',status:'connected',active:true},{id:'two',name:'Example Contact Two',phone:'15550000002',status:'disconnected',active:true}]})}};});
 await page.addScriptTag({content:fs.readFileSync(path.join(__dirname,'../docs/tenant-call-sources.js'),'utf8')});
 await page.addScriptTag({content:fs.readFileSync(path.join(__dirname,'../docs/tenant-shared-integration-settings.js'),'utf8')});
 await page.evaluate(()=>watchers.find(x=>x.provider==='connect').activate({member:{tenant_id:'synthetic',telegram_id:7},integrations:[{provider:'connect',status:'connected',management_actions:{view:true,update:true,delete:true}}]}));
 await page.locator('tbody tr').first().waitFor();assert.equal(await page.locator('tbody tr').count(),2);assert.equal(await page.locator('[data-key]').isVisible(),false);
 assert.equal(await page.evaluate(()=>calls.filter(c=>c.body).length),1);assert.equal(await page.evaluate(()=>JSON.parse(calls.find(c=>c.body).body).api_key),'');
 await page.waitForFunction(()=>document.querySelector('[data-call-status-channel=one]')?.dataset.state==='offline');assert.equal(await page.locator('[data-call-status-channel=two]').getAttribute('data-state'),'online');assert.equal(await page.locator('[data-call-status-channel=one]').getAttribute('aria-label'),'Calls: source offline');assert.equal(await page.locator('.connect-call-status svg').count(),2);
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'no page overflow');assert(await page.locator('.connect-numbers-wrap').evaluate(n=>n.scrollWidth>=n.clientWidth),'table owns overflow');
 await page.locator('.connect-number-add').click();assert.equal(await page.locator('[data-key]').isVisible(),true);assert.equal(await page.evaluate(()=>calls.filter(c=>c.body).length),1);
 await page.close();
 }
 console.log('PASS Connect number table browser: 320–1440, light/dark, auto load, disclosure and contained horizontal overflow');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
