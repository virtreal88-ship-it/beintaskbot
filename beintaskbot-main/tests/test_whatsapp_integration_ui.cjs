// Run the shipped bundle in isolated Chromium, never a live account/provider.
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||undefined});
 try {for(const width of [1280,390]){
  const page=await browser.newPage({viewport:{width,height:850}});
  const member={tenant_id:'synthetic-tenant',telegram_id:11,role:'owner',active:true};
  const me={success:true,member,integrations:[{provider:'connect',status:'connected',management_actions:{view:true}}]};
  let mutations=0,reads=0,paired=false,denied=false,stopped=false;
  let includeDuplicates=false,removed=false,refuseRemove=true;
  await page.route('https://app.invalid/**',async route=>{
   const url=new URL(route.request().url());let value={success:true};
   if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:'<section id="view-integrations" class="active"><div id="chakra-integrations-root"></div></section>'});
   if(url.pathname==='/api/platform/me')value=me;
   if(url.pathname==='/api/platform/integrations/whatsapp'){
    if(denied)return route.fulfill({status:403,json:{success:false,error:'Access revoked'}});
    assert.equal(url.searchParams.get('expected_tenant_id'),member.tenant_id);
    assert.equal(url.searchParams.get('expected_user_id'),'11');reads++;
    if(route.request().method()==='POST'){
      const data=route.request().postDataJSON();mutations++;
      if(data.action==='begin')value={success:true,result:{channel_id:'source-new',phone:data.phone,state:'pairing',activation_pending:true,qr:'YWJj'}};
      else if(data.action==='disconnect'||data.action==='disconnect_all'){assert.deepEqual(data.sources,['source-101']);stopped=true;value={success:true,result:{stopped:data.sources}};}
      else if(data.action==='resume'){assert.equal(data.source,'source-101');stopped=false;value={success:true,result:{channel_id:'source-101',phone:'994102135101',state:'pairing',activation_pending:true}};}
      else if(data.action==='continue'){
        assert.deepEqual(data,{action:'continue',source:'source-original'});
        value={success:true,result:{channel_id:data.source,phone:'994102135103',state:'pairing',activation_pending:true,qr:'YWJj'}};
      }
      else if(data.action==='remove'){
        assert.deepEqual(data,{action:'remove',source:'source-duplicate'});
        if(refuseRemove)return route.fulfill({status:503,json:{success:false,error:'Остановка пока не подтверждена.'}});
        removed=true;value={success:true,result:{removed:data.source}};
      }
      else {assert.equal(data.action,'activate');value={success:true,result:{channel_id:'source-new',phone:'994100000777',state:'ready',activation_pending:false}};}
    } else if(url.searchParams.has('source')){
      const source=url.searchParams.get('source'),ready=source==='source-new'&&paired;
      value={success:true,result:{channel_id:source,phone:source==='source-original'?'994102135103':'994100000777',
        state:ready?'ready':'pairing',activation_pending:true,qr:ready?null:'YWJj'}};
    }
    else {
      const channels=[{id:'source-101',name:'Texniki Dəstək',phone:'994102135101',state:stopped?'stopped':'ready',paused:stopped,messages_ready:!stopped,calls_ready:!stopped,can_remove:stopped}];
      if(includeDuplicates){
        const pending={name:'+994102135103',phone:'994102135103',state:'pairing',activation_pending:true,can_remove:true};
        channels.push({...pending,id:'source-original'});
        if(!removed)channels.push({...pending,id:'source-duplicate',state:'unavailable',duplicate_pending:true});
      }
      value={success:true,result:{channels}};
    }
   }
   return route.fulfill({json:value});
  });
  await page.goto('https://app.invalid/');
  await page.evaluate(member=>{window.tenantI18n={language:'ru'};window.tenantProfileSnapshot={member};window.tenantViewDemand={watch:entry=>{if(entry.view==='integrations')entry.activate();},integrationCard:()=>null,rememberIntegrationCard(){},clearIntegrationSecrets(){}};},member);
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
  await page.getByRole('button',{name:'К списку номеров'}).click();
  await page.getByRole('button',{name:'Отключить номер',exact:true}).click();
  assert.equal(mutations,2,'opening confirmation is not a disconnect');
  await page.getByRole('button',{name:'Отмена',exact:true}).click();
  assert.equal(mutations,2,'cancel leaves source unchanged');
  await page.getByRole('button',{name:'Отключить интеграцию',exact:true}).click();
  await page.getByRole('button',{name:'Отключить',exact:true}).click();
  await page.getByText('Номер отключён. История сохранена.',{exact:true}).waitFor();
  assert.equal(mutations,3);
  await page.getByRole('button',{name:'Подключить снова',exact:true}).click();
  await page.getByText('Ожидаем подключение WhatsApp. Проверка обновляется автоматически.',{exact:true}).waitFor();
  assert.equal(mutations,4,'resume existing source, no new begin');
  includeDuplicates=true;
  await page.getByRole('button',{name:'К списку номеров'}).click();
  const original=page.locator('[data-whatsapp-source="source-original"]');
  const duplicate=page.locator('[data-whatsapp-source="source-duplicate"]');
  const active=page.locator('[data-whatsapp-source="source-101"]');
  await duplicate.getByText('Повторная запись.',{exact:false}).waitFor();
  assert.equal(await duplicate.getByRole('button',{name:'Продолжить подключение'}).count(),0);
  assert.equal(await active.getByRole('button',{name:'Удалить',exact:true}).count(),0);
  await duplicate.getByRole('button',{name:'Удалить',exact:true}).click();
  const confirmRemove=page.getByRole('alertdialog',{name:'Подтверждение удаления'});
  await confirmRemove.getByText('Удалить номер +994102135103 из списка?').waitFor();
  await confirmRemove.getByRole('button',{name:'Отмена',exact:true}).click();
  assert.equal(mutations,4,'cancel leaves the duplicate visible');
  await duplicate.getByRole('button',{name:'Удалить',exact:true}).click();
  await confirmRemove.getByRole('button',{name:'Удалить',exact:true}).click();
  await page.getByRole('alert').filter({hasText:'Остановка пока не подтверждена.'}).waitFor();
  assert(await duplicate.isVisible());
  refuseRemove=false;
  await confirmRemove.getByRole('button',{name:'Удалить',exact:true}).click();
  await duplicate.waitFor({state:'detached'});
  await page.getByRole('button',{name:'Обновить',exact:true}).click();
  assert.equal(await duplicate.count(),0,'removed channel stays hidden on reload');
  assert(await original.isVisible());
  assert(await active.isVisible());
  await original.getByRole('button',{name:'Продолжить подключение',exact:true}).click();
  await page.getByAltText('QR подключения WhatsApp').waitFor();
  assert.equal(mutations,7,'the original was continued by source ID');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no mobile horizontal overflow');
  if(width===390){denied=true;await page.getByRole('button',{name:'К списку номеров'}).click();}
  else await page.evaluate(()=>document.dispatchEvent(new Event('tenant-auth-invalid')));
  await page.getByRole('alert').filter({hasText:'Доступ изменился'}).waitFor();
  assert.equal(await page.getByAltText('QR подключения WhatsApp').count(),0);
  const count=reads;await page.waitForTimeout(1500);assert.equal(reads,count,'no polling after access is lost');
  await page.close();
 }
 console.log('PASS shipped bundle: desktop/mobile, explicit QR, one activation, scoped removal and continuation, access cleanup');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
