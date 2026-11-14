const assert=require('node:assert/strict');
const {mount,settle,writes,edit,submit}=require('./employee_card_fixture.cjs');
const department='10000000-0000-4000-8000-000000000501';
const access={version:'scoped-v1',scoped_access_enabled:true,scope_catalog:{tasks:['view','create','update','delete'],customers:['view','create','update','delete'],companies:['view','create','update','delete'],deals:['view','create','update','delete'],dialogs:['view','update'],night_report:['view']},scoped_actions:{tasks:{view:'department',create:'self',update:'self',delete:'none'},customers:{view:null},companies:{view:'none'},deals:{view:'all'},dialogs:{view:'self',update:'department'},night_report:{view:'all'}},legacy_actions:{customers:{view:true}},actions:{},pages:{tasks:true}};
const row={department_id:null,department_catalog:[{id:department,name:'Support',active:true}],editable_fields:['display_name','role','permissions','permission_changes','active','access_settings','work_settings','department_id'],access_settings:access,permissions:['tasks','finance:view','legacy:retained'],explicit_permissions:['tasks','finance:view','legacy:retained'],work_settings:{version:'work-v1',values:{ai_enabled:true,hot_order_services:['retained-service'],notifications:{linear_done:{push:true}}},defaults:{},services:[],notifications:[{key:'task_assigned',label:'Task',channels:['push']}]}};
const change=(b,node,value)=>{assert(node);node.value=value;node.dispatchEvent(new b.w.Event('change',{bubbles:true}));};
(async()=>{
 for(const language of ['ru','az','en']){
  const b=await mount({role:'owner',language,row});try{
   let opened;b.d.addEventListener('tenant-member-card-open',e=>opened=e.detail);
   const form=edit(b);
   assert.equal(form.querySelectorAll('[role=tab]').length,3);
   assert.equal(form.querySelectorAll('[data-access-action],[data-permission],[data-access-page]').length,0,'No competing broad, boolean, or visibility editor');
   const section=form.querySelector('.staff-scope-matrix');assert(section);
   assert.equal(section.querySelectorAll('[data-access-scope]').length,19);
   assert.equal(section.querySelectorAll('[data-scope-row=dialogs] select').length,2,'No fictional create/delete for dialogs');
   assert.equal(section.querySelectorAll('[data-scope-row=night_report] select').length,1,'A report has no fictional mutations');
   assert.equal(section.querySelector('[data-scope-row=news],[data-scope-row=instructions]'),null);
   assert.equal(section.querySelector('[data-access-module=tasks][data-access-scope=view]').value,'department');
   assert.equal(section.querySelector('[data-access-module=customers][data-access-scope=view]').value,'legacy','Legacy grant never inferred as all');
   assert.deepEqual([...section.querySelector('[data-access-module=tasks][data-access-scope=view]').options].map(o=>o.value),['none','self','department','all']);
   assert(form.querySelector('[data-work-setting=ai_enabled]'));
   assert(form.querySelector('[data-work-services] option[value="retained-service"]').selected);
   assert.equal(form.querySelector('[name=department_id]').closest('[data-staff-pane]').dataset.staffPane,'details');
   assert(opened.container&&opened.isCurrent());
   submit(b,form);await settle();assert.equal(writes(b).length,0);
   change(b,form.querySelector('[data-access-module=tasks][data-access-scope=delete]'),'self');
   change(b,form.querySelector('[name=department_id]'),department);
   submit(b,form);await settle();
   assert.deepEqual(writes(b)[0].body.changes,{department_id:department,access_settings:{expected_version:'scoped-v1',changes:{scopes:{tasks:{delete:'self'}}}}});
  }finally{b.close()}
 }
 {
  const b=await mount({role:'owner',row:{...row,role:'owner'}});try{const form=edit(b);assert([...form.querySelectorAll('[data-access-scope]')].every(n=>n.disabled));submit(b,form);await settle();assert.equal(writes(b).length,0)}finally{b.close()}
 }
 {
  const b=await mount({role:'owner',row});try{
   const form=edit(b);b.f.writeResult={status:200,data:{success:true,department:{id:'10000000-0000-4000-8000-000000000502',name:'New team',active:true,version:'d1'}}};
   form.querySelector('[data-department-add]').click();change(b,form.querySelector('[data-department-name]'),'New team');form.querySelector('[data-department-save]').click();form.querySelector('[data-department-save]').click();await settle();
   assert.equal(writes(b).length,1,'Double click creates once');assert.equal(writes(b)[0].body.action,'create_department');assert.equal(form.elements.namedItem('department_id').value,'10000000-0000-4000-8000-000000000502');assert.equal(writes(b).filter(c=>c.body.action==='update_member_card').length,0,'Creating catalog entry does not silently assign membership');
  }finally{b.close()}
 }
 {
  const b=await mount({role:'owner',row});try{
   const form=edit(b);b.f.writeResult={status:409,data:{success:false,code:'member_settings_conflict',fields:{access_settings:'version_conflict'}}};change(b,form.querySelector('[data-access-module=tasks][data-access-scope=view]'),'self');submit(b,form);await settle();assert.equal(form.querySelector('[data-access-module=tasks][data-access-scope=view]').value,'self','Conflict retains draft');assert.equal(writes(b).length,1);
  }finally{b.close()}
 }
 {
  const b=await mount({role:'owner',row:{...row,permission_modules:['linear','hot_orders','settings','finance','dialogs'],explicit_permissions:['linear:view','legacy:retained']}});try{
   const form=edit(b),controls=[...form.querySelectorAll('[data-permission]')];
   assert.deepEqual(controls.map(n=>n.value),['linear','hot_orders'],'Only nonmatrix, non-owner-only modules get coarse controls');
   assert(controls[0].indeterminate,'Partial Linear grant stays partial until explicitly changed');
   submit(b,form);await settle();assert.equal(writes(b).length,0,'Untouched coarse grants are preserved');
   controls[0].checked=false;controls[0].dispatchEvent(new b.w.Event('change',{bubbles:true}));
   controls[1].checked=true;controls[1].dispatchEvent(new b.w.Event('change',{bubbles:true}));
   change(b,form.querySelector('[data-access-module=tasks][data-access-scope=delete]'),'self');
   submit(b,form);await settle();
   assert.deepEqual(writes(b)[0].body.changes,{permission_changes:{linear:false,hot_orders:true},access_settings:{expected_version:'scoped-v1',changes:{scopes:{tasks:{delete:'self'}}}}});
  }finally{b.close()}
 }
 {
  const b=await mount({role:'owner',row:{...row,role:'owner',permission_modules:['linear','hot_orders']}});try{const form=edit(b);assert([...form.querySelectorAll('[data-permission]')].every(n=>n.disabled),'Owner coarse grants are protected')}finally{b.close()}
 }
 console.log('PASS scoped matrix RU/AZ/EN, independent CRUD, legacy preservation, department selection/create, source slot, owner protection and conflict draft');
})().catch(error=>{console.error(error.stack);process.exitCode=1});
