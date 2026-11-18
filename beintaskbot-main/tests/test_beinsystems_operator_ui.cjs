const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const fs=require('node:fs');
const assert=require('node:assert/strict');

const dom=new JSDOM('<!doctype html><div id="integrationSettingsHost"></div>',{
  url:'https://beintaskbot.test/app?view=integrations',runScripts:'outside-only',
});
const {window}=dom;
let viewDemand,requests=[];
let employeeLinks=[];
window.tenantViewDemand={watch(value){viewDemand=value;}};
window.confirm=()=>true;
window.fetch=async(path,options={})=>{
  requests.push({path,options});
  let result;
  if(path==='/api/platform/members')result={success:true,members:[{telegram_id:42,display_name:'Workspace operator',role:'operator',active:true}]};
  else if(path==='/api/platform/operator/employee-users')result={success:true,users:[{login:'operator.login',bindable:true}]};
  else if(path==='/api/platform/operator/employee-links'&&options.method==='POST'){
    const body=JSON.parse(options.body);employeeLinks=[{id:'link-1',member_telegram_id:body.member_telegram_id,account:body.account,login:body.login,status:'confirmed'}];
    result={success:true,link:employeeLinks[0]};
  }
  else if(path==='/api/platform/operator/employee-links')result={success:true,links:employeeLinks};
  else if(options.method==='POST'&&JSON.parse(options.body).action==='login')result={success:true,integration:{
    status:'connected',connected:true,configured:true,api_ready:false,pending_auth_received:true,
    pending_auth_access_confirmed:false,pending_auth_received_at:'2026-11-18T22:00:00+00:00'}};
  else result={success:true,integration:{status:'connected',connected:true,configured:true,api_ready:false,
    pending_auth_received:false,pending_auth_access_confirmed:null}};
  return {ok:true,json:async()=>result};
};
window.eval(fs.readFileSync('docs/tenant-beinsystems-operator-settings.js','utf8'));

(async()=>{
  const profile={member:{tenant_id:'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa',telegram_id:7,role:'owner'},
    integrations:[{provider:'beinsystems_operator'}]};
  await viewDemand.activate(profile);
  const panel=window.document.querySelector('#operatorIntegrationSettings');
  assert(panel,'Operator settings panel is mounted in the existing integrations host');
  const login=panel.querySelector('[data-operator-login]');
  const password=panel.querySelector('[data-operator-password]');
  assert.equal(login.type,'text');
  assert.equal(password.type,'password');
  await new Promise(resolve=>setTimeout(resolve,0));
  const memberSelect=panel.querySelector('[data-employee-member]');
  assert.equal(memberSelect.options.length,2);
  const confirmEmployee=panel.querySelector('[data-confirm-employee]');
  await confirmEmployee.onclick({currentTarget:confirmEmployee});
  assert(panel.textContent.includes('Выберите сотрудника Beintaskbot'));
  assert.equal(requests.filter(row=>row.path==='/api/platform/operator/employee-links'&&row.options.method==='POST').length,0,
    'employee mapping still requires an explicit employee selection');
  login.value='operator-login';
  password.value='operator-password';
  const save=panel.querySelector('[data-save]');
  assert.equal(save.disabled,false,'credential exchange does not depend on API verification host');
  await save.onclick({currentTarget:save});
  const posted=JSON.parse(requests.at(-1).options.body);
  assert.deepEqual(posted,{
    action:'login',login:'operator-login',password:'operator-password',
    expected_tenant_id:profile.member.tenant_id,expected_user_id:profile.member.telegram_id,
  });
  assert.equal(password.value,'','password input is cleared immediately after submission');
  assert.equal(requests.filter(row=>String(row.options.body||'').includes('operator-password')).length,1,
    'credentials are sent only to the authenticated same-origin login endpoint');
  assert(!panel.querySelector('[data-token]'),'the API token is obtained by the server instead of pasted manually');
  assert(!panel.querySelector('[data-test]'),'unverified /user/me is not exposed as a test action');
  assert(panel.textContent.includes('JWT получен, доступ не подтверждён'));
  assert(panel.textContent.includes('JWT получен и зашифрован отдельно'));
  assert(panel.textContent.includes('Пароль отправляется по HTTPS только для получения JWT и не сохраняется'));
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
  const readOnlyProfile={member:{...profile.member,role:'manager',permissions:['operator_data:view']},
    integrations:profile.integrations};
  await viewDemand.activate(readOnlyProfile);
  const readOnlyPanel=window.document.querySelector('#operatorIntegrationSettings');
  assert.equal(readOnlyPanel.querySelector('[data-link-controls]').hidden,true);
  assert.equal(readOnlyPanel.querySelector('[data-operator-password]'),null);
  window.close();
  console.log('PASS: Operator credentials stage a private JWT without claiming access confirmation');
})().catch(error=>{window.close();console.error(error);process.exitCode=1;});
