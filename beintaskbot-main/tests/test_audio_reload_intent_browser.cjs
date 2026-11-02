/* Legacy selected-audio hints share the visible queue; never a second GET. */
const assert=require('node:assert/strict');
const {fixture,server,message,launch}=require('./visible_media_fixture.cjs');
const KEY='tenant.audio-reload-intent.v1';
async function hint(f,changes={}){
 return f.page.evaluate(async({key,tenant,user,route,changes})=>{
  const hash=async value=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))].map(b=>b.toString(16).padStart(2,'0')).join('');
  const value={v:1,owner:await hash(JSON.stringify([tenant+':'+user,'worker',true,['dialogs'],null,true])),scope:await hash([route.provider,route.channel_id,route.conversation_id,route.lead_id].join(':')),source:await hash('voice-a'),version:'a'.repeat(64),expires_at:Date.now()+3600000,blocked:false,retry_until:0,...changes};sessionStorage.setItem(key,JSON.stringify(value));return value;
 },{key:KEY,tenant:f.state.tenant,user:f.state.user,route:f.state.dialog,changes});
}
(async()=>{const browser=await launch();try{
 for(const width of [390,1280]){
  const s=server(),f=await fixture(browser,s,{width});try{
   let saved;await f.open(async()=>{saved=await hint(f);});await f.loaded('voice-a');assert.equal(s.upstream.length,1,'old hint and visibility share one acquisition');assert.equal(s.requests.filter(r=>r.automatic).length,1);
   await f.page.reload();await f.loaded('voice-a');assert.equal(s.upstream.length,1,'reload uses server bytes, not an extra restore GET');assert.equal(await f.page.evaluate(key=>JSON.parse(sessionStorage.getItem(key)).expires_at,KEY),saved.expires_at,'automatic reads never slide old hint TTL');
   await f.page.evaluate(()=>document.querySelector('[data-view="profile"]').click());f.page.once('dialog',dialog=>dialog.accept());await f.page.locator('#logout').click();await f.page.waitForURL('**/login');assert.equal(await f.page.locator('audio[src]').count(),0,'actual logout removes all private preview URLs');
   f.state.tenant='00000000-0000-0000-0000-000000000102';f.state.authed=true;await f.open();await f.loaded('voice-a');assert.equal(await f.page.evaluate(key=>sessionStorage.getItem(key),KEY),null,'a different login cannot restore the old employee hint');assert.equal(s.upstream.length,2,'tenant cache identity never serves the previous tenant binary');
   assert.equal(s.paidOrSend,0);assert.deepEqual(f.errors,[]);console.log('PASS audio hint '+width+': one visible acquisition, immutable hint TTL, reload reuse, logout purge');
  }finally{await f.context.close();}
 }
 for(const changes of [{owner:'b'.repeat(64)},{version:'b'.repeat(64)},{expires_at:1}]){
  const s=server(),f=await fixture(browser,s);try{await f.open(()=>hint(f,changes));await f.loaded('voice-a');assert.equal(await f.page.evaluate(key=>sessionStorage.getItem(key),KEY),null,'wrong authority/version or expired hint is discarded');assert.equal(s.upstream.length,1);assert.equal(new URL(s.requests[0].url).searchParams.get('expected_tenant_id'),f.state.tenant);}finally{await f.context.close();}
 }
 {
  const s=server(),f=await fixture(browser,s);try{
   await f.open(()=>f.page.evaluate(key=>sessionStorage.setItem(key,'{"v":1,"url":"https://forbidden.invalid"}'),KEY));await f.loaded('voice-a');assert.equal(await f.page.evaluate(key=>sessionStorage.getItem(key),KEY),null);assert.equal(s.upstream.length,1);console.log('PASS malformed/expired/source/authority hints cannot steer acquisition');
  }finally{await f.context.close();}
 }
 {
  const s=server(),f=await fixture(browser,s);try{
   await f.open(()=>hint(f,{blocked:true,retry_until:Date.now()+60000}));await f.page.waitForFunction(()=>document.querySelector('[data-chat-attachment="voice-a"]')?.dataset.mediaPreviewState==='rate_limited');assert.equal(s.requests.length,0);assert.equal(await f.card('voice-a').locator('[data-media-open]').isVisible(),false);
   await f.action('voice-a','load','keyboard');assert.equal(s.requests.length,0,'menu retry respects persisted cooldown');await f.page.reload();await f.page.waitForFunction(()=>document.querySelector('[data-chat-attachment="voice-a"]')?.dataset.mediaPreviewState==='rate_limited');assert.equal(s.requests.length,0,'full reload cannot bypass legacy Retry-After');
   await f.page.evaluate(()=>{const now=Date.now;Date.now=()=>now()+61000;});await f.action('voice-a','load','touch');await f.loaded('voice-a');assert.equal(s.upstream.length,1);await f.page.waitForFunction(key=>JSON.parse(sessionStorage.getItem(key)).blocked===false,KEY);assert.equal(s.paidOrSend,0);console.log('PASS legacy unknown/failure waits for explicit menu retry after cooldown');
  }finally{await f.context.close();}
 }
 {
  const s=server(),f=await fixture(browser,s,{messages:[message(),...Array.from({length:25},(_,i)=>message('later-'+i,'voice','b'))]});try{
   await f.open(()=>hint(f));await f.loaded('later-24');assert.equal(s.requests.some(r=>r.id==='voice-a'),false);assert.equal(s.paidOrSend,0);assert.deepEqual(f.errors,[]);console.log('PASS selected hidden audio cannot bypass viewport queue');
  }finally{await f.context.close();}
 }
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
