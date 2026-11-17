/* Clean synthetic browser profile only. Never reads the user's password store. */
const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const script=fs.readFileSync('docs/tenant-search-inputs.js','utf8');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
 try{
  for(const viewport of [{width:1440,height:900},{width:390,height:844}]){
   const page=await browser.newPage({viewport});let requests=0;page.on('request',()=>requests++);
   await page.setContent(`<html><head><script>${script}</script></head><body>
    <button id="before">Before</button><label for="query">Search</label><input id="query" type="search" value="saved query" name="original-query"><button id="after">After</button>
    <form id="searchForm"><input type="search" id="owned" name="query"></form>
    <input type="search" id="locked" readonly value="fixed query">
    <form id="login"><input id="username" autocomplete="username" value="synthetic-user"><input id="password" type="password" autocomplete="current-password"><input id="loginWidget" type="search"></form>
    <input id="ordinary" type="text"><input id="mail" type="email"><div id="dynamic"></div></body></html>`);
   await page.waitForFunction(()=>document.getElementById('query').readOnly);
   assert.equal(await page.locator('#query').getAttribute('autocomplete'),'off');
   assert.equal(await page.locator('#query').getAttribute('data-lpignore'),'true');
   assert.equal(await page.locator('#query').getAttribute('data-1p-ignore'),'true');
   assert.equal(await page.locator('#query').getAttribute('data-bwignore'),'true');
   assert.equal(await page.locator('#query').inputValue(),'saved query');
   await page.locator('#before').focus();await page.keyboard.press('Tab');
   assert.equal(await page.locator('#query').evaluate(n=>n.readOnly),false,'Tab focus unlocks');
   await page.keyboard.press('ControlOrMeta+A');await page.keyboard.type('typed query');
   await page.locator('#after').click();assert.equal(await page.locator('#query').evaluate(n=>n.readOnly),true);
   assert.equal(await page.locator('#query').inputValue(),'typed query','blur never clears query');
   await page.locator('label').click();await page.keyboard.type(' + label');assert.match(await page.locator('#query').inputValue(),/label/);
   assert.equal(await page.locator('#query').getAttribute('name'),'original-query');
   assert.equal(await page.locator('#owned').evaluate(n=>n.form.id),'searchForm');
   await page.locator('#locked').focus();assert.equal(await page.locator('#locked').evaluate(n=>n.readOnly),true,'authored readonly stays readonly');
   for(const id of ['username','password','loginWidget','ordinary','mail'])assert.equal(await page.locator('#'+id).getAttribute('data-lpignore'),null,'non-search/credential form untouched');
   assert.equal(await page.locator('#username').getAttribute('autocomplete'),'username');
   assert.equal(await page.locator('#password').getAttribute('autocomplete'),'current-password');
   await page.evaluate(()=>document.querySelector('#dynamic').innerHTML='<input id="added" type="search" value="restored"><input id="optIn" data-search-input type="tel">');
   await page.waitForFunction(()=>document.getElementById('added').readOnly);
   await page.locator('#added').click();await page.locator('#added').fill('dynamic query');await page.locator('#after').click();
   assert.equal(await page.locator('#added').inputValue(),'dynamic query');
   await page.evaluate(()=>{const field=document.createElement('input');field.id='lateType';document.querySelector('#dynamic').append(field);field.type='search';});
   await page.waitForFunction(()=>document.getElementById('lateType').readOnly);
   await page.locator('#optIn').click();await page.keyboard.type('12345');assert.equal(await page.locator('#optIn').inputValue(),'12345');
   assert.equal(requests,0,'guard does not send data');await page.close();
  }
  const html=fs.readFileSync('docs/tenant-app.html','utf8');assert(html.indexOf('tenant-search-inputs.js')<html.indexOf('<input'),'guard loads before static fields');
  for(const [file,pattern] of [['docs/tenant-app.html',/<input type="search" id="companySearch"/],['docs/tenant-linear-board.js',/<input type="search" class="search" id="tlSearch"/],['docs/deal-tags.js',/<input type="search" data-query/],['docs/tenant-beinsystems-operator-settings.js',/<input type="search" data-search/]])assert.match(fs.readFileSync(file,'utf8'),pattern);
  console.log('PASS Chromium desktop/mobile: static/dynamic search hints, mouse/label/Tab editing, readonly preservation, login exclusions, zero requests. Saved-login autofill not exercised.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
