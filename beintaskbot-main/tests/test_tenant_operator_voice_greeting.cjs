const assert=require('node:assert/strict');
const test=require('node:test');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const mountVoice=require('../docs/tenant-copilot-voice-controller.js');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function pcm(ms,voiced=false){const samples=new Int16Array(ms*24);if(voiced)for(let i=0;i<samples.length;i++)samples[i]=Math.round(2000*Math.sin(i*.2));return new Uint8Array(samples.buffer);}
async function setup({greeting=false,deferredPlayback=false,language='ru'}={}){
 const dom=new JSDOM('<button data-voice-orb type="button" disabled></button><div id="voice"></div>');const root=dom.window.document.querySelector('#voice');
 const sent=[],mutes=[],plays=[],states=[];let capture,emit,finishPlayback,released=0,closed=0,interrupts=0;
 const playback=new Promise(resolve=>finishPlayback=resolve);
 const voice=mountVoice(root,{language,onState:value=>states.push(value),acquireMicrophone:async()=>()=>released++,
  openAudio:async fn=>{capture=fn;return{play:(id)=>plays.push(id),contextState:()=> 'running',
   finished:async()=>deferredPlayback?playback:true,interrupt:()=>{interrupts++;return 0;},
   interruptForTalk:()=>{interrupts++;return plays.length?{item_id:plays.at(-1),audio_end_ms:120}:null;},
   mute:value=>mutes.push(value),flush(){},stopNow(){},close:async()=>{closed++;}};},
  connect:async callback=>{emit=callback;await callback({type:'ready',greeting_pending:greeting});return{send:event=>sent.push(event),close:async()=>{}};},
  interruptTarget:dom.window.document.querySelector('[data-voice-orb]'),
  lifecycle:{visibilityState:'visible',addEventListener(){},removeEventListener(){}}});
 await voice.start();
 return{dom,root,voice,sent,mutes,plays,states,capture:(ms,voiced=false)=>{for(let n=0;n<ms;n+=20)capture(pcm(Math.min(20,ms-n),voiced));},
  emit:event=>emit(event),finishPlayback,counts:()=>({released,closed,interrupts}),destroy:async()=>{await voice.destroy();dom.window.close();}};
}
test('employee starts automatically; idle silence and brief noise do not create requests; one second silence submits once',async()=>{
 const h=await setup();try{
  assert.deepEqual(h.sent,[{type:'talk_start'}]);assert.equal(h.dom.window.document.querySelector('[data-voice-orb]').disabled,true);
  await h.emit({type:'talk_ready'});h.capture(5000);h.capture(80,true);h.capture(2000);
  assert.equal(h.sent.length,1,'idle silence and brief noise are not transmitted');
  h.capture(120,true);h.capture(980);assert.equal(h.sent.filter(x=>x.type==='talk_end').length,0);
  h.capture(20);assert.equal(h.sent.filter(x=>x.type==='talk_end').length,1);
  assert.equal(h.sent.at(-1).type,'talk_end');assert.equal(h.mutes.at(-1),true);
  h.capture(3000,true);h.capture(3000);assert.equal(h.sent.filter(x=>x.type==='talk_end').length,1);
 }finally{await h.destroy();}
});
test('a shorter pause followed by speech resets the one-second window',async()=>{
 const h=await setup();try{await h.emit({type:'talk_ready'});h.capture(120,true);h.capture(700);h.capture(100,true);h.capture(980);
 assert.equal(h.sent.some(x=>x.type==='talk_end'),false);h.capture(20);assert.equal(h.sent.filter(x=>x.type==='talk_end').length,1);
 }finally{await h.destroy();}
});
test('speech started immediately after an orb interrupt is buffered until the server confirms listening',async()=>{
 const h=await setup();try{
  await h.emit({type:'talk_ready'});h.capture(120,true);h.capture(1000);await h.emit({type:'thinking'});
  await h.emit({type:'audio',item_id:'answer',pcm:pcm(20)});
  const orb=h.dom.window.document.querySelector('[data-voice-orb]');orb.click();
  const before=h.sent.filter(x=>x.type==='audio').length;
  h.capture(120,true);h.capture(200);
  assert.equal(h.sent.filter(x=>x.type==='audio').length,before,'speech stays buffered while interruption is pending');
  await h.emit({type:'talk_ready'});
  assert.ok(h.sent.filter(x=>x.type==='audio').length>before,'the buffered first words are sent after readiness');
 }finally{await h.destroy();}
});
test('thinking and playback ignore microphone; listening resumes only after actual playback drain and server confirmation',async()=>{
 const h=await setup({deferredPlayback:true});try{
  await h.emit({type:'talk_ready'});h.capture(120,true);h.capture(2000);await h.emit({type:'thinking'});
  const before=h.sent.length;h.capture(3000,true);assert.equal(h.sent.length,before);
  await h.emit({type:'audio',item_id:'answer',pcm:pcm(20)});await h.emit({type:'audio_done',item_id:'answer'});
  h.capture(3000,true);assert.equal(h.sent.filter(x=>x.type==='talk_start').length,1);assert.equal(h.sent.some(x=>x.type==='played_all'),false);
  assert.equal(h.counts().interrupts,0);assert.equal(h.mutes.at(-1),true);
  h.finishPlayback(true);await tick();assert.deepEqual(h.sent.at(-1),{type:'played_all',item_id:'answer'});
  assert.equal(h.mutes.at(-1),true,'playback drain alone does not reopen microphone');
  await h.emit({type:'listening'});assert.equal(h.sent.at(-1).type,'talk_start');await h.emit({type:'talk_ready'});
  assert.equal(h.mutes.at(-1),false);h.capture(4000);assert.equal(h.sent.filter(x=>x.type==='talk_end').length,1);
 }finally{await h.destroy();}
});
test('tapping the active orb interrupts playback and resumes listening without a separate stop button',async()=>{
 const h=await setup({deferredPlayback:true});try{
  await h.emit({type:'talk_ready'});h.capture(120,true);h.capture(2000);await h.emit({type:'thinking'});
  await h.emit({type:'audio',item_id:'stopped',pcm:pcm(20)});await h.emit({type:'audio_done',item_id:'stopped'});
  const stop=h.dom.window.document.querySelector('[data-voice-orb]');assert.equal(stop.disabled,false);assert.equal(stop.getAttribute('aria-label'),'Перебить ответ и говорить');stop.click();stop.click();
  assert.deepEqual(h.sent.at(-1),{type:'stop_response',item_id:'stopped',audio_end_ms:120});
  assert.equal(h.sent.filter(x=>x.type==='stop_response').length,1);
  await h.emit({type:'thinking'});await h.emit({type:'audio',item_id:'stopped',pcm:pcm(20)});h.finishPlayback(true);await tick();
  assert.deepEqual(h.plays,['stopped']);assert.equal(h.sent.some(x=>x.type==='played_all'),false);
  const audioBefore=h.sent.filter(x=>x.type==='audio').length;h.capture(120,true);h.capture(300);assert.equal(h.sent.filter(x=>x.type==='audio').length,audioBefore,'input remains locally buffered until server confirmation');await h.emit({type:'talk_ready'});
  await h.emit({type:'thinking'});await h.emit({type:'audio',item_id:'stopped',pcm:pcm(20)});
  assert.equal(h.mutes.at(-1),false);assert.deepEqual(h.plays,['stopped']);h.capture(3000);
  assert.equal(h.sent.filter(x=>x.type==='talk_end').length,2);
  h.capture(120,true);h.capture(2000);await h.emit({type:'thinking'});await h.emit({type:'audio',item_id:'new',pcm:pcm(20)});
  assert.deepEqual(h.plays,['stopped','new']);
 }finally{await h.destroy();}
});
test('optional legacy greeting remains gated until server listening',async()=>{
 const h=await setup({greeting:true});try{
  h.capture(500,true);assert.equal(h.sent.length,0);await h.emit({type:'audio',item_id:'greeting',pcm:pcm(20)});
  await h.emit({type:'audio_done',item_id:'greeting'});await tick();assert.equal(h.sent.some(x=>x.type==='talk_start'),false);
  await h.emit({type:'listening'});assert.equal(h.sent.at(-1).type,'talk_start');
 }finally{await h.destroy();}
});
test('close control is accessible and graceful, ignores repeated close and late output, releases microphone',async()=>{
 for(const language of ['ru','az']){const h=await setup({language});try{
  const end=h.root.querySelector('.voice-session-end');assert.equal(end.getAttribute('aria-label'),language==='ru'?'Завершить сессию':'Sessiyanı bitir');
  assert.ok(end.querySelector('svg'));assert.equal(end.type,'button');end.focus();assert.equal(h.dom.window.document.activeElement,end);
  end.click();end.click();await h.emit({type:'audio',item_id:'late',pcm:pcm(20)});assert.equal(h.plays.length,0);
  assert.equal(h.sent.filter(x=>x.type==='end').length,1);await h.emit({type:'ended'});assert.equal(h.counts().released,1);assert.equal(h.counts().closed,1);
  assert.equal(h.root.querySelector('[role=status]').textContent,language==='ru'?'Соединение завершено':'Bağlantı bitdi','close status is translated text, not a DOM event');
 }finally{await h.destroy();}}
});
test('Stop while next answer is thinking never truncates the previous completed item',async()=>{
 const h=await setup();try{
  await h.emit({type:'talk_ready'});h.capture(120,true);h.capture(2000);await h.emit({type:'thinking'});
  await h.emit({type:'audio',item_id:'completed-A',pcm:pcm(20)});await h.emit({type:'audio_done',item_id:'completed-A'});await tick();
  await h.emit({type:'listening'});await h.emit({type:'talk_ready'});h.capture(120,true);h.capture(2000);await h.emit({type:'thinking'});
  h.dom.window.document.querySelector('[data-voice-orb]').click();
  assert.deepEqual(h.sent.at(-1),{type:'stop_response'},'stale engine item cannot identify the response currently thinking');
 }finally{await h.destroy();}
});

