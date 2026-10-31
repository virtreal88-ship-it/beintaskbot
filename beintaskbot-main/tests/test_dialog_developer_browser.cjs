/* Mock-only browser regression for marker lifecycle and assistant modes. */
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':chromium.executablePath()),args:['--no-sandbox']});
 try{
 for(const width of [390,1280]){
  const page=await browser.newPage({viewport:{width,height:844}}),calls=[],markers=new Set();let tenant='a';
  const markup=`<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><select data-app-language></select><div id="operatorDialogTitleSlot"></div><details open><summary>Menu</summary><div id="operatorMenuExtra"></div></details><section id="operatorCopilot"><header class="operator-copilot-header"><button id="operatorCopilotClose">Close</button></header><textarea id="privatePrompt">Private helper prompt</textarea><p id="hiddenHelper" hidden>Hidden result</p></section></body></html>`;
  await page.route('https://crm.test/',route=>route.fulfill({contentType:'text/html',body:markup}));
  await page.route('**/api/**',async route=>{
   const req=route.request(),body=req.postDataJSON(),url=new URL(req.url());calls.push({path:url.pathname,body});let result={};
   if(url.pathname.endsWith('/developer-marker')){const key=body.expected_tenant_id+':'+body.conversation_id;if('enabled' in body){if(body.enabled)markers.add(key);else markers.delete(key);}result={enabled:markers.has(key)};}
   else if(url.pathname.endsWith('/destinations'))result={destinations:tenant==='a'&&markers.has('a:target')?[{channel_id:'channel',conversation_id:'target',customer_name:'Developers',channel_name:'Test'}]:[]};
   else if(url.pathname.endsWith('/preview'))result={messages:[{id:'m1',body:'Customer problem',happened_at:'12:00',message_type:'text'}]};
   else if(url.pathname.endsWith('/replies'))result={replies:[]};
   await route.fulfill({json:{success:true,result}});
  });
  await page.goto('https://crm.test/');
  await page.evaluate(()=>{window.tenantI18n={language:'ru'};window.tenantOperator={member:{tenant_id:'a',telegram_id:33},hasRight:()=>true,currentDialog:{provider:'connect',channel_id:'channel',conversation_id:'source'}};});
  await page.addScriptTag({path:'docs/tenant-dialog-consultations.js'});
  assert.equal(await page.locator('#dialogHelperType').inputValue(),'Helper');assert.equal(await page.locator('#privatePrompt').isVisible(),true);
  await page.locator('#dialogHelperType').selectOption('Developer');await page.locator('#dialogHelperType').dispatchEvent('change');
  assert.equal(await page.locator('#privatePrompt').isVisible(),false);assert.equal(await page.locator('#operatorCopilotClose').isVisible(),true);
  await page.locator('#dialogHelperType').selectOption('Helper');assert.equal(await page.locator('#privatePrompt').inputValue(),'Private helper prompt');assert.equal(await page.locator('#privatePrompt').isVisible(),true);assert.equal(await page.locator('#hiddenHelper').isVisible(),false);
  await page.locator('#dialogHelperType').selectOption('Developer');await page.locator('#dialogConsultationStart').click();await page.locator('[data-developer-empty]').waitFor();
  assert.match(await page.locator('[data-developer-empty]').textContent(),/Отметить как разработчика/);assert.equal(await page.locator('button[type=submit]').isDisabled(),true);
  await page.getByRole('button',{name:'Закрыть',exact:true}).click();assert.equal(calls.some(x=>x.path.endsWith('/dispatch')),false);
  // Route changes reuse DOM: stale captured button must not write the old route.
  await page.evaluate(()=>{window.staleMarker=document.getElementById('dialogDeveloperMarker');window.tenantOperator.currentDialog.conversation_id='target';window.staleMarker.click();document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));});
  await page.waitForFunction(()=>document.getElementById('dialogDeveloperMarker').dataset.scope.endsWith(':target')&&!document.getElementById('dialogDeveloperMarker').disabled);
  assert.equal(calls.filter(x=>x.path.endsWith('/developer-marker')&&x.body.enabled!==undefined).length,0);
  await page.locator('#dialogDeveloperMarker').click();await page.locator('#dialogDeveloperBadge').waitFor();assert.ok(markers.has('a:target'));
  await page.evaluate(()=>{window.tenantI18n.language='en';document.dispatchEvent(new CustomEvent('tenant-language'));});
  assert.equal(await page.locator('#dialogDeveloperBadge').textContent(),'Developer');assert.equal(await page.locator('#dialogHelperType option[value=Helper]').textContent(),'Helper');
  await page.locator('#dialogDeveloperMarker').click();await page.waitForFunction(()=>!document.getElementById('dialogDeveloperBadge'));assert.equal(markers.size,0);
  tenant='b';await page.evaluate(()=>{window.tenantOperator.member={tenant_id:'b',telegram_id:44};document.dispatchEvent(new CustomEvent('tenant-profile',{detail:{member:window.tenantOperator.member}}));});
  await page.waitForFunction(()=>document.getElementById('dialogDeveloperMarker').dataset.scope.startsWith('b:44:')&&!document.getElementById('dialogDeveloperMarker').disabled);
  await page.locator('#dialogConsultationStart').click();await page.locator('[data-developer-empty]').waitFor();assert.equal(await page.locator('form select').first().locator('option').count(),0);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.equal(calls.some(x=>/dispatch|support-tickets|messages\/send/.test(x.path)),false);
  await page.close();
 }
 console.log('Developer browser: mobile/desktop, default Helper, repeated mode, localization, empty/cancel, route/tenant privacy and reversible marker passed');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
