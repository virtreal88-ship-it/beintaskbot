const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),{webcrypto}=require('node:crypto');
const source=fs.readFileSync('docs/tenant-chat-send.js','utf8');
function fixture(){
  const storage=new Map(),calls=[],host={style:{},children:[{files:[],value:'',click(){}},{},{textContent:''},{hidden:true}]};
  const input={value:'caption',parentElement:{after(){}}},button={classList:{add(){},remove(){}}};
  const env={window:{},document:{getElementById:id=>id==='sendChat'?button:input,createElement:()=>host},
    crypto:{subtle:webcrypto.subtle,randomUUID:()=>{env.ids++;return 'request';}},ids:0,current:true,
    alert(){},sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    FormData:class{constructor(){this.fields=[];}append(k,v){this.fields.push([k,v]);}},
    fetch:async(url,options)=>{calls.push([url,options]);return{ok:true,json:async()=>({success:true,state:'accepted'})};}};
  vm.runInNewContext(source,env);
  env.window.tenantChatSend.bind({api:async()=>{},identity:'tenant:20',leadId:7,me:{tenant_id:'tenant',telegram_id:20},isCurrent:()=>env.current,reload:async()=>{}});
  const file={name:'a.png',size:12,type:'image/png',arrayBuffer:async()=>new Uint8Array([137,80,78,71,13,10,26,10,100,97,116,97]).buffer};
  host.children[0].files=[file];host.children[0].onchange();
  return {env,calls,storage,input,host,file,button,click:()=>host.children[3].onclick()};
}
(async()=>{
  let f=fixture();await f.click();assert.equal(f.calls.length,1);assert.equal(f.storage.size,0);
  assert.equal(f.calls[0][0],'/api/platform/crm/chat/send-file');assert.equal(f.calls[0][1].headers,undefined);
  const command=JSON.parse(f.calls[0][1].body.fields[0][1]);assert.equal(command.expected_tenant_id,'tenant');assert.equal(command.expected_user_id,20);
  f=fixture();f.env.fetch=async()=>{throw new Error('network');};await f.click();assert.equal(f.storage.size,1);
  f.env.fetch=async(url,options)=>{f.calls.push(options);return{ok:true,json:async()=>({success:true})};};
  f.input.value='different';await f.click();assert.equal(f.calls.length,0);assert.equal(f.env.ids,1);
  f.input.value='caption';await f.click();assert.equal(f.calls.length,1);assert.equal(f.env.ids,1);assert.equal(f.storage.size,0);
  f=fixture();let resolve;f.env.fetch=()=>new Promise(r=>resolve=r);const first=f.click();
  while(!resolve)await new Promise(r=>setImmediate(r));await f.click();assert.equal(f.env.ids,1);
  resolve({ok:true,json:async()=>({success:true})});await first;
  f=fixture();f.env.current=false;await f.click();assert.equal(f.calls.length,0);
  f=fixture();f.env.crypto.subtle={digest:async()=>{f.env.current=false;return new ArrayBuffer(32);}};
  await f.click();assert.equal(f.calls.length,0);assert.equal(f.storage.size,0);
  f=fixture();f.button.disabled=true;await f.click();assert.equal(f.calls.length,0);
  console.log('PASS: file UUID recovery, caption edit blocking, multipart identity, no double send or stale-company write.');
})().catch(e=>{console.error(e);process.exitCode=1;});
