const assert = require('node:assert/strict');
const connectVoice = require('../docs/tenant-copilot-voice-channel.js');

class Socket {
  static OPEN = 1;
  constructor() { this.readyState = Socket.OPEN; Socket.instance = this; }
  close() { this.readyState = 3; }
}

(async () => {
  const timers = [];
  const env = {
    location: { href: 'https://crm.pro.az/app', origin: 'https://crm.pro.az' },
    WebSocket: Socket,
    setTimeout(_callback, milliseconds) { timers.push(milliseconds); return 1; },
    clearTimeout() {},
  };
  const pending = connectVoice('/api/copilot-voice/ws', () => {}, env);
  assert.equal(timers[0], 15000, 'client must allow the bounded server-side Realtime startup');
  Socket.instance.onopen();
  Socket.instance.onmessage({ data: JSON.stringify({ type: 'ready' }) });
  const channel = await pending;
  assert.equal(typeof channel.send, 'function');
  await channel.close();

  const refused = connectVoice('/api/copilot-voice/ws', () => {}, env);
  Socket.instance.onopen();
  Socket.instance.onmessage({data: JSON.stringify({type:'error',code:'voice_session_busy'})});
  await assert.rejects(refused, {message:'voice_session_busy'});

  const other = connectVoice('/api/copilot-voice/ws', () => {}, env);
  Socket.instance.onopen();
  Socket.instance.onmessage({data: JSON.stringify({type:'error',code:'private_detail'})});
  await assert.rejects(other, {message:'voice_not_ready'});
})().catch(error => { console.error(error); process.exitCode = 1; });
