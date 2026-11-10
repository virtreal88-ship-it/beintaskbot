const assert = require('node:assert/strict');
const test = require('node:test');
const openAudio = require('../docs/tenant-copilot-voice-audio.js');

test('sends microphone PCM in low-latency bounded chunks and flushes the final partial chunk', async () => {
  let stream, processor;
  const stopped = [];
  class FakeAudioContext {
    constructor(options) { this.sampleRate = options.sampleRate; this.state = 'running'; this.destination = {}; }
    async resume() {}
    createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
    createScriptProcessor(size) {
      assert.equal(size, 1024);
      processor = { connect() {}, disconnect() {}, onaudioprocess: null };
      return processor;
    }
    createGain() { return { gain: { value: 1 }, connect() {}, disconnect() {} }; }
    async close() { this.state = 'closed'; }
  }
  const deps = {
    AudioContext: FakeAudioContext,
    navigator: { mediaDevices: { getUserMedia: async () => {
      stream = { getTracks: () => [{ stop() { stopped.push(true); } }], getAudioTracks: () => [] };
      return stream;
    } } },
  };
  const frames = [];
  const audio = await openAudio(frame => frames.push(frame), deps);
  const pushSamples = (value,count=1024) => processor.onaudioprocess({
    inputBuffer: { getChannelData: () => new Float32Array(count).fill(value) },
  });

  pushSamples(0.25);
  assert.equal(frames.length, 1);
  assert.equal(frames[0].byteLength, 2048, 'each callback is sent promptly as a 43 ms chunk within the server frame limit');

  pushSamples(-0.25);
  assert.equal(frames.length, 2);
  assert.equal(frames[1].byteLength, 2048);
  pushSamples(-0.25,512);
  assert.equal(frames.length, 2);
  audio.flush();
  assert.equal(frames.length, 3);
  assert.equal(frames[2].byteLength, 1024, 'ending flushes the remaining microphone audio');
  await audio.close();
  assert.equal(stopped.length, 1, 'microphone tracks are released');
});

test('stale interrupt item stops current audio without throwing or ending the session', async () => {
  let processor, stops=0, trackStops=0;
  class FakeAudioContext {
    constructor(options){this.sampleRate=options.sampleRate;this.state='running';this.destination={};this.currentTime=1;}
    async resume(){}
    createMediaStreamSource(){return{connect(){},disconnect(){}};}
    createScriptProcessor(){processor={connect(){},disconnect(){},onaudioprocess:null};return processor;}
    createGain(){return{gain:{value:1},connect(){},disconnect(){}};}
    createBuffer(_channels,length){return{getChannelData:()=>new Float32Array(length)};}
    createBufferSource(){return{buffer:null,connect(){},start(){},stop(){stops++;},disconnect(){}};}
    async close(){this.state='closed';}
  }
  const deps={AudioContext:FakeAudioContext,navigator:{mediaDevices:{getUserMedia:async()=>({
    getTracks:()=>[{stop(){trackStops++;}}],getAudioTracks:()=>[],
  })}}};
  const audio=await openAudio(()=>{},deps);
  audio.play('current-item',new Uint8Array([0,0]));
  const finished=audio.finished('current-item');
  assert.equal(audio.interrupt('stale-server-item'),0);
  assert.equal(await finished,false,'interruption resolves pending playback as stopped');
  assert.equal(stops,1,'all scheduled local output is stopped even when item IDs raced');
  await audio.close();
  assert.equal(trackStops,1,'interrupt recovery does not leak the microphone');
});

