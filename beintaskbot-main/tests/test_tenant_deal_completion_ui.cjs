const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../docs/tenant-deal-completion.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
class Element{
  constructor(){this.children=[];this.hidden=false;this.disabled=false;this.style={};this.textContent='';}
  append(...values){this.children.push(...values);}prepend(value){this.children.unshift(value);}replaceChildren(){this.children=[];}
  set innerHTML(value){this.html=value;this.parts={'.deal-review-list':new Element(),'.notice-text':new Element(),'.more':new Element(),'.refresh':new Element()};}
  querySelector(s){return this.parts[s];}querySelectorAll(){return this.children.filter(c=>c.onclick);}
}
(async()=>{
  const host=new Element(),modal=new Element(),listeners={},requests=[],storage=new Map();let confirmations=0,refreshes=0,uuids=0;
  const profile={tenant_id:'company-a',telegram_id:20,role:'manager'},state={failure:false};
  const window={};
  const document={createElement:()=>new Element(),getElementById:id=>id==='systemApprovalsHost'?host:id==='modalContent'?modal:{click:()=>refreshes++},addEventListener:(name,fn)=>listeners[name]=fn};
  const fetch=async(url,options={})=>{
    if(url==='/api/platform/me')return{json:async()=>({success:true,member:profile,capabilities:{modules:{deals:true}}})};
    requests.push([url,options]);if(state.network)throw Error('Network failed');if(state.malformed)return {ok:true,status:200,json:async()=>{throw SyntaxError('Malformed JSON');}};if(state.incomplete)return {ok:true,status:200,json:async()=>({success:true})};return{ok:!state.failure,status:state.failure?409:200,json:async()=>state.failure?{success:false,error:'unknown',...(state.keep===undefined?{}:{keep_request:state.keep})}:{success:true,...(state.pending?{approval_pending:true}:{completed:true})}};
  };
  vm.runInNewContext(source,{window,document,fetch,sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    crypto:{randomUUID:()=> 'request-uuid-'+(++uuids)},confirm:()=>{confirmations++;return true;},alert:()=>{}});
  await tick();assert(host.children[0].hidden);
  assert.equal(listeners['tenant-chat-open'],undefined,'No injected completion button');
  const request=()=>window.tenantDealCompletion.request(7,{tenant_id:'company-a',user_id:20});
  assert.equal(requests.length,0,'Never automatic completion from opening deal');
  state.failure=true;await assert.rejects(request);assert.equal(confirmations,1);assert.equal(storage.size,1);
  state.failure=false;await request();assert.equal(confirmations,1,'Original request is reused');assert.equal(storage.size,0);
  const payloads=requests.map(r=>JSON.parse(r[1].body));assert.equal(payloads[0].request_id,payloads[1].request_id);
  assert.equal(payloads[0].expected_tenant_id,'company-a');assert.equal(payloads[0].expected_user_id,20);assert.equal(refreshes,1);
  state.pending=true;await request();assert.equal(storage.size,1,'retain UUID while awaiting approval');
  const confirmationCount=confirmations;await request();assert.equal(confirmations,confirmationCount,'pending approval reuses request without duplicate confirmation');
  const pendingPayloads=requests.slice(-2).map(r=>JSON.parse(r[1].body));assert.equal(pendingPayloads[0].request_id,pendingPayloads[1].request_id);
  const requestCount=requests.length;await assert.rejects(()=>window.tenantDealCompletion.request(7,{tenant_id:'other',user_id:20}));assert.equal(requests.length,requestCount,'identity change cannot send command');
  state.pending=false;await request();assert.equal(storage.size,0);
  for(const mode of ['network','malformed','incomplete','failure']){
    state[mode]=true;await assert.rejects(request);assert.equal(storage.size,1,mode+' retains uncertain command');
    const original=JSON.parse(requests.at(-1)[1].body).request_id,confirmed=confirmations;
    state[mode]=false;await request();assert.equal(JSON.parse(requests.at(-1)[1].body).request_id,original,mode+' retry uses the same UUID');assert.equal(confirmations,confirmed);assert.equal(storage.size,0);
  }
  state.failure=true;state.keep=false;await assert.rejects(request);assert.equal(storage.size,0,'explicit definitive refusal clears command');
  const refused=JSON.parse(requests.at(-1)[1].body).request_id;state.failure=false;await request();assert.notEqual(JSON.parse(requests.at(-1)[1].body).request_id,refused,'only definitive refusal permits a new UUID');
  assert(!source.includes('data-task-complete'),'No connection to task completion');
  console.log('PASS: explicit stage completion, confirmation, saved UUID, identity binding and no automatic task-stage coupling.');
})().catch(e=>{console.error(e);process.exitCode=1;});
