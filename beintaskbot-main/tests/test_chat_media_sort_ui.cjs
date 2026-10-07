const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(require('node:path').join(__dirname,'../docs/index.html'),'utf8');
for(const script of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)) new Function(script[1]);
function extract(name) {
  const start=html.indexOf('function '+name+'(');
  const end=html.indexOf('\nfunction ',start+1);
  return html.slice(start,end);
}
const context={};vm.runInNewContext(extract('sortDealsByModified'),context);
const deals=[{id:1,updated_at:5},{id:2,updated_at:10},{id:3,updated_at:'2026-10-07T00:00:00Z'}, {id:4,created_at:3}];
assert.equal(context.sortDealsByModified(deals).map(row=>row.id).join(','),'3,2,1,4');
assert.equal(deals[0].id,1,'sorting must not mutate shared stage cache');
assert.equal(context.sortDealsByModified([{id:1,lead_updated_at:5,updated_at:100},{id:2,lead_updated_at:10,updated_at:10}])[0].id,2,'chat activity must not replace lead modification time');
assert(html.includes('return sortDealsByModified(filtered)'));
assert(html.includes('signal: controller.signal'));
assert(html.includes('window.clearTimeout(timeout)'));
const start=html.indexOf('async function loadMissingPhoto(');
const photo=html.slice(start,html.indexOf('\nfunction bindPhotoWait',start));
assert(photo.includes("direct && !cloud ? direct : lookup"));
assert(photo.includes("wrap.dataset.loading = ''"));
assert(photo.includes("wrap.classList.add('failed')"));
(async()=>{
  const calls=[];
  const classes=new Set();
  const wrap={dataset:{},classList:{contains:name=>classes.has(name),add:(...names)=>names.forEach(name=>classes.add(name)),remove:(...names)=>names.forEach(name=>classes.delete(name))},
              getAttribute:()=>'/api/deal/file?src=known',querySelector:()=>({})};
  let fail=false;
  const ui={showCachedPhoto:()=>false,mediaLookupUrl:()=>'/lookup',_mediaBlobs:new Map(),mediaCacheKey:()=> 'photo',
            fetchMediaBlob:async url=>{calls.push(url);if(fail)throw Error('offline');return {size:5};},URL:{createObjectURL:()=> 'blob:photo'}};
  vm.runInNewContext(photo,ui);
  await ui.loadMissingPhoto(wrap);
  assert.equal(calls.join(','),'/api/deal/file?src=known');
  assert(classes.has('ready'));assert(!classes.has('loading'));
  classes.clear();fail=true;calls.length=0;
  await ui.loadMissingPhoto(wrap);
  assert.equal(calls.join(','),'/api/deal/file?src=known,/lookup');
  assert(classes.has('failed'));assert(!classes.has('loading'));assert.equal(wrap.dataset.loading,'');
  let timeout,cleared=false;
  const loading={AbortController,idbGet:async()=>null,authFetchHeaders:()=>({}),window:{setTimeout:fn=>{timeout=fn;return 1;},clearTimeout:()=>{cleared=true;}},
                 fetch:async (url,options)=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Error('timeout'))))};
  const blobStart=html.indexOf('async function fetchMediaBlob(');
  vm.runInNewContext(html.slice(blobStart,start),loading);
  const pending=loading.fetchMediaBlob('/slow');
  await new Promise(resolve=>setImmediate(resolve));timeout();
  await assert.rejects(pending,/timeout/);assert(cleared);
  const queueStart=html.indexOf('const _photoLoadQueue =');
  const queueCode=html.slice(queueStart,html.indexOf('\nfunction dropAudioCache',queueStart));
  const waiting=[],observed=[];
  let visible;
  const wrappers=Array.from({length:5},()=>({dataset:{},isConnected:true,classList:{contains:()=>false},querySelector:()=>null}));
  const auto={window:{IntersectionObserver:true},showCachedPhoto:()=>false,
              loadMissingPhoto:wrap=>new Promise(resolve=>waiting.push({wrap,resolve})),
              IntersectionObserver:class {constructor(callback){visible=callback;}observe(wrap){observed.push(wrap);}unobserve(){}}};
  vm.runInNewContext(queueCode,auto);
  auto.bindPhotoWait({querySelectorAll:()=>wrappers});
  assert.equal(observed.length,5);assert.equal(waiting.length,0,'offscreen photos must not download');
  visible(wrappers.map(target=>({target,isIntersecting:true})));
  assert.equal(waiting.length,3,'visible photos start automatically, at most three at once');
  waiting[0].resolve();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(waiting.length,4,'a queued photo starts when a slot becomes free');
  wrappers[4].isConnected=false;
  waiting[1].resolve();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(waiting.length,4,'closed-chat placeholders must be skipped');
  waiting.slice(2).forEach(item=>item.resolve());
  let fallbackLoads=0;
  const fallback={window:{},showCachedPhoto:()=>false,loadMissingPhoto:async()=>{fallbackLoads++;}};
  vm.runInNewContext(queueCode,fallback);
  fallback.bindPhotoWait({querySelectorAll:()=>[{dataset:{},isConnected:true,classList:{contains:()=>false},querySelector:()=>null}]});
  assert.equal(fallbackLoads,1,'without IntersectionObserver binding must still start a request');
  assert(html.includes('.photo-wait .audio-spin { display:none;'));
  assert(html.includes('.photo-wait .photo-miss { display:block;'));
  console.log('PASS: script syntax, newest-modified deals, immutable cache, direct-first photo, fallback, failure reset and timeout.');
})().catch(error=>{console.error(error);process.exitCode=1;});
