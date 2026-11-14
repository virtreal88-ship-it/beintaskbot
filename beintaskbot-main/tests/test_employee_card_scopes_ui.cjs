const assert=require('node:assert/strict');
const {mount,settle,edit,writes}=require('./employee_card_fixture.cjs');
(async()=>{
 const b=await mount();
 try {
  b.f.profile.capabilities.can_manage_employees=false;
  b.f.profile.capabilities.can_view_employees=true;
  b.f.profile.capabilities.can_update_employees=false;
  b.f.members[0].editable_fields=[];
  b.w.document.dispatchEvent(new b.w.CustomEvent('tenant-profile',{detail:b.f.profile}));
  await settle();
  assert.equal(b.d.getElementById('staffManagement').hidden,false,'read-only employee page remains visible');
  assert.equal(b.d.getElementById('staffAdd').hidden,true);
  assert.equal(b.d.querySelector('[data-staff-tab=requests]'),null);
  assert.equal(b.d.querySelector('[data-staff-tab=invites]'),null);
  b.d.querySelector('[data-staff-tab=members]').click();await settle();
  const form=edit(b);
  assert.equal(form.elements.namedItem('display_name').readOnly,true);
  assert.equal(form.elements.namedItem('role').disabled,true);
  assert.equal(form.elements.namedItem('active').disabled,true);
  b.d.querySelector('[data-staff-close]').click();
  b.f.profile.capabilities.can_view_employees=false;
  b.w.document.dispatchEvent(new b.w.CustomEvent('tenant-profile',{detail:b.f.profile}));await settle();
  assert.equal(b.d.getElementById('staffManagement').hidden,true);
  assert.equal(b.d.getElementById('staffMemberModal').classList.contains('show'),false);
  assert.equal(writes(b).length,0);
 } finally {b.close()}
 console.log('PASS: scoped read-only cards, hidden creation/requests and revocation cleanup.');
})().catch(e=>{console.error(e);process.exitCode=1});
