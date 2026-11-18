// Run the shipped bundle in isolated Chromium, never a live account/provider.
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {for(const width of [1280,390]){
  const page=await browser.newPage({viewport:{width,height:850}});
  const member={tenant_id:'synthetic-tenant',telegram_id:11,role:'owner'};
  const me={success:true,member,integrations:[{provider:'connect',status:'connected',management_actions:{view:true}}]};
  let mutations=0,reads=0,paired=false;
  await page.route('https://app.invalid/**',async route=>{
   const url=new URL(route.request().url());let value={success:true};
   if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:'<div id="chakra-integrations-root"></div>'});
   if(url.pathname==='/api/platform/me')value=me;
   if(url.pathname==='/api/platform/integrations/whatsapp'){
    assert.equal(url.searchParams.get('expected_tenant_id'),member.tenant_id);
    assert.equal(url.searchParams.get('expected_user_id'),'11');reads++;
    if(route.request().method()==='POST'){
      const data=route.request().postDataJSON();mutations++;
      if(data.action==='begin')value={success:true,result:{channel_id:'source-new',phone:data.phone,state:'pairing',activation_pending:true,qr:'YWJj'}};
      else {assert.equal(data.action,'activate');value={success:true,result:{channel_id:'source-new',phone:'994100000777',state:'ready',activation_pending:false}};}
    } else if(url.searchParams.has('source'))value={success:true,result:{channel_id:'source-new',phone:'994100000777',state:paired?'ready':'pairing',activation_pending:true,qr:paired?null:'YWJj'}};
    else value={success:true,result:{channels:[{id:'source-101',name:'Texniki Dəstək',phone:'994102135101',state:'ready',messages_ready:true,calls_ready:true}]}};
   }
   return route.fulfill({json:value});
  });
  await page.goto('https://app.invalid/');
  await page.evaluate(member=>{window.tenantI18n={language:'ru'};window.tenantProfileSnapshot={member};window.tenantViewDemand={watch:entry=>{if(entry.view==='integrations')entry.activate();}};},member);
  await page.addScriptTag({path:path.join(__dirname,'../docs/tenant-chakra-ui.js'),type:'module'});
  const card=page.locator('.chakra-card__root').filter({hasText:'Один QR для каждого номера'});
  await card.getByRole('button').click();
  await page.getByText('Переписка: подключена').waitFor();
  await page.getByRole('button',{name:'Подключить номер',exact:true}).click();
  assert.equal(mutations,0,'opening a form must not provision a device');
  await page.getByRole('textbox',{name:'Номер WhatsApp',exact:true}).fill('994100000777');
  await page.getByRole('textbox',{name:'Название источника'}).fill('Synthetic');
  await page.getByRole('button',{name:'Сгенерировать QR'}).click();
  await page.getByAltText('QR подключения WhatsApp').waitFor();assert.equal(mutations,1);
  paired=true;await page.getByText('Переписка и звонки подключены.',{exact:true}).waitFor({timeout:12000});
  assert.equal(mutations,2,'one begin and one activation only');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no mobile horizontal overflow');
  await page.evaluate(()=>document.dispatchEvent(new Event('tenant-auth-invalid')));
  await page.getByRole('alert').filter({hasText:'Доступ изменился'}).waitFor();
  assert.equal(await page.getByAltText('QR подключения WhatsApp').count(),0);
  const count=reads;await page.waitForTimeout(1500);assert.equal(reads,count,'no polling after access is lost');
  await page.close();
 }
 console.log('PASS shipped bundle: desktop/mobile, explicit QR, one activation, access cleanup');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
