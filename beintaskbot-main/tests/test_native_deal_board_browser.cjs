const {chromium}=require(process.env.PLAYWRIGHT_PATH||'../.support-test-deps/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});try{for(const width of [390,1440]){
 const page=await browser.newPage({viewport:{width,height:900}});await page.setContent('<html lang="en"><body><div id="nativeDealsList"></div></body></html>');await page.addScriptTag({content:fs.readFileSync('docs/tenant-chakra-ui.js','utf8')});
 await page.evaluate(()=>{window.calls=[];window.settings={tenantId:'a',userId:7,canUpdate:true,workflows:[{pipeline_id:'p',stage_id:'a',stage_name:'New'},{pipeline_id:'p',stage_id:'b',stage_name:'Done'},{pipeline_id:'q',stage_id:'c',stage_name:'Other'}],onMove:async(row,stage)=>{calls.push({id:row.id,stage});if(window.fail)throw Error('Rejected');return {...row,stage_id:stage,updated_at:'2026-11-15T00:00:00Z'};}};window.row={id:'1',title:'Client deal',pipeline_id:'p',stage_id:'a',updated_at:'2026-11-14T00:00:00Z',actions:{update:true}};tenantChakraCRM.renderNativeDeals([row],settings);});
 const select=page.getByRole('combobox');await select.waitFor();assert.equal(await select.locator('option').count(),2);
 await select.selectOption('b');await page.waitForFunction(()=>calls.length===1);await page.locator('[data-native-stage="b"] [data-native-deal-card]').waitFor();
 await page.evaluate(()=>window.fail=true);await select.selectOption('a');await page.getByRole('alert').waitFor();assert.equal(await select.inputValue(),'b');
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-profile',{detail:{}})));assert.equal(await page.locator('[data-native-deal-card]').count(),0);await page.close();
 } console.log('PASS native stage board scoped targets, keyboard, rollback and identity clear');}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
