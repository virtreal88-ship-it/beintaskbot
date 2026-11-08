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
  window.requests=[];window.voiceEvents=[];window.incomingEvents=[];window.sockets=[];window.releaseCount=0;window.audioClosed=0;window.interruptCalls=0;window.deferTalkReady=false;window.releaseTalkReady=null;window.talkEndEmpty=false;window.muteCalls=[];window.interruptItem='assistant-1';
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
   send(value){if(value instanceof ArrayBuffer){window.voiceEvents.push({type:'audio',bytes:value.byteLength});return;}if(typeof value==='string'){const event=JSON.parse(value);window.voiceEvents.push(event);if(event.type==='end')setTimeout(()=>this.emit({type:'ended'}),0);if(['talk_start','stop_response'].includes(event.type)){if(window.deferTalkReady)window.releaseTalkReady=()=>this.emit({type:'talk_ready'});else setTimeout(()=>this.emit({type:'talk_ready'}),0);}if(event.type==='talk_end')setTimeout(()=>this.emit(window.talkEndEmpty?{type:'talk_empty'}:{type:'thinking'}),0);if(event.type==='played_all')setTimeout(()=>this.emit({type:'listening'}),0);}}
   close(){this.readyState=3;this.onclose?.();}
  }
  window.WebSocket=FakeSocket;
 });
 for(const file of ['docs/tenant-operator-i18n.js','docs/tenant-microphone-lease.js','docs/tenant-copilot-voice-audio.js','docs/tenant-copilot-voice-controller.js','docs/tenant-copilot-voice-channel.js','docs/tenant-operator-copilot-voice.js','docs/tenant-operator-dialogs.js'])await page.addScriptTag({path:path.join(root,file)});
 await page.evaluate(()=>{
  window.acquireTenantMicrophone=async()=>()=>window.releaseCount++;
  window.playCount=0;window.openExperimentalCopilotAudio=async(onPcm)=>{window.emitPcm=onPcm;return{flush(){onPcm(new Uint8Array(120));},mute:value=>{window.muted=value;window.muteCalls.push(value);},play:()=>{window.playCount++;},contextState:()=> 'running',interrupt:()=>{window.interruptCalls++;return 0;},interruptForTalk:()=>{window.interruptCalls++;return{item_id:window.interruptItem,audio_end_ms:120};},finished:async()=>true,stopNow(){},close:async()=>{window.audioClosed++;}};};
 });
 await page.evaluate(()=>window.tenantOperator.openDialog(window.testRoute));
 await page.locator('#operatorCopilotVoiceToggle').click();
 const voice=page.locator('[data-copilot-voice]');await voice.waitFor({state:'visible'});
 assert.equal(await page.locator('#operatorCopilot').isVisible(),false,'voice starts directly without opening the Copilot helper panel');
 assert.equal(await voice.evaluate(node=>node.parentElement?.id==='operatorConversationContent'),true,'voice screen is mounted independently of the helper panel');
 assert.equal(await voice.getAttribute('role'),'dialog','voice mode opens as an accessible full-screen conversation');
 const voiceScreen=await voice.boundingBox();assert.ok(voiceScreen.width>=390&&voiceScreen.height>=800,'voice screen fills the mobile viewport');
 assert.ok(await voice.locator('[data-voice-orb]').isVisible(),'animated assistant orb is visible');
 await page.waitForFunction(()=>document.querySelector('[data-copilot-voice] [role=status]')?.textContent==='Говорите. После паузы в 2 секунды AI ответит');
 assert.equal(await voice.locator('[data-voice-orb]').getAttribute('data-state'),'listening');
 assert.equal(await voice.locator('[data-voice-orb]').evaluate(node=>getComputedStyle(node).animationName),'tenantVoiceListenPulse');
 assert.equal(await page.locator('#operatorCopilotVoiceToggle').getAttribute('data-voice-state'),'listening','composer voice icon indicates that the user can speak');
 assert.equal(await page.locator('#operatorCopilotVoiceToggle .operator-voice-wave i').first().evaluate(node=>getComputedStyle(node).animationName),'operatorVoiceListen','listening state animates distinctly');
 assert.match(await page.evaluate(()=>window.sockets[0].url),/^wss:\/\/test\.local\/api\/platform\/ai\/dialogs\/copilot-voice\/ws\?/);
 await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-language')));
 await page.evaluate(()=>window.tenantOperator.openDialog(window.testRoute));
 assert.equal(await page.evaluate(()=>window.releaseCount),0);
 assert.equal(await voice.isVisible(),true);
 await page.waitForFunction(()=>window.muted===false);
 assert.equal(await voice.locator('.voice-session-talk').isVisible(),false,'there is no manual employee-turn button');
 await page.evaluate(()=>{
   window.emitSpeech=(ms=120)=>{for(let offset=0;offset<ms;offset+=20){const pcm=new Int16Array(480);for(let i=0;i<pcm.length;i++)pcm[i]=Math.round(2000*Math.sin(i*.2));window.emitPcm(new Uint8Array(pcm.buffer));}};
   window.emitSilence=ms=>{for(let offset=0;offset<ms;offset+=20)window.emitPcm(new Uint8Array(960));};
   window.emitSilence(4000);
 });
 assert.equal(await page.evaluate(()=>window.voiceEvents.filter(x=>x.type==='audio'||x.type==='talk_end').length),0,'idle silence does not upload or generate empty requests');
 await page.evaluate(()=>{window.emitSpeech();window.emitSilence(1980);});
 assert.equal(await page.evaluate(()=>window.voiceEvents.some(x=>x.type==='talk_end')),false,'shorter than two seconds does not submit');
 await page.evaluate(()=>window.emitSilence(20));
 await page.waitForFunction(()=>window.voiceEvents.some(x=>x.type==='talk_end'));
 // The fake server delivers thinking on a timer, after the outgoing commit.
 // Wait for that acknowledgement before injecting its response audio.
 await page.waitForFunction(()=>window.incomingEvents.filter(x=>x.type==='thinking').length===1);
 assert.equal(await page.evaluate(()=>window.voiceEvents.findIndex(x=>x.type==='talk_start')<window.voiceEvents.findIndex(x=>x.type==='talk_end')),true,'automatic capture starts before submitting');
 assert.equal(await page.evaluate(()=>window.voiceEvents.findLastIndex(x=>x.type==='audio')<window.voiceEvents.findIndex(x=>x.type==='talk_end')),true,'last audio precedes commit');
 await voice.getByRole('button',{name:'Стоп',exact:true}).waitFor({state:'visible'});
 await page.evaluate(()=>window.sockets[0].emit({type:'audio',item_id:'assistant-1',pcm_base64:'AAA='}));
 await page.waitForFunction(()=>document.querySelector('#operatorCopilotVoiceToggle')?.dataset.voiceState==='responding');
 assert.equal(await voice.locator('[data-voice-orb]').getAttribute('data-state'),'responding');
 assert.equal(await voice.locator('[data-voice-orb]').evaluate(node=>getComputedStyle(node).animationName),'tenantVoiceSpeakPulse');
 assert.equal(await page.locator('#operatorCopilotVoiceToggle .operator-voice-wave i').first().evaluate(node=>getComputedStyle(node).animationName),'operatorVoiceSpeak');
 const uploadsBefore=await page.evaluate(()=>window.voiceEvents.filter(x=>x.type==='audio').length);
 await page.evaluate(()=>{window.emitSpeech(300);window.emitSilence(2200);});
 assert.equal(await page.evaluate(()=>window.voiceEvents.filter(x=>x.type==='audio').length),uploadsBefore,'speech cannot upload or interrupt AI output');
 assert.equal(await page.evaluate(()=>window.interruptCalls),0);
 assert.equal(await page.evaluate(()=>window.muted),true,'microphone is physically muted during output');
 await page.waitForFunction(()=>window.voiceEvents.some(x=>x.type==='playback_scheduled'));
 assert.deepEqual(await page.evaluate(()=>window.voiceEvents.filter(x=>x.type==='playback_scheduled')),[{type:'playback_scheduled',item_id:'assistant-1',context_state:'running'}]);
 await page.evaluate(()=>window.sockets[0].emit({type:'audio_done',item_id:'assistant-1'}));
 await page.waitForFunction(()=>window.voiceEvents.some(x=>x.type==='played_all'));
 await page.waitForFunction(()=>window.muted===false);
 assert.equal(await voice.locator('.voice-session-talk').isVisible(),false);
 // A second turn starts with speech, without a new click.
 await page.evaluate(()=>{window.emitSpeech();window.emitSilence(2000);});
 await page.waitForFunction(()=>window.voiceEvents.filter(x=>x.type==='talk_end').length===2);
 await page.waitForFunction(()=>window.incomingEvents.filter(x=>x.type==='thinking').length===2);
 await page.evaluate(()=>window.sockets[0].emit({type:'audio',item_id:'assistant-2',pcm_base64:'AAA='}));
 await page.waitForFunction(()=>window.playCount===2);
 await page.evaluate(()=>{window.deferTalkReady=true;window.interruptItem='assistant-2';});
 await voice.getByRole('button',{name:'Стоп',exact:true}).click();
 await page.waitForFunction(()=>window.voiceEvents.some(x=>x.type==='stop_response'));
 assert.deepEqual(await page.evaluate(()=>window.voiceEvents.findLast(x=>x.type==='stop_response')),
   {type:'stop_response',item_id:'assistant-2',audio_end_ms:120},'explicit Stop carries exact item and conservative playback offset');
 await page.evaluate(()=>{window.emitSpeech(5000);window.sockets[0].emit({type:'audio',item_id:'assistant-2',pcm_base64:'AAA='});});
 assert.equal(await page.evaluate(()=>window.playCount),2,'late cancelled audio is discarded');
 assert.equal(await page.evaluate(()=>window.muted),true,'input stays muted until server confirms Stop');
 assert.equal(await page.evaluate(()=>window.voiceEvents.some(x=>x.type==='end')),false,'delayed Stop acknowledgement does not terminate the call');
 await page.evaluate(()=>{window.deferTalkReady=false;window.releaseTalkReady?.();});
 await page.waitForFunction(()=>window.muted===false);
 await page.evaluate(()=>window.emitSilence(5000));
 assert.equal(await page.evaluate(()=>window.voiceEvents.filter(x=>x.type==='talk_end').length),2,'Stop resumes idle waiting, not an empty answer');
 await page.evaluate(()=>{window.emitSpeech();window.emitSilence(2000);});
 await page.waitForFunction(()=>window.voiceEvents.filter(x=>x.type==='talk_end').length===3);
 await page.waitForFunction(()=>window.incomingEvents.filter(x=>x.type==='thinking').length===3);
 await page.evaluate(()=>window.sockets[0].emit({type:'audio',item_id:'assistant-3',pcm_base64:'AAA='}));
 await page.waitForFunction(()=>window.playCount===3);
 const closeButton=voice.getByRole('button',{name:'Завершить сессию'});
 assert.equal(await closeButton.locator('svg').count(),1,'session close uses a labelled cross icon');
 await closeButton.focus();
 await page.keyboard.press('Enter');
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
 await wrapperPage.waitForFunction(()=>document.querySelector('[data-copilot-voice] [role=status]')?.textContent==='Говорите. После паузы в 2 секунды AI ответит');
 await wrapperPage.evaluate(()=>window.dispatchEvent(new Event('pagehide')));
 await wrapperPage.waitForFunction(()=>window.released===1&&window.closedSockets===1);
 assert.equal(await wrapperPage.evaluate(()=>window.voiceEvents.filter(x=>x.type==='end').length),1,'pagehide sends end and waits for server acknowledgement before closing');
 await wrapperPage.evaluate(()=>window.voiceSection.startVoice());
 await wrapperPage.waitForFunction(()=>document.querySelector('[data-copilot-voice] [role=status]')?.textContent==='Говорите. После паузы в 2 секунды AI ответит');
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
