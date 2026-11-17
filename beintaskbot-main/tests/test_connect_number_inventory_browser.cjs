const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.CHROMIUM_EXECUTABLE_PATH}:{}),args:['--no-sandbox']});try{
 for(const width of [320,390,768,1440])for(const theme of ['light','dark']){
 const page=await browser.newPage({viewport:{width,height:900}});await page.setContent(`<html data-app-theme="${theme}"><body data-app-ui="kwid"><div id="integrationSettingsHost" style="max-width:850px;padding:12px"></div></body></html>`);
 await page.addStyleTag({content:fs.readFileSync(path.join(__dirname,'../ui/src/app-theme.css'),'utf8')});
 await page.evaluate(()=>{window.watchers=[];window.calls=[];window.tenantI18n={language:'en'};window.tenantViewDemand={watch:x=>watchers.push(x)};window.fetch=async(url,options)=>{calls.push(JSON.parse(options.body));return{ok:true,json:async()=>({success:true,channels:[{id:'one',name:'Rüfət Həsənzadə',phone:'994102135105',status:'connected',active:true},{id:'two',name:'Nizami Qasımov',phone:'994502072240',status:'disconnected',active:true}]})}};});
 await page.addScriptTag({content:fs.readFileSync(path.join(__dirname,'../docs/tenant-shared-integration-settings.js'),'utf8')});
 await page.evaluate(()=>watchers.find(x=>x.provider==='connect').activate({member:{tenant_id:'synthetic',telegram_id:7},integrations:[{provider:'connect',status:'connected',management_actions:{view:true,update:true,delete:true}}]}));
 await page.locator('tbody tr').first().waitFor();assert.equal(await page.locator('tbody tr').count(),2);assert.equal(await page.locator('[data-key]').isVisible(),false);
 assert.equal(await page.evaluate(()=>calls.length),1);assert.equal(await page.evaluate(()=>calls[0].api_key),'');
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'no page overflow');assert(await page.locator('.connect-numbers-wrap').evaluate(n=>n.scrollWidth>=n.clientWidth),'table owns overflow');
 await page.locator('.connect-number-add').click();assert.equal(await page.locator('[data-key]').isVisible(),true);assert.equal(await page.evaluate(()=>calls.length),1);
 await page.close();
 }
 console.log('PASS Connect number table browser: 320–1440, light/dark, auto load, disclosure and contained horizontal overflow');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
