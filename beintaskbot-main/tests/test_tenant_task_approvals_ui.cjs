/* Queue UX without a browser, Telegram, or real provider writes. */
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
class Element {
  constructor(tag='div') { this.tag=tag; this.children=[]; this.style={}; this.hidden=false; }
  append(...items) { this.children.push(...items); }
  replaceChildren() { this.children=[]; }
  set innerHTML(value) {
    this.html=value;
    if (value.includes('approval-list')) this.parts={button:new Element('button'),'.approval-notice':new Element(),'.approval-list':new Element(),'.approval-more':new Element('button')};
  }
  querySelector(key) { return this.parts[key]; }
  querySelectorAll(tag) { return this.children.filter(e=>e.tag===tag); }
}
const host=new Element(), refresh=new Element('button'), listeners={}, calls=[];
refresh.click=()=>{refresh.clicked=true;};
const document={getElementById:id=>id==='view-tasks'?host:refresh,createElement:tag=>new Element(tag),
  addEventListener:(type,callback)=>{listeners[type]=callback;}};
let approvals=[{creator_id:20,request_id:'request',creator_name:'Employee',executor_name:'Worker',
  step:'waiting_approval',task:{text:'<script>unsafe</script>',executor_id:20,due_at:'2030-01-01T12:00:00Z'}}];
let moreError=true;
async function fetch(url, options={}) {
  calls.push([url,options]);
  if (url.endsWith('/me')) return {json:async()=>({success:true,member:{role:'manager'},capabilities:{modules:{tasks:true}}})};
  if (options.method==='POST') { approvals=[];return {ok:true,json:async()=>({success:true,task_id:9})}; }
  if (url.includes('&cursor=')) {
    if (moreError) return {ok:false,json:async()=>({success:false,error:'Page failed'})};
    return {ok:true,json:async()=>({success:true,approvals:[...approvals,{...approvals[0],request_id:'second'}],has_more:false,next_cursor:null})};
  }
  return {ok:true,json:async()=>({success:true,approvals,has_more:!!approvals.length,next_cursor:approvals.length?'cursor+/=':null})};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../docs/tenant-task-approvals.js'),'utf8'),{document,fetch,Date});
(async()=>{
  await tick(); const panel=host.children[0];
  assert.equal(panel.hidden,true);assert.equal(calls.length,1);
  listeners['tenant-profile']({detail:{member:{role:'owner'},capabilities:{modules:{tasks:true}}}});
  await tick();assert.equal(panel.hidden,false);
  const list=panel.querySelector('.approval-list'), card=list.children[0];
  assert.ok(card.html.includes('&lt;script&gt;unsafe&lt;/script&gt;'));
  const more=panel.querySelector('.approval-more');
  assert.equal(more.hidden,false);
  const failed=more.onclick();
  assert.equal(more.disabled,true);
  await failed;
  assert.equal(list.children.length,1);assert.equal(more.hidden,false);assert.equal(more.disabled,false);
  assert.equal(panel.querySelector('.approval-notice').textContent,'Page failed');
  moreError=false;
  const next=more.onclick(); const count=calls.length;
  await more.onclick();assert.equal(calls.length,count,'No duplicate load-more while busy');
  await next;
  assert.equal(list.children.length,2,'Appending must deduplicate existing requests');
  assert.equal(more.hidden,true);
  assert.ok(calls.some(([url])=>url.includes('cursor=cursor%2B%2F%3D')));
  const actions=card.children[0];
  const pending=actions.children[0].onclick();
  assert.ok(actions.children.every(b=>b.disabled)); await pending;
  const posted=JSON.parse(calls.find(c=>c[1].method==='POST')[1].body);
  assert.deepEqual(posted,{creator_id:20,request_id:'request',action:'approve'});
  assert.equal(refresh.clicked,true);assert.equal(list.children.length,0);
  listeners['tenant-profile']({detail:{member:{role:'worker'},capabilities:{modules:{tasks:true}}}});
  assert.equal(panel.hidden,true);assert.equal(list.children.length,0);
  assert.equal(more.hidden,true);
  console.log('PASS: review queue is admin-only, escapes task text, locks buttons and reloads durable state.');
})().catch(error=>{console.error(error);process.exitCode=1;});
