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
   const sources=[source('Synthetic source A','a'),source('Synthetic source B','b')];window.historyReads=[];window.companyReads=[];window.accountCalls=[];window.listSearches=[];
   sources[1].lifecycle={...sources[1].lifecycle,id:'episode',revision:2};
   window.fetch=async (target,opt={})=>{const url=String(target);let result={};
    if(url.includes('/api/platform/me'))return{ok:true,json:async()=>window.tenantProfileSnapshot};
    if(url.includes('/operator/dialog-company/public-link'))
     return{ok:true,json:async()=>({success:true,public_link:{state:'not_found'}})};
    if(url.includes('/operator/dialog-company')){
     const q=new URL(url,'https://example.test').searchParams;
     if(opt.method==='POST'){
      const cmd=JSON.parse(opt.body);
      if(cmd.action==='company_profile')return{ok:true,json:async()=>({success:true,company_profile:{state:'not_linked'}})};
      if(cmd.company_search)return{ok:true,json:async()=>({success:true,companies:[]})};
      window.accountCalls.push(cmd);
      if(cmd.operator_search)return{ok:true,json:async()=>({success:true,accounts:[{account:'synthetic_a'},{account:'synthetic_b'}]})};
      if(cmd.operator_account){window.boundOperator=true;return{ok:true,json:async()=>({success:true,binding:{company_id:'new',company_name:'Selected Operator account',can_manage:true}})};}
      throw Error('Unexpected mutation');
     }
     window.companyReads.push(q.get('channel_id'));
     if(window.offerOperatorPicker&&q.get('channel_id')==='b'){
      return{ok:true,json:async()=>({success:true,binding:window.boundOperator?{company_id:'new',company_name:'Selected Operator account',can_manage:true}:{company_id:null,can_manage:true,can_search_operator:window.offerOperatorPicker==='on'}})};
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
    else if(url.includes('/dialogs?')){window.listSearches.push(new URL(url,'https://example.test').searchParams.get('search')||'');result={dialogs:[{...sources[0],group_sources:sources,unread_count:4,last_message_at:'2026-11-16T09:11:22Z'},...['waiting','accepted','closed'].map((state,i)=>({...source('Single','single'+i),customer_kind:'contact',customer_name:state,conversation_id:'single'+i,lifecycle:{state},dialog_company_name:i===0?'Long <b>stored</b> company name '.repeat(10):undefined,unread_count:7,last_message_preview:'Latest message',last_message_at:'2026-11-16T09:11:22Z'}))],channels:[],agents:[],total_count:4,next_offset:null};}
    else if(!url.includes('/dialogs/read'))throw Error('Unexpected request '+url);
    return{ok:true,status:200,json:async()=>({success:true,result})};};
  });
  await page.addScriptTag({path:path.join(docs,'tenant-search-inputs.js')});
  await page.addScriptTag({path:path.join(docs,'tenant-operator-i18n.js')});
  await page.addScriptTag({path:path.join(docs,'tenant-dialog-company.js')});
  await page.addScriptTag({path:path.join(docs,'tenant-operator-dialogs.js')});
  await page.evaluate(()=>window.tenantOperator.refresh());
  await page.locator('.operator-row').first().waitFor();
  const listSearch=page.locator('#operatorSearch');
  await listSearch.click();await listSearch.fill('Synthetic');
  await page.waitForFunction(()=>window.listSearches.includes('Synthetic'));
  await listSearch.evaluate(input=>{input.value='saved.login@example.test';input.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertReplacementText'}));});
  assert.equal(await listSearch.inputValue(),'Synthetic');
  await listSearch.evaluate(input=>{input.value='silent.saved.login@example.test';return window.tenantOperator.refresh();});
  assert.equal(await listSearch.inputValue(),'Synthetic');
  assert(!await page.evaluate(()=>window.listSearches.some(query=>query.includes('example.test'))),'list API never receives the synthetic saved login');
  await listSearch.fill('');
  await page.waitForFunction(()=>window.listSearches.at(-1)==='');
  assert.equal(await page.locator('.operator-row').count(),4);
  for(const row of await page.locator('.operator-row').all()){
   const g=await row.evaluate(el=>{const rect=n=>{const r=n.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom}};return {row:rect(el),copy:rect(el.querySelector('.operator-row-copy')),meta:rect(el.querySelector('.operator-row-meta')),unread:rect(el.querySelector('.operator-unread-count')),badge:el.querySelector('.operator-row-indicators .operator-lifecycle-badge')?rect(el.querySelector('.operator-row-indicators .operator-lifecycle-badge')):null,children:el.children.length};});
   assert.equal(g.children,2,'the avatar sits beside the card body');
   assert(g.meta.left>=g.copy.right-1&&g.meta.right<=g.row.right+1,'metadata stays right of the copy and within the row');
   assert(g.unread.left>=g.copy.right-1&&g.unread.bottom<=g.row.bottom+1,'counter must not wrap below the avatar');
   if(g.badge)assert(Math.abs(g.badge.top-g.unread.top)<12,'text status and unread counter share the indicator line');
  }
  assert.equal(await page.locator('.operator-row').nth(1).locator('.operator-unread-count').textContent(),'7');
  const companyCard=page.locator('.operator-row').filter({has:page.locator('.operator-row-company')});
  assert.equal(await companyCard.count(),1);
  const companyLine=await companyCard.evaluate(row=>{
    const c=row.querySelector('.operator-row-company');
    const fullHeight=row.offsetHeight,literal=c.textContent.includes('<b>stored</b>')&&!c.querySelector('b');
    const truncated=c.scrollWidth>c.clientWidth&&getComputedStyle(c).textOverflow==='ellipsis';
    const sample=document.createElement('span');
    sample.className='operator-state operator-row-status';sample.dataset.state='manual';sample.hidden=true;
    row.append(sample);
    const former=getComputedStyle(sample),badge=getComputedStyle(c);
    const sameBadge=['backgroundColor','color','borderRadius','fontSize','fontWeight'].every(key=>former[key]===badge[key]);
    sample.remove();
    c.textContent='Short company';return {literal,truncated,sameBadge,noGrowth:fullHeight===row.offsetHeight};
  });
  assert.deepEqual(companyLine,{literal:true,truncated:true,sameBadge:true,noGrowth:true});
  assert.equal(await page.locator('#operatorRows .operator-row-status').count(),0);
  assert(await companyCard.locator('.operator-row-company').evaluate(n=>n.parentElement.nextElementSibling.matches('.operator-row-preview')),'company precedes the preview');
  assert.equal(await page.locator('#operatorRows .operator-row-source').count(),0,'sources are not duplicated in the list');
  assert.equal(await page.locator('.operator-row-context-with-preview').count(),0);
  const group=page.locator('.operator-row').first();
  assert.equal(await group.locator('.operator-row-sub').isVisible(),false,'missing group phone does not take space');
  assert.equal(await group.locator('.operator-row-preview').count(),0,'missing preview does not take space');
  assert.equal(await group.locator('.operator-row-source').count(),0,'group source is shown in the chat header, not the list');
  await page.locator('.operator-row').first().click();
  await page.waitForFunction(()=>window.historyReads.at(-1)==='a');
  await page.waitForFunction(()=>document.querySelector('[data-company-value]')?.textContent.startsWith('Company A'));
  const card=page.locator('#operatorClientCard'),panel=page.locator('#operatorCompanyPanel');
  if(width>900)await page.waitForFunction(()=>document.querySelector('#operatorCompanyPanel')?.open===true);
  else assert.equal(await panel.evaluate(node=>node.open),false,'right panel starts closed on mobile');
  const chat=page.locator('#operatorConversationContent');
  const closedWidth=(await chat.boundingBox()).width;
  assert.equal(await card.isVisible(),false);
  await page.locator('#operatorClientCardOpen').click();
  await card.waitFor();
  assert(Math.abs((await chat.boundingBox()).width-closedWidth)<1,'avatar opens a modal without resizing the chat');
  assert(await card.evaluate(n=>n.matches(':modal')));
  assert.equal(await page.locator('#operatorConversationHeader [data-company-value]').count(),0);
  assert.equal(await card.locator('#operatorClientCompanyHost').count(),0);
  assert.equal(await card.locator('#operatorConversationName').count(),1);
  const dialogueBlock=card.locator('.operator-client-panel-block').filter({has:page.locator('[data-panel-agent-status]')});
  assert(await dialogueBlock.locator('[data-panel-agent-status]').isVisible(),'agent mode stays in the dialogue block');
  assert.match(await dialogueBlock.innerText(),/Synthetic source A/,'source is retained in the separate avatar modal');
  assert.equal(await card.locator('.operator-client-panel-block').last().locator('[data-group-participants]').count(),1,'the roster is the last block in groups');
  assert.equal(await card.locator('.operator-client-panel-block').last().getAttribute('open'),null,'group members are collapsed by default');
  const sourceRow=dialogueBlock.locator('.operator-client-panel-row').first();
  assert.deepEqual(await sourceRow.evaluate(row=>{
    const style=getComputedStyle(row);
    return [style.paddingBlockStart,style.paddingBlockEnd,style.rowGap];
  }),['6px','6px','3px'],'read-only source label and value use compact spacing');
  assert.equal(await card.locator('[data-client-transfer]').isVisible(),false);
  await page.keyboard.press('Escape');
  if(width<900)await page.evaluate(()=>document.querySelector('#operatorCompanyPanelOpen').click());
  else assert.equal(await panel.evaluate(node=>node.open),true,'right panel stays open on desktop');
  const openWidth=(await chat.boundingBox()).width;
  if(width<900)assert(Math.abs(openWidth-closedWidth)<1,'company overlays on mobile without resizing the chat');
  else assert(openWidth<(await page.locator('.operator-conversation-pane').boundingBox()).width-100,'desktop right panel reserves a column beside the chat');
  const headerSource=page.locator('#operatorConversationHeader .operator-client-source');
  assert.equal(await headerSource.innerText(),'Synthetic source A');
  const sidebarInfo=panel.locator('#operatorSidebarDialogInfo');
  assert.equal(await sidebarInfo.count(),1,'the structural Conversation container survives');
  assert(!await sidebarInfo.isVisible(),'no empty Conversation section is shown after removing the duplicate source');
  assert.equal(await sidebarInfo.evaluate(el=>el.getBoundingClientRect().height),0,'the hidden section reserves no space');
  assert.equal(await panel.getByText('Synthetic source A',{exact:true}).count(),0,'the right panel does not repeat the source');
  const companyBox=await page.locator('[data-operator-dialog-company]').boundingBox();
  assert(companyBox.width>0&&companyBox.x>=-1&&companyBox.x+companyBox.width<=width+1,'company field is discoverable without horizontal overflow');
  assert.match(await page.locator('#operatorDialogueCompanySearch').inputValue(),/^Company A/);
  const unlink=panel.locator('.operator-company-unlink');
  assert.equal(await unlink.textContent(),'×');
  assert((await unlink.boundingBox()).width<=30);
  await page.keyboard.press('Escape');
  assert.equal(await card.isVisible(),false);
  for(const index of [1,0]){
   await page.locator('#operatorComposerSourceSelect').selectOption(String(index));
   await page.waitForFunction(channel=>window.historyReads.at(-1)===channel,index===0?'a':'b');
   await page.waitForFunction(name=>document.querySelector('[data-company-value]')?.textContent.startsWith(name),index===0?'Company A':'Company B');
   assert.equal(await headerSource.innerText(),index===0?'Synthetic source A':'Synthetic source B','the group picker still updates the exact header source');
  }
  await page.evaluate(()=>{window.offerOperatorPicker='off';});
  await page.locator('#operatorComposerSourceSelect').selectOption('1');
  await page.waitForFunction(()=>window.historyReads.at(-1)==='b'&&document.querySelector('#operatorDialogueCompanySearch')&&!document.querySelector('[data-company-value]').textContent.startsWith('Company'));
  await page.locator('#operatorDialogTitleText').click();
  await card.locator('[data-client-transfer]').waitFor();
  assert.match(await card.locator('[data-client-transfer]').innerText(),/Synthetic sender → Synthetic recipient/);
  assert.match(await card.innerText(),/Synthetic source B/);
  await page.keyboard.press('Escape');
  if(width<900)await page.locator('#operatorCompanyPanelOpen').click();
  else assert.equal(await panel.evaluate(node=>node.open),true,'desktop details remain open after closing the client card');
  const search=page.locator('#operatorDialogueCompanySearch');
  const header=()=>page.locator('#operatorConversationHeader').evaluate(node=>[node.offsetWidth,node.offsetHeight,node.textContent]);
  const before=await header();
  assert(await search.isVisible(),'company search is visible without an extra button');
  await search.click();
  assert(await page.locator('#operatorDialogueCompanyPicker').isVisible(),'default-off still offers the inline CRM company picker');
  await page.keyboard.press('Escape');
  const priorReads=await page.evaluate(()=>window.companyReads.length);
  await page.evaluate(()=>{window.offerOperatorPicker='on';document.dispatchEvent(new Event('tenant-dialog-reauthorized'));});
  await page.waitForFunction(n=>window.companyReads.length>n,priorReads);
  if(width<900)await page.locator('#operatorCompanyPanelOpen').click();
  else assert.equal(await panel.evaluate(node=>node.open),true,'desktop details remain open while permissions refresh');
  assert.deepEqual(await header(),before);
  await search.click();
  const drop=page.locator('#operatorDialogueCompanySuggestions'),input=search;
  assert(!await drop.isVisible(),'focusing an empty field does not open a list');
  await input.fill('synthetic');
  await drop.locator('[data-operator-account-results] button').first().waitFor();
  const geometry=await drop.boundingBox(),anchor=await search.boundingBox();
  assert(await drop.evaluate(node=>node.closest('dialog')?.id==='operatorCompanyPanel'));
  assert(geometry.x>=-1&&geometry.x+geometry.width<=width+1&&geometry.y>=anchor.y+anchor.height-1,
   'Operator chooser stays in the right panel below the company control '+JSON.stringify({geometry,anchor,width}));
  assert.deepEqual(await header(),before,'opening the inline chooser leaves the header geometry and content intact');
  assert(await input.evaluate(node=>document.activeElement===node),'Operator search receives focus');
  assert.equal(await drop.locator('button.primary,select').count(),0,'choosing a result is the only save action');
  assert.equal(await card.locator('[data-group-participants]').count(),1,'group keeps its participant roster');
  const scroll=panel.locator('.operator-client-panel-content');
  const h=(await page.viewportSize()).height;
  await page.setViewportSize({width,height:300});
  await scroll.evaluate(el=>{const spacer=document.createElement('div');spacer.style.height='360px';spacer.dataset.testOverflow='true';el.append(spacer);});
  await page.waitForFunction(()=>{const n=document.querySelector('#operatorCompanyPanel .operator-client-panel-content');return n.scrollHeight>n.clientHeight;});
  const headerTop=await panel.locator('header').evaluate(n=>n.getBoundingClientRect().top);
  await scroll.evaluate(n=>{n.scrollTop=n.scrollHeight;});
  assert((await scroll.evaluate(n=>n.scrollTop))>0,'the panel content scrolls at a short viewport');
  assert.equal(await panel.locator('header').evaluate(n=>n.getBoundingClientRect().top),headerTop);
  await scroll.locator('[data-test-overflow]').evaluate(el=>el.remove());
  await page.setViewportSize({width,height:h});
  const result=drop.locator('[data-operator-account-results] button').first();
  await result.waitFor();
  assert.equal(await result.locator('strong').textContent(),'@synthetic_a');
  assert.equal(await page.evaluate(()=>window.accountCalls.filter(cmd=>cmd.operator_account).length),0);
  assert.equal(await input.evaluate(el=>el.form),null,'binding search is not associated with a login form');
  assert.equal(await input.evaluate(el=>el.closest('[role=search]')?.dataset.formType),'other','dialogue company chooser is classified as search, not authentication');
  assert.equal(await result.evaluate(el=>el.form),null,'binding action never submits a native form');
  await page.evaluate(()=>{window.companyNativeSubmits=0;document.addEventListener('submit',()=>window.companyNativeSubmits++,true);});
  await result.click();
  assert.equal(await page.evaluate(()=>window.companyNativeSubmits),0,'actual company binding does not trigger native password-save heuristics from a form submit');
  await page.waitForFunction(()=>document.querySelector('[data-company-value]')?.textContent==='Selected Operator account');
  assert.equal(await input.inputValue(),'Selected Operator account');
  const calls=await page.evaluate(()=>window.accountCalls.map(({operator_search,operator_account,channel_id,lead_id})=>[operator_search??null,operator_account??null,channel_id,lead_id]));
  assert.deepEqual(calls,[['synthetic',null,'b','42'],[null,'synthetic_a','b','42']]);
  if(width<900){
   await panel.locator('#operatorCompanyPanelClose').click();
   await panel.waitFor({state:'hidden'});
   await page.locator('[data-operator-back]').click();
   await page.waitForFunction(()=>document.querySelector('#operatorWorkspace').dataset.selected==='false');
  }
  await page.close();
 }
 console.log('PASS Chromium group routes, Operator dropdown geometry and explicit account binding at 360/1440px');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
