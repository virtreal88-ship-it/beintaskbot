'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {transform, hash, BEFORE, AFTER} = require('./patch-direct-identity.cjs');
const PATCHED = 'f1ce1daf9d79fcdd5f897e997783bd49a0bb8ebbffaf0e3f1010cf7c026d9060';
const file = process.argv[2] || '/evolution/dist/main.js';
const source = fs.readFileSync(file, 'utf8');
const patched = hash(source) === PATCHED ? source : transform(source);
assert.equal(hash(patched), PATCHED);
assert.equal(patched.split(AFTER).length, 2);
assert.equal(patched.includes(BEFORE), false);
assert.throws(() => transform(patched), /Unrecognized/);
assert.throws(() => transform(source + '\nchanged'), /Unrecognized/);
// Execute the exact dispatch expression extracted from the actual image. No
// imports, sockets, credentials, sessions, provider calls or customer content.
const extracted = patched.slice(patched.indexOf(AFTER), patched.indexOf(AFTER) + AFTER.length);
function run(expression, input) {
  const c = structuredClone(input), emitted = [];
  const context = {c, console: {log() {}}, sink: {sendDataWebhook(event, payload) {emitted.push({event, payload});}}};
  vm.runInNewContext(`(function(){${expression};}).call(sink)`, context, {timeout: 1000});
  assert.equal(emitted.length, 1); assert.equal(emitted[0].event, 'messages.upsert');
  return {internal: c, output: emitted[0].payload};
}
const LID = '111111111111111@lid', PN = '15551234567@s.whatsapp.net';
function fixture(key) {return {key: {id: 'SYNTHETIC_MESSAGE', fromMe: false, ...key}, message: {conversation: 'synthetic'}, pushName: 'Synthetic', messageTimestamp: 1700000000};}
let cases = 0;
for (const fromMe of [false, true]) {
  const input = fixture({remoteJid: LID, remoteJidAlt: PN, addressingMode: 'lid', fromMe});
  const baseline = run(BEFORE, input), actual = run(extracted, input);
  assert.deepEqual(actual.internal, baseline.internal); // chatbot/cache path unchanged
  assert.equal(actual.internal.key.remoteJid, PN);
  assert.equal(actual.output.key.remoteJid, LID);
  assert.equal(actual.output.key.remoteJidAlt, PN);
  assert.equal(actual.output.key.fromMe, fromMe);
  assert.deepEqual(JSON.parse(JSON.stringify(actual.output)), input);
  actual.internal.key.remoteJid = 'late-change@s.whatsapp.net';
  assert.equal(actual.output.key.remoteJid, LID); // delayed webhook serialization
  cases++;
}
for (const key of [
  {remoteJid: PN}, {remoteJid: LID}, {remoteJid: PN, remoteJidAlt: LID},
  {remoteJid: '123456@g.us', remoteJidAlt: PN, participant: LID},
  {remoteJid: 'status@broadcast', remoteJidAlt: PN},
  {remoteJid: LID, remoteJidAlt: undefined}, {remoteJid: LID, remoteJidAlt: ''},
  {remoteJid: LID, remoteJidAlt: 'unverified'},
  {remoteJid: 'bad@lid', remoteJidAlt: PN},
  {remoteJid: LID, remoteJidAlt: '123456@g.us'},
  {remoteJid: LID, remoteJidAlt: '111111111111112@lid'},
  {remoteJid: '111111111111111:1@lid', remoteJidAlt: PN},
]) {
  const input = fixture(key), baseline = run(BEFORE, input), actual = run(extracted, input);
  assert.deepEqual(actual.internal, baseline.internal);
  assert.deepEqual(actual.output, baseline.output);
  cases++;
}
// Two channels/events have no shared identity state or cache.
const first = run(extracted, fixture({remoteJid: LID, remoteJidAlt: PN}));
const second = run(extracted, fixture({remoteJid: '222222222222222@lid', remoteJidAlt: '15559876543@s.whatsapp.net'}));
assert.equal(first.output.key.remoteJid, LID);
assert.equal(second.output.key.remoteJidAlt, '15559876543@s.whatsapp.net');
console.log(`PASS ${cases + 1} direct-identity cases on exact pinned entrypoint; patched SHA256 ${PATCHED}`);
