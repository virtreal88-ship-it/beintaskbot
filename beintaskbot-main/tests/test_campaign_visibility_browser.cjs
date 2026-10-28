// Real Chromium + shipped CSS; network and campaign API are synthetic.
const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require(process.env.SUPPORT_CASES_PLAYWRIGHT||'../.support-test-deps/node_modules/playwright');
const html=fs.readFileSync('docs/tenant-app.html','utf8'),css=html.match(/<style[^>]*>([\s\S]*?)<\/style>/)[1],script=fs.readFileSync('docs/tenant-knowledge-campaigns.js','utf8');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{}),args:['--no-sandbox']});
 try{for(const mode of ['pending','error','empty','existing','stopped']){
  const page=await browser.newPage({viewport:{width:390,height:844}});await page.route('https://fixture.test/**',route=>route.fulfill({status:200,contentType:'text/html',body:`<style>${css}</style><div id="supportCases"></div>`}));await page.goto('https://fixture.test/app');await page.addScriptTag({content:script});
  await page.evaluate(mode=>{window.syntheticCalls=[];window.resolveRead=null;const get=new Promise(resolve=>{window.resolveRead=resolve});window.TenantKnowledgeCampaigns.mount(document.getElementById('supportCases'),{lang:()=> 'en',expected:()=>({expected_tenant_id:'fixture',expected_user_id:1}),channels:[],agents:[],api:async(url,opt)=>{window.syntheticCalls.push({url,write:!!opt});if(opt)throw Error('Unexpected write');if(url.includes('/chat/ai/settings'))return {};if(mode==='pending')return get;if(mode==='error')throw Error('synthetic read error');if(mode==='empty')return {campaign:null};return {campaign:{id:'fixture',version:1,status:mode==='stopped'?'stopped':'awaiting_budget',channel_ids:[],max_calls:2,max_tokens:48000,media_policy:{max_main_calls:1,max_asr_calls:0,max_vision_calls:0},cards:0}};}});},mode);
  if(mode!=='pending')await page.waitForFunction(()=>document.querySelector('[data-campaign-status]')?.textContent&&!document.querySelector('[data-campaign-status]').textContent.includes('Checking'));
  await page.locator('[data-campaign-panel]').evaluate(el=>{el.open=true});
  const plan=page.locator('[data-campaign-plan]'),prepare=page.locator('[data-prepare]');
  if(['pending','error','existing'].includes(mode)){assert.equal(await plan.isVisible(),false,`${mode}: hidden form must not appear`);assert.equal(await prepare.isVisible(),false,`${mode}: no accidental preparation`);}else{assert.equal(await plan.isVisible(),false,`${mode}: advanced form is collapsed`);assert.equal(await prepare.isVisible(),false);await page.locator('[data-analysis-settings] > summary').click();assert.equal(await plan.isVisible(),true,`${mode}: advanced form opens on request`);}
  if(mode==='pending'){assert.equal(await page.locator('[data-campaign-restore]').isVisible(),false);assert.equal(await page.locator('[data-campaign-status]').isVisible(),true);assert.match(await page.locator('[data-campaign-status]').textContent(),/Checking processing status/);}
  if(mode==='error'){assert.equal(await page.locator('[data-error]').isVisible(),true);assert.equal(await page.locator('[data-campaign-status]').isVisible(),true);assert.match(await page.locator('[data-error]').textContent(),/synthetic read error/);assert.equal(await page.locator('[data-campaign-restore]').isVisible(),true);}
  if(mode==='existing'){assert.equal(await page.locator('[data-campaign-restore]').isVisible(),false);assert.match(await page.locator('[data-campaign-status]').textContent(),/Ready for confirmation/);assert.equal(await page.locator('[data-consent]').isVisible(),true);}
  if(mode==='empty')assert.equal(await page.locator('[data-campaign-restore]').isVisible(),true);
  if(mode==='empty'||mode==='stopped'){
   for(const selector of ['[data-retry-wrap]','[data-retry-warning]','[data-batch-grant]','[data-campaign-case]'])assert.equal(await page.locator(selector).isVisible(),false,`${mode}: ${selector} must respect hidden`);
   if(mode==='empty'){await page.locator('[data-all-existing]').check();assert.equal(await page.locator('[data-retry-wrap]').isVisible(),true);assert.equal(await page.locator('[data-retry-warning]').isVisible(),true);assert.equal(await page.locator('[data-batch-grant]').isVisible(),true);await page.locator('[data-all-existing]').uncheck();assert.equal(await page.locator('[data-retry-wrap]').isVisible(),false);assert.equal(await page.locator('[data-batch-grant]').isVisible(),false);}
  }
  const calls=await page.evaluate(()=>window.syntheticCalls);assert(calls.every(x=>!x.write),'no campaign write');await page.close();
 }console.log('PASS Chromium: shipped CSS hides pending/error/existing forms and all hidden campaign controls; empty/stopped forms remain visible');}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
