/* Unchanged application HTML and real assets; every API/provider request mocked. */
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..');
const tenant='00000000-0000-0000-0000-000000000101',channel='00000000-0000-0000-0000-000000000201';
const dialog={provider:'connect',lead_id:41,channel_id:channel,conversation_id:'994501234567@s.whatsapp.net',customer_kind:'contact',customer_name:'Customer',channel_name:'WhatsApp',display_state:'human',state:'human',ai_availability:'stopped',available_actions:[],can_send:false,controls_allowed:true,agent_status:'manual',agent_mode:'manual',agent_id:'00000000-0000-0000-0000-000000000301',agent_name:'Assigned Helper'};
const profile={success:true,member:{tenant_id:tenant,telegram_id:33,role:'worker',permissions:['dialogs'],active:true},capabilities:{modules:{dialogs:true}}};
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':chromium.executablePath()),args:['--no-sandbox']});
 try{
  for(const [width,height,language] of [[390,844,'ru'],[1280,900,'ru']]){
   const page=await browser.newPage({viewport:{width,height}}),errors=[],calls=[];let mediaRequests=0,mediaStatus=503,mediaGate=null,releaseMedia=null;
   page.on('pageerror',error=>errors.push(error.message));page.on('dialog',()=>assert.fail('Unexpected native dialog'));
   // Production stores question templates in this tenant/employee scoped key.
   await page.addInitScript(({tenant})=>localStorage.setItem(`tenant.operator.copilot-question-templates.${tenant}:33`,JSON.stringify(Array.from({length:12},(_,i)=>({id:'template-'+i,name:'Saved question '+i,prompt:'Saved question'})))),{tenant});
   await page.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url()),name=url.pathname;calls.push({path:name,method:request.method(),body:request.postData(),url:request.url()});
    if(name==='/app')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8')});
    if(name.startsWith('/assets/')){const file=path.join(root,'docs',path.basename(name));return fs.existsSync(file)?route.fulfill({path:file}):route.fulfill({status:404,body:''});}
    if(name.endsWith('/crm/chat/attachment')){mediaRequests++;if(mediaGate)await mediaGate;if(mediaStatus!==200)return route.fulfill({status:mediaStatus,headers:{'Retry-After':'60'},json:{error:'Synthetic temporary file failure'}});const bytes=Buffer.alloc(44+32000);bytes.write('RIFF');bytes.writeUInt32LE(bytes.length-8,4);bytes.write('WAVEfmt ',8);bytes.writeUInt32LE(16,16);bytes.writeUInt16LE(1,20);bytes.writeUInt16LE(1,22);bytes.writeUInt32LE(8000,24);bytes.writeUInt32LE(16000,28);bytes.writeUInt16LE(2,32);bytes.writeUInt16LE(16,34);bytes.write('data',36);bytes.writeUInt32LE(32000,40);return route.fulfill({contentType:'audio/wav',body:bytes});}
    if(name==='/api/platform/me')return route.fulfill({json:profile});
    let result={};
    if(name.endsWith('/dialogs/history'))result={dialog,messages:[{id:'voice-row',external_id:'voice-1',source:'connect',source_lead_id:41,source_channel_id:channel,source_conversation_id:dialog.conversation_id,direction:'incoming',message_type:'audio',body:'',media_version:'a'.repeat(64),duration_seconds:2,media_size_bytes:0,analysis_state:'failed',happened_at:'2026-10-21T10:00:00Z'},{id:'doc-row',external_id:'doc-1',source:'connect',source_lead_id:41,source_channel_id:channel,source_conversation_id:dialog.conversation_id,direction:'incoming',message_type:'document',media_filename:'Second file.pdf',media_version:'a'.repeat(64),body:'',happened_at:'2026-10-21T10:00:30Z'},{id:'image-row',external_id:'image-1',source:'connect',source_lead_id:41,direction:'incoming',message_type:'image',media_version:'a'.repeat(64),body:''},{id:'video-row',external_id:'video-1',source:'connect',source_lead_id:41,direction:'incoming',message_type:'video',media_version:'a'.repeat(64),body:''},{id:'after-voice',external_id:'after',direction:'incoming',message_type:'text',body:'Message below the voice',happened_at:'2026-10-21T10:01:00Z'}],next_before:null};
    else if(name.endsWith('/dialogs'))result={dialogs:[{...dialog,last_message_id:'m1',last_message_direction:'incoming',last_message_text:'Customer question',last_message_at:'2026-10-21T10:00:00Z'}],channels:[{id:'connect:'+channel,name:'WhatsApp'}],agents:[],next_offset:null};
    else if(name.endsWith('/developer-marker'))result={enabled:false};
    else if(name.endsWith('/replies'))result={replies:[],consultations:[]};
    return route.fulfill({json:{success:true,result}});
   });
   try{
    await page.goto('https://crm.test/app?view=dialogs');await page.locator('[data-app-language]').selectOption(language);await page.locator('.operator-row').first().click();
    const article=page.locator('article[data-message-id="voice-row"]'),card=article.locator('[data-native-audio]'),menu=page.locator('.operator-reply-context-menu');await card.waitFor();
    assert.equal(await card.locator('summary').filter({hasText:'⋯'}).count(),0);
    const open=async()=>{await article.focus();await page.keyboard.press('Shift+F10');await menu.waitFor();};
    await open();assert.equal(await menu.locator('[data-reply-context-action=reply]').isDisabled(),true,'read-only dialog still has usable attachment actions');assert.equal(mediaRequests,0,'keyboard open never fetches a file');assert.equal(calls.some(c=>c.method==='POST'&&/audio-transcription|copilot|chat\/send/.test(c.path)),false);
    assert.equal(await menu.locator('[data-context-file-action=load]').isEnabled(),true);await page.keyboard.press('Escape');assert.equal(await article.evaluate(n=>document.activeElement===n),true);
    // A future/multi-file article must retain each host identity, not choose the first file.
    await page.evaluate(()=>{const article=document.querySelector('article[data-message-id="voice-row"]'),source=document.querySelector('article[data-message-id="doc-row"] [data-chat-attachment]');article.append(source);});
    await open();assert.equal(await menu.locator('[data-context-file]').count(),2);assert.equal(await menu.locator('[data-context-file]').nth(0).getAttribute('data-context-file'),'voice-1');assert.equal(await menu.locator('[data-context-file]').nth(1).getAttribute('data-context-file'),'doc-1');
    await menu.locator('[data-context-file="doc-1"] [data-context-file-action=load]').click();await page.waitForFunction(()=>document.querySelector('[data-chat-attachment="doc-1"]')?.dataset.mediaPreviewState==='retryable');
    assert.equal(mediaRequests,1);const fileCall=calls.filter(c=>c.path.endsWith('/crm/chat/attachment')).at(-1);assert.equal(new URL(fileCall.url).searchParams.get('message_id'),'doc-1','secondary file uses its own source identity');
    assert.match(await menu.locator('[data-context-file="doc-1"] [data-context-file-action=load]').innerText(),/Повтор|Retry|təkrar/);
    // A stale host/source cannot run an action from an already-open portal.
    await page.evaluate(()=>{document.querySelector('[data-chat-attachment="doc-1"]').dataset.mediaVersion='b'.repeat(64);});await page.waitForFunction(()=>!document.querySelector('.operator-reply-context-menu'));assert.equal(mediaRequests,1);
    await open();await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-language')));assert.equal(await menu.count(),0,'same-dialog render retires the portal');
    await article.focus();await page.keyboard.press('ContextMenu');await menu.waitFor();assert.equal(await menu.locator('[data-context-file]').count(),1,'fresh rendering restores the real per-row files');await page.keyboard.press('Escape');
    // Actual desktop right-click and touch hold share one entry point.
    await card.locator('.operator-audio-heading').click({button:'right'});await menu.waitFor();assert.equal(await menu.count(),1);await page.keyboard.press('Escape');
    await page.evaluate(()=>{const node=document.querySelector('[data-native-audio] .operator-audio-heading'),rect=node.getBoundingClientRect();window.menuPress={x:rect.x+30,y:rect.y+8};node.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerType:'touch',isPrimary:true,pointerId:7,clientX:window.menuPress.x,clientY:window.menuPress.y}));});await page.waitForTimeout(600);await menu.waitFor();await page.evaluate(()=>document.querySelector('[data-native-audio] .operator-audio-heading').dispatchEvent(new PointerEvent('pointerup',{bubbles:true,pointerType:'touch',isPrimary:true,pointerId:7})));await page.keyboard.press('Escape');assert.equal(mediaRequests,1);
    // The first selected action after a hold must reach the original file handler.
    const holdVoice=async()=>{await page.evaluate(()=>{const node=document.querySelector('[data-native-audio] .operator-audio-heading'),rect=node.getBoundingClientRect();node.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerType:'touch',isPrimary:true,pointerId:9,clientX:rect.x+30,clientY:rect.y+8}));});await page.waitForTimeout(600);await menu.waitFor();await page.evaluate(()=>document.querySelector('[data-native-audio] .operator-audio-heading').dispatchEvent(new PointerEvent('pointerup',{bubbles:true,pointerType:'touch',isPrimary:true,pointerId:9})));};
    await holdVoice();await menu.locator('[data-context-file-action=load]').click();await page.waitForFunction(()=>document.querySelector('[data-native-audio]')?.dataset.mediaPreviewState==='retryable');assert.equal(mediaRequests,2,'first post-hold Load is never eaten by trailing-click suppression');await page.keyboard.press('Escape');
    await holdVoice();await menu.locator('[data-context-file-action=download]').click();await page.waitForFunction(()=>document.querySelector('[data-native-audio] [data-media-download]')?.getAttribute('aria-busy')!=='true');assert.equal(mediaRequests,3,'first post-hold Download reaches the original actuator');await page.keyboard.press('Escape');
    await open();await page.keyboard.press('Tab');assert.equal(await menu.count(),0);await page.keyboard.press('ArrowDown');assert.equal(await menu.count(),0,'keyboard focus outside is not captured back by the retired menu');
    // Native control geometry is excluded even when inert retargets its pointer event.
    await page.evaluate(()=>{const node=document.querySelector('[data-native-audio]'),rect=node.querySelector('.operator-audio-controls').getBoundingClientRect();node.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerType:'touch',isPrimary:true,pointerId:8,clientX:rect.x+30,clientY:rect.y+20}));});await page.waitForTimeout(600);assert.equal(await menu.count(),0);
    await page.evaluate(()=>document.querySelector('#chatRows').dispatchEvent(new PointerEvent('pointercancel',{bubbles:true,pointerId:8})));
    for(const id of ['image-row','video-row']){const row=page.locator('article[data-message-id="'+id+'"]');await row.focus();await page.keyboard.press('ContextMenu');await menu.waitFor();assert.equal(await menu.locator('[data-context-file-action=load]').count(),1);assert.equal(await menu.locator('[data-context-file-action=download]').count(),1);assert.equal(await row.locator('[data-media-actions] summary').count(),0);await page.keyboard.press('Escape');}
    await open();await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-auth-invalid')));assert.equal(await menu.count(),0);
    assert.equal(calls.some(c=>/audio-transcription/.test(c.path)&&c.method!=='GET'),false,'menu never starts paid ASR');assert.deepEqual(errors,[]);
    console.log('PASS shared attachment menu '+width+': read-only, keyboard/right-click/hold, per-file identity, stale source/render/auth, inert player exclusion, no paid writes');
   }catch(error){console.error('PAGEERRORS',errors,await page.locator('#operatorConversationContent').innerText());await page.screenshot({path:'/tmp/voice-card-failure-'+width+'-'+language+'.png'});throw error;}finally{await page.close();}
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
