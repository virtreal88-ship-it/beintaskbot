/* Form behavior without a browser or a live CRM account. */
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');
const elements = [];
class Element {
  constructor(id = '') { this.id = id; this.value = ''; this.style = {}; this.disabled = false; this.scrollHeight = 120; this.label = {}; this.classList = {add(){}, remove(){}}; elements.push(this); }
  set innerHTML(value) {
    this.html = value;
    for (const match of value.matchAll(/id="([^"]+)"/g)) if (!elements.some(e => e.id === match[1])) new Element(match[1]);
    if (['taskExecutor','taskPipeline','taskLead'].includes(this.id)) this.value = (value.match(/<option value="([^"]*)"/) || [,''])[1];
  }
  get innerHTML() { return this.html; }
  before(element) { this.beforeElement = element; }
  append() {}
  closest() { return this.label; }
  reset() { elements.forEach(el => el.value = ''); }
  click() { this.clicked = true; if (this.onclick) return this.onclick(); }
}
const $ = id => elements.find(e => e.id === id);
new Element('tasksRefresh');
const document = {head:new Element(), body:new Element(), getElementById:$, createElement:() => new Element(), addEventListener(){}};
const session = new Map(), posted = [];
let fail = true;
async function fetch(url, options = {}) {
  let data;
  let status = 200;
  if (url.endsWith('/me')) data = {success:true,member:{tenant_id:'company-a',telegram_id:20},capabilities:{modules:{tasks:true}}};
  else if (url.endsWith('/options')) data = {success:true,executors:[{id:20,name:'Employee',role:'manager',pipelines:[{pipeline_id:10}]}],deals:[{id:7,name:'Client',pipeline_id:10}]};
  else {
    posted.push(JSON.parse(options.body));
    status = fail ? 500 : 200;
    data = fail ? {success:false,error:'Connection interrupted'} : {success:true,task_id:9};
  }
  return {ok:status === 200,status,json:async () => data};
}
const context = {document,fetch,alert:message => { throw Error(message); }, Date, JSON,
  crypto:{randomUUID:()=>'d4436c61-49a9-4ff2-8be6-86d7fba3d257'},
  sessionStorage:{getItem:key=>session.get(key)||null,setItem:(key,value)=>session.set(key,value),removeItem:key=>session.delete(key)}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../docs/tenant-tasks.js'),'utf8'), context);
(async () => {
  await new Promise(resolve => setImmediate(resolve));
  const create = $('tasksRefresh').beforeElement;
  assert.equal(create.hidden,false);
  await create.click();
  $('taskBody').value = 'Call client'; $('taskDue').value = '2030-01-01T10:00'; $('taskLead').value = '7';
  await $('tenantTaskForm').onsubmit({preventDefault(){}});
  assert.equal(posted.length,1); assert.equal(posted[0].executor_id,20);
  assert.equal(posted[0].pipeline_id,10); assert.equal(posted[0].lead_id,7);
  assert.equal($('taskBody').disabled,true); assert.equal(session.size,1);
  assert.equal($('taskSubmit').textContent,'Eyni sorğunu yoxla');
  fail = false;
  await $('tenantTaskForm').onsubmit({preventDefault(){}});
  assert.deepEqual(posted[0],posted[1]); assert.equal(session.size,0);
  assert.equal($('tasksRefresh').clicked,true);
  console.log('PASS: task form preserves the same request after network failure and refreshes only after success.');
})().catch(error => { console.error(error); process.exitCode = 1; });
