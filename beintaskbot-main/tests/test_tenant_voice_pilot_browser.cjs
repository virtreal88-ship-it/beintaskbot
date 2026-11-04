const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'),root=path.join(__dirname,'..');

(async()=>{const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined,args:['--no-sandbox']});try{
 for(const width of [390,1440]){
 const page=await browser.newPage({viewport:{width,height:900}});
 await page.route('https://test.local/**',route=>route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')}));
 await page.goto('https://test.local/app?lang=ru');
 await page.evaluate(()=>{
  document.querySelectorAll('.view').forEach(e=>e.classList.remove('active'));document.getElementById('view-dialogs').classList.add('active');
  window.testRoute={provider:'connect',lead_id:41,channel_id:'00000000-0000-4000-8000-000000000002',conversation_id:'client',state:'human',agent_id:'00000000-0000-4000-8000-000000000003',can_send:true,controls_allowed:true,available_actions:[]};
  window.tenantProfileSnapshot={member:{tenant_id:'00000000-0000-4000-8000-000000000001',telegram_id:33,role:'owner',active:true},capabilities:{modules:{dialogs:true,support:true}}};
  window.requests=[];window.voiceEvents=[];window.sockets=[];window.releaseCount=0;window.audioClosed=0;
  window.fetch=async(url,options={})=>{url=String(url);window.requests.push({url,method:options.method||'GET'});let result={};
   if(url.includes('/dialogs/copilot-voice/preflight'))result={available:true};
   else if(url.includes('/dialogs/history'))result={dialog:window.testRoute,messages:[{id:'one',external_id:'one',direction:'incoming',message_type:'text',body:'synthetic'}]};
   else if(url.includes('/dialogs?'))result={dialogs:[],channels:[],agents:[]};
   else if(url.includes('/api/platform/me'))return{ok:true,json:async()=>window.tenantProfileSnapshot};
   return{ok:true,json:async()=>({success:true,result})};};
  class FakeSocket{
   static OPEN=1;
   constructor(url){this.url=url;this.readyState=0;window.sockets.push(this);setTimeout(()=>{this.readyState=1;this.onopen?.();setTimeout(()=>this.emit({type:'ready'}),0);},0);}
   emit(event){this.onmessage?.({data:JSON.stringify(event)});}
   send(value){if(typeof value==='string'){const event=JSON.parse(value);window.voiceEvents.push(event);if(event.type==='end')setTimeout(()=>this.emit({type:'ended'}),0);}}
   close(){this.readyState=3;this.onclose?.();}
  }
  window.WebSocket=FakeSocket;
 });
 for(const file of ['docs/tenant-operator-i18n.js','docs/tenant-microphone-lease.js','docs/tenant-copilot-voice-audio.js','docs/tenant-copilot-voice-controller.js','docs/tenant-copilot-voice-channel.js','docs/tenant-operator-copilot-voice.js','docs/tenant-operator-dialogs.js'])await page.addScriptTag({path:path.join(root,file)});
 await page.evaluate(()=>{
  window.acquireTenantMicrophone=async()=>()=>window.releaseCount++;
  window.openExperimentalCopilotAudio=async()=>({mute:value=>{window.muted=value;},play:()=>{window.played=true;},interrupt:()=>0,finished:async()=>true,stopNow(){},close:async()=>{window.audioClosed++;}});
 });
 await page.evaluate(()=>window.tenantOperator.openDialog(window.testRoute));
 await page.locator('#operatorCopilotToggle').click();
 const voice=page.locator('[data-copilot-voice]');await voice.waitFor({state:'visible'});
 await voice.getByRole('button',{name:'Начать голосовой разговор'}).click();
 await page.waitForFunction(()=>document.querySelector('[data-copilot-voice] [role=status]')?.textContent==='Слушаю');
 assert.match(await page.evaluate(()=>window.sockets[0].url),/^wss:\/\/test\.local\/api\/platform\/ai\/dialogs\/copilot-voice\/ws\?/);
 await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-language')));
 await page.evaluate(()=>window.tenantOperator.openDialog(window.testRoute));
 assert.equal(await page.evaluate(()=>window.releaseCount),0);
 assert.equal(await voice.isVisible(),true);
 await page.evaluate(()=>window.sockets[0].emit({type:'audio',item_id:'assistant-1',pcm_base64:'AAA='}));
 await page.evaluate(()=>window.sockets[0].emit({type:'interrupt',item_id:'assistant-1'}));
 await page.waitForFunction(()=>window.voiceEvents.some(x=>x.type==='played'));
 assert.deepEqual(await page.evaluate(()=>window.voiceEvents.find(x=>x.type==='played')),{type:'played',item_id:'assistant-1',played_ms:0});
 await voice.getByRole('button',{name:'Выключить микрофон'}).click();assert.equal(await page.evaluate(()=>window.muted),true);
 await voice.getByRole('button',{name:'Завершить разговор'}).click();
 await page.waitForFunction(()=>window.releaseCount===1 && window.audioClosed===1);
 assert.equal(await page.evaluate(()=>window.voiceEvents.some(x=>x.type==='end')),true);
 assert.equal(await page.evaluate(()=>window.requests.some(x=>/\/crm\/chat\/send/.test(x.url))),false);
 await page.close();
 }
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
