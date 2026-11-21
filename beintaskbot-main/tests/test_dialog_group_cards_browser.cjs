/* Synthetic source selection in Chromium; no production requests or messages. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'../.support-test-deps/node_modules/playwright');
const docs=path.resolve(__dirname,'../docs');
const html=fs.readFileSync(path.join(docs,'tenant-app.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined});
 try{for(const width of [360,1440]){
  const page=await browser.newPage({viewport:{width,height:900}});
  await page.route('**/*',route=>route.request().isNavigationRequest()?route.fulfill({contentType:'text/html',body:html}):route.abort());
  await page.goto('https://example.test/app?view=dialogs');
  await page.evaluate(()=>{
   document.querySelectorAll('.view').forEach(n=>n.classList.toggle('active',n.id==='view-dialogs'));
   const member={tenant_id:'synthetic',telegram_id:11,role:'owner',active:true};
   window.tenantProfileSnapshot={member,capabilities:{modules:{dialogs:true,support:true},dialog_company_binding:true}};
   const source=(name,id)=>({provider:'connect',channel_id:id,channel_name:name,lead_id:id==='a'?41:42,conversation_id:'120363432608782851@g.us',customer_name:'Synthetic group',customer_kind:'group',can_send:false,lifecycle_enabled:true,lifecycle:{state:'waiting',revision:0,can_accept:true}});
   const sources=[source('Huseyn','a'),source('Texniki Dəstək','b')];window.historyReads=[];window.accountCalls=[];
   sources[1].lifecycle={...sources[1].lifecycle,id:'episode',revision:2};
   window.fetch=async (target,opt={})=>{const url=String(target);let result={};
    if(url.includes('/api/platform/me'))return{ok:true,json:async()=>window.tenantProfileSnapshot};
    if(url.includes('/operator/dialog-company')){
     const q=new URL(url,'https://example.test').searchParams;
     if(opt.method==='POST'){
      const cmd=JSON.parse(opt.body);window.accountCalls.push(cmd);
      if(cmd.operator_search)return{ok:true,json:async()=>({success:true,accounts:[{account:'synthetic_a'},{account:'synthetic_b'}]})};
      if(cmd.operator_account){window.boundOperator=true;return{ok:true,json:async()=>({success:true,binding:{company_id:'new',company_name:'Selected Operator account',can_manage:true}})};}
      throw Error('Unexpected mutation');
     }
     if(window.offerOperatorPicker&&q.get('channel_id')==='b'){
      return{ok:true,json:async()=>({success:true,binding:window.boundOperator?{company_id:'new',company_name:'Selected Operator account',can_manage:true}:{company_id:null,can_manage:true,can_search_operator:true}})};
     }
     const name=(q.get('channel_id')==='a'?'Company A ':'Company B ')+'longname'.repeat(18);
     return{ok:true,json:async()=>({success:true,binding:{company_id:'test',company_name:name,can_manage:true}})};
    }
    if(url.includes('/dialogs/history')){const channel=new URL(url,'https://example.test').searchParams.get('channel_id');window.historyReads.push(channel);result={dialog:sources.find(s=>s.channel_id===channel),messages:[],next_before:null};}
    else if(url.includes('/dialogs/transfer-details')){
      const channel=new URL(url,'https://example.test').searchParams.get('channel_id');
      result={transfer:channel==='b'?{from_name:'Synthetic sender',to_name:'Synthetic recipient',at:'2026-11-16T09:11:22Z'}:null};
    }
    else if(url.includes('/dialogs/group-participants'))result={status:'available',phones:[],total:0};
    else if(url.includes('/dialogs?'))result={dialogs:[{...sources[0],group_sources:sources,unread_count:4,last_message_at:'2026-11-16T09:11:22Z'},...['waiting','accepted','closed'].map((state,i)=>({...source('Single','single'+i),customer_kind:'contact',customer_name:state,conversation_id:'single'+i,lifecycle:{state},unread_count:7,last_message_at:'2026-11-16T09:11:22Z'}))],channels:[],agents:[],total_count:4,next_offset:null};
    else if(!url.includes('/dialogs/read'))throw Error('Unexpected request '+url);
    return{ok:true,status:200,json:async()=>({success:true,result})};};
  });
  await page.addScriptTag({path:path.join(docs,'tenant-operator-i18n.js')});
  await page.addScriptTag({path:path.join(docs,'tenant-dialog-company.js')});
  await page.addScriptTag({path:path.join(docs,'tenant-operator-dialogs.js')});
  await page.evaluate(()=>window.tenantOperator.refresh());
  await page.locator('.operator-row').first().waitFor();
  assert.equal(await page.locator('.operator-row').count(),4);
  for(const row of await page.locator('.operator-row').all()){
   const g=await row.evaluate(el=>{const rect=n=>{const r=n.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom}};return {row:rect(el),copy:rect(el.querySelector('.operator-row-copy')),meta:rect(el.querySelector('.operator-row-meta')),unread:rect(el.querySelector('.operator-unread-count')),badge:el.querySelector('.operator-row-indicators .operator-lifecycle-badge')?rect(el.querySelector('.operator-row-indicators .operator-lifecycle-badge')):null,children:el.children.length};});
   assert.equal(g.children,3,'avatar, copy and right-side metadata occupy one grid row');
   assert(g.meta.left>=g.copy.right-1&&g.meta.right<=g.row.right+1,'metadata stays right of the copy and within the row');
   assert(g.unread.left>=g.copy.right-1&&g.unread.bottom<=g.row.bottom+1,'counter must not wrap below the avatar');
   if(g.badge)assert(Math.abs(g.badge.top-g.unread.top)<12,'text status and unread counter share the indicator line');
  }
  assert.equal(await page.locator('.operator-row').nth(1).locator('.operator-unread-count').textContent(),'7');
  await page.locator('.operator-row').first().click();
  await page.waitForFunction(()=>window.historyReads.at(-1)==='a');
  await page.waitForFunction(()=>document.querySelector('[data-company-value]')?.textContent.startsWith('Company A'));
  const card=page.locator('#operatorClientCard');
  const chat=page.locator('#operatorConversationContent');
  const closedWidth=(await chat.boundingBox()).width;
  assert.equal(await card.isVisible(),false);
  await page.locator('#operatorClientCardOpen').click();
  await card.waitFor();
  const openWidth=(await chat.boundingBox()).width;
  assert.equal(Math.abs(openWidth-closedWidth)<1,width<900,'mobile overlays the chat and desktop allocates a side column: '+JSON.stringify({width,openWidth,closedWidth,card:await card.boundingBox()}));
  assert.equal(await page.locator('#operatorConversationHeader [data-company-value]').count(),0);
  assert.equal(await card.locator('[data-client-transfer]').isVisible(),false);
  const companyBox=await page.locator('[data-operator-dialog-company]').boundingBox();
  assert(companyBox.width>0&&companyBox.x>=-1&&companyBox.x+companyBox.width<=width+1,'company field is discoverable without horizontal overflow');
  await page.locator('[data-operator-dialog-company] button').click();
  const companyPicker=await page.locator('#operatorDialogueCompanyPicker').boundingBox();
  assert(companyPicker.width>0&&companyPicker.x>=-1&&companyPicker.x+companyPicker.width<=width+1,'company picker fits the viewport');
  await page.keyboard.press('Escape');
  assert.equal(await card.isVisible(),false);
  for(const index of [1,0]){
   await page.locator('#operatorComposerSourceSelect').selectOption(String(index));
   await page.waitForFunction(channel=>window.historyReads.at(-1)===channel,index===0?'a':'b');
   await page.waitForFunction(name=>document.querySelector('[data-company-value]')?.textContent.startsWith(name),index===0?'Company A':'Company B');
  }
  await page.evaluate(()=>{window.offerOperatorPicker=true;});
  await page.locator('#operatorComposerSourceSelect').selectOption('1');
  await page.waitForFunction(()=>window.historyReads.at(-1)==='b'&&document.querySelector('[data-operator-dialog-company] button:not([hidden])')&&!document.querySelector('[data-company-value]').textContent.startsWith('Company'));
  await page.locator('#operatorDialogTitleText').click();
  await card.locator('[data-client-transfer]').waitFor();
  assert.match(await card.locator('[data-client-transfer]').innerText(),/Synthetic sender → Synthetic recipient/);
  assert.match(await card.innerText(),/Texniki Dəstək/);
  const edit=page.locator('[data-operator-dialog-company]>button');
  await edit.click();
  const drop=page.locator('#operatorDialogueAccountPicker'),input=drop.locator('input');
  const geometry=await drop.boundingBox(),anchor=await edit.boundingBox();
  assert(await drop.evaluate(node=>node.closest('dialog')?.id==='operatorClientCard'));
  assert(geometry.x>=-1&&geometry.x+geometry.width<=width+1&&geometry.y>=anchor.y+anchor.height-1,
   'Operator chooser stays in the right panel below the company control '+JSON.stringify({geometry,anchor,width}));
  assert(await input.evaluate(node=>document.activeElement===node),'Operator search receives focus');
  await input.fill('synthetic');
  const result=drop.locator('[data-operator-account-results] button').first();
  await result.waitFor();
  assert.equal(await result.locator('strong').textContent(),'@synthetic_a');
  assert.equal(await page.evaluate(()=>window.accountCalls.filter(cmd=>cmd.operator_account).length),0);
  await result.click();
  await page.waitForFunction(()=>document.querySelector('[data-company-value]')?.textContent==='Selected Operator account');
  const calls=await page.evaluate(()=>window.accountCalls.map(({operator_search,operator_account,channel_id,lead_id})=>[operator_search??null,operator_account??null,channel_id,lead_id]));
  assert.deepEqual(calls,[['synthetic',null,'b','42'],[null,'synthetic_a','b','42']]);
  if(width<900){await page.locator('[data-operator-back]').click();await page.waitForFunction(()=>document.querySelector('#operatorWorkspace').dataset.selected==='false');}
  await page.close();
 }
 console.log('PASS Chromium group routes, Operator dropdown geometry and explicit account binding at 360/1440px');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
