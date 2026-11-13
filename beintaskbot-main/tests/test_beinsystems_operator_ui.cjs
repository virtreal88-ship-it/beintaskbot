const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const fs=require('node:fs');
const assert=require('node:assert/strict');

const dom=new JSDOM('<!doctype html><div id="integrationSettingsHost"></div>',{
  url:'https://beintaskbot.test/app?view=integrations',runScripts:'outside-only',
});
const {window}=dom;
let viewDemand,requests=[];
window.tenantViewDemand={watch(value){viewDemand=value;}};
window.confirm=()=>true;
window.fetch=async(path,options={})=>{
  requests.push({path,options});
  const result=options.method==='POST'
    ?{success:true,integration:{status:'configured',api_ready:true}}
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
  login.value='operator-login';
  password.value='operator-password';
  panel.querySelector('[data-token]').value='sso-jwt';
  const save=panel.querySelector('[data-save]');
  await save.onclick({currentTarget:save});
  const posted=JSON.parse(requests.at(-1).options.body);
  assert.deepEqual(posted,{
    action:'connect',token:'sso-jwt',
    expected_tenant_id:profile.member.tenant_id,expected_user_id:profile.member.telegram_id,
  });
  assert(!JSON.stringify(requests).includes('operator-password'));
  assert(panel.textContent.includes('Эти поля не отправляются и не сохраняются'));
  const readOnlyProfile={member:{...profile.member,role:'manager',permissions:['operator_data:view']},
    integrations:profile.integrations};
  await viewDemand.activate(readOnlyProfile);
  const readOnlyPanel=window.document.querySelector('#operatorIntegrationSettings');
  assert.equal(readOnlyPanel.querySelector('[data-link-controls]').hidden,true);
  assert.equal(readOnlyPanel.querySelector('[data-operator-password]'),null);
  window.close();
  console.log('PASS: Operator login/password fields are present and never submitted without an auth contract');
})().catch(error=>{window.close();console.error(error);process.exitCode=1;});
