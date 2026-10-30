// Real Chromium, synthetic HTTP/provider; never contacts production.
const assert=require('node:assert/strict');
const path=require('node:path');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const base=path.join(__dirname,'..');
const ticketId='ad00d138-a2f4-491d-9b25-1009bfbca928',consultId='dfb2ba61-c045-4ee7-a9fc-fd9c5204769f',messageId='aac7dfc1-aefe-487b-9379-f6a5219928ca';
const ticket={id:ticketId,contact_name:'Synthetic client',channel_id:'channel-1',conversation_id:'client',lead_id:41,description:'50 → 49,9992',status:'waiting',source:'manual',created_at:'2026-10-16T00:00:00Z'};
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:390,height:844}}),calls=[];let delivery=null;
  await page.route('https://crm.test/**',async route=>{
   const req=route.request(),url=new URL(req.url()),method=req.method(),body=req.postDataJSON()||{};calls.push({path:url.pathname,method,body});
   let result={},status=200;
   if(url.pathname==='/app')return route.fulfill({status:200,contentType:'text/html',body:'<section id="view-support"><div id="operatorSupport"></div></section><div id="modalContent"></div>'});
   if(url.pathname==='/api/platform/me')return route.fulfill({status:200,json:{member:{tenant_id:'company-a',telegram_id:11,role:'owner'}}});
   if(url.pathname==='/api/platform/ai/support-tickets')result={tickets:[ticket],members:[{telegram_id:11,display_name:'Owner'}],next_offset:null};
   else if(url.pathname==='/api/platform/ai/support-tickets/'+ticketId)result={ticket,messages:[{id:messageId,message_type:'image',direction:'incoming',happened_at:'2026-10-16T00:00:00Z'}],expert_results:[],next_before:null};
   else if(url.pathname==='/api/platform/ai/internal-consultations/groups')result={groups:[{channel_id:'channel-1',conversation_id:'120363411822115409@g.us',contact_name:'Synthetic group'}]};
   else if(url.pathname==='/api/platform/ai/internal-consultations')result={consultations:[{id:consultId,reason:'50 → 49,9992',status:'waiting_specialist',recipient_name:'Owner',created_at:'2026-10-16T00:00:00Z'}]};
   else if(url.pathname==='/api/platform/ai/internal-consultations/'+consultId+'/group'){
    if(method==='POST'&&body.action==='prepare')delivery={state:'ready',request_id:body.request_id,destination:'Synthetic group',prepared_text:body.summary,replies:[],draft_revision:0};
    if(method==='POST'&&body.action==='send'){delivery.state='sent';delivery.replies=[{message_id:'reply-1',body:'We are investigating'}];}
    if(method==='POST'&&body.action==='draft'){delivery.customer_draft=body.draft;delivery.draft_reply_id=body.message_id;}
    if(!delivery)status=404;else result=delivery;
   }
   return route.fulfill({status,json:status===200?{success:true,result}:{success:false,error:'Not prepared'}});
  });
  await page.goto('https://crm.test/app');
  await page.evaluate(()=>{window.tenantOperator={hasRight:()=>true};});
  await page.addScriptTag({path:path.join(base,'docs/tenant-operator-i18n.js')});
  await page.addScriptTag({path:path.join(base,'docs/tenant-support-inbox.js')});
  await page.locator('[data-support-id]').click();
  const panel=page.locator('[data-consult-id] details');await panel.locator('summary').click();
  await panel.locator('select').selectOption('0');await panel.locator('textarea').fill('50 → 49,9992');await panel.locator('input[type=checkbox]').check();
  await panel.getByRole('button',{name:'Подготовить'}).click();
  await panel.getByText('Synthetic group').waitFor();assert.equal(calls.filter(c=>c.body.action==='send').length,0);
  assert.deepEqual(calls.find(c=>c.body.action==='prepare').body.attachment_ids,[messageId]);
  await panel.getByRole('button',{name:'Отправить в выбранную группу'}).click();
  await panel.getByText('We are investigating').waitFor();await panel.locator('textarea').fill('We are checking.');
  await panel.locator('[data-group-draft]').click();await page.waitForFunction(()=>document.querySelector('[data-consult-id] details textarea')?.value==='We are checking.');
  assert.equal(calls.filter(c=>c.body.action==='send').length,1);assert.equal(delivery.customer_draft,'We are checking.');
  assert.ok(!calls.some(c=>c.path.includes('/dialogs/send')));
  console.log('PASS: Chromium group consultation prepare, explicit send and unsent draft (synthetic HTTP).');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
