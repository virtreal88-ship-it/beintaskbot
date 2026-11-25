const {chromium}=require(process.env.PLAYWRIGHT_PATH||'../.support-test-deps/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
const source=fs.readFileSync('docs/tenant-customer-media-ui.js','utf8');
const operator=fs.readFileSync('docs/tenant-operator-dialogs.js','utf8');
const css=[...operator.matchAll(/(?:workspaceStyle|mobileStyle)\.textContent\+?=`([^`]*)`/g)].map(m=>m[1]).join('\n');
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});try{
for(const width of [320,390,430,1280])for(const state of ['empty','draft','unknown']){
 const page=await browser.newPage({viewport:{width,height:800}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.setContent('<html><body style="margin:0"><main style="display:flex;flex-direction:column;height:100dvh"><div id="history" style="overflow:auto;flex:1;min-height:0"><div style="height:1600px">Messages</div><p id="lastMessage">Last real message remains visible</p></div><div class="operator-composer"><textarea id="chatText" rows="1" placeholder="Message"></textarea><div class="operator-composer-actions"><button id="operatorAIReply" aria-label="AI reply">AI reply</button><button id="operatorCopilotToggle" aria-label="AI helper">AI helper</button><button id="operatorCopilotVoiceToggle" aria-label="Voice assistant">Voice assistant</button><button class="operator-voice-mic">mic</button><button id="sendChat">Send</button></div></div></main></body></html>');
 await page.addStyleTag({content:css});
 await page.evaluate(state=>{window.tenantI18n={language:'ru',t:k=>k};window.tenantOperator={member:{tenant_id:'tenant',telegram_id:'1',role:'worker',active:true,permissions:['dialogs']},currentDialog:{provider:'connect',lead_id:1,channel_id:'source',conversation_id:'contact'}};window.calls=[];window.fetch=async(url,opts={})=>{window.calls.push({url,method:opts.method||'GET'});return {ok:true,json:async()=>({enabled:true})}};window.createTenantVoiceStore=()=>({ownerVersion:async()=>0,activate:async()=>true,get:()=>new Promise(resolve=>{window.releaseStore=()=>resolve(state==='draft'?{state:'draft',name:'photo.png',caption:'Draft caption'}:null)}),latestReceipt:async()=>state==='unknown'?{state:'unknown',name:'photo.png'}:null});},state);
 await page.addScriptTag({content:fs.readFileSync('docs/tenant-dialog-composer.js','utf8')});
 await page.locator('#history').evaluate(el=>el.scrollTop=el.scrollHeight);
 const primary=page.locator('#operatorComposerPrimary'),before=await primary.boundingBox();
 await page.addScriptTag({content:source});await page.waitForFunction(()=>typeof window.releaseStore==='function');
 await page.waitForTimeout(150);assert.equal(await page.locator('.operator-customer-media').isVisible(),false,'unrestored attachment controls never paint');assert.equal(await page.locator('#operatorMediaAdd').isDisabled(),true);
 const pending=await primary.boundingBox();assert(Math.abs(pending.y-before.y)<1,'buttons do not jump while storage is pending');
 const msg=await page.locator('#lastMessage').boundingBox();assert(msg.y+msg.height<=pending.y,'last message remains above composer');
 await page.evaluate(()=>releaseStore());await page.waitForTimeout(80);
 if(state==='empty'){assert.equal(await page.locator('.operator-customer-media').isVisible(),false);const end=await primary.boundingBox();assert(Math.abs(end.y-before.y)<1,'empty draft restoration has zero vertical shift');await page.locator('#chatText').fill('Unsent');await page.setViewportSize({width,height:450});await page.locator('#chatText').blur();assert.equal(await page.locator('#chatText').inputValue(),'Unsent');const b=await primary.boundingBox();assert(b.y+b.height<=450);}
 else{assert.equal(await page.locator('.operator-customer-media').isVisible(),true,'real recovered draft or uncertain-send warning must be visible');if(state==='unknown')assert.match(await page.locator('.operator-customer-media [role=status]').innerText(),/Не повторяйте|неизвестен/);else assert.equal(await page.locator('.operator-customer-media textarea').inputValue(),'Draft caption');}
 assert.deepEqual(errors,[]);assert((await page.evaluate(()=>calls)).every(c=>c.method==='GET'),'opening composer never sends');
 await page.close();console.log('PASS stable pre-paint media restoration',width,state);
}
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