test('continuous employee speech longer than 24 seconds is not forcibly submitted',async()=>{
 const h=await setup();try{
  await h.emit({type:'talk_ready'});h.capture(26000,true);
  assert.equal(h.sent.some(x=>x.type==='talk_end'),false,'only an actual silence ends speech');
  h.capture(980);assert.equal(h.sent.some(x=>x.type==='talk_end'),false);
  h.capture(20);assert.equal(h.sent.filter(x=>x.type==='talk_end').length,1);
 }finally{await h.destroy();}
});

test('busy startup explains uncertainty and permits closing the local screen without sending an end',async()=>{
 const dom=new JSDOM('<div id="voice"></div>');const root=dom.window.document.querySelector('#voice');
 let dismissed=0,released=0,closed=0;
 const voice=mountVoice(root,{onDismiss:()=>dismissed++,acquireMicrophone:async()=>()=>released++,
  openAudio:async()=>({mute(){},stopNow(){},close:async()=>{closed++;}}),
  connect:async()=>{throw new Error('voice_session_busy');},
  lifecycle:{visibilityState:'visible',addEventListener(){},removeEventListener(){}}});
 try{
  await voice.start();await tick();
  assert.match(root.querySelector('[role=status]').textContent,/другой голосовой сеанс|завершение предыдущего не подтверждено/i);
  const end=root.querySelector('.voice-session-end');assert.equal(end.hidden,false);
  end.click();assert.equal(dismissed,1);assert.equal(released,1);assert.equal(closed,1);
 }finally{await voice.destroy();dom.window.close();}
});

