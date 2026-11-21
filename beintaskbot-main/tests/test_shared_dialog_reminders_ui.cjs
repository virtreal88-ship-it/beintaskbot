const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('jsdom');

(async()=>{
  const dom=new JSDOM(`<body><div id="view-dialogs" class="active">
      <div id="operatorWorkspace"><div class="operator-list-pane"><div class="operator-filters"></div></div></div>
      <div id="operatorRows"><button data-open-operator="0"><span class="operator-row-indicators"></span></button></div>
      <details id="operatorMoreActions"><div class="operator-action-menu"></div></details>
    </div></body>`,{runScripts:'outside-only',pretendToBeVisual:true,url:'https://crm.test/app'});
  const w=dom.window,d=w.document,$=x=>d.querySelector(x);
  try{
    w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
    const account={tenant_id:'tenant',telegram_id:33,role:'operator',active:true};
    const actual={provider:'connect',channel_id:'support',conversation_id:'chat',lead_id:41,
      shared_reminder:{can_schedule:true,id:'own'}};
    const personal={...actual,channel_id:'personal',shared_reminder:undefined};
    let selected=actual,posts=[],openedChat=false,rows=[
      {id:'own',title:'Actual dialogue',due_at:new Date().toISOString(),due:true,dialog:actual},
      {id:'foreign',title:'Other closed dialogue',due_at:new Date().toISOString(),due:true}
    ];
    w.tenantI18n={language:'en'};
    w.tenantProfileSnapshot={capabilities:{shared_dialog_reminders:true},member:account};
    w.tenantOperator={member:account,get currentDialog(){return selected;},hasRight:()=>true,
      openDialog:()=>{openedChat=true;return Promise.resolve(true);}};
    w.fetch=async(url,options)=>{
      assert.match(url,/^\/api\/platform\/ai\/dialogs\/shared-reminders/);
      let result={reminders:rows,has_more:false};
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
    await tick();
    w.tenantSharedReminders.markRows([[actual]]);
    assert.equal($('#operatorSharedRemindersOpen').textContent,'Shared reminders (2)');
    assert($('#operatorRows [data-shared-reminder]'));
    assert.equal($('#operatorSharedReminderCreate').hidden,false);
    assert.equal(posts.length,0,'poll and normal render do not consume marks');
    $('#operatorSharedRemindersOpen').click();
    [...d.querySelectorAll('#sharedDialogReminderModal article button')].find(b=>b.textContent==='Open reminder').click();
    await tick();
    assert.equal(posts[0].id,'foreign');
    assert.equal(openedChat,false,'a shared card does not open a restricted history');
    assert.match($('#sharedDialogReminderModal').textContent,/Marker cleared for everyone/);
    assert.equal($('#sharedDialogReminderModal img'),null,'team note is rendered as text');
    assert.equal($('#operatorSharedRemindersOpen').textContent,'Shared reminders (1)');
    await w.tenantSharedReminders.dialogOpened(actual);
    assert.equal(posts[1].id,'own');
    assert.equal($('#operatorRows [data-shared-reminder]'),null);
    selected=personal;
    d.dispatchEvent(new w.CustomEvent('tenant-chat-attachments-ready'));
    assert.equal($('#operatorSharedReminderCreate').hidden,true);
    d.dispatchEvent(new w.CustomEvent('tenant-auth-pending'));
    assert.equal($('#operatorSharedRemindersOpen').hidden,true);
    assert.equal($('#sharedDialogReminderModal'),null);
    console.log('PASS shared reminder markers, distinct closed card, exact acknowledgement and auth reset');
  }finally{w.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
