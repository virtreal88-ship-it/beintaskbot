/* Actual app renderer, synthetic authorized route bindings, real Chromium. */
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const {docs,assets,appHtml,installNotificationCaseFixture}=require('./notification_case_targets_fixture.cjs');
function installAliasFixture(mode){
 const f=window.notificationCaseFixture,base=window.fetch,keys=['provider','lead_id','channel_id','conversation_id'];
 const original=Object.fromEntries(keys.map(key=>[key,f.route[key]]));
 const canonical={...original,lead_id:8101,conversation_id:'synthetic:canonical@s.whatsapp.net'};
 f.historyCalls=[];f.historyMode=mode;
 window.fetch=async(input,options={})=>{
  const url=new URL(String(input),location);
  if(!url.pathname.endsWith('/dialogs/history'))return base(input,options);
  f.historyCalls.push(Object.fromEntries(url.searchParams));
  const earlier=url.searchParams.has('before'),mode=f.historyMode;
  const dialog={...f.route,...canonical};
  const scope={tenant_id:f.tenant,requested_route:{...original},canonical_route:{...canonical}};
  if(mode==='exact'||mode==='exact-no-proof'||mode==='exact-wrong-tenant'){Object.assign(dialog,original);scope.canonical_route={...original};}
  if(mode==='wrong-tenant'||mode==='exact-wrong-tenant')scope.tenant_id='foreign-tenant';
  if(mode.startsWith('wrong-request-'))scope.requested_route[mode.slice('wrong-request-'.length)]='unrelated';
  if(mode.startsWith('wrong-canonical-'))scope.canonical_route[mode.slice('wrong-canonical-'.length)]='unrelated';
  if(mode==='changed-channel'||mode==='changed-provider'){const key=mode==='changed-channel'?'channel_id':'provider';dialog[key]='unrelated';scope.canonical_route[key]='unrelated';}
  if(mode==='changed-pagination'&&earlier){dialog.lead_id=8999;dialog.conversation_id='changed-after-first-page';Object.assign(scope.canonical_route,{lead_id:8999,conversation_id:dialog.conversation_id});}
  const message={id:earlier?'older-alias-message':'alias-message',external_id:earlier?'connect:older-alias':'connect:alias',provider_message_id:'alias',direction:'incoming',author_type:'customer',body:earlier?'Earlier merged dialogue':'Merged dialogue text',message_type:'text',happened_at:earlier?'2026-11-01T10:00:00Z':'2026-11-02T10:00:00Z',source_lead_id:earlier?original.lead_id:canonical.lead_id,source_channel_id:f.channel,source_conversation_id:earlier?original.conversation_id:canonical.conversation_id};
  return new Response(JSON.stringify({success:true,result:{dialog,...(['no-proof','exact-no-proof'].includes(mode)?{}:{history_scope:scope}),messages:[message],next_before:earlier?null:'alias-cursor'}}),{headers:{'Content-Type':'application/json'}});
 };
}
(async()=>{
 const server=http.createServer((req,res)=>{const u=new URL(req.url,'http://test');if(u.pathname.startsWith('/assets/')){const name=path.basename(u.pathname);if(!assets.includes(name)){res.writeHead(404);res.end();return;}res.setHeader('content-type','text/javascript');res.end(fs.readFileSync(path.join(docs,name)));return;}res.setHeader('content-type','text/html');res.end(appHtml());});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||process.env.CHROMIUM_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':fs.existsSync('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe')?'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe':undefined)});
 async function mount(mode){
  const context=await browser.newContext({viewport:{width:390,height:900}}),page=await context.newPage(),errors=[];
  await context.addInitScript(installNotificationCaseFixture);await context.addInitScript(installAliasFixture,mode);page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
  await page.goto(origin+'/app?view=support&lang=ru');await page.locator('[data-support-id]').first().click();
  await page.waitForFunction(()=>notificationCaseFixture.historyCalls.length===1&&!document.getElementById('supportDialogueRefresh').disabled);
  return {context,page,errors};
 }
 try{
  for(const mode of ['valid','exact','exact-no-proof']){
   const {context,page,errors}=await mount(mode);try{
    assert.equal(await page.locator('#supportDialogueHistory [data-message-id=alias-message]').count(),1,mode);
    assert.equal(await page.locator('#supportHistory [data-message-id]').count(),0,'display-only history never enters case material scope');
    await page.locator('#supportDialogueEarlier').click();await page.locator('#supportDialogueHistory [data-message-id=older-alias-message]').waitFor();
    assert.equal(await page.locator('#supportDialogueHistory [data-message-id]').count(),2);
    const calls=await page.evaluate(()=>notificationCaseFixture.historyCalls);
    assert.equal(calls[1].before,'alias-cursor');assert.equal(calls[1].lead_id,'41');assert.equal(calls[1].conversation_id,calls[0].conversation_id);
    await page.locator('#supportDialogueRefresh').click();await page.waitForFunction(()=>notificationCaseFixture.historyCalls.length===3&&!document.getElementById('supportDialogueRefresh').disabled);
    assert.equal(await page.locator('#supportDialogueHistory [data-message-id]').count(),1);
    assert.deepEqual(errors,[]);
   }finally{await context.close();}
  }
  const rejected=['no-proof','wrong-tenant','exact-wrong-tenant','changed-channel','changed-provider',...['provider','lead_id','channel_id','conversation_id'].flatMap(key=>['wrong-request-'+key,'wrong-canonical-'+key])];
  for(const mode of rejected){
   const {context,page,errors}=await mount(mode);try{
    assert.equal(await page.locator('#supportDialogueHistory [data-message-id]').count(),0,mode+' must fail closed');
    assert.ok(await page.locator('#supportDialogueNotice').textContent(),mode+' reports error');assert.deepEqual(errors,[]);
   }finally{await context.close();}
  }
  const {context,page,errors}=await mount('changed-pagination');try{
   await page.locator('#supportDialogueEarlier').click();await page.waitForFunction(()=>notificationCaseFixture.historyCalls.length===2&&!document.getElementById('supportDialogueRefresh').disabled);
   assert.equal(await page.locator('#supportDialogueHistory [data-message-id]').count(),1,'another canonical route cannot be appended to the loaded history');
   assert.equal(await page.locator('#supportDialogueHistory [data-message-id=older-alias-message]').count(),0);assert.ok(await page.locator('#supportDialogueNotice').textContent());assert.deepEqual(errors,[]);
  }finally{await context.close();}
  console.log('PASS case history: authenticated alias, exact-route compatibility, original pagination, material isolation, 13 invalid bindings and changed canonical pagination rejected');
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
