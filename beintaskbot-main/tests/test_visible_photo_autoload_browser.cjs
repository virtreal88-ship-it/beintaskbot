/* Full application HTML/CSS, synthetic authenticated HTTP fixtures only. */
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..'),tenant='00000000-0000-0000-0000-000000000101',channel='00000000-0000-0000-0000-000000000201';
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aHZkAAAAASUVORK5CYII=','base64');
const base={provider:'connect',lead_id:41,channel_id:channel,conversation_id:'synthetic@s.whatsapp.net',customer_kind:'contact',customer_name:'Synthetic customer',channel_name:'WhatsApp',display_state:'human',state:'human',ai_availability:'stopped',available_actions:[],can_send:true,controls_allowed:true,agent_status:'manual',agent_mode:'manual',agent_id:'00000000-0000-0000-0000-000000000301',agent_name:'Assigned Helper'};
const message=(id='photo',version='a',kind='image')=>({id,external_id:id,source:'connect',source_lead_id:41,source_channel_id:channel,source_conversation_id:base.conversation_id,direction:'incoming',message_type:kind,media_version:version.repeat(64),body:'',analysis_state:'not_requested',happened_at:'2026-10-21T10:00:00Z'});
const cache=new Set(),flights=new Map(),requests=[],upstream=[];let responseStatus=200,upstreamGate=null,automaticActive=0,enforceAutomaticSlot=false;
const cacheKey=(url)=>[url.searchParams.get('expected_tenant_id'),url.searchParams.get('channel_id'),url.searchParams.get('conversation_id'),url.searchParams.get('message_id'),url.searchParams.get('expected_media_version')].join('|');
async function fixture(browser,width=390,withoutLocks=false){
 const context=await browser.newContext({viewport:{width,height:844}}),page=await context.newPage(),errors=[];
 if(withoutLocks)await context.addInitScript(()=>Object.defineProperty(navigator,'locks',{value:undefined,configurable:true}));
 page.setDefaultTimeout(10000);
 const state={authed:true,tenant,user:33,dialog:{...base},messages:[message()]};page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),name=url.pathname;
  if(name==='/app')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8')});
  if(name==='/login')return route.fulfill({contentType:'text/html',body:'<button id="fixtureLogin" onclick="fetch(\'/fixture/login\',{method:\'POST\'}).then(()=>location.href=\'/app?view=dialogs\')">Sign in</button>'});
  if(name==='/fixture/login'){state.authed=true;return route.fulfill({json:{success:true}});}
  if(name.startsWith('/assets/')){const file=path.join(root,'docs',path.basename(name));return fs.existsSync(file)?route.fulfill({path:file}):route.fulfill({status:404,body:''});}
  if(name==='/api/platform/me')return route.fulfill({json:state.authed?{success:true,member:{tenant_id:state.tenant,telegram_id:state.user,role:'worker',permissions:['dialogs'],active:true},capabilities:{modules:{dialogs:true}}}:{success:false}});
  if(name==='/api/platform/logout'){state.authed=false;return route.fulfill({json:{success:true}});}
  if(name.endsWith('/crm/chat/attachment')){
   requests.push({url:url.href,auto:url.searchParams.get('auto_preview')==='1',cache:url.searchParams.get('cache_only')==='1',id:url.searchParams.get('message_id')});
   assert.equal(state.authed,true);assert.equal(url.searchParams.get('expected_tenant_id'),state.tenant);assert.equal(url.searchParams.get('expected_user_id'),String(state.user));
   const key=cacheKey(url);
   if(url.searchParams.get('cache_only')==='1')return cache.has(key)?route.fulfill({contentType:'image/png',body:png}):route.fulfill({status:204,body:''});
   if(!flights.has(key)){
    const automatic=url.searchParams.get('auto_preview')==='1';
    if(automatic&&enforceAutomaticSlot&&automaticActive>=1)return route.fulfill({status:429,headers:{'Retry-After':'1'},json:{error:'Synthetic background slot busy'}});
    if(automatic)automaticActive++;
    flights.set(key,(async()=>{try{upstream.push(key);if(upstreamGate)await upstreamGate;if(responseStatus===200)cache.add(key);return responseStatus;}finally{if(automatic)automaticActive--;}})());
   }
   const status=await flights.get(key);flights.delete(key);
   return status===200?route.fulfill({contentType:'image/png',body:png}):route.fulfill({status,headers:{'Retry-After':'17'},json:{error:'Synthetic upstream failure'}});
  }
  assert.equal(/copilot|audio-transcription|chat\/send/.test(name)&&req.method()==='POST',false,'photo opening never dispatches AI/ASR or messages');
  let result={};
  if(name.endsWith('/dialogs/history'))result={dialog:state.dialog,messages:state.messages,next_before:null};
  else if(name.endsWith('/dialogs'))result={dialogs:[{...state.dialog,last_message_id:'photo',last_message_direction:'incoming',last_message_text:'Synthetic photo',last_message_at:'2026-10-21T10:00:00Z'}],channels:[{id:'connect:'+state.dialog.channel_id,name:'WhatsApp'}],agents:[],next_offset:null};
  else if(name.endsWith('/developer-marker'))result={enabled:false};else if(name.endsWith('/replies'))result={replies:[],consultations:[]};
  return route.fulfill({json:{success:true,result}});
 });
 const open=async()=>{await page.goto('https://crm.test/app?view=dialogs');await page.locator('.operator-row').first().click();};
 const photo=id=>page.locator('[data-chat-attachment="'+id+'"]');
 return {page,context,state,errors,open,photo};
}
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
 try{
  for(const width of [320,390,1280]){
   cache.clear();requests.length=0;upstream.length=0;responseStatus=200;
   const f=await fixture(browser,width);
   try{
    await f.open();await f.photo('photo').locator('[data-media-preview=image]').waitFor();
    assert.equal(upstream.length,1);assert.equal(requests.filter(r=>r.auto).length,1);assert.equal(requests.filter(r=>r.cache).length,1);
    assert.equal(await f.photo('photo').locator('[data-media-open]').isVisible(),false);
    await f.page.evaluate(()=>{for(let n=0;n<10;n++)document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));document.dispatchEvent(new CustomEvent('tenant-language'));});
    assert.equal(upstream.length,1,'rerender events do not repeat acquisition');
    // A separate browser has no tab cache/restore hint. A hot server serves bytes directly.
    const warm=await fixture(browser,width);try{await warm.open();await warm.photo('photo').locator('[data-media-preview=image]').waitFor();assert.equal(upstream.length,1);}finally{await warm.context.close();}
    // TTL expiry/process-memory loss + clean app restart require one new upstream acquisition.
    cache.clear();const cold=await fixture(browser,width);try{await cold.open();await cold.photo('photo').locator('[data-media-preview=image]').waitFor();assert.equal(upstream.length,2);assert.equal(await cold.page.evaluate(()=>sessionStorage.getItem('tenant.audio-reload-intent.v1')),null);}finally{await cold.context.close();}
    await f.page.evaluate(()=>document.querySelector('[data-view="profile"]').click());
    f.page.once('dialog',d=>d.accept());await f.page.locator('#logout').click();await f.page.locator('#fixtureLogin').waitFor();
    assert.equal(await f.page.locator('[data-media-preview]').count(),0,'logout removes private preview DOM');
    cache.clear();await f.page.locator('#fixtureLogin').click();await f.page.waitForURL('**/app?view=dialogs');
    if(await f.page.locator('.operator-row').first().isVisible())await f.page.locator('.operator-row').first().click();
    await f.photo('photo').locator('[data-media-preview=image]').waitFor();assert.equal(upstream.length,3,'new login restores visible cold photo automatically');
    await f.page.evaluate(()=>{const source=document.querySelector('[data-chat-attachment="photo"]');for(const kind of ['audio','video','document','image']){const node=source.cloneNode(true);node.dataset.chatAttachment='hidden-'+kind;node.dataset.mediaKind=kind;node.querySelector('[data-media-preview]')?.remove();if(kind==='image')node.hidden=true;document.querySelector('#chatRows').append(node);}document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));});
    await f.page.waitForTimeout(60);assert.equal(requests.some(r=>r.id.startsWith('hidden-')),false,'hidden photos and other media do not auto-load');
    await f.page.evaluate(()=>{document.querySelector('#view-dialogs').classList.remove('active');document.querySelector('[data-chat-attachment="hidden-image"]').hidden=false;document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));});
    await f.page.waitForTimeout(60);assert.equal(requests.some(r=>r.id.startsWith('hidden-')),false,'inactive app view cannot auto-load');
    await f.page.evaluate(()=>document.querySelector('#view-dialogs').classList.add('active'));
    await f.photo('hidden-image').scrollIntoViewIfNeeded();await f.photo('hidden-image').locator('[data-media-preview=image]').waitFor();
    assert.equal(requests.some(r=>r.id.startsWith('hidden-')&&r.id!=='hidden-image'),false);
    assert.deepEqual(f.errors,[]);assert.equal(await f.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    console.log('PASS visible photo '+width+': cold/hot cache, new context, app restart, logout/login, no duplicate/AI/ASR');
   }finally{await f.context.close();}
  }
  cache.clear();requests.length=0;upstream.length=0;responseStatus=200;enforceAutomaticSlot=true;
  upstreamGate=new Promise(resolve=>setTimeout(resolve,150));
  const fallback=await fixture(browser,1280,true);fallback.state.messages=[message('photo-a'),message('photo-b','b')];
  try{
    await fallback.open();assert.equal(await fallback.page.evaluate(()=>!!navigator.locks),false);
    await Promise.all([fallback.photo('photo-a').locator('[data-media-preview=image]').waitFor(),fallback.photo('photo-b').locator('[data-media-preview=image]').waitFor()]);
    assert.equal(requests.filter(r=>r.auto).length,2);assert.equal(upstream.length,2);
    assert.equal(await fallback.page.locator('[data-media-preview-state=rate_limited]').count(),0);
    assert.deepEqual(fallback.errors,[]);console.log('PASS no Web Locks: two visible cold photos serialize against background cap1, no self429/manual retry');
  }finally{upstreamGate=null;enforceAutomaticSlot=false;await fallback.context.close();}
  // Two independent contexts racing one source model the shared endpoint's singleflight.
  cache.clear();upstream.length=0;responseStatus=200;let release;upstreamGate=new Promise(resolve=>release=resolve);
  const a=await fixture(browser),b=await fixture(browser);try{await Promise.all([a.open(),b.open()]);await a.page.waitForFunction(()=>document.querySelector('[data-media-open]')?.getAttribute('aria-busy')==='true');while(!upstream.length)await a.page.waitForTimeout(10);await a.page.waitForTimeout(80);assert.equal(upstream.length,1);release();await Promise.all([a.photo('photo').locator('[data-media-preview=image]').waitFor(),b.photo('photo').locator('[data-media-preview=image]').waitFor()]);}finally{upstreamGate=null;release();await a.context.close();await b.context.close();}
  cache.clear();requests.length=0;upstream.length=0;upstreamGate=new Promise(resolve=>release=resolve);
  const replaced=await fixture(browser);try{
    await replaced.open();await replaced.page.waitForFunction(()=>document.querySelector('[data-media-open]')?.getAttribute('aria-busy')==='true');
    while(!upstream.length)await replaced.page.waitForTimeout(10);
    replaced.state.messages=[message('replacement','b')];await replaced.page.evaluate(()=>window.tenantOperator.openDialog(window.tenantOperator.currentDialog));
    await replaced.photo('replacement').waitFor();release();await replaced.photo('replacement').locator('[data-media-preview=image]').waitFor();
    assert.equal(await replaced.photo('photo').count(),0,'late bytes from a replaced source cannot reappear');assert.equal(upstream.length,2);
    console.log('PASS photo source replacement: abort and stale response fencing');
  }finally{upstreamGate=null;release();await replaced.context.close();}
  for(const status of [429,404]){
   cache.clear();requests.length=0;upstream.length=0;responseStatus=status;const f=await fixture(browser);
   try{
    await f.open();await f.page.waitForFunction(status=>document.querySelector('[data-chat-attachment="photo"]')?.dataset.mediaPreviewState===(status===429?'rate_limited':'unavailable'),status);
    const count=requests.length;await f.page.evaluate(()=>{for(let n=0;n<12;n++)document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));document.dispatchEvent(new CustomEvent('tenant-language'));});
    assert.equal(upstream.length,1);assert.equal(requests.length,count,'failed auto load cannot loop through rerender');
    if(status===429){
      await f.page.evaluate(()=>{const old=document.querySelector('[data-chat-attachment="photo"]'),node=old.cloneNode(true);node.dataset.chatAttachment='other-photo';node.dataset.channelId='00000000-0000-0000-0000-000000000202';node.dataset.mediaVersion='b'.repeat(64);delete node.dataset.mediaPreviewState;node.querySelector('[data-media-open]').disabled=false;document.querySelector('#chatRows').append(node);document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));});
      await f.photo('other-photo').scrollIntoViewIfNeeded();await f.page.waitForTimeout(80);assert.equal(requests.length,count,'new tiles cannot bypass tenant cooldown');
      await f.photo('photo').locator('[data-media-open]').evaluate(n=>n.click());assert.equal(requests.length,count,'explicit retry also respects Retry-After');
      await f.page.evaluate(()=>{const now=Date.now;Date.now=()=>now()+18000;});
    }
    responseStatus=200;await f.photo('photo').scrollIntoViewIfNeeded();await f.photo('photo').locator('[data-media-open]').click();await f.photo('photo').locator('[data-media-preview=image]').waitFor();
    assert.equal(upstream.filter(key=>key.includes('|photo|')).length,2,'after failure a deliberate safe retry remains available');console.log('PASS photo auto '+status+': persistent failure, no loop, tenant cooldown, explicit retry');
   }finally{await f.context.close();}
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
