const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('jsdom');

(async()=>{
  const dom=new JSDOM(`<body><header><button class="app-avatar-button" type="button"></button><button data-app-language>Language</button></header><div id="view-dialogs">
      <div id="operatorWorkspace"><div class="operator-list-pane"><div class="operator-filters"></div></div></div>
      <div id="operatorRows"><button data-open-operator="0"><span class="operator-row-indicators"></span></button></div>
      <dialog id="operatorCompanyPanel" open><div id="operatorClientSharedReminderHost" hidden></div></dialog>
      <details id="operatorMoreActions"><div class="operator-action-menu"></div></details>
    </div></body>`,{runScripts:'outside-only',pretendToBeVisual:true,url:'https://crm.test/app'});
  const w=dom.window,d=w.document,$=x=>d.querySelector(x);
  try{
    w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
    const account={tenant_id:'tenant',telegram_id:33,role:'operator',active:true};
    const actual={provider:'connect',channel_id:'support',conversation_id:'chat',lead_id:41,
      shared_reminder:{can_schedule:true,id:'own'}};
    const personal={...actual,channel_id:'personal',shared_reminder:undefined};
    let selected=actual,posts=[],openedChat=false,navigated=0,feedFailure=0,holdFeed=false,releaseFeed,rows=[
      {id:'own',title:'Actual dialogue',due_at:new Date().toISOString(),due:true,dialog:actual},
      {id:'foreign',title:'Other closed dialogue',due_at:new Date().toISOString(),due:true}
    ];
    w.tenantI18n={language:'en'};
    w.tenantProfileSnapshot={capabilities:{shared_dialog_reminders:true},member:account};
    w.tenantOperator={member:account,get currentDialog(){return selected;},hasRight:()=>true,
      openDialog:()=>{openedChat=true;return Promise.resolve(true);}};
    const switchView=view=>{
      $('#view-dialogs').classList.toggle('active',view==='dialogs');
      d.dispatchEvent(new w.CustomEvent('tenant-view',{detail:{view}}));
    };
    w.tenantAppNavigate=view=>{navigated++;switchView(view);};
    w.fetch=async(url,options)=>{
      const path=new URL(url,w.location).pathname;
      if(path==='/api/platform/notifications')return {ok:true,json:async()=>({success:true,
        result:{notifications:[{id:'regular',title:'Regular notification',body:'Body',created_at:new Date().toISOString()}],unread_count:3}})};
      if(path.endsWith('/replies'))return {ok:true,json:async()=>({success:true,result:{replies:[],consultations:[]}})};
      assert.match(url,/^\/api\/platform\/ai\/dialogs\/shared-reminders/);
      let result={reminders:rows,has_more:false};
      if(options.method==='GET'){
        if(holdFeed)await new Promise(resolve=>{releaseFeed=resolve;});
        if(feedFailure)return {ok:false,status:feedFailure,json:async()=>({success:false,error:'Synthetic availability issue'})};
      }
      if(options.method==='POST'){
        const body=JSON.parse(options.body);
        posts.push(body);
        assert.equal(body.expected_tenant_id,'tenant');assert.equal(body.expected_user_id,'33');
        assert.equal(body.action,'open');
        rows=rows.filter(r=>r.id!==body.id);
        result={title:'Opened',due_at:new Date().toISOString(),note:'<img onerror=alert(1)>'};
      }
      return {ok:true,json:async()=>({success:true,result})};
    };
    const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
    w.eval(fs.readFileSync('docs/tenant-shared-dialog-reminders.js','utf8'));
    w.eval(fs.readFileSync('docs/tenant-dialog-consultations.js','utf8'));
    await tick();
    const bell=$('#dialogConsultationBell'),entry=()=>$('#dialogConsultationMenu [data-notification-focus=shared-reminders]'),
      badge=()=>$('#dialogConsultationBell .notification-reminder-badge');
    assert.equal(bell.nextElementSibling?.classList.contains('app-avatar-button'),true,'bell stays in the app header beside the avatar');
    w.tenantSharedReminders.markRows([[actual]]);
    assert.equal($('#operatorSharedRemindersOpen'),null,'no duplicate dialogue toolbar');
    assert.equal($('#operatorClientSharedReminderHost').hidden,true,'cannot schedule on another page');
    assert.equal(badge().textContent,'2','due reminders are visible on another page');
    assert.equal($('#dialogConsultationBell .notification-unread-badge:not(.notification-reminder-badge)').textContent,'3');
    assert.match(bell.getAttribute('aria-label'),/Unread notifications 3; Shared reminders \(2\)/);
    assert($('#operatorRows [data-shared-reminder]'));
    assert.equal(posts.length,0,'poll and normal render do not consume marks');
    bell.click();await tick();
    assert.match($('#dialogConsultationMenu').textContent,/Regular notification/);
    assert.equal(entry().textContent,'Shared reminders (2)');entry().click();
    assert($('#dialogConsultationMenu').hidden);
    [...d.querySelectorAll('#sharedDialogReminderModal article button')].find(b=>b.textContent==='Open reminder').click();
    await tick();
    assert.equal(posts[0].id,'foreign');
    assert.equal(openedChat,false,'a shared card does not open a restricted history');
    assert.equal(navigated,0,'a shared card does not navigate to restricted history');
    assert.match($('#sharedDialogReminderModal').textContent,/Marker cleared for everyone/);
    assert.equal($('#sharedDialogReminderModal img'),null,'team note is rendered as text');
    assert.equal(badge().textContent,'1');
    [...d.querySelectorAll('#sharedDialogReminderModal header button')][0].click();
    assert.equal(d.activeElement,bell,'closing the reminders returns focus to the bell');
    switchView('dialogs');await tick();
    const create=$('#operatorSharedReminderCreate');
    assert.equal(create.hidden,false);
    assert.equal(create.textContent,'Create reminder');
    assert.equal(create.firstElementChild?.matches('svg[aria-hidden="true"][focusable="false"]'),true);
    assert.equal(create.lastElementChild?.textContent,'Create reminder','the decorative plus precedes the localized action label');
    assert.equal($('#operatorMoreActions #operatorSharedReminderCreate'),null,'creation is not in the ellipsis');
    create.click();assert($('#sharedDialogReminderModal form'),'company control opens the existing schedule form');
    $('#sharedDialogReminderModal header button').click();
    await w.tenantSharedReminders.dialogOpened(actual);
    assert.equal(posts[1].id,'own');
    assert.equal($('#operatorRows [data-shared-reminder]'),null);
    rows.push({id:'another',title:'Authorized dialogue',due_at:new Date().toISOString(),due:true,dialog:actual});
    switchView('home');d.dispatchEvent(new w.CustomEvent('tenant-employee-status-changed'));await tick();
    bell.click();await tick();entry().click();
    [...d.querySelectorAll('#sharedDialogReminderModal article button')].find(b=>b.textContent==='Open dialogue').click();
    await tick();
    assert.equal(navigated,1,'a normal authorized route can navigate from the global bell');
    assert.equal(openedChat,true);assert.equal(posts.at(-1).id,'another');
    $('#sharedDialogReminderModal header button').click();
    selected=personal;
    d.dispatchEvent(new w.CustomEvent('tenant-chat-attachments-ready'));
    assert.equal($('#operatorSharedReminderCreate').hidden,true);
    feedFailure=503;
    switchView('home');await tick();
    assert.equal(badge(),null,'unknown feed does not display a due count');
    bell.click();await tick();assert.equal(entry().textContent,'Shared reminders','temporary load failure keeps the entry');
    entry().click();await tick();
    const reminderModal=()=>$('#sharedDialogReminderModal');
    assert.match(reminderModal().textContent,/Could not load reminders/);
    assert.equal(reminderModal().querySelector('article'),null,'unknown feed does not expose a previous result');
    feedFailure=0;holdFeed=true;
    [...reminderModal().querySelectorAll('button')].find(b=>b.textContent==='Retry').click();
    assert.match(reminderModal().textContent,/Loading/);assert.equal(badge(),null);
    holdFeed=false;releaseFeed();await tick();
    assert.match(reminderModal().textContent,/No shared reminders/,'successful empty result is distinct from failure');
    reminderModal().querySelector('header button').click();
    holdFeed=true;switchView('dialogs');d.dispatchEvent(new w.CustomEvent('tenant-employee-status-changed'));await tick();
    bell.click();await tick();assert(entry(),'loading keeps the eligible bell entry');
    entry().click();assert.match(reminderModal().textContent,/Loading/);
    holdFeed=false;feedFailure=403;releaseFeed();await tick();
    assert.equal(reminderModal(),null,'definitive authorization error closes reminder list');
    bell.click();await tick();assert.equal(entry(),null,'definitive authorization error hides only the reminder entry');
    assert.match($('#dialogConsultationMenu').textContent,/Regular notification/);
    bell.click();
    d.dispatchEvent(new w.CustomEvent('tenant-auth-pending'));
    assert.equal(badge(),null);
    bell.click();assert.equal(entry(),null,'the globally visible bell does not expose shared reminders after auth reset');
    await tick();
    assert.equal($('#sharedDialogReminderModal'),null);
    await checkRealCompanyPlacement(390);await checkRealCompanyPlacement(1440);
    await checkRealCompanyPlacement(390,'owner');await checkRealCompanyPlacement(1440,'owner');
    console.log('PASS shared reminder bell, actual Company panel layout, restricted closed card, and auth reset');
  }finally{w.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});


async function checkRealCompanyPlacement(width,role='operator'){
  const read=path=>fs.readFileSync(path,'utf8');
  const dom=new JSDOM(read('docs/tenant-app.html'),{runScripts:'outside-only',pretendToBeVisual:true,
    url:'https://crm.test/app?view=dialogs'});
  const w=dom.window,d=w.document,$=selector=>d.querySelector(selector),tick=()=>new Promise(resolve=>setTimeout(resolve,0));
  try{
    w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
    w.HTMLDialogElement.prototype.close=function(){this.open=false;};
    w.matchMedia=query=>({matches:query.includes('900px')&&width<=900,addListener(){},removeListener(){}});
    w.alert=()=>{};
    const member={tenant_id:'synthetic',telegram_id:33,role,active:true};
    const reminders={shared_dialog_reminder_creation:true,...(role==='operator'?{shared_dialog_reminders:true}:{})};
    const profile={member,capabilities:{modules:{dialogs:true},dialog_company_binding:true,...reminders}};
    const base={provider:'connect',lead_id:41,channel_id:'eligible-a',conversation_id:'same-client',can_send:false,
      lifecycle_enabled:true,lifecycle:{state:'closed',revision:1},archive_read_only:role==='owner',shared_reminder:{can_schedule:true}};
    const bound={...base,company_binding:{company_id:'company-a',company_name:'Linked company',can_manage:false}};
    const unbound={...base,lead_id:42,channel_id:'eligible-b',company_binding:{company_id:null,can_manage:false}};
    const denied={...base,lead_id:43,channel_id:'denied',shared_reminder:{can_schedule:false}};
    const routes=[bound,unbound,denied],reminderPosts=[],companyCalls=[];
    let feedReads=0;
    const selector='#operatorSharedReminderCreate';
    w.tenantProfileSnapshot=profile;
    let savedOpen=false;
    w.fetch=async(url,opt={})=>{
      const u=new URL(url,w.location),path=u.pathname,body=opt.body&&JSON.parse(opt.body);
      const ok=(result)=>({ok:true,status:200,json:async()=>({success:true,result})});
      if(path.endsWith('/dialogs/history')){
        const route=routes.find(r=>r.channel_id===u.searchParams.get('channel_id'));
        assert(route);assert.equal(u.searchParams.get('conversation_id'),route.conversation_id);
        if(u.searchParams.has('support_case_only'))return ok({dialog:{...route,support_case:null}});
        return ok({dialog:route,messages:[]});
      }
      if(path.endsWith('/dialogs/company-panel/preferences')){
        if(body)savedOpen=body.open;
        return ok({open:savedOpen});
      }
      if(path.endsWith('/dialogs/shared-reminders')){
        if(body)reminderPosts.push(body);else feedReads++;
        return ok({reminders:[]});
      }
      if(path.endsWith('/operator/dialog-company')){
        companyCalls.push(body||u.searchParams.toString());
        if(body?.action==='company_tasks')
          return {ok:true,json:async()=>({success:true,company_section:{state:'available',rows:[]}})};
        if(body?.action==='company_profile')
          return {ok:true,json:async()=>({success:true,company_profile:{state:'available'}})};
        const route=routes.find(r=>r.channel_id===u.searchParams.get('channel_id'));
        return {ok:true,json:async()=>({success:true,binding:route?.company_binding??null})};
      }
      if(path.endsWith('/dialogs'))return ok({dialogs:[],channels:[],agents:[]});
      return ok({});
    };
    for(const name of ['tenant-operator-i18n','tenant-operator-dialogs','tenant-dialog-company','tenant-shared-dialog-reminders']){
      w.eval(read('docs/'+name+'.js'));
    }
    w.tenantI18n.setLanguage('en');
    $('#view-dialogs').classList.add('active');
    d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:profile}));
    await w.tenantOperator.refresh();
    for(const route of [bound,unbound]){
      assert(await w.tenantOperator.openDialog(route));await tick();
      $('#operatorClientCardOpen').click();
      assert($('#operatorClientCard').open);
      assert(!$('#operatorClientCard '+selector),'avatar modal has no scheduling control');
      $('#operatorCancelConversationName').click();
      const sidebar=$('#operatorCompanyPanel'),toggle=$('#operatorCompanyPanelOpen');
      if(!sidebar.open)toggle.click();
      assert(sidebar.open);assert(!toggle.closest('[inert]'));
      const main=$('#operatorCompanyView-main'),create=$(selector);
      assert(create&&!create.hidden&&!create.closest('[hidden]'));
      if(role==='owner'&&width<900){
        for(let node=create;node;node=node.parentElement)assert(!node.inert,'mobile reminder button must not inherit an inert ancestor');
      }
      assert.equal(d.querySelectorAll(selector).length,1);
      assert.equal(main.lastElementChild,create.parentElement,'last row of the actual Company Main tab');
      for(const tab of ['tasks','modules','history']){
        $('#operatorCompanyTab-'+tab).click();
        assert.equal(create.closest('[hidden]'),main,'no scheduling action in '+tab);
      }
      $('#operatorCompanyTab-main').click();assert(!create.closest('[hidden]'));
      create.click();assert($('#sharedDialogReminderModal form'));assert.equal(reminderPosts.length,0);
      if(route===bound)$('#sharedDialogReminderModal header button').click();
    }
    const staleForm=$('#sharedDialogReminderModal form');
    assert(await w.tenantOperator.openDialog(denied));await tick();
    assert(!$(selector),'server-scoped noneligible source has no reminder control');
    assert(!$('#sharedDialogReminderModal'),'switching away closes the exact-route scheduling modal');
    staleForm.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
    assert.equal(reminderPosts.length,0,'stale form and panel navigation cannot schedule');
    const reminderOnly={member,capabilities:{modules:{dialogs:true},...reminders}};
    w.tenantProfileSnapshot=reminderOnly;
    d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:reminderOnly}));
    const priorCalls=companyCalls.length;
    assert(await w.tenantOperator.openDialog(unbound));await tick();
    if(!$('#operatorCompanyPanel').open)$('#operatorCompanyPanelOpen').click();
    assert($('#operatorCompanyPanel [data-company-reminder-fallback] '+selector),'eligible route without Company permission keeps scheduling');
    assert(!$('#operatorCompanyTab-main'),'no Company privilege added for reminder-only users');
    assert.equal(companyCalls.length,priorCalls,'no Company request added without permission');
    if(role==='owner'){
      $(selector).click();
      const modal=$('#sharedDialogReminderModal');
      modal.querySelector('input').value='2099-11-24T12:00';
      modal.querySelector('textarea').value='Synthetic note';
      assert.equal(reminderPosts.length,0,'opening a reminder does not submit');
      modal.querySelector('form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
      await tick();
      assert.equal(reminderPosts.length,1);
      const {action,expected_tenant_id,expected_user_id,lead_id,channel_id,conversation_id,note}=reminderPosts[0];
      assert.deepEqual({action,expected_tenant_id,expected_user_id,lead_id,channel_id,conversation_id,note},
        {action:'schedule',expected_tenant_id:'synthetic',expected_user_id:'33',lead_id:42,channel_id:'eligible-b',
          conversation_id:'same-client',note:'Synthetic note'});
      assert.equal(feedReads,0,'creation-only role cannot fetch reminders even after scheduling');
    }
    d.dispatchEvent(new w.CustomEvent('tenant-auth-pending'));
    assert(!$(selector)||$(selector).closest('[hidden]'),'auth invalidation hides the control immediately');
  }finally{w.close();}
}
