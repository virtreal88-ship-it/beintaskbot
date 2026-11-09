/* Entirely synthetic: no customer tokens, backend, credentials or sends. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const template=fs.readFileSync(path.join(__dirname,'../../docs/public-case.html'),'utf8');
const MB=1024*1024,tick=async()=>{for(let i=0;i<12;i++)await new Promise(r=>setImmediate(r));};
function setup(types,{selected=types.map((_,i)=>String(i)),sizes={},fail={},headers={},retryAfter=null,fakeTimers=false}={}){
 const data={selected_media_ids:selected,public_messages:types.map((type,i)=>({id:String(i),type,text:'Synthetic '+i}))};
 const dom=new JSDOM(template.replace('__CASE_DATA__',JSON.stringify(data)),{url:'https://fixture.invalid/public/case/synthetic',runScripts:'outside-only'}),w=dom.window;
 const timers=new Map();let timerId=0;if(fakeTimers){w.setTimeout=(fn,delay)=>{const id=++timerId;timers.set(id,{fn,delay});return id;};w.clearTimeout=id=>timers.delete(id);}
 const pending=[],calls=[],revoked=[],created=[],cancelled=[];let live=0,peak=0,plays=0;
 w.HTMLMediaElement.prototype.pause=function(){};w.HTMLMediaElement.prototype.load=function(){};w.HTMLMediaElement.prototype.play=function(){plays++;return Promise.resolve();};
 w.HTMLElement.prototype.getBoundingClientRect=function(){return {top:this.dataset.visible==='yes'?0:2000,bottom:this.dataset.visible==='yes'?100:2100};};
 // Fake byte lengths avoid allocating hundreds of MB. Production code itself is unchanged.
 w.Blob=class{constructor(parts,options){this.size=parts.reduce((sum,p)=>sum+p.byteLength,0);this.type=options?.type;}};
 w.URL.createObjectURL=blob=>{created.push(blob.size);return 'blob:synthetic-'+created.length;};w.URL.revokeObjectURL=url=>revoked.push(url);
 w.fetch=(url,options)=>new Promise((resolve,reject)=>{const id=url.split('/').pop();calls.push(id);live++;peak=Math.max(peak,live);let done=false;
 const finish=()=>{if(done)return;done=true;live--;const chunks=[...(sizes[id]||[1024])];resolve({ok:!fail[id],status:fail[id]||200,headers:{get:key=>key==='Content-Length'?headers[id]??null:key==='Retry-After'?retryAfter:'application/octet-stream'},body:{cancel:async()=>cancelled.push(id),getReader:()=>({read:async()=>chunks.length?{done:false,value:{byteLength:chunks.shift()}}:{done:true},cancel:async()=>cancelled.push(id),releaseLock(){}})}});};
 options.signal.addEventListener('abort',()=>{if(done)return;done=true;live--;reject(new w.DOMException('Aborted','AbortError'));});pending.push({id,finish,options});});
 w.eval([...w.document.scripts].map(s=>s.textContent).join('\n'));
 return {w,dom,calls,pending,timers,revoked,created,cancelled,fail,peak:()=>peak,plays:()=>plays,box:i=>w.document.querySelectorAll('.media-box')[i],finish:async id=>{pending.find(p=>p.id===String(id)&&!p.finished&&(p.finished=true))?.finish();await tick();},close(){w.dispatchEvent(new w.Event('pagehide'));dom.window.close();}};
}
(async()=>{
 let h=setup(['image','audio','video','voice','document','image'],{selected:['0','1','2','3','4']});
 assert.deepEqual(h.calls,['0','1'],'two downloads begin immediately');
 h.box(0).querySelector('button').click();assert.equal(h.calls.length,2,'loading item cannot duplicate');
 await h.finish(0);await h.finish(1);await h.finish(2);await h.finish(3);
 assert.deepEqual(h.calls,['0','1','2','3'],'offscreen selected AV all starts without clicks or scroll');assert.equal(h.peak(),2);assert.equal(h.plays(),0);
 assert.ok([...h.w.document.querySelectorAll('audio,video')].every(n=>!n.autoplay&&n.controls&&n.preload==='metadata'));
 h.box(4).querySelector('button').click();await h.finish(4);assert.equal(h.calls.length,5,'document manual only');
 h.box(0).querySelector('button').click();assert.equal(h.calls.length,5,'zoom reuses loaded blob');assert.equal(h.revoked.length,0,'offscreen blobs retained without pressure');
 h.w.dispatchEvent(new h.w.Event('pagehide'));assert.equal(h.revoked.length,5);assert.equal(h.w.document.querySelectorAll('.media').length,0);
 h.w.dispatchEvent(new h.w.PageTransitionEvent('pageshow',{persisted:true}));await tick();assert.equal(h.calls.length,7,'bfcache resumes automatic media');assert.equal(h.calls.filter(id=>id==='4').length,1,'bfcache does not auto-load documents');assert.equal(h.box(4).querySelector('button').hidden,false,'manual document is reopenable after bfcache');h.box(4).querySelector('button').click();await h.finish(0);await h.finish(1);await h.finish(2);await h.finish(3);await h.finish(4);assert.equal(h.calls.filter(id=>id==='4').length,2);h.close();
 h=setup(['image','audio','video'],{fail:{0:503}});await h.finish(0);await h.finish(1);await h.finish(2);assert.match(h.box(0).textContent,/Yenidən/);h.w.dispatchEvent(new h.w.Event('scroll'));await tick();assert.equal(h.calls.filter(id=>id==='0').length,1,'errors require explicit retry');delete h.fail[0];h.box(0).querySelector('button').click();h.box(0).querySelector('button').click();await h.finish(0);assert.equal(h.calls.filter(id=>id==='0').length,2);h.close();
 for(const [kind,cap] of [['image',20],['audio',10],['voice',10],['video',100],['document',5]]){
  h=setup([kind],{sizes:{0:[cap*MB,1]}});if(kind==='document')h.box(0).querySelector('button').click();await h.finish(0);assert.deepEqual(h.cancelled,['0'],kind+' streamed overflow cancelled');assert.equal(h.created.length,0);assert.match(h.box(0).textContent,/ölçüsü/);h.close();
 }
 h=setup(['image'],{headers:{0:String(21*MB)}});await h.finish(0);assert.deepEqual(h.cancelled,['0'],'oversize content length cancelled before read');assert.equal(h.created.length,0);h.close();
 h=setup(['video','video','video'],{sizes:{0:[100*MB],1:[100*MB],2:[100*MB]}});assert.equal(h.calls.length,2);await h.finish(0);assert.equal(h.calls.length,3,'offscreen nonplaying blob evicted to reserve next video');assert.equal(h.revoked.length,1);await h.finish(1);await h.finish(2);assert.equal(h.peak(),2);h.close();
 h=setup(['video','video','video'],{sizes:{0:[100*MB],1:[100*MB],2:[100*MB]}});h.box(0).dataset.visible='yes';h.box(1).dataset.visible='yes';await h.finish(0);await h.finish(1);assert.equal(h.calls.length,2,'visible retained blobs plus reservation cannot exceed 200MB');h.box(0).dataset.visible='no';h.w.dispatchEvent(new h.w.Event('scroll'));await tick();assert.equal(h.calls.length,3);h.close();
 h=setup(['video','video','video'],{sizes:{0:[100*MB],1:[100*MB]}});h.box(0).dataset.visible='yes';await h.finish(0);const video=h.box(0).querySelector('video');Object.defineProperty(video,'paused',{value:false,configurable:true});Object.defineProperty(video,'ended',{value:false});h.box(0).dataset.visible='no';h.box(1).dataset.visible='yes';await h.finish(1);assert.equal(h.calls.length,2,'playing offscreen blob is protected');Object.defineProperty(video,'paused',{value:true});video.dispatchEvent(new h.w.Event('pause'));await tick();assert.equal(h.calls.length,3);h.close();
 h=setup(['image','audio','video']);h.w.dispatchEvent(new h.w.Event('pagehide'));await tick();assert.ok(h.pending.every(p=>p.options.signal.aborted));assert.equal(h.calls.length,2,'pagehide clears queue');assert.equal(h.created.length,0);h.w.dispatchEvent(new h.w.PageTransitionEvent('pageshow',{persisted:true}));await tick();assert.equal(h.calls.length,4,'bfcache resumes aborted requests safely');assert.equal(h.peak(),2);h.close();
 h=setup(['document','video','video'],{sizes:{0:[5*MB],1:[95*MB],2:[100*MB]}});await h.finish(1);await h.finish(2);h.box(0).querySelector('button').click();await h.finish(0);h.box(2).dataset.visible='yes';h.box(1).querySelector('video').dispatchEvent(new h.w.Event('error'));h.box(1).querySelector('button').click();await tick();assert.equal(h.box(0).querySelector('a'),null,'offscreen document evicted under pressure');assert.equal(h.box(0).querySelector('button').hidden,false,'evicted document can be reopened manually');await h.finish(1);h.box(0).querySelector('button').click();await h.finish(0);assert.equal(h.calls.filter(id=>id==='0').length,2);h.close();
 for(const [retryAfter,expected] of [['-10',1000],['9999',300000],[null,60000]]){
  h=setup(['image'],{fail:{0:429},retryAfter,fakeTimers:true});
  for(let attempt=0;attempt<2;attempt++){await h.finish(0);assert.equal(h.timers.size,1);const [id,timer]=[...h.timers][0];assert.equal(timer.delay,expected);h.box(0).querySelector('button').click();assert.equal(h.calls.length,attempt+1,'waiting cannot bypass Retry-After');h.timers.delete(id);timer.fn();await tick();}
  await h.finish(0);assert.equal(h.calls.length,3,'429 automatic retries bounded at two');assert.equal(h.timers.size,0);assert.match(h.box(0).textContent,/Yenidən/);h.close();
 }
 h=setup(['image'],{fail:{0:429},fakeTimers:true});await h.finish(0);assert.equal(h.timers.size,1);h.w.dispatchEvent(new h.w.Event('pagehide'));assert.equal(h.timers.size,0,'pagehide cancels retry timers');h.close();
 console.log('PASS: automatic selected offscreen AV, concurrency, retry, stream caps, memory pressure, playing protection, pagehide and bfcache.');
})().catch(error=>{console.error(error);process.exitCode=1;});
