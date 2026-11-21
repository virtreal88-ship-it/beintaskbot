const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const fs=require('node:fs');
const assert=require('node:assert/strict');

const dom=new JSDOM('<!doctype html><div id="integrationSettingsHost"></div>',{
  url:'https://beintaskbot.test/app?view=integrations',runScripts:'outside-only',
});
const {window}=dom;
let viewDemand,requests=[],companyImports=[],confirmMessages=[];
let mockIntegration={status:'connected',connected:true,configured:true,api_ready:false,
  pending_auth_received:false,pending_auth_access_confirmed:null};
let batchConfirmable=false;
let loginMode='success';
let probeMode='empty';
let directorySource='pending',directoryReady=false,companyAddOutcome='create';
let employeeLinks=[];
let holdDirectory=false,directoryResolvers=[];
window.tenantViewDemand={watch(value){viewDemand=value;}};
window.confirm=message=>{confirmMessages.push(message);return true;};
window.fetch=async(path,options={})=>{
  requests.push({path,options});
  let result;
  if(path==='/api/platform/members')result={success:true,members:[{telegram_id:42,display_name:'Workspace operator',role:'operator',active:true}]};
  else if(path.startsWith('/api/platform/operator/accounts?')&&options.method!=='POST')result={success:true,accounts:[{account:'imported-synthetic'}]};
  else if(path==='/api/platform/operator/employee-users')result={success:true,users:[{login:'operator.login',bindable:true}]};
  else if(path==='/api/platform/operator/employee-links'&&options.method==='POST'){
    const body=JSON.parse(options.body);employeeLinks=[{id:'link-1',member_telegram_id:body.member_telegram_id,account:body.account,login:body.login,status:'confirmed'}];
    result={success:true,link:employeeLinks[0]};
  }
  else if(path==='/api/platform/operator/employee-links')result={success:true,links:employeeLinks};
  else if(path==='/api/platform/integrations/beinsystems-operator'&&options.method!=='POST')result={success:true,integration:{...mockIntegration}};
  else if(options.method==='POST'&&JSON.parse(options.body).action==='preview_first_company_batch'){
    result={success:true,company_batch_preview:{partial:true,checked:1,limit:100,...(batchConfirmable?{receipt:'opaque-receipt'}:{}),
      rows:[{account:'<img src=x>',action:'create'}]}};
  }
  else if(options.method==='POST'&&JSON.parse(options.body).action==='confirm_first_company_batch'){
    result={success:true,company_import:{partial:true,checked:1,created:1,reused:0,manual_review:0}};
  }
  else if(options.method==='POST'&&JSON.parse(options.body).action==='create_companies'){
    companyImports.push(JSON.parse(options.body));
    result={success:true,company_import:{checked:4,created:2,reused:1,manual_review:1,
      name_collision_count:1,name_collision_accounts:['synthetic-collision'],name_collision_truncated:false}};
  }
  else if(path==='/api/platform/operator/accounts'&&options.method==='POST'
      &&JSON.parse(options.body).action==='add_account_company'){
    result={success:true,account_company:{status:companyAddOutcome,
      account_id:JSON.parse(options.body).account_id,source:directorySource,
      company:{id:'company-created',name:JSON.parse(options.body).account_id}}};
  }
  else if(options.method==='POST'&&JSON.parse(options.body).action==='test'){
    let verification;
    if(probeMode==='empty'){
      verification={source:'pending',request_accepted:true,access_confirmed:false,
        account_read:false,result_count:0,match_found:false};
    }else if(probeMode==='active'){
      verification={source:'active',request_accepted:true,access_confirmed:true,
        account_read:true,result_count:1,match_found:true};
    }else{
      verification={source:'pending',request_accepted:true,access_confirmed:true,
        account_read:true,result_count:1,match_found:true};
    }
    if(verification.account_read){
      mockIntegration={...mockIntegration,pending_auth_access_confirmed:probeMode==='pending'};
    }
    result={success:true,integration:{...mockIntegration},verification};
  }
  else if(options.method==='POST'&&JSON.parse(options.body).action==='directory_search'){
    if(holdDirectory)return await new Promise(resolve=>directoryResolvers.push(resolve));
    result={success:true,directory:{source:directorySource,account_id:'synthetic-account-id',result_count:1,
      exact_match:true,accounts_only_ready:directoryReady,
      identity:'private-identity',role:'superadmin',email:'private@example.test'}};
  }
  else if(options.method==='POST'&&JSON.parse(options.body).action==='prepare_accounts_capability'){
    result={success:true,accounts_only_confirmation:{identifier:'owner@synthetic.test',
      candidate_received_at:'2026-11-18T22:00:00+00:00'}};
  }
  else if(options.method==='POST'&&JSON.parse(options.body).action==='confirm_accounts_capability'){
    directoryReady=true;
    mockIntegration={...mockIntegration,accounts_only_ready:true};
    result={success:true,accounts_only:{ready:true,label:'accounts_only',expires_in_hours:24},
      integration:{...mockIntegration}};
  }
  else if(options.method==='POST'&&JSON.parse(options.body).action==='import'){
    mockIntegration={status:'connected',connected:true,configured:true,api_ready:true,
      pending_auth_received:false,pending_auth_access_confirmed:null};
    result={success:true,imported:137,activated:true,identity:{identifier:'synthetic.operator',role:'superadmin'},
      integration:{...mockIntegration}};
  }
  else if(options.method==='POST'&&JSON.parse(options.body).action==='login'){
    if(loginMode==='error')result={success:false,error:'Mock provider refusal (HTTP 403)'};
    else {
      mockIntegration={status:'not_connected',connected:false,configured:false,api_ready:false,pending_auth_received:true,
        pending_auth_access_confirmed:false,pending_auth_received_at:'2026-11-18T22:00:00+00:00'};
      result={success:true,integration:{...mockIntegration}};
    }
  }
  else result={success:true,integration:{...mockIntegration}};
  return {ok:true,json:async()=>result};
};
window.eval(fs.readFileSync('docs/tenant-beinsystems-operator-settings.js','utf8'));

