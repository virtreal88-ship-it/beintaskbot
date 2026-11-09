const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'),root=path.join(__dirname,'..');
(async()=>{const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined,args:['--no-sandbox']});try{
 for(const width of [390,1440]){
  const page=await browser.newPage({viewport:{width,height:900}});
  await page.route('http://test.local/**',route=>route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')}));
  await page.goto('http://test.local/app');
  await page.evaluate(()=>{
   document.querySelectorAll('.view').forEach(e=>e.classList.remove('active'));document.getElementById('view-dialogs').classList.add('active');
   window.testRoute={provider:'connect',lead_id:41,channel_id:'00000000-0000-4000-8000-000000000002',conversation_id:'client',state:'human',agent_id:'00000000-0000-4000-8000-000000000003',can_send:true,controls_allowed:true,available_actions:[]};
   window.tenantProfileSnapshot={member:{tenant_id:'00000000-0000-4000-8000-000000000001',telegram_id:33,role:'owner',active:true},capabilities:{modules:{dialogs:true,support:true}}};
   window.requests=[];window.micCalls=0;
   Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:async()=>{window.micCalls++;throw Error('not allowed');}},configurable:true});
   window.fetch=async(url,options={})=>{url=String(url);window.requests.push({url,method:options.method||'GET'});let result={};if(url.includes('/dialogs/history'))result={dialog:window.testRoute,messages:[{id:'one',external_id:'one',direction:'incoming',message_type:'text',body:'synthetic'}]};else if(url.includes('/dialogs?'))result={dialogs:[],channels:[],agents:[]};else if(url.includes('/api/platform/me'))return{ok:true,json:async()=>window.tenantProfileSnapshot};return{ok:true,json:async()=>({success:true,result})};};
  });
  for(const file of ['docs/tenant-operator-i18n.js','docs/tenant-microphone-lease.js','docs/tenant-copilot-voice-audio.js','docs/tenant-copilot-voice-controller.js','docs/tenant-copilot-voice-channel.js','docs/tenant-operator-copilot-voice.js','docs/tenant-operator-dialogs.js'])await page.addScriptTag({path:path.join(root,file)});
  await page.evaluate(()=>window.tenantOperator.openDialog(window.testRoute));
  await page.locator('#operatorCopilotToggle').click();
  const section=page.locator('[data-copilot-voice]');assert.equal(await section.count(),1);assert.equal(await section.isHidden(),true);
  assert.equal(await section.locator('button').isDisabled(),true);
  assert.equal(await section.locator('[data-voice-orb]').count(),1,'the voice screen contains only the real animation button, not a disabled placeholder CTA');
  assert.equal(await section.locator('[role=status]').count(),0,'an unavailable preflight does not mount session controls');
  assert.equal(await page.locator('#operatorCopilotAsk').isEnabled(),true);assert.equal(await page.locator('#chatText').isEnabled(),true);
  assert.equal(await page.evaluate(()=>window.micCalls),0);assert.equal(await page.evaluate(()=>window.requests.filter(x=>x.url.includes('copilot-voice/preflight')).length),1);
  await page.close();
 }
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
