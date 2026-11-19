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
let loginMode='success';
let employeeLinks=[];
let dialogBinding={company_id:null,company_name:null,can_manage:true};
let companySearches=[];
window.tenantViewDemand={watch(value){viewDemand=value;}};
window.confirm=message=>{confirmMessages.push(message);return true;};
window.fetch=async(path,options={})=>{
  requests.push({path,options});
  let result;
  if(path==='/api/platform/members')result={success:true,members:[{telegram_id:42,display_name:'Workspace operator',role:'operator',active:true}]};
  else if(path.startsWith('/api/platform/crm/native/companies?')){
    const query=new URL(path,'https://beintaskbot.test').searchParams;
    companySearches.push(query.get('search'));
    result={success:true,companies:[{id:'company-synthetic',name:'Synthetic company'}]};
  }
  else if(path.startsWith('/api/platform/operator/dialog-company?'))result={success:true,binding:{...dialogBinding}};
  else if(path==='/api/platform/operator/dialog-company'&&options.method==='POST'){
    const body=JSON.parse(options.body);
    dialogBinding=body.company_id
      ? {company_id:body.company_id,company_name:'Synthetic company',can_manage:true}
      : {company_id:null,company_name:null,can_manage:true};
    result={success:true,binding:{...dialogBinding}};
  }
  else if(path.startsWith('/api/platform/operator/accounts?')&&options.method!=='POST')result={success:true,accounts:[{account:'imported-synthetic'}]};
  else if(path==='/api/platform/operator/employee-users')result={success:true,users:[{login:'operator.login',bindable:true}]};
  else if(path==='/api/platform/operator/employee-links'&&options.method==='POST'){
    const body=JSON.parse(options.body);employeeLinks=[{id:'link-1',member_telegram_id:body.member_telegram_id,account:body.account,login:body.login,status:'confirmed'}];
    result={success:true,link:employeeLinks[0]};
  }
  else if(path==='/api/platform/operator/employee-links')result={success:true,links:employeeLinks};
  else if(path==='/api/platform/integrations/beinsystems-operator'&&options.method!=='POST')result={success:true,integration:{...mockIntegration}};
  else if(options.method==='POST'&&JSON.parse(options.body).action==='create_companies'){
    companyImports.push(JSON.parse(options.body));
    result={success:true,company_import:{checked:4,created:2,reused:1,manual_review:1,
      name_collision_count:1,name_collision_accounts:['synthetic-collision'],name_collision_truncated:false}};
  }
  else if(options.method==='POST'&&JSON.parse(options.body).action==='test'){
    mockIntegration={...mockIntegration,pending_auth_me_verified:true};
    result={success:true,integration:{...mockIntegration},
      verification:{identifier:'synthetic.operator',role:'superadmin',source:'pending'}};
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
  // The dialog-company picker only uses same-origin CRM reads and local binding writes.
  const card=window.document.createElement('article');card.id='operatorClientCard';
  const actions=window.document.createElement('div');actions.className='operator-client-card-actions';
  card.append(actions);window.document.body.append(card);
  window.document.dispatchEvent(new window.CustomEvent('tenant-beinsystems-dialog-card',{detail:{
    route:{provider:'connect',channel_id:'channel-synthetic',conversation_id:'dialog-synthetic',customer_kind:'person'},
    card,scope:'scope-synthetic',isCurrent:()=>true,
    member:{tenant_id:profile.member.tenant_id,telegram_id:profile.member.telegram_id},
  }}));
  await new Promise(resolve=>setTimeout(resolve,0));
  const localPicker=card.querySelector('[data-operator-dialog-company]');
  assert(localPicker,'company selector is mounted in the current dialog card');
  assert(localPicker.textContent.includes('не отправляется в Operator'));
  localPicker.querySelector('[data-company-search]').value='Synthetic';
  await localPicker.querySelector('[data-company-search-button]').onclick({
    currentTarget:localPicker.querySelector('[data-company-search-button]'),
  });
  assert.deepEqual(companySearches,['Synthetic']);
  const companyOption=[...localPicker.querySelector('[data-company-results]').querySelectorAll('button')]
    .find(button=>button.textContent==='Synthetic company');
  assert(companyOption);
  await companyOption.onclick({currentTarget:companyOption});
  assert.equal(dialogBinding.company_id,'company-synthetic');
  const bindingPost=requests.filter(row=>row.path==='/api/platform/operator/dialog-company'
    &&row.options.method==='POST').at(-1);
  assert.deepEqual(JSON.parse(bindingPost.options.body),{
    channel_id:'channel-synthetic',conversation_id:'dialog-synthetic',company_id:'company-synthetic',
    expected_tenant_id:profile.member.tenant_id,expected_user_id:String(profile.member.telegram_id),
  });
  dialogBinding={company_id:'company-synthetic',company_name:'Synthetic company',can_manage:false};
  window.document.dispatchEvent(new window.CustomEvent('tenant-beinsystems-dialog-card',{detail:{
    route:{provider:'connect',channel_id:'channel-synthetic',conversation_id:'dialog-synthetic',customer_kind:'person'},
    card,scope:'scope-synthetic-readonly',isCurrent:()=>true,
    member:{tenant_id:profile.member.tenant_id,telegram_id:profile.member.telegram_id},
  }}));
  await new Promise(resolve=>setTimeout(resolve,0));
  const readonlyPicker=card.querySelector('[data-operator-dialog-company]');
  assert(readonlyPicker.textContent.includes('Изменять привязку может владелец workspace.'));
  assert.equal(readonlyPicker.querySelector('[data-company-search]'),null,
    'non-admins can see the local company but cannot change its binding');
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
  authLogin.value='synthetic-login';
  authPassword.value='synthetic-password';
  save.click();
  await new Promise(resolve=>setTimeout(resolve,0));
  const posted=JSON.parse(authPosts().at(-1).options.body);
  assert.deepEqual(posted,{
    action:'login',login:'synthetic-login',password:'synthetic-password',
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
  assert(authPanel.querySelector('[data-test]'),'the documented /user/me check is available');
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
  await testButton.onclick({currentTarget:testButton});
  await new Promise(resolve=>setTimeout(resolve,0));
  assert(authPanel.textContent.includes('synthetic.operator'));
  assert(authPanel.textContent.includes('superadmin'));
  assert(authPanel.textContent.includes('не меняет права Beintaskbot'));
  assert.equal(mockIntegration.status,'not_connected','/user/me alone does not activate the pending token');
  const testPost=requests.filter(row=>row.path==='/api/platform/integrations/beinsystems-operator'
    &&row.options.method==='POST'&&JSON.parse(row.options.body).action==='test').at(-1);
  assert(testPost,'the documented identity check is explicitly invoked');

  // Provider errors appear next to the submit action.
  loginMode='error';
  const errorLogin=authPanel.querySelector('[data-operator-login]');
  const errorPassword=authPanel.querySelector('[data-operator-password]');
  const errorSave=authPanel.querySelector('[data-save]');
  errorLogin.value='synthetic-login';
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
  const readOnlyProfile={member:{...profile.member,role:'manager',permissions:['operator_data:view']},
    integrations:profile.integrations};
  await viewDemand.activate(readOnlyProfile);
  const readOnlyPanel=window.document.querySelector('#operatorIntegrationSettings');
  assert.equal(readOnlyPanel.querySelector('[data-link-controls]').hidden,true);
  assert.equal(readOnlyPanel.querySelector('[data-operator-password]'),null);
  assert.equal(readOnlyPanel.querySelector('[data-create-companies]'),null,
    'the owner-only company action and collision list are not exposed to a read-only member');
  window.close();
  console.log('PASS: Operator credentials stage a private JWT without claiming access confirmation');
})().catch(error=>{window.close();console.error(error);process.exitCode=1;});
