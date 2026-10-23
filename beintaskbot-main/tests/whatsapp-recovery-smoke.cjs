const {chromium}=require('../.support-test-deps/node_modules/playwright');
const {spawn}=require('node:child_process');
const path=require('node:path'),fs=require('node:fs'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),out=path.join(root,'.support-test-deps','whatsapp-recovery-smoke');
fs.mkdirSync(out,{recursive:true});
const server=spawn('python',['tests/tenant_dialog_integrity_browser.py'],{cwd:root,stdio:'ignore'});
(async()=>{
 let browser;
 try{
  for(let n=0;n<30;n++){try{await fetch('http://127.0.0.1:8782/app');break;}catch{await new Promise(r=>setTimeout(r,200));}}
  browser=await chromium.launch({headless:true});let cases=0;
  for(const lang of ['ru','az','en'])for(const theme of ['light','dark'])for(const width of [1440,390]){
   const page=await browser.newPage({viewport:{width,height:width===390?844:900}}),errors=[];
   page.on('pageerror',error=>errors.push(error.message));
   await page.goto(`http://127.0.0.1:8782/app?site_lang=${lang}&theme=${theme}`);
   await page.locator('#operatorRows [data-open-operator]').first().waitFor();
   assert.equal(await page.locator('#operatorEnableNotifications').count(),0,'Push enrollment is only in profile');
   assert.equal(await page.locator('#operatorRows .operator-unread-count').innerText(),'2');
   await page.locator('#operatorRows [data-open-operator]').first().click();
   await page.locator('.operator-header-avatar img').waitFor({state:'visible'});
   await page.waitForFunction(()=>document.querySelector('.operator-header-avatar img')?.complete);
   const geometry=await page.locator('.operator-header-avatar').evaluate(e=>{const box=e.getBoundingClientRect(),image=e.querySelector('img').getBoundingClientRect();return {width:box.width,height:box.height,imageWidth:image.width,imageHeight:image.height,left:box.left,right:box.right};});
   assert(geometry.width>=32&&geometry.height>=32&&geometry.imageWidth===geometry.width&&geometry.imageHeight===geometry.height);
   assert(geometry.left>=0&&geometry.right<=width);
   assert.equal(await page.locator('#chatRows .operator-message').count(),2);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'No horizontal overflow');
   assert.deepEqual(errors,[]);
   await page.screenshot({path:path.join(out,`${lang}-${theme}-${width}.png`)});
   await page.close();cases++;
  }
  console.log(`PASS: ${cases} Chromium cases: RU/AZ/EN, both themes, desktop/mobile, complete avatar, history/unread and no enrollment button.`);
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
