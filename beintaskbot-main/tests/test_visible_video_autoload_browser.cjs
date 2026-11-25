const assert=require('node:assert/strict');
const {fixture,server,message,launch,deferred}=require('./visible_media_fixture.cjs');

// Two seconds of synthetic WebM generated with ffmpeg color=c=blue:s=16x16:r=4:d=2.
const clip=Buffer.from('GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQJChYECGFOAZwEAAAAAAAKvEU2bdLpNu4tTq4QVSalmU6yBoU27i1OrhBZUrmtTrIHWTbuMU6uEElTDZ1OsggEyTbuMU6uEHFO7a1OsggKZ7AEAAAAAAABZAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAVSalmsCrXsYMPQkBNgIxMYXZmNjMuMS4xMDFXQYxMYXZmNjMuMS4xMDFEiYhAn0AAAAAAABZUrmvXrgEAAAAAAABO14EBc8WIfVYpcvQbusqcgQAitZyDdW5kiIEAhoVWX1ZQOIOBASPjg4QO5rKA4JCwgRC6gRCagQJVsIRVuYEBVe6BAOwBAAAAAAAAAgAAElTDZ/pzc59jwIBnyJlFo4dFTkNPREVSRIeMTGF2ZjYzLjEuMTAxc3PVY8CLY8WIfVYpcvQbuspnyKBFo4dFTkNPREVSRIeTTGF2YzYzLjEuMTAxIGxpYnZweGfIoUWjiERVUkFUSU9ORIeTMDA6MDA6MDIuMDAwMDAwMDAwAB9DtnVA4ueBAKO8gQAAgLACAJ0BKhAAEAAARwiFhYiFhIgCAgJ1qgP4Agz9KAD+/00S//xYV/FhX8WFf/FhX/z8zu3F/OYAo5WBAPoAsQEABRCsABgAGFgv9AAIAACjlYEB9ACxAQAFEKwAGAAYWC/0AAgAAKOVgQLuALEBAAUQrAAYABhYL/QACAAAo5WBA+gAsQEABRCsABgAGFgv9AAIAACjlYEE4gCxAQAFEKwAGAAYWC/0AAgAAKOVgQXcALEBAAUQrAAYABhYL/QACAAAo5WBBtYAsQEABRAQFGAAYWC/0AAgAAAcU7trkbuPs4EAt4r3gQHxggGx8IED','base64');
const video=(id,direction)=>({...message(id,'video'),direction});
const text=i=>({...message('spacer-'+i,'text'),message_type:'text',body:'Synthetic conversation line '+i});

async function playAndSeek(f,srv,id){
 const player=f.card(id).locator('video');
 await f.page.waitForFunction(id=>document.querySelector('[data-chat-attachment="'+id+'"] video')?.readyState>=1,id);
 assert.equal(await player.evaluate(node=>node.paused&&!node.autoplay&&node.playsInline),true);
 assert.equal(await f.card(id).locator('[data-media-open]').isVisible(),false);
 assert.equal(await f.card(id).locator('[data-media-download]').count(),0);
 const count=srv.requests.length;
 await player.evaluate(async node=>{
  await node.play();
  node.pause();
  await new Promise(resolve=>{node.addEventListener('seeked',resolve,{once:true});node.currentTime=1.25;});
 });
 assert.equal(await player.evaluate(node=>node.currentTime),1.25);
 assert.equal(srv.requests.length,count,'play and seek use retained bytes');
}

(async()=>{
 const browser=await launch();
 try{
  for(const width of [390,1280]){
   const srv=server();srv.video=clip;
   const first=await fixture(browser,srv,{width,mobile:width===390,messages:[video('incoming','incoming')]});
   try{
    await first.open();await playAndSeek(first,srv,'incoming');
    assert.deepEqual(srv.requests.map(req=>[req.cacheOnly,req.automatic]),[[true,false],[false,true]]);
    assert.equal(srv.upstream.length,1);assert.equal(srv.paidOrSend,0);assert.deepEqual(first.errors,[]);
   }finally{await first.context.close();}

   const cold=server();cold.video=clip;const gate=deferred();cold.gates.set('outgoing',gate);
   const scroll=await fixture(browser,cold,{width,mobile:width===390,messages:[text(0),...Array.from({length:22},(_,i)=>text(i+1)),
     video('outgoing','outgoing'),...Array.from({length:22},(_,i)=>text(i+23))]});
   try{
    await scroll.open();
    const top=scroll.page.locator('[data-message-id="spacer-0"]');
    await top.scrollIntoViewIfNeeded();
    assert.equal(cold.upstream.length,0,'out of view videos do not reach the provider');
    const auto=scroll.page.waitForRequest(req=>req.url().includes('message_id=outgoing')&&req.url().includes('auto_preview=1'));
    await scroll.card('outgoing').scrollIntoViewIfNeeded();await auto;
    const aborted=scroll.page.waitForEvent('requestfailed',{predicate:req=>req.url().includes('message_id=outgoing')&&req.url().includes('auto_preview=1')});
    await top.scrollIntoViewIfNeeded();await aborted;
    const resumed=scroll.page.waitForRequest(req=>req.url().includes('message_id=outgoing')&&req.url().includes('cache_only=1'));
    await scroll.card('outgoing').scrollIntoViewIfNeeded();await resumed;
    gate.release();
    await playAndSeek(scroll,cold,'outgoing');
    assert.equal(cold.upstream.length,1,'returning reuses the server flight or completed cache');
    assert.equal(cold.maxActive,1);assert.equal(cold.paidOrSend,0);assert.deepEqual(scroll.errors,[]);
   }finally{gate.release();await scroll.context.close();}
  }
  const srv=server();srv.video=clip;srv.status=503;
  const failure=await fixture(browser,srv,{messages:[video('retry-video','incoming')]});
  try{
   await failure.open();
   const retry=failure.card('retry-video').locator('[data-media-open]');
   await retry.waitFor();
   assert.match(await failure.card('retry-video').locator('[data-media-load-status]').textContent(),/Synthetic upstream failure/);
   srv.status=200;await retry.click();
   await playAndSeek(failure,srv,'retry-video');
   assert.equal(srv.upstream.length,2);
  }finally{await failure.context.close();}
  console.log('PASS cold video, scroll cancellation and return, 503 retry, playback and seek at phone and desktop widths');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
