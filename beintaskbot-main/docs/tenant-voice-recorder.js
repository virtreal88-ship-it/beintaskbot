/* Isolated customer voice capture; this module has no network or send path. */
((root, make) => {
  if (typeof module === 'object' && module.exports) module.exports = make;
  else root.createTenantVoiceRecorder = make;
})(typeof window === 'object' ? window : globalThis, function createTenantVoiceRecorder(options = {}) {
  const mediaDevices = options.mediaDevices || globalThis.navigator?.mediaDevices;
  const Recorder = options.MediaRecorder || globalThis.MediaRecorder;
  const BlobClass = options.Blob || globalThis.Blob;
  const clock = options.now || (() => Date.now());
  const setTimer = options.setTimeout || globalThis.setTimeout;
  const clearTimer = options.clearTimeout || globalThis.clearTimeout;
  const notify = options.onChange || (() => {});
  const maxBytes = options.maxBytes || 5 * 1024 * 1024;
  const maxMs = options.maxMs || 120_000;
  const formats = ['audio/ogg;codecs=opus', 'audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'];
  let state = 'idle', error = '', stream = null, recorder = null, timer = null;
  let generation = 0, chunks = [], bytes = 0, blob = null, started = 0, durationMs = 0;

  const snapshot = () => ({ state, error, durationMs: state === 'recording' ? Math.min(maxMs, Math.max(0, clock() - started)) : durationMs, size: blob?.size || 0, mime: blob?.type || '' });
  const emit = () => notify(snapshot());
  const release = () => {
    if (timer !== null) clearTimer(timer);
    timer = null;
    if (stream) for (const track of stream.getTracks()) track.stop();
    stream = null;
  };
  const discard = () => { chunks = []; bytes = 0; blob = null; durationMs = 0; };
  const fail = code => { error = code; state = 'error'; discard(); release(); emit(); };
  function cancel() {
    if (state === 'disposed') return;
    generation++;
    const old = recorder;
    recorder = null;
    if (old && old.state !== 'inactive') { try { old.stop(); } catch {} }
    release(); discard(); error = ''; state = 'idle'; emit();
  }
  function stop() {
    if (state !== 'recording' || !recorder) return false;
    durationMs = Math.min(maxMs, Math.max(0, clock() - started));
    state = 'stopping'; emit();
    try { recorder.stop(); } catch { recorder = null; fail('recording_failed'); }
    return true;
  }
  async function start() {
    if (!['idle', 'ready', 'error'].includes(state)) return false;
    discard(); error = '';
    if (!mediaDevices?.getUserMedia || !Recorder || !BlobClass || typeof Recorder.isTypeSupported !== 'function') {
      fail('unsupported'); return false;
    }
    const mime = formats.find(format => Recorder.isTypeSupported(format));
    if (!mime) { fail('unsupported'); return false; }
    const current = ++generation;
    state = 'requesting'; emit();
    let acquired;
    try { acquired = await mediaDevices.getUserMedia({ audio: true, video: false }); }
    catch (cause) {
      if (generation === current) fail(cause?.name === 'NotAllowedError' || cause?.name === 'SecurityError' ? 'permission_denied' : 'microphone_unavailable');
      return false;
    }
    if (generation !== current || state !== 'requesting') {
      for (const track of acquired.getTracks()) track.stop();
      return false;
    }
    stream = acquired;
    try {
      recorder = new Recorder(stream, { mimeType: mime });
      const active = recorder;
      active.ondataavailable = event => {
        if (generation !== current || !['recording', 'stopping'].includes(state) || !event.data?.size) return;
        bytes += event.data.size;
        if (bytes > maxBytes) {
          generation++; recorder = null;
          try { if (active.state !== 'inactive') active.stop(); } catch {}
          fail('size_limit'); return;
        }
        chunks.push(event.data);
      };
      active.onerror = () => {
        if (generation !== current) return;
        generation++; recorder = null;
        try { if (active.state !== 'inactive') active.stop(); } catch {}
        fail('recording_failed');
      };
      active.onstop = () => {
        if (generation === current && state === 'recording') { recorder = null; fail('recording_failed'); return; }
        if (generation !== current || state !== 'stopping') return;
        recorder = null; release();
        blob = new BlobClass(chunks, { type: active.mimeType || mime }); chunks = [];
        if (!blob.size) { fail('empty_recording'); return; }
        state = 'ready'; emit();
      };
      active.start(1000);
      started = clock(); state = 'recording';
      timer = setTimer(() => { if (generation === current) stop(); }, maxMs);
      emit(); return true;
    } catch {
      recorder = null; fail('recording_failed'); return false;
    }
  }
  return { start, stop, cancel, snapshot, recording: () => state === 'ready' ? blob : null,
    dispose() { if (state === 'disposed') return; cancel(); state = 'disposed'; emit(); } };
});
