/* Real Chromium layout and exact-route interaction; all data and responses are synthetic. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'../.support-test-deps/node_modules/playwright');
const docs=path.resolve(__dirname,'../docs');
const html=fs.readFileSync(path.join(docs,'tenant-app.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined});
 try{for(const width of [360,1440]){
  const page=await browser.newPage({viewport:{width,height:800},hasTouch:width===360,isMobile:width===360});
  page.setDefaultTimeout(5000);
  const unexpected=[];
  page.on('pageerror',error=>unexpected.push(error.message));
  await page.route('**/*',route=>{
   const target=new URL(route.request().url());
   if(route.request().isNavigationRequest())return route.fulfill({contentType:'text/html',body:html});
   if(target.origin==='https://example.test'&&target.pathname.startsWith('/assets/')&&target.pathname.endsWith('.css')){
    const css=path.join(docs,path.basename(target.pathname));if(fs.existsSync(css))return route.fulfill({contentType:'text/css',body:fs.readFileSync(css)});
   }
   return route.abort();
  });
  await page.goto('https://example.test/app?view=dialogs');
  await page.evaluate(()=>{
   document.querySelectorAll('.view').forEach(n=>n.classList.toggle('active',n.id==='view-dialogs'));
   // The isolated fixture skips app-shell bootstrap; remove its unrelated desktop sidebar at the mobile breakpoint.
   if(matchMedia('(max-width: 900px)').matches)document.querySelector('.shell > aside')?.remove();
   const member={tenant_id:'00000000-0000-0000-0000-000000000101',telegram_id:11,role:'owner',active:true,permissions:['dialogs']};
   const channel_id='00000000-0000-0000-0000-000000000201';
   const owner={member,capabilities:{modules:{dialogs:true},dialog_history_merge:true,dialog_history_merge_source_id:channel_id}};
   const operator={member:{...member,role:'operator'},capabilities:{modules:{dialogs:true}}};
   window.tenantProfileSnapshot=owner;window.fixture={mode:'owner',action:'progress',calls:[],otherWrites:[],certificate:'00000000-0000-0000-0000-000000000901',wrongCertificate:false,listQueries:[],job:{id:'fixture',state:'running',processed:2,total:3,unit:'steps'}};
   window.fixture.quickAttachments=[];
   window.tenantDialogQuickReplies={attach(_input,route,current){
     fixture.quickAttachments.push({mode:fixture.mode,lead:route.lead_id,conversation:route.conversation_id,current:current()});
   }};
   const identities=[1,2].map(n=>({provider:'connect',channel_id,conversation_id:'10000000000'+n+'@lid',lead_id:n}));
   const base=n=>({...identities[n-1],customer_name:'Fixture customer',customer_kind:'contact',display_state:'waiting',phone:'+994500000001'});
   window.fixture.switchMode=mode=>{window.fixture.mode=mode;document.dispatchEvent(new CustomEvent('tenant-profile',{detail:['owner','passive','mixed'].includes(mode)?owner:operator}));};
   window.fetch=async(target,options={})=>{
    const u=new URL(target,location.origin),key=u.pathname.replace(/^\/api\/platform\/(?:ai\/)?/,''),q=u.searchParams;
    const ok=result=>({ok:true,json:async()=>({success:true,result})});
    const body=options.body?JSON.parse(options.body):null;
    if(key==='me')return {ok:true,json:async()=>['owner','passive','mixed'].includes(window.fixture.mode)?owner:operator};
    if(key==='dialogs'){
      window.fixture.listQueries.push({mixed:q.get('mixed_history_card'),overview:q.get('owner_overview')});
      return ok({dialogs:[{...base(1),history_merge_id:window.fixture.mode==='mixed'?'fixture-mode-1':'fixture',...(window.fixture.mode==='mixed'?{history_merge_certificate:window.fixture.certificate,history_merge_display_name:'Combined fixture name',history_merge_display_phone:'+994500000002'}:{})}],channels:[],agents:[],total_count:1,unread_count:0,next_offset:null});
    }
    if(key==='dialogs/duplicates')return ok({dialogs:[{...base(1),candidate_group:1},{...base(2),candidate_group:1}],next_offset:null});
    if(key==='dialogs/duplicates/preview')return ok({display_revision:'a'.repeat(64),
      name_choices:[{lead_id:1,display_name:'Combined fixture name'}],phone_choices:[{lead_id:2,display_phone:'+994500000002'}]});
    if(key==='dialogs/history-merges'){window.fixture.calls.push(body);return ok({job:window.fixture.job});}
    if(key==='dialogs/history'){
      const n=Number(q.get('lead_id')),mode=window.fixture.mode;
      if(![1,2].includes(n)||q.get('conversation_id')!==identities[n-1].conversation_id||q.get('channel_id')!==channel_id||q.get('paired_history')!=='1')throw Error('Inexact history route');
      const mixed=mode==='mixed',controlled=(mode==='operator'||mixed)&&n===1;
      if(mixed&&q.get('owner_overview')!=='1')throw Error('Explicit owner All scope was lost');
      if(q.has('mixed_history_card'))throw Error('Mixed card opt-in unexpectedly sent to history');
      const mixedState=n===1?'accepted':'closed';
      const certificate=n===2&&window.fixture.wrongCertificate?'00000000-0000-0000-0000-000000000902':window.fixture.certificate;
      return ok({dialog:{...base(n),history_merge_id:mixed?'fixture-mode-'+n:'fixture',lifecycle_enabled:true,lifecycle:{state:mixed?mixedState:'accepted',revision:n,can_close:controlled},can_send:controlled,
        ...(mode==='operator'?{history_merge_action_routes:identities}:{}),...(mode==='passive'?{owner_overview_read_only:true}:{}),
        ...(mixed?{history_merge_certificate:certificate,history_merge_display_name:'Combined fixture name',history_merge_display_phone:'+994500000002',history_merge_read_routes:identities.map((item,index)=>({...item,state:index?'closed':'accepted',read_only:!!index})),
          ...(n===2?{owner_overview_read_only:true,archive_read_only:true}:{})}:{}),
      },messages:identities.map((route,index)=>({id:'row'+index,body:'Fixture history '+index,external_id:'ext'+index,source:'connect',message_type:'text',direction:'incoming',source_channel_id:channel_id,source_conversation_id:route.conversation_id})),next_before:null});
    }
    if(options.method==='POST')window.fixture.otherWrites.push(key);
    if(key.startsWith('dialogs/'))return ok({open:false});
    return {ok:true,json:async()=>({success:true})};
   };
  });
  for(const file of ['tenant-operator-i18n.js','tenant-operator-dialogs.js'])await page.addScriptTag({path:path.join(docs,file)});
  const hit=async locator=>{await locator.waitFor({state:'visible'});if(width===360)await locator.tap();else await locator.click();};
  const row=page.locator('[data-open-operator]');
  await row.waitFor().catch(async error=>{console.error(await page.evaluate(()=>({notice:document.getElementById("operatorNotice")?.textContent,member:window.tenantOperator?.member,rows:document.getElementById("operatorRows")?.children.length,body:document.body.className,loading:document.documentElement.className})),unexpected);throw error;});
  assert.equal(await row.count(),1,'one visible card for the mocked paired projection');
  await hit(page.locator('#operatorToolsMenu > summary'));
  await hit(page.locator('#operatorFindDuplicates'));
  const submit=page.locator('[data-dupe-form] button[type="submit"]');
  await hit(submit);
  const chooseName=page.locator('[data-dupe-display-field="name"]'),choosePhone=page.locator('[data-dupe-display-field="phone"]');
  await chooseName.waitFor({state:'visible'});
  assert(await page.locator('[data-dupe-merge-start]').isDisabled());
  await chooseName.selectOption('0');await choosePhone.selectOption('0');
  await hit(page.locator('[data-dupe-merge-start]'));
  const modal=page.locator('#operatorHistoryMerge');
  await modal.waitFor({state:'visible'});
  await page.waitForFunction(()=>document.querySelector('#operatorHistoryMerge progress')?.value===66);
  const geometry=await modal.evaluate(node=>{
    const b=node.getBoundingClientRect(),track=node.querySelector('progress').getBoundingClientRect();
    return {centerX:b.left+b.width/2,centerY:b.top+b.height/2,width:b.width,height:b.height,track:track.height};
  });
  assert(Math.abs(geometry.centerX-width/2)<=2&&Math.abs(geometry.centerY-400)<=2,JSON.stringify({width,geometry}));
  assert(geometry.width<=440&&geometry.width<=width-16&&geometry.height<320);
  assert(geometry.track>0&&geometry.track<=7,'progress bar is thin');
  await hit(page.locator('[data-dupe-merge-close]'));
  assert.equal(await modal.count(),0);
  const calls=await page.evaluate(()=>fixture.calls.map(x=>x.operation));
  assert.equal(calls[0],'create');
  assert.deepEqual(await page.evaluate(()=>fixture.calls[0].display),{revision:'a'.repeat(64),name_lead_id:1,phone_lead_id:2});
  assert.equal(calls.filter(action=>action==='create').length,1);
  assert(!calls.includes('cancel'),'tapping X does not cancel');
  await hit(page.locator('[data-dupe-close]'));
  for(const mode of ['passive','different','operator']){
    await page.evaluate(mode=>{window.tenantOperator.closeDialog(true,true);history.replaceState({},'');fixture.switchMode(mode);},mode);
    await hit(row);
    await page.locator('#chatRows').waitFor();
    assert.match(await page.locator('#chatRows').textContent(),/Fixture history 0.*Fixture history 1/s);
    const picker=page.locator('[data-merge-action-route]');
    const attachments=()=>page.evaluate(mode=>fixture.quickAttachments.filter(item=>item.mode===mode),mode);
    if(mode==='operator'){
      const attached=await attachments();
      assert.ok(attached.length>0&&attached.every(item=>item.lead===1&&item.conversation==='100000000001@lid'&&item.current));
    }else assert.deepEqual(await attachments(),[],'passive path must not mount quick replies');
    if(mode!=='operator'){
      assert.equal(await picker.count(),0);
      assert(await page.locator('#sendChat').isDisabled());
    }else{
      await picker.waitFor({state:'visible'});
      assert.equal(await picker.locator('option').count(),2);
      assert(await page.locator('[data-operator-lifecycle="close"]').isVisible());
      await picker.selectOption('1');
      await page.waitForFunction(()=>window.tenantOperator.currentDialog?.lead_id===2);
      assert.equal(await picker.inputValue(),'1');
      assert.equal(await page.locator('[data-operator-lifecycle="close"]').count(),0);
      assert(await page.locator('#sendChat').isDisabled());
      assert((await attachments()).every(item=>item.lead===1),'read-only alternate does not mount quick replies');
    }
    await page.evaluate(()=>window.tenantOperator.closeDialog(true,true));
  }
  await page.evaluate(()=>{window.tenantOperator.closeDialog(true,true);history.replaceState({},'');fixture.switchMode('mixed');
    const all=document.getElementById('operatorLifecycle');all.value='all';all.dispatchEvent(new Event('change',{bubbles:true}));
  });
  await page.waitForFunction(()=>fixture.listQueries.some(q=>q.mixed==='1'&&q.overview==='1'));
  await hit(row);
  const mixedPicker=page.locator('[data-merge-read-route]');
  await mixedPicker.waitFor({state:'visible'});
  assert.equal(await row.locator('.operator-row-title').textContent(),'Combined fixture name');
  assert.equal(await row.locator('.operator-row-sub').textContent(),'+994500000002');
  assert.equal(await page.locator('#operatorDialogTitleText').textContent(),'Combined fixture name');
  assert.equal(await page.locator('[data-header-phone]').textContent(),'+994500000002');
  assert.equal(await page.evaluate(()=>tenantOperator.currentDialog.phone),'+994500000001','display projection must not mutate normal route identity');
  await page.locator('#operatorClientCardOpen').click();
  assert.equal(await page.locator('[data-panel-name]').textContent(),'Combined fixture name');
  assert.equal(await page.locator('[data-panel-phone]').textContent(),'+994500000002');
  const editName=page.locator('#operatorConversationNameInput');
  if(await editName.count())assert.notEqual(await editName.inputValue(),'Combined fixture name','physical route editor keeps its own name');
  await page.locator('#operatorCancelConversationName').click();
  assert.equal(await mixedPicker.locator('option').count(),2);
  await page.evaluate(()=>window.tenantI18n.setLanguage('en'));
  await mixedPicker.waitFor({state:'visible'});
  assert.match(await mixedPicker.locator('option').nth(0).textContent(),/In progress/);
  assert.match(await mixedPicker.locator('option').nth(1).textContent(),/Archive.*Read only/);
  assert.equal(await page.locator('#operatorComposerSource [data-merge-read-route]').count(),0,'navigation is outside the composer');
  assert.equal(await page.locator('[data-merge-action-route]').count(),0,'mixed card is not an action route list');
  const before=await page.evaluate(()=>fixture.otherWrites.length);
  await page.evaluate(()=>{fixture.wrongCertificate=true;});
  await mixedPicker.selectOption('1');
  await page.waitForFunction(()=>document.querySelector('#operatorMergedReadRoutes [role="status"]')?.textContent||document.querySelector('[data-merge-read-route]')?.value==='0');
  assert.equal(await page.evaluate(()=>window.tenantOperator.currentDialog?.lead_id),1,'different certificate cannot switch physical route');
  await page.evaluate(()=>{fixture.wrongCertificate=false;});
  await mixedPicker.selectOption('1');
  await page.waitForFunction(()=>window.tenantOperator.currentDialog?.lead_id===2);
  assert.equal(await mixedPicker.inputValue(),'1');
  assert.equal(await mixedPicker.isVisible(),true);
  await page.evaluate(()=>window.tenantOperator.refresh());
  assert.equal(await row.getAttribute('aria-selected'),'true','one card stays selected in the other mode');
  if(width===1440){await hit(row);await page.waitForFunction(()=>document.getElementById('operatorConversationContent')?.getAttribute('aria-busy')==='false');
   assert.equal(await page.evaluate(()=>window.tenantOperator.currentDialog?.lead_id),2,'clicking the same folded card preserves the selected archive route');}
  assert.equal(await page.locator('[data-operator-lifecycle="close"]').count(),0);
  assert(await page.locator('#sendChat').isDisabled());
  assert.equal(await page.evaluate(()=>fixture.otherWrites.length),before,'history selection does not mark, send or mutate');
  assert(!(await page.evaluate(()=>fixture.quickAttachments.some(item=>item.mode==='mixed'&&item.lead===2))),'archived lens never mounts quick replies');
  assert.deepEqual(unexpected,[]);
  await page.close();
 }
 }finally{await browser.close();}
 console.log('PASS: Chromium 360/1440 real progress geometry/touch, one card, mixed archive navigation, exact actions, and passive/different-owner denial.');
})().catch(error=>{console.error(error);process.exitCode=1;});
