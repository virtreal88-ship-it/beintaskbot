// Run the shipped bundle in isolated Chromium, never a live account/provider.
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined});
 const artifacts=path.join(__dirname,'artifacts/whatsapp-integration');fs.mkdirSync(artifacts,{recursive:true});
 try {for(const width of [1280,390]){
  const page=await browser.newPage({viewport:{width,height:850}});
  const member={tenant_id:'synthetic-tenant',telegram_id:11,role:'owner'};
  const me={success:true,member,integrations:[{provider:'connect',status:'connected',management_actions:{view:true}}]};
  let mutations=0,reads=0,inventoryReads=0,paired=false,denied=false;
  let markInventoryRefresh;
  const refreshedInventory=new Promise(resolve=>{markInventoryRefresh=resolve;});
  await page.route('https://app.invalid/**',async route=>{
   const url=new URL(route.request().url());let value={success:true};
   if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:'<div id="chakra-integrations-root"></div>'});
   if(url.pathname==='/api/platform/me')value=me;
   if(url.pathname==='/api/platform/integrations/connect')value={success:true,channels:[{id:'connect-source-101',name:'Texniki Dəstək',phone:'994102135101',status:'connected',active:true}]};
   if(url.pathname==='/api/platform/browser-calls')value={success:true,result:{sources:[]}};
   if(url.pathname==='/api/platform/integrations/whatsapp'){
    if(denied)return route.fulfill({status:403,json:{success:false,error:'Access revoked'}});
    assert.equal(url.searchParams.get('expected_tenant_id'),member.tenant_id);
    assert.equal(url.searchParams.get('expected_user_id'),'11');reads++;
    if(route.request().method()==='POST'){
      const data=route.request().postDataJSON();mutations++;
      if(data.action==='begin')value={success:true,result:{channel_id:'source-new',phone:data.phone,state:'pairing',activation_pending:true,qr:'YWJj'}};
      else {assert.equal(data.action,'activate');value={success:true,result:{channel_id:'source-new',phone:'994100000777',state:'ready',activation_pending:false}};}
    } else if(url.searchParams.has('source'))value={success:true,result:{channel_id:'source-new',phone:'994100000777',state:paired?'ready':'pairing',activation_pending:true,qr:paired?null:'YWJj'}};
    else {inventoryReads++;if(inventoryReads>1)markInventoryRefresh();value={success:true,result:{channels:[{id:'source-101',name:'Texniki Dəstək',phone:'994102135101',state:'ready',messages_ready:true,calls_ready:true}]}};}
   }
   return route.fulfill({json:value});
  });
  await page.goto('https://app.invalid/');
  await page.evaluate(member=>{window.tenantI18n={language:'ru'};window.tenantProfileSnapshot={member};window.testWatchers=[];window.tenantViewDemand={watch:entry=>{testWatchers.push(entry);if(entry.view==='integrations')entry.activate();}};},member);
  await page.addStyleTag({path:path.join(__dirname,'../ui/src/app-theme.css')});
  await page.addScriptTag({path:path.join(__dirname,'../docs/tenant-chakra-ui.js'),type:'module'});
  const card=page.locator('.chakra-card__root').filter({hasText:'Один QR для каждого номера'});
  await card.getByText('WhatsApp',{exact:true}).waitFor();
  assert.equal(await card.locator('text=WhatsApp Connect').count(),0,'the native WhatsApp card has its own exact title');
  await page.screenshot({path:path.join(artifacts,`market-${width}.png`),fullPage:true});
  await card.getByRole('button').click();
  await page.getByText('Переписка подключена').waitFor();
  assert.equal(await page.locator('#integrationMarketplaceDialog [data-whatsapp-number-list]').count(),1);
  assert.equal(await page.locator('#integrationMarketplaceDialog').getByRole('heading',{name:'WhatsApp',exact:true}).count(),1,'dialog uses the same exact WhatsApp title');
  assert.equal(await page.locator('#integrationMarketplaceDialog .connect-call-status').getAttribute('aria-label'),'Звонки подключены','call availability retains the shared accessible icon');
  const nativeStyles=await page.locator('#integrationMarketplaceDialog .connect-numbers-table').evaluate(table=>({font:getComputedStyle(table).fontSize,headerPadding:getComputedStyle(table.tHead.rows[0].cells[0]).padding,cellPadding:getComputedStyle(table.tBodies[0].rows[0].cells[0]).padding,minWidth:getComputedStyle(table).minWidth}));
  assert.equal(await page.locator('#integrationMarketplaceDialog .connect-numbers-table th').count(),5,'number inventory keeps the shared Connect row composition');
  await page.waitForTimeout(350);
  await page.locator('#integrationMarketplaceDialog').screenshot({path:path.join(artifacts,`whatsapp-${width}.png`)});
  const connectPage=await browser.newPage({viewport:{width,height:850}});
  await connectPage.route('https://app.invalid/**',async route=>{
   const url=new URL(route.request().url());let value={success:true};
   if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:'<div id="chakra-integrations-root"></div>'});
   if(url.pathname==='/api/platform/me')value=me;
   if(url.pathname==='/api/platform/integrations/connect')value={success:true,channels:[{id:'connect-source-101',name:'Texniki Dəstək',phone:'994102135101',status:'connected',active:true}]};
   if(url.pathname==='/api/platform/browser-calls')value={success:true,result:{sources:[]}};
   return route.fulfill({json:value});
  });
  await connectPage.goto('https://app.invalid/');
  await connectPage.evaluate(me=>{window.tenantI18n={language:'ru'};window.tenantProfileSnapshot=me;window.testWatchers=[];window.tenantViewDemand={watch:entry=>{testWatchers.push(entry);if(entry.view==='integrations')entry.activate();}};},me);
  await connectPage.addStyleTag({path:path.join(__dirname,'../ui/src/app-theme.css')});
  await connectPage.addScriptTag({path:path.join(__dirname,'../docs/tenant-call-sources.js')});
  await connectPage.addScriptTag({path:path.join(__dirname,'../docs/tenant-shared-integration-settings.js')});
  await connectPage.addScriptTag({path:path.join(__dirname,'../docs/tenant-chakra-ui.js'),type:'module'});
  const connectCard=connectPage.locator('.chakra-card__root').filter({hasText:'WhatsApp Connect'});
  await connectCard.getByRole('button').click();
  await connectPage.evaluate(()=>window.testWatchers.find(entry=>entry.provider==='connect').activate(window.tenantProfileSnapshot));
  await connectPage.locator('#integrationSettingsHost .connect-numbers-table').waitFor();
  const connectStyles=await connectPage.locator('#integrationSettingsHost .connect-numbers-table').evaluate(table=>({font:getComputedStyle(table).fontSize,headerPadding:getComputedStyle(table.tHead.rows[0].cells[0]).padding,cellPadding:getComputedStyle(table.tBodies[0].rows[0].cells[0]).padding,minWidth:getComputedStyle(table).minWidth}));
  assert.deepEqual(nativeStyles,connectStyles,'WhatsApp and Connect share table typography, spacing, and mobile sizing');
  await connectPage.waitForTimeout(350);
  await connectPage.locator('#integrationMarketplaceDialog').screenshot({path:path.join(artifacts,`connect-${width}.png`)});
  await connectPage.close();
  await page.getByRole('button',{name:'Подключить номер',exact:true}).click();
  assert.equal(mutations,0,'opening a form must not provision a device');
  await page.getByRole('textbox',{name:'Номер WhatsApp',exact:true}).fill('994100000777');
  await page.getByRole('textbox',{name:'Название источника'}).fill('Synthetic');
  await page.getByRole('button',{name:'Сгенерировать QR'}).click();
  await page.getByAltText('QR подключения WhatsApp').waitFor();assert.equal(mutations,1);
  paired=true;await page.getByText('Переписка и звонки подключены.',{exact:true}).waitFor({timeout:12000});
  assert.equal(mutations,2,'one begin and one activation only');
  await refreshedInventory;
  assert.equal(inventoryReads,2,'activation refreshes the normal number inventory');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no mobile horizontal overflow');
  if(width===390){denied=true;await page.getByRole('button',{name:'К списку номеров'}).click();}
  else await page.evaluate(()=>document.dispatchEvent(new Event('tenant-auth-invalid')));
  await page.getByRole('alert').filter({hasText:'Доступ изменился'}).waitFor();
  assert.equal(await page.getByAltText('QR подключения WhatsApp').count(),0);
  const count=reads;await page.waitForTimeout(1500);assert.equal(reads,count,'no polling after access is lost');
  await page.close();
 }
 console.log('PASS shipped bundle: desktop/mobile, explicit QR, one activation, access cleanup');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
