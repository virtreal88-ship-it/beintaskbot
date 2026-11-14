const assert=require('node:assert/strict');
const {mount,edit,settle}=require('./employee_card_fixture.cjs');
(async()=>{for(const language of ['en','ru','az']){const b=await mount({language,role:'owner',settings:true,modules:{integrations:true},row:{editable_fields:['work_settings'],work_settings:{version:'v1',values:{notifications:{task_assigned:{push:true}}},notifications:[{key:'task_assigned',channels:['push','telegram']}]}}});try{
 const family=b.d.querySelector('.settings-family');assert.equal(family.hidden,false);
 for(const view of ['integrations','settings','employees']){const tab=b.d.querySelector('[data-settings-page='+view+']');tab.click();await settle();assert.equal(b.w.location.search.includes('view='+view),true);assert.equal(tab.getAttribute('aria-selected'),'true');}
 assert.equal(b.d.querySelector('[data-staff-tab=invites]'),null);assert.equal(b.d.querySelector('[data-staff-tab=requests]'),null);
 assert.equal(b.w.getComputedStyle(b.d.querySelector('#companyEmployeesNav')).display,'none');
 const form=edit(b),tab=form.querySelector('[data-staff-pane-tab=notifications]'),pane=form.querySelector('[data-staff-pane=notifications]');
 assert(tab);assert.equal(pane.hidden,true);tab.click();assert.equal(pane.hidden,false);assert.equal(pane.querySelector('[data-work-channel=push]').checked,true);
 form.querySelector('[data-staff-pane-tab=rights]').click();tab.click();assert.equal(pane.querySelector('[data-work-channel=push]').checked,true);
 assert.equal(form.querySelector('[data-staff-pane=rights] [data-work-event]'),null);assert.equal(form.querySelectorAll('[data-work-event]').length,2);
 console.log('PASS Settings routes and separate notifications retain values: '+language);
}finally{b.close()}}})().catch(e=>{console.error(e);process.exitCode=1});
