const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('docs/tenant-chat-attachments.js','utf8');
function fixture(){
  const events={},calls=[],revoked=[];let response;
  const button={disabled:false,setAttribute(){}},status={textContent:''};
  const host={dataset:{chatAttachment:'message-id'},isConnected:true,children:[],
    querySelector:selector=>selector==='button'?button:status,querySelectorAll:()=>[],append(node){this.children.push(node);}};
  const nodes={closeModal:{addEventListener(name,fn){events['close:'+name]=fn;}},dealModal:{addEventListener(name,fn){events['modal:'+name]=fn;}}};
  class LocalURL extends URL{}LocalURL.createObjectURL=()=> 'blob:local';LocalURL.revokeObjectURL=url=>revoked.push(url);
  const env={Set,URL:LocalURL,URLSearchParams,Blob,AbortController,setTimeout,clearTimeout,
    document:{hidden:false,addEventListener:(name,fn)=>events[name]=fn,getElementById:id=>nodes[id],
      querySelectorAll:()=>[host],createElement:tag=>({tagName:tag.toUpperCase(),remove(){this.removed=true;}})},
    fetch:async(url,options)=>{if(url==='/api/platform/me')return {ok:true,json:async()=>({member:{tenant_id:'company',telegram_id:20}})};calls.push({url,options});return response();}};
  vm.runInNewContext(source,env);
  events['tenant-profile']({detail:{member:{tenant_id:'company',telegram_id:20}}});
  events['tenant-chat-open']({detail:{lead_id:7}});events['tenant-chat-loaded']({detail:{}});
  const good=()=>({ok:true,headers:{get:name=>name==='Content-Type'?'image/png':'3'},body:{getReader:()=>{
    let once=false;return {read:async()=>once?{done:true}:(once=true,{done:false,value:new Uint8Array([1,2,3])}),cancel:async()=>{}};}}});
  response=async()=>good();
  return {events,calls,revoked,host,button,status,setResponse:fn=>response=fn,good};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  let f=fixture();assert.equal(f.calls.length,0);await f.button.onclick();
  assert.equal(f.calls.length,1);assert.ok(f.calls[0].url.includes('message_id=message-id'));
  assert.ok(!f.calls[0].url.includes('drive-'));assert.equal(f.host.children[0].tagName,'IMG');
  assert.equal(f.button.hidden,true);assert.equal(f.status.textContent,'');
  f.events['close:click']();assert.deepEqual(f.revoked,['blob:local']);
  f=fixture();f.setResponse(async()=>({ok:false,json:async()=>({error:'Fayl yüklənmədi.'})}));
  await f.button.onclick();assert.ok(f.status.textContent.includes('Fayl yüklənmədi'));assert.equal(f.button.disabled,false);
  f=fixture();let resolve;f.setResponse(()=>new Promise(r=>resolve=r));
  const pending=f.button.onclick();await flush();await f.button.onclick();assert.equal(f.calls.length,1);
  f.events['tenant-chat-open']({detail:{lead_id:8}});resolve(f.good());await pending;
  assert.equal(f.host.children.length,0);assert.equal(f.calls[0].options.signal.aborted,true);
  f=fixture();f.setResponse(()=>new Promise(r=>resolve=r));const switched=f.button.onclick();await flush();
  f.events['tenant-profile']({detail:{member:{tenant_id:'other',telegram_id:21}}});resolve(f.good());await switched;
  assert.equal(f.host.children.length,0);
  console.log('PASS: explicit media load, identity binding, double click, error, close and stale response protection.');
})().catch(error=>{console.error(error);process.exitCode=1;});
