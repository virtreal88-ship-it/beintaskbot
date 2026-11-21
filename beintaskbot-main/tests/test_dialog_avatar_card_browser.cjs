const {revealAccountControl,selectAppLanguage}=require('./account_controls_browser.cjs');
/* Unchanged application HTML and real assets; every API/provider request mocked. */
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..');
const tenant='00000000-0000-0000-0000-000000000101',channel='00000000-0000-0000-0000-000000000201';
const dialog={provider:'connect',phone:'+994501234567',lead_id:41,channel_id:channel,conversation_id:'994501234567@s.whatsapp.net',customer_kind:'contact',customer_name:'Customer',channel_name:'WhatsApp',display_state:'human',state:'human',ai_availability:'stopped',available_actions:[],can_send:true,controls_allowed:true,agent_status:'manual',agent_mode:'manual',agent_id:'00000000-0000-0000-0000-000000000301',agent_name:'Assigned Helper'};
const technicalName='405e6e7d-3911-4ac6-b5a0-9bdad7ddcd50.pdf',realName='Inventory report.pdf';
const pdfs=[
  {id:'original-pdf',external_id:'external-1',body:realName,media_filename:realName},
  {id:'technical-pdf',external_id:'external-2',body:technicalName,media_filename:technicalName},
  {id:'comment-pdf',external_id:'external-3',body:'Check the totals',media_filename:'',media_mime_type:'application/pdf'}
].map((file,index)=>({...file,source:'connect',source_lead_id:41,source_channel_id:channel,direction:'outgoing',
  message_type:'document',media_version:String(index+1).repeat(64),happened_at:'2026-10-21T10:00:00Z'}));
