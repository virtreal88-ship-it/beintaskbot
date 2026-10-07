/* Mobile form, durable retries and tenant switches without real provider writes. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const elements=[],listeners={},session=new Map(),posted=[],alerts=[];
class Element {
  constructor(id=''){this.id=id;this.style={};this.dataset={};this.value='';this.scrollHeight=150;this.classes=new Set();this.classList={add:c=>this.classes.add(c),remove:c=>this.classes.delete(c)};elements.push(this);}
  set innerHTML(value){this.html=value;for(const m of value.matchAll(/id="([^"]+)"/g))if(!$(m[1]))new Element(m[1]);}
  append(){}
  click(){this.clicked=true;}
}
const $=id=>elements.find(e=>e.id===id);
new Element('tasksRefresh');
const document={body:new Element(),getElementById:$,createElement:()=>new Element(),addEventListener:(type,fn)=>listeners[type]=fn};
let status=500,resolvePost=null,hold=false;
async function fetch(url,options={}) {
  if(url.endsWith('/me'))return {json:async()=>({success:true,member:{tenant_id:'company-a',telegram_id:20},capabilities:{modules:{tasks:true}}})};
  posted.push(JSON.parse(options.body));
  if(hold)return new Promise(resolve=>{resolvePost=resolve;});
  return {ok:status===200,status,json:async()=>status===200?{success:true,completed:true}:{success:false,error:'Connection interrupted',retry_same_request:status===409}};
}
const context={document,fetch,alert:msg=>alerts.push(msg),crypto:{randomUUID:()=> 'd4436c61-49a9-4ff2-8be6-86d7fba3d257'},
  sessionStorage:{getItem:key=>session.get(key)||null,setItem:(key,value)=>session.set(key,value),removeItem:key=>session.delete(key)}};
const source=fs.readFileSync(path.join(__dirname,'../docs/tenant-task-completion.js'),'utf8');
vm.runInNewContext(source,context);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const open=()=>listeners.click({target:{closest:()=>({dataset:{taskComplete:'9'}})}});
const submit=()=>$('completeForm').onsubmit({preventDefault(){}});
(async()=>{
  await tick();open();$('completeText').value='Reached client';$('completeText').oninput();
  assert.equal($('completeText').style.height,'150px');
  await submit();assert.equal(session.size,1);assert.equal($('completeText').disabled,true);
  assert.equal($('completeSubmit').textContent,'Eyni sorğunu yoxla');
  $('completeCancel').onclick();open();assert.equal($('completeText').value,'Reached client');
  status=409;await submit();assert.deepEqual(posted[0],posted[1]);
  status=200;await submit();assert.deepEqual(posted[0],posted[2]);
  assert.equal(session.size,0);assert.equal($('tasksRefresh').clicked,true);
  // Validation error unlocks the text; unknown provider writes do not.
  open();$('completeText').value='Result';status=400;await submit();
  assert.equal($('completeText').disabled,false);assert.equal(session.size,0);
  // A late completion response must not refresh or leak a result into a new tenant.
  $('tasksRefresh').clicked=false;hold=true;const pending=submit();
  assert.equal($('completeSubmit').disabled,true);const count=posted.length;await submit();assert.equal(posted.length,count);
  listeners['tenant-profile']({detail:{member:{tenant_id:'company-b',telegram_id:20},capabilities:{modules:{tasks:true}}}});
  assert.equal($('tenantCompleteModal').classes.has('show'),false);
  resolvePost({ok:true,status:200,json:async()=>({success:true,completed:true})});await pending;
  assert.equal($('tasksRefresh').clicked,false);
  assert(source.includes('env(safe-area-inset-bottom)'));assert(source.includes('position:sticky'));
  console.log('PASS: completion keeps original UUID on response loss, locks buttons, handles validation and tenant switches.');
})().catch(error=>{console.error(error);process.exitCode=1;});
