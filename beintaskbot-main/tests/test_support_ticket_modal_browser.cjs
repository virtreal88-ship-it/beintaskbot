const assert=require('node:assert/strict');
const fs=require('node:fs');
const http=require('node:http');
const path=require('node:path');
const {chromium}=require(process.env.SUPPORT_CASES_PLAYWRIGHT||'../.support-test-deps/node_modules/playwright');
const root=path.join(__dirname,'..');
const ticketId='c0ffee00-0000-4000-8000-000000000001';
const ticket={id:ticketId,contact_name:'Synthetic overnight ticket',channel_id:'channel-a',channel_name:'Synthetic WhatsApp',conversation_id:'conversation-a',lead_id:41,description:'Synthetic issue description',summary:'Synthetic persisted summary',summary_state:'completed',suggestions:'Synthetic check already done',next_steps:'Synthetic next step',status:'waiting',assigned_to:null,assignee_name:'Support',source:'ai_handoff',reason:'Synthetic reason',created_at:'2026-10-31T08:00:00Z',updated_at:'2026-10-31T08:30:00Z',last_message_at:'2026-10-31T08:30:00Z',ai_state:'human',agent_id:'agent-a'};
const message={id:'d0ffee00-0000-4000-8000-000000000001',external_id:'connect:synthetic-photo',direction:'incoming',body:'Synthetic attachment metadata',message_type:'image',happened_at:'2026-10-31T08:25:00Z',media_filename:'synthetic.png',media_size_bytes:3};
const earlierMessage={...message,id:'e0ffee00-0000-4000-8000-000000000002',external_id:'connect:synthetic-document',message_type:'document',media_filename:'synthetic.txt'};
const baseConsult='/api/platform/ai/dialog-consultations';
const textMessage={...message,id:'a0ffee00-0000-4000-8000-000000000003',external_id:'connect:synthetic-text',message_type:'text',body:'Printer cannot connect',happened_at:'2026-10-31T08:20:00Z'};
const audioMessage={...message,id:'a0ffee00-0000-4000-8000-000000000004',external_id:'connect:synthetic-audio',message_type:'audio',body:'',happened_at:'2026-10-31T08:21:00Z'};
const videoMessage={...message,id:'a0ffee00-0000-4000-8000-000000000005',external_id:'connect:synthetic-video',message_type:'video',body:'',happened_at:'2026-10-31T08:22:00Z'};
const initialMessages=[textMessage,audioMessage,videoMessage,message];
let mediaRequests=0,holdConsultations=false,consultationWaiter=null,releaseConsultations=null,consultations=[],replies=[],prepared=[],dispatched=[],unexpectedPosts=[],rejectPrepare=false;
function holdNextConsultation(){holdConsultations=true;return new Promise(resolve=>{consultationWaiter=resolve;});}
const server=http.createServer(async(req,res)=>{
  const pathname=new URL(req.url,'http://fixture').pathname;let body={};if(req.method==='POST'){for await(const chunk of req){body.raw=(body.raw||'')+chunk;}body=body.raw?JSON.parse(body.raw):{};if(![baseConsult+'/preview',baseConsult,baseConsult+'/synthetic-consultation/dispatch'].includes(pathname))unexpectedPosts.push(pathname);}
  if(req.url.startsWith('/app')){res.writeHead(200,{'content-type':'text/html'});res.end('<!doctype html><html><head><style>:root{--card:#fff;--ink:#17212b;--line:#d8e0ea;--paper:#f4f7fb;--muted:#64748b;--brand:#0f766e;--app-warning-bg:#fffbeb;--app-warning-ink:#92400e}body{font:15px system-ui,sans-serif;color:var(--ink);background:#f3f5f7;margin:24px}button,input,select,textarea{font:inherit}button{border:1px solid var(--line);border-radius:8px;padding:8px 12px;background:var(--card);color:var(--ink)}#operatorSupport{max-width:1200px;margin:auto}</style></head><body><section id="view-support"><div id="operatorSupport"></div></section></body></html>');return;}if(req.url==='/favicon.ico'){res.writeHead(204);res.end();return;}
  const headers={'content-type':'application/json','cache-control':'no-store'};
  if(req.url.startsWith('/api/platform/ai/support-tickets?')){res.writeHead(200,headers);res.end(JSON.stringify({success:true,result:{tickets:[ticket],members:[{telegram_id:11,display_name:'Synthetic Support'}],channels:[],next_offset:null,unresolved_count:1}}));return;}
  if(req.url==='/api/platform/ai/support-tickets/'+ticketId&&req.method==='GET'){res.writeHead(200,headers);res.end(JSON.stringify({success:true,result:{ticket,messages:initialMessages,attached_media:[],expert_results:[],next_before:'older-cursor'}}));return;}
  if(req.url.startsWith('/api/platform/ai/support-tickets/'+ticketId+'?before=')){res.writeHead(200,headers);res.end(JSON.stringify({success:true,result:{ticket,messages:[earlierMessage],attached_media:[],expert_results:[],next_before:null}}));return;}
  if(req.url==='/api/platform/ai/support-tickets/'+ticketId&&req.method==='PATCH'){res.writeHead(200,headers);res.end(JSON.stringify({success:true,result:{ticket:{...ticket,status:'in_progress'},messages:initialMessages,attached_media:[],expert_results:[],next_before:'older-cursor'}}));return;}
  if(pathname===baseConsult+'/replies'){if(holdConsultations){holdConsultations=false;consultationWaiter?.();await new Promise(resolve=>{releaseConsultations=resolve;});}res.writeHead(200,headers);res.end(JSON.stringify({success:true,result:{consultations,replies}}));return;}
  if(pathname===baseConsult+'/destinations'){res.writeHead(200,headers);res.end(JSON.stringify({success:true,result:{destinations:[{channel_id:'developer-channel',conversation_id:'120363411822115409@g.us',customer_name:'Developers',channel_name:'Support WhatsApp'}]}}));return;}
  if(pathname===baseConsult+'/preview'){res.writeHead(200,headers);res.end(JSON.stringify({success:true,result:{case_id:ticketId,messages:[earlierMessage,...initialMessages],has_earlier:false}}));return;}
  if(pathname===baseConsult&&req.method==='POST'){if(rejectPrepare){res.writeHead(409,headers);res.end(JSON.stringify({success:false,error:'Developer recipient is no longer available'}));return;}prepared.push(body);consultations=[{id:'synthetic-consultation',case_id:ticketId,request_id:body.request_id,source_channel:ticket.channel_id,source_conversation:ticket.conversation_id,destination_channel:body.destination_channel_id,destination_conversation:body.destination_conversation_id,recipient:'Developers',reason:body.summary,state:'ready',can_dispatch:true,can_manual_reply:true,created_at:'2026-11-01T12:00:00Z',expires_at:'2099-11-08T12:00:00Z'}];res.writeHead(200,headers);res.end(JSON.stringify({success:true,result:{id:'synthetic-consultation',state:'ready'}}));return;}
  if(pathname===baseConsult+'/synthetic-consultation/dispatch'){dispatched.push(body);consultations[0].state='pending';consultations[0].can_dispatch=false;res.writeHead(200,headers);res.end(JSON.stringify({success:true,result:{state:'pending'}}));return;}
  if(req.url.startsWith('/api/platform/support-cases/'+ticketId+'/public-shares')){res.writeHead(200,headers);res.end(JSON.stringify({success:true,result:[]}));return;}
  if(req.url.startsWith('/api/platform/crm/chat/attachment?')){mediaRequests++;res.writeHead(200,{'content-type':'image/png','cache-control':'no-store','content-disposition':'inline; filename="synthetic.png"'});res.end(Buffer.from([1,2,3]));return;}
  res.writeHead(404,headers);res.end(JSON.stringify({success:false,error:'synthetic route not found'}));
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;
  try{
    browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
    const base='http://127.0.0.1:'+server.address().port;
    async function openPage(width,height,url='/app?view=support'){
      const context=await browser.newContext({viewport:{width,height}}),page=await context.newPage();
      await page.addInitScript(({ticketId})=>{
        window.tenantProfileSnapshot={member:{tenant_id:'synthetic-company',telegram_id:11,role:'owner'}};window.tenantAppNavigate=view=>{window.__navigatedView=view;document.dispatchEvent(new CustomEvent('tenant-view',{detail:{view}}));};document.addEventListener('tenant-support-open',event=>window.__supportOpenDetail=event.detail);
        window.tenantOperator={hasRight:permission=>permission==='support'||permission==='dialogs'};
      },{ticketId});
      page.on('console',message=>console.log('browser console:',message.text()));page.on('pageerror',error=>console.log('browser pageerror:',error.message));page.on('requestfailed',request=>console.log('browser requestfailed:',request.url(),request.failure()?.errorText));
      await page.goto(base+url);
      await page.addScriptTag({path:path.join(root,'docs/tenant-operator-i18n.js')});
      // All calls use this local synthetic fixture server.
      await page.addScriptTag({path:path.join(root,'docs/tenant-support-consultations.js')});
      await page.addScriptTag({path:path.join(root,'docs/tenant-support-inbox.js')});
      try{await page.waitForSelector('[data-support-id]',{timeout:5000});}catch(error){console.log('page html:',(await page.locator('body').innerText()).slice(0,1000));throw error;}
      return {context,page};
    }
    // Use only the local synthetic fixture server.
    async function developerJourney(page,label){
      const beforeMedia=mediaRequests;await page.locator('#consultCreate').click();const form=page.locator('#caseDeveloperForm');await form.waitFor();
      assert.equal(await form.locator('#consultRecipient').count(),1,'one Developer receiver control');assert.equal(await form.locator('#consultRecipient').inputValue(),'0','one marked Developer is selected once');assert.equal(await form.locator('[data-consultation-confirm]').count(),0,'the send action has no extra confirmation step');
      await form.locator('#consultReason').fill('Printer cannot connect after the documented checks');await form.locator('#caseConsultFirst').selectOption(textMessage.id);await form.locator('#caseConsultLast').selectOption(message.id);await form.locator('input[value="'+audioMessage.id+'"]').check();await form.locator('input[value="'+videoMessage.id+'"]').check();assert.equal(await page.locator('.support-modal-card').evaluate(node=>node.scrollWidth<=node.clientWidth+1),true,'developer form fits the modal width');await page.screenshot({path:'/tmp/support-developer-form-'+label+'.png'});
      await form.locator('#caseConsultSend').evaluate(button=>{button.click();button.click();});await page.locator('#consultRows').getByText(/принял запрос в очередь/).waitFor();assert.equal(prepared.length,1);assert.equal(dispatched.length,1);assert.equal(prepared[0].request_id,dispatched[0].request_id);assert.deepEqual(prepared[0].message_ids,initialMessages.map(row=>row.id));assert.deepEqual(prepared[0].media_ids,[audioMessage.id,videoMessage.id]);assert.equal(mediaRequests,beforeMedia,'request sends selected references without downloading files');assert.equal(await page.locator('#consultCreate').isDisabled(),true,'pending delivery cannot be sent again');assert.equal(await page.locator('textarea[data-consult-answer]').count(),0,'no unexplained manual answer input');
      consultations[0].state='sent';replies=[{id:'late-developer-reply',consultation_id:'synthetic-consultation',author:'Developer',body:'Replace the printer cable, then retry the connection.',source_kind:'provider',draft_state:'not_requested',can_draft:true,happened_at:'2026-11-01T13:00:00Z',url:'/app?view=dialogs&open_dialog=1&provider=connect&lead_id=41&channel_id=channel-a&conversation_id=conversation-a'}];
      await page.locator('#supportConsultations .support-heading').getByRole('button',{name:'Обновить',exact:true}).click();await page.locator('[data-consultation-reply="late-developer-reply"]').getByText('Replace the printer cable, then retry the connection.',{exact:true}).waitFor();assert.equal(await page.locator('textarea[data-consult-answer]').count(),0);assert.equal(await page.locator('#consultRows').getByRole('button',{name:'Добавить ответ вручную',exact:true}).count(),1);assert.equal(mediaRequests,beforeMedia);assert.deepEqual(unexpectedPosts,[],'late reply never starts AI or sends to customer');await page.screenshot({path:'/tmp/support-developer-flow-'+label+'.png'});
      rejectPrepare=true;await page.locator('#consultCreate').click();const follow=page.locator('#caseDeveloperForm');await follow.waitFor();assert.equal(await follow.locator('#consultRecipient').isDisabled(),true,'follow-up preserves its Developer receiver');await follow.locator('#consultReason').fill('Does the cable need replacement?');await follow.locator('#caseConsultSend').click();await follow.getByText('Developer recipient is no longer available',{exact:true}).waitFor();assert.equal(await page.locator('#supportNotice').textContent(),'');assert.equal(await follow.evaluate(node=>document.getElementById('supportDetail').contains(node)),true,'request errors stay inside the open modal');await follow.getByRole('button',{name:'Закрыть',exact:true}).click();rejectPrepare=false;assert.equal(dispatched.length,1,'rejected follow-up does not dispatch');assert.deepEqual(unexpectedPosts,[]);
    }
    const desktop=await openPage(1280,900);
    await desktop.page.locator('[data-support-id]').click();
    await desktop.page.waitForSelector('#supportTicketTitle');
    assert.equal(await desktop.page.locator('#supportDetail').getAttribute('aria-modal'),'true');
    assert.equal(new URL(desktop.page.url()).searchParams.get('support_ticket_id'),ticketId);
    assert.equal(await desktop.page.locator('#supportRows').count(),1,'filtered queue stays mounted behind the modal');
    const bounds=await desktop.page.locator('#supportDetail').boundingBox();assert.ok(bounds.width>1000&&bounds.height>850,'desktop modal is roomy and centered');await desktop.page.screenshot({path:'/tmp/support-ticket-modal-desktop.png'});await desktop.page.keyboard.press('Shift+Tab');assert.equal(await desktop.page.evaluate(()=>document.getElementById('supportDetail').contains(document.activeElement)),true,'focus stays inside the dialog');
    assert.equal(mediaRequests,0,'ticket open does not fetch attachment bytes');
    assert.equal(await desktop.page.locator('article.support-message img,article.support-message video,article.support-message audio').count(),0,'media src is absent before an explicit click');
    await desktop.page.locator('#supportTake').click();await desktop.page.waitForFunction(()=>document.querySelector('#supportEditStatus')?.value==='in_progress');assert.equal(mediaRequests,0,'status action rerender does not fetch attachment bytes');await desktop.page.locator('#supportEarlier').click();await desktop.page.locator('[data-ticket-media-open="e0ffee00-0000-4000-8000-000000000002"]').click();
    await desktop.page.waitForSelector('.support-media-preview a');assert.equal(mediaRequests,1,'media attached after action rerender and pagination loads only on explicit click');
    await desktop.page.keyboard.press('Escape');await desktop.page.locator('#supportDetail').waitFor({state:'hidden'});
    await desktop.page.waitForFunction(()=>!new URL(location.href).searchParams.has('support_ticket_id'));
    assert.equal(await desktop.page.locator('[data-support-id]').evaluate(node=>node===document.activeElement),true,'closing restores the invoking control');
    const consultPending=holdNextConsultation();await desktop.page.locator('[data-support-id]').click();await desktop.page.waitForSelector('#supportTicketTitle');await consultPending;await desktop.page.locator('#supportDescription').fill('Unsaved edit during consultation load');releaseConsultations?.();await desktop.page.waitForSelector('#supportConsultations');assert.equal(await desktop.page.locator('#supportDescription').inputValue(),'Unsaved edit during consultation load','late consultation response preserves edits made while it was loading');await desktop.page.keyboard.press('Escape');await desktop.page.locator('#supportDetail').waitFor({state:'hidden'});await desktop.page.locator('[data-support-id]').click();await desktop.page.waitForSelector('#supportTicketTitle');assert.equal(await desktop.page.locator('#supportDescription').inputValue(),'Unsaved edit during consultation load','closing and reopening keeps the unsaved draft');
    await developerJourney(desktop.page,'desktop');await desktop.context.close();consultations=[];replies=[];prepared=[];dispatched=[];

    const mobile=await openPage(390,844,'/app?view=support&support_ticket_id='+ticketId);
    await mobile.page.waitForSelector('#supportTicketTitle');assert.equal(await mobile.page.locator('#supportDetail').getAttribute('aria-modal'),'true');
    const mobileBounds=await mobile.page.locator('#supportDetail').boundingBox();assert.equal(Math.round(mobileBounds.width),390);assert.equal(Math.round(mobileBounds.height),844);await mobile.page.screenshot({path:'/tmp/support-ticket-modal-mobile.png'});
    assert.equal(mediaRequests,1,'deep link open also avoids loading attachment bytes');
    await developerJourney(mobile.page,'mobile');await mobile.page.locator('#supportClose').click();await mobile.page.locator('#supportDetail').waitFor({state:'hidden'});await mobile.page.waitForFunction(()=>!new URL(location.href).searchParams.has('support_ticket_id'),'close button removes deep-link ticket state');await mobile.page.locator('[data-support-id]').click();await mobile.page.waitForSelector('#supportTicketTitle');await mobile.page.locator('#supportOpenChat').click();await mobile.page.waitForFunction(()=>document.getElementById('supportDetail').hidden&&window.__navigatedView==='dialogs'&&window.__supportOpenDetail?.lead_id===41,'opening chat closes the modal before navigating to the selected dialog');assert.equal(new URL(mobile.page.url()).searchParams.has('support_ticket_id'),false,'chat navigation clears ticket deep-link state');
    await mobile.context.close();
    console.log('PASS: desktop/390px ticket modal, one Developer request, pending/late reply, inline errors, no customer/AI sends, click-only media, deep links and drafts.');
  }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
