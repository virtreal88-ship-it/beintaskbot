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
  else result=options.method==='POST'
    ?{success:true,integration:{status:JSON.parse(options.body).action==='login'?'connected':'configured',api_ready:true}}
    :{success:true,integration:{status:'not_connected',api_ready:true}};
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
  assert(panel.textContent.includes('Сначала подключите Operator'));
  assert.equal(requests.filter(row=>row.path==='/api/platform/operator/employee-links'&&row.options.method==='POST').length,0,
    'employee mapping is blocked until the Operator connection is verified');
  login.value='operator-login';
  password.value='operator-password';
  const save=panel.querySelector('[data-save]');
  await save.onclick({currentTarget:save});
  const posted=JSON.parse(requests.at(-1).options.body);
  assert.deepEqual(posted,{
    action:'login',login:'operator-login',password:'operator-password',
    expected_tenant_id:profile.member.tenant_id,expected_user_id:profile.member.telegram_id,
  });
  assert.equal(password.value,'','password input is cleared immediately after submission');
  assert.equal(requests.filter(row=>String(row.options.body||'').includes('operator-password')).length,1,
    'credentials are sent only to the authenticated same-origin login endpoint');
  assert(!panel.querySelector('[data-token]'),'the SSO token is obtained by the server instead of pasted manually');
  assert(panel.textContent.includes('Пароль используется только для получения токена и не сохраняется'));
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
  console.log('PASS: Operator login/password fields connect through SSO and clear the password input');
})().catch(error=>{window.close();console.error(error);process.exitCode=1;});
