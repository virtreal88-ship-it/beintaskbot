const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, '../docs/tenant-crm-sync.js'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '../docs/tenant-app.html'), 'utf8');
new vm.Script(source);
assert(app.includes('/assets/tenant-crm-sync.js'));
assert(app.includes('tenant-crm-sync-complete'));
assert(app.includes('loadDeals(false)'));
assert(app.includes('loadTasks(false)'));

function setup() {
  const listeners = {}, events = [], boxes = [], timers = new Map();
  let next = 0, responses = [];
  const document = {
    hidden: false,
    getElementById() { return {after(box) { boxes.push(box); }}; },
    createElement() { return {textContent:'', classList:{toggle(){}}, setAttribute(){}}; },
    addEventListener(name, handler) { listeners[name] = handler; },
    dispatchEvent(event) { events.push(event); }
  };
  const requests = [];
  const context = {document, window:{}, CustomEvent:class { constructor(type, options) {this.type=type;this.detail=options.detail;} },
    fetch: async (url, options) => {requests.push({url, options}); const value=responses.shift();return await value;},
    setTimeout:fn=>{timers.set(++next,fn);return next;}, clearTimeout:id=>timers.delete(id)};
  vm.runInNewContext(source, context);
  const reply = jobs => ({ok:true,json:async()=>({success:true,jobs})});
  return {context, document, requests, listeners, events, boxes, timers, reply,
    push(value) {responses.push(value);},
    profile(tenant,role='worker') {listeners['tenant-profile']({detail:{member:{tenant_id:tenant,role},capabilities:{modules:{tasks:true}}}});}}
}
const flush = () => new Promise(resolve=>setImmediate(resolve));
(async()=>{
  const s = setup();
  s.push(s.reply([{resource:'tasks',status:'queued',pages_done:2}]));s.profile('company-a');await flush();
  assert(s.boxes.some(box=>box.textContent.includes('2 səhifə')));
  assert.equal(s.timers.size,1);
  const fn = [...s.timers.values()][0];s.timers.clear();
  s.push(s.reply([{resource:'tasks',status:'done',pages_done:3}]));await fn();await flush();
  assert.equal(s.timers.size,0);assert.equal(s.events.length,1);
  assert.equal(s.events[0].detail.tenant_id,'company-a');
  assert.equal(s.events[0].detail.resources[0],'tasks');
  assert(s.requests.every(r=>r.url==='/api/platform/crm/sync' && !r.options.method));
  assert(s.boxes.filter(box=>box.type==='button').every(button=>button.hidden));

  const admin = setup();admin.push(admin.reply([]));admin.profile('company-a','admin');await flush();
  const full = admin.boxes.find(box=>box.type==='button');assert(full&&!full.hidden);
  admin.push(admin.reply([]));admin.push(admin.reply([{resource:'tasks',status:'queued'}]));
  await full.onclick();await flush();
  assert(admin.requests.some(r=>r.url==='/api/platform/crm/tasks?refresh=1&full=1'));
  assert(!full.disabled);

  const race = setup();let release;
  race.push(new Promise(resolve=>{release=resolve;}));race.profile('old-company');
  race.push(race.reply([{resource:'tasks',status:'failed'}]));race.profile('new-company');await flush();
  release(race.reply([{resource:'tasks',status:'done'}]));await flush();
  assert.equal(race.events.length,0);assert(race.boxes.some(b=>b.textContent.includes('dayandı')));
  assert.equal(race.timers.size,0);

  const hidden = setup();hidden.document.hidden=true;hidden.profile('company-a');await flush();
  assert.equal(hidden.requests.length,0);
  hidden.document.hidden=false;hidden.push(hidden.reply([{resource:'tasks',status:'running'}]));
  hidden.listeners.visibilitychange();await flush();assert.equal(hidden.requests.length,1);
  hidden.document.hidden=true;hidden.listeners.visibilitychange();assert.equal(hidden.timers.size,0);
  console.log('PASS: sync progress is read-only, bounded, pauses in hidden tabs and ignores old tenant replies.');
})().catch(error=>{console.error(error);process.exitCode=1;});
