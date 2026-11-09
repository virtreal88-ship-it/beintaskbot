const assert=require('node:assert/strict');
const test=require('node:test');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const mountVoice=require('../docs/tenant-copilot-voice-controller.js');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function pcm(ms,voiced=false){const samples=new Int16Array(ms*24);if(voiced)for(let i=0;i<samples.length;i++)samples[i]=Math.round(2000*Math.sin(i*.2));return new Uint8Array(samples.buffer);}
async function setup({greeting=false,deferredPlayback=false,language='ru'}={}){
 const dom=new JSDOM('<div id="voice"></div>');const root=dom.window.document.querySelector('#voice');
 const sent=[],mutes=[],plays=[],states=[];let capture,emit,finishPlayback,released=0,closed=0,interrupts=0;
 const playback=new Promise(resolve=>finishPlayback=resolve);
 const voice=mountVoice(root,{language,onState:value=>states.push(value),acquireMicrophone:async()=>()=>released++,
  openAudio:async fn=>{capture=fn;return{play:(id)=>plays.push(id),contextState:()=> 'running',
   finished:async()=>deferredPlayback?playback:true,interrupt:()=>{interrupts++;return 0;},
   interruptForTalk:()=>{interrupts++;return plays.length?{item_id:plays.at(-1),audio_end_ms:120}:null;},
   mute:value=>mutes.push(value),flush(){},stopNow(){},close:async()=>{closed++;}};},
  connect:async callback=>{emit=callback;await callback({type:'ready',greeting_pending:greeting});return{send:event=>sent.push(event),close:async()=>{}};},
  lifecycle:{visibilityState:'visible',addEventListener(){},removeEventListener(){}}});
 await voice.start();
 return{dom,root,voice,sent,mutes,plays,states,capture:(ms,voiced=false)=>{for(let n=0;n<ms;n+=20)capture(pcm(Math.min(20,ms-n),voiced));},
  emit:event=>emit(event),finishPlayback,counts:()=>({released,closed,interrupts}),destroy:async()=>{await voice.destroy();dom.window.close();}};
}
test('employee starts automatically; idle silence and brief noise do not create requests; exactly two seconds submits once',async()=>{
 const h=await setup();try{
  assert.deepEqual(h.sent,[{type:'talk_start'}]);assert.equal(h.root.querySelector('.voice-session-talk').hidden,true);
  h.capture(200,true);assert.equal(h.sent.length,1,'wait for server readiness');
  await h.emit({type:'talk_ready'});h.capture(5000);h.capture(80,true);h.capture(2000);
  assert.equal(h.sent.length,1,'idle silence and brief noise are not transmitted');
  h.capture(120,true);h.capture(1980);assert.equal(h.sent.filter(x=>x.type==='talk_end').length,0);
  h.capture(20);assert.equal(h.sent.filter(x=>x.type==='talk_end').length,1);
  assert.equal(h.sent.at(-1).type,'talk_end');assert.equal(h.mutes.at(-1),true);
  h.capture(3000,true);h.capture(3000);assert.equal(h.sent.filter(x=>x.type==='talk_end').length,1);
 }finally{await h.destroy();}
});
test('a shorter pause followed by speech resets the two-second window',async()=>{
 const h=await setup();try{await h.emit({type:'talk_ready'});h.capture(120,true);h.capture(1500);h.capture(100,true);h.capture(1980);
 assert.equal(h.sent.some(x=>x.type==='talk_end'),false);h.capture(20);assert.equal(h.sent.filter(x=>x.type==='talk_end').length,1);
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
test('explicit Stop drops late audio, thinking and playback receipts and resumes silent waiting',async()=>{
 const h=await setup({deferredPlayback:true});try{
  await h.emit({type:'talk_ready'});h.capture(120,true);h.capture(2000);await h.emit({type:'thinking'});
  await h.emit({type:'audio',item_id:'stopped',pcm:pcm(20)});await h.emit({type:'audio_done',item_id:'stopped'});
  const stop=h.root.querySelector('.voice-session-talk');assert.equal(stop.getAttribute('aria-label'),'Стоп');stop.click();stop.click();
  assert.deepEqual(h.sent.at(-1),{type:'stop_response',item_id:'stopped',audio_end_ms:120});
  assert.equal(h.sent.filter(x=>x.type==='stop_response').length,1);
  await h.emit({type:'thinking'});await h.emit({type:'audio',item_id:'stopped',pcm:pcm(20)});h.finishPlayback(true);await tick();
  assert.deepEqual(h.plays,['stopped']);assert.equal(h.sent.some(x=>x.type==='played_all'),false);
  h.capture(3000,true);assert.equal(h.mutes.at(-1),true);await h.emit({type:'talk_ready'});
  await h.emit({type:'thinking'});await h.emit({type:'audio',item_id:'stopped',pcm:pcm(20)});
  assert.equal(h.mutes.at(-1),false);assert.deepEqual(h.plays,['stopped']);h.capture(3000);
  assert.equal(h.sent.filter(x=>x.type==='talk_end').length,1);
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
  h.root.querySelector('.voice-session-talk').click();
  assert.deepEqual(h.sent.at(-1),{type:'stop_response'},'stale engine item cannot identify the response currently thinking');
 }finally{await h.destroy();}
});

test('continuous employee speech longer than 24 seconds is not forcibly submitted',async()=>{
 const h=await setup();try{
  await h.emit({type:'talk_ready'});h.capture(26000,true);
  assert.equal(h.sent.some(x=>x.type==='talk_end'),false,'only an actual silence ends speech');
  h.capture(1980);assert.equal(h.sent.some(x=>x.type==='talk_end'),false);
  h.capture(20);assert.equal(h.sent.filter(x=>x.type==='talk_end').length,1);
 }finally{await h.destroy();}
});
