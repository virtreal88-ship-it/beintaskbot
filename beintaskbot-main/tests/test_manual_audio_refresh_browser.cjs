const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'),root=path.join(__dirname,'..');
(async()=>{const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined,args:['--no-sandbox']});try{for(const [width,deferred] of [[390,false],[1440,false],[390,'history'],[1440,'language']]){
 const page=await browser.newPage({viewport:{width,height:900}});
 await page.route('https://test.local/**',route=>route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')}));await page.goto('https://test.local/app');
 await page.evaluate(deferred=>{
  window.deferAiReply=deferred;window.deferBackgroundHistory=false;
  document.querySelectorAll('.view').forEach(e=>e.classList.remove('active'));document.getElementById('view-dialogs').classList.add('active');
  window.testRoute={provider:'connect',lead_id:41,agent_id:'assigned-agent',channel_id:'channel-a',conversation_id:'client',state:'human',can_send:true,controls_allowed:true,available_actions:[]};
  window.tenantProfileSnapshot={member:{tenant_id:'tenant-a',telegram_id:33,role:'owner',active:true},capabilities:{modules:{dialogs:true,support:true}}};
  window.requests=[];window.aiDone=false;window.historyReads=0;
  window.testMessages=[{id:'voice-row',external_id:'connect:voice',source:'connect',provider_message_id:'PROVIDER_VOICE',direction:'incoming',message_type:'audio',body:'',author_type:'customer',analysis_state:'failed',analysis_diagnostic:{status:'error',code:'ai_spend_media_price_unknown',stage:'audio_transcription',at:'2026-11-01T05:06:33Z'}},{id:'other-row',external_id:'connect:other',source:'connect',direction:'incoming',message_type:'voice',body:'',author_type:'customer',analysis_state:'failed',analysis_diagnostic:{status:'error',code:'media_unavailable',stage:'media_download',at:'2026-11-01T05:00:00Z'}}];
  window.fetch=async(url,options={})=>{
   url=String(url);window.requests.push({url,method:options.method||'GET'});let result={};
   if(url.includes('/dialogs/history')){
    window.historyReads++;
    if(window.aiDone){return new Promise(resolve=>{window.releaseAudioHistory=()=>{
     const messages=structuredClone(window.testMessages);messages[0]={...messages[0],has_transcription:true,audio_transcript:'New exact-message transcript <img src=x onerror=alert(1)>',analysis_state:'completed',analysis_diagnostic:{status:'processed',code:'transcribed',stage:'unknown',at:'2026-11-01T06:40:17Z'},analysis_receipt_diagnostic:{status:'processed',code:'transcribed',stage:'unknown',at:'2026-11-01T06:40:17Z'}};
     // An unrelated changed row must not be patched by this request's receipt IDs.
     messages[1]={...messages[1],has_transcription:true,audio_transcript:'Unrelated new result'};
     resolve({ok:true,json:async()=>({success:true,result:{dialog:window.testRoute,messages}})});
    };});}
    result={dialog:window.testRoute,messages:structuredClone(window.testMessages)};
    if(window.deferBackgroundHistory)return new Promise(resolve=>{window.releaseBackgroundHistory=()=>resolve({ok:true,json:async()=>({success:true,result})});});
   }else if(url.includes('/dialogs/copilot')){if(window.deferAiReply)await new Promise(resolve=>{window.releaseAiReply=resolve;});window.aiDone=true;result={text:'Private customer draft',context_usage:{messages:2,voice_transcriptions:1,evidence:[{source_id:'connect:voice',kind:'audio',status:'processed',code:'transcribed'}]}};}
   else if(url.includes('/dialogs?'))result={dialogs:[],channels:[],agents:[]};
   else if(url.includes('/api/platform/me'))return {ok:true,json:async()=>window.tenantProfileSnapshot};
   return {ok:true,json:async()=>({success:true,result})};
  };
 },deferred);
 for(const file of ['docs/tenant-operator-i18n.js','docs/tenant-operator-dialogs.js'])await page.addScriptTag({path:path.join(root,file)});
 await page.evaluate(()=>window.tenantOperator.openDialog(window.testRoute));
 const voice=page.locator('[data-message-id="voice-row"]');assert.doesNotMatch(await voice.textContent(),/ai_spend_media_price_unknown/);await page.locator('#operatorMoreActions summary').click();await page.locator('[data-operator-diagnostics]').click();const initialDiagnostic=page.locator('#operatorDiagnosticsBody [data-audio-message-id="voice-row"]');await initialDiagnostic.waitFor();await initialDiagnostic.locator('summary').click();assert.match(await initialDiagnostic.innerText(),/ai_spend_media_price_unknown/);await page.locator('#operatorDiagnosticsClose').click();
 if(deferred==='history')await page.evaluate(()=>{window.deferBackgroundHistory=true;window.backgroundRefresh=window.tenantOperator.openDialog(window.testRoute);});
 await page.evaluate(()=>{window.originalAudio=document.querySelector('[data-message-id="voice-row"] audio');document.querySelector('[data-reply-message="voice-row"]').click();document.getElementById('operatorAIReply').click();});
 if(deferred){await page.waitForFunction(()=>window.releaseAiReply);if(deferred==='history'){await page.waitForFunction(()=>window.releaseBackgroundHistory);await page.evaluate(async()=>{window.releaseBackgroundHistory();await window.backgroundRefresh;});}else await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-language')));await page.evaluate(()=>window.releaseAiReply());}
 await page.waitForFunction(()=>window.releaseAudioHistory&&document.getElementById('chatText').value==='Private customer draft',{},{timeout:5000}).catch(async error=>{console.error(await page.evaluate(()=>({requests:window.requests,status:document.getElementById('operatorAIReplyStatus')?.textContent,chatError:document.getElementById('operatorChatError')?.textContent,draft:document.getElementById('chatText')?.value,done:window.aiDone})));throw error;});
 await page.evaluate(()=>{window.originalAudio=document.querySelector('[data-message-id="voice-row"] audio');const editor=document.getElementById('chatText');editor.value='Employee edited draft';editor.focus();editor.setSelectionRange(2,7);document.getElementById('chatRows').scrollTop=10;window.savedScroll=document.getElementById('chatRows').scrollTop;window.releaseAudioHistory();});
 await voice.locator('[data-audio-transcript]').waitFor();
 assert.equal(await voice.locator('[data-audio-transcript] [data-user-content]').textContent(),'New exact-message transcript <img src=x onerror=alert(1)>');
 assert.equal(await voice.locator('img').count(),0);assert.equal(await voice.locator('[data-audio-diagnostic]').count(),0,'technical audio diagnostics stay out of ordinary chat after refresh');await page.locator('#operatorMoreActions summary').click();await page.locator('[data-operator-diagnostics]').click();const refreshedDiagnostic=page.locator('#operatorDiagnosticsBody [data-audio-message-id="voice-row"]');await refreshedDiagnostic.waitFor();await refreshedDiagnostic.locator('summary').click();assert.doesNotMatch(await refreshedDiagnostic.innerText(),/ai_spend_media_price_unknown|No original analysis receipt/);assert.match(await refreshedDiagnostic.innerText(),/processed/);await page.locator('#operatorDiagnosticsClose').click();await page.evaluate(()=>{const editor=document.getElementById('chatText');editor.focus();editor.setSelectionRange(2,7);});
 assert.equal(await page.locator('[data-message-id="other-row"] [data-audio-transcript]').count(),0);
 assert.deepEqual(await page.evaluate(()=>({audio:document.querySelector('[data-message-id="voice-row"] audio')===window.originalAudio,draft:document.getElementById('chatText').value,focus:document.activeElement.id,start:document.getElementById('chatText').selectionStart,end:document.getElementById('chatText').selectionEnd,quote:document.querySelector('[data-quote-message-id]')?.dataset.quoteMessageId,scroll:document.getElementById('chatRows').scrollTop===window.savedScroll})),{audio:true,draft:'Employee edited draft',focus:'chatText',start:2,end:7,quote:'voice-row',scroll:true});
 const calls=await page.evaluate(()=>window.requests);assert.equal(calls.filter(r=>r.method==='POST'&&r.url.includes('/copilot')).length,1);assert.equal(calls.filter(r=>r.method==='GET'&&r.url.includes('/dialogs/history')).length,deferred==='history'?3:2);assert.equal(calls.filter(r=>/transcrib|\/asr|\/send/.test(r.url)).length,0);
 console.log('PASS manual audio refresh '+width+(deferred?' deferred '+deferred:'')+': stale error to saved transcript, exact receipt IDs, native player/draft/quote/focus/caret/scroll preserved, GET only');await page.close();
 }}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
