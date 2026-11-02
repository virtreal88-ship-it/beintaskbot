const assert=require('node:assert/strict');
const {fixture,server,message,launch,deferred}=require('./visible_media_fixture.cjs');
(async()=>{const browser=await launch();try{
 for(const width of [320,390,1280])for(const language of ['ru','az']){
  const states=['sent','delivered','read','pending','retrying','failed','unknown'],s=server();
  const rows=states.map(state=>({...message('status-'+state,'text'),direction:'outgoing',body:'Long message '+('слово söz '.repeat(35)),delivery_state:state,author_type:'operator',author_name:'A long operator name '.repeat(5)}));
  rows.push({...message('incoming','text'),body:'Incoming copied text',delivery_state:'read'},
   {...message('unproven','text'),direction:'outgoing',body:'No receipt',read:true,delivered:true},
   {...message('voice','audio'),has_transcription:true,audio_transcript:'Saved voice transcript'});
  const f=await fixture(browser,s,{width,messages:rows});try{
   await f.context.addInitScript(()=>{window.copiedText='';Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>window.copiedText=text}});});
   await f.open(()=>f.page.locator('[data-app-language]').selectOption(language));
   const colors={};
   for(const state of states){
    const article=f.page.locator('article[data-message-id="status-'+state+'"]');await article.scrollIntoViewIfNeeded();
    const copy=article.locator('[data-copy-message]');assert.equal(await copy.count(),1);assert.equal(await copy.evaluate(n=>!!n.closest('footer')),true);assert.equal(await article.locator('[data-user-content] [data-copy-message]').count(),0);
    const rect=await article.evaluate(node=>{const r=n=>{const x=n.getBoundingClientRect();return {left:x.left,right:x.right,top:x.top,bottom:x.bottom,width:x.width,height:x.height};};return {article:r(node),copy:r(node.querySelector('[data-copy-message]')),body:r(node.querySelector('[data-user-content]')),time:r(node.querySelector('time')),actions:r(node.querySelector('.operator-message-actions'))};});
    assert.ok(rect.copy.top>=rect.body.bottom-1);assert.ok(rect.copy.right<=rect.article.right&&rect.article.right-rect.copy.right<=24,JSON.stringify(rect));assert.ok(rect.copy.bottom<=rect.article.bottom);assert.ok(rect.copy.left>=rect.time.right-1||rect.copy.top>=rect.time.bottom-1||rect.time.top>=rect.copy.bottom-1,'copy does not overlap timestamp');if(width<900)assert.ok(rect.copy.width>=44&&rect.copy.height>=44);
    assert.equal(await article.locator('footer time').count(),1);
    const indicator=article.locator('[data-delivery-state]');assert.equal(await indicator.getAttribute('data-delivery-state'),state);
    if(['sent','delivered','read'].includes(state)){
     assert.equal(await indicator.locator('svg path').count(),state==='sent'?1:2);assert.equal((await indicator.innerText()).trim(),'');assert.ok(await indicator.getAttribute('aria-label'));assert.equal(await indicator.getAttribute('title'),await indicator.getAttribute('aria-label'));colors[state]=await indicator.evaluate(n=>getComputedStyle(n).color);
    }else{assert.equal(await indicator.locator('svg').count(),0);assert.ok((await indicator.innerText()).trim());}
   }
   assert.equal(colors.sent,colors.delivered);assert.notEqual(colors.read,colors.delivered,'only explicit read is blue');
   if(width===320&&language==='ru'){await f.page.locator('article[data-message-id=status-read]').scrollIntoViewIfNeeded();await f.page.screenshot({path:'/tmp/message-footer-320-ru.png'});}
   for(const id of ['incoming','unproven'])assert.equal(await f.page.locator('article[data-message-id="'+id+'"] .operator-delivery-ticks').count(),0);
   const incoming=f.page.locator('article[data-message-id=incoming]');await incoming.scrollIntoViewIfNeeded();await incoming.locator('[data-copy-message]').focus();await f.page.keyboard.press('Enter');assert.equal(await f.page.evaluate(()=>window.copiedText),'Incoming copied text');assert.ok((await incoming.locator('[data-copy-status]').innerText()).trim());assert.equal(await f.page.locator('.operator-reply-context-menu').count(),0);
   await f.card('voice').scrollIntoViewIfNeeded();await f.loaded('voice');assert.equal(await f.page.locator('article[data-message-id=voice] [data-copy-message]').count(),1);await f.page.locator('article[data-message-id=voice] [data-copy-message]').click();assert.equal(await f.page.evaluate(()=>window.copiedText),'Saved voice transcript');
   await f.page.evaluate(()=>{window.keptMessageAudio=document.querySelector('[data-chat-attachment=voice] audio');window.keptMessageAudio.currentTime=.2;});f.state.messages.find(m=>m.id==='status-delivered').delivery_state='read';await f.refresh();assert.equal(await f.page.locator('article[data-message-id=status-delivered] .operator-delivery-ticks').getAttribute('data-delivery-state'),'read');assert.equal(await f.page.evaluate(()=>document.querySelector('[data-chat-attachment=voice] audio')===window.keptMessageAudio),true,'saved receipt refresh keeps the existing player');
   assert.doesNotMatch(await f.card('voice').innerText(),/Файл загружен|Səs yükləndi|Audio loaded/);await f.page.locator('article[data-message-id=voice]').focus();await f.page.keyboard.press('Shift+F10');const menu=f.page.locator('.operator-reply-context-menu');await menu.waitFor();assert.doesNotMatch(await menu.innerText(),/Файл загружен|Səs yükləndi|Audio loaded/);assert.equal(await menu.locator('[data-context-file-action=load]').isVisible(),false);await f.page.keyboard.press('Escape');
   assert.equal(await f.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.equal(s.paidOrSend,0);assert.deepEqual(f.errors,[]);assert.equal(f.state.navigations,1);console.log('PASS footer '+width+' '+language+': copy bottom-right/keyboard, all receipts, blue onlyread, incoming/no inference, player/popup no success label');
  }finally{await f.context.close();}
 }
 // Exercise the real list -> appendNewMessages/history path, never openDialog.
 for(const width of [390,1280]){
  const s=server(),out={...message('incremental','text'),direction:'outgoing',author_type:'operator',body:'Preserve this text selection',delivery_state:'sent'},f=await fixture(browser,s,{width,messages:[out,message('voice-a')]});try{
   // A valid snapshot without a read cursor isolates status refresh from the
   // separate mark-read/list-refresh lifecycle. No extra poll is introduced.
   f.state.readThroughId=null;await f.open();await f.loaded('voice-a');await f.page.evaluate(async()=>{
    const article=document.querySelector('article[data-message-id=incremental]'),body=article.querySelector('[data-user-content]'),copy=article.querySelector('[data-copy-message]'),audio=document.querySelector('audio');
    const range=document.createRange();range.selectNodeContents(body);getSelection().removeAllRanges();getSelection().addRange(range);copy.focus({preventScroll:true});audio.currentTime=.5;
    window.keptIncremental={article,body,copy,audio,selection:getSelection().toString()};await audio.play();
   });
   for(const [index,state] of ['delivered','read','pending','failed','unknown'].entries()){
    f.state.messages[0].delivery_state=state;const id='receipt-update-'+index;f.state.messages.push({...message(id,'text'),body:'New cached incoming message'});f.state.lastMessageId=id;
    await f.page.evaluate(()=>window.tenantOperator.refresh());await f.page.waitForFunction(state=>document.querySelector('article[data-message-id=incremental] [data-delivery-state]')?.dataset.deliveryState===state,state);
    assert.equal(await f.page.evaluate(()=>{const k=window.keptIncremental;return document.querySelector('article[data-message-id=incremental]')===k.article&&k.article.querySelector('[data-user-content]')===k.body&&k.article.querySelector('[data-copy-message]')===k.copy&&document.activeElement===k.copy&&getSelection().toString()===k.selection&&document.querySelector('audio')===k.audio&&k.audio.currentTime>=.5&&!k.audio.paused;}),true,'incremental receipt changes only the indicator, preserving text/focus/playing player');
    const indicator=f.page.locator('article[data-message-id=incremental] [data-delivery-state]');assert.equal(await indicator.locator('svg').count(),['delivered','read'].includes(state)?1:0);
   }
   const originalChannel=f.state.messages[0].source_channel_id;f.state.messages[0].source_channel_id='00000000-0000-0000-0000-000000000299';f.state.messages[0].delivery_state='read';f.state.messages.push({...message('wrong-source-update','text'),body:'New message'});f.state.lastMessageId='wrong-source-update';await f.page.evaluate(()=>window.tenantOperator.refresh());await f.page.locator('article[data-message-id=wrong-source-update]').waitFor();assert.equal(await f.page.locator('article[data-message-id=incremental] [data-delivery-state]').getAttribute('data-delivery-state'),'unknown','foreign source cannot turn an existing receipt blue');
   f.state.messages[0].source_channel_id=originalChannel;delete f.state.messages[0].delivery_state;f.state.messages.push({...message('missing-receipt-update','text'),body:'New message'});f.state.lastMessageId='missing-receipt-update';await f.page.evaluate(()=>window.tenantOperator.refresh());await f.page.locator('article[data-message-id=missing-receipt-update]').waitFor();assert.equal(await f.page.locator('article[data-message-id=incremental] [data-delivery-state]').count(),0,'missing persisted state does not retain stale ticks');
   assert.equal(s.upstream.length,1);assert.equal(s.paidOrSend,0);assert.equal(f.state.navigations,1);assert.deepEqual(f.errors,[]);console.log('PASS incremental receipts '+width+': sent/delivered/read/pending/failed/unknown, exact source/removal; selection/focus/player stay unchanged');
  }finally{await f.context.close();}
 }
 // Retain meaningful loading and error feedback while removing only success.
 for(const status of [200,503]){
  const s=server(),gate=deferred();s.status=status;s.gates.set('voice-a',gate);const f=await fixture(browser,s);try{
   await f.open();await f.page.waitForFunction(()=>document.querySelector('[data-native-audio]')?.dataset.audioState==='loading');assert.ok((await f.card('voice-a').locator('[data-media-load-status]').innerText()).trim());gate.release();
   if(status===200){await f.loaded('voice-a');assert.equal((await f.card('voice-a').locator('[data-media-load-status]').innerText()).trim(),'');}
   else{await f.page.waitForFunction(()=>document.querySelector('[data-native-audio]')?.dataset.audioState==='retryable');assert.ok((await f.card('voice-a').locator('[data-media-load-status]').innerText()).trim());}
   assert.equal(s.paidOrSend,0);
  }finally{gate.release();await f.context.close();}
 }
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
