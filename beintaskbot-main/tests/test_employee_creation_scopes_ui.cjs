const assert=require('node:assert/strict');
const {mount,settle,until,writes,edit,input,submit}=require('./employee_card_fixture.cjs');
const department='00000000-0000-0000-0000-000000009301';
const profileEvent=b=>b.d.dispatchEvent(new b.w.CustomEvent('tenant-profile',{detail:b.f.profile}));
(async()=>{
 for(const language of ['ru','az','en']){
  const b=await mount({role:'owner',language,row:{editable_fields:['access_settings'],access_settings:{version:'v1',scoped_access_enabled:true,scope_catalog:{employees:['view','create','update']},action_scope_levels:{employees:{create:['none','department','all']}},scoped_actions:{employees:{view:'all',create:'department',update:'self'}},legacy_actions:{},actions:{},pages:{}}}});
  try{
   const form=edit(b),create=form.querySelector('[data-access-module=employees][data-access-scope=create]');
   assert.deepEqual([...create.options].map(o=>o.value),['none','department','all']);
   assert.equal(form.querySelector('[data-access-module=employees][data-access-scope=delete]'),null);
   assert.deepEqual([...form.querySelector('[data-access-module=employees][data-access-scope=update]').options].map(o=>o.value),['none','self','department','all']);
   assert.equal(writes(b).length,0);
  }finally{b.close()}
 }
 const b=await mount();try{
  Object.assign(b.f.profile.capabilities,{can_manage_employees:false,can_view_employees:false,can_update_employees:false,can_create_employees:true,employee_creation_scoped:true});
  b.f.profile.member.department_id=department;
  const original=b.w.fetch;let pending=null,hold=false,optionReads=0;
  b.w.fetch=async(url,opts)=>{
   if(String(url).includes('/api/platform/members?options=create')){
    optionReads++;
    const response=()=>new Response(JSON.stringify({success:true,creation_options:{scope:'department',department_required:true,departments:[{id:department,name:'Own department',active:true}],permission_keys:['tasks']}}),{status:200,headers:{'Content-Type':'application/json'}});
    return hold?new Promise(resolve=>{pending=()=>resolve(response())}):response();
   }
   return original(url,opts);
  };
  profileEvent(b);await settle();
  assert.equal(b.d.getElementById('staffManagement').hidden,false);
  assert.equal(b.d.querySelector('[data-staff-edit]'),null,'C without R reveals no cards');
  const beforeReads=b.f.calls.filter(c=>c.path==='/api/platform/members'&&c.method==='GET').length;
  b.d.getElementById('staffAdd').click();await until(()=>b.d.getElementById('staffInviteForm'));
  let form=b.d.getElementById('staffInviteForm');
  assert.equal(form.elements.namedItem('department_id').value,department);
  assert.equal(form.elements.namedItem('department_id').options.length,1);
  assert.equal(form.querySelector('[data-permission][value=deals]').disabled,true);
  for(const [key,value]of Object.entries({first_name:'Synthetic',last_name:'Employee',login:'synthetic.employee',password:'Synthetic-test-pass-123!',confirm_password:'Synthetic-test-pass-123!'}))input(b,form,key,value);
  b.f.writeResult={status:200,data:{success:true,applied:{telegram_id:55,card_version:'created',created:true}}};
  submit(b,form);submit(b,form);await settle();
  assert.equal(writes(b).length,1,'repeated click cannot duplicate creation');
  assert.equal(writes(b)[0].body.department_id,department);
  assert.equal(b.f.calls.filter(c=>c.path==='/api/platform/members'&&c.method==='GET').length,beforeReads,'successful C-only save does not read cards');
  assert.equal(b.d.getElementById('staffMemberModal').classList.contains('show'),false);
  hold=true;b.d.getElementById('staffAdd').click();await until(()=>pending);
  b.f.profile.capabilities.can_create_employees=false;profileEvent(b);pending();await settle();
  assert.equal(b.d.getElementById('staffManagement').hidden,true);
  assert.equal(b.d.getElementById('staffMemberModal').classList.contains('show'),false,'late creation options cannot restore revoked editor');
  assert.equal(writes(b).length,1);assert.equal(optionReads,2);
 }finally{b.close()}
 console.log('PASS: employee C excludes self, C-only department form and receipt, duplicate click and late-revocation safety.');
})().catch(e=>{console.error(e.stack||e);process.exitCode=1});
