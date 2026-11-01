const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('docs/tenant-chat-attachments.js','utf8');
assert.match(source,/function audioPlayer/, 'voice notes must use the compact lazy audio player');
assert.match(source,/preload='metadata'/, 'video metadata must not be eagerly downloaded');
assert.match(source,/data-media-preview/, 'decoded previews must be removable when tenant or dialog changes');
assert.match(source,/expected_tenant_id:member\.tenant_id/, 'each media request must stay tenant scoped');
function fixture(){
  const events={},calls=[],revoked=[],hosts=[];let response,downloadClicks=0;
  const makeHost=id=>{
    const button={disabled:false,hidden:false,dataset:{},isConnected:true,setAttribute(){},click(){this.onclick?.();},closest(){return host;}};
    const menuLoad={dataset:{},disabled:false,hidden:false,textContent:'Load file',closest:()=>({removeAttribute(){}})};const downloadButton={disabled:false,dataset:{mediaDownload:'/api/platform/crm/chat/attachment?download=1'},isConnected:true,setAttribute(){},closest:()=>host};
    const status={textContent:''};
    const host={dataset:{chatAttachment:id,connectMedia:'true',attachmentLeadId:'1002',channelId:'channel',conversationId:'conversation'},isConnected:true,children:[],
      querySelector:selector=>selector==='button' || selector==='button[data-media-open]' || selector==='[data-media-open]'?button:selector==='[role="status"]'?status:selector==='[data-media-menu-load]'?menuLoad:selector==='[data-media-download]'?downloadButton:selector==='[data-media-preview]'?null:selector==='.operator-native-audio'?null:null,
      querySelectorAll:()=>[],append(node){this.children.push(node);},insertBefore(node){this.children.push(node);},
    };
    button.closest=()=>host;menuLoad.onclick=null;hosts.push(host);return {host,button,status,menuLoad,downloadButton};
  };
  const primary=makeHost('message-id'),{host,button,status,menuLoad,downloadButton}=primary;
  const nodes={closeModal:{addEventListener(name,fn){events['close:'+name]=fn;}},dealModal:{addEventListener(name,fn){events['modal:'+name]=fn;}}};
  class LocalURL extends URL{}LocalURL.createObjectURL=()=> 'blob:local';LocalURL.revokeObjectURL=url=>revoked.push(url);
  const env={Set,URL:LocalURL,URLSearchParams,Blob,AbortController,setTimeout,clearTimeout,
    document:{hidden:false,addEventListener:(name,fn)=>events[name]=fn,getElementById:id=>nodes[id],
      querySelectorAll:()=>hosts,createElement:tag=>({tagName:tag.toUpperCase(),remove(){this.removed=true;},click(){downloadClicks++;}})},
    window:{tenantI18n:{t:key=>key==='mediaRetryAfter'?'Try again in {seconds} seconds.':key==='mediaBusy'?'Other attachments are loading. Try again when they finish.':''}},
    fetch:async(url,options)=>{if(url==='/api/platform/me')return {ok:true,json:async()=>({member:{tenant_id:'company',telegram_id:20}})};calls.push({url,options});return response();}};
  vm.runInNewContext(source,env);
  events['tenant-profile']({detail:{member:{tenant_id:'company',telegram_id:20}}});
  events['tenant-chat-open']({detail:{lead_id:7}});events['tenant-chat-loaded']({detail:{}});
  const good=()=>({ok:true,headers:{get:name=>name==='Content-Type'?'image/png':'3'},body:{getReader:()=>{
    let once=false;return {read:async()=>once?{done:true}:(once=true,{done:false,value:new Uint8Array([1,2,3])}),cancel:async()=>{}};}}});
  response=async()=>good();
  return {events,calls,revoked,host,button,status,menuLoad,downloadButton,hosts,makeHost,downloadClicks:()=>downloadClicks,window:env.window,setResponse:fn=>response=fn,good};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  let f=fixture();f.host.dataset.attachmentLeadId='1002';assert.equal(f.calls.length,0);await f.button.onclick();
  assert.equal(f.calls.length,1);assert.equal(f.calls[0].options.credentials,'same-origin');assert.equal(f.calls[0].options.cache,'no-store');assert.ok(f.calls[0].url.includes('message_id=message-id'));assert.ok(f.calls[0].url.includes('lead_id=1002'));
  assert.ok(!f.calls[0].url.includes('drive-'));assert.equal(f.host.children[0].tagName,'IMG');
  assert.equal(f.button.hidden,true);assert.equal(f.status.textContent,'');
  f.events['close:click']();assert.deepEqual(f.revoked,['blob:local']);
  f=fixture();f.setResponse(async()=>({ok:false,status:503,headers:{get:()=>null},json:async()=>({error:'Fayl yüklənmədi.'})}));
  await f.button.onclick();assert.ok(f.status.textContent.includes('Fayl yüklənmədi'));assert.equal(f.button.disabled,false);
  f=fixture();let resolve;f.setResponse(()=>new Promise(r=>resolve=r));
  const pending=f.button.onclick();await flush();await f.button.onclick();assert.equal(f.calls.length,1);
  f.events['tenant-chat-open']({detail:{lead_id:8}});resolve(f.good());await pending;
  assert.equal(f.host.children.length,0);assert.equal(f.calls[0].options.signal.aborted,true);
  f=fixture();f.setResponse(()=>new Promise(r=>resolve=r));const switched=f.button.onclick();await flush();
  f.events['tenant-profile']({detail:{member:{tenant_id:'other',telegram_id:21}}});resolve(f.good());await switched;
  assert.equal(f.host.children.length,0);
  f=fixture();f.setResponse(async()=>({ok:false,status:429,headers:{get:()=> '60'},json:async()=>({})}));await f.button.onclick();assert.equal(f.calls.length,1);assert.match(f.status.textContent,/60/);assert.equal(f.menuLoad.textContent,'Retry loading');assert.equal(f.menuLoad.disabled,false);await f.window?.tenantDownloadAttachment?.(f.downloadButton,f.downloadButton.dataset.mediaDownload,f.status);assert.equal(f.calls.length,1,'download shares the same per-attachment Retry-After cooldown');
  for(const statusCode of [403,404,410,413,415]){f=fixture();f.setResponse(async()=>({ok:false,status:statusCode,headers:{get:()=>null},json:async()=>({error:'not available'})}));await f.button.onclick();assert.equal(f.button.disabled,true,`${statusCode} blocks futile retry from the card`);assert.equal(f.menuLoad.disabled,true,`${statusCode} disables retry in the menu`);f=fixture();f.setResponse(async()=>({ok:false,status:statusCode,headers:{get:()=>null},json:async()=>({error:'not available'})}));await f.window.tenantDownloadAttachment(f.downloadButton,f.downloadButton.dataset.mediaDownload,f.status);assert.equal(f.downloadButton.disabled,true,`${statusCode} keeps futile download retry disabled after request completion`);}
  f=fixture();f.setResponse(async()=>({ok:false,status:503,headers:{get:()=>null},json:async()=>({error:'temporarily unavailable'})}));await f.button.onclick();assert.equal(f.menuLoad.disabled,false,'503 leaves a manual retry available');assert.equal(f.menuLoad.textContent,'Retry loading');
  f=fixture();let release=[];f.setResponse(()=>new Promise(resolve=>release.push(resolve)));const second=f.makeHost('second'),third=f.makeHost('third');f.events['tenant-chat-loaded']({detail:{}});const one=f.button.onclick(),two=second.button.onclick();await flush();assert.equal(f.calls.length,2);await third.button.onclick();assert.equal(f.calls.length,2,'third Connect request is held at the safe concurrency cap');assert.match(third.status.textContent,/Other attachments/);release.forEach(resolve=>resolve(f.good()));await Promise.all([one,two]);
  console.log('PASS: explicit media load, Connect concurrency cap, shared 429 cooldown, identity binding, double click, error, close and stale response protection.');
})().catch(error=>{console.error(error);process.exitCode=1;});
