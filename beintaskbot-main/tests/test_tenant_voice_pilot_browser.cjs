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
 const lifecyclePage=await browser.newPage();
 await lifecyclePage.route('https://test.local/**',route=>route.fulfill({contentType:'text/html',body:'<main id="root"></main>'}));
 await lifecyclePage.goto('https://test.local/lifecycle');
 await lifecyclePage.addScriptTag({path:path.join(root,'docs/tenant-copilot-voice-controller.js')});
 const startup=await lifecyclePage.evaluate(async()=>{
  const root=document.getElementById('root'),lifecycle=new EventTarget();let hidden=false,resolveOpening,released=0,closed=0,connections=0;
  Object.defineProperty(lifecycle,'visibilityState',{get:()=>hidden?'hidden':'visible'});
  const opening=new Promise(resolve=>{resolveOpening=()=>resolve({stopNow(){},close:async()=>{closed++;}});});
  const controller=window.mountExperimentalCopilotVoice(root,{acquireMicrophone:async()=>()=>released++,
   openAudio:()=>opening,connect:async()=>{connections++;return{send(){},close:async()=>{}};},lifecycle,language:'ru'});
  hidden=true;lifecycle.dispatchEvent(new Event('visibilitychange'));
  const idle={text:root.querySelector('[role=status]').textContent,disabled:root.querySelector('button').disabled,released,connections};
  hidden=false;
  const starting=controller.start();await new Promise(resolve=>setTimeout(resolve,0));
  hidden=true;lifecycle.dispatchEvent(new Event('visibilitychange'));resolveOpening();await starting;
  await new Promise(resolve=>{const timer=setInterval(()=>{if(released){clearInterval(timer);resolve();}},0);});
  return{idle,text:root.querySelector('[role=status]').textContent,disabled:root.querySelector('button').disabled,released,closed,connections};
 });
 assert.deepEqual(startup.idle,{text:'Голосовой разговор не начат',disabled:false,released:0,connections:0});
 assert.equal(startup.text,'Не удалось начать голосовой разговор. Проверьте подключение и доступ к микрофону.');
 assert.equal(startup.disabled,false);assert.equal(startup.released,1);assert.equal(startup.connections,0);
 const hiddenSession=await lifecyclePage.evaluate(async()=>{
  const root=document.createElement('section');document.body.append(root);let hidden=false,resolveConnect,sent=[],released=0,closed=0;
  const lifecycle=new EventTarget();Object.defineProperty(lifecycle,'visibilityState',{get:()=>hidden?'hidden':'visible'});
  const controller=window.mountExperimentalCopilotVoice(root,{acquireMicrophone:async()=>()=>released++,
   openAudio:async()=>({mute(){},play(){},interrupt(){return 0;},finished:async()=>true,stopNow(){},close:async()=>{}}),
   connect:async onEvent=>new Promise(resolve=>{resolveConnect=()=>{onEvent({type:'ready'});resolve({send(event){sent.push(event);if(event.type==='end')setTimeout(()=>onEvent({type:'ended'}),0);},close:async()=>{closed++;}});};}),
   lifecycle,language:'ru'});
  const starting=controller.start();await new Promise(resolve=>setTimeout(resolve,0));
  hidden=true;lifecycle.dispatchEvent(new Event('visibilitychange'));
  const beforeReady={status:root.querySelector('[role=status]').textContent,closed,sent:sent.length};
  resolveConnect();await starting;
  await new Promise(resolve=>{const timer=setInterval(()=>{if(root.querySelector('[role=status]').textContent==='Соединение завершено'){clearInterval(timer);resolve();}},0);});
  return{beforeReady,status:root.querySelector('[role=status]').textContent,sent,released,closed};
 });
 assert.deepEqual(hiddenSession.beforeReady,{status:'Подключение…',closed:0,sent:0});
 assert.equal(hiddenSession.sent.some(event=>event.type==='end'),true);
 assert.equal(hiddenSession.status,'Соединение завершено');assert.equal(hiddenSession.released,1);assert.equal(hiddenSession.closed,1);
 const hiddenActive=await lifecyclePage.evaluate(async()=>{
  const root=document.createElement('section');document.body.append(root);let hidden=false,sent=[],closed=0;
  const lifecycle=new EventTarget();Object.defineProperty(lifecycle,'visibilityState',{get:()=>hidden?'hidden':'visible'});
  const controller=window.mountExperimentalCopilotVoice(root,{acquireMicrophone:async()=>()=>{},
   openAudio:async()=>({mute(){},play(){},interrupt(){return 0;},finished:async()=>true,stopNow(){},close:async()=>{}}),
   connect:async onEvent=>{onEvent({type:'ready'});return{send(event){sent.push(event);if(event.type==='end')setTimeout(()=>onEvent({type:'ended'}),0);},close:async()=>{closed++;}};},
   lifecycle,language:'ru'});
  await controller.start();hidden=true;lifecycle.dispatchEvent(new Event('visibilitychange'));
  await new Promise(resolve=>{const timer=setInterval(()=>{if(root.querySelector('[role=status]').textContent==='Соединение завершено'){clearInterval(timer);resolve();}},0);});
  return{status:root.querySelector('[role=status]').textContent,sent,closed};
 });
 assert.equal(hiddenActive.sent.some(event=>event.type==='end'),true);
 assert.equal(hiddenActive.status,'Соединение завершено');assert.equal(hiddenActive.closed,1);
 await lifecyclePage.close();
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
