const assert=require('node:assert/strict');
const {mount,settle,writes,edit,submit}=require('./employee_card_fixture.cjs');
const actions={view:true,create:false,update:false,delete:false};
const access={version:'access-v1',pages:{tasks:true,customers:true,companies:true,deals:true,profile:true},action_catalog:Object.fromEntries(['tasks','customers','deals'].map(module=>[module,Object.keys(actions)])),actions:Object.fromEntries(['tasks','customers','deals'].map(module=>[module,{...actions}]))};
const row={editable_fields:['display_name','role','permissions','permission_changes','active','access_settings','work_settings'],permission_modules:['finance'],access_settings:access,permissions:['tasks:view','finance:view','legacy:retained'],explicit_permissions:['tasks:view','finance:view','legacy:retained'],work_settings:{version:'work-v1',values:{},defaults:{},services:[],notifications:[{key:'task_assigned',label:'Task',channels:['push']}]}};
const change=(b,node,value)=>{assert(node);if(node.type==='checkbox')node.checked=value;else node.value=value;node.dispatchEvent(new b.w.Event('change',{bubbles:true}));};
(async()=>{
 for(const language of ['ru','az','en']){
  const b=await mount({role:'owner',language,row});try{
   const form=edit(b);
   assert.equal(form.querySelectorAll('[role=tab]').length,2);
   assert.equal(form.querySelector('[data-staff-pane=settings]'),null);
   assert.equal(form.querySelector('[data-access-page],[data-access-pages],[data-access-hint]'),null);
   for(const selector of ['[data-access-action]','[data-permission]','[data-work-setting]','[data-work-services]','[data-work-event]'])assert.equal(form.querySelector(selector).closest('[data-staff-pane]').dataset.staffPane,'rights',selector);
   for(const module of ['tasks','customers','deals'])assert.equal(form.querySelectorAll('[data-access-action][data-access-module='+module+']').length,4);
   for(const card of form.querySelectorAll('.staff-section-permission')){assert.equal(card.querySelectorAll('[data-permission]').length,1);assert.equal(card.querySelectorAll('[data-access-action]').length,0,'No fictitious CRUD');}
   const finance=form.querySelector('[data-permission][value=finance]');assert(finance.indeterminate,'Partial grants stay visibly partial');
   assert.equal(form.querySelector('[data-permission][value=tasks]'),null,'Native and broad grants are not duplicated');
   form.querySelector('[data-staff-pane-tab=details]').dispatchEvent(new b.w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));assert.equal(form.querySelector('[data-staff-pane-tab=rights]').getAttribute('aria-selected'),'true');
   form.querySelector('[data-staff-pane-tab=rights]').dispatchEvent(new b.w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));assert.equal(form.querySelector('[data-staff-pane-tab=details]').getAttribute('aria-selected'),'true');
   submit(b,form);await settle();assert.equal(writes(b).length,0,'No migration, normalization or rights expansion on unchanged save');
   change(b,form.querySelector('[data-access-action=update][data-access-module=tasks]'),true);submit(b,form);await settle();
   assert.deepEqual(writes(b)[0].body.changes,{access_settings:{expected_version:'access-v1',changes:{actions:{tasks:{update:true}}}}},'Only the changed real action is written');
  }finally{b.close()}
 }
 {
  const b=await mount({role:'owner',row});try{
   const form=edit(b);change(b,form.querySelector('[data-work-setting=completion_requires_admin]'),'true');change(b,form.querySelector('[data-work-event=task_assigned]'),true);change(b,form.querySelector('[data-permission][value=support]'),true);submit(b,form);await settle();
   assert.deepEqual(writes(b)[0].body.changes,{permission_changes:{support:true},work_settings:{expected_version:'work-v1',changes:{completion_requires_admin:true,notifications:{task_assigned:{push:true}}}}},'Work rules and notifications remain sparse, retaining unrelated partial and legacy grants');
  }finally{b.close()}
 }
 {
  const b=await mount({role:'owner',row:{...row,role:'owner'}});try{
   const form=edit(b);assert(form.querySelector('[data-access-action=view]').disabled);assert(form.querySelector('[data-permission]').disabled);submit(b,form);await settle();assert.equal(writes(b).length,0);
  }finally{b.close()}
 }
 console.log('PASS RU/AZ/EN unified rights: two tabs, real actions, no page preferences, partial/legacy retention and sparse work rules');
})().catch(error=>{console.error(error.stack);process.exitCode=1});
