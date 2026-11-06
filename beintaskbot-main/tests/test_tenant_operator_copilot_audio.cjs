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
