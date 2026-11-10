const assert=require('node:assert/strict');
const {mount,settle,writes,edit,input}=require('./employee_card_fixture.cjs');
(async()=>{
 for(const language of ['ru','az','en'])for(const kind of ['password','email']){
  const b=await mount({role:'owner',language,row:{password_assign_available:true,email_assign_available:true,email:''}});
  try{
   const form=edit(b);assert(form.querySelector('#staffAssignPassword'));assert(form.querySelector('#staffAssignEmail'));
   if(kind==='password'){input(b,form,'password','Synthetic-new-password-42');input(b,form,'confirm_password','Synthetic-new-password-42')}else input(b,form,'new_email','new@isolated.example');
   b.f.writeResult={status:200,data:{success:true,applied:{telegram_id:22,card_version:'v2',[kind+'_changed']:true}}};
   form.querySelector(kind==='password'?'#staffAssignPassword':'#staffAssignEmail').click();await settle();
   assert.equal(writes(b).length,1);assert.equal(writes(b)[0].body.action,'assign_member_'+kind);
   assert.equal(form.elements.namedItem('password').value,'');assert.equal(form.elements.namedItem('confirm_password').value,'');
   assert.equal(b.f.calls.filter(c=>c.path.includes('email/reset')).length,0);
  }finally{b.close()}
 }
 for(const extra of [{password_assign_available:false},{active:false},{card_version:''}]){
  const b=await mount({role:'owner',row:{password_assign_available:true,email_assign_available:true,...extra}});try{edit(b);assert.equal(b.d.getElementById('staffAssignPassword'),null);assert.equal(b.d.getElementById('staffAssignEmail'),null)}finally{b.close()}
 }
 {
  const b=await mount({role:'owner',row:{password_assign_available:true,email_assign_available:true}});try{
   const form=edit(b);input(b,form,'password','Synthetic-new-password-42');input(b,form,'confirm_password','mismatch');form.querySelector('#staffAssignPassword').click();await settle();assert.equal(writes(b).length,0);
   input(b,form,'confirm_password','Synthetic-new-password-42');b.f.transportError=true;form.querySelector('#staffAssignPassword').click();await settle();assert.equal(writes(b).length,1);assert.equal(form.dataset.blocked,'true');form.querySelector('#staffAssignPassword').click();await settle();assert.equal(writes(b).length,1);assert.equal(form.elements.namedItem('password').value,'');
  }finally{b.close()}
 }
 console.log('PASS employee password/email actions RU/AZ/EN, login-only employee, masking, guards, mismatch and no uncertain retry');
})().catch(e=>{console.error(e.stack);process.exitCode=1});
