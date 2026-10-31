// Used only by test_support_group_local_e2e.py against its local synthetic server.
const assert=require('node:assert/strict');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
(async()=>{
 const {GROUP_E2E_ORIGIN:origin,GROUP_E2E_CASE:ticket,GROUP_E2E_CONSULT:consult,GROUP_E2E_IMAGE:image,GROUP_E2E_IMAGE2:image2}=process.env;
 assert.ok(origin&&ticket&&consult&&image&&image2);
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:390,height:844}});await page.goto(origin+'/app');
  await page.locator('[data-support-id="'+ticket+'"]').click();
  const panel=page.locator('[data-consult-id="'+consult+'"] details');await panel.locator('summary').click();
  await panel.locator('select').selectOption('0');await panel.locator('textarea').fill('50 → 49,9992');await panel.locator('input[value="'+image+'"]').check();
  await panel.getByRole('button',{name:'Подготовить'}).click();
  const initialSend=panel.getByRole('button',{name:'Отправить в выбранную группу'});
  try{await initialSend.waitFor({timeout:4000});}catch{
   throw new Error('Group preparation did not produce the send action. Notice: '+
    await page.locator('#supportNotice').innerText()+'; panel: '+await panel.innerText());
  }
  await initialSend.click();
  await panel.getByText('Отправка подтверждена').waitFor();
  const response=await page.request.post(origin+'/simulate/reply');assert.equal(response.status(),200);
  await panel.getByRole('button',{name:'Обновить'}).click();await panel.getByText(/We are investigating/).waitFor();
  await panel.locator('textarea').fill('We are checking.');await panel.locator('[data-group-draft]').click();
  await page.waitForFunction(()=>document.querySelector('[data-group-draft]')&&!document.querySelector('[data-group-draft]').disabled);
  assert.equal(await panel.locator('textarea').inputValue(),'We are checking.');
  assert.equal((await page.request.post(origin+'/simulate/client-attachment')).status(),200);
  await page.reload();await page.locator('#supportTicketTitle').waitFor();assert.equal(new URL(page.url()).searchParams.get('support_ticket_id'),ticket,'reload restores the ticket through its deep link without a second row click');
  const again=page.locator('[data-consult-id="'+consult+'"] details');await again.locator('summary').click();
  await again.getByRole('button',{name:'Передать уточнение'}).click();
  assert.equal(await again.locator('textarea').inputValue(),''); // never reuse an earlier remote-access code
  await again.locator('textarea').fill('Customer provided a new image.');await again.locator('input[value="'+image2+'"]').check();
  await again.getByRole('button',{name:'Подготовить'}).click();await again.getByRole('button',{name:'Отправить в выбранную группу'}).click();
  await again.getByText('Отправка подтверждена').waitFor();assert.equal((await page.request.post(origin+'/simulate/reply')).status(),200);
  console.log('PASS: real browser and local HTTP/store/signed webhook, synthetic provider.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
