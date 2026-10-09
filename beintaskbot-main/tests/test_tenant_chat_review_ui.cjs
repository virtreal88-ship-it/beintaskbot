const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('docs/tenant-chat-review.js','utf8');
function fixture(role='owner') {
  const nodes=new Map(),events={},calls=[],storage=new Map();
  class Element {
    constructor(tag){this.tag=tag;this.children=[];this.isConnected=true;this.value='';this.disabled=false;}
    set id(v){this._id=v;nodes.set(v,this);}get id(){return this._id;}
    append(...items){this.children.push(...items);}after(item){this.children.push(item);}
    remove(){this.isConnected=false;nodes.delete(this.id);}
    replaceChildren(){this.children=[];}setAttribute(){}
    querySelector(tag){return this.children.find(x=>x.tag===tag);}
  }
  const input=new Element('textarea');input.id='chatText';input.parentElement=new Element('div');
  const send=new Element('button');send.id='sendChat';
  const member={role,tenant_id:'tenant',telegram_id:20};
  const receipt={actor_id:20,request_id:'original',state:'unknown',created_at:'today',updated_at:'version'};
  let response=()=>({receipts:[receipt]});
  const env={document:{getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag),addEventListener:(name,fn)=>events[name]=fn},
    crypto:{randomUUID:()=> 'review'},sessionStorage:{getItem:k=>storage.get(k)||null,removeItem:k=>storage.delete(k)},
    fetch:async(url,opt)=>{calls.push({url,opt});if(url==='/api/platform/me')return {ok:true,json:async()=>({member})};
      return {ok:true,json:async()=>({success:true,...await response(url,opt)})};}};
  vm.runInNewContext(source,env);
  return {nodes,events,calls,member,storage,input,receipt,setResponse:r=>response=r};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  let f=fixture();await flush();f.events['tenant-chat-open']({detail:{lead_id:7}});
  assert.equal(f.calls.length,1); // Opening the UI never sends or resolves anything.
  let box=f.nodes.get('chatSendReview'),refresh=box.children[0];await refresh.onclick();
  let row=box.children[1].children[0];row.children[1].onclick();
  const form=row.querySelector('form'),choice=form.children[1],reason=form.children[2];
  choice.value='not_sent';reason.value='Checked in Kommo';
  f.storage.set('tenant-chat-send:tenant:20',JSON.stringify({request_id:'original'})); // Different key must survive.
  f.storage.set('tenant-chat-send:tenant:20:7',JSON.stringify({request_id:'original',text:'draft'}));
  let attempts=[];f.setResponse((url,opt)=>{attempts.push(JSON.parse(opt.body));if(attempts.length===1)throw Error('network lost');return {};});
  await form.onsubmit({preventDefault(){}});assert.equal(f.storage.size,2);
  reason.value='changed after timeout';await form.onsubmit({preventDefault(){}});
  assert.deepEqual(attempts[0],attempts[1]);assert.equal(f.storage.size,1);
  assert.ok(f.calls.every(c=>!c.url.endsWith('/chat/send')));
  f=fixture('worker');await flush();f.events['tenant-chat-open']({detail:{lead_id:7}});assert.ok(!f.nodes.has('chatSendReview'));
  f=fixture();await flush();f.events['tenant-chat-open']({detail:{lead_id:7}});box=f.nodes.get('chatSendReview');
  let resolve;f.setResponse(()=>new Promise(r=>resolve=r));const waiting=box.children[0].onclick();await flush();
  f.events['tenant-chat-open']({detail:{lead_id:8}});resolve({receipts:[f.receipt]});await waiting;
  assert.equal(f.nodes.get('chatSendReview').children[1].children.length,0);
  console.log('PASS: manual-only resolution, exact retry, sender storage scoped, employees hidden, stale results ignored.');
})().catch(e=>{console.error(e);process.exitCode=1;});