test('talk interruption stops playback and reports only a conservative heard offset', async () => {
  let processor, context, stopped=0;
  class FakeAudioContext {
    constructor(options){this.sampleRate=options.sampleRate;this.state='running';this.destination={};this.currentTime=1;this.outputLatency=0.05;context=this;}
    async resume(){}
    createMediaStreamSource(){return{connect(){},disconnect(){}};}
    createScriptProcessor(){processor={connect(){},disconnect(){},onaudioprocess:null};return processor;}
    createGain(){return{gain:{value:1},connect(){},disconnect(){}};}
    createBuffer(_channels,length){return{getChannelData:()=>new Float32Array(length)};}
    createBufferSource(){return{buffer:null,connect(){},start(){},stop(){stopped++;},disconnect(){}};}
    async close(){this.state='closed';}
  }
  const deps={AudioContext:FakeAudioContext,navigator:{mediaDevices:{getUserMedia:async()=>({
    getTracks:()=>[{stop(){}}],getAudioTracks:()=>[],
  })}}};
  const audio=await openAudio(()=>{},deps);
  audio.play('assistant-item',new Uint8Array(48000));
  context.currentTime=1.5;
  const interrupted=audio.interruptForTalk();
  assert.equal(interrupted.item_id,'assistant-item');
  assert.ok(interrupted.audio_end_ms>=360&&interrupted.audio_end_ms<=380,
    'the transcript is truncated behind the browser output clock, not at the end of queued audio');
  assert.equal(stopped,1,'the assistant stops immediately when the user presses Talk');
  await audio.close();
});

test('local barge-in pauses queued assistant audio on sustained speech, not quiet or high-frequency noise', async () => {
  let processor, stopped=0, speechStarts=0;
  class FakeAudioContext {
    constructor(options){this.sampleRate=options.sampleRate;this.state='running';this.destination={};this.currentTime=1;}
    async resume(){}
    createMediaStreamSource(){return{connect(){},disconnect(){}};}
    createScriptProcessor(){processor={connect(){},disconnect(){},onaudioprocess:null};return processor;}
    createGain(){return{gain:{value:1},connect(){},disconnect(){}};}
    createBuffer(_channels,length){return{getChannelData:()=>new Float32Array(length)};}
    createBufferSource(){return{buffer:null,connect(){},start(){},stop(){stopped++;},disconnect(){}};}
    async close(){this.state='closed';}
  }
  const deps={AudioContext:FakeAudioContext,navigator:{mediaDevices:{getUserMedia:async()=>({
    getTracks:()=>[{stop(){}}],getAudioTracks:()=>[],
  })}}};
  const audio=await openAudio(()=>{},deps,()=>{speechStarts++;});
  audio.play('assistant-item',new Uint8Array([0,0,0,0]));
  const frame=samples=>processor.onaudioprocess({inputBuffer:{getChannelData:()=>samples}});
  const lowTone=Float32Array.from({length:1024},(_,i)=>0.015*Math.sin(2*Math.PI*220*i/24000));
  const hiss=Float32Array.from({length:1024},(_,i)=>i%2?0.12:-0.12);
  frame(lowTone);frame(lowTone);frame(hiss);frame(hiss);
  assert.equal(stopped,0,'quiet speech and high zero-crossing noise do not stop playback');
  const speech=Float32Array.from({length:1024},(_,i)=>0.12*Math.sin(2*Math.PI*220*i/24000));
  frame(speech);assert.equal(stopped,0,'one isolated loud frame is insufficient');
  frame(speech);
  assert.equal(stopped,1,'sustained speech stops already queued output locally');
  assert.equal(speechStarts,1,'the controller is notified once for the local interruption');
  assert.equal(audio.interrupt('assistant-item'),0,'the later server VAD interrupt remains safe and idempotent');
  await audio.close();
});

