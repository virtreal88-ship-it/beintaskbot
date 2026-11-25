const assert=require('node:assert/strict');
const {fixture,server,message,launch,deferred}=require('./visible_media_fixture.cjs');

const receiptId='00000000-0000-0000-0000-000000000901';
const externalId='connect:'+receiptId;
const accepted={success:true,provider_accepted:true,delivery_state:'sent',message_id:receiptId};

async function scenario(browser,recover){
 const srv=server();srv.status=503;
 const initial={...message('old-text','text'),body:'Existing chat text'};
 const f=await fixture(browser,srv,{width:1280,height:900,messages:[initial]});
 const submitted=deferred(),confirm=deferred();
 let posts=0,checks=0,reads=0;
 const commit=()=>f.state.messages.push({...message(externalId,'video'),direction:'outgoing',
   body:'Video caption',media_filename:'clip.mp4',delivery_state:'sent'});
 try{
  await f.page.route(/\/crm\/chat\/media-capability\?/,route=>route.fulfill({json:{success:true,enabled:true,video_max_bytes:100*1024*1024}}));
  await f.page.route(/\/dialogs\/history\?/,route=>{reads++;return route.fallback();});
  await f.page.route('**/api/platform/crm/chat/send-media',async route=>{
   assert.equal(route.request().method(),'POST');posts++;submitted.release();await confirm.promise;
   if(recover)return route.abort('failed');
   commit();return route.fulfill({json:accepted});
  });
  await f.page.route(/\/crm\/chat\/media-receipt\?/,route=>{
   assert.equal(route.request().method(),'GET');checks++;commit();return route.fulfill({json:accepted});
  });
  await f.open();
  await f.page.getByText('Existing chat text',{exact:true}).waitFor();
  await f.page.evaluate(()=>{window.initialHistory=document.querySelector('#chatRows');});
  await f.page.locator('#operatorMediaAdd').click();
  const file=f.page.waitForEvent('filechooser');
  await f.page.locator('.operator-media-menu button').nth(1).click();
  await (await file).setFiles({name:'clip.mp4',mimeType:'video/mp4',buffer:Buffer.from('synthetic video fixture')});
  await f.page.locator('.operator-customer-media input').fill('Video caption');
  const send=f.page.locator('#operatorComposerPrimary[data-action="media"]');
  await send.click();await submitted.promise;
  assert.match(await f.page.locator('.operator-customer-media [role=status]').textContent(),/Отправляем|göndərilir|Sending/);
  assert.equal(await f.card(externalId).count(),0);
  const previousReads=reads;
  confirm.release();
  if(recover){
   await f.page.waitForFunction(()=>/Проверить|yoxla/i.test(document.querySelector('#operatorComposerPrimary')?.title||''));
   assert.equal(await f.card(externalId).count(),0);
   await send.click();
  }
  await f.card(externalId).waitFor();
  assert.equal(await f.card(externalId).count(),1);
  assert.equal(await f.card(externalId).evaluate(card=>card.closest('article').dataset.direction),'outgoing');
  assert.equal(await f.page.evaluate(()=>window.initialHistory===document.querySelector('#chatRows')),true);
  assert(reads>previousReads);
  assert.equal(f.state.navigations,1);
  assert.equal(posts,1);
  assert.equal(checks,recover?1:0);
  assert.deepEqual(f.errors,[]);
 }finally{confirm.release();await f.context.close();}
}

(async()=>{
 const browser=await launch();
 try{
  await scenario(browser,false);
  await scenario(browser,true);
  console.log('PASS outgoing video appears in the open history on acceptance or receipt recovery, exactly once');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
