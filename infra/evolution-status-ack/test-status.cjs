'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { transform, hash, EXPECTED } = require('./patch-status.cjs');
const source = fs.readFileSync(process.argv[2] || '/evolution/node_modules/baileys/lib/Socket/messages-recv.js', 'utf8');
const patched = hash(source) === EXPECTED ? transform(source) : source;
const slice = (start, end) => patched.slice(patched.indexOf(start), patched.indexOf(end));
assert(patched.includes("['status', handleStatus]"));
const ack = slice('    const sendMessageAck =', '    const rejectCall =');
const handlers = slice('    /// processes a node', "    ev.on('call',");
const listeners = new Map(), sent = [], errors = [], events = [];
let fail = false;
const ctx = {
  ws: {isOpen:true, on: (name, fn) => listeners.set(name, fn)},
  ev: {buffer(){},flush(){},emit(){throw Error('Status must not produce customer events');}},
  logger: {debug(){}},
  sendNode: async n => { if(fail) {fail=false;throw Error('synthetic send failure');} sent.push(n); },
  onUnexpectedError: (e,id) => errors.push([e.message,id]),
  handleMessage: async n => events.push(n.attrs.id),
  handleCall: async()=>{}, handleReceipt:async()=>{}, handleNotification:async()=>{}, handleBadAck:async()=>{},
};
vm.runInNewContext(ack + handlers,ctx);
const node = (id, extra={}) => ({tag:'status', attrs:{id,from:'fixture@s.whatsapp.net', type:'media',...extra}});
const tick = () => new Promise(r=>setImmediate(r));
(async()=>{
  await listeners.get('CB:status')(node('live',{participant:'participant', recipient:'recipient'}));
  assert.deepEqual(JSON.parse(JSON.stringify(sent.shift())), {tag:'ack',attrs:{id:'live',to:'fixture@s.whatsapp.net',class:'status',participant:'participant',recipient:'recipient',type:'media'}});
  await listeners.get('CB:status')(node('text',{type:'text'}));
  assert.equal(sent.shift().attrs.type,'text');
  await listeners.get('CB:status')(node('no-type',{type:undefined}));
  assert.equal(Object.hasOwn(sent.shift().attrs,'type'),false);
  fail=true;
  await listeners.get('CB:status')(node('offline-fail',{offline:'true'}));
  await listeners.get('CB:message')({tag:'message',attrs:{id:'existing-queue-fixture',offline:'true'}});
  await listeners.get('CB:status')(node('offline-next',{offline:'true'}));
  await tick();await tick();
  assert.equal(errors.length,1);assert.equal(errors[0][1],'acking status stanza');
  assert.deepEqual(events,['existing-queue-fixture']);
  assert.equal(sent.shift().attrs.id,'offline-next');
  fail=true;await listeners.get('CB:status')(node('live-fail'));assert.equal(errors.length,2);
  await listeners.get('CB:status')(node('live-after-failure'));assert.equal(sent.shift().attrs.id,'live-after-failure');
  assert.equal(sent.length,0);
  assert.throws(()=>transform(patched),/Unrecognized/);
  assert.throws(()=>transform(source+'\n'),/Unrecognized/);
  console.log('PASS: live media/text/minimal ACK; offline ACK failure continues queue; live failure recovery; no status customer event; duplicate/tampered source rejected');
})().catch(e=>{console.error(e);process.exitCode=1});