test('half-duplex output ignores speech and gently boosts quiet PCM without clipping', async()=>{
  let processor,buffer,stops=0,speech=0;
  class Ctx{
    constructor(){this.sampleRate=24000;this.state='running';this.destination={};this.currentTime=0;}
    async resume(){} async close(){this.state='closed';}
    createMediaStreamSource(){return{connect(){},disconnect(){}};}
    createScriptProcessor(){processor={connect(){},disconnect(){}};return processor;}
    createGain(){return{gain:{value:1},connect(){},disconnect(){}};}
    createBuffer(_,length){buffer=new Float32Array(length);return{getChannelData:()=>buffer};}
    createBufferSource(){return{connect(){},start(){},stop(){stops++;}};}
  }
  const audio=await openAudio(()=>{},{AudioContext:Ctx,navigator:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[],getAudioTracks:()=>[]})}}},()=>speech++,{pushToTalk:true});
  const samples=new Int16Array([3277,-3277,32767,-32768]);
  audio.play('answer',new Uint8Array(samples.buffer));
  assert.ok(buffer[0]>0.1&&buffer[0]<0.13);assert.ok(buffer[1]<-0.1);
  assert.ok([...buffer].every(x=>Math.abs(x)<1),'limited output never clips');
  const voice=Float32Array.from({length:1024},(_,i)=>Math.sin(i*0.2)*0.3);
  for(let i=0;i<10;i++)processor.onaudioprocess({inputBuffer:{getChannelData:()=>voice}});
  assert.equal(stops,0);assert.equal(speech,0,'speech never cancels half-duplex output');
  await audio.close();
});

test('duplex forwards quiet varying microphone samples across interruption without a local speech gate', async () => {
  let processor, speechStarts=0, stopped=0;
  class FakeAudioContext {
    constructor(options){this.sampleRate=options.sampleRate;this.state='running';this.destination={};this.currentTime=1;}
    async resume(){}
    createMediaStreamSource(){return{connect(){},disconnect(){}};}
    createScriptProcessor(){processor={connect(){},disconnect(){},onaudioprocess:null};return processor;}
    createGain(){return{gain:{value:1},connect(){},disconnect(){}};}
    createBuffer(_channels,length){return{getChannelData:()=>new Float32Array(length)};}
    createBufferSource(){return{connect(){},start(){},stop(){stopped++;}};}
    async close(){this.state='closed';}
  }
  const deps={AudioContext:FakeAudioContext,navigator:{mediaDevices:{getUserMedia:async()=>(
    {getTracks:()=>[{stop(){}}],getAudioTracks:()=>[]}
  )}}};
  const frames=[];
  const audio=await openAudio(frame=>frames.push(Uint8Array.from(frame)),deps,()=>{speechStarts++;});
  const feed=samples=>processor.onaudioprocess({inputBuffer:{getChannelData:()=>samples}});
  // Synthetic speech-like harmonics stay far below the 0.035 local mute threshold.
  const quiet=phase=>Float32Array.from({length:1024},(_,i)=>
    0.002*Math.sin((i+phase)*2*Math.PI*190/24000)+0.0007*Math.sin((i+phase)*2*Math.PI*460/24000));
  const loud=Float32Array.from({length:1024},(_,i)=>0.12*Math.sin(i*2*Math.PI*220/24000));
  const before=quiet(0),after=quiet(1024),next=quiet(2048);
  audio.play('assistant-item',new Uint8Array([0,0,0,0]));
  feed(before);
  assert.equal(speechStarts,0,'quiet samples do not trigger local barge-in');
  feed(loud);feed(loud);
  assert.equal(speechStarts,1);assert.equal(stopped,1,'sustained speech triggers local barge-in');
  feed(after); // leading samples while locally muted, before the provider interrupt
  audio.interrupt('assistant-item');
  feed(next); // first samples after the provider interrupt
  assert.equal(frames.length,5,'all input callbacks are forwarded during playback and interruption');
  for(const [index,samples] of [before,loud,loud,after,next].entries()){
    const expected=new Uint8Array(samples.length*2),view=new DataView(expected.buffer);
    for(let i=0;i<samples.length;i++){
      const sample=Math.max(-1,Math.min(1,samples[i]));
      view.setInt16(i*2,Math.trunc(sample<0?sample*32768:sample*32767),true);
    }
    assert.deepEqual(frames[index],expected,`frame ${index} keeps every quantized sample in order`);
  }
  await audio.close();
});
