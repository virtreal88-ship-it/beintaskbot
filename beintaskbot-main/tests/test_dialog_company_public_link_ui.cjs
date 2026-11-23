const fs=require('node:fs'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom');
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
(async()=>{
  const dom=new JSDOM('<dialog><header class="operator-client-panel-header"></header><div id="host"></div></dialog><span id="name"></span>',
    {url:'https://crm.test/app',runScripts:'outside-only'});
  const w=dom.window,d=w.document,card=d.querySelector('dialog'),host=d.querySelector('#host');
  const URL='https://1000.az/AbC123',copied=[],calls=[],profiles=[];let clipboardFails=false,gestureWrites=0;
  w.tenantI18n={language:'az'};
  w.ClipboardItem=class{constructor(data){this.data=data;}};
  Object.defineProperty(w.navigator,'clipboard',{configurable:true,value:{writeText:async value=>{
    if(clipboardFails)throw Error('blocked');copied.push(value);
  },write(items){
    gestureWrites++;
    return items[0].data['text/plain'].then(blob=>new Promise((resolve,reject)=>{
      if(clipboardFails){reject(Error('blocked'));return;}
      const r=new w.FileReader();r.onload=()=>{copied.push(r.result);resolve();};r.onerror=reject;r.readAsText(blob);
    }));
  }}});
  const actionBinding='c'.repeat(64),submissions=[],sectionReads=[];let holdWrite=false,releaseWrite;
  w.fetch=(path,{signal,method,body,cache,headers})=>{
    assert.equal(method,'POST');assert.equal(cache,'no-store');assert.equal(headers['Content-Type'],'application/json');
    const data=JSON.parse(body);
    if(path.endsWith('/action')){
      submissions.push(data);
      if(holdWrite)return new Promise(resolve=>{releaseWrite=(state='unconfirmed')=>resolve({ok:true,json:async()=>({success:true,state})});});
      return Promise.resolve({ok:true,json:async()=>({success:true,state:'done'})});
    }
    if(data.action==='company_profile'){
      assert.equal(path,'/api/platform/operator/dialog-company');
      return new Promise(resolve=>profiles.push(profile=>resolve({ok:true,json:async()=>({
        success:true,company_profile:profile})})));
    }
    if(data.action==='company_tasks'||data.action==='company_history'){
      sectionReads.push(data);
      const rows=data.action==='company_history'?[
        ['2026-11-03T23:59:00-12:00','+1234.50 USD','credit'],
        ['2026-11-04','-2.00','daily_charge'],
        ['not-a-date','amount unknown','<b>Custom payment</b>'],
        ['2026-02-30','12.50','credit','EUR'],
        ['2026-11-06','1,200 AZN','credit','AZN'],
        ['2026-11-07','10 EUR','credit','AZN'],
        ...Array.from({length:35},(_,i)=>['day-'+(i+6),'0.00 AZN','payment-'+(i+6)])
      ]:[];
      return Promise.resolve({ok:true,json:async()=>({success:true,company_section:{state:'available',rows}})});
    }
    assert.equal(path,'/api/platform/operator/dialog-company/public-link');
    assert.equal(data.action,'copy_customer_link');assert.equal('account' in data,false);assert.equal('public_base_url' in data,false);
    return new Promise(resolve=>calls.push({id:data.expected_company_id,channel:data.channel_id,signal,
      finish(link,status=200){resolve({ok:status===200,status,json:async()=>({success:status===200,public_link:link})});}}));
  };
  w.eval(fs.readFileSync('docs/tenant-dialog-company.js','utf8'));
  const api=w.tenantDialogueCompany,box=()=>host.querySelector('[data-company-public-link]'),
        icon=()=>box().querySelector('button'),press=()=>icon().click();
  function mount(company,channel='a'){
    api.mount({host,nameHost:d.querySelector('#name'),
      route:{provider:'connect',lead_id:42,channel_id:channel,conversation_id:'peer',
        company_binding:{company_id:company,company_name:'Synthetic'}},
      member:{tenant_id:'tenant',telegram_id:33},isCurrent:()=>true});
  }
  mount('first');assert.equal(calls.length,0);assert(box().hidden);
  card.open=true;api.refreshPublicLink();assert.equal(calls.length,0,'opening never issues a link');
  assert.equal(profiles.length,1);
  api.refreshPublicLink();assert.equal(profiles.length,1,'no duplicate read while the same panel remains open');
  assert.equal(box().textContent,'Müştəri linki');assert.equal(icon().getAttribute('aria-label'),'Linki kopyala');
  press();press();assert.equal(calls.length,1);assert.equal(gestureWrites,1);
  assert.equal(calls[0].id,'first');
  mount('second');assert(calls[0].signal.aborted);assert.equal(calls.length,1);
  profiles[0]({state:'available',registration_phone:'111111111',balance:123});await tick();
  assert(!host.querySelector('[data-company-profile]').textContent.includes('111111111'));
  profiles[1]({state:'available',registration_phone:'222222222',balance:0,actions:{task:true,credit:true},action_binding:actionBinding,
    task_options:{account:'verified-account',projects:['Umumi'],priorities:[2,3,4]}});await tick();
  assert(host.querySelector('[data-company-profile]').textContent.includes('222222222'));
  const area=id=>d.querySelector('#operatorCompanyView-'+id),tab=id=>d.querySelector('#operatorCompanyTab-'+id);
  assert(!area('main').querySelector('[data-company-action]'),'Main contains no task or credit control');
  const taskTrigger=area('tasks').querySelector('[data-company-action=task]');
  assert(!area('tasks').querySelector('[data-company-action=credit]'));
  tab('tasks').click();await tick();taskTrigger.click();
  const task=d.querySelector('[data-company-action-modal=task]');
  assert(task.open);assert.equal(task.querySelector('h2').textContent,'Yeni Tapşırıq');
  assert.equal(task.querySelector('input[readonly]').value,'verified-account');
  assert.equal(submissions.length,0,'opening the modal never writes');
  task.querySelector('input:not([readonly])').value='Test';
  task.querySelector('textarea').value='Details';
  task.querySelectorAll('.operator-company-action-grid select')[1].value='Umumi';
  task.querySelector('button[type=submit]').click();task.querySelector('button[type=submit]').click();await tick();
  assert.equal(submissions.length,1);
  assert.equal(submissions[0].fields.project,'Umumi');assert.equal(submissions[0].expected_company_id,'second');assert.equal(submissions[0].expected_binding,actionBinding);
  assert(!('account' in submissions[0].fields),'bound account is displayed, not accepted as a caller field');
  task.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert(!task.isConnected);
  assert.equal(d.activeElement,taskTrigger,'closing returns focus to the opening control');
  tab('history').click();await tick();
  const pay=area('history'),historyReadCount=()=>sectionReads.filter(r=>r.action==='company_history').length;
  const readBeforePaging=historyReadCount(),shown=()=>[...pay.querySelectorAll('tbody tr')];
  assert.equal(shown().length,20);
  for(const [lang,locale,labels] of [
    ['ru','ru-RU',['История операций','Тип','Сумма','Кредит','Ежедневный расход']],
    ['en','en-US',['Transaction history','Type','Amount','Credit','Daily expense']],
    ['az','az-AZ',['Əməliyyat tarixçəsi','Növ','Məbləğ','Kredit','günlük xərc']]
  ]){
    w.tenantI18n.language=lang;d.dispatchEvent(new w.Event('tenant-language'));
    assert.equal(pay.querySelector('h3').textContent,labels[0]);
    assert.deepEqual([...pay.querySelectorAll('th')].map(x=>x.textContent),labels.slice(1,3));
    assert.equal(shown()[0].querySelector('[data-kind=credit]').textContent,labels[3]);
    assert.equal(shown()[1].querySelector('[data-kind=expense]').textContent,labels[4]);
    const format=(n,options)=>new Intl.NumberFormat(locale,{minimumFractionDigits:2,maximumFractionDigits:2,...options}).format(n);
    assert.equal(shown()[0].lastElementChild.textContent,format(1234.5,{style:'currency',currency:'USD',currencyDisplay:'code',signDisplay:'always'}));
    assert(shown()[0].lastElementChild.hasAttribute('data-positive-credit'));
    assert.equal(shown()[1].lastElementChild.textContent,format(-2));
    assert(!shown()[1].lastElementChild.hasAttribute('data-positive-credit'));
    assert.equal(shown()[0].querySelector('time').dateTime,'2026-11-03','date does not shift across midnight');
    assert.equal(shown()[2].querySelector('[data-kind=unknown]').textContent,'<b>Custom payment</b>');
    assert(!shown()[2].querySelector('b'));
    assert.equal(shown()[2].lastElementChild.textContent,'amount unknown');
    assert.equal(shown()[3].querySelector('small').textContent,'2026-02-30','invalid date stays literal');
    assert.equal(shown()[3].lastElementChild.textContent,format(12.5,{style:'currency',currency:'EUR',currencyDisplay:'code'}));
    assert.equal(shown()[4].lastElementChild.textContent,'1,200 AZN','opaque value keeps only the original unit');
    assert.equal(shown()[5].lastElementChild.textContent,'10 EUR','conflicting units preserve the original literal');
    assert(!shown()[4].lastElementChild.hasAttribute('data-positive-credit')&&!shown()[5].lastElementChild.hasAttribute('data-positive-credit'));
  }
  const pageButtons=()=>[...pay.querySelectorAll('.operator-company-pagination button')];
  assert(pageButtons()[0].disabled);
  pageButtons()[1].click();assert.equal(shown().length,20);assert.match(shown()[0].textContent,/payment-20/);
  w.tenantI18n.language='ru';d.dispatchEvent(new w.Event('tenant-language'));
  assert.match(shown()[0].textContent,/payment-20/,'locale update keeps current page');
  w.tenantI18n.language='az';d.dispatchEvent(new w.Event('tenant-language'));
  pageButtons()[1].click();assert.equal(shown().length,1);assert.match(shown()[0].textContent,/payment-40/);
  assert(pageButtons()[1].disabled);assert.equal(historyReadCount(),readBeforePaging,'page buttons only slice the verified returned snapshot');
  const creditTrigger=pay.querySelector('[data-company-action=credit]');creditTrigger.click();
  const credit=d.querySelector('[data-company-action-modal=credit]');
  assert(credit.open);assert(!pay.querySelector('[data-company-action=task]'));
  credit.querySelector('input[type=number]').value='1.50';
  credit.querySelector('input:not([type=number])').value='Example';
  w.confirm=()=>false;credit.querySelector('button[type=submit]').click();assert.equal(submissions.length,1);
  w.confirm=()=>true;credit.querySelector('button[type=submit]').click();await tick();
  assert.equal(submissions[1].fields.amount,'1.50');assert.equal(submissions[1].expected_binding,actionBinding);assert(!('account' in submissions[1].fields));
  credit.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  tab('main').click();
  calls[0].finish({state:'available',url:'https://1000.az/Wrong1'});await tick();
  assert.equal(copied.length,0,'late first-company receipt cannot enter clipboard');
  press();clipboardFails=true;calls[1].finish({state:'available',url:URL});await tick();await tick();
  assert.match(box().textContent,/Brauzer linki kopyalamadı/);
  const visibleLink=box().querySelector('.operator-company-public-url');
  assert(visibleLink&&!visibleLink.hidden,'the existing customer link remains visible when clipboard write fails');
  assert.equal(visibleLink.href,URL);
  clipboardFails=false;press();calls[2].finish(undefined,403);await tick();
  assert.equal(copied.length,0,'retry uses current authorization');
  press();calls[3].finish({state:'available',url:URL});await tick();await tick();
  assert.deepEqual(copied,[URL]);
  mount('second','b');press();assert.equal(calls[4].channel,'b');
  api.hidePublicLink();card.open=false;calls[4].finish({state:'available',url:URL});await tick();
  assert(box().hidden,'late closed-panel result never copies');
  assert.deepEqual(copied,[URL]);
  card.open=true;api.refreshPublicLink();press();calls[5].finish(undefined,502);await tick();
  assert(!icon().disabled,'a read can be retried after an ambiguous failure');
  press();calls[6].finish({state:'available',url:URL});
  for(let i=0;i<6;i++)await tick();
  assert.deepEqual(copied,[URL,URL]);assert.match(box().textContent,/Link kopyalandı/);
  press();calls[7].finish({state:'available',url:URL});await tick();await tick();
  assert.deepEqual(copied,[URL,URL,URL]);assert.equal(calls.length,8,'sequential clicks verify the existing URL');
  mount('third');
  for(const [reply,status,message] of [
    [{state:'not_found'},200,/Operator mövcud linki qaytarmadı/],
    [{state:'not_connected'},200,/Operator qoşulmayıb/],
    [{state:'not_linked'},200,/təsdiqlənmiş Operator hesabı/],
    [undefined,424,/Operator link sorğusunu rədd etdi/],
    [undefined,403,/giriş icazəniz yoxdur/]
  ]){
    press();calls.at(-1).finish(reply,status);await tick();
    assert.match(box().textContent,message);
    assert.equal(copied.length,3,'failure does not change the clipboard');
  }
  Object.defineProperty(w.navigator,'clipboard',{value:undefined});
  press();await tick();assert.equal(calls.length,13,'unsupported clipboard does not make a request');
  assert.match(box().textContent,/kopyalama əlçatan deyil/);
  // Route change disposes the modal and a late ambiguous write never enables
  // a second submission by closing/reopening this route's modal.
  mount('fourth','d');
  profiles.at(-1)({state:'available',actions:{task:true,credit:false},action_binding:actionBinding,task_options:{
    account:'fourth-account',projects:['Umumi'],priorities:[3]}});await tick();
  assert(!area('history').querySelector('[data-company-action=credit]'));
  tab('tasks').click();await tick();area('tasks').querySelector('[data-company-action=task]').click();
  const pending=d.querySelector('[data-company-action-modal=task]');
  pending.querySelector('input:not([readonly])').value='Pending task';
  pending.querySelector('textarea').value='Details';
  pending.querySelectorAll('select')[1].value='Umumi';
  holdWrite=true;pending.querySelector('button[type=submit]').click();assert.equal(submissions.length,3);
  mount('fifth','e');assert(!pending.isConnected);
  releaseWrite();await tick();holdWrite=false;
  mount('fourth','d');
  profiles.at(-1)({state:'available',actions:{task:true,credit:false},action_binding:actionBinding,task_options:{
    account:'fourth-account',projects:['Umumi'],priorities:[3]}});await tick();
  tab('tasks').click();await tick();area('tasks').querySelector('[data-company-action=task]').click();
  const reopened=d.querySelector('[data-company-action-modal=task]');
  assert(reopened.querySelector('button[type=submit]').disabled);
  assert.match(reopened.querySelector('[role=status]').textContent,/təsdiqlənməyib/);
  reopened.querySelector('button[type=submit]').click();assert.equal(submissions.length,3);
  for(const result of ['done','denied','unconfirmed']){
    mount('latest-'+result,'f');
    profiles.at(-1)({state:'available',actions:{task:true,credit:false},action_binding:actionBinding,task_options:{
      account:'active-account',projects:['Umumi'],priorities:[3]}});await tick();
    tab('tasks').click();await tick();
    const open=()=>{area('tasks').querySelector('[data-company-action=task]').click();return d.querySelector('[data-company-action-modal=task]');};
    const old=open();
    old.querySelector('input:not([readonly])').value='Held task';old.querySelector('textarea').value='Details';
    old.querySelectorAll('select')[1].value='Umumi';
    holdWrite=true;old.querySelector('button[type=submit]').click();
    const writes=submissions.length;
    old.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
    assert(!old.isConnected);
    const fresh=open();assert(fresh.querySelector('button[type=submit]').disabled);
    fresh.querySelector('button[type=submit]').click();assert.equal(submissions.length,writes);
    releaseWrite(result);holdWrite=false;await tick();
    assert.equal(fresh.querySelector('button[type=submit]').disabled,result==='unconfirmed');
    assert.match(fresh.querySelector('[role=status]').textContent,{done:/Tamamlandı/,denied:/rədd etdi/,unconfirmed:/təsdiqlənməyib/}[result]);
  }
  api.clear();assert.equal(box(),null);assert(!d.querySelector('[data-company-action-modal]'));
  dom.window.close();
  console.log('dialog company one-click public-link assertions passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
