const assert = require('node:assert/strict');
const test = require('node:test');
const openAudio = require('../docs/tenant-copilot-voice-audio.js');

test('batches microphone PCM into bounded live chunks and flushes the final partial chunk', async () => {
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
  const pushSamples = value => processor.onaudioprocess({
    inputBuffer: { getChannelData: () => new Float32Array(1024).fill(value) },
  });

  for (let i = 0; i < 3; i++) pushSamples(0.25);
  assert.equal(frames.length, 0, 'small callbacks are grouped to reduce server round trips');
  pushSamples(0.25);
  assert.equal(frames.length, 1);
  assert.equal(frames[0].byteLength, 8192, 'four callbacks form a 171 ms chunk within the server frame limit');

  for (let i = 0; i < 3; i++) pushSamples(-0.25);
  assert.equal(frames.length, 1);
  audio.flush();
  assert.equal(frames.length, 2);
  assert.equal(frames[1].byteLength, 6144, 'ending flushes the remaining microphone audio');
  await audio.close();
  assert.equal(stopped.length, 1, 'microphone tracks are released');
});
