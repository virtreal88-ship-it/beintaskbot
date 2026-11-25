/* Manual wall-clock test. Supply a valid, synthetic MP4 of at least two seconds. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const http=require('node:http');
const {once}=require('node:events');
const {setTimeout:delay}=require('node:timers/promises');
const {fixture,server,message,launch,png,wav}=require('./visible_media_fixture.cjs');

const clip=fs.readFileSync(process.argv[2]);
const movie=Buffer.alloc(99*1024*1024);
assert(clip.length+8<movie.length);
clip.copy(movie);
// A valid MP4 padding atom preserves playback and seeking with the full HTTP payload.
movie.writeUInt32BE(movie.length-clip.length,clip.length);
movie.write('free',clip.length+4);

const requests=[];
let started,firstChunk,streamEnded=false;
const native=http.createServer(async(req,res)=>{
 const url=new URL(req.url,'http://localhost');
 if(!url.pathname.endsWith('/crm/chat/attachment')){res.writeHead(404);res.end();return;}
 const id=url.searchParams.get('message_id');
 const cacheOnly=url.searchParams.get('cache_only')==='1';
 const automatic=url.searchParams.get('auto_preview')==='1';
 requests.push({id,cacheOnly,automatic});
 assert.equal(req.method,'GET');
 assert.equal(url.searchParams.get('expected_user_id'),'33');
 assert(url.searchParams.get('expected_media_version'));
 if(cacheOnly){res.writeHead(204);res.end();return;}
 if(id!=='cold-video'){
  const bytes=id.endsWith('photo')?png:wav;
  res.writeHead(200,{'Content-Type':id.endsWith('photo')?'image/png':'audio/wav','Content-Length':bytes.length,'Cache-Control':'no-store'});
  res.end(bytes);
  return;
 }
 started=performance.now();
 await delay(75000);
 if(res.destroyed)return;
 res.writeHead(200,{'Content-Type':'video/mp4','Content-Length':movie.length,'Cache-Control':'no-store'});
 firstChunk=performance.now();
 // Real HTTP body, paced at roughly 10.5 Mbit/s for about 79 seconds.
 const block=512*1024;
 for(let offset=0;offset<movie.length&&!res.destroyed;offset+=block){
  res.write(movie.subarray(offset,offset+block));
  if(offset+block<movie.length)await delay(400);
 }
 if(!res.destroyed){streamEnded=true;res.end();}
});

async function ordinaryChat(browser,origin,label,sharedContext){
 const photo=label+'-photo',audio=label+'-audio';
 const messages=[{...message(label+'-text','text'),body:'Control conversation'},
  message(photo,'image'),message(audio,'audio')];
 const f=await fixture(browser,server(),{width:1280,height:900,messages,origin,attachmentPassthrough:true,sharedContext});
 try{
  const start=performance.now();
  await f.open();
  await f.page.getByText('Control conversation',{exact:true}).waitFor();
  const history=Math.round(performance.now()-start);
  await f.card(photo).locator('img[data-media-preview]').waitFor();
  await f.loaded(audio);
  const media=Math.round(performance.now()-start);
  assert.deepEqual(f.errors,[]);
  return {history,media};
 }finally{if(sharedContext)await f.page.close();else await f.context.close();}
}

(async()=>{
 native.listen(0,'127.0.0.1');
 await once(native,'listening');
 const origin='http://127.0.0.1:'+native.address().port;
 const browser=await launch();
 try{
  const baseline=await ordinaryChat(browser,origin,'baseline');
  const f=await fixture(browser,server(),{width:1280,height:900,messages:[message('cold-video','video')],origin,attachmentPassthrough:true});
  try{
   const autoRequest=f.page.waitForRequest(req=>req.url().includes('message_id=cold-video')&&req.url().includes('auto_preview=1'));
   await f.open();
   await autoRequest;
   const duringOrigin=await ordinaryChat(browser,origin,'origin-wait',f.context);
   assert(!firstChunk,'control chat and light media completed before video response');
   const card=f.card('cold-video');
   await f.page.waitForFunction(()=>document.querySelector('[data-chat-attachment="cold-video"] [data-media-load-status]')?.textContent.includes('%'),null,{timeout:85000});
   assert(firstChunk-started>=74000);
   const duringBody=await ordinaryChat(browser,origin,'during-body',f.context);
   assert(!streamEnded,'control chat and light media completed before the transfer');
   const player=card.locator('video');
   await f.page.waitForFunction(()=>document.querySelector('[data-chat-attachment="cold-video"] video')?.readyState>=1,null,{timeout:100000});
   const readyAfter=Math.round(performance.now()-started);
   assert(streamEnded);
   assert.equal(await player.evaluate(node=>node.paused&&!node.autoplay&&node.playsInline),true);
   const beforePlayback=requests.length;
   await player.evaluate(async node=>{
    await node.play();
    node.pause();
    await new Promise(resolve=>{node.addEventListener('seeked',resolve,{once:true});node.currentTime=1.75;});
   });
   assert.equal(await player.evaluate(node=>node.currentTime),1.75);
   assert.equal(requests.length,beforePlayback,'play and seek reuse the downloaded file');
   assert.deepEqual(requests.filter(request=>request.id==='cold-video').map(({cacheOnly,automatic})=>[cacheOnly,automatic]),[[true,false],[false,true]]);
   assert.deepEqual(f.errors,[]);
   console.log(JSON.stringify({bytes:movie.length,headerDelayMs:Math.round(firstChunk-started),readyAfterMs:readyAfter,baseline,duringOrigin,duringBody}));
  }finally{await f.context.close();}
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>{native.closeAllConnections();native.close();});
