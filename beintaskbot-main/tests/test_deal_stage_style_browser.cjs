const {chromium}=require('../.support-test-deps/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});try{
for(const width of [320,390,1440])for(const theme of ['light','dark']){
const page=await browser.newPage({viewport:{width,height:900},colorScheme:theme});
await page.setContent('<html lang="en"><body><div id="dealsList"></div><div id="nativeDealsList"></div></body></html>');
await page.evaluate(()=>{window.tenantViewDemand={watch(){}};window.fetch=async()=>({ok:true,json:async()=>({success:true,catalog:[{pipeline_id:1,name:'Sales',stages:[{stage_id:1,name:'New',color:'#22c55e'},{stage_id:2,name:'Working'},{stage_id:3,name:'Very long stage name '.repeat(12)}]}],hidden_stage_keys:[]})});});
await page.addScriptTag({content:fs.readFileSync('docs/tenant-chakra-ui.js','utf8')});
await page.evaluate(()=>tenantChakraCRM.renderDeals('dealsList',[{kommo_lead_id:1,pipeline_id:1,status_id:1,name:'One'},{kommo_lead_id:2,pipeline_id:1,status_id:2,name:'Two'},{kommo_lead_id:3,pipeline_id:1,status_id:3,name:'Three'}],false,()=>{}));
await page.locator('[data-kommo-stage-key="1:3"] [data-deal-stage-badge]').waitFor();
const colors=await page.locator('[data-deal-stage-header]').evaluateAll(nodes=>nodes.map(n=>getComputedStyle(n).borderBottomColor));
assert.equal(colors[0],'rgb(34, 197, 94)');assert.equal(new Set(colors).size,3);
for(const column of await page.locator('[data-kommo-stage-key]').all()){
const badge=column.locator('[data-deal-stage-badge]'),header=column.locator('[data-deal-stage-header]');
assert.equal(await badge.textContent(),await header.locator('h2').count()?await header.locator('h2').textContent():await header.locator('h3').textContent());
assert.equal(await badge.evaluate(n=>getComputedStyle(n).borderRadius),'9999px');
assert(await badge.evaluate(n=>n.scrollWidth<=n.clientWidth+1));
}
assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
await page.evaluate(()=>{
  const host=document.createElement('div');host.id='editableDeal';document.body.append(host);
  window.fetch=async()=>({ok:true,json:async()=>({success:true,catalog:[{pipeline_id:1,name:'Sales',stages:[{stage_id:1,name:'New',color:'#22c55e'},{stage_id:2,name:'Working'}]}],hidden_stage_keys:[],move_context:{tenant_id:'a',user_id:1},move_stage_sources:[{pipeline_id:1,stage_id:1}],move_stage_targets:[{pipeline_id:1,stage_id:1},{pipeline_id:1,stage_id:2}]})});
  tenantChakraCRM.renderDeals('editableDeal',[{kommo_lead_id:4,pipeline_id:1,status_id:1,name:'Editable client',source_revision:'a'.repeat(64),raw:{updated_at:1000}}],false,()=>{});
});
const editable=page.locator('#editableDeal [data-deal-stage-control]');await editable.waitFor();
assert.equal(await page.locator('#editableDeal [data-deal-stage-badge]').count(),0);
assert.equal(await editable.evaluate(n=>getComputedStyle(n).borderRadius),'9999px');
assert.equal(await editable.evaluate(n=>getComputedStyle(n).backgroundColor),'rgb(224, 247, 232)');
assert((await editable.boundingBox()).height>=44);
assert(await editable.evaluate(n=>n.getBoundingClientRect().right<=innerWidth));
assert((await editable.boundingBox()).y>(await page.locator('#editableDeal').getByText('Editable client',{exact:true}).first().boundingBox()).y);
await page.evaluate(()=>tenantChakraCRM.renderNativeDeals([{id:'n',title:'Native',pipeline_id:'p',stage_id:'a'}],{tenantId:'a',userId:1,workflows:[{pipeline_id:'p',stage_id:'a',stage_name:'Open'},{pipeline_id:'p',stage_id:'b',stage_name:'Closed'}]}));
await page.locator('[data-native-deal-card] [data-deal-stage-badge]').waitFor();
assert.equal(await page.locator('[data-native-deal-card] [data-deal-stage-badge]').textContent(),'Open');
assert.equal(new Set(await page.locator('#nativeDealsList [data-deal-stage-header]').evaluateAll(nodes=>nodes.map(n=>getComputedStyle(n).borderBottomColor))).size,2);
await page.evaluate(()=>tenantChakraCRM.renderNativeDeals([{id:'n',title:'Native',pipeline_id:'p',stage_id:'a',updated_at:'2026-11-14',actions:{update:true}}],{tenantId:'a',userId:1,canUpdate:true,workflows:[{pipeline_id:'p',stage_id:'a',stage_name:'Open'},{pipeline_id:'p',stage_id:'b',stage_name:'Closed'}]}));
await page.locator('[data-native-deal-card] [data-deal-stage-control]').waitFor();
assert.equal(await page.locator('[data-native-deal-card] [data-deal-stage-badge]').count(),0);
const control=page.locator('[data-native-deal-card] [data-deal-stage-control]');
assert.equal(await control.evaluate(n=>getComputedStyle(n).borderRadius),'9999px');
assert((await control.boundingBox()).height>=44);
assert(await control.evaluate(n=>n.getBoundingClientRect().right<=innerWidth));
fs.mkdirSync('tests/artifacts/deal-stage-style',{recursive:true});await page.screenshot({path:`tests/artifacts/deal-stage-style/${width}-${theme}.png`,fullPage:true});await page.close();
}console.log('PASS stage underline, matching rounded badge, long labels, native/Kommo, 320/390/1440');}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
