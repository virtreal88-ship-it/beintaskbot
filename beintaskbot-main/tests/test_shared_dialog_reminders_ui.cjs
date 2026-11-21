const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('jsdom');

(async()=>{
  const dom=new JSDOM(`<body><button data-app-language>Language</button><div id="view-dialogs">
      <div id="operatorWorkspace"><div class="operator-list-pane"><div class="operator-filters"></div></div></div>
      <div id="operatorRows"><button data-open-operator="0"><span class="operator-row-indicators"></span></button></div>
      <dialog id="operatorClientCard"><details id="companyPanel" open><summary>Company</summary><div id="operatorClientCompanyHost"></div><div id="operatorClientSharedReminderHost" hidden></div></details></dialog>
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
    assert.equal($('#companyPanel').lastElementChild,create.parentElement,'creation is the last company row');
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
    console.log('PASS shared reminders in the global bell, company creation, restricted closed card, and auth reset');
  }finally{w.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