const profile={success:true,member:{tenant_id:tenant,telegram_id:33,role:'worker',permissions:['dialogs','support'],active:true},capabilities:{modules:{dialogs:true,support:true}}};
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':chromium.executablePath()),args:['--no-sandbox']});
 try{
  for(const [width,height,language] of [[320,760,'ru'],[360,800,'ru'],[390,844,'az'],[430,900,'az'],[1280,900,'en'],[390,420,'ru']]){
   const page=await browser.newPage({viewport:{width,height}}),errors=[],calls=[];let marker=true,nameFailure=false,nameGate=null,releaseName;const savedNames=new Map();
   page.on('pageerror',error=>errors.push(error.stack));page.on('dialog',()=>assert.fail('Unexpected native dialog'));
   // Production stores question templates in this tenant/employee scoped key.
   await page.addInitScript(({tenant})=>localStorage.setItem(`tenant.operator.copilot-question-templates.${tenant}:33`,JSON.stringify(Array.from({length:12},(_,i)=>({id:'template-'+i,name:'Saved question '+i,prompt:'Saved question'})))),{tenant});
   await page.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url()),name=url.pathname;calls.push({path:name,method:request.method(),body:request.postData()});
    if(name==='/app')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8')});
    if(name.startsWith('/assets/')){const file=path.join(root,'docs',path.basename(name));return fs.existsSync(file)?route.fulfill({path:file}):route.fulfill({status:404,body:''});}
    if(name.endsWith('/crm/chat/attachment')){
      const filename=({'external-1':realName,'external-2':technicalName})[url.searchParams.get('message_id')]||'attachment.pdf';
      return route.fulfill({contentType:'application/pdf',headers:{'Content-Disposition':'inline; filename="'+filename+'"'},body:'%PDF-1.4\n%%EOF'});
    }
    if(name==='/api/platform/me')return route.fulfill({json:profile});
    let result={tickets:[],agents:[],members:[],channels:[],items:[]};
    if(name.endsWith('/dialogs/display-name')){if(nameGate)await nameGate;if(nameFailure)return route.fulfill({status:500,json:{error:'Synthetic name save failure'}});const body=JSON.parse(request.postData());savedNames.set(body.conversation_id,body.display_name);result={};}
    else if(name.endsWith('/dialogs/history')){const conversation=url.searchParams.get('conversation_id')||dialog.conversation_id,original=conversation===dialog.conversation_id?'Very long customer name / Очень длинное имя клиента':'Other customer';result={dialog:{...dialog,lead_id:conversation===dialog.conversation_id?41:42,conversation_id:conversation,customer_name:savedNames.get(conversation)||original,manual_display_name:savedNames.get(conversation)||'',agent_name:'Assigned agent / Назначенный агент с очень длинным названием / Uzun agent adı'},messages:[{id:'text',external_id:'e1',direction:'incoming',message_type:'text',body:'Synthetic conversation',happened_at:'2026-10-21T10:00:00Z'},...pdfs],next_before:null};}
    else if(name.endsWith('/dialogs'))result={dialogs:[{...dialog,last_message_id:'m1',last_message_direction:'incoming',last_message_text:'Customer question',last_message_at:'2026-10-21T10:00:00Z'}],channels:[{id:'connect:'+channel,name:'WhatsApp'}],agents:[],next_offset:null};
    else if(name.endsWith('/developer-marker')){if(request.method()==='POST'&&typeof JSON.parse(request.postData()).enabled==='boolean')marker=JSON.parse(request.postData()).enabled;result={enabled:marker};}
    else if(name.endsWith('/replies'))result={replies:[],consultations:[]};
    return route.fulfill({json:{success:true,result}});
   });
   try{
    await page.goto('https://crm.test/app?view=dialogs');await selectAppLanguage(page,language);await page.locator('.operator-row').first().click();await page.locator('#dialogDeveloperMarker').waitFor({state:'attached'});await page.waitForFunction(()=>document.querySelector('#dialogDeveloperMarker')?.disabled===false);
    const avatar=page.locator('#operatorClientCardOpen'),card=page.locator('#operatorClientCard'),field=page.locator('#operatorConversationNameInput'),menu=page.locator('#operatorMoreActions');
    assert.equal(await page.locator('#operatorEditConversationName').count(),0);assert.equal(await card.isVisible(),false);assert.equal(await menu.locator('.operator-menu-agent').count(),0,'agent details have one home in the avatar card');
    assert.equal(await page.locator('#operatorClientDeveloperHost #dialogDeveloperMarker').count(),1);assert.equal(await page.locator('#operatorClientDeveloperStatus #dialogDeveloperBadge').count(),1);assert.equal(await page.locator('#operatorDialogTitleSlot #dialogDeveloperBadge').count(),0);
    assert.equal(await page.locator('#operatorClientDeveloperHost').evaluate(node=>getComputedStyle(node).marginTop),'4px');
    const bell=page.locator('#dialogConsultationBell');assert.equal(await bell.count(),1,'global bell remains in place');assert.equal(await page.locator('#dialogConsultationMobileBell,#operatorDialogNotificationsHost').count(),0,'no duplicate global bell or new scoped bell is inserted in the dialogue');
    // Measure settled layout, not the 170ms conversation entrance translation.
    await page.locator('.operator-conversation-pane').evaluate(async node=>{await Promise.all(node.getAnimations({subtree:true}).filter(a=>Number.isFinite(a.effect?.getComputedTiming().endTime)).map(a=>a.finished.catch(()=>{})))});
    const geometry=await avatar.boundingBox();assert.ok(geometry.width>=43.95&&geometry.height>=43.95,JSON.stringify(geometry));assert.equal(await page.locator('[data-operator-create-ticket]').count(),1,'task creation stays available');
    assert.equal(await page.locator('#operatorConversationHeader .operator-channel-label').count(),0);
    const number=avatar.locator('.operator-client-phone');
    assert.match(await number.innerText(),/994501234567/);
    const avatarPicture=await avatar.locator('.operator-header-avatar').boundingBox();
    const phoneBox=await number.boundingBox(),titleBox=await page.locator('#operatorDialogTitleText').boundingBox();
    assert(phoneBox.y>=avatarPicture.y+avatarPicture.height+2,'the phone has a gap below the avatar');
    assert(Math.abs((titleBox.y+titleBox.height/2)-(avatarPicture.y+avatarPicture.height/2))<=2,JSON.stringify({width, titleBox, avatarPicture, titleLayout: await page.locator('#operatorDialogTitleSlot').evaluate(e=>({height:e.offsetHeight, minHeight:getComputedStyle(e).minHeight, alignItems:getComputedStyle(e).alignItems}))}));
    if(language==='az')assert.match(await page.locator('[data-operator-create-ticket]').innerText(),/\+\s*sorğu/);
    const pdfLabel={ru:'Документ PDF',az:'PDF sənədi',en:'PDF document'}[language];
    const cards=page.locator('#chatRows article').filter({has:page.locator('[data-media-title]')});
    assert.equal(await cards.count(),3);
    assert.equal((await cards.nth(0).innerText()).split(realName).length-1,1);
    assert.equal(await cards.nth(1).locator('[data-media-title]').innerText(),pdfLabel);
    assert(!((await cards.nth(1).innerText()).includes(technicalName)));
    assert.equal(await cards.nth(2).locator('.operator-message-caption').innerText(),'Check the totals');
    assert.equal(await cards.nth(2).locator('[data-media-title]').innerText(),pdfLabel);
    if(width===1280){
      for(const [index,title,download] of [[0,realName,realName],[1,pdfLabel,technicalName],[2,pdfLabel,'attachment.pdf']]){
        const media=cards.nth(index);
        await media.locator('button[data-media-open]').click();
        const openFile=media.locator('a[data-media-preview=file]');
        await openFile.waitFor();
        assert.match(await openFile.getAttribute('href'),/^blob:/);
        assert.equal(await openFile.getAttribute('download'),download);
        assert.equal((await media.innerText()).split(title).length-1,1);
        assert(!((await media.innerText()).includes(technicalName)));
      }
    }
    const headerControls=['#operatorClientCardOpen','[data-operator-create-ticket]','#operatorMoreActions summary'];
    const headerGeometry=await page.evaluate(selectors=>selectors.map(selector=>{const node=document.querySelector(selector),box=node.getBoundingClientRect(),hit=document.elementFromPoint(box.x+box.width/2,box.y+box.height/2);return {selector,x:box.x,y:box.y,right:box.right,bottom:box.bottom,clickable:hit===node||node.contains(hit)};}),headerControls);
    for(const box of headerGeometry){assert.ok(box.x>=0&&box.right<=width&&box.y>=0&&box.bottom<=height,'header control fits viewport '+JSON.stringify(box));assert.equal(box.clickable,true,'header control is not covered '+JSON.stringify(box));}
    for(let a=0;a<headerGeometry.length;a++)for(let b=a+1;b<headerGeometry.length;b++){const one=headerGeometry[a],two=headerGeometry[b];assert.ok(one.right<=two.x||two.right<=one.x||one.bottom<=two.y||two.bottom<=one.y,'header controls must not overlap '+JSON.stringify([one,two]));}
    for(const selector of headerControls)await page.locator(selector).click({trial:true});
    assert.equal(await page.locator('#dialogDeveloperAsk').isVisible(),false);await page.locator('#operatorMoreActions summary').click();assert.equal(await menu.getAttribute('open'),'');assert.equal(await page.locator('#dialogDeveloperAsk').isVisible(),true);await page.locator('#dialogDeveloperAsk').click({trial:true});await page.locator('#operatorMoreActions summary').click();
    const header=page.locator('#operatorConversationHeader'),headerHeight=(await header.boundingBox()).height;
    await avatar.focus();await page.keyboard.press('Enter');await card.waitFor();assert.equal((await header.boundingBox()).height,headerHeight);assert.equal(await avatar.getAttribute('aria-expanded'),'true');assert.equal(await page.evaluate(()=>document.activeElement.id),'operatorCancelConversationName','opening the panel never focuses the name field or opens the keyboard');assert.match(await card.locator('.operator-agent-label').innerText(),/Assigned agent/);assert.equal(await card.locator('select').count(),0,'moving the agent name never changes assignment');
    assert.equal(await card.locator('.operator-client-panel-block').count(),2,'the display-name block stays available even when company binding is unavailable');
    assert.equal(await field.evaluate(n=>n.closest('details')===document.querySelector('#operatorClientCard .operator-client-panel-block')),true);
    assert.equal(await card.locator('[data-panel-phone],#operatorBeinContext').count(),0);
    assert.equal(calls.some(c=>c.method==='POST'&&(c.path.endsWith('/display-name')||(c.path.endsWith('/developer-marker')&&typeof JSON.parse(c.body).enabled==='boolean'))),false,'opening card never saves or toggles anything');
    await field.fill('Unsaved / Несохранённое имя');await field.evaluate(n=>n.setSelectionRange(3,9));await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-language')));await card.waitFor();assert.equal(await field.inputValue(),'Unsaved / Несохранённое имя');assert.equal(await field.evaluate(n=>n.selectionStart),3,'same-source background rendering preserves typed name and selection');
    const saveMark=card.locator('[data-client-name-saved]');
    const blurName=()=>card.locator('header h2').click();
    assert.equal(await card.locator('#operatorConversationName button').count(),0);
    assert((await field.boundingBox()).height<=34.5,'name input is compact');
    assert.equal(calls.filter(c=>c.path.endsWith('/dialogs/display-name')).length,0,'a redraw does not autosave the focused field');
    await field.fill('Very long customer name / Очень длинное имя клиента');await blurName();
    assert.equal(calls.filter(c=>c.path.endsWith('/dialogs/display-name')).length,0,'an unchanged name is not posted');
    await page.keyboard.press('Escape');assert.equal(await card.isVisible(),false);assert.equal(await avatar.evaluate(n=>n===document.activeElement),true);assert.equal(await page.locator('#operatorDialogBody').count(),1,'Escape closes the panel first');
    await avatar.click();nameFailure=true;await field.fill('Keep this value');await blurName();await card.locator('[data-client-card-error]').filter({hasText:'Synthetic name save failure'}).waitFor();assert.equal(await field.inputValue(),'Keep this value');assert(!await saveMark.isVisible());nameFailure=false;
    await field.fill('Saved customer');await blurName();await page.waitForFunction(()=>document.querySelector('#operatorDialogTitleText')?.textContent==='Saved customer');await saveMark.waitFor();assert.equal(await card.isVisible(),true);const savedCall=calls.filter(c=>c.path.endsWith('/dialogs/display-name')).at(-1),body=JSON.parse(savedCall.body);assert.equal(body.kind,'conversation');assert.equal(body.channel_id,channel);assert.equal(body.conversation_id,dialog.conversation_id);assert.equal(body.expected_tenant_id,tenant);
    await page.locator('#dialogDeveloperMarker').click();await page.waitForFunction(()=>!document.querySelector('#dialogDeveloperBadge'));assert.equal(marker,false,'existing marker handler receives the same explicit toggle');await page.locator('#operatorCancelConversationName').click();
    nameGate=new Promise(resolve=>{releaseName=resolve;});await avatar.click();await field.fill('Saved after same-source refresh');await blurName();await page.waitForFunction(()=>document.querySelector('#operatorConversationNameInput')?.readOnly);await page.evaluate(()=>window.tenantOperator.openDialog(window.tenantOperator.currentDialog,true));releaseName();nameGate=null;await saveMark.waitFor();assert.equal(await page.locator('#operatorDialogTitleText').innerText(),'Saved after same-source refresh','late success refreshes the connected current source');
    await page.locator('#operatorCancelConversationName').click();
    await revealAccountControl(page,'#dialogConsultationBell');const beforeBell=calls.filter(c=>c.method==='POST').length;await bell.click();await page.locator('#dialogConsultationMenu').waitFor();assert.equal(calls.filter(c=>c.method==='POST').length,beforeBell,'opening the retained global bell does not mark read automatically');await page.locator('#notificationClose').click();if(!await avatar.isVisible())await page.locator('.operator-row').first().click();
    await avatar.click();await field.fill('Pending name stays with its source');nameGate=new Promise(resolve=>{releaseName=resolve;});const beforePending=calls.filter(c=>c.path.endsWith('/dialogs/display-name')).length;await blurName();await page.waitForFunction(()=>document.querySelector('#operatorConversationNameInput')?.readOnly);await page.locator('#operatorConversationName').evaluate(n=>n.requestSubmit());assert.equal(calls.filter(c=>c.path.endsWith('/dialogs/display-name')).length,beforePending+1,'pending guard rejects double submission');
    await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-language')));assert.equal(await card.isVisible(),true);assert.equal(await field.inputValue(),'Pending name stays with its source');assert.equal(await field.evaluate(n=>n.readOnly),true,'background rendering retains pending save');
    await page.evaluate(()=>window.tenantOperator.openDialog({...window.tenantOperator.currentDialog,lead_id:42,conversation_id:'other-customer'},true));assert.equal(await card.isVisible(),false);await avatar.click();assert.equal(await field.inputValue(),'Other customer');const response=page.waitForResponse(r=>r.url().includes('/dialogs/display-name')&&r.request().method()==='POST');releaseName();await response;nameGate=null;await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));assert.equal(await field.inputValue(),'Other customer','late name response never edits another customer');assert(!await saveMark.isVisible(),'a late save never marks another dialogue');assert.equal(await page.locator('#operatorDialogTitleText').innerText(),'Other customer');await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.querySelector('#operatorClientCard').contains(document.activeElement)),true);
    if(width===390){await page.setViewportSize({width,height:420});await page.waitForTimeout(50);const compact=await card.boundingBox();assert.ok(compact.y>=0&&compact.y+compact.height<=420,'card fits a keyboard-sized viewport');assert.equal(await field.inputValue(),'Other customer');await page.setViewportSize({width,height});}
    const box=await card.boundingBox();assert.ok(box.x>=0&&box.x+box.width<=width+1&&box.y>=0&&box.y+box.height<=height+1);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.screenshot({path:'/tmp/dialog-avatar-card-'+width+'-'+language+'.png'});
    await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-auth-invalid')));assert.equal(await page.locator('#operatorClientCard[open]').count(),0,'logout closes the card');
    assert.deepEqual(errors,[]);assert.equal(calls.some(c=>c.method==='POST'&&/copilot|dispatch|chat\/send|audio-transcription/.test(c.path)),false);console.log('PASS avatar card '+width+' '+language+': actual CSS, avatar/keyboard, scoped draft, cancel/error/save, developer relocation, global bell only/no read mutation, scope/logout');
   }catch(error){await page.screenshot({path:'/tmp/voice-card-failure-'+width+'-'+language+'.png'});throw error;}finally{await page.close();}
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
