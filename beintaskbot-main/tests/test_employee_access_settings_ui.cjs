const assert=require('node:assert/strict');
const {mount,settle,writes,edit,input,submit}=require('./employee_card_fixture.cjs');
const crmPages=['tasks','customers','companies','deals'];
const nativeActions={view:true,create:false,update:false,delete:false};
const labels={ru:{open:'По правам: открыта',unavailable:'По правам: недоступна',pending:'По правам: пересчёт'},az:{open:'Hüquqla: açıq',unavailable:'Hüquqla: giriş yoxdur',pending:'Hüquqla: yoxlanacaq'},en:{open:'By rights: open',unavailable:'By rights: unavailable',pending:'By rights: pending'}};
const pageInput=(form,page)=>form.querySelector('[data-access-page='+page+']');
const pageState=(form,page)=>form.querySelector('[data-access-hint='+page+']').dataset.accessState;
const selectedLabel=(form,page)=>pageInput(form,page).selectedOptions[0].textContent;
const access={version:'access-v1',page_catalog:[...crmPages.map(id=>({id,module:id==='companies'?'customers':id})),{id:'finance',module:'finance'},{id:'profile',module:null}],action_catalog:Object.fromEntries(['tasks','customers','deals'].map(module=>[module,Object.keys(nativeActions)])),page_visibility:{},page_prerequisites:{...Object.fromEntries(crmPages.map(id=>[id,true])),finance:false,profile:true},pages:{...Object.fromEntries(crmPages.map(id=>[id,true])),finance:false,profile:true},actions:Object.fromEntries(['tasks','customers','deals'].map(module=>[module,{...nativeActions}]))};
const row={editable_fields:['display_name','role','permissions','active','access_settings'],access_settings:access,permissions:['tasks:view','legacy:retained'],explicit_permissions:['tasks:view','legacy:retained']};
const change=(b,node,value)=>{if(node.type==='checkbox')node.checked=value;else node.value=value;node.dispatchEvent(new b.w.Event('change',{bubbles:true}));};
(async()=>{
 for(const language of ['ru','az','en']){
  const b=await mount({role:'owner',language,row});try{
   const form=edit(b);assert(form.querySelector('[data-access-settings]'));
   for(const page of crmPages){
    assert.equal(pageInput(form,page).value,'inherit','Existing pages retain automatic inheritance');
    assert.equal(selectedLabel(form,page),labels[language].open,'Inherited open state is explicit in '+language);
    assert.equal(pageState(form,page),'visible');
    assert.equal(pageInput(form,page).getAttribute('aria-describedby'),form.querySelector('[data-access-hint='+page+']').id);
   }
   assert.equal(selectedLabel(form,'finance'),labels[language].unavailable);
   assert.equal(pageState(form,'finance'),'unavailable','Missing access is not manual hiding');
   change(b,pageInput(form,'finance'),'true');
   assert.equal(pageState(form,'finance'),'unavailable','Show cannot grant missing authority');
   change(b,pageInput(form,'finance'),'inherit');
   assert.equal(form.querySelector('[name=first_name]').closest('[data-staff-pane]').dataset.staffPane,'details');
   assert.equal(form.querySelector('[name=active]').closest('[data-staff-pane]').dataset.staffPane,'details');
   assert.equal(form.querySelector('[name=role]').closest('[data-staff-pane]').dataset.staffPane,'rights');
   assert.equal(form.querySelector('[data-access-page=tasks]').closest('[data-staff-pane]').dataset.staffPane,'settings');
   assert.equal(form.querySelector('[data-access-action=view]').closest('[data-staff-pane]').dataset.staffPane,'rights');
   const first=form.querySelector('[data-staff-pane-tab=details]');first.dispatchEvent(new b.w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
   assert.equal(form.querySelector('[data-staff-pane-tab=rights]').getAttribute('aria-selected'),'true');
   form.querySelector('[data-staff-pane-tab=settings]').click();assert.equal(form.querySelector('[data-staff-pane=settings]').hidden,false);
assert.equal(form.querySelector('[data-permission][value=tasks]'),null);
   assert(form.querySelector('[data-access-page=profile]').disabled);assert(form.querySelector('[data-access-hint=finance]').textContent);
   assert.equal(form.querySelector('[data-access-action=create]').checked,false);
   submit(b,form);await settle();assert.equal(writes(b).length,0,'No migration or broadening on unchanged save');
   change(b,pageInput(form,'tasks'),'false');assert.equal(pageState(form,'tasks'),'hidden');submit(b,form);await settle();
   assert.deepEqual(writes(b)[0].body.changes,{access_settings:{expected_version:'access-v1',changes:{pages:{tasks:false}}}});
  }finally{b.close()}
 }
 for(const language of ['ru','az','en']){
  // Three provider-backed pages remain open even when native CRM operations are denied.
  const providerAccess={...access,actions:Object.fromEntries(['tasks','customers','deals'].map(module=>[module,{...nativeActions,view:false}])),page_prerequisites:{...access.page_prerequisites,companies:false},pages:{...access.pages,companies:false}};
  const b=await mount({role:'owner',language,row:{...row,access_settings:providerAccess}});try{
   const original=JSON.stringify(providerAccess),form=edit(b);
   assert([...form.querySelectorAll('[data-access-action=view]')].every(node=>!node.checked));
   for(const page of ['tasks','customers','deals']){assert.equal(pageState(form,page),'visible');assert.equal(selectedLabel(form,page),labels[language].open)}
   assert.equal(pageState(form,'companies'),'unavailable','Native-only company page follows its own server state');
   submit(b,form);await settle();assert.equal(writes(b).length,0,'Provider-backed no-op save does not materialize grants');
   const view=form.querySelector('[data-access-action=view][data-access-module=tasks]');
   change(b,view,true);assert.equal(pageState(form,'tasks'),'pending');assert.equal(selectedLabel(form,'tasks'),labels[language].pending);
   change(b,pageInput(form,'tasks'),'false');assert.equal(pageState(form,'tasks'),'hidden','Manual hide is clear even with pending rights');
   change(b,pageInput(form,'tasks'),'inherit');assert.equal(pageState(form,'tasks'),'pending');
   change(b,view,false);assert.equal(pageState(form,'tasks'),'visible','Undo restores the provider-backed snapshot');
   submit(b,form);await settle();assert.equal(writes(b).length,0);assert.equal(JSON.stringify(providerAccess),original);
  }finally{b.close()}
  const hidden=await mount({role:'owner',language,row:{...row,access_settings:{...access,page_visibility:{tasks:false,finance:false},pages:{...access.pages,tasks:false}}}});try{
   const form=edit(hidden);assert.equal(pageInput(form,'tasks').value,'false');assert.equal(pageState(form,'tasks'),'hidden');
   assert.equal(pageState(form,'finance'),'hidden','An explicit hide is not presented as missing permission');
   submit(hidden,form);await settle();assert.equal(writes(hidden).length,0,'Existing false overrides survive no-op');
   change(hidden,pageInput(form,'tasks'),'inherit');assert.equal(selectedLabel(form,'tasks'),labels[language].open);assert.equal(pageState(form,'tasks'),'visible');
   change(hidden,pageInput(form,'finance'),'inherit');assert.equal(pageState(form,'finance'),'unavailable','Restoring inheritance does not grant rights');
   submit(hidden,form);await settle();assert.deepEqual(writes(hidden)[0].body.changes,{access_settings:{expected_version:'access-v1',changes:{pages:{tasks:null,finance:null}}}});
  }finally{hidden.close()}
 }
 {
  const b=await mount({role:'owner',row:{...row,permission_modules:['finance'],editable_fields:[...row.editable_fields,'permission_changes','work_settings'],work_settings:{version:'work-v1',values:{},defaults:{},notifications:[],services:[]}}});try{
   const form=edit(b);
   for(const [selector,value,original] of [
    ['[name=role]','manager','worker'],['[name=active]',false,true],
    ['[data-permission][value=finance]',true,false],['[data-work-setting=ai_agents_manage]','true','inherit'],
    ['[data-access-action=update][data-access-module=tasks]',true,false]
   ]){
    change(b,form.querySelector(selector),value);assert.equal(pageState(form,'tasks'),'pending',selector+' invalidates the saved authority snapshot');
    assert.equal(selectedLabel(form,'tasks'),labels.en.pending);assert.equal(pageState(form,'profile'),'visible','Profile stays available');
    change(b,form.querySelector(selector),original);assert.equal(pageState(form,'tasks'),'visible',selector+' can be undone');
   }
   submit(b,form);await settle();assert.equal(writes(b).length,0,'Undoing all authority edits stays a no-op');
  }finally{b.close()}
 }
 {
  const b=await mount({role:'owner',row:{...row,access_settings:{...access,page_visibility:{finance:false}}}});try{
   const form=edit(b);change(b,pageInput(form,'tasks'),'false');change(b,pageInput(form,'customers'),'false');change(b,pageInput(form,'finance'),'inherit');
   change(b,form.querySelector('[data-access-action=update][data-access-module=tasks]'),true);submit(b,form);await settle();
   assert.deepEqual(writes(b)[0].body.changes,{access_settings:{expected_version:'access-v1',changes:{pages:{tasks:false,customers:false,finance:null},actions:{tasks:{update:true}}}}},'Mixed save includes only changed preferences/operations and retains sparse inheritance');
  }finally{b.close()}
 }
 {
  const b=await mount({role:'owner',row});try{const form=edit(b);change(b,form.querySelector('[data-access-action=update]'),true);submit(b,form);await settle();assert.deepEqual(writes(b)[0].body.changes,{access_settings:{expected_version:'access-v1',changes:{actions:{tasks:{update:true}}}}});}finally{b.close()}
 }
 {
  const b=await mount({role:'owner',row:{...row,access_settings:{...access,page_visibility:{tasks:false},pages:{...access.pages,tasks:false}}}});try{const form=edit(b);change(b,form.querySelector('[data-access-page=tasks]'),'inherit');submit(b,form);await settle();assert.deepEqual(writes(b)[0].body.changes.access_settings.changes,{pages:{tasks:null}})}finally{b.close()}
 }
 {
  const b=await mount({role:'owner',row:{...row,role:'owner'}});try{const form=edit(b);assert(form.querySelector('[data-access-page=tasks]').disabled);assert(form.querySelector('[data-access-action=view]').closest('fieldset').disabled);submit(b,form);await settle();assert.equal(writes(b).length,0)}finally{b.close()}
 }
 {
  const b=await mount({role:'owner',row:{...row,password_assign_available:true,email_assign_available:true}});try{const form=edit(b);change(b,form.querySelector('[data-access-page=tasks]'),'false');input(b,form,'new_email','new@isolated.example');b.f.writeResult={status:200,data:{success:true,applied:{telegram_id:22,card_version:'email-v2',email_changed:true}}};form.querySelector('#staffAssignEmail').click();await settle();b.f.confirmResult=false;b.d.querySelector('[data-staff-close]').click();assert(b.d.getElementById('staffMemberModal').classList.contains('show'));submit(b,form);await settle();assert.deepEqual(writes(b)[1].body.changes,{access_settings:{expected_version:'access-v1',changes:{pages:{tasks:false}}}});assert.equal(writes(b)[1].body.expected_version,'email-v2')}finally{b.close()}
 }
 {
  const b=await mount({role:'owner',row});try{
   b.f.profile.capabilities.modules.tasks=false;b.f.profile.capabilities.pages={tasks:true,deals:true,employees:true,profile:true};b.f.profile.capabilities.actions={tasks:{view:true},deals:{view:true,create:false,update:false,delete:false},customers:{view:false}};
   b.w.tenantRequestCoordinator.reset();await b.w.tenantReloadProfile();await settle();
   assert.equal(b.f.profile.capabilities.modules.tasks,false,'Navigation must not rewrite module authority');
   assert.equal(b.d.querySelector('.nav [data-view=tasks]').hidden,false,'Native tasks:view page visible without coarse module');
   assert.equal(b.w.tenantAppNavigate('tasks'),true);
   assert.equal(b.w.tenantAppNavigate('deals'),true);await settle();assert(b.f.calls.some(call=>call.path==='/api/platform/crm/native/deals'));
   assert.equal(b.d.querySelector('[data-native-operation=view]').hidden,false);assert.equal(b.d.getElementById('nativeDealCreate').hidden,true);
   assert.equal(b.d.querySelector('[data-app-instructions]').dataset.pageHidden,'true');
   assert.equal(b.d.querySelector('[data-app-open=settings]').dataset.pageHidden,'true');
   b.f.profile.capabilities.pages.tasks=false;b.f.profile.capabilities.page_visibility={tasks:false};b.w.tenantRequestCoordinator.reset();await b.w.tenantReloadProfile();await settle();
   assert.equal(b.d.querySelector('.nav [data-view=tasks]').hidden,true);assert.equal(b.w.tenantAppNavigate('tasks'),false);
   b.w.history.pushState({},'','/app?view=tasks');b.w.dispatchEvent(new b.w.PopStateEvent('popstate'));await settle();
   assert(!b.d.getElementById('view-tasks').classList.contains('active'),'Hidden deep-link cannot bypass preference');
   assert.equal(b.w.tenantAppNavigate('profile'),true);
   const task=b.d.querySelector('.nav [data-view=tasks]');task.hidden=false;task.style.display='';await settle();assert(task.hidden,'Late module controller cannot resurrect hidden page');
   b.d.getElementById('view-tasks').classList.add('active');await settle();assert(!b.d.getElementById('view-tasks').classList.contains('active'),'Direct late activation is blocked');
   const dynamic=b.d.createElement('button');dynamic.dataset.view='ai_testing';b.d.querySelector('.nav').append(dynamic);await settle();assert.equal(dynamic.dataset.pageHidden,'true');
   b.f.profile.capabilities.pages.employees=false;b.f.profile.capabilities.can_manage_employees=false;
   b.w.tenantRequestCoordinator.reset();await b.w.tenantReloadProfile();await settle();
   const beforeReads=b.f.calls.filter(call=>call.path==='/api/platform/members').length;
   assert.equal(b.w.tenantAppNavigate('employees'),true,'Unavailable employee route retains its explanatory shell');await settle();
   assert.equal(b.d.getElementById('employeesAccessNotice').hidden,false);
   assert.equal(b.d.getElementById('staffManagement').hidden,true);
   assert.equal(b.d.querySelector('.nav [data-view=employees]').hidden,true);
   assert.equal(b.f.calls.filter(call=>call.path==='/api/platform/members').length,beforeReads,'Denied shell must not load employee data');
   assert.equal(b.errors.length,0,b.errors.join('\n'));
  }finally{b.close()}
 }
 console.log('PASS: RU/AZ/EN inherited effective visibility, provider/native separation, pending authority changes, sparse/no-op saves and navigation guards');
})().catch(error=>{console.error(error.stack);process.exitCode=1});
