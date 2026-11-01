/* Unchanged application HTML and real assets; every API/provider request mocked. */
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..');
const tenant='00000000-0000-0000-0000-000000000101',channel='00000000-0000-0000-0000-000000000201';
const dialog={provider:'connect',lead_id:41,channel_id:channel,conversation_id:'994501234567@s.whatsapp.net',customer_kind:'contact',customer_name:'Customer',channel_name:'WhatsApp',display_state:'human',state:'human',ai_availability:'stopped',available_actions:[],can_send:true,controls_allowed:true,agent_status:'manual',agent_mode:'manual',agent_id:'00000000-0000-0000-0000-000000000301',agent_name:'Assigned Helper'};
const profile={success:true,member:{tenant_id:tenant,telegram_id:33,role:'worker',permissions:['dialogs','tasks'],active:true},capabilities:{modules:{dialogs:true,tasks:true}}};
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':chromium.executablePath()),args:['--no-sandbox']});
 try{
  for(const [width,height] of [[390,844],[1280,900],[390,500]]){
   const page=await browser.newPage({viewport:{width,height}}),errors=[],calls=[];let navigations=0,latest=1,holdHistory=null,releaseHistory=null,holdCopilot=null,releaseCopilot=null;page.on('request',request=>{if(request.isNavigationRequest()&&request.frame()===page.mainFrame())navigations++;});
   page.on('pageerror',error=>errors.push(error.message));page.on('dialog',()=>assert.fail('Unexpected native dialog'));
   // Production stores question templates in this tenant/employee scoped key.
   await page.addInitScript(()=>window.documentLifetime=crypto.randomUUID());
   await page.addInitScript(({tenant})=>localStorage.setItem(`tenant.operator.copilot-question-templates.${tenant}:33`,JSON.stringify(Array.from({length:12},(_,i)=>({id:'template-'+i,name:'Saved question '+i,prompt:'Saved question'})))),{tenant});
   await page.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url()),name=url.pathname;calls.push({path:name,method:request.method(),body:request.postData()});
    if(name==='/app')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8')});
    if(name.startsWith('/assets/')){const file=path.join(root,'docs',path.basename(name));return fs.existsSync(file)?route.fulfill({path:file}):route.fulfill({status:404,body:''});}
    if(name.endsWith('/crm/chat/attachment')){const bytes=Buffer.alloc(44+1280000);bytes.write('RIFF');bytes.writeUInt32LE(bytes.length-8,4);bytes.write('WAVEfmt ',8);bytes.writeUInt32LE(16,16);bytes.writeUInt16LE(1,20);bytes.writeUInt16LE(1,22);bytes.writeUInt32LE(8000,24);bytes.writeUInt32LE(16000,28);bytes.writeUInt16LE(2,32);bytes.writeUInt16LE(16,34);bytes.write('data',36);bytes.writeUInt32LE(1280000,40);return route.fulfill({contentType:'audio/wav',body:bytes});}
    if(name==='/api/platform/me')return route.fulfill({json:profile});
    let result={};if(name.endsWith('/dialogs/copilot')){if(holdCopilot)await holdCopilot;return route.fulfill({json:{success:true,result:{text:'Late response from A'}}});}if(name.endsWith('/dialogs/history')&&holdHistory){const gate=holdHistory;holdHistory=null;await gate;}
    if(name.endsWith('/dialogs/history'))result={dialog:{...dialog,lead_id:Number(url.searchParams.get('lead_id'))||41,conversation_id:url.searchParams.get('conversation_id')||dialog.conversation_id},messages:[...Array.from({length:35+latest},(_,i)=>({id:'m'+i,external_id:'e'+i,direction:'incoming',message_type:'text',body:'Customer question '+i,happened_at:'2026-10-21T10:00:00Z'})),{id:'voice-row',external_id:'voice-1',source:'connect',source_lead_id:41,source_channel_id:channel,source_conversation_id:dialog.conversation_id,direction:'incoming',message_type:'audio',media_version:'a'.repeat(64),duration_seconds:8}],next_before:null};
    else if(name.endsWith('/dialogs'))result={dialogs:[{...dialog,last_message_id:'m'+latest,last_message_direction:'incoming',last_message_text:'Customer question',last_message_at:'2026-10-21T10:00:00Z'}],channels:[{id:'connect:'+channel,name:'WhatsApp'}],agents:[],next_offset:null};
    else if(name.endsWith('/developer-marker'))result={enabled:false};
    else if(name.endsWith('/replies'))result={replies:[],consultations:[]};
    return route.fulfill({json:{success:true,result}});
   });
   try{
    await page.goto('https://crm.test/app?view=dialogs');const documentLifetime=await page.evaluate(()=>window.documentLifetime);
    await page.locator('.operator-row').first().click();await page.locator('#dialogHelperType').waitFor({state:'attached'});
    // Decode a real synthetic WAV. Stable-source rerenders must keep this exact player.
    await page.locator('[data-chat-attachment="voice-1"] [data-media-open]').click();
    await page.waitForFunction(()=>document.querySelector('audio.operator-native-audio')?.readyState>=1);
    await page.evaluate(()=>{window.keptPlayer=document.querySelector('audio.operator-native-audio');window.keptPlayer.currentTime=2;window.keptPlayer.play();});
    const mediaCalls=()=>calls.filter(c=>c.path.endsWith('/crm/chat/attachment')).length;
    assert.equal(mediaCalls(),1);
    await page.locator('#operatorCopilotToggle').click();
    const panel=page.locator('#operatorCopilot'),prompt=page.locator('#operatorCopilotPrompt');
    await prompt.fill('Private question for this conversation');
    if(width<=900)await page.locator('#operatorCopilotBackdrop').click({position:{x:2,y:2},force:true});else await page.locator('#operatorConversationHeader').click({position:{x:2,y:2},force:true});
    assert.equal(await panel.isVisible(),true,'backdrop never dismisses or clears a draft');
    await page.screenshot({path:'/tmp/bein-helper-mobile-'+width+'.png'});
    await page.locator('[data-copilot-mode=instruction]').click();
    await page.locator('#operatorCopilotTemplates summary').click();
    await page.locator('#operatorCopilotTemplateCreate').click();
    await page.locator('#operatorCopilotTemplateName').fill('Unsaved name');
    await page.locator('#operatorCopilotTemplateQuestion').fill('Unsaved template question');
    await page.locator('#dialogHelperType').selectOption('Developer');
    await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-language')));
    assert.equal(await page.locator('#dialogHelperType').inputValue(),'Developer');
    await page.locator('#dialogHelperType').selectOption('Helper');
    assert.equal(await prompt.inputValue(),'Private question for this conversation');
    assert.equal(await page.locator('[data-copilot-mode=instruction]').getAttribute('aria-pressed'),'true');
    assert.equal(await page.locator('#operatorCopilotTemplateName').inputValue(),'Unsaved name');
    assert.equal(await page.locator('#operatorCopilotTemplateQuestion').inputValue(),'Unsaved template question');
    await page.locator('#operatorCopilotTemplates summary').click();
    await prompt.focus();await prompt.evaluate(node=>node.setSelectionRange(4,9));
    await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-language')));
    assert.deepEqual(await prompt.evaluate(node=>[document.activeElement===node,node.selectionStart,node.selectionEnd]),[true,4,9]);
    assert.equal(await page.evaluate(()=>document.querySelector('audio.operator-native-audio')===window.keptPlayer),true,'same source keeps the native player object');
    assert.ok(await page.evaluate(()=>window.keptPlayer.currentTime>=2),'playhead is retained');assert.equal(mediaCalls(),1);
    // Async data refresh and background/foreground are data updates, never document navigations.
    await page.evaluate(()=>{const chat=document.getElementById('chatRows');chat.scrollTop=200;});
    const scrollBefore=await page.locator('#chatRows').evaluate(n=>n.scrollTop);latest++;
    await page.evaluate(()=>window.tenantOperator.refresh());
    assert.equal(await page.locator('#chatRows').evaluate(n=>n.scrollTop),scrollBefore,'append below the reader does not move their position');
    await page.evaluate(()=>{Object.defineProperty(document,'hidden',{value:true,configurable:true});document.dispatchEvent(new Event('visibilitychange'));Object.defineProperty(document,'hidden',{value:false,configurable:true});document.dispatchEvent(new Event('visibilitychange'));});
    assert.equal(await page.evaluate(()=>document.querySelector('audio.operator-native-audio')===window.keptPlayer&&!window.keptPlayer.paused),true,'background/foreground retains the playing object');
    await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-profile',{detail:window.tenantProfileSnapshot})));
    assert.equal(await panel.isVisible(),true);assert.equal(await prompt.inputValue(),'Private question for this conversation');
    await page.evaluate(()=>window.tenantAppNavigate('tasks',true));assert.equal(await panel.isVisible(),false,'actual view navigation hides its overlay');
    await page.evaluate(()=>window.tenantAppNavigate('dialogs',true));await page.locator('#operatorCopilotToggle').click();assert.equal(await prompt.inputValue(),'Private question for this conversation');
    if(width<=900){
      // A smaller visual viewport models the space left by the on-screen keyboard.
      await page.setViewportSize({width,height:500});await prompt.focus();
      await page.evaluate(()=>{document.getElementById('dialogHelperContent').scrollTop=0;});
      const geometry=await page.evaluate(()=>{const rect=id=>{const r=document.getElementById(id).getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};};return {panel:rect('operatorCopilot'),close:rect('operatorCopilotClose'),prompt:rect('operatorCopilotPrompt'),ask:rect('operatorCopilotAsk'),vw:innerWidth,vh:visualViewport.height};});
      assert.ok(geometry.panel.top>=0&&geometry.panel.bottom<=geometry.vh+1,JSON.stringify(geometry));
      assert.ok(geometry.close.width>=44&&geometry.close.height>=44);
      assert.ok(geometry.prompt.width>250&&geometry.ask.bottom<=geometry.panel.bottom,JSON.stringify(geometry));
      await page.goBack();await panel.waitFor({state:'hidden'});
      assert.ok(await page.locator('#chatRows').isVisible(),'first Back closes the helper, keeps the conversation');
      await page.locator('#operatorCopilotToggle').click();assert.equal(await prompt.inputValue(),'Private question for this conversation');
    }
    await page.locator('#operatorCopilotClose').click();await panel.waitFor({state:'hidden'});
    if(width<=900)await page.waitForFunction(()=>!history.state?.operatorHelper);
    await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-language')));
    await page.locator('#operatorCopilotToggle').click();assert.equal(await prompt.inputValue(),'Private question for this conversation','closed panel keeps its draft during refresh');
    // Late history for this dialog cannot overwrite the new dialog or transfer its draft.
    holdCopilot=new Promise(resolve=>{releaseCopilot=resolve;});await page.locator('#operatorCopilotAsk').click();
    holdHistory=new Promise(resolve=>{releaseHistory=resolve;});
    await page.evaluate(route=>{window.pendingHistory=window.tenantOperator.openDialog(route,true);},dialog);
    const second={...dialog,lead_id:42,conversation_id:'994500009999@s.whatsapp.net'};
    await page.evaluate(route=>window.tenantOperator.openDialog(route,true),second);
    await page.locator('#operatorCopilotToggle').click();assert.equal(await prompt.inputValue(),'','different dialog starts with its own draft');
    const answered=page.waitForResponse(response=>new URL(response.url()).pathname.endsWith('/dialogs/copilot'));releaseCopilot();await answered;releaseHistory();await page.evaluate(()=>window.pendingHistory);
    assert.equal(await page.locator('#operatorCopilotResult').innerText(),'','late AI result cannot enter another dialog');
    assert.equal(await page.evaluate(()=>window.tenantOperator.currentDialog.lead_id),42);
    await page.evaluate(route=>window.tenantOperator.openDialog(route,true),dialog);
    await page.locator('#operatorCopilotToggle').click();assert.equal(await prompt.inputValue(),'Private question for this conversation','returning to this dialog restores only its own in-memory draft');
    await page.evaluate(()=>document.dispatchEvent(new Event('tenant-auth-invalid')));
    assert.equal(await page.locator('#operatorCopilot').count(),0,'logout removes private UI');
    await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-profile',{detail:window.tenantProfileSnapshot})));
    await page.evaluate(route=>window.tenantOperator.openDialog(route,true),dialog);
    await page.locator('#operatorCopilotToggle').click();assert.equal(await prompt.inputValue(),'','logout destroys retained helper drafts');
    if(width<=900){const historyCalls=calls.filter(c=>c.path.endsWith('/dialogs/history')).length;await page.evaluate(()=>window.tenantOperator.closeDialog());await panel.waitFor({state:'hidden'});await page.waitForFunction(()=>!history.state?.operatorHelper);assert.equal(await page.evaluate(()=>window.tenantOperator.currentDialog.lead_id),41);assert.equal(calls.filter(c=>c.path.endsWith('/dialogs/history')).length,historyCalls,'layer close keeps the existing dialog, does not destroy/reload it');}
    assert.equal(await page.evaluate(()=>window.documentLifetime),documentLifetime);assert.equal(navigations,1,'no document reload from data, media, profile or modal updates');
    assert.deepEqual(errors,[]);assert.equal(calls.filter(c=>c.path.endsWith('/dialogs/copilot')).length,1,'only the explicitly requested fixture generation');assert.equal(calls.some(c=>/\/dispatch$|chat\/send|transcrib/.test(c.path)),false,'no sends or ASR');
    console.log('PASS helper modal '+width+'x'+height+': backdrop, mode/type/templates, focus/cursor, closed draft, keyboard bounds, Back, scoped reopen, late history, logout, media object/playhead, polling/visibility and no reload');
   }catch(error){await page.screenshot({path:'/tmp/helper-modal-failure-'+width+'.png'});throw error;}finally{await page.close();}
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
