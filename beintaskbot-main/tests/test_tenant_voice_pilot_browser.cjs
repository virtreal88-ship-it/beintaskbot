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
  window.requests=[];window.voiceEvents=[];window.incomingEvents=[];window.sockets=[];window.releaseCount=0;window.audioClosed=0;window.interruptCalls=0;window.deferTalkReady=false;window.releaseTalkReady=null;
  window.fetch=async(url,options={})=>{url=String(url);window.requests.push({url,method:options.method||'GET'});let result={};
   if(url.includes('/dialogs/copilot-voice/preflight'))result={available:true};
   else if(url.includes('/dialogs/history'))result={dialog:window.testRoute,messages:[{id:'one',external_id:'one',direction:'incoming',message_type:'text',body:'synthetic'}]};
   else if(url.includes('/dialogs?'))result={dialogs:[],channels:[],agents:[]};
   else if(url.includes('/api/platform/me'))return{ok:true,json:async()=>window.tenantProfileSnapshot};
   return{ok:true,json:async()=>({success:true,result})};};
  class FakeSocket{
   static OPEN=1;
   constructor(url){this.url=url;this.readyState=0;window.sockets.push(this);setTimeout(()=>{this.readyState=1;this.onopen?.();setTimeout(()=>this.emit({type:'ready'}),0);},0);}
   emit(event){window.incomingEvents.push(event);this.onmessage?.({data:JSON.stringify(event)});}
   send(value){if(value instanceof ArrayBuffer){window.voiceEvents.push({type:'audio',bytes:value.byteLength});return;}if(typeof value==='string'){const event=JSON.parse(value);window.voiceEvents.push(event);if(event.type==='end')setTimeout(()=>this.emit({type:'ended'}),0);if(event.type==='talk_start'){if(window.deferTalkReady)window.releaseTalkReady=()=>this.emit({type:'talk_ready'});else setTimeout(()=>this.emit({type:'talk_ready'}),0);}if(event.type==='talk_end')setTimeout(()=>this.emit({type:'thinking'}),0);if(event.type==='played_all')setTimeout(()=>this.emit({type:'listening'}),0);}}
   close(){this.readyState=3;this.onclose?.();}
  }
  window.WebSocket=FakeSocket;
 });
 for(const file of ['docs/tenant-operator-i18n.js','docs/tenant-microphone-lease.js','docs/tenant-copilot-voice-audio.js','docs/tenant-copilot-voice-controller.js','docs/tenant-copilot-voice-channel.js','docs/tenant-operator-copilot-voice.js','docs/tenant-operator-dialogs.js'])await page.addScriptTag({path:path.join(root,file)});
 await page.evaluate(()=>{
  window.acquireTenantMicrophone=async()=>()=>window.releaseCount++;
  window.playCount=0;window.openExperimentalCopilotAudio=async(onPcm)=>{window.emitPcm=onPcm;return{flush(){onPcm(new Uint8Array(120));},mute:value=>{window.muted=value;},play:()=>{window.playCount++;},contextState:()=> 'running',interrupt:()=>{window.interruptCalls++;return 0;},interruptForTalk:()=>{window.interruptCalls++;return{item_id:'assistant-1',audio_end_ms:120};},finished:async()=>true,stopNow(){},close:async()=>{window.audioClosed++;}};};
 });
 await page.evaluate(()=>window.tenantOperator.openDialog(window.testRoute));
 await page.locator('#operatorCopilotVoiceToggle').click();
 const voice=page.locator('[data-copilot-voice]');await voice.waitFor({state:'visible'});
 assert.equal(await page.locator('#operatorCopilot').isVisible(),false,'voice starts directly without opening the Copilot helper panel');
 assert.equal(await voice.evaluate(node=>node.parentElement?.id==='operatorConversationContent'),true,'voice screen is mounted independently of the helper panel');
 assert.equal(await voice.getAttribute('role'),'dialog','voice mode opens as an accessible full-screen conversation');
 const voiceScreen=await voice.boundingBox();assert.ok(voiceScreen.width>=390&&voiceScreen.height>=800,'voice screen fills the mobile viewport');
 assert.ok(await voice.locator('[data-voice-orb]').isVisible(),'animated assistant orb is visible');
 await page.waitForFunction(()=>document.querySelector('[data-copilot-voice] [role=status]')?.textContent==='Нажмите, чтобы говорить');
 assert.equal(await voice.locator('[data-voice-orb]').getAttribute('data-state'),'listening');
 assert.equal(await voice.locator('[data-voice-orb]').evaluate(node=>getComputedStyle(node).animationName),'tenantVoiceListenPulse');
 assert.equal(await page.locator('#operatorCopilotVoiceToggle').getAttribute('data-voice-state'),'listening','composer voice icon indicates that the user can speak');
 assert.equal(await page.locator('#operatorCopilotVoiceToggle .operator-voice-wave i').first().evaluate(node=>getComputedStyle(node).animationName),'operatorVoiceListen','listening state animates distinctly');
 assert.match(await page.evaluate(()=>window.sockets[0].url),/^wss:\/\/test\.local\/api\/platform\/ai\/dialogs\/copilot-voice\/ws\?/);
 await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-language')));
 await page.evaluate(()=>window.tenantOperator.openDialog(window.testRoute));
 assert.equal(await page.evaluate(()=>window.releaseCount),0);
 assert.equal(await voice.isVisible(),true);
 await voice.getByRole('button',{name:'Нажмите, чтобы говорить'}).click();
 await voice.getByRole('button',{name:'Нажмите, чтобы отправить'}).waitFor({state:'visible'});
 await page.evaluate(()=>window.emitPcm(new Uint8Array(480)));
 assert.equal(await page.evaluate(()=>window.voiceEvents.filter(x=>x.type==='audio').length),1,'microphone frames are sent only after the talk click');
 await voice.getByRole('button',{name:'Нажмите, чтобы отправить'}).click();
 await page.waitForFunction(()=>window.voiceEvents.some(x=>x.type==='talk_end'));
 assert.equal(await page.evaluate(()=>window.voiceEvents.findIndex(x=>x.type==='talk_start')<window.voiceEvents.findIndex(x=>x.type==='talk_end')),true,'the second click submits the recorded turn');
 assert.equal(await page.evaluate(()=>window.voiceEvents.findLastIndex(x=>x.type==='audio')<window.voiceEvents.findIndex(x=>x.type==='talk_end')),true,'the final buffered audio is sent before the commit request');
 await page.waitForFunction(()=>document.querySelector('[data-copilot-voice] .voice-session-talk')?.getAttribute('aria-label')==='Нажмите, чтобы перебить и говорить');
 await page.evaluate(()=>window.sockets[0].emit({type:'audio',item_id:'assistant-1',pcm_base64:'AAA='}));
 await page.waitForFunction(()=>document.querySelector('#operatorCopilotVoiceToggle')?.dataset.voiceState==='responding');
 assert.equal(await voice.locator('[data-voice-orb]').getAttribute('data-state'),'responding');
 assert.equal(await voice.locator('[data-voice-orb]').evaluate(node=>getComputedStyle(node).animationName),'tenantVoiceSpeakPulse');
 assert.equal(await page.locator('#operatorCopilotVoiceToggle .operator-voice-wave i').first().evaluate(node=>getComputedStyle(node).animationName),'operatorVoiceSpeak','assistant response animates distinctly');
 await page.waitForFunction(()=>window.voiceEvents.some(x=>x.type==='playback_scheduled'));
 assert.deepEqual(await page.evaluate(()=>window.voiceEvents.filter(x=>x.type==='playback_scheduled')),[{type:'playback_scheduled',item_id:'assistant-1',context_state:'running'}]);
 await page.evaluate(()=>window.sockets[0].emit({type:'audio_done',item_id:'assistant-1'}));
 await page.waitForFunction(()=>window.voiceEvents.some(x=>x.type==='played_all'));
 await page.waitForFunction(()=>document.querySelector('[data-copilot-voice] [role=status]')?.textContent==='Нажмите, чтобы говорить');
 // A user can interrupt a later answer at any point, then speak a follow-up
 // without closing the session or losing the provider conversation context.
 await page.evaluate(()=>window.sockets[0].emit({type:'thinking'}));
 await page.evaluate(()=>window.sockets[0].emit({type:'audio',item_id:'assistant-1',pcm_base64:'AAA='}));
 await page.waitForFunction(()=>document.querySelector('[data-copilot-voice] [role=status]')?.textContent==='AI отвечает');
 await page.evaluate(()=>window.deferTalkReady=true);
 await voice.getByRole('button',{name:'Нажмите, чтобы перебить и говорить'}).click();
 await page.waitForFunction(()=>window.voiceEvents.some(x=>x.type==='talk_start'&&x.item_id==='assistant-1'));
 assert.deepEqual(await page.evaluate(()=>window.voiceEvents.findLast(x=>x.type==='talk_start')),
   {type:'talk_start',item_id:'assistant-1',audio_end_ms:120},'the interruption carries the exact assistant item and conservative playback offset');
 await page.evaluate(()=>{for(let i=0;i<120;i++)window.emitPcm(new Uint8Array(2048));});
 assert.equal(await page.evaluate(()=>{const start=window.voiceEvents.findLastIndex(x=>x.type==='talk_start'&&x.item_id==='assistant-1');return window.voiceEvents.findIndex((x,i)=>i>start&&x.type==='audio'&&x.bytes===2048)===-1;}),true,'microphone frames wait for server acknowledgement before uplink');
 assert.equal(await page.evaluate(()=>window.voiceEvents.some(x=>x.type==='end')),false,'a five-second server acknowledgement delay does not terminate the active voice session');
 await page.evaluate(()=>{window.deferTalkReady=false;window.releaseTalkReady?.();});
 await page.waitForFunction(()=>document.querySelector('[data-copilot-voice] button[aria-pressed="true"]')!==null);
 assert.equal(await page.evaluate(()=>window.voiceEvents.filter(x=>x.type==='audio'&&x.bytes===2048).length),120,'all bounded speech captured while waiting for talk_ready is forwarded');
 assert.equal(await page.evaluate(()=>window.voiceEvents.findLastIndex(x=>x.type==='audio')>window.voiceEvents.findLastIndex(x=>x.type==='talk_start'&&x.item_id==='assistant-1')),true,'queued follow-up audio is sent after talk_ready');
 await page.evaluate(()=>window.sockets[0].emit({type:'audio',item_id:'assistant-1',pcm_base64:'AAA='}));
 assert.equal(await page.evaluate(()=>window.interruptCalls>=2),true,'late assistant audio is discarded while the user is speaking');
 assert.equal(await page.evaluate(()=>window.playCount),2,'late assistant audio does not overwrite the follow-up turn');
 await voice.getByRole('button',{name:'Нажмите, чтобы отправить'}).click();
 await page.waitForFunction(()=>window.voiceEvents.filter(x=>x.type==='talk_end').length===2);
 await page.evaluate(()=>window.sockets[0].emit({type:'thinking'}));
 await page.evaluate(()=>window.sockets[0].emit({type:'audio',item_id:'assistant-2',pcm_base64:'AAA='}));
 await page.waitForFunction(()=>window.playCount===3,'the follow-up answer can play after the user turn');
 await voice.getByRole('button',{name:'Завершить разговор'}).click();
 await page.waitForFunction(()=>window.releaseCount===1 && window.audioClosed===1);
 await voice.waitFor({state:'hidden'});
 assert.equal(await page.evaluate(()=>window.voiceEvents.some(x=>x.type==='end')),true);
 assert.equal(await page.evaluate(()=>window.requests.some(x=>/\/crm\/chat\/send/.test(x.url))),false);
 await page.close();
 }
 const wrapperPage=await browser.newPage();
 await wrapperPage.route('https://test.local/**',route=>route.fulfill({contentType:'text/html',body:'<main id="root"></main>'}));
 await wrapperPage.goto('https://test.local/wrapper');
 for(const file of ['docs/tenant-copilot-voice-controller.js','docs/tenant-copilot-voice-channel.js','docs/tenant-operator-copilot-voice.js'])await wrapperPage.addScriptTag({path:path.join(root,file)});
 await wrapperPage.evaluate(()=>{
  window.voiceEvents=[];window.closedSockets=0;window.released=0;
  class FakeSocket{static OPEN=1;constructor(){this.readyState=0;setTimeout(()=>{this.readyState=1;this.onopen?.();setTimeout(()=>this.emit({type:'ready'}),0);},0);}emit(value){this.onmessage?.({data:JSON.stringify(value)});}send(value){if(typeof value!=='string')return;const item=JSON.parse(value);window.voiceEvents.push(item);if(item.type==='end')setTimeout(()=>this.emit({type:'ended'}),0);}close(){if(this.readyState===3)return;this.readyState=3;window.closedSockets++;this.onclose?.();}}
  window.WebSocket=FakeSocket;window.acquireTenantMicrophone=async()=>()=>window.released++;
  window.openExperimentalCopilotAudio=async()=>({mute(){},play(){},interrupt(){return 0;},finished:async()=>true,stopNow(){},close:async()=>{}});
 });
 await wrapperPage.evaluate(()=>{window.voiceSection=window.mountTenantCopilotVoice(document.getElementById('root'),{preflight:async()=>({available:true}),isCurrent:()=>true,socketPath:'/api/voice',language:'ru'});window.voiceSection.activateVoice();});
 await wrapperPage.evaluate(()=>window.voiceSection.startVoice());
 await wrapperPage.waitForFunction(()=>document.querySelector('[data-copilot-voice] [role=status]')?.textContent==='Нажмите, чтобы говорить');
 await wrapperPage.evaluate(()=>window.dispatchEvent(new Event('pagehide')));
 await wrapperPage.waitForFunction(()=>window.released===1&&window.closedSockets===1);
 assert.equal(await wrapperPage.evaluate(()=>window.voiceEvents.filter(x=>x.type==='end').length),1,'pagehide sends end and waits for server acknowledgement before closing');
 await wrapperPage.evaluate(()=>window.voiceSection.startVoice());
 await wrapperPage.waitForFunction(()=>document.querySelector('[data-copilot-voice] [role=status]')?.textContent==='Нажмите, чтобы говорить');
 await wrapperPage.evaluate(()=>document.dispatchEvent(new Event('tenant-chat-close')));
 await wrapperPage.waitForFunction(()=>window.released===2&&window.closedSockets===2);
 assert.equal(await wrapperPage.evaluate(()=>window.voiceEvents.filter(x=>x.type==='end').length),2,'dialog disposal also waits for graceful end');
 await wrapperPage.close();
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
