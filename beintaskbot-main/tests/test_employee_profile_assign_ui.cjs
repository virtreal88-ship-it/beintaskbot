const assert=require('node:assert/strict');
const {mount,settle,until,writes,edit,input,submit}=require('./employee_card_fixture.cjs');
(async()=>{
 for(const language of ['ru','az','en']){
  const b=await mount({role:'owner',language,row:{profile_assign_available:true,password_assign_available:true,email_assign_available:true}});
  try{
   const form=edit(b);for(const key of ['first_name','last_name','login','phone','avatar_url'])assert.equal(form.elements.namedItem(key).readOnly,false,key);
   assert(form.querySelector('option[value=admin]'));assert(form.querySelector('option[value=master]'));assert(form.querySelector('option[value=operator]'));assert.notEqual(form.querySelector('option[value=operator]').textContent,form.querySelector('option[value=worker]').textContent);
   input(b,form,'first_name','Nigar');input(b,form,'phone','+994509999999');
   b.f.writeResult={status:200,data:{success:true,applied:{telegram_id:22,card_version:'profile-v2',profile_changed:true,profile:{first_name:'Nigar',phone:'+994509999999'}}}};
   submit(b,form);await settle();assert.equal(writes(b).length,1);assert.equal(writes(b)[0].body.action,'assign_member_profile');assert.deepEqual(writes(b)[0].body.changes,{first_name:'Nigar',phone:'+994509999999'});
   assert.equal(form.elements.namedItem('first_name').value,'Nigar');assert.equal(form.dataset.blocked,undefined);
   submit(b,form);await settle();assert.equal(writes(b).length,1,'unchanged save has no duplicate command');
  }finally{b.close()}
 }
 {
  const b=await mount({role:'owner',row:{profile_assign_available:true,password_assign_available:true,email_assign_available:true}});try{
   const form=edit(b);input(b,form,'first_name','Draft name');input(b,form,'new_email','new@isolated.example');
   b.f.writeResult={status:200,data:{success:true,applied:{telegram_id:22,card_version:'email-v2',email_changed:true}}};
   form.querySelector('#staffAssignEmail').click();await settle();assert.equal(form.elements.namedItem('first_name').value,'Draft name');
   b.f.confirmResult=false;b.d.querySelector('[data-staff-close]').click();assert(b.d.getElementById('staffMemberModal').classList.contains('show'),'partial email save retains dirty profile');
   b.f.writeResult={status:200,data:{success:true,applied:{telegram_id:22,card_version:'profile-v3',profile_changed:true}}};submit(b,form);await settle();assert.equal(writes(b)[1].body.expected_version,'email-v2');assert.equal(writes(b)[1].body.changes.first_name,'Draft name');
  }finally{b.close()}
 }
 {
  const b=await mount({role:'owner',row:{profile_assign_available:true}});try{
   const form=edit(b);input(b,form,'login','bad space');submit(b,form);await settle();assert.equal(writes(b).length,0);
   input(b,form,'login','valid.new');b.f.writeResult={status:409,data:{success:false,code:'member_duplicate',fields:{login:'duplicate'}}};submit(b,form);await settle();assert.equal(form.elements.namedItem('login').value,'valid.new');assert.match(form.querySelector('[data-field-error=login]').textContent,/already/);
  }finally{b.close()}
 }
 {
  const b=await mount({role:'owner',row:{profile_assign_available:true}});try{
   const form=edit(b);input(b,form,'first_name','First saved');input(b,form,'display_name','Local saved');b.f.writeResult={status:200,data:{success:true,applied:{telegram_id:22,card_version:'profile-v2',profile_changed:true}}};submit(b,form);await settle();assert.equal(writes(b).length,2);assert.equal(writes(b)[1].body.action,'update_member_card');assert.equal(writes(b)[1].body.expected_version,'profile-v2');assert.deepEqual(writes(b)[1].body.changes,{display_name:'Local saved'});
  }finally{b.close()}
 }
 {
  const b=await mount({role:'owner',row:{profile_assign_available:true}});try{
   const form=edit(b);input(b,form,'first_name','Submitted');b.f.holdWrite=true;
   b.f.writeResult={status:200,data:{success:true,applied:{telegram_id:22,card_version:'profile-v2',profile_changed:true,profile:{first_name:'Submitted',last_name:'Testova'}}}};
   submit(b,form);await until(()=>b.f.pendingWrites.length===1);input(b,form,'first_name','New draft');input(b,form,'last_name','New surname');b.f.pendingWrites.shift()();await settle();assert.equal(form.elements.namedItem('first_name').value,'New draft');assert.equal(form.elements.namedItem('last_name').value,'New surname');b.f.confirmResult=false;b.d.querySelector('[data-staff-close]').click();assert(b.d.getElementById('staffMemberModal').classList.contains('show'));
  }finally{b.close()}
 }
 for(const row of [{profile_assign_available:false},{profile_assign_available:true,active:false}]){const b=await mount({role:'owner',row});try{const form=edit(b);assert.equal(form.elements.namedItem('first_name').readOnly,true)}finally{b.close()}}
 console.log('PASS managed profile edit, roles, sparse save, partial email dirty draft, validation and sequential version');
})().catch(e=>{console.error(e.stack);process.exitCode=1});
