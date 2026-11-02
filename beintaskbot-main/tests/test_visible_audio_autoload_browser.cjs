const assert=require('node:assert/strict');
const {fixture,server,message,launch,deferred}=require('./visible_media_fixture.cjs');
const settle=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
(async()=>{const browser=await launch();try{
 for(const [width,kind] of [[320,'voice'],[390,'audio'],[1280,'ptt']]){
  const s=server(),f=await fixture(browser,s,{width,messages:[message('voice-a',kind)]});try{
   await f.open();await f.loaded('voice-a');const audio=f.card('voice-a').locator('audio');
   assert.equal(await f.card('voice-a').locator('[data-media-open]').isVisible(),false);assert.equal(await audio.evaluate(a=>a.paused),true,'byte acquisition never autoplays');
   assert.equal(await f.card('voice-a').locator('.operator-audio-controls').evaluate(n=>n.inert),false);
   const box=await audio.boundingBox();await f.page.mouse.click(box.x+20,box.y+box.height/2);await f.page.waitForFunction(()=>document.querySelector('audio')?.paused===false);await audio.evaluate(a=>a.pause());
   await f.page.locator('article[data-message-id="voice-a"]').focus();await f.page.keyboard.press('Shift+F10');const menu=f.page.locator('.operator-reply-context-menu');await menu.waitFor();
   assert.equal(await menu.locator('[data-context-file-action=load]').isVisible(),false,'loaded audio has no redundant Load menu item');assert.equal(await menu.evaluate(n=>n.contains(document.activeElement)&&document.activeElement.getClientRects().length>0),true,'keyboard menu focuses a visible usable command');await f.page.keyboard.press('Escape');
   assert.equal(s.requests.filter(r=>r.cacheOnly).length,1);assert.equal(s.requests.filter(r=>r.automatic).length,1);assert.equal(s.upstream.length,1);
   assert.equal(await f.page.evaluate(()=>sessionStorage.getItem('tenant.audio-reload-intent.v1')),null,'automatic visibility never writes a manual-selection hint');
   await f.page.reload();await f.loaded('voice-a');assert.equal(s.upstream.length,1,'reload uses server cache, no second upstream load');
   const cold=await fixture(browser,s,{width,messages:[message('voice-a',kind)]});try{await cold.open();await cold.loaded('voice-a');assert.equal(s.upstream.length,1,'clean browser has no hint but reuses authorized server bytes');}finally{await cold.context.close();}
   await f.page.evaluate(()=>{window.savedAudio=document.querySelector('audio');window.savedAudio.currentTime=.5;for(let i=0;i<12;i++)document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));document.dispatchEvent(new CustomEvent('tenant-language'));});await settle(f.page);
   assert.equal(await audio.evaluate(a=>a===window.savedAudio&&a.currentTime>=.5),true);assert.equal(f.state.navigations,2,'background data/render events do not navigate the document');assert.equal(s.upstream.length,1);assert.equal(s.paidOrSend,0);assert.deepEqual(f.errors,[]);
   assert.equal(await f.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);console.log('PASS visible '+kind+' '+width+': cold load, native playback/no autoplay, cache reload/new browser, stable DOM/no AI');
  }finally{await f.context.close();}
 }
 for(const withoutLocks of [false,true]){
  const s=server(),gate=deferred();s.gates.set('voice-a',gate);const f=await fixture(browser,s,{width:1280,height:1600,withoutLocks,messages:['a','b','c'].map((v,i)=>message('voice-'+v,i?'voice':'audio',v))});try{
   const first=f.page.waitForRequest(r=>new URL(r.url()).searchParams.get('auto_preview')==='1');await f.open();await first;
   await f.page.waitForFunction(()=>document.querySelector('[data-chat-attachment="voice-a"] [data-media-open]')?.getAttribute('aria-busy')==='true');await settle(f.page);
   assert.equal(s.requests.filter(r=>r.automatic).length,1);assert.equal(s.maxActive,1);gate.release();await Promise.all(['a','b','c'].map(id=>f.loaded('voice-'+id)));
   assert.equal(s.maxActive,1);assert.equal(s.self429,0);assert.equal(s.upstream.length,3);assert.equal(await f.page.locator('[data-media-preview=audio]').count(),3);assert.deepEqual(f.errors,[]);console.log('PASS three voices shared automatic cap1, Locks='+!withoutLocks);
  }finally{gate.release();await f.context.close();}
 }
 // The fourth resident preview and a new photo must not pause the user's voice.
 {
  const s=server(),gate=deferred();s.gates.set('voice-b',gate);const f=await fixture(browser,s,{width:1280,height:2000,messages:['a','b','c','d'].map(v=>message('voice-'+v,'voice',v))});try{
   await f.open();await f.loaded('voice-a');const audio=f.card('voice-a').locator('audio'),src=await audio.getAttribute('src');await audio.evaluate(a=>a.play());gate.release();await f.loaded('voice-d');
   assert.equal(await audio.getAttribute('src'),src);assert.equal(await audio.evaluate(a=>a.paused),false,'automatic fourth voice cannot evict the playing first voice');assert.equal(await f.page.locator('[data-media-preview]').count(),3);
   f.state.messages.push(message('new-photo','image','e'));await f.refresh();await f.card('new-photo').scrollIntoViewIfNeeded();await f.card('new-photo').locator('[data-media-preview=image]').waitFor();assert.equal(await audio.evaluate(a=>a.paused),false);assert.equal(await audio.getAttribute('src'),src);
   const before=s.upstream.length;await f.page.evaluate(()=>{for(let n=0;n<20;n++)document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));});await settle(f.page);assert.equal(s.upstream.length,before,'four visible sources do not repeatedly evict/refetch each other');assert.equal(await f.page.locator('[data-media-preview]').count(),3);assert.equal(s.self429,0);assert.deepEqual(f.errors,[]);console.log('PASS playing audio survives fourth voice, scroll/new photo, cap3/no loop');
  }finally{gate.release();await f.context.close();}
 }
 {
  const s=server(),f=await fixture(browser,s,{width:1280,height:2000,messages:['a','b','c'].map(v=>message('voice-'+v,'voice',v))});try{
   await f.open();await Promise.all(['a','b','c'].map(v=>f.loaded('voice-'+v)));await f.page.evaluate(()=>Promise.all([...document.querySelectorAll('audio')].map(a=>a.play())));
   f.state.messages.push(message('voice-d','voice','d'));await f.refresh();await f.page.waitForFunction(()=>window.fixtureVisibleMedia.has('voice-d'));await settle(f.page);assert.equal(s.requests.some(r=>r.id==='voice-d'),false,'all three playing slots defer a fourth acquisition');
   await f.card('voice-a').locator('audio').evaluate(a=>a.pause());await f.loaded('voice-d');for(const id of ['voice-b','voice-c'])assert.equal(await f.card(id).locator('audio').evaluate(a=>a.paused),false);
   assert.equal(await f.page.locator('[data-media-preview]').count(),3);assert.equal(s.self429,0);assert.deepEqual(f.errors,[]);console.log('PASS all3 playing: fourth waits without fetch, pause resumes queue, other playback survives');
  }finally{await f.context.close();}
 }
 {
  const s=server(),f=await fixture(browser,s,{messages:Array.from({length:35},(_,i)=>message('history-'+i,'audio',String(i%9)))});try{
   await f.open();await f.loaded('history-34');assert.ok(s.upstream.length<=3,'initial viewport does not acquire the historical page');const initial=s.upstream.length;
   await f.page.locator('#chatRows').evaluate(n=>n.scrollTop=0);await f.loaded('history-0');assert.ok(s.upstream.length<=initial+3);assert.ok(s.upstream.length<35);assert.equal(s.self429,0);
   assert.equal(s.requests.some(r=>!['audio','voice'].includes(f.state.messages.find(m=>m.external_id===r.id)?.message_type)),false);assert.deepEqual(f.errors,[]);console.log('PASS history viewport bound: initial/scroll loads only visible audio');
  }finally{await f.context.close();}
 }
 for(const failure of ['network',429,404,503]){
  const s=server();s.networkFailure=failure==='network';s.status=failure==='network'?200:failure;const f=await fixture(browser,s);try{
   await f.open();await f.page.waitForFunction(state=>document.querySelector('[data-chat-attachment="voice-a"]')?.dataset.mediaPreviewState===state,failure===429?'rate_limited':failure===404?'unavailable':'retryable');
   const count=s.requests.length;await f.page.evaluate(()=>{for(let i=0;i<12;i++)document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));});await settle(f.page);assert.equal(s.requests.length,count,'failed/unknown acquisition waits for deliberate retry');
   if(failure===429){f.state.messages.push(message('other-channel','audio','b'));await f.refresh();await f.card('other-channel').scrollIntoViewIfNeeded();await settle(f.page);assert.equal(s.requests.length,count,'new source cannot bypass shared tenant cooldown');await f.action('voice-a','load','keyboard');assert.equal(s.requests.length,count);await f.page.evaluate(()=>{const now=Date.now;Date.now=()=>now()+18000;});}
   s.networkFailure=false;s.status=200;await f.card('voice-a').scrollIntoViewIfNeeded();assert.equal(await f.card('voice-a').locator('[data-media-open]').isVisible(),false,'failure never restores the redundant lower button');await f.action('voice-a','load',failure==='network'?'touch':failure===429?'keyboard':'mouse');await f.loaded('voice-a');assert.equal(s.paidOrSend,0);assert.deepEqual(f.errors,[]);console.log('PASS audio '+failure+': visible failure, no retry storm, message menu retry/cooldown');
  }finally{await f.context.close();}
 }
 {
  const s=server(),gate=deferred();s.gates.set('voice-a',gate);const f=await fixture(browser,s);try{
   const requested=f.page.waitForRequest(r=>new URL(r.url()).searchParams.get('auto_preview')==='1');await f.open();await requested;
   f.state.dialog={...f.state.dialog,lead_id:42,conversation_id:'new-synthetic@s.whatsapp.net'};f.state.messages=[{...message('replacement','voice','b'),source_lead_id:42,source_conversation_id:f.state.dialog.conversation_id}];await f.page.evaluate(route=>window.tenantOperator.openDialog(route),f.state.dialog);gate.release();await f.loaded('replacement');assert.equal(await f.card('voice-a').count(),0);assert.deepEqual(f.errors,[]);assert.equal(s.paidOrSend,0);console.log('PASS navigate/cancel: late old audio cannot cross dialog/source scope');
  }finally{gate.release();await f.context.close();}
 }
 {
  const s=server(),gate=deferred();s.gates.set('voice-a',gate);const f=await fixture(browser,s);try{
   const requested=f.page.waitForRequest(r=>new URL(r.url()).searchParams.get('auto_preview')==='1');await f.open();await requested;
   f.state.messages=[message('voice-a','voice','b')];const refresh=f.refresh();gate.release();await refresh;await f.loaded('voice-a');assert.equal(await f.card('voice-a').getAttribute('data-media-version'),'b'.repeat(64));assert.ok(s.requests.some(r=>r.automatic&&new URL(r.url).searchParams.get('expected_media_version')==='b'.repeat(64)));assert.deepEqual(f.errors,[]);console.log('PASS stale version response discarded; current exact source reacquired');
  }finally{gate.release();await f.context.close();}
 }
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
