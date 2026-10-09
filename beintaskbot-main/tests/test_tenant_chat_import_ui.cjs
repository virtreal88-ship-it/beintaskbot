const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('docs/tenant-chat-import.js','utf8');
function fixture(){
  const nodes=new Map(),events={},calls=[];let result=async()=>({success:true,imported:250,has_more:true,next_cursor:'next'}),status=200,reloads=0;
  class Element{
    constructor(){this.children=[];this.isConnected=true;}
    set id(value){this._id=value;nodes.set(value,this);}get id(){return this._id;}
    append(...items){this.children.push(...items);}before(item){this.beforeNode=item;}
    remove(){nodes.delete(this.id);this.isConnected=false;}setAttribute(){}
  }
  const box=new Element();box.id='chatRows';const member={tenant_id:'tenant',telegram_id:20};
  const env={document:{getElementById:id=>nodes.get(id),createElement:()=>new Element(),addEventListener:(name,fn)=>events[name]=fn},
    fetch:async(url,opt)=>{calls.push({url,opt});return url==='/api/platform/me'?{ok:true,json:async()=>({member})}:{ok:status<400,status,json:()=>result()};}};
  vm.runInNewContext(source,env);
  function open(id=7){events['tenant-chat-open']({detail:{lead_id:id}});loaded(id);}
  function loaded(id){events['tenant-chat-loaded']({detail:{lead_id:id,reload_cache:async()=>{reloads++;loaded(id);}}});}
  return {nodes,events,calls,member,open,loaded,reloads:()=>reloads,result:fn=>result=fn,status:value=>status=value};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  let f=fixture();await flush();f.open();assert.equal(f.calls.length,1,'No automatic import');
  let resolve;f.result(()=>new Promise(r=>resolve=r));let b=f.nodes.get('chatImportPages').children[0];
  const pending=b.onclick();await flush();await b.onclick();assert.equal(f.calls.length,2,'No double click');
  assert(b.disabled);assert.equal(b.textContent,'Yüklənir…');
  const data=JSON.parse(f.calls[1].opt.body);assert.equal(data.expected_tenant_id,'tenant');assert.equal(data.expected_user_id,20);
  resolve({success:true,imported:250,has_more:true,next_cursor:'next'});await pending;assert.equal(f.reloads(),1);
  f.result(async()=>({success:false,error:'temporary'}));f.status(503);
  await f.nodes.get('chatImportPages').children[0].onclick();assert.equal(JSON.parse(f.calls.at(-1).opt.body).cursor,'next');
  f.result(async()=>({success:true,has_more:false,next_cursor:null}));f.status(200);
  await f.nodes.get('chatImportPages').children[0].onclick();assert.equal(JSON.parse(f.calls.at(-1).opt.body).cursor,'next','Retry same page');
  assert(f.nodes.get('chatImportPages').children[0].disabled,'Stop when complete');
  assert(f.calls.every(call=>!call.url.includes('/send')&&!call.url.includes('/ai')));
  f=fixture();await flush();f.open();f.result(()=>new Promise(r=>resolve=r));
  const late=f.nodes.get('chatImportPages').children[0].onclick();await flush();f.open(8);
  resolve({success:true,has_more:true,next_cursor:'foreign'});await late;assert.equal(f.reloads(),0);
  f.events['tenant-profile']({detail:{member:{tenant_id:'other',telegram_id:21}}});assert(!f.nodes.has('chatImportPages'));
  f=fixture();await flush();f.open();f.status(403);f.result(async()=>({success:false,error:'revoked'}));
  await f.nodes.get('chatImportPages').children[0].onclick();assert(f.nodes.get('chatImportPages').children[0].disabled);
  console.log('PASS: explicit bounded import, spinner, same-page retry, cache reload and stale-company isolation.');
})().catch(error=>{console.error(error);process.exitCode=1;});
