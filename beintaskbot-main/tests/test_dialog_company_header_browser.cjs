/* Synthetic route/authorization checks for the read-only header company. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'../.support-test-deps/node_modules/playwright');
const docs=path.resolve(__dirname,'../docs');
const html=fs.readFileSync(path.join(docs,'tenant-app.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
const alpha='<b>Company A & Partners</b> '+ 'very long stored name '.repeat(10);
const beta='@verified_account';
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined});
 try{for(const width of [360,430,1440]){
  const page=await browser.newPage({viewport:{width,height:800},hasTouch:width<=900});
  await page.route('**/*',route=>route.request().isNavigationRequest()?route.fulfill({contentType:'text/html',body:html}):route.abort());
  await page.goto('https://example.test/app?view=dialogs');
  await page.evaluate(({alpha,beta})=>{
   document.querySelectorAll('.view').forEach(n=>n.classList.toggle('active',n.id==='view-dialogs'));
   const member={tenant_id:'synthetic',telegram_id:11,role:'owner',active:true};
   const profile={member,capabilities:{modules:{dialogs:true,support:true},dialog_company_binding:true,shared_dialog_reminders:true}};
   window.tenantProfileSnapshot=profile;
   const group=(channel_id,lead_id)=>({provider:'connect',channel_id,lead_id,channel_name:'Source '+channel_id,
    conversation_id:'same-synthetic@g.us',customer_name:'Customer Alpha',customer_kind:'group',can_send:false,shared_reminder:{can_schedule:true}});
   const sources=[
    {...group('a',41),assignee_name:'Chat-only person',
     handoff_active:true,handoff_assignee_name:'Transfer history person'},
    {...group('b',42),assignee_name:'Other chat-only person'},
   ];
   const fixtures={a:{company_id:'alpha',company_name:alpha,can_manage:true,can_search_operator:true},
      b:{company_id:'beta',company_name:beta,can_manage:false}};
   window.mode={a:'delayed',b:'masked'};window.releases={};window.bindingGets=[];
   window.cases={a:{status:'resolved',responsible_name:'Actual & <em>case</em> worker'},b:null};
   window.caseGets=[];window.caseWaiters=[];window.profilePosts=[];window.reminderWrites=0;window.transferReads=0;window.savedOpen=false;
   window.fetch=async(target,opt={})=>{
    const u=new URL(target,'https://example.test'),api=u.pathname,q=u.searchParams;
    const ok=result=>({ok:true,json:async()=>({success:true,result})});
    if(api==='/api/platform/me')return {ok:true,json:async()=>profile};
    if(api.endsWith('/dialogs/shared-reminders')){
      if(opt.method==='POST')window.reminderWrites++;
      return ok({reminders:[]});
    }
    if(api.endsWith('/dialogs/company-panel/preferences')){
      if(opt.method==='POST')window.savedOpen=JSON.parse(opt.body).open;
      return ok({open:window.savedOpen});
    }
    if(api==='/api/platform/operator/dialog-company'){
     if(opt.method==='POST'){
      const data=JSON.parse(opt.body);
      if(data.action==='company_profile'){
       if(data.expected_company_id!==fixtures[data.channel_id]?.company_id)throw Error('Inexact company profile');
       window.profilePosts.push(data.channel_id);
       return {ok:true,json:async()=>({success:true,company_profile:{state:'available',registration_phone:'+994501234567',balance:0,actions:{task:true,credit:true},action_binding:'b'.repeat(64),task_options:{account:'verified-account',projects:['Umumi'],priorities:[2,3,4]}}})};
      }
      if(data.action==='company_tasks'||data.action==='company_history')return {ok:true,json:async()=>({success:true,company_section:{
        state:'available',rows:data.action==='company_history'?[['2026-11-03','4.50 AZN','credit'],['2026-11-02','-2.00','daily_expense']]:[]}})};
      if(data.operator_search!=='Test company'||data.channel_id!=='a'||data.lead_id!=='41')throw Error('Unexpected company search');
      return {ok:true,json:async()=>({success:true,accounts:[{account:'Test company'}]})};
     }
     if(opt.method!=='GET')throw Error('Unexpected method');
     const channel=q.get('channel_id'),route=sources.find(r=>r.channel_id===channel);
     if(!route||q.get('provider')!==route.provider||q.get('lead_id')!==String(route.lead_id)||
       q.get('conversation_id')!==route.conversation_id||q.get('expected_tenant_id')!==member.tenant_id||
       q.get('expected_user_id')!==String(member.telegram_id))throw Error('Inexact company read');
     window.bindingGets.push(channel);
     const mode=window.mode[channel];
     if(mode==='delayed')await new Promise(release=>{window.releases[channel]=release;});
     if(mode==='denied'||mode==='offline')return {ok:false,status:mode==='denied'?403:503,json:async()=>({success:false})};
     const binding=mode==='masked'?{company_id:null,company_name:null,can_manage:false}:fixtures[channel];
     return {ok:true,json:async()=>({success:true,binding})};
    }
    if(api.endsWith('/dialogs/transfer-candidates')){
     if(opt.method||q.get('channel_id')!=='a'||q.get('lead_id')!=='41'||q.get('expected_revision')!=='3')throw Error('Inexact transfer candidate read');
     window.transferReads++;
     return ok({targets:[{employee_id:22,name:'Synthetic online operator'}]});
    }
    if(api.endsWith('/dialogs/history')){
     const channel=q.get('channel_id'),source=sources.find(r=>r.channel_id===channel);
     if(q.get('support_case_only')==='1'){
      const {provider,lead_id,channel_id,conversation_id}=source;
      if(q.get('provider')!==provider||q.get('lead_id')!==String(lead_id)||q.get('conversation_id')!==conversation_id||
         q.has('anchor')||q.has('before'))throw Error('Inexact compact case request');
      window.caseGets.push(channel);
      const support_case=window.cases[channel]; // Capture before any simulated revocation.
      if(window.holdCaseRead)await new Promise(release=>window.caseWaiters.push(release));
      return ok({dialog:{provider,lead_id,channel_id,conversation_id,support_case}});
     }
     return ok({dialog:{...source,...(channel==='a'?{lifecycle:{state:'accepted',revision:3,can_transfer:true,accepted_by_name:'Actual & <b>accepting</b> employee'}}:{})},messages:[],next_before:null});
    }
    if(api.endsWith('/dialogs'))return ok({dialogs:[{...sources[0],group_sources:sources}],channels:[],agents:[],next_offset:null});
    if(api.endsWith('/dialogs/group-participants'))return ok({status:'available',phones:[],total:0});
    return ok({});
   };
  },{alpha,beta});
  for(const file of ['tenant-operator-i18n.js','tenant-dialog-company.js','tenant-operator-dialogs.js','tenant-shared-dialog-reminders.js'])
    await page.addScriptTag({path:path.join(docs,file)});
  await page.evaluate(()=>{window.tenantI18n.setLanguage('en');return window.tenantOperator.refresh();});
  await page.locator('.operator-row').first().click();
  const name=page.locator('#operatorDialogCompanyName'),head=page.locator('#operatorConversationHeader');
  await page.waitForFunction(()=>Boolean(window.releases.a));
  assert(!await name.isVisible());
  assert.equal(await name.textContent(),'');
  assert.equal(await name.getAttribute('title'),null);
  const height=await head.evaluate(n=>n.offsetHeight);
  await page.evaluate(()=>{window.mode.a='visible';window.releases.a();});
  await name.waitFor();
  assert.equal(await name.textContent(),alpha,'stored name is preserved as literal text');
  assert.equal(await name.locator('b').count(),0);
  assert.equal(await name.getAttribute('title'),alpha);
  assert.equal(await head.evaluate(n=>n.offsetHeight),height,'the tag never adds a header row');
  assert(await name.evaluate(n=>n.parentElement.matches('[data-header-meta]')),'company name stays in the compact metadata row');
  assert(await name.evaluate(n=>n.getBoundingClientRect().top>=document.querySelector('#operatorDialogTitleSlot').getBoundingClientRect().bottom),
    'company stays below the customer name');
  assert(await name.evaluate(n=>n.scrollWidth>n.clientWidth&&getComputedStyle(n).textOverflow==='ellipsis'));
  assert.equal(await head.locator('.operator-channel-label').count(),0);
  assert.deepEqual(await page.evaluate(()=>window.caseGets),[],'no case query while Information is closed');
  assert.deepEqual(await page.evaluate(()=>window.bindingGets),['a'],'header and side panel reuse one exact GET');
  assert.equal(await page.locator('#operatorClientCompanyHost [data-company-value]').textContent(),alpha);
  const card=page.locator('#operatorClientCard'),panel=page.locator('#operatorCompanyPanel');
  assert.equal(await panel.isVisible(),width>900,'desktop details stay open; mobile starts with the overlay closed');
  assert.deepEqual(await page.evaluate(()=>window.profilePosts),width<=900?[]:['a'],'company profile loads only when the details panel is visible');
  const pane=page.locator('#operatorConversationPane');
  const corners=el=>{const c=getComputedStyle(el);return [c.borderTopLeftRadius,c.borderTopRightRadius,c.borderBottomLeftRadius,c.borderBottomRightRadius];};
  const originalPaneCorners=await pane.evaluate(corners);
  const toggle=page.locator('#operatorCompanyPanelOpen');
  const hitTarget=el=>{
    const a=el.getBoundingClientRect();
    const target=document.elementFromPoint(a.left+a.width/2,a.top+a.height/2);
    return a.width>=34&&a.height>=34&&el.contains(target);
  };
  assert.equal(await page.locator('#operatorConversationHeader #operatorCompanyPanelOpen').count(),0);
  assert.equal(await page.locator('.operator-history-tools #operatorCompanyPanelOpen').count(),1);
  if(width<=900){
    await toggle.scrollIntoViewIfNeeded();
    assert(await toggle.evaluate(hitTarget),'closed mobile sidebar has a real toggle in the previous history toolbar');
    await toggle.click();
  }else{
    assert(await panel.isVisible(),'desktop details do not depend on a toggle');
  }
  await panel.locator('[data-company-profile]').getByText('+994501234567',{exact:true}).waitFor();
  assert.match(await panel.locator('[data-company-profile]').textContent(),/0\.00/);
  assert(!await card.isVisible());
  const companyHeader=panel.locator('.operator-client-panel-header');
  assert.deepEqual(await companyHeader.getByRole('tab').allTextContents(),['Main','Tasks','Modules','Payments']);
  const panelClose=panel.locator('#operatorCompanyPanelClose');
  assert.equal(await panelClose.isVisible(),width<=900,'only mobile has a visible in-panel close');
  assert.equal(await panel.getAttribute('aria-label'),'Company details');
  assert.equal(await companyHeader.getByRole('tablist',{name:'Company',exact:true}).count(),1);
  assert.equal(await companyHeader.getByRole('heading').count(),0,'remove only the visual company title');
  const compactHeader=await companyHeader.evaluate(el=>{
    const h=el.getBoundingClientRect(),tabs=el.querySelector('[role=tablist]').getBoundingClientRect();
    const style=getComputedStyle(el);
    return {height:h.height,tabHeight:tabs.height,contained:tabs.top>=h.top&&tabs.bottom<=h.bottom&&tabs.right<=h.right,
      paddingStart:style.paddingBlockStart,paddingEnd:style.paddingBlockEnd};
  });
  assert(compactHeader.contained&&compactHeader.height<=(width<=900?56:60),'four tabs fit the chat-aligned header: '+JSON.stringify(compactHeader));
  assert.deepEqual([compactHeader.paddingStart,compactHeader.paddingEnd],['0px','0px'],'tabs fill the aligned header without extra vertical padding');
  for(const tab of await companyHeader.getByRole('tab').all()){
    assert.deepEqual(await tab.evaluate(corners),['0px','0px','0px','0px'],'tab edges and the active underline are square');
    assert((await tab.boundingBox()).height>=32,'compact tabs retain a usable click target');
  }
  assert.deepEqual((await panel.evaluate(corners)).slice(2),['0px','0px']);
  assert.deepEqual(await pane.evaluate(corners),width<=900?originalPaneCorners:[...originalPaneCorners.slice(0,3),'0px'],'only the desktop sidebar shares the lower right corner');
  if(width<=900){
    const box=await panel.boundingBox();
    assert.deepEqual(box,{x:0,y:0,width,height:800},'mobile panel covers the viewport including both edges');
    assert(await panel.evaluate(el=>!el.closest('[inert]')),'the mobile panel itself is interactive');
    assert(await panelClose.evaluate(hitTarget),'the mobile close control receives a real touch');
    assert(await toggle.evaluate(el=>!!el.closest('[inert]')),'the covered toolbar is not keyboard-accessible');
    assert(await page.locator('#chatRows').evaluate(el=>!!el.closest('[inert]')),'behind the panel the chat is inert');
  }else{
    assert(await toggle.evaluate(el=>!el.closest('[inert]')),'the desktop original-slot toggle remains active');
    assert(await toggle.evaluate(hitTarget),'the desktop panel does not intercept the toggle');
  }
  if(width<=900)await page.waitForFunction(()=>window.savedOpen===true);
  else assert.equal(await page.evaluate(()=>window.savedOpen),false,'desktop auto-open does not persist a manual preference');
  if(width<=900){
    const transfer=panel.locator('#operatorSidebarTransfer summary');
    await transfer.tap();
    await panel.getByRole('button',{name:'Synthetic online operator',exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>window.transferReads),1,'tapping Transfer reads exact authorized recipients without writing');
    await transfer.tap();
  }
  assert.equal(await panel.locator('#operatorConversationNameInput,[data-client-case-field],[data-panel-agent-status]').count(),0);
  assert.deepEqual(await page.evaluate(()=>window.profilePosts),['a']);
  assert.equal(await panel.locator('#operatorCompanyView-main [data-company-action]').count(),0);
  const reminder=panel.getByRole('button',{name:'Create reminder',exact:true});
  await reminder.waitFor();
  assert(await reminder.evaluate(button=>button.parentElement===document.querySelector('#operatorCompanyView-main').lastElementChild),
    'rendered Company Main ends with the existing reminder control');
  await reminder.click();
  const schedule=page.getByRole('dialog',{name:'Create reminder',exact:true});
  await schedule.getByRole('button',{name:'Close',exact:true}).click();
  assert.equal(await page.evaluate(()=>window.reminderWrites),0,'opening the reminder form does not schedule');
  await companyHeader.getByRole('tab',{name:'Tasks',exact:true}).click();
  assert(!await panel.locator('#operatorSharedReminderCreate').isVisible());
  await panel.getByRole('button',{name:'Create task',exact:true}).click();
  const taskModal=page.getByRole('dialog',{name:'New task',exact:true});
  await taskModal.waitFor();
  assert.equal(await taskModal.locator('input[readonly]').inputValue(),'verified-account');
  assert(await taskModal.evaluate(el=>{
    const r=el.getBoundingClientRect();
    return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight&&
      Math.abs((r.left+r.right)/2-innerWidth/2)<=1&&Math.abs((r.top+r.bottom)/2-innerHeight/2)<=1;
  }),'task modal stays centered horizontally and vertically');
  const modalForm=taskModal.locator('form');
  const previousTheme=await page.evaluate(()=>document.documentElement.getAttribute('data-app-theme'));
  const borders=[];
  for(const theme of ['light','dark']){
    await page.evaluate(theme=>document.documentElement.dataset.appTheme=theme,theme);
    const fields=await modalForm.evaluate(el=>{
      el.querySelector('button').focus();
      return [el.querySelector('input:not([readonly])'),el.querySelector('textarea')].map(field=>{
      const c=getComputedStyle(field);
      return [c.borderTopWidth,c.borderTopStyle,c.borderTopColor,c.backgroundColor,c.borderTopLeftRadius];
      });
    });
    assert.deepEqual(fields[0],fields[1],'description border matches other task fields');
    assert(fields[1][0]==='1px'&&fields[1][1]==='solid'&&fields[1][2]!==fields[1][3]&&fields[1][4]==='9px');
    const focus=await modalForm.locator('textarea').evaluate(field=>{
      field.focus();const c=getComputedStyle(field);
      return [c.borderTopWidth,c.borderTopColor,c.outlineWidth,c.boxShadow];
    });
    assert(focus[0]==='1px'&&focus[1]!==fields[1][2]&&parseFloat(focus[2])<=1&&focus[3]==='none','task focus is a restrained border cue');
    borders.push(fields[1][2]);
  }
  assert.notEqual(...borders,'task border follows light and dark theme');
  await page.evaluate(theme=>{if(theme===null)delete document.documentElement.dataset.appTheme;else document.documentElement.dataset.appTheme=theme;},previousTheme);
  assert.equal(await taskModal.locator('.operator-company-action-grid').evaluate(el=>getComputedStyle(el).gridTemplateColumns.split(' ').length),width<=600?1:3);
  const taskWidth=(await taskModal.boundingBox()).width;
  await taskModal.getByRole('button',{name:'Cancel',exact:true}).click();
  await companyHeader.getByRole('tab',{name:'Payments',exact:true}).click();
  const paymentTable=panel.getByRole('table',{name:'Transaction history',exact:true});
  await paymentTable.waitFor();
  assert.deepEqual(await paymentTable.getByRole('columnheader').allTextContents(),['Type','Amount']);
  assert(await paymentTable.evaluate(el=>{
    const table=el.getBoundingClientRect(),panel=el.closest('dialog').getBoundingClientRect();
    return table.left>=panel.left&&table.right<=panel.right&&el.scrollWidth<=el.clientWidth;
  }),'payment table fits the panel with no horizontal overflow');
  await panel.getByRole('button',{name:'Add credit',exact:true}).click();
  const creditModal=page.getByRole('dialog',{name:'Add credit',exact:true});await creditModal.waitFor();
  assert((await creditModal.boundingBox()).width<=Math.min(400,taskWidth));
  assert(await creditModal.evaluate(el=>{
    const r=el.getBoundingClientRect(),outer=getComputedStyle(el);
    const thin=[...el.querySelectorAll('input')].every(field=>{
      const c=getComputedStyle(field);return c.borderTopWidth==='1px'&&c.borderTopStyle==='solid';
    });
    return Math.abs((r.left+r.right)/2-innerWidth/2)<=1&&Math.abs((r.top+r.bottom)/2-innerHeight/2)<=1&&
      outer.borderTopWidth==='1px'&&thin;
  }),'credit modal is centered and keeps thin borders');
  assert(await creditModal.locator('input').last().evaluate(field=>{
    field.blur();const idle=getComputedStyle(field).borderTopColor;
    field.focus();const active=getComputedStyle(field);
    return active.borderTopWidth==='1px'&&active.borderTopLeftRadius==='9px'&&active.borderTopColor!==idle&&
      parseFloat(active.outlineWidth)<=1&&active.boxShadow==='none';
  }),'credit reason focus keeps the rounded one-pixel cue');
  await creditModal.getByRole('button',{name:'Cancel',exact:true}).click();
  await companyHeader.getByRole('tab',{name:'Main',exact:true}).click();
  const search=panel.locator('#operatorDialogueCompanySearch');
  assert(!await search.isEditable(),'idle search resists autofill');
  assert(await search.evaluate(el=>{const r=el.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===el;}),'visible search field receives its actual pointer event');
  if(width<=900)await search.tap();else await search.click();
  assert(await search.evaluate(el=>el===document.activeElement),'the actual pointer opens the keyboard focus target');
  assert(await search.isEditable(),'authorized user can type after focusing the company picker');
  await search.press('ControlOrMeta+a');
  await search.pressSequentially('Test company');
  assert.equal(await search.inputValue(),'Test company');
  await panel.getByRole('button',{name:'@Test company',exact:true}).waitFor();
  await companyHeader.getByRole('tab',{name:'Main',exact:true}).click();
  assert.equal(await search.inputValue(),alpha,'leaving an unsubmitted search restores the bound company');
  if(width<=900){
    // Simulate the real visual viewport shrinking and scrolling under the mobile keyboard.
    await page.evaluate(()=>{
      const viewport=window.visualViewport;
      for(const [name,value] of [['height',280],['offsetTop',90]])
        Object.defineProperty(viewport,name,{configurable:true,value});
      viewport.dispatchEvent(new Event('resize'));viewport.dispatchEvent(new Event('scroll'));
      const panelBody=document.querySelector('#operatorCompanyPanel .operator-client-panel-content');
      const extra=document.createElement('div');extra.style.height='550px';extra.dataset.testOverflow='true';panelBody.append(extra);
    });
    assert.deepEqual(await panel.boundingBox(),{x:0,y:90,width,height:280},'panel tracks the remaining visual viewport under the keyboard');
    assert(await panelClose.evaluate(hitTarget),'close stays reachable under keyboard sizing');
    const scroll=panel.locator('.operator-client-panel-content');
    assert(await scroll.evaluate(el=>{el.scrollTop=200;return el.scrollTop>0;}),'the panel body can scroll internally without moving the header');
    await panelClose.tap();
    await page.evaluate(()=>{
      const viewport=window.visualViewport;delete viewport.height;delete viewport.offsetTop;
      viewport.dispatchEvent(new Event('resize'));
    });
    await page.waitForFunction(()=>document.activeElement?.id==='operatorCompanyPanelOpen');
  }else assert(await panel.isVisible(),'desktop details remain open beside the chat');
  if(width<=900){
    await page.waitForFunction(()=>window.savedOpen===false);
    await panel.waitFor({state:'hidden'});
    assert.deepEqual(await pane.evaluate(corners),originalPaneCorners,'closing restores the pane corners');
    assert(!await page.locator('#chatRows').evaluate(el=>!!el.closest('[inert]')),'closing makes history interactive again');
  }
  await page.locator('#operatorClientCardOpen').click();
  assert.equal(await card.locator('#operatorSharedReminderCreate').count(),0,'the avatar modal has no reminder action');
  const caseField=key=>card.locator('[data-client-case-field="'+key+'"]');
  await caseField('status').waitFor();
  assert.equal(await caseField('status').count(),1);
  assert.equal(await caseField('status').locator('span').textContent(),'Resolved');
  assert.equal(await caseField('responsible').locator('span').textContent(),'Actual & <em>case</em> worker');
  assert.equal(await caseField('responsible').locator('em').count(),0);
  assert.equal(await caseField('status').evaluate(n=>n.closest('details').querySelector('summary').textContent),'Support case');
  const acceptedBy=card.locator('[data-client-accepted-by]');
  assert.equal(await acceptedBy.locator('small').textContent(),'Accepted by');
  assert.equal(await acceptedBy.locator('span').textContent(),'Actual & <b>accepting</b> employee');
  assert.equal(await acceptedBy.locator('b').count(),0);
  assert.equal(await acceptedBy.evaluate(n=>n.closest('details').querySelector('summary').textContent),'Conversation');
  assert(!(await head.textContent()).includes('accepting'));
  assert.equal(await card.locator('[data-client-transfer-value]').textContent(),'Transfer history person');
  assert(!((await head.textContent())+(await card.textContent())).includes('Chat-only person'));
  await page.evaluate(()=>{
   window.caseTestExistingEditor=document.querySelector('#operatorConversationNameInput');
   window.caseTestExistingEditor.value='Unsaved synthetic edit';
   window.holdCaseRead=true;
   document.dispatchEvent(new Event('tenant-dialog-reauthorized'));
  });
  await page.waitForFunction(()=>window.caseWaiters.length===1);
  assert(await card.isVisible(),'same exact Information panel remains open');
  assert.equal(await card.locator('[data-client-case-field]').count(),0,'case is hidden during held reauthorization');
  await page.evaluate(()=>{
   window.cases.a=null;window.holdCaseRead=false;
   document.dispatchEvent(new Event('tenant-dialog-reauthorized'));
  });
  await page.waitForFunction(()=>window.caseGets.length===3);
  await page.evaluate(()=>window.caseWaiters.shift()());
  assert.equal(await card.locator('[data-client-case-field]').count(),0,'the old allowed request cannot repaint after a fresh deny');
  assert(await page.evaluate(()=>window.caseTestExistingEditor===document.querySelector('#operatorConversationNameInput')));
  assert.equal(await page.locator('#operatorConversationNameInput').inputValue(),'Unsaved synthetic edit');
  assert.equal(await card.locator('[data-client-transfer-value]').textContent(),'Transfer history person');
  await page.locator('#operatorCancelConversationName').click();

  const reauthorize=async(mode,channel='a')=>page.evaluate(({mode,channel})=>{
   window.mode[channel]=mode;
   document.dispatchEvent(new Event('tenant-dialog-reauthorized'));
  },{mode,channel});
  await reauthorize('offline');
  await page.waitForFunction(()=>window.bindingGets.length>=2);
  assert.equal(await name.textContent(),alpha,'transient failure retains the same authorized route label');
  await reauthorize('visible');
  await name.waitFor();
  await page.evaluate(()=>{delete window.releases.a;});
  await reauthorize('delayed');
  await page.waitForFunction(()=>Boolean(window.releases.a));
  assert.equal(await name.textContent(),alpha,'refreshing the same route keeps its last authorized label visible');
  const picker=page.locator('#operatorComposerSourceSelect');
  await picker.selectOption('1');
  await page.waitForFunction(()=>window.bindingGets.at(-1)==='b');
  await page.evaluate(()=>window.releases.a());
  assert(!await name.isVisible()&&await name.textContent()==='','masked source and delayed previous source cannot populate the header');
  assert.equal(await card.locator('[data-client-case-field]').count(),0,'old case does not cross source');
  assert(!((await head.textContent())+(await card.textContent())).includes('Other chat-only person'));
  await reauthorize('visible','b');
  await name.waitFor();
  assert.equal(await name.textContent(),beta,'visible company does not require edit permission');
  await page.locator('#operatorClientCardOpen').click();
  assert(!await acceptedBy.isVisible(),'a source without a verified acceptance must not borrow another source’s name');
  assert.equal(await acceptedBy.locator('span').textContent(),'');
  await page.locator('#operatorCancelConversationName').click();
  if(width<=900)await toggle.click();
  else assert(await panel.isVisible(),'desktop read-only details remain visible without a toggle');
  assert(!await search.isEditable(),'read access does not enable changing the bound company');
  assert.equal(await search.getAttribute('placeholder'),'Company selection requires access');
  if(width<=900){await panelClose.tap();await panel.waitFor({state:'hidden'});}
  await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-view',{detail:{view:'tasks'}})));
  if(width>900)await panel.waitFor({state:'hidden'});
  assert(!await name.isVisible()&&!(await name.textContent()),'leaving Dialogs clears company name');
  await page.evaluate(()=>{window.mode.b='denied';document.dispatchEvent(new CustomEvent('tenant-view',{detail:{view:'dialogs'}}));});
  await page.waitForFunction(()=>window.bindingGets.filter(c=>c==='b').length>=3);
  assert(!await name.isVisible());
  await reauthorize('visible','b');
  await name.waitFor();
  await picker.selectOption('0');
  await page.evaluate(()=>{window.holdCaseRead=true;});
  await page.locator('#operatorClientCardOpen').click();
  await page.waitForFunction(()=>window.caseWaiters.length===1);
  assert.equal(await card.locator('[data-client-case-field]').count(),0,'opening an earlier chat starts a fresh case check, not a cached display');
  await page.evaluate(()=>{window.holdCaseRead=false;window.caseWaiters.shift()();});
  assert.equal(await card.locator('[data-client-case-field]').count(),0,'fresh null case remains absent');
  await page.evaluate(()=>document.dispatchEvent(new Event('tenant-auth-pending')));
  assert(!await name.isVisible()&&!(await name.textContent()),'identity transition clears company name');
  await page.close();
 }
 console.log('PASS Chromium company header, separate authorized support case, transfer retained, 360/430/1440px');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
