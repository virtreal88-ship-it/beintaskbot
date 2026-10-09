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
  const host=new Element(),modal=new Element(),listeners={},requests=[],storage=new Map();let confirmations=0,refreshes=0;
  const profile={tenant_id:'company-a',telegram_id:20,role:'manager'},state={failure:false};
  const document={createElement:()=>new Element(),getElementById:id=>id==='systemApprovalsHost'?host:id==='modalContent'?modal:{click:()=>refreshes++},addEventListener:(name,fn)=>listeners[name]=fn};
  const fetch=async(url,options={})=>{
    if(url==='/api/platform/me')return{json:async()=>({success:true,member:profile,capabilities:{modules:{deals:true}}})};
    requests.push([url,options]);return{ok:!state.failure,status:state.failure?409:200,json:async()=>state.failure?{success:false,error:'unknown',keep_request:true}:{success:true,completed:true}};
  };
  vm.runInNewContext(source,{document,fetch,sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    crypto:{randomUUID:()=> 'request-uuid'},confirm:()=>{confirmations++;return true;},alert:()=>{}});
  await tick();assert(host.children[0].hidden);
  listeners['tenant-chat-open']({detail:{lead_id:7}});const button=modal.children[0];assert.equal(button.textContent,'Sövdələşməni tamamla');
  assert.equal(requests.length,0,'Never automatic completion from opening deal');
  state.failure=true;await button.onclick();assert.equal(confirmations,1);assert.equal(storage.size,1);
  state.failure=false;await button.onclick();assert.equal(confirmations,1,'Original request is reused');assert.equal(storage.size,0);
  const payloads=requests.map(r=>JSON.parse(r[1].body));assert.equal(payloads[0].request_id,payloads[1].request_id);
  assert.equal(payloads[0].expected_tenant_id,'company-a');assert.equal(payloads[0].expected_user_id,20);assert.equal(refreshes,1);
  assert(!source.includes('data-task-complete'),'No connection to task completion');
  console.log('PASS: explicit deal button, confirmation, saved UUID, identity binding and no automatic task-stage coupling.');
})().catch(e=>{console.error(e);process.exitCode=1;});
