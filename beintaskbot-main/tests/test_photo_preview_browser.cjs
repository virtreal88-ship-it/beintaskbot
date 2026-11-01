/* Unchanged application HTML and real assets; every API/provider request mocked. */
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..');
const tenant='00000000-0000-0000-0000-000000000101',channel='00000000-0000-0000-0000-000000000201';
const dialog={provider:'connect',lead_id:41,channel_id:channel,conversation_id:'994501234567@s.whatsapp.net',customer_kind:'contact',customer_name:'Customer',channel_name:'WhatsApp',display_state:'human',state:'human',ai_availability:'stopped',available_actions:[],can_send:true,controls_allowed:true,agent_status:'manual',agent_mode:'manual',agent_id:'00000000-0000-0000-0000-000000000301',agent_name:'Assigned Helper'};
const profile={success:true,member:{tenant_id:tenant,telegram_id:33,role:'worker',permissions:['dialogs'],active:true},capabilities:{modules:{dialogs:true}}};
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':chromium.executablePath()),args:['--no-sandbox']});
 try{
  for(const [width,height,language] of [[320,760,'ru'],[390,844,'az'],[1280,900,'ru']]){
   const page=await browser.newPage({viewport:{width,height}}),errors=[],calls=[];let mediaRequests=0,mediaStatus=200,downloadGate=null,releaseDownloads;const cached=new Set(['cached-photo']),png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aHZkAAAAASUVORK5CYII=','base64');
   page.on('pageerror',error=>errors.push(error.message));page.on('dialog',()=>assert.fail('Unexpected native dialog'));
   // Production stores question templates in this tenant/employee scoped key.
   await page.addInitScript(({tenant})=>localStorage.setItem(`tenant.operator.copilot-question-templates.${tenant}:33`,JSON.stringify(Array.from({length:12},(_,i)=>({id:'template-'+i,name:'Saved question '+i,prompt:'Saved question'})))),{tenant});
   await page.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url()),name=url.pathname;calls.push({path:name,method:request.method(),body:request.postData(),url:request.url()});
    if(name==='/app')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8')});
    if(name.startsWith('/assets/')){const file=path.join(root,'docs',path.basename(name));return fs.existsSync(file)?route.fulfill({path:file}):route.fulfill({status:404,body:''});}
    if(name.endsWith('/crm/chat/attachment')){const id=url.searchParams.get('message_id');if(url.searchParams.get('cache_only')==='1')return cached.has(id)?route.fulfill({contentType:'image/png',body:png}):route.fulfill({status:204,body:''});mediaRequests++;if(downloadGate&&url.searchParams.get('download')==='1')await downloadGate;if(mediaStatus!==200)return route.fulfill({status:mediaStatus,headers:{'Retry-After':'60'},json:{error:'Synthetic file failure'}});cached.add(id);return route.fulfill({contentType:'image/png',body:png});}
    if(name==='/api/platform/me')return route.fulfill({json:profile});
    let result={};
    if(name.endsWith('/dialogs/history'))result={dialog,messages:['cached-photo','thumbnail-photo','placeholder-photo'].map((id,index)=>({id,external_id:id,source:'connect',source_lead_id:41,source_channel_id:channel,source_conversation_id:dialog.conversation_id,direction:'incoming',message_type:'image',media_version:'a'.repeat(64),body:'',media_thumbnail:index===1?'data:image/png;base64,'+png.toString('base64'):'',analysis_state:'failed',happened_at:'2026-10-21T10:00:00Z'})),next_before:null};
    else if(name.endsWith('/dialogs'))result={dialogs:[{...dialog,last_message_id:'m1',last_message_direction:'incoming',last_message_text:'Customer question',last_message_at:'2026-10-21T10:00:00Z'}],channels:[{id:'connect:'+channel,name:'WhatsApp'}],agents:[],next_offset:null};
    else if(name.endsWith('/developer-marker'))result={enabled:false};
    else if(name.endsWith('/replies'))result={replies:[],consultations:[]};
    return route.fulfill({json:{success:true,result}});
   });
   try{
    await page.goto('https://crm.test/app?view=dialogs');await page.locator('[data-app-language]').selectOption(language);await page.locator('.operator-row').first().click();
    const card=id=>page.locator('article[data-message-id="'+id+'"] [data-chat-attachment]'),cachedCard=card('cached-photo'),thumb=card('thumbnail-photo'),empty=card('placeholder-photo');
    await cachedCard.scrollIntoViewIfNeeded();await cachedCard.locator('[data-media-preview=image]').waitFor();assert.equal(await cachedCard.locator('[data-media-open]').isVisible(),false,'cached bytes show an actual photo without a redundant Load action');assert.equal(mediaRequests,0,'automatic cached-only reads never start provider downloads');
    await thumb.scrollIntoViewIfNeeded();await page.waitForFunction(()=>document.querySelector('[data-chat-attachment="thumbnail-photo"]')?.dataset.mediaPreviewState==='unloaded');assert.equal(await thumb.locator('[data-media-thumbnail]').isVisible(),true);assert.match(await thumb.locator('[data-media-thumbnail]').evaluate(n=>getComputedStyle(n).filter),/blur/);assert.equal(await thumb.locator('.operator-media-analysis').isVisible(),false,'AI status is a separate collapsed detail');
    await empty.scrollIntoViewIfNeeded();await page.waitForFunction(()=>document.querySelector('[data-chat-attachment="placeholder-photo"]')?.dataset.mediaPreviewState==='unloaded');assert.equal(await empty.locator('[data-media-thumbnail]').count(),0);assert.equal(await empty.locator('[data-photo-placeholder]').isVisible(),true,'missing thumbnail uses an honest photo placeholder');
    assert.equal(mediaRequests,0);assert.equal(calls.some(c=>/copilot|audio-transcription|chat\/send/.test(c.path)&&c.method==='POST'),false);
    await page.screenshot({path:'/tmp/photo-preview-unloaded-'+width+'-'+language+'.png'});
    // Holding the clickable preview opens actions; releasing it cannot load the image.
    const session=await page.context().newCDPSession(page),surface=empty.locator('[data-photo-preview]');if(width<=900){const box=await surface.boundingBox(),x=Math.round(box.x+box.width/2),y=Math.round(box.y+30);await session.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{id:1,x,y}]});await page.waitForTimeout(650);await session.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});}else await surface.click({button:'right'});
    const menu=page.locator('.operator-reply-context-menu');await menu.waitFor();assert.equal(mediaRequests,0,'opening the photo menu never starts loading');await page.keyboard.press('Escape');
    mediaStatus=429;await surface.click();await page.waitForFunction(()=>document.querySelector('[data-chat-attachment="placeholder-photo"]')?.dataset.mediaPreviewState==='rate_limited');assert.equal(mediaRequests,1);await surface.click();assert.equal(mediaRequests,1,'preview respects Retry-After');
    await page.evaluate(()=>{const now=Date.now;Date.now=()=>now()+61000;});mediaStatus=200;await surface.click();await empty.locator('[data-media-preview=image]').waitFor();assert.equal(mediaRequests,2);assert.equal(await surface.isVisible(),false);
    await thumb.scrollIntoViewIfNeeded();await thumb.locator('[data-photo-preview]').click();await thumb.locator('[data-media-preview=image]').waitFor();assert.equal(mediaRequests,3);
    await page.evaluate(()=>{window.originalPhoto=document.querySelector('[data-chat-attachment="thumbnail-photo"] [data-media-preview]');document.dispatchEvent(new CustomEvent('tenant-language'));});assert.equal(await thumb.locator('[data-media-preview]').evaluate(n=>n===window.originalPhoto),true,'same-source refresh keeps the displayed image');assert.equal(mediaRequests,3);
    if(width===390){
      downloadGate=new Promise(resolve=>{releaseDownloads=resolve;});const beforeDownloads=mediaRequests;await cachedCard.locator('[data-media-download]').evaluate(n=>n.click());await thumb.locator('[data-media-download]').evaluate(n=>n.click());await page.waitForFunction(()=>[...document.querySelectorAll('[data-media-download]')].filter(n=>n.getAttribute('aria-busy')==='true').length===2);
      cached.add('queued-photo');await page.evaluate(()=>{const source=document.querySelector('[data-chat-attachment="cached-photo"]'),node=source.cloneNode(true);node.dataset.chatAttachment='queued-photo';node.dataset.mediaVersion='b'.repeat(64);node.querySelector('[data-media-preview]')?.remove();node.querySelector('[data-media-open]').hidden=false;delete node.dataset.mediaPreviewState;document.querySelector('#chatRows').append(node);document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));});
      const queued=page.locator('[data-chat-attachment="queued-photo"]');await queued.scrollIntoViewIfNeeded();await page.waitForTimeout(60);assert.equal(calls.some(c=>c.path.endsWith('/crm/chat/attachment')&&new URL(c.url).searchParams.get('message_id')==='queued-photo'),false,'cache probe waits while both Connect slots are used');releaseDownloads();await queued.locator('[data-media-preview=image]').waitFor();assert.equal(mediaRequests,beforeDownloads+2,'releasing download slots resumes only the cached photo read');
    }
    const beforeReload=mediaRequests;
    await page.reload();await thumb.waitFor();await thumb.scrollIntoViewIfNeeded();await thumb.locator('[data-media-preview=image]').waitFor();assert.equal(mediaRequests,beforeReload,'full reload restores already cached bytes without provider replay');
    const geometry=await thumb.boundingBox();assert.ok(geometry.x>=0&&geometry.x+geometry.width<=width+1);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.screenshot({path:'/tmp/photo-preview-'+width+'-'+language+'.png'});assert.deepEqual(errors,[]);console.log('PASS photo preview '+width+' '+language+': cached bytes, real blur/placeholder, touch menu without load, 429, explicit load, rerender/full reload, no AI/provider replay');
   }catch(error){await page.screenshot({path:'/tmp/voice-card-failure-'+width+'-'+language+'.png'});throw error;}finally{await page.close();}
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
