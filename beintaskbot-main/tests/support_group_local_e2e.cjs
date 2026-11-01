// Local HTTP/store/signed-webhook integration only; providers and AI are synthetic.
const assert=require('node:assert/strict');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
(async()=>{
 const {GROUP_E2E_ORIGIN:origin,GROUP_E2E_CASE:ticket,GROUP_E2E_OLD_CONSULT:oldConsult,GROUP_E2E_IMAGE:image,GROUP_E2E_IMAGE2:image2}=process.env;
 assert.ok(origin&&ticket&&oldConsult&&image&&image2);
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:390,height:844}}),writes=[];
  page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname.startsWith('/api/'))writes.push(new URL(request.url()).pathname);});
  await page.goto(origin+'/app');await page.locator('[data-support-id="'+ticket+'"]').click();await page.locator('[data-consult-id="'+oldConsult+'"]').waitFor();
  async function sendImage(id,summary){
   await page.locator('#consultCreate').click();const form=page.locator('#caseDeveloperForm');await form.waitFor();assert.equal(await form.locator('#consultRecipient').count(),1);
   assert.equal(await form.locator('#consultReason').inputValue(),'','a follow-up never reuses an earlier remote access code');
   await form.locator('#consultReason').fill(summary);await form.locator('#caseConsultFirst').selectOption(id);await form.locator('#caseConsultLast').selectOption(id);await form.locator('input[value="'+id+'"]').check();
   await form.locator('#caseConsultSend').evaluate(button=>{button.click();button.click();});await page.locator('#consultRows').getByText(/принял запрос в очередь/).last().waitFor();assert.equal(await page.locator('#consultCreate').isDisabled(),true,'queue receipt does not claim delivery');
   return page.locator('[data-consult-id]').filter({has:page.getByText(summary,{exact:true})});
  }
  const first=await sendImage(image,'50 → 49,9992');
  const earlyReply=await page.request.post(origin+'/simulate/reply');assert.equal(earlyReply.status(),200);assert.equal((await earlyReply.json()).linked_replies,0,'unconfirmed provider receipt cannot bind the reply');
  await page.locator('#supportConsultations .support-heading').getByRole('button',{name:'Обновить',exact:true}).click();assert.equal(await page.locator('[data-consultation-reply]').count(),0);
  const receipt=await page.request.post(origin+'/simulate/receipt');assert.equal(receipt.status(),200);const confirmed=await receipt.json();assert.equal(confirmed.state,'sent');assert.equal(confirmed.reconciled,1,'signed provider receipt recovers the earlier signed reply once');
  await page.locator('#supportConsultations .support-heading').getByRole('button',{name:'Обновить',exact:true}).click();await first.getByText(/We are investigating/).waitFor();
  await first.getByRole('button',{name:'Подготовить AI-черновик клиенту',exact:true}).click();await first.getByText('We are checking.',{exact:true}).waitFor();
  assert.equal((await page.request.post(origin+'/simulate/client-attachment')).status(),200);
  await page.reload();await page.locator('#supportTicketTitle').waitFor();assert.equal(new URL(page.url()).searchParams.get('support_ticket_id'),ticket,'reload restores the exact case');
  await page.getByText('We are checking.',{exact:true}).waitFor();
  await page.locator('#caseMediaLoad').click();const newImage=page.locator('#supportExistingMedia input[value="'+image2+'"]');await newImage.waitFor();await newImage.check();await page.locator('[data-case-media-attach]').click();await page.locator('[data-message-id="'+image2+'"]').waitFor();
  const second=await sendImage(image2,'Customer provided a new image.');
  const secondEarly=await page.request.post(origin+'/simulate/reply');assert.equal(secondEarly.status(),200);assert.equal((await secondEarly.json()).linked_replies,1,'second pending request cannot bind before its own receipt');
  const secondReceipt=await page.request.post(origin+'/simulate/receipt');assert.equal(secondReceipt.status(),200);assert.equal((await secondReceipt.json()).state,'sent');
  await page.locator('#supportConsultations .support-heading').getByRole('button',{name:'Обновить',exact:true}).click();await second.getByText(/We are investigating/).waitFor();await page.getByText('We are checking.',{exact:true}).waitFor();
  assert.equal(writes.filter(path=>path.endsWith('/dispatch')).length,2,'one dispatch per deliberate group request');assert.equal(writes.filter(path=>path.endsWith('/draft')).length,1,'one explicit private AI draft');assert.equal(writes.filter(path=>path==='/api/platform/ai/dialog-consultations').length,2);
  assert.equal(writes.some(path=>/crm\/chat\/send|dialogs\/send|internal-consultations/.test(path)),false,'no automatic customer send or obsolete transport');
  console.log('PASS: Chromium/local HTTP/SQL: two bounded group queue sends, signed receipts/replies, one explicit unsent draft, fresh follow-up file.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
