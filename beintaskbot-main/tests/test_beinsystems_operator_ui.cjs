const assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
const integration='/api/platform/integrations/beinsystems-operator';
const owner={member:{tenant_id:'tenant',telegram_id:33,role:'owner'},integrations:[{provider:'beinsystems_operator'}]};
const empty={status:'not_connected',connected:false,configured:false};
const connected={status:'connected',connected:true,configured:true};
(async()=>{
 const dom=new JSDOM('<div id="operatorConnectionStatusHost"></div><div id="integrationSettingsHost"></div>',{
  url:'https://crm.test/app?view=integrations',runScripts:'outside-only'
 });
 const w=dom.window,d=w.document,calls=[];
 const at=s=>d.querySelector(s), text=s=>at(s).textContent;
 const post=(path,action)=>calls.filter(row=>row.path===path&&row.body&&(!action||row.body.action===action));
 const response=(value,status=200)=>({ok:status===200,status,json:async()=>value});
 let watch,state={...empty},confirmAnswer=false,loginPending=false,loadFailure=true,saveFailure=false;
 let listRevision=0, linksFailure=false;
 let memberLinks=[{id:'existing-1',member_telegram_id:42,account:'existing-account',login:'existing-user',status:'confirmed'}];
 w.tenantViewDemand={watch(value){watch=value;}};
 w.confirm=()=>confirmAnswer;
 w.fetch=async(path,opt={})=>{
  const body=opt.body?JSON.parse(opt.body):null;calls.push({path,body,opt});
  if(path.split('?')[0]===integration&&!body){
   const projection={...state};if(!path.includes('?'))delete projection.connected_login;
   return response({success:true,integration:projection});
  }
  if(path===integration&&body.action==='login'){
   if(loginPending)return new Promise(resolve=>{loginPending=resolve;});
   state={...state,pending_auth_received:true,pending_auth_received_at:'rev-1'};
   return response({success:true,integration:state});
  }
  if(path===integration&&body.action==='prepare_accounts_capability')
   return response({success:true,accounts_only_confirmation:{identifier:'owner@example.test',candidate_received_at:'rev-1'}});
  if(path===integration&&body.action==='confirm_accounts_capability'){
   state={...state,accounts_only_ready:true};
   return response({success:true,integration:state});
  }
  if(path==='/api/platform/operator/accounts'&&body?.action==='import'){
   state={...connected,connected_login:'verified@example.test'};return response({success:true,integration:connected,activated:true,identity:{identifier:'ignore-legacy-result@example.test'}});
  }
  if(path==='/api/platform/members')return response({success:true,members:[
   {telegram_id:42,display_name:'Synthetic',role:'operator',active:true},{telegram_id:43,active:false}]});
  if(path==='/api/platform/operator/employee-users'){
   if(loadFailure)return response({success:false,error:'Operator: загрузка не удалась'},403);
   return response({success:true,selection_revision:'opaque-'+(++listRevision),users:[{login:'from-operator',bindable:true},{login:'unavailable',bindable:false}]});
  }
  if(path==='/api/platform/operator/employee-links'&&!body){
   if(linksFailure)return response({success:false,error:'Operator: связи не загружены'},403);
   return response({success:true,links:memberLinks});
  }
  if(path==='/api/platform/operator/employee-links'&&body){
   if(saveFailure)return response({success:false,error:'Operator: связь не сохранена'},409);
   memberLinks=[...memberLinks,{id:'map-2',member_telegram_id:42,account:'server-selected',login:body.login,status:'confirmed'}];
   return response({success:true});
  }
  return response({success:false,error:'Unexpected mock route'},404);
 };
 w.eval(fs.readFileSync('docs/tenant-beinsystems-operator-settings.js','utf8'));
 await watch.activate(owner);await tick();
 const obsolete=['[data-test]','[data-catalog]','[data-import]','[data-disconnect]','[data-create-companies]',
  '[data-first-company-batch-plan]','[data-search]','[data-account-info]','[data-link-controls]','[data-verify-public-link]'];
 for(const selector of obsolete)assert.equal(at(selector),null,selector+' must not be mounted');
 assert(!at('#operatorIntegrationSettings').textContent.includes('Документация'));
 assert.equal(post(integration).length,0,'opening the card must not initiate connection actions');
 assert.equal(calls[0].path,integration+'?include_connected_login=1&expected_tenant_id=tenant&expected_user_id=33');
 assert.equal(text('[data-status]'),'Не подключено');
 assert.equal(at('#operatorIntegrationSettings [data-status]'),null);
 assert.equal(at('#operatorIntegrationSettings h2'),null);
 assert(at('#operatorConnectionStatusHost [data-status]'));
 assert(at('[data-connection-next]').hidden);
 const login=at('[data-operator-login]'),password=at('[data-operator-password]');
 assert.equal(login.autocomplete,'off');assert.equal(password.autocomplete,'new-password');
 at('[data-save]').click();assert.equal(post(integration,'login').length,0);
 login.value=' entered@example.test ';password.value='synthetic-password';
 const passwordToggle=at('[data-show-password]');
 assert.equal(passwordToggle.textContent.trim(),'');
 assert.equal(passwordToggle.getAttribute('aria-label'),'Показать пароль');
 assert.equal(passwordToggle.getAttribute('aria-pressed'),'false');
 assert.ok(passwordToggle.querySelector('svg[aria-hidden="true"]'));
 passwordToggle.click();assert.equal(password.type,'text');
 assert.equal(passwordToggle.getAttribute('aria-label'),'Скрыть пароль');
 assert.equal(passwordToggle.getAttribute('aria-pressed'),'true');
 passwordToggle.click();assert.equal(password.type,'password');
 assert.equal(passwordToggle.getAttribute('aria-label'),'Показать пароль');
 assert.equal(passwordToggle.getAttribute('aria-pressed'),'false');
 at('[data-show-password]').click();at('[data-save]').click();at('[data-save]').click();
 assert.equal(password.type,'password');assert.equal(password.value,'');
 await tick();
 assert.equal(login.value,' entered@example.test ');
 assert.deepEqual(post(integration,'login').map(r=>r.body),[{
  action:'login',login:'entered@example.test',password:'synthetic-password',expected_tenant_id:'tenant',expected_user_id:33
 }]);
 assert(!at('#operatorIntegrationSettings').textContent.includes('synthetic-password'));
 assert.equal(text('[data-status]'),'Не подключено');
 assert.equal(text('[data-auth-message]'),'');
 assert(!at('[data-connection-next]').hidden);
 assert.equal(post('/api/platform/operator/accounts').length,0,'login never automatically imports or activates');
 at('[data-finish-connection]').click();await tick();assert.equal(post('/api/platform/operator/accounts').length,0);
 at('[data-accounts-only]').click();await tick();
 assert.match(text('[data-identity-confirmation]'),/owner@example\.test/);
 at('[data-identity-confirmation] button:nth-of-type(2)').click();
 assert.equal(post(integration,'confirm_accounts_capability').length,0);
 at('[data-accounts-only]').click();await tick();
 at('[data-identity-confirmation] button').click();await tick();
 assert.equal(text('[data-status]'),'Подключено');
 assert.equal(at('[data-status]').dataset.connected,'true');
 assert(!at('[data-employee-user]').disabled);
 assert(!at('[data-employee-member]').disabled);
 assert(at('[data-confirm-employee]').disabled);
 assert.equal(post('/api/platform/operator/employee-users').length,0,'no staff prefetch for accounts-only access');
 at('[data-employee-user]').dispatchEvent(new w.Event('pointerdown'));await tick();
 assert.equal(text('[data-employee-message]'),'Operator: загрузка не удалась');
 assert.equal(post('/api/platform/operator/employee-users').length,1);
 assert(at('[data-confirm-employee]').disabled);
 assert.equal(post(integration,'confirm_accounts_capability').length,1);
 confirmAnswer=true;at('[data-finish-connection]').click();await tick();
 assert.equal(text('[data-status]'),'Подключено');
 assert.equal(text('[data-connected-login]'),'verified@example.test');
 assert.equal(text('[data-auth-message]'),'');
 assert(at('[data-connection-next]').hidden);

 assert.match(text('[data-employee-links]'),/existing-user ↔ Synthetic/);
 const staff=at('[data-employee-member]'),operator=at('[data-employee-user]');
 const message=at('[data-employee-message]'),bind=at('[data-confirm-employee]');
 assert(at('[data-employee-controls]').contains(message));assert.equal(staff.options.length,2);
 assert.deepEqual([...at('[data-employee-form]').querySelectorAll('select')],[operator,staff]);
 assert.deepEqual([...at('[data-employee-controls]').querySelectorAll('button')],[bind]);
 for(const selector of ['[data-employee-account]','[data-load-employee-users]','[data-employee-evidence]'])assert.equal(at(selector),null);
 assert.equal(bind.textContent,'Привязать');
 assert.equal(post('/api/platform/operator/employee-users').length,2);
 assert.equal(message.textContent,'Operator: загрузка не удалась');
 operator.dispatchEvent(new w.Event('pointerdown'));await tick();
 assert.equal(d.activeElement,message);
 loadFailure=false;
 operator.dispatchEvent(new w.Event('pointerdown'));await tick();
 assert.deepEqual(post('/api/platform/operator/employee-users').at(-1).body,
  {expected_tenant_id:'tenant',expected_user_id:33});
 assert.equal(operator.options.length,3);
 operator.dispatchEvent(new w.Event('pointerdown'));await tick();
 assert.equal(post('/api/platform/operator/employee-users').length,4,'reuse a loaded list');
 operator.value='unavailable';staff.value='42';staff.dispatchEvent(new w.Event('change'));bind.click();await tick();
 assert.equal(post('/api/platform/operator/employee-links').length,0);
 operator.value='from-operator';operator.dispatchEvent(new w.Event('change'));
 saveFailure=true;bind.click();await tick();
 assert.equal(message.textContent,'Operator: связь не сохранена');assert.equal(d.activeElement,message);
 assert.equal(operator.value,'','a 409 clears a stale selection');
 assert.equal(listRevision,2,'refreshes the dropdown without retrying the write');
 assert.equal(post('/api/platform/operator/employee-links').length,1);
 saveFailure=false;operator.value='from-operator';operator.dispatchEvent(new w.Event('change'));bind.click();await tick();
 assert.deepEqual(post('/api/platform/operator/employee-links').at(-1).body,
  {member_telegram_id:42,login:'from-operator',selection_revision:'opaque-2',expected_tenant_id:'tenant',expected_user_id:33});
 assert.equal(message.textContent,'');
 assert.match(text('[data-employee-links]'),/from-operator ↔ Synthetic/);
 assert(!text('[data-employee-links]').includes('server-selected'));

 loginPending=true;login.value='replacement@example.test';password.value='replacement';
 at('[data-save]').click();assert.equal(text('[data-status]'),'Подключено','a pending replacement preserves the current connection');
 const settleLogin=loginPending;watch.reset();await watch.activate({...owner,member:{...owner.member,role:'operator'}});await tick();
 settleLogin(response({success:true,integration:{...empty,pending_auth_received:true}}));await tick();
 assert.equal(text('[data-status]'),'Подключено','a stale owner response cannot replace the new card');
 assert.equal(at('[data-operator-auth-form]'),null);
 assert.equal(at('[data-employee-controls]'),null);
 assert.equal(calls.at(-1).path,integration,'non-owner does not request active login identity');
 state={...connected,connected_login:'confirmed-after-reload@example.test'};
 await watch.activate(owner);await tick();
 assert.match(text('[data-connected-login]'),/confirmed-after-reload@example\.test/);
 assert.equal(at('[data-operator-login]').value,'','never prefill or overwrite entered credentials');
 linksFailure=true;state={...empty,accounts_only_ready:true};
 await watch.activate(owner);await tick();
 assert.equal(at('[data-employee-member]').options.length,2,'CRM picker loads even if link history fails');
 assert.equal(at('[data-confirm-employee]').disabled,true,'no Bind without a verified pair');
 assert(!calls.some(row=>/^https?:/.test(row.path)));
 dom.window.close();console.log('PASS: Operator connection, employee mapping and minimal card');
})().catch(error=>{console.error(error);process.exitCode=1;});