(async()=>{
  const profile={member:{tenant_id:'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa',telegram_id:7,role:'owner'},
    integrations:[{provider:'beinsystems_operator'}]};
  await viewDemand.activate(profile);
  const panel=window.document.querySelector('#operatorIntegrationSettings');
  assert(!requests.some(row=>/^https?:/.test(row.path)),'the UI never calls Operator directly');
  assert(panel,'Operator settings panel is mounted in the existing integrations host');
  const login=panel.querySelector('[data-operator-login]');
  const password=panel.querySelector('[data-operator-password]');
  assert.equal(login.type,'text');
  assert.equal(password.type,'password');
  await new Promise(resolve=>setTimeout(resolve,0));
  const importedAccount=panel.querySelector('[data-catalog-results] button');
  assert(importedAccount);
  assert.equal(importedAccount.textContent,'imported-synthetic');
  importedAccount.click();
  assert.equal(panel.querySelector('[data-account]').value,'imported-synthetic',
    'imported account identifiers fill the existing manual mapping field without guessing a company');
  const memberSelect=panel.querySelector('[data-employee-member]');
  assert.equal(memberSelect.options.length,2);
  const confirmEmployee=panel.querySelector('[data-confirm-employee]');
  await confirmEmployee.onclick({currentTarget:confirmEmployee});
  assert(panel.textContent.includes('Выберите сотрудника Beintaskbot'));
  assert.equal(requests.filter(row=>row.path==='/api/platform/operator/employee-links'&&row.options.method==='POST').length,0,
    'employee mapping still requires an explicit employee selection');
  await confirmEmployee.onclick({currentTarget:confirmEmployee});
  assert(panel.textContent.includes('Выберите сотрудника Beintaskbot'));
  memberSelect.value='42';
  panel.querySelector('[data-employee-account]').value='staff-account';
  panel.querySelector('[data-employee-evidence]').value='Owner matched staff account';
  const loadUsers=panel.querySelector('[data-load-employee-users]');
  await confirmEmployee.onclick({currentTarget:confirmEmployee});
  assert(panel.textContent.includes('Выберите пользователя Operator'));
  await loadUsers.onclick({currentTarget:loadUsers});
  const userSelect=panel.querySelector('[data-employee-user]');
  assert.equal(userSelect.options.length,2);
  userSelect.value='operator.login';
  await confirmEmployee.onclick({currentTarget:confirmEmployee});
  const employeePost=requests.filter(row=>row.path==='/api/platform/operator/employee-links'&&row.options.method==='POST').at(-1);
  assert.deepEqual(JSON.parse(employeePost.options.body),{
    member_telegram_id:42,account:'staff-account',login:'operator.login',evidence:'Owner matched staff account',
    expected_tenant_id:profile.member.tenant_id,expected_user_id:profile.member.telegram_id,
  });
  assert(panel.textContent.includes('staff-account/operator.login'));

  // Keep the auth result beside the submit action, and retain the pending state after a reload.
  mockIntegration={status:'not_connected',connected:false,configured:false,api_ready:false,
    pending_auth_received:false,pending_auth_access_confirmed:null};
  await viewDemand.activate(profile);
  let authPanel=window.document.querySelector('#operatorIntegrationSettings');
  assert(authPanel,'the credential panel is recreated on a fresh integrations view');
  const authForm=authPanel.querySelector('[data-operator-auth-form]');
  const authLogin=authPanel.querySelector('[data-operator-login]');
  const authPassword=authPanel.querySelector('[data-operator-password]');
  const save=authPanel.querySelector('[data-save]');
  const authMessage=authPanel.querySelector('[data-auth-message]');
  assert(authForm && authMessage && authForm.contains(authMessage));
  assert.equal(save.type,'submit','the save control uses native form submission');
  assert.equal(authLogin.required,true);
  assert.equal(authPassword.required,true);
  assert.equal(authLogin.autocomplete,'off','do not autofill the app login into the Operator login');
  assert.equal(authPassword.autocomplete,'new-password','do not reuse the Beintaskbot saved password');
  assert.equal(save.nextElementSibling,authMessage,'auth result appears immediately after the submit button');
  assert.equal(authPanel.querySelector('[data-status]').getAttribute('aria-live'),'polite');
  assert(authPanel.querySelector('[data-status]').textContent.includes('not_connected'));
  const authPosts=()=>requests.filter(row=>row.path==='/api/platform/integrations/beinsystems-operator'
    &&row.options.method==='POST'&&JSON.parse(row.options.body).action==='login');
  save.click();
  assert.equal(authPosts().length,0,'native required validation blocks empty credentials');
  assert.equal(authForm.checkValidity(),false);
  authLogin.value='synthetic-user@synthetic-account';
  authPassword.value='synthetic-password';
  save.click();
  await new Promise(resolve=>setTimeout(resolve,0));
  const posted=JSON.parse(authPosts().at(-1).options.body);
  assert.deepEqual(posted,{
    action:'login',login:'synthetic-user@synthetic-account',password:'synthetic-password',
    expected_tenant_id:profile.member.tenant_id,expected_user_id:profile.member.telegram_id,
  });
  assert.equal(authPassword.value,'','password input is cleared immediately after submission');
  assert.equal(authPosts().length,1,'synthetic credentials are sent once to the authenticated same-origin endpoint');
  assert.equal(mockIntegration.status,'not_connected','receiving a JWT must not activate the integration');
  assert.equal(mockIntegration.connected,false,'unverified JWT does not change active connection state');
  assert.equal(mockIntegration.pending_auth_received,true);
  assert(authMessage.textContent.includes('JWT получен и зашифрован отдельно'));
  assert(authMessage.textContent.includes('Доступ не подтверждён'));
  assert.equal(window.document.activeElement,authMessage,'success focuses the visible auth result');
  assert(authPanel.querySelector('[data-status]').textContent.includes('not_connected'));
  assert(!authPanel.querySelector('[data-token]'),'the API token remains server-side');
  assert(authPanel.querySelector('[data-test]'),'the bounded account-access check is available');
  assert(authPanel.querySelector('[data-import]'),'full account import is an explicit owner action');
  assert(authPanel.querySelector('[data-create-companies]')?.disabled,
    'company creation stays disabled while only a pending token exists');

  // A fresh render/reload reads the durable pending state and shows the result without repeating login.
  await viewDemand.activate(profile);
  authPanel=window.document.querySelector('#operatorIntegrationSettings');
  assert(authPanel.querySelector('[data-auth-message]').textContent.includes('JWT получен и зашифрован отдельно'));
  assert(authPanel.querySelector('[data-status]').textContent.includes('not_connected'));
  assert.equal(authPosts().length,1,'a reload does not resubmit credentials');
  const testButton=authPanel.querySelector('[data-test]');
  const testPostCount=()=>requests.filter(row=>row.path==='/api/platform/integrations/beinsystems-operator'
    &&row.options.method==='POST'&&JSON.parse(row.options.body).action==='test').length;
  await testButton.onclick({currentTarget:testButton});
  assert.equal(testPostCount(),0,'a missing known-account search does not call the backend');
  authPanel.querySelector('[data-search]').value='synthetic-account';
  await testButton.onclick({currentTarget:testButton});
  await new Promise(resolve=>setTimeout(resolve,0));
  const probeMessage=()=>authPanel.querySelector('[data-message]').textContent;
  assert(probeMessage().includes('Operator принял запрос /user/accounts, совпадений нет'));
  assert(probeMessage().includes('чтение конкретного account не подтверждено'));
  assert(probeMessage().includes('сохранённый pending JWT'));
  assert(probeMessage().includes('не подтверждение нового способа входа'));
  assert(!probeMessage().includes('synthetic-account'),
    'the probe response does not reveal the queried account');
  assert.equal(mockIntegration.status,'not_connected','a bounded accounts probe does not activate the pending token');
  assert.equal(mockIntegration.connected,false);
  assert.equal(mockIntegration.pending_auth_access_confirmed,false,
    'an empty successful list does not persist account-read confirmation');

  authPanel.querySelector('[data-search]').value='synthetic-account';
  authPanel.querySelector('[data-account]').value='unchanged-local-account';
  const directoryButton=authPanel.querySelector('[data-search-button]');
  await directoryButton.onclick({currentTarget:directoryButton});
  await new Promise(resolve=>setTimeout(resolve,0));
  const directoryResults=authPanel.querySelector('[data-results]').textContent;
  assert(directoryResults.includes('synthetic-account-id'));
  assert(directoryResults.includes('one record')||directoryResults.includes('одна запись'));
  assert.equal(authPanel.querySelector('[data-results] button'),null,
    'a pending account result cannot select or write a local binding value');
  assert.equal(authPanel.querySelector('[data-account]').value,'unchanged-local-account');
  assert(!authPanel.textContent.includes('private@example.test'));
  assert(!authPanel.textContent.includes('superadmin'));
  assert.equal(mockIntegration.status,'not_connected','directory lookup does not activate pending credentials');
  assert.equal(requests.filter(row=>row.path==='/api/platform/integrations/beinsystems-operator'
    &&row.options.method==='POST'&&JSON.parse(row.options.body).action==='directory_search').length,1);
  const capabilityPrepare=authPanel.querySelector('[data-accounts-capability] button');
  assert(capabilityPrepare,'pending credentials require an explicit separate identity check before a company action appears');
  await capabilityPrepare.onclick();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert(authPanel.querySelector('[data-accounts-capability]').textContent.includes('owner@synthetic.test'));
  const capabilityConfirm=authPanel.querySelector('[data-accounts-capability] button');
  assert(capabilityConfirm.textContent.includes('Подтверждаю'));
  await capabilityConfirm.onclick();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert(authPanel.querySelector('[data-accounts-capability]').textContent.includes('Подключено для аккаунтов'));
  assert(authPanel.querySelector('[data-accounts-capability]').textContent.includes('попытку добавления'));
  assert.equal(mockIntegration.connected,false,'the accounts capability never marks generic Operator connected');
  assert.equal(mockIntegration.status,'not_connected');
  assert(authPanel.querySelector('[data-status]').textContent.includes('Подключено для аккаунтов'));
  await directoryButton.onclick({currentTarget:directoryButton});
  await new Promise(resolve=>setTimeout(resolve,0));
  const pendingAdd=[...authPanel.querySelectorAll('[data-results] button')]
    .find(button=>button.textContent.includes('Добавить этот account'));
  assert(pendingAdd,'only the explicit, identity-bound accounts capability offers one-account creation');
  await pendingAdd.onclick();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert(authPanel.querySelector('[data-message]').textContent.includes('название — только account ID'));
  assert(authPanel.querySelector('[data-message]').textContent.includes('CRM-компания создана'),
    'the backend outcome "create" is reported as creation');
  assert(requests.some(row=>row.path==='/api/platform/operator/accounts'&&row.options.method==='POST'
    &&JSON.parse(row.options.body).action==='add_account_company'));
  assert(!requests.some(row=>row.path==='/api/platform/operator/accounts'&&row.options.method==='POST'
    &&JSON.parse(row.options.body).action==='import'),'one-account creation does not call full import');
  companyAddOutcome='reuse';
  await pendingAdd.onclick();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert(authPanel.querySelector('[data-message]').textContent.includes('Существующая CRM-компания сохранена'));
  companyAddOutcome='bind';
  await pendingAdd.onclick();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert(authPanel.querySelector('[data-message]').textContent.includes('Существующая CRM-компания сохранена'));
  companyAddOutcome='manual_review';
  await pendingAdd.onclick();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert(authPanel.querySelector('[data-message]').textContent.includes('ручная проверка'));
  companyAddOutcome='unexpected';
  await pendingAdd.onclick();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert(authPanel.querySelector('[data-message]').textContent.includes('Не удалось определить результат'));
  assert(!authPanel.querySelector('[data-message]').textContent.includes('CRM-компания создана'));
  companyAddOutcome='create';
  directorySource='active';
  await directoryButton.onclick({currentTarget:directoryButton});
  await new Promise(resolve=>setTimeout(resolve,0));
  const activeResult=authPanel.querySelector('[data-results] button');
  assert(activeResult,'an active account result preserves the existing selectable affordance');
  activeResult.click();
  assert.equal(authPanel.querySelector('[data-account]').value,'synthetic-account-id');
  directorySource='pending';
  holdDirectory=true;
  authPanel.querySelector('[data-search]').value='first-query';
  const staleSearch=directoryButton.onclick({currentTarget:directoryButton});
  authPanel.querySelector('[data-search]').value='second-query';
  const currentSearch=directoryButton.onclick({currentTarget:directoryButton});
  assert.equal(directoryResolvers.length,2);
  directoryResolvers[1]({ok:true,json:async()=>({success:true,directory:{source:'active',
    account_id:'second-result',result_count:1}})});
  await currentSearch;
  directoryResolvers[0]({ok:true,json:async()=>({success:true,directory:{source:'pending',
    account_id:'stale-result',result_count:1}})});
  await staleSearch;
  holdDirectory=false;
  assert(authPanel.querySelector('[data-results]').textContent.includes('second-result'));
  assert(!authPanel.querySelector('[data-results]').textContent.includes('stale-result'));
  authPanel.querySelector('[data-search]').value='synthetic-account';

  probeMode='pending';
  await testButton.onclick({currentTarget:testButton});
  await new Promise(resolve=>setTimeout(resolve,0));
  assert(probeMessage().includes('вернул одну запись; чтение account подтверждено'));
  assert(probeMessage().includes('Точное совпадение найдено'));
  assert.equal(mockIntegration.pending_auth_access_confirmed,true);
  assert(authPanel.querySelector('[data-status]').textContent.includes('отметка предыдущей проверки'));
  assert(!authPanel.querySelector('[data-status]').textContent.includes('прочитан account'),
    'persisted legacy status is not presented as a confirmed account read');

  mockIntegration={status:'connected',connected:true,configured:true,api_ready:true,
    pending_auth_received:false,pending_auth_access_confirmed:null};
  probeMode='active';
  await testButton.onclick({currentTarget:testButton});
  await new Promise(resolve=>setTimeout(resolve,0));
  assert(probeMessage().includes('Проверено действующее подключение'));
  assert(probeMessage().includes('pending JWT и способ нового входа не проверялись'));
  assert(!probeMessage().includes('Использован сохранённый pending JWT'),
    'an active result is not labelled as a pending-token check');

  mockIntegration={status:'not_connected',connected:false,configured:false,api_ready:false,
    pending_auth_received:true,pending_auth_access_confirmed:false};
  probeMode='pending';
  const testPost=requests.filter(row=>row.path==='/api/platform/integrations/beinsystems-operator'
    &&row.options.method==='POST'&&JSON.parse(row.options.body).action==='test').at(-1);
  assert(testPost,'the bounded account-access check is explicitly invoked');
  assert.deepEqual(JSON.parse(testPost.options.body),{
    action:'test',search:'synthetic-account',
    expected_tenant_id:profile.member.tenant_id,expected_user_id:profile.member.telegram_id,
  });

  // Provider errors appear next to the submit action.
  loginMode='error';
  const errorLogin=authPanel.querySelector('[data-operator-login]');
  const errorPassword=authPanel.querySelector('[data-operator-password]');
  const errorSave=authPanel.querySelector('[data-save]');
  errorLogin.value='synthetic-user@synthetic-account';
  errorPassword.value='synthetic-password';
  errorSave.click();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert(authPanel.querySelector('[data-auth-message]').textContent.includes('Mock provider refusal (HTTP 403)'));
  assert.equal(window.document.activeElement,authPanel.querySelector('[data-auth-message]'),'errors focus the visible auth result');
  assert.equal(errorPassword.value,'');
  assert.equal(mockIntegration.status,'not_connected');
  assert.equal(mockIntegration.pending_auth_received,true,'an error does not erase the prior pending token');
  assert.equal(authPosts().length,2);
  const importButton=authPanel.querySelector('[data-import]');
  await importButton.onclick({currentTarget:importButton});
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(mockIntegration.status,'connected','mocked full account read may activate a new token after access succeeds');
  assert(authPanel.textContent.includes('Импортировано account ID: 137'));
  assert(requests.some(row=>row.path==='/api/platform/operator/accounts'
    &&row.options.method==='POST'&&JSON.parse(row.options.body).action==='import'));
  const createCompanies=authPanel.querySelector('[data-create-companies]');
  assert(createCompanies&&!createCompanies.disabled,'the separate company action is enabled only after the active connection is established');
  await createCompanies.onclick({currentTarget:createCompanies});
  assert.equal(companyImports.length,1);
  assert.deepEqual(companyImports[0],{
    action:'create_companies',expected_tenant_id:profile.member.tenant_id,
    expected_user_id:profile.member.telegram_id,
  });
  assert(confirmMessages.at(-1).includes('свежему списку активного Operator'));
  assert(authPanel.querySelector('[data-message]').textContent.includes('создано компаний: 2'));
  assert(authPanel.querySelector('[data-message]').textContent.includes('ручная проверка: 1'));
  assert(authPanel.querySelector('[data-message]').textContent.includes('Совпадение названия'));
  assert(authPanel.querySelector('[data-message]').textContent.includes('synthetic-collision'));
  assert.equal(authPanel.querySelector('[data-message] img'),null,'account names render as text, not markup');
  assert(!requests.some(row=>/^https?:/.test(row.path)),'all Operator reads and local company creation use the same-origin backend only');
  assert.equal(authPanel.querySelector('[data-first-company-batch]').hidden,true);
  mockIntegration={...mockIntegration,status:'not_connected',accounts_only_ready:false,
    pending_auth_received:false,connected:false,configured:false,first_company_batch_preview_enabled:true};
  await viewDemand.activate(profile);
  let blockedPanel=window.document.querySelector('#operatorIntegrationSettings');
  assert.equal(blockedPanel.querySelector('[data-first-company-batch]').hidden,true,'no current pending candidate');
  mockIntegration={...mockIntegration,pending_auth_received:true};
  await viewDemand.activate(profile);
  blockedPanel=window.document.querySelector('#operatorIntegrationSettings');
  const disabledBatch=blockedPanel.querySelector('[data-first-company-batch]');
  const prerequisite=blockedPanel.querySelector('[data-first-batch-prerequisite]');
  assert.equal(disabledBatch.hidden,false);
  assert.equal(disabledBatch.disabled,true);
  assert.equal(disabledBatch.textContent,'Предпросмотр партии до 100 аккаунтов');
  assert.equal(prerequisite.hidden,false);
  assert(prerequisite.textContent.includes('Проверить личность для аккаунтного доступа'));
  assert.equal(disabledBatch.getAttribute('aria-describedby'),prerequisite.id);
  const requestsBeforeBlockedClick=requests.length;
  await disabledBatch.onclick({currentTarget:disabledBatch});
  assert.equal(requests.length,requestsBeforeBlockedClick,'disabled preview does not call Operator');
  assert.equal(disabledBatch.disabled,true);
  mockIntegration={...mockIntegration,accounts_only_ready:true,configured:true};
  await viewDemand.activate(profile);
  assert.equal(window.document.querySelector('[data-first-company-batch]').hidden,true,'active or configured source stays excluded');
  mockIntegration={...mockIntegration,configured:false};
  await viewDemand.activate(profile);
  const batchPanel=window.document.querySelector('#operatorIntegrationSettings');
  const batchButton=batchPanel.querySelector('[data-first-company-batch]');
  const batchPreview=batchPanel.querySelector('[data-first-batch-preview]');
  const batchPosts=()=>requests.filter(row=>row.options.method==='POST').map(row=>JSON.parse(row.options.body))
    .filter(body=>body.action==='confirm_first_company_batch');
  assert.equal(batchButton.hidden,false);
  assert.equal(batchButton.disabled,false);
  assert.equal(batchPanel.querySelector('[data-first-batch-prerequisite]').hidden,true);
  await batchButton.onclick({currentTarget:batchButton});
  assert.equal(batchPreview.querySelector('[data-first-batch-confirm]'),null,'preview-only offers no commit');
  assert(batchPreview.textContent.includes('CRM-компании не создаются'));
  batchConfirmable=true;
  await batchButton.onclick({currentTarget:batchButton});
  assert.equal(batchPosts().length,0,'preview must never confirm automatically');
  assert(batchPreview.textContent.includes('не полный каталог'));
  assert(batchPreview.textContent.includes('<img src=x>'));
  assert.equal(batchPreview.querySelector('img'),null);
  const cancelled=batchPreview.querySelector('[data-first-batch-confirm]');
  batchPreview.querySelector('[data-first-batch-cancel]').click();
  await cancelled.onclick({currentTarget:cancelled});
  assert.equal(batchPosts().length,0,'cancelled receipt is not submitted');
  await batchButton.onclick({currentTarget:batchButton});
  const confirmBatch=batchPreview.querySelector('[data-first-batch-confirm]');
  await confirmBatch.onclick({currentTarget:confirmBatch});
  assert.deepEqual(batchPosts(),[{action:'confirm_first_company_batch',receipt:'opaque-receipt',
    expected_tenant_id:profile.member.tenant_id,expected_user_id:profile.member.telegram_id}]);
  assert(batchPanel.querySelector('[data-message]').textContent.includes('Первая выборка (не весь каталог)'));
  assert.equal(batchPreview.textContent,'');
  const readOnlyProfile={member:{...profile.member,role:'manager',permissions:['operator_data:view']},
    integrations:profile.integrations};
  await viewDemand.activate(readOnlyProfile);
  const readOnlyPanel=window.document.querySelector('#operatorIntegrationSettings');
  assert.equal(readOnlyPanel.querySelector('[data-link-controls]').hidden,true);
  assert.equal(readOnlyPanel.querySelector('[data-operator-password]'),null);
  assert.equal(readOnlyPanel.querySelector('[data-first-company-batch]'),null);
  assert.equal(readOnlyPanel.querySelector('[data-create-companies]'),null,
    'the owner-only company action and collision list are not exposed to a read-only member');
  window.close();
  console.log('PASS: Operator credentials stage a private JWT without claiming access confirmation');
})().catch(error=>{window.close();console.error(error);process.exitCode=1;});
