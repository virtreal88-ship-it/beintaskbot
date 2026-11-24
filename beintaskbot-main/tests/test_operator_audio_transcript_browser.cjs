const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'),root=path.join(__dirname,'..');
async function checkReplyControls(browser,width,role){
 const mobile=width<500;
 const page=await browser.newPage({viewport:{width,height:844},hasTouch:mobile,isMobile:mobile});
 try{
  const html=fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
  await page.route('http://test.local/**',route=>route.fulfill({contentType:'text/html',body:html}));
  await page.goto('http://test.local/app?view=dialogs');
  await page.evaluate(({role,mobile})=>{
    document.querySelectorAll('.view').forEach(e=>e.classList.remove('active'));
    document.getElementById('view-dialogs').classList.add('active');
    const tenant_id='e09a5c24-093f-4141-992d-532052e00c90',channel_id='96863d75-d272-4c93-a25e-ffb2b16ecae3';
    window.testRoute={provider:'connect',lead_id:41,channel_id,conversation_id:'client',state:'human',can_send:true,controls_allowed:true,available_actions:[],lifecycle_enabled:true,lifecycle:{state:'accepted',revision:1}};
    window.tenantProfileSnapshot={member:{tenant_id,telegram_id:33,role,active:true},capabilities:{modules:{dialogs:true},dialog_company_binding:true}};
    window.requests=[];
    const reply=(result,status=200,error='')=>({ok:status===200,status,json:async()=>({success:status===200,result,error})});
    window.fetch=async(url,options={})=>{
      url=String(url);
      window.requests.push({url,method:options.method||'GET'});
      if(url.includes('/api/platform/me'))return{ok:true,json:async()=>window.tenantProfileSnapshot};
      if(url.includes('/dialogs/history'))return reply({dialog:window.testRoute,messages:[{id:'long',provider_message_id:'provider-long',direction:'incoming',message_type:'text',body:'long'.repeat(100),author_type:'customer'}],next_before:null});
      if(url.includes('/dialogs?'))return reply({dialogs:[window.testRoute],channels:[],agents:[],next_offset:null});
      if(url.includes('company-panel/preferences'))return reply({open:!mobile});
      if(url.includes('/state?')){
        if(role!=='owner')return reply(null,403,'ai_diagnostics_require_unrestricted_dialog_read');
        return reply({agents:[],events:[],can_view_technical:true});
      }
      if(url.includes('/state/technical?'))return reply({technical:{}});
      if(url.includes('/quote-receipt?'))return reply(null,404,'No previous reply');
      return reply({});
    };
  },{role,mobile});
  for(const file of ['docs/tenant-operator-i18n.js','docs/tenant-chat-send.js','docs/tenant-operator-dialogs.js','docs/tenant-dialog-composer.js'])await page.addScriptTag({path:path.join(root,file)});
  await page.evaluate(()=>window.tenantOperator.openDialog(window.testRoute));
  if(!mobile)await page.locator('#operatorCompanyPanel[open]').waitFor();
  await page.locator('[data-reply-message]').focus();
  await page.keyboard.press('Enter');
  const quote=page.locator('#operatorReplyQuote');
  const remove=quote.locator('button');
  await page.locator('#chatText').fill('Keep this draft');
  await remove.click();
  assert.equal(await quote.isVisible(),false,'remove the selected quote');
  assert.equal(await page.locator('#chatText').inputValue(),'Keep this draft','removing a quote preserves the draft');
  await page.locator('#operatorMoreActions summary').click();
  if(role==='owner'){
    await page.evaluate(async()=>{
      window.testRoute={...window.testRoute,lifecycle:{state:'accepted',revision:2}};
      await window.tenantOperator.openDialog(window.testRoute,true);
    });
    assert.equal(await page.locator('#operatorMoreActions').getAttribute('open'),'','menu stays open after a same-dialog render');
  }
  await page.locator('[data-operator-diagnostics]').click();
  const ai=page.locator('#operatorAiDiagnostics');
  if(role==='owner'){
    const technical=ai.locator('.operator-diagnostic-technical summary');
    await technical.click();
    await page.waitForFunction(()=>window.requests.some(r=>r.url.includes('/state/technical?')));
  }else{
    await ai.locator('p').waitFor();
    await page.waitForFunction(()=>document.querySelector('#operatorAiDiagnostics')?.textContent.includes('rolunuz'));
  }
  await page.evaluate(()=>window.tenantOperator.refresh());
  assert.equal(await page.locator('#operatorDiagnosticsModal').isVisible(),true,'refresh keeps diagnostics open for the current conversation');
  const receipt=page.locator('#quoteReceiptDiagnostic');
  await receipt.locator('summary').click();
  await receipt.locator('[data-quote-receipt-check]').click();
  await receipt.locator('output').filter({hasText:'No previous reply'}).waitFor();
  assert.equal(await page.evaluate(()=>window.tenantOperator.currentDialog?.lead_id),41,'receipt 404 preserves the dialog');
  assert.equal(await page.locator('#chatText').inputValue(),'Keep this draft','diagnostics preserves the draft');
  assert.equal(await page.evaluate(()=>window.requests.filter(r=>r.url.includes('quote-receipt')).map(r=>r.method).join(',')),'GET');
  assert.equal(await page.evaluate(()=>window.requests.some(r=>r.url.includes('/chat/send'))),false);
  await page.locator('#operatorDiagnosticsClose').click();
  assert.equal(await page.locator('#operatorMoreActions summary').evaluate(e=>e===document.activeElement),true,'closing diagnostics returns focus to the menu');
 }finally{await page.close();}
}
(async()=>{const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined,args:['--no-sandbox']});try{for(const viewport of [{width:390,height:844},{width:1440,height:900}]){
 const page=await browser.newPage({viewport,hasTouch:viewport.width<500,isMobile:viewport.width<500});await page.route('http://test.local/**',route=>route.fulfill({status:200,contentType:'text/html',body:fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')}));await page.goto('http://test.local/app?view=dialogs');
 await page.evaluate(()=>{document.querySelectorAll('.view').forEach(e=>e.classList.remove('active'));document.getElementById('view-dialogs').classList.add('active');const tenant='e09a5c24-093f-4141-992d-532052e00c90',channel='96863d75-d272-4c93-a25e-ffb2b16ecae3';window.testRoute={provider:'connect',lead_id:41,channel_id:channel,conversation_id:'client',state:'human',can_send:true,controls_allowed:true,available_actions:[]};window.tenantProfileSnapshot={member:{tenant_id:tenant,telegram_id:33,role:'owner',active:true},capabilities:{modules:{dialogs:true,support:true}}};window.requests=[];window.fetch=async(url,options={})=>{url=String(url);window.requests.push({url,method:options.method||'GET'});let result={};if(url.includes('/dialogs/history'))result={dialog:window.testRoute,messages:[{id:'one',external_id:'connect:one',source:'connect',direction:'incoming',message_type:'audio',audio_transcript:'Salam, привет!',has_transcription:true,body:'',author_type:'customer'},{id:'two',external_id:'connect:two',source:'connect',direction:'incoming',message_type:'voice',analysis_state:'pending',body:'',author_type:'customer'},{id:'three',external_id:'connect:three',source:'connect',direction:'incoming',message_type:'voice',analysis_state:'failed',analysis_diagnostic:{status:'unsupported',code:'media_unavailable',stage:'unknown',at:'2026-10-31T22:54:49Z'},analysis_receipt_diagnostic:{status:'error',code:'media_unavailable',stage:'media_download',at:'2026-10-31T20:00:00Z',pre_dispatch_proven:true},body:'',author_type:'customer'},{id:'admission',external_id:'connect:admission',source:'connect',direction:'incoming',message_type:'voice',analysis_state:'failed',analysis_diagnostic:{status:'error',code:'ai_spend_media_price_unknown',stage:'audio_transcription',at:'2026-11-01T03:09:00Z'},body:'',author_type:'customer'},{id:'four',external_id:'connect:four',source:'connect',direction:'incoming',message_type:'image',body:'',author_type:'customer'}],next_before:null};else if(url.includes('/dialogs?'))result={dialogs:[],channels:[],agents:[],attention_count:0,next_offset:null};else if(url.includes('/api/platform/me'))return{ok:true,json:async()=>window.tenantProfileSnapshot};return {ok:true,json:async()=>({success:true,result})};};});
 for(const file of ['docs/tenant-operator-i18n.js','docs/tenant-chat-send.js','docs/tenant-operator-dialogs.js'])await page.addScriptTag({path:path.join(root,file)});await page.evaluate(()=>window.tenantOperator.openDialog(window.testRoute));
 const chat=page.locator('#chatRows');assert.equal(await chat.locator('[data-media-download]').count(),5,'audio and photo attachments expose explicit download actions');assert.equal(await chat.locator('[data-media-open]').count(),5,'each attachment has an explicit load action');assert.equal(await chat.locator('audio[preload="none"]').count(),4);assert.equal(await page.evaluate(()=>window.requests.filter(r=>r.url.includes('/crm/chat/attachment')).length),0,'opening a conversation does not fetch attachment bytes');await chat.locator('[data-audio-transcript] summary').first().click();assert((await chat.innerText()).includes('Salam, привет!'));for(const analysis of await chat.locator('.operator-audio-analysis>summary').all())await analysis.click();const text=await chat.innerText();assert(/gözləyir|ожидает|awaiting/i.test(text));assert(/xəta|ошиб|failed/i.test(text));assert.equal(await chat.locator('[data-audio-diagnostic]').count(),0,'audio diagnostics are not shown among ordinary messages');assert.doesNotMatch(text,/Диагностика аудио|Səs diaqnostikası|Audio diagnostics/);await page.locator('#operatorMoreActions summary').click();await page.locator('[data-operator-diagnostics]').click();const audioDiagnostics=page.locator('#operatorDiagnosticsBody .operator-diagnostic-audio');await audioDiagnostics.waitFor();const diagnosticRows=audioDiagnostics.locator('[data-audio-diagnostic]');assert.equal(await diagnosticRows.count(),4);const diagnosticThree=audioDiagnostics.locator('[data-audio-diagnostic]').filter({hasText:'connect:three'});await diagnosticThree.locator('summary').click();assert.match(await diagnosticThree.innerText(),/media_unavailable/);assert.match(await diagnosticThree.innerText(),/media_download/);assert.match(await diagnosticThree.innerText(),/unknown/);assert.match(await diagnosticThree.innerText(),/təsdiqlənib|подтверждён|proven/);const admission=audioDiagnostics.locator('[data-audio-diagnostic]').filter({hasText:'ai_spend_media_price_unknown'});await admission.locator('summary').click();assert.match(await admission.innerText(),/ASR tarifi|тариф ASR|ASR tariff/);const receipt=page.locator('#operatorDiagnosticsBody #quoteReceiptDiagnostic');assert.equal(await receipt.count(),1,'reply status is available only inside diagnostic details');assert.equal(await receipt.locator('[data-quote-receipt-check]').count(),1);await receipt.locator('summary').click();assert.equal(await page.evaluate(()=>window.requests.filter(r=>r.url.includes('quote-receipt')).length),0,'opening diagnostics does not check receipt automatically');await receipt.locator('[data-quote-receipt-check]').click();await page.waitForFunction(()=>window.requests.some(r=>r.url.includes('quote-receipt')));assert.equal(await page.evaluate(()=>window.requests.filter(r=>r.url.includes('quote-receipt')&&(r.method||'GET')==='POST').length),0,'receipt diagnostics is read-only');assert.equal(await page.evaluate(()=>window.requests.filter(r=>/transcrib|asr|\/copilot|chat\/send/i.test(r.url)).length),0,'opening and expanding diagnostics triggers no ASR, Copilot, or send action');console.log('PASS audio UI '+viewport.width);await page.close();
 }
 await checkReplyControls(browser,1440,'owner');
 await checkReplyControls(browser,390,'employee');
 console.log('PASS: removable quote, owner menu, and scoped employee diagnostics');
 }finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