test('the full screen closes locally after a busy refusal and retains no microphone',async()=>{
 const dom=new JSDOM('<main id="host"></main>',{url:'https://crm.pro.az/'});
 const win=dom.window,host=win.document.querySelector('#host');let released=0;
 win.mountExperimentalCopilotVoice=mountVoice;
 win.acquireTenantMicrophone=async()=>()=>released++;
 win.openExperimentalCopilotAudio=async()=>({mute(){},stopNow(){},close:async()=>{}});
 win.connectExperimentalCopilotVoice=async()=>{throw new Error('voice_session_busy');};
 const mountScreen=require('../docs/tenant-operator-copilot-voice.js');
 const screen=mountScreen(host,{language:'ru',socketPath:'/ws',preflight:async()=>({available:true}),isCurrent:()=>true});
 try{
  assert.equal(await screen.startVoice(),true);await tick();await tick();
  assert.equal(screen.hidden,false);assert.match(screen.querySelector('[role=status]').textContent,/другой голосовой сеанс/i);
  const end=screen.querySelector('.voice-session-end');assert.equal(end.hidden,false);end.click();
  assert.equal(screen.hidden,true);assert.equal(released,1);
  screen.hidden=false;
  screen.dispatchEvent(new win.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  assert.equal(screen.hidden,true);assert.equal(released,1);
 }finally{await screen.disposeVoice();dom.window.close();}
});

test('voice screen uses its animated orb as the only response interruption control',async()=>{
 const mountScreen=require('../docs/tenant-operator-copilot-voice.js');
 const dom=new JSDOM('<main id="host"></main>',{url:'https://crm.pro.az/'});const host=dom.window.document.querySelector('#host');
 const screen=mountScreen(host,{language:'ru',preflight:async()=>({available:true}),isCurrent:()=>true});
 try{
  const orb=screen.querySelector('[data-voice-orb]');assert.equal(orb.tagName,'BUTTON');assert.equal(orb.disabled,true);
  assert.equal(screen.querySelector('.voice-session-talk'),null,'no separate Stop/interrupt button is rendered');
 }finally{await screen.disposeVoice();dom.window.close();}
});

test('close during connecting is honored if the lease claim is refused',async()=>{
 const dom=new JSDOM('<div id="voice"></div>');const root=dom.window.document.querySelector('#voice');
 let dismissals=0,rejectConnect;const connecting=new Promise((_resolve,reject)=>{rejectConnect=reject;});
 const voice=mountVoice(root,{onDismiss:()=>dismissals++,acquireMicrophone:async()=>()=>{},
  openAudio:async()=>({mute(){},stopNow(){},close:async()=>{}}),connect:async()=>connecting,
  lifecycle:{visibilityState:'visible',addEventListener(){},removeEventListener(){}}});
 try{
  const starting=voice.start();await tick();root.querySelector('.voice-session-end').click();
  rejectConnect(new Error('voice_session_busy'));await starting;await tick();
  assert.equal(dismissals,1);assert.equal(voice.isActive(),false);
 }finally{await voice.destroy();dom.window.close();}
});

test('Escape on active voice waits for the server; unconfirmed termination stays visible',async()=>{
 for(const terminal of ['ended','end_unconfirmed']){
  const dom=new JSDOM('<main id="host"></main>',{url:'https://crm.pro.az/',pretendToBeVisual:true});
  const win=dom.window,host=win.document.querySelector('#host');let eventHandler,endCount=0;
  win.mountExperimentalCopilotVoice=mountVoice;win.acquireTenantMicrophone=async()=>()=>{};
  win.openExperimentalCopilotAudio=async()=>({mute(){},flush(){},stopNow(){},close:async()=>{}});
  win.connectExperimentalCopilotVoice=async (_path,cb)=>{eventHandler=cb;await cb({type:'ready'});
    return{send(event){if(event.type==='end')endCount++;},close:async()=>{}};};
  const mountScreen=require('../docs/tenant-operator-copilot-voice.js');
  const screen=mountScreen(host,{language:'ru',socketPath:'/ws',preflight:async()=>({available:true}),isCurrent:()=>true});
  try{
   await screen.startVoice();await tick();
   assert.equal(screen.isVoiceActive(),true,screen.querySelector('[role=status]').textContent);
   screen.dispatchEvent(new win.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
   assert.equal(endCount,1);assert.equal(screen.hidden,false,'screen remains while end is pending');
   await eventHandler({type:terminal});await tick();
   assert.equal(screen.hidden,terminal==='ended');
   if(terminal==='end_unconfirmed')assert.match(screen.querySelector('[role=status]').textContent,/не подтверждено/i);
  }finally{await screen.disposeVoice();dom.window.close();}
 }
});
