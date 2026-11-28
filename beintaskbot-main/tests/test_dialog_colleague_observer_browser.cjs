/* Production UI, isolated routes and synthetic dialogue only. */
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..'), tenant='00000000-0000-0000-0000-000000000101';
const source='00000000-0000-0000-0000-000000000201';
const row={provider:'connect',lead_id:41,channel_id:source,conversation_id:'synthetic-peer',
 customer_kind:'contact',customer_name:'Synthetic customer',channel_name:'WhatsApp',
 lifecycle_enabled:true,lifecycle:{state:'accepted',operator_id:'44',revision:2},
 colleague_read_only:true,archive_read_only:true};
const original={id:'voice-row',direction:'incoming',source:'connect',message_type:'audio',body:'',
 external_id:'connect:00000000-0000-0000-0000-000000000301',
 happened_at:'2026-11-25T10:00:00Z'};
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||
 (fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':chromium.executablePath()),args:['--no-sandbox']});
 try{
 for(const width of [1280,390]){
  const page=await browser.newPage({viewport:{width,height:844}}),errors=[],requests=[];
  let assigned=true,peerSelected=false;
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.testReminderCalls=[];});
  await page.route('**/*',async route=>{
   const req=route.request(),url=new URL(req.url()),p=url.pathname,q=url.searchParams;
   if(p==='/app')return route.fulfill({path:path.join(root,'docs/tenant-app.html')});
   if(p.startsWith('/assets/')){const file=path.join(root,'docs',path.basename(p));return fs.existsSync(file)?route.fulfill({path:file}):route.fulfill({status:404,body:''});}
   requests.push({path:p,method:req.method(),observed:q.get('observed_operator_id'),lifecycle:q.get('lifecycle'),channel:q.get('channel')});
   if(p==='/api/platform/me')return route.fulfill({json:{success:true,member:{tenant_id:tenant,telegram_id:33,role:'operator',
     permissions:['dialogs'],active:true},capabilities:{modules:{dialogs:true}}}});
   let result={};
   if(p.endsWith('/dialogs')&&q.get('colleague_options')==='1')result={enabled:true,operators:[{id:'44',name:'Synthetic colleague'}]};
   else if(p.endsWith('/dialogs')){
    const observed=q.get('observed_operator_id');
    if(observed){assert.equal(observed,'44');assert.equal(q.get('expected_tenant_id'),tenant);assert.ok(!q.has('paired_history'));peerSelected=true;}
    result={dialogs:observed&&assigned?[row]:[],total_count:observed&&assigned?1:0,channels:[{id:'connect:'+source,name:'WhatsApp'}],agents:[],next_offset:null};
   }else if(p.endsWith('/dialogs/history')){
    assert.equal(q.get('observed_operator_id'),'44');
    assert.equal(q.get('expected_user_id'),'33');
    for(const key of ['search_overview','paired_history','shared_reminder_events','operator_events','owner_overview'])assert.ok(!q.has(key));
    if(!assigned)return route.fulfill({status:404,json:{success:false,error:'Synthetic transfer'}});
    const old=q.has('before');
    result={dialog:row,messages:old?[{...original,id:'older-message',message_type:'text',body:'Older synthetic message'}]:[original],next_before:old?null:'older-cursor',read_through_message_id:null};
   }else if(p.endsWith('/crm/chat/attachment')){
    assert.equal(q.get('observed_operator_id'),'44');
    return route.fulfill({body:'test',contentType:'audio/ogg'});
   }
   return route.fulfill({json:{success:true,result}});
  });
  try{
   await page.goto('https://crm.test/app?view=dialogs');
   await page.locator('#operatorFiltersToggle').click();
   const selector=page.locator('#operatorColleague');await selector.waitFor();
   assert.equal(await selector.inputValue(),'');
   const state=page.locator('#operatorLifecycle');
   const sourceSelect=page.locator('#operatorChannel');
   await sourceSelect.selectOption('connect:'+source);
   await state.selectOption('completed');
   const completed=page.waitForRequest(r=>{const q=new URL(r.url()).searchParams;return q.get('observed_operator_id')==='44'&&q.get('lifecycle')==='completed';});
   await selector.selectOption('44');
   await page.waitForFunction(()=>document.querySelector('#operatorLifecycle')?.value==='completed');
   assert.equal(new URL((await completed).url()).searchParams.get('channel'),'connect:'+source);
   for(const mode of ['working','all']){
     const request=page.waitForRequest(r=>{const q=new URL(r.url()).searchParams;return q.get('observed_operator_id')==='44'&&q.get('lifecycle')===mode;});
     await state.selectOption(mode);
     assert.equal(new URL((await request).url()).searchParams.get('channel'),'connect:'+source);
   }
   await page.keyboard.press('Escape');
   await page.locator('.operator-row').first().click();
   await page.locator('[data-colleague-read-only]').waitFor();
   assert.equal(await page.locator('#sendChat,#chatText,#operatorCopilotToggle,#operatorAcceptAction').count(),0);
   assert.equal(await page.locator('#operatorMarkAllRead').isVisible(),false);
   const audio=await page.locator('audio').getAttribute('src');
   assert.equal(new URL(audio,'https://crm.test').searchParams.get('observed_operator_id'),'44');
   await page.locator('#operatorColleagueEarlier').click();
   await page.locator('[data-message-id=older-message]').waitFor();
   assigned=false;
   await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-employee-status-changed')));
   await page.locator('[data-colleague-read-only]').waitFor({state:'detached'});
   assert.ok(peerSelected);
   assert.equal(requests.some(r=>r.method==='POST'&&/dialogs\/read|reminder|chat\/send/.test(r.path)),false);
   assert.deepEqual(errors,[]);
  }finally{await page.close();}
 }
 }finally{await browser.close();}
 console.log('PASS colleague filter desktop and mobile: exact reads, older pages, transfer, no mutations');
})().catch(e=>{console.error(e);process.exitCode=1;});
