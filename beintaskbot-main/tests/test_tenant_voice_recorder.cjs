const { test } = require('node:test');
const assert = require('node:assert/strict');
const create = require('../docs/tenant-voice-recorder.js');

function fixture({ permission, supported = true, maxBytes = 64, maxMs = 5000 } = {}) {
  const track = { stopped: 0, stop() { this.stopped++; } };
  const stream = { getTracks: () => [track] };
  let instance, timeout, time = 100;
  class FakeRecorder {
    static isTypeSupported(type) { return supported && type === 'audio/webm;codecs=opus'; }
    constructor(_stream, options) { instance = this; this.mimeType = options.mimeType; this.state = 'inactive'; }
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; }
    data(value) { this.ondataavailable({ data: new Blob([value]) }); }
    ended() { this.state = 'inactive'; this.onstop(); }
  }
  const changes = [];
  const recorder = create({ mediaDevices: { getUserMedia: permission || (async () => stream) }, MediaRecorder: FakeRecorder,
    maxBytes, maxMs, now: () => time, setTimeout: fn => { timeout = fn; return 1; }, clearTimeout: () => { timeout = null; },
    onChange: value => changes.push(value) });
  return { recorder, track, stream, changes, instance: () => instance, tick(ms) { time += ms; }, timeout: () => timeout?.() };
}

test('explicit stop makes a preview available and releases the microphone', async () => {
  const f = fixture();
  assert.equal(await f.recorder.start(), true);
  assert.equal(f.recorder.recording(), null);
  f.instance().data('voice'); f.tick(1200);
  assert.equal(f.recorder.stop(), true);
  assert.equal(f.recorder.recording(), null);
  f.instance().onstop();
  assert.equal(await f.recorder.recording().text(), 'voice');
  assert.equal(f.recorder.snapshot().durationMs, 1200);
  assert.equal(f.track.stopped, 1);
  assert.equal(f.recorder.snapshot().state, 'ready');
  f.recorder.cancel();
  assert.equal(f.recorder.recording(), null);
});

test('cancelling while permission is pending stops a late stream without starting capture', async () => {
  let grant;
  const f = fixture({ permission: () => new Promise(resolve => { grant = resolve; }) });
  const pending = f.recorder.start();
  f.recorder.cancel(); grant(f.stream);
  assert.equal(await pending, false);
  assert.equal(f.track.stopped, 1);
  assert.equal(f.instance(), undefined);
  assert.equal(f.recorder.snapshot().state, 'idle');
});

test('cancel during recording ignores late events and discards data', async () => {
  const f = fixture(); await f.recorder.start();
  const previous = f.instance(); previous.data('private'); f.recorder.cancel();
  previous.ondataavailable({ data: new Blob(['late']) }); previous.onstop();
  assert.equal(f.recorder.recording(), null);
  assert.equal(f.recorder.snapshot().state, 'idle');
  assert.equal(f.track.stopped, 1);
});

test('recording limits stop the capture but never send it', async () => {
  const f = fixture({ maxMs: 500 }); await f.recorder.start();
  f.instance().data('voice'); f.tick(800); f.timeout();
  assert.equal(f.recorder.snapshot().state, 'stopping');
  f.instance().onstop();
  assert.equal(f.recorder.snapshot().durationMs, 500);
  assert.equal(f.recorder.snapshot().state, 'ready');
  const large = fixture({ maxBytes: 3 }); await large.recorder.start();
  large.instance().data('oversized');
  assert.equal(large.recorder.snapshot().error, 'size_limit');
  assert.equal(large.recorder.recording(), null);
  assert.equal(large.track.stopped, 1);
});

test('permissions, unsupported browsers and unexpected microphone loss fail closed', async () => {
  const denied = fixture({ permission: async () => { throw Object.assign(new Error(), { name: 'NotAllowedError' }); } });
  assert.equal(await denied.recorder.start(), false);
  assert.equal(denied.recorder.snapshot().error, 'permission_denied');
  const unsupported = fixture({ supported: false });
  assert.equal(await unsupported.recorder.start(), false);
  assert.equal(unsupported.recorder.snapshot().error, 'unsupported');
  const lost = fixture(); await lost.recorder.start(); lost.instance().ended();
  assert.equal(lost.recorder.snapshot().error, 'recording_failed');
  assert.equal(lost.track.stopped, 1);
});
