/* Actual app and shared dialogue renderer, synthetic API boundary, real Chromium. */
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const {docs,assets,appHtml,installNotificationCaseFixture}=require('./notification_case_targets_fixture.cjs');
function installCaseTabsFixture(){
 const f=window.notificationCaseFixture,base=window.fetch;
 f.holdPrepare=false;f.pendingPrepare=[];f.historyCalls=[];f.prepared=[];f.dispatched=[];f.mediaCalls=[];f.pendingHistory=[];f.holdHistory=false;f.wrongHistory=false;f.consultations=[];f.replies=[];
 f.material={id:'20000000-0000-4000-8000-000000000601',external_id:'connect:material',provider_message_id:'material',source:'connect',author_type:'customer',author_name:'Aysel',direction:'incoming',body:'Printer is offline. Can you help?',message_type:'text',happened_at:'2026-11-02T10:00:00Z'};
 f.history=[{...f.material,id:'outside-case',provider_message_id:'older-question',body:'Earlier dialogue message outside this case',happened_at:'2026-11-01T09:00:00Z'},f.material,{...f.material,id:'operator-answer',external_id:'connect:answer',provider_message_id:'answer',direction:'outgoing',author_type:'operator',body:'Please check the printer cable.',happened_at:'2026-11-02T10:01:00Z'},{...f.material,id:'history-photo',external_id:'connect:photo',body:'Printer display',message_type:'image',media_filename:'printer.png'},{...f.material,id:'history-audio',external_id:'connect:audio',body:'',message_type:'audio',media_filename:'message.ogg'}];
 f.history=f.history.map(m=>({...m,source_lead_id:41,source_channel_id:f.channel,source_conversation_id:f.route.conversation_id}));
 if(new URL(location.href).searchParams.get('consultation_reply_id')==='reply-one'){f.consultations=[{id:'consult-one',case_id:f.caseA,state:'sent',recipient:'Rauf'}];f.replies=[{id:'reply-one',case_id:f.caseA,consultation_id:'consult-one',body:'Persisted case reply',source_kind:'manual',draft_state:'completed'}];}
 const response=(result,status=200)=>new Response(JSON.stringify(status<400?{success:true,result}:{success:false,error:result}),{status,headers:{'Content-Type':'application/json'}});
 window.fetch=async(input,options={})=>{
  const url=new URL(String(input),location),p=url.pathname,body=options.body?JSON.parse(options.body):null;
  if(p.endsWith('/dialogs/history')){f.historyCalls.push(Object.fromEntries(url.searchParams));const result={dialog:{...f.route,...(f.wrongHistory?{conversation_id:'wrong-client'}:{})},messages:url.searchParams.has('before')?[{...f.material,id:'oldest',body:'Oldest dialogue message'}]:f.history,next_before:url.searchParams.has('before')?null:'history-cursor'};if(f.holdHistory){f.holdHistory=false;return new Promise(resolve=>f.pendingHistory.push(()=>resolve(response(result))))}return response(result);}
  if(p.endsWith('/dialog-consultations/replies')){f.lastConsultationCase=url.searchParams.get('case_id');return response({consultations:f.consultations.filter(row=>row.case_id===f.lastConsultationCase),replies:f.replies.filter(row=>row.case_id===f.lastConsultationCase)});}
  if(p.endsWith('/dialog-consultations/destinations'))return response({destinations:[{channel_id:'dev-channel',conversation_id:'dev-conversation',customer_name:'Rauf · Developer'}]});
  if(p.endsWith('/dialog-consultations/preview'))return response({public_header:{version:1,customer_name:'Synthetic customer',phone_status:'unknown',company_name:'Synthetic client',operator_name:'Synthetic operator'},public_header_hash:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',case_id:body.case_id,messages:[{...f.material,public_sender_version:'b'.repeat(64),media_shareable:false,source_version:'synthetic-v1'}],customer_identity_preview:{name:'Private identity',verified_phone:'+15551112222'}});
  if(p.endsWith('/dialog-consultations')&&options.method==='POST'){f.prepared.push(body);f.consultations=[{id:'consult-one',case_id:body.case_id,request_id:body.request_id,recipient:'Rauf · Developer',reason:body.summary,state:'ready',can_dispatch:true,can_send:true,destination_channel:'dev-channel',destination_conversation:'dev-conversation',created_at:'2026-11-02T12:00:00Z',expires_at:'2099-11-09T12:00:00Z'}];if(f.holdPrepare)return new Promise(resolve=>f.pendingPrepare.push(()=>resolve(response({id:'consult-one'}))));return response({id:'consult-one'});}
  if(p.endsWith('/consult-one/dispatch')){f.dispatched.push(body);f.consultations[0].state='pending';f.consultations[0].can_dispatch=false;return response({state:'pending'});}
  if(p.endsWith('/chat/attachment')){f.mediaCalls.push(Object.fromEntries(url.searchParams));return new Response(Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9WQAAAAASUVORK5CYII='),c=>c.charCodeAt(0)),{headers:{'Content-Type':'image/png'}});}
  const result=await base(input,options);
  if(p.startsWith('/api/platform/ai/support-tickets/')&&result.ok){const data=await result.json();if(data.result?.ticket){data.result.messages=f.caseMessages||[f.material];data.result.scope={message_count:data.result.messages.length,media_count:data.result.messages.filter(row=>row.message_type!=='text').length,has_earlier_source:true};}return new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});}
  return result;
 };
}
(async()=>{
 const artifacts=path.join(__dirname,'../output/case-card-tabs');fs.mkdirSync(artifacts,{recursive:true});
 const server=http.createServer((req,res)=>{const u=new URL(req.url,'http://test');if(u.pathname.startsWith('/assets/')){const name=path.basename(u.pathname);if(![...assets,'tenant-chat-attachments.js'].includes(name)){res.writeHead(404);res.end();return;}res.setHeader('content-type','text/javascript');res.end(fs.readFileSync(path.join(docs,name)));return;}res.setHeader('content-type','text/html');res.end(appHtml().replace('</body>','<script src="/assets/tenant-chat-attachments.js"></script></body>'));});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||process.env.CHROMIUM_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':fs.existsSync('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe')?'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe':undefined)});
 const active=page=>page.locator('#supportDetail [role=tab][aria-selected=true]').getAttribute('data-support-tab');
 async function mount(width,query=''){
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage(),errors=[];
  await context.addInitScript(installNotificationCaseFixture);await context.addInitScript(installCaseTabsFixture);page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());await page.goto(origin+'/app?view=support&lang=ru'+query);await page.locator('[data-support-id]').first().waitFor();return {context,page,errors};
 }
 async function assertTabOrder(page,selectors,label){
  await page.locator(selectors[0]).focus();
  for(const [key,direction] of [['Tab',1],['Shift+Tab',-1]]){
   for(let step=1;step<=selectors.length;step++){
    await page.keyboard.press(key);const selector=selectors[(direction*step+selectors.length)%selectors.length];
    const actual=await page.evaluate(()=>document.activeElement.outerHTML.slice(0,240));
    assert.equal(await page.locator(selector).evaluate(node=>node===document.activeElement),true,label+' '+key+' reaches '+selector+', got '+actual);
    assert.equal(await page.locator(selector).isVisible(),true,label+' never focuses a hidden control');
    assert.equal(await page.evaluate(()=>!document.querySelector('#supportDetail').hidden&&document.querySelector('#supportDetailContent').contains(document.activeElement)&&!document.activeElement.closest('[hidden],[inert]')),true,label+' keeps focus in the active modal');
   }
  }
 }
 try{
  for(const width of [390,1280]){
   const focused=await mount(width);try{const {page}=focused;
    await page.evaluate(()=>{const f=notificationCaseFixture;f.caseMessages=[f.material,...['image','document'].map((kind,index)=>({...f.material,id:'20000000-0000-4000-8000-00000000060'+(index+2),external_id:'connect:case-'+kind,message_type:kind,media_filename:'case.'+(index?'pdf':'png')}))]});
    await page.locator('[data-support-id]').first().click();await page.locator('#supportDialogueHistory [data-message-id=outside-case]').waitFor();await page.locator('#supportTab-details').click();
    const summary=page.locator('#supportCaseMaterials>summary'),close=page.locator('#supportClose');
    assert.equal(await page.locator('#supportHistory [data-ticket-media-open]').count(),2);
    assert.equal(await page.locator('#supportCaseMaterials').evaluate(node=>node.open),false);
    const closed=['#supportClose','#supportTab-details','#supportPanel-details','#supportDescription','#supportSuggestions','#supportNextSteps','#supportEditStatus','#supportEditAssignee','#supportTake','#supportTakeover','#supportResolve','#supportSave','#supportCaseMaterials>summary'];
    const photo='#supportHistory [data-message-id="20000000-0000-4000-8000-000000000602"]',documentMessage='#supportHistory [data-message-id="20000000-0000-4000-8000-000000000603"]';
    const opened=[...closed,'#caseMediaLoad',photo+' [data-ticket-media-open]',documentMessage+' [data-ticket-media-open]'];
    await assertTabOrder(page,closed,'closed Materials '+width);
    assert.equal(await page.evaluate(()=>notificationCaseFixture.mediaCalls.length),0,'keyboard traversal never downloads attachments');
    await summary.focus();await page.keyboard.press('Enter');assert.equal(await page.locator('#supportCaseMaterials').evaluate(node=>node.open),true);
    await assertTabOrder(page,opened,'open Materials '+width);
    await page.locator(photo+' [data-ticket-media-open]').click();await page.locator(photo+' .support-media-preview button').waitFor();
    await page.locator(documentMessage+' [data-ticket-media-open]').click();await page.locator(documentMessage+' .support-media-preview a[download]').waitFor();
    const previews=[...closed,'#caseMediaLoad',photo+' [data-ticket-media-open]',photo+' .support-media-preview button',documentMessage+' [data-ticket-media-open]',documentMessage+' .support-media-preview a:not([download])',documentMessage+' .support-media-preview a[download]'];
    await assertTabOrder(page,previews,'open Materials with nested preview controls '+width);
    await summary.focus();await page.keyboard.press('Space');assert.equal(await page.locator('#supportCaseMaterials').evaluate(node=>node.open),false);
    await assertTabOrder(page,closed,'collapsed loaded Materials '+width);
    await summary.focus();await page.keyboard.press('Enter');await page.locator(photo+' [data-ticket-media-open]').click();
    await assertTabOrder(page,previews.filter(selector=>selector!==photo+' .support-media-preview button'),'hidden image preview '+width);
    await page.locator('#supportTab-details').focus();await page.keyboard.press('ArrowRight');assert.equal(await active(page),'developer');await page.locator('#consultCreate').waitFor();
    await close.focus();await page.keyboard.press('Shift+Tab');assert.equal(await page.locator('#supportConsultations>details>summary').evaluate(node=>node===document.activeElement),true,'hidden Details controls never become the Developer tab boundary: '+await page.evaluate(()=>document.activeElement.outerHTML));
    await page.keyboard.press('Tab');assert.equal(await close.evaluate(node=>node===document.activeElement),true);
    await page.keyboard.press('Tab');assert.equal(await page.locator('#supportTab-developer').evaluate(node=>node===document.activeElement),true,'only the active tab is in sequential focus order');
    assert.deepEqual(await page.locator('#supportDetail [role=tab]').evaluateAll(tabs=>tabs.map(tab=>[tab.getAttribute('aria-selected'),tab.tabIndex])),[['false',-1],['false',-1],['true',0]]);
    await page.keyboard.press('Home');assert.equal(await active(page),'history');await page.keyboard.press('ArrowRight');assert.equal(await active(page),'details');
    assert.equal(await page.locator('#supportDetail [role=tabpanel]:visible').count(),1);assert.equal(await page.locator('#supportCaseMaterials').evaluate(node=>node.open),true,'tab changes preserve Materials expansion');
    await page.keyboard.press('Escape');await page.locator('#supportDetail').waitFor({state:'hidden'});await page.waitForURL(url=>!url.searchParams.has('support_ticket_id'));
    assert.equal(await page.locator('#supportDetail').evaluate(node=>node.contains(document.activeElement)),false,'Escape never leaves focus in the closed modal');
    assert.deepEqual(focused.errors,[]);console.log('PASS case focus Chrome '+width+': complete Tab/Shift+Tab cycles, closed/open Materials, nested and hidden previews, hidden tabs, ARIA, summary Enter/Space, Escape');
   }finally{await focused.context.close();}
  }
  for(const width of [320,390,1280]){
   const {context,page,errors}=await mount(width);
   try{
    await page.locator('[data-support-id]').first().click();await page.locator('#supportDialogueHistory [data-message-id=outside-case]').waitFor();
    assert.equal(await active(page),'history');assert.equal(await page.locator('#supportDetail [role=tab]').count(),3);assert.equal(await page.locator('#supportDetail [role=tabpanel]:visible').count(),1);
    assert.equal(await page.locator('#supportDialogueHistory [data-reply-message]').count(),0,'read-only display never offers a dead composer action');
    assert.equal(await page.locator('#supportHistory [data-message-id=outside-case]').count(),0,'whole history never expands case materials');
    assert.equal(await page.evaluate(()=>notificationCaseFixture.historyCalls.every(q=>q.provider==='connect'&&q.lead_id==='41'&&q.channel_id===notificationCaseFixture.channel&&q.conversation_id===notificationCaseFixture.route.conversation_id)),true);
    assert.equal(await page.evaluate(()=>{const expected=document.createElement('template');expected.innerHTML=tenantOperator.messageMarkup(notificationCaseFixture.history,notificationCaseFixture.route,{readOnly:true});return expected.content.firstElementChild.outerHTML===document.querySelector('#supportDialogueHistory .operator-message').outerHTML;}),true,'history uses the actual dialogue component');
    assert.equal(await page.evaluate(()=>notificationCaseFixture.mediaCalls.length),0,'case history does not auto-download media');
    await page.locator('#supportDialogueHistory [data-message-id=history-photo] [data-media-open]').click();await page.locator('#supportDialogueHistory [data-media-preview=image]').waitFor();
    assert.equal(await page.evaluate(()=>notificationCaseFixture.mediaCalls[0].conversation_id===notificationCaseFixture.route.conversation_id),true);
    assert.equal(await page.locator('#supportDialogueHistory [data-message-id=history-audio] [data-media-open]').isVisible(),true,'read-only audio has a working explicit loader');
    await page.locator('#supportDialogueHistory [data-media-preview=image]').click();await page.locator('.operator-photo-viewer').waitFor();await page.goBack();await page.locator('.operator-photo-viewer').waitFor({state:'detached'});assert.equal(await page.locator('#supportDetail').isVisible(),true,'photo Back does not close or recreate the case');
    const download=page.waitForEvent('download');await page.locator('#supportDialogueHistory [data-message-id=history-photo] [data-media-download]').click();await download;assert.equal(await page.evaluate(()=>notificationCaseFixture.mediaCalls.at(-1).download),'1','existing guarded download is usable');
    await page.locator('#supportDialogueEarlier').click();await page.locator('#supportDialogueHistory [data-message-id=oldest]').waitFor();
    assert.equal(await page.locator('#supportDialogueEarlier').isVisible(),false);
    for(const tab of ['history','details','developer']){
     await page.locator('#supportTab-'+tab).click();
     const geometry=await page.locator('#supportDetailContent').evaluate(card=>({overflow:card.scrollWidth>card.clientWidth+1,tabs:[...card.querySelectorAll('[role=tab]')].map(tab=>{const r=tab.getBoundingClientRect();return {left:r.left,right:r.right,height:r.height,hit:tab.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}})}));
     assert.equal(geometry.overflow,false,tab+' fits '+width);assert(geometry.tabs.every(r=>r.left>=0&&r.right<=width&&r.height>=44&&r.hit));
     await page.screenshot({path:path.join(artifacts,`${width}-${tab}.png`)});
    }
    await page.locator('#supportTab-history').focus();await page.keyboard.press('ArrowRight');assert.equal(await active(page),'details');await page.keyboard.press('End');assert.equal(await active(page),'developer');await page.keyboard.press('Home');assert.equal(await active(page),'history');
    await page.locator('#supportTab-details').click();await page.locator('#supportDescription').fill('Private unsaved case draft');await page.locator('#supportTab-developer').click();await page.locator('#consultCreate').click();await page.locator('#consultReason').fill('Printer remains offline');
    assert.equal(await page.locator('#caseConsultRecipientConfirm,#caseConsultIdentityConsent').count(),0);assert.match(await page.locator('#consultRecipient option:checked').textContent(),/Rauf/);
    assert.deepEqual(await page.locator('#caseConsultFirst option').evaluateAll(nodes=>nodes.map(n=>n.value)),[await page.evaluate(()=>notificationCaseFixture.material.id)]);
    assert.equal(await page.evaluate(()=>notificationCaseFixture.prepared.length),0);
    await page.locator('#supportTab-details').click();assert.equal(await page.locator('#supportDescription').inputValue(),'Private unsaved case draft');await page.locator('#supportTab-developer').click();assert.equal(await page.locator('#consultReason').inputValue(),'Printer remains offline');
    await page.locator('#supportClose').click();await page.locator('#supportDetail').waitFor({state:'hidden'});await page.waitForURL(u=>!u.searchParams.has('support_ticket_id'));await page.locator('[data-support-id]').first().click();await page.locator('#supportTab-developer').waitFor();assert.equal(await active(page),'developer');await page.locator('#consultCreate').click();await page.locator('#consultReason').waitFor();assert.equal(await page.locator('#consultReason').inputValue(),'Printer remains offline');
    await page.locator('#caseConsultSend').click();await page.locator('#consultRows').getByText(/принял запрос в очередь/).waitFor();
    const sends=await page.evaluate(()=>({prepared:notificationCaseFixture.prepared,dispatched:notificationCaseFixture.dispatched}));assert.equal(sends.prepared.length,1);assert.equal(sends.dispatched.length,1);assert.equal(sends.prepared[0].public_dialog_version,1);assert.deepEqual(sends.prepared[0].public_sender_versions,{[await page.evaluate(()=>notificationCaseFixture.material.id)]:'b'.repeat(64)});assert.equal(sends.prepared[0].include_customer_identity,false);assert.equal(sends.prepared[0].confirm,true);assert.equal(sends.prepared[0].request_id,sends.dispatched[0].request_id);
    await page.locator('#supportTab-details').click();await page.reload();await page.locator('#supportSummaryText').waitFor();assert.equal(await active(page),'details','refresh restores tab in exact case URL');
    await page.locator('#supportTab-history').click();await page.locator('#supportOpenChat').click();await page.locator('#supportDetail').waitFor({state:'hidden'});assert.equal(new URL(page.url()).searchParams.has('support_ticket_id'),false);
    assert.deepEqual(errors,[]);console.log('PASS case tabs Chrome '+width+': geometry, shared history/media, scope separation, ARIA, drafts, Send, privacy, reload/navigation');
   }finally{await context.close();}
  }
  const b=await mount(390);try{const {page}=b;
   await page.evaluate(()=>{const f=notificationCaseFixture;f.consultations=[{id:'consult-one',case_id:f.caseA,state:'sent',recipient:'Rauf'}];f.replies=[{id:'reply-one',case_id:f.caseA,consultation_id:'consult-one',author:'Rauf',body:'Only this case answer',source_kind:'manual',draft_state:'completed'},{id:'other-case-reply',case_id:f.caseB,consultation_id:'consult-other',body:'Different case answer'}]});
   assert.equal(await page.evaluate(()=>tenantSupportInbox.openNotification({ticketId:notificationCaseFixture.caseA,replyId:'reply-one'})),true);assert.equal(await active(page),'developer');assert.equal(await page.locator('[data-consultation-reply=reply-one]').evaluate(n=>n===document.activeElement),true);assert.doesNotMatch(await page.locator('#consultRows').textContent(),/Different case/);
   const deep=page.url();assert.equal(new URL(deep).searchParams.get('consultation_reply_id'),'reply-one');
   await page.locator('#supportClose').click();await page.locator('#supportDetail').waitFor({state:'hidden'});await page.waitForURL(u=>!u.searchParams.has('support_ticket_id'));
   await page.evaluate(()=>{notificationCaseFixture.holdHistory=true});await page.locator('[data-support-id]').nth(1).click();await page.locator('#supportTab-history').click();await page.waitForFunction(()=>notificationCaseFixture.pendingHistory.length===1);await page.keyboard.press('Escape');await page.locator('#supportDetail').waitFor({state:'hidden'});await page.evaluate(()=>notificationCaseFixture.pendingHistory.shift()());assert.equal(await page.locator('#supportDialogueHistory [data-message-id]').count(),0,'late history cannot reappear after close');
   await page.waitForURL(u=>!u.searchParams.has('support_ticket_id'));await page.locator('[data-support-id]').first().click();await page.locator('#supportTab-history').click();await page.evaluate(()=>{notificationCaseFixture.wrongHistory=true});await page.locator('#supportDialogueRefresh').click();await page.locator('#supportDialogueNotice').filter({hasText:/./}).waitFor();assert.equal(await page.locator('#supportDialogueHistory').textContent().then(x=>x.includes('wrong-client')),false);
   assert.deepEqual(b.errors,[]);console.log('PASS case tabs: exact reply focus, foreign case exclusion, late response dismissal, route mismatch fail-closed');
  }finally{await b.context.close();}
  const deep=await mount(390,'&support_ticket_id=a0000000-0000-4000-8000-00000000b301&consultation_reply_id=reply-one');try{const {page}=deep;await page.locator('[data-consultation-reply=reply-one]').waitFor();assert.equal(await active(page),'developer');assert.equal(await page.locator('[data-consultation-reply=reply-one]').evaluate(n=>n===document.activeElement),true);await page.locator('#supportTab-history').click();assert.equal(new URL(page.url()).searchParams.has('consultation_reply_id'),false);await page.reload();await page.locator('#supportDialogueHistory [data-message-id=outside-case]').waitFor();assert.equal(await active(page),'history');}finally{await deep.context.close();}
  const stale=await mount(390);try{const {page}=stale;await page.locator('[data-support-id]').first().click();await page.locator('#supportTab-developer').click();await page.locator('#consultCreate').click();await page.locator('#consultReason').fill('Pending request');await page.evaluate(()=>notificationCaseFixture.holdPrepare=true);await page.locator('#caseConsultSend').click();await page.waitForFunction(()=>notificationCaseFixture.pendingPrepare.length===1);await page.keyboard.press('Escape');await page.locator('#supportDetail').waitFor({state:'hidden'});await page.evaluate(()=>notificationCaseFixture.pendingPrepare.shift()());assert.equal(await page.evaluate(()=>notificationCaseFixture.dispatched.length),0,'prepare finishing after close never dispatches');await page.waitForURL(u=>!u.searchParams.has('support_ticket_id'));await page.evaluate(()=>notificationCaseFixture.holdHistory=true);await page.locator('[data-support-id]').nth(1).click();await page.waitForFunction(()=>notificationCaseFixture.pendingHistory.length===1);await page.evaluate(async()=>{notificationCaseFixture.profile.capabilities.modules.dialogs=false;tenantRequestCoordinator.reset();await tenantReloadProfile();});await page.evaluate(()=>notificationCaseFixture.pendingHistory.shift()());await page.waitForFunction(()=>!document.querySelector('#supportDialogueHistory [data-message-id=outside-case]'));assert.equal(await page.locator('#supportDialogueHistory [data-message-id]').count(),0,'pending history is discarded when dialogue rights are revoked');assert.deepEqual(stale.errors,[]);console.log('PASS fresh reply deeplink, explicit-tab reload, delayed prepare close, pending history rights revocation');}finally{await stale.context.close();}
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
