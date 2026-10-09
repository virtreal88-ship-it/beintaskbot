const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('docs/tenant-chat-history.js','utf8');
function fixture(){
  const nodes=new Map(),events={},calls=[];let response=async()=>({messages:[],next_cursor:null}),status=200;
  class Element {
    constructor(){this.children=[];this.isConnected=true;this.html='';}
    set id(value){this._id=value;nodes.set(value,this);}get id(){return this._id;}
    append(...items){this.children.push(...items);}before(item){this.beforeNode=item;}
    remove(){nodes.delete(this.id);this.isConnected=false;}
    setAttribute(){}insertAdjacentHTML(where,text){this.html=text+this.html;}
  }
  const rows=new Element();rows.id='chatRows';
  const member={tenant_id:'tenant',telegram_id:20};
  const env={URLSearchParams,Set,CustomEvent:class{constructor(name,options){this.type=name;this.detail=options.detail;}},
    document:{getElementById:id=>nodes.get(id),createElement:()=>new Element(),addEventListener:(name,fn)=>events[name]=fn,
      dispatchEvent:event=>events[event.type]?.(event)},
    fetch:async(url,opt)=>{calls.push({url,opt});if(url==='/api/platform/me')return {ok:true,json:async()=>({member})};return {ok:status<400,status,json:()=>response()};}};
  vm.runInNewContext(source,env);
  function open(id=7){events['tenant-chat-open']({detail:{lead_id:id}});events['tenant-chat-loaded']({detail:{lead_id:id,messages:[{external_id:'new'}],next_cursor:'cursor-1',render_message:row=>row.external_id+','}});}
  return {nodes,events,calls,rows,member,open,setResponse:fn=>response=fn,setStatus:value=>status=value};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  let f=fixture();await flush();f.open();assert.equal(f.calls.length,1);
  const button=f.nodes.get('chatHistoryPages').children[0];let resolve;
  f.setResponse(()=>new Promise(r=>resolve=r));const request=button.onclick();await flush();await button.onclick();assert.equal(f.calls.length,2);
  resolve({success:true,messages:[{external_id:'old'},{external_id:'new'}],next_cursor:'cursor-2'});await request;
  assert.equal(f.rows.html,'old,');assert.ok(f.calls[1].url.includes('cursor=cursor-1'));
  f.setResponse(async()=>{throw Error('network');});await button.onclick();assert.equal(button.disabled,false);
  f.setResponse(async()=>({success:true,messages:[{external_id:'older'},{external_id:'old'}],next_cursor:null}));await button.onclick();
  assert.equal(f.rows.html,'older,old,');assert.equal(button.hidden,true);assert.ok(f.calls[3].url.includes('cursor=cursor-2'));
  assert.ok(f.calls.every(c=>!c.url.includes('/send')&&!c.url.includes('refresh=1')));
  f=fixture();await flush();f.open();const b=f.nodes.get('chatHistoryPages').children[0];f.setResponse(()=>new Promise(r=>resolve=r));
  const late=b.onclick();await flush();f.open(8);resolve({success:true,messages:[{external_id:'foreign'}],next_cursor:null});await late;
  assert.equal(f.rows.html,'');
  f.events['tenant-profile']({detail:{member:{tenant_id:'other',telegram_id:21}}});assert.ok(!f.nodes.has('chatHistoryPages'));
  f=fixture();await flush();f.open();f.setStatus(403);f.setResponse(async()=>({success:false,error:'Access revoked'}));
  await f.nodes.get('chatHistoryPages').children[0].onclick();assert.equal(f.rows.textContent,'Access revoked');assert.ok(!f.nodes.has('chatHistoryPages'));
  f=fixture();await flush();f.open();f.setResponse(()=>new Promise(r=>resolve=r));
  const resetRequest=f.nodes.get('chatHistoryPages').children[0].onclick();await flush();
  f.events['tenant-chat-history-reset']({detail:{lead_id:7}});resolve({success:true,messages:[{external_id:'stale'}],next_cursor:null});await resetRequest;
  assert.equal(f.rows.html,'');assert.ok(!f.nodes.has('chatHistoryPages'));
  console.log('PASS: history cursor retry, double click protection, dedupe, stale responses and company switch.');
})().catch(error=>{console.error(error);process.exitCode=1;});
