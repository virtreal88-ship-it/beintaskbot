// Real Chromium, synthetic historical CASE records; never contacts production.
const assert=require('node:assert/strict');
const path=require('node:path');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const base=path.join(__dirname,'..');
const ticketId='ad00d138-a2f4-491d-9b25-1009bfbca928',consultId='dfb2ba61-c045-4ee7-a9fc-fd9c5204769f',messageId='aac7dfc1-aefe-487b-9379-f6a5219928ca';
const ticket={id:ticketId,contact_name:'Synthetic client',channel_id:'channel-1',conversation_id:'client',lead_id:41,description:'50 → 49,9992',status:'waiting',source:'manual',created_at:'2026-10-16T00:00:00Z'};
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:390,height:844}}),calls=[];
  const consultations=[{id:consultId,case_id:ticketId,reason:'50 → 49,9992',state:'sent',recipient:'Synthetic group',destination_channel:'channel-1',destination_conversation:'developers@g.us',legacy_delivery:true,needs_public_snapshot:true,can_dispatch:false,can_manual_reply:true,response:'Historical phone answer',created_at:'2026-10-16T00:00:00Z'},
   {id:'64862110-f942-4d28-96fa-1e810a7ce643',case_id:ticketId,reason:'Unsent old request',state:'ready',legacy_delivery:true,needs_public_snapshot:true,can_dispatch:false,can_manual_reply:false,created_at:'2026-10-15T00:00:00Z'}];
  const replies=[{id:'historical-provider-reply',consultation_id:consultId,body:'We are investigating',author:'Original developer',source_kind:'provider',happened_at:'2026-10-16T01:00:00Z',draft_state:'completed',historical_draft:'Unsent customer draft retained',can_draft:false,handled_at:'2026-10-16T02:00:00Z'}];
  await page.route('https://crm.test/**',async route=>{
   const req=route.request(),url=new URL(req.url()),method=req.method(),body=req.postDataJSON()||{};calls.push({path:url.pathname,method,body});
   let result={};
   if(url.pathname==='/app')return route.fulfill({status:200,contentType:'text/html',body:'<section id="view-support"><div id="operatorSupport"></div></section><div id="modalContent"></div>'});
   if(url.pathname==='/api/platform/me')return route.fulfill({status:200,json:{member:{tenant_id:'company-a',telegram_id:11,role:'owner'}}});
   if(url.pathname==='/api/platform/ai/support-tickets')result={tickets:[ticket],members:[{telegram_id:11,display_name:'Owner'}],next_offset:null};
   else if(url.pathname==='/api/platform/ai/support-tickets/'+ticketId)result={ticket,messages:[{id:messageId,message_type:'image',direction:'incoming',happened_at:'2026-10-16T00:00:00Z'}],expert_results:[],next_before:null};
   else if(url.pathname==='/api/platform/ai/dialog-consultations/replies')result={consultations,replies};
   else if(url.pathname==='/api/platform/ai/dialog-consultations/'+consultId+'/manual-reply'&&method==='POST'){
    replies.push({id:'recorded-phone-reply',consultation_id:consultId,body:body.response,author:'Owner',source_kind:'manual',draft_state:'not_requested',can_draft:false,happened_at:'2026-11-01T14:00:00Z'});
    result={id:'recorded-phone-reply'};
   }else return route.fulfill({status:400,json:{success:false,error:'Unexpected synthetic request: '+url.pathname}});
   return route.fulfill({status:200,json:{success:true,result}});
  });
  await page.goto('https://crm.test/app');
  await page.evaluate(()=>{window.tenantOperator={hasRight:()=>true};});
  await page.addScriptTag({path:path.join(base,'docs/tenant-operator-i18n.js')});
  await page.addScriptTag({path:path.join(base,'docs/tenant-support-consultations.js')});
  await page.addScriptTag({path:path.join(base,'docs/tenant-support-inbox.js')});
  await page.locator('[data-support-id]').click();
  const row=page.locator('[data-consult-id="'+consultId+'"]');
  await row.getByText('We are investigating',{exact:true}).waitFor();
  await row.getByText('Historical phone answer',{exact:true}).waitFor();
  await row.getByText('Unsent customer draft retained',{exact:true}).waitFor();
  assert.match(await row.innerText(),/Original developer/);
  assert.equal(await page.locator('textarea[data-consult-answer]').count(),0,'historical answers do not expose an unexplained answer field');
  assert.equal(await page.getByRole('button',{name:'Отправить подготовленный запрос',exact:true}).count(),0,'an old CRM-only ready request cannot replay its old body');
  assert.equal(await page.locator('#consultCreate').isEnabled(),true,'old CRM-only preparation does not block a new explicit bounded request');
  assert.equal(calls.filter(call=>call.method==='POST').length,0,'opening history never dispatches or starts AI');
  await row.getByRole('button',{name:'Добавить ответ вручную',exact:true}).click();
  await row.locator('textarea[data-consult-answer]').fill('Developer called: replace the cable');
  await row.getByRole('button',{name:'Сохранить ответ',exact:true}).click();
  await page.getByText('Developer called: replace the cable',{exact:true}).waitFor();
  assert.match(await page.locator('[data-consultation-reply="recorded-phone-reply"]').innerText(),/Ответ записан сотрудником/);
  assert.equal(calls.filter(call=>call.method==='POST').length,1,'the only write is the explicit manual record');
  assert.ok(calls.filter(call=>call.method==='POST').every(call=>call.path.endsWith('/manual-reply')));
  assert.equal(calls.some(call=>/\/group$|\/dispatch$|\/draft$|\/dialogs\/send/.test(call.path)),false,'history and manual records never resend group/customer messages or start AI');
  console.log('PASS: Chromium preserves legacy CASE/group answers and private drafts, blocks old-body replay, and records an explicit manual answer without sends.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
