const assert=require('node:assert/strict');
const {mount,settle,writes,edit,input,submit}=require('./employee_card_fixture.cjs');
const settings={version:'settings-v1',values:{ai_enabled:false,hot_order_services:['gone'],notifications:{task_assigned:{push:false}}},defaults:{ai_enabled:false},services:[{id:'install',name:'Install',active:true}],notifications:[{key:'task_assigned',label:'Assigned',channels:['push','telegram']},{key:'linear_done',label:'Linear',channels:['push','telegram']},{key:'proactive_risk',label:'Risk',channels:['push','internal']}]};
const row={permission_modules:['finance','employees'],editable_fields:['display_name','role','permissions','active','whatsapp_numbers','permission_changes','work_settings'],work_settings:settings,explicit_permissions:['tasks','finance:manage','legacy:retained'],permissions:['tasks','finance:manage','legacy:retained']};
(async()=>{
for(const language of ['ru','az','en']){
 const b=await mount({role:'owner',language,row});try{
  const form=edit(b);assert(form.querySelector('[data-permission][value=finance]'));assert(form.querySelector('[data-permission][value=employees]'));assert(!form.querySelector('[data-permission][value=linear]'));assert(form.querySelector('[data-work-setting=ai_enabled]'));assert(!form.querySelector('[data-work-event=linear_done]'));assert(!form.querySelector('[data-work-event=proactive_risk]'));
  submit(b,form);await settle();assert.equal(writes(b).length,0,'unchanged settings must not broaden partial grants');
  const ai=form.querySelector('[data-work-setting=ai_enabled]');ai.value='true';ai.dispatchEvent(new b.w.Event('change',{bubbles:true}));
  const notify=form.querySelector('[data-work-event=task_assigned][data-work-channel=push]');notify.checked=true;notify.dispatchEvent(new b.w.Event('change',{bubbles:true}));
  submit(b,form);await settle();assert.deepEqual(writes(b)[0].body.changes,{work_settings:{expected_version:'settings-v1',changes:{ai_enabled:true,notifications:{task_assigned:{push:true}}}}});
 }finally{b.close()}
}
{
 const b=await mount({role:'owner',row});try{const form=edit(b),finance=form.querySelector('[data-permission][value=finance]');assert(finance.indeterminate);finance.checked=false;finance.dispatchEvent(new b.w.Event('change',{bubbles:true}));submit(b,form);await settle();assert.deepEqual(writes(b)[0].body.changes,{permission_changes:{finance:false}})}finally{b.close()}
}
{
 const b=await mount({role:'admin',row:{explicit_permissions:['tasks:view','legacy:retained'],permissions:['tasks:view','legacy:retained']}});try{const form=edit(b);assert.equal(form.querySelector('[data-permission][value=tasks]').checked,false);input(b,form,'display_name','Only name');submit(b,form);await settle();assert.deepEqual(writes(b)[0].body.changes,{display_name:'Only name'})}finally{b.close()}
}
{
 const b=await mount({role:'owner',row:{...row,role:'owner'}});try{const form=edit(b);assert(!form.querySelector('[data-work-setting=ai_enabled]'));assert(!form.querySelector('[data-work-setting=ai_agents_manage]'))}finally{b.close()}
}
{
 const b=await mount({role:'owner',row:{...row,password_assign_available:true,email_assign_available:true}});try{const form=edit(b);const ai=form.querySelector('[data-work-setting=ai_enabled]');ai.value='inherit';ai.dispatchEvent(new b.w.Event('change',{bubbles:true}));input(b,form,'new_email','new@isolated.example');b.f.writeResult={status:200,data:{success:true,applied:{telegram_id:22,card_version:'email-v2',email_changed:true}}};form.querySelector('#staffAssignEmail').click();await settle();b.f.confirmResult=false;b.d.querySelector('[data-staff-close]').click();assert(b.d.getElementById('staffMemberModal').classList.contains('show'));submit(b,form);await settle();assert.deepEqual(writes(b)[1].body.changes,{work_settings:{expected_version:'settings-v1',changes:{ai_enabled:null}}})}finally{b.close()}
}
console.log('PASS unified employee settings RU/AZ/EN, sparse settings, partial rights, owner AI, dirty state');
})().catch(error=>{console.error(error.stack);process.exitCode=1});
