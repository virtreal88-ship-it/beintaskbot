/* Actual app and shared dialogue renderer, synthetic API boundary, real Chromium. */
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const {docs,assets,appHtml,installNotificationCaseFixture}=require('./notification_case_targets_fixture.cjs');
function installCaseTabsFixture(){
 const f=window.notificationCaseFixture,base=window.fetch;
 f.holdPrepare=false;f.pendingPrepare=[];f.historyCalls=[];f.prepared=[];f.dispatched=[];f.mediaCalls=[];f.pendingHistory=[];f.holdHistory=false;f.wrongHistory=false;f.consultations=[];f.replies=[];
 f.caseReplies=[];f.sends=[];f.sendChecks=[];f.replyAllowed=true;f.sendOutcome='accepted';
 f.removals=[];
 f.material={id:'20000000-0000-4000-8000-000000000601',external_id:'connect:material',provider_message_id:'material',source:'connect',author_type:'customer',author_name:'Aysel',direction:'incoming',body:'Printer is offline. Can you help?',message_type:'text',happened_at:'2026-11-02T10:00:00Z'};
 f.history=[{...f.material,id:'outside-case',provider_message_id:'older-question',body:'Earlier dialogue message outside this case',happened_at:'2026-11-01T09:00:00Z'},f.material,{...f.material,id:'operator-answer',external_id:'connect:answer',provider_message_id:'answer',direction:'outgoing',author_type:'operator',body:'Please check the printer cable.',happened_at:'2026-11-02T10:01:00Z'},{...f.material,id:'history-photo',external_id:'connect:photo',body:'Printer display',message_type:'image',media_filename:'printer.png'},{...f.material,id:'history-audio',external_id:'connect:audio',body:'',message_type:'audio',media_filename:'message.ogg'}];
 f.history=f.history.map(m=>({...m,source_lead_id:41,source_channel_id:f.channel,source_conversation_id:f.route.conversation_id}));
 if(new URL(location.href).searchParams.get('consultation_reply_id')==='reply-one'){f.consultations=[{id:'consult-one',case_id:f.caseA,state:'sent',recipient:'Rauf'}];f.replies=[{id:'reply-one',case_id:f.caseA,consultation_id:'consult-one',body:'Persisted case reply',source_kind:'manual',draft_state:'completed'}];}
 const response=(result,status=200)=>new Response(JSON.stringify(status<400?{success:true,result}:{success:false,error:result}),{status,headers:{'Content-Type':'application/json'}});
 window.fetch=async(input,options={})=>{
  const url=new URL(String(input),location),p=url.pathname,body=options.body?JSON.parse(options.body):null;
  if(p.startsWith('/api/platform/ai/support-tickets/')&&options.method==='DELETE'){
   const id=p.split('/').at(-1);
   if(f.profile.member.role!=='owner'||body?.expected_tenant_id!==f.tenant||body?.expected_user_id!==11)return response('Denied',403);
   f.removals.push(id);
   f.tickets=f.tickets.filter(row=>row.id!==id);
   return response({id,deleted:true});
  }
  if(p.startsWith('/api/platform/ai/support-tickets/')&&url.searchParams.get('reply_context')==='1'){
   const id=p.split('/').at(-1),ticket=f.tickets.find(row=>row.id===id);
   if(!ticket||url.searchParams.get('expected_tenant_id')!==f.tenant||url.searchParams.get('expected_user_id')!=='11')return response('Denied',403);
   return response({route:{lead_id:ticket.lead_id,channel_id:ticket.channel_id,conversation_id:ticket.conversation_id},can_send:f.replyAllowed,messages:f.caseReplies.filter(row=>row.caseId===id)});
  }
  if(p.endsWith('/crm/chat/send')&&options.method==='POST'){
   f.sends.push(body);
   if(f.sendOutcome==='denied')return new Response(JSON.stringify({success:false,keep_request:false,error:'Denied'}),{status:403});
   const accepted=f.sendOutcome==='accepted';
   if(accepted)f.caseReplies.push({caseId:body.support_ticket_id,id:body.request_id,external_id:'connect:'+body.request_id,direction:'outgoing',author_name:'Synthetic operator',body:body.text,message_type:'text',happened_at:'2026-11-27T12:00:00Z'});
   return new Response(JSON.stringify(accepted?{success:true,state:'accepted',message_id:body.request_id}:{success:false,keep_request:true,error:'Unknown'}),{status:accepted?200:503});
  }
  if(p.endsWith('/crm/chat/send-status')){
   f.sendChecks.push(Object.fromEntries(url.searchParams));return new Response(JSON.stringify({success:true,state:'unknown'}));
  }
  if(p.endsWith('/dialogs/history')){f.historyCalls.push(Object.fromEntries(url.searchParams));const result={dialog:{...f.route,...(f.wrongHistory?{conversation_id:'wrong-client'}:{})},messages:url.searchParams.has('before')?[{...f.material,id:'oldest',body:'Oldest dialogue message'}]:f.history,next_before:url.searchParams.has('before')?null:'history-cursor'};if(f.holdHistory){f.holdHistory=false;return new Promise(resolve=>f.pendingHistory.push(()=>resolve(response(result))))}return response(result);}
  if(p.endsWith('/dialog-consultations/replies')){f.lastConsultationCase=url.searchParams.get('case_id');return response({consultations:f.consultations.filter(row=>row.case_id===f.lastConsultationCase),replies:f.replies.filter(row=>row.case_id===f.lastConsultationCase)});}
  if(p.endsWith('/dialog-consultations/destinations'))return response({destinations:[{channel_id:'dev-channel',conversation_id:'dev-conversation',customer_name:'Rauf · Developer'}]});
  if(p.endsWith('/dialog-consultations/preview'))return response({case_history_hash:'c'.repeat(64),scope:{message_count:1},public_header:{version:1,customer_name:'Synthetic customer',phone_status:'unknown',company_name:'Synthetic client',operator_name:'Synthetic operator'},public_header_hash:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',case_id:body.case_id,messages:[{...f.material,public_sender_version:'b'.repeat(64),media_shareable:false,source_version:'synthetic-v1'}],customer_identity_preview:{name:'Private identity',verified_phone:'+15551112222'}});
  if(p.endsWith('/dialog-consultations')&&options.method==='POST'){f.prepared.push(body);f.consultations=[{id:'consult-one',case_id:body.case_id,request_id:body.request_id,recipient:'Rauf · Developer',reason:body.summary,state:'ready',can_dispatch:true,can_send:true,destination_channel:'dev-channel',destination_conversation:'dev-conversation',created_at:'2026-11-02T12:00:00Z',expires_at:'2099-11-09T12:00:00Z'}];if(f.holdPrepare)return new Promise(resolve=>f.pendingPrepare.push(()=>resolve(response({id:'consult-one'}))));return response({id:'consult-one'});}
  if(p.endsWith('/consult-one/dispatch')){f.dispatched.push(body);f.consultations[0].state='pending';f.consultations[0].can_dispatch=false;return response({state:'pending'});}
  if(p.endsWith('/chat/attachment')){f.mediaCalls.push(Object.fromEntries(url.searchParams));return new Response(Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9WQAAAAASUVORK5CYII='),c=>c.charCodeAt(0)),{headers:{'Content-Type':'image/png'}});}
  const result=await base(input,options);
  if(p.startsWith('/api/platform/ai/support-tickets/')&&result.ok){const data=await result.json();if(data.result?.ticket){data.result.capabilities={delete:f.profile.member.role==='owner'};data.result.messages=url.searchParams.has('before')?[{...f.material,id:'oldest',body:'Oldest accepted-interval message'}]:(f.caseMessages||f.history.filter(row=>row.id!=='outside-case'));data.result.next_before=url.searchParams.has('before')?null:'case-cursor';data.result.history_context={boundary_status:'accepted_interval',accepted_at:'2026-11-02T09:00:00Z',created_at:'2026-11-02T12:00:00Z'};data.result.scope={message_count:data.result.messages.length,media_count:data.result.messages.filter(row=>row.message_type!=='text').length,has_earlier_source:true};}if(f.holdHistory&&url.searchParams.has('before')){f.holdHistory=false;return new Promise(resolve=>f.pendingHistory.push(()=>resolve(response(data.result))))}return new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});}
  return result;
 };
}
(async()=>{
 const artifacts=path.join(__dirname,'../output/case-card-tabs');fs.mkdirSync(artifacts,{recursive:true});
 const server=http.createServer((req,res)=>{const u=new URL(req.url,'http://test');if(u.pathname.startsWith('/assets/')){const name=path.basename(u.pathname);if(![...assets,'tenant-chat-attachments.js'].includes(name)){res.writeHead(404);res.end();return;}res.setHeader('content-type','text/javascript');res.end(fs.readFileSync(path.join(docs,name)));return;}res.setHeader('content-type','text/html');res.end(appHtml().replace('</body>','<script src="/assets/tenant-chat-attachments.js"></script></body>'));});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||process.env.CHROMIUM_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':fs.existsSync('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe')?'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe':undefined)});
 const active=page=>page.locator('#supportDetail [role=tab][aria-selected=true]:visible').getAttribute('data-support-tab');
 const showData=async page=>{const tab=page.locator('#supportTab-details');if(await tab.isVisible())await tab.click();};
 async function mount(width,query=''){
  const context=await browser.newContext({viewport:{width,height:900},colorScheme:'dark'}),page=await context.newPage(),errors=[];
  await context.addInitScript(installNotificationCaseFixture);await context.addInitScript(installCaseTabsFixture);page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());await page.goto(origin+'/app?view=support&lang=ru'+query);await page.locator('[data-support-id]').first().waitFor();return {context,page,errors};
 }
 try{
  for(const width of [390,1280]){
   const {context,page,errors}=await mount(width);
   try{
    const id=await page.locator('[data-support-id]').first().getAttribute('data-support-id');
    const row=page.locator('[data-support-id="'+id+'"]');
    await row.click();await showData(page);
    const remove=page.getByRole('button',{name:'Удалить',exact:true});
    await page.evaluate(()=>{window.confirm=()=>false;});
    await remove.click();assert.equal(await page.evaluate(()=>notificationCaseFixture.removals.length),0);
    await page.evaluate(()=>{window.confirm=()=>true;});
    await remove.evaluate(button=>{button.click();button.click();});
    await page.locator('#supportDetail').waitFor({state:'hidden'});
    await row.waitFor({state:'detached'});
    assert.deepEqual(await page.evaluate(()=>notificationCaseFixture.removals),[id]);
    assert.deepEqual(errors,[]);
    console.log('PASS owner deletes a case with confirmation at '+width+'px');
   }finally{await context.close();}
  }
  for(const width of [320,390,1280]){const {context,page,errors}=await mount(width);try{
   await page.locator('[data-support-id]').first().click();await page.locator('#supportHistory').waitFor();
   assert.equal(await active(page),'history');
   const tabs=page.locator('#supportDetail [role=tab]:visible');
   assert.deepEqual(await tabs.allTextContents(),width>=900?['Чат','Лента','Диалог']:['Чат','Данные','Лента','Диалог']);
   assert.equal(await page.locator('.support-card-header [role=tab]').count(),0);
   assert.equal(await page.locator('#supportEditStatus').isVisible(),width>=900);
   if(width>=900){const [left,right,strip]=await Promise.all([page.locator('#supportPanel-details').boundingBox(),page.locator('.support-case-main').boundingBox(),tabs.first().boundingBox()]);assert(left.x+left.width<=right.x&&left.y===right.y&&strip.x>=right.x);}
   await page.locator('#supportReplyText').waitFor();
   await page.locator('#supportDetailContent').screenshot({path:path.join(artifacts,'reference-'+width+'.png')});
   await page.locator('#supportTab-history').click();await page.locator('#supportHistory [data-message-id=history-photo]').waitFor();
   for(const [locale,label]of [['az','Lent'],['en','Activity'],['ru','Лента']]){await page.evaluate(value=>tenantI18n.setLanguage(value),locale);await page.waitForFunction(text=>document.getElementById('supportTab-developer')?.textContent===text,label);const sizes=await page.locator('.support-case-tabs').evaluate(tabs=>({overflow:tabs.scrollWidth>tabs.clientWidth+1,words:[...tabs.querySelectorAll('button:not([hidden])')].flatMap(button=>{const text=button.firstChild;if(!text||text.nodeType!==Node.TEXT_NODE)return[];return [...text.textContent.matchAll(/\S+/g)].map(match=>{const range=document.createRange();range.setStart(text,match.index);range.setEnd(text,match.index+match[0].length);const rects=[...range.getClientRects()],box=button.getBoundingClientRect();return{word:match[0],lines:rects.length,fits:rects.every(r=>r.left>=box.left&&r.right<=box.right+1)};});})}));assert.equal(sizes.overflow,false,locale+' tabs fit '+width);for(const word of sizes.words){assert.equal(word.lines,1,word.word+' never splits inside the word at '+width);assert(word.fits,word.word+' fits its tab at '+width);}}

   assert.equal(await page.locator('#supportHistory [data-message-id=outside-case]').count(),0,'out-of-case messages never expand the history');
   assert.equal(await page.evaluate(()=>notificationCaseFixture.historyCalls.length),0,'never calls unrestricted dialogue history');
   assert.equal(await page.evaluate(()=>notificationCaseFixture.mediaCalls.length),0,'no automatic media downloads');
   assert.equal(await page.locator('#supportSummary,#supportSummaryText,#supportCaseMaterials,#supportCaseContext,#supportAnalysisFields,#supportMoreActions,#supportTakeover,#caseMediaLoad').count(),0);
   await page.locator('#supportHistory [data-message-id=history-photo] [data-ticket-media-open]').click();await page.locator('#supportHistory .support-media-preview img').waitFor();
   assert.equal(await page.evaluate(()=>notificationCaseFixture.mediaCalls[0].conversation_id===notificationCaseFixture.route.conversation_id),true,'attachment retains exact conversation guard');
   await page.locator('#supportEarlier').click();await page.locator('#supportHistory [data-message-id=oldest]').waitFor();assert.equal(await page.locator('#supportEarlier').isVisible(),false);
   const composer=page.locator('#supportReplyText'),send=page.locator('#supportReplySend');
   await composer.fill('Reply from the case Chat');
   await send.evaluate(button=>{button.click();button.click();});
   await page.locator('#supportCaseReplies').getByText('Reply from the case Chat').waitFor();
   assert.equal(await active(page),'history');
   assert.equal(await composer.inputValue(),'');
   assert.deepEqual(await page.evaluate(()=>notificationCaseFixture.sends.map(({support_ticket_id,expected_tenant_id,expected_user_id,lead_id})=>[support_ticket_id,expected_tenant_id,expected_user_id,lead_id])),await page.evaluate(()=>[[notificationCaseFixture.caseA,notificationCaseFixture.tenant,11,41]]));
   assert.equal(await page.evaluate(()=>notificationCaseFixture.historyCalls.length),0,'sending does not open unrestricted dialogue history');
   await composer.fill('Unsent case reply draft');
   await page.locator('#supportTab-dialog').click();
   await page.locator('#supportLinkedDialog #chatText').waitFor();
   const request=await page.evaluate(()=>notificationCaseFixture.historyCalls[0]);
   assert.equal(request.channel_id,await page.evaluate(()=>notificationCaseFixture.channel));
   assert.equal(request.conversation_id,await page.evaluate(()=>notificationCaseFixture.route.conversation_id));
   await page.locator('#supportTab-history').click();
   assert.equal(await composer.inputValue(),'Unsent case reply draft');
   assert.equal(await page.locator('#supportCaseReplies').getByText('Reply from the case Chat').count(),1);
   assert.equal(await page.locator('#supportLinkedDialog #operatorConversationPane').count(),0);
   assert.equal(await page.locator('#supportHistory [data-message-id=outside-case]').count(),0);
   if(width===390){
    await page.evaluate(()=>{notificationCaseFixture.sendOutcome='unknown'});
    await composer.press('Enter');
    await page.waitForFunction(()=>/Результат отправки неизвестен/.test(document.getElementById('supportCaseComposer').textContent));
    await send.click();await page.waitForFunction(()=>notificationCaseFixture.sendChecks.length===1);
    const outcome=await page.evaluate(()=>({sent:notificationCaseFixture.sends.length,checked:notificationCaseFixture.sendChecks[0].request_id,pending:notificationCaseFixture.sends[1].request_id}));
    assert.equal(outcome.sent,2);assert.equal(outcome.checked,outcome.pending);
    assert.equal(await composer.inputValue(),'Unsent case reply draft');
    await page.locator('#supportTab-developer').click();
    await page.evaluate(()=>{notificationCaseFixture.replyAllowed=false});
    await page.locator('#supportTab-history').click();
    await page.getByText('Отправка из этого обращения недоступна.').waitFor();
    assert.equal(await composer.count(),0);
    await page.locator('#supportTab-developer').click();
    await page.evaluate(()=>{notificationCaseFixture.replyAllowed=true});
    await page.locator('#supportTab-history').click();
   }
   if(width===320){
    await page.evaluate(()=>{notificationCaseFixture.sendOutcome='denied'});
    await send.click();
    await page.getByText('Отправка из этого обращения недоступна.').waitFor();
    assert.equal(await composer.isVisible(),false);
    assert.equal(await composer.inputValue(),'Unsent case reply draft');
    await page.evaluate(()=>{notificationCaseFixture.sendOutcome='accepted'});
   }
   for(const tab of (width>=900?['history','developer','dialog']:['history','details','developer','dialog'])){await page.locator('#supportTab-'+tab).click();assert.equal(await page.locator('#supportDetail [role=tabpanel]:visible').count(),1);assert.equal(await page.locator('#supportDetailContent').evaluate(n=>n.scrollWidth<=n.clientWidth+1),true,tab+' fits '+width);
    await page.locator('#supportClose').focus();for(let step=0;step<18;step++){await page.keyboard.press(step<9?'Tab':'Shift+Tab');assert.equal(await page.evaluate(()=>document.querySelector('#supportDetailContent').contains(document.activeElement)&&!document.activeElement.closest('[hidden],[inert]')),true,'focus remains in visible modal controls');}
    await page.screenshot({path:path.join(artifacts,width+'-'+tab+'.png')});}
   await page.locator('#supportTab-history').focus();await page.keyboard.press('ArrowRight');assert.equal(await active(page),width>=900?'developer':'details');await page.keyboard.press('End');assert.equal(await active(page),'dialog');await page.keyboard.press('Home');assert.equal(await active(page),'history');
   await showData(page);await page.locator('#supportCaseEdit>summary').click();await page.locator('#supportDescription').fill('Private unsaved case draft');await page.locator('#supportTab-developer').click();await page.locator('#consultCreate').click();await page.locator('#consultReason').fill('Printer remains offline');
   assert.equal(await page.locator('#caseConsultFirst,#caseConsultLast,#caseConsultRecipientConfirm,#caseConsultIdentityConsent').count(),0);assert.equal(await page.evaluate(()=>notificationCaseFixture.prepared.length),0);
   await showData(page);assert.equal(await page.locator('#supportDescription').inputValue(),'Private unsaved case draft');await page.locator('#supportTab-developer').click();assert.equal(await page.locator('#consultReason').inputValue(),'Printer remains offline');
   await page.locator('#caseConsultSend').click();await page.waitForFunction(()=>notificationCaseFixture.dispatched.length===1);
   const sends=await page.evaluate(()=>({prepared:notificationCaseFixture.prepared,dispatched:notificationCaseFixture.dispatched}));assert.equal(sends.prepared.length,1);assert.equal(sends.prepared[0].public_dialog_version,1);assert.equal(sends.prepared[0].include_customer_identity,false);assert.equal(sends.prepared[0].automatic_case_history,true);assert.equal(sends.prepared[0].case_history_hash,'c'.repeat(64));for(const key of ['message_ids','media_ids','public_sender_versions','public_header_source_versions','question_draft_request_id'])assert.equal(key in sends.prepared[0],false);assert.equal(sends.prepared[0].request_id,sends.dispatched[0].request_id);
   await page.locator('#supportTab-history').click();await showData(page);await page.reload();await page.locator('#supportEditStatus').waitFor();assert.equal(await active(page),width>=900?'history':'details','reload restores exact case tab');
   await page.keyboard.press('Escape');await page.locator('#supportDetail').waitFor({state:'hidden'});assert.equal(await page.locator('#supportDetail').evaluate(n=>n.contains(document.activeElement)),false);
   assert.deepEqual(errors,[]);console.log('PASS scoped case tabs '+width+': history, lazy media, pagination, retired controls, ARIA, focus, drafts, explicit send, reload');
  }finally{await context.close();}}
  const stale=await mount(390);try{const {page}=stale;await page.locator('[data-support-id]').first().click();await page.locator('#supportTab-history').click();await page.locator('#supportHistory').waitFor();const initial=await page.locator('#supportHistory').innerHTML();await page.evaluate(()=>notificationCaseFixture.holdHistory=true);await page.locator('#supportEarlier').click();await page.waitForFunction(()=>notificationCaseFixture.pendingHistory.length===1);await page.keyboard.press('Escape');await page.locator('#supportDetail').waitFor({state:'hidden'});await page.evaluate(()=>notificationCaseFixture.pendingHistory.shift()());assert.equal(await page.locator('#supportDetail').isVisible(),false,'late case pagination cannot revive closed modal');assert.equal(await page.locator('#supportHistory').innerHTML(),initial,'late page does not mutate retained hidden history');assert.deepEqual(stale.errors,[]);}finally{await stale.context.close();}
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
