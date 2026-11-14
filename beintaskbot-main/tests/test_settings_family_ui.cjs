const assert=require('node:assert/strict');
const {mount,edit,settle}=require('./employee_card_fixture.cjs');
(async()=>{for(const language of ['en','ru','az']){const b=await mount({language,role:'owner',settings:true,modules:{integrations:true},pages:{profile:true,settings:true,integrations:true,employees:true},row:{editable_fields:['work_settings'],work_settings:{version:'v1',values:{notifications:{task_assigned:{push:true}}},notifications:[{key:'task_assigned',channels:['push','telegram']}]}}});try{
 const family=b.d.querySelector('.settings-family');assert.equal(family.hidden,false);
 assert.equal(b.d.querySelector('[data-settings-page=settings]'),null,'the General tab is removed from Settings');
 assert.equal(b.d.querySelector('#chakra-settings-root').parentElement.id,'profile-account-settings','the existing account editor is in Profile');
 assert.equal(b.d.querySelector('#chakra-notification-settings-root').parentElement.id,'profile-company-settings','notification preferences were moved without copying');
 assert.equal(b.d.querySelector('#integrationInfo').closest('.panel').parentElement.id,'legacyIntegrationSettings');
 assert.equal(b.d.querySelector('#inviteName').closest('.panel').parentElement.id,'legacyEmployeeSettings');
 for(const view of ['integrations','employees']){const tab=b.d.querySelector('[data-settings-page='+view+']');assert(tab&&!tab.hidden);tab.click();await settle();assert.equal(b.w.location.search.includes('view='+view),true);assert.equal(tab.getAttribute('aria-selected'),'true');}
 assert.equal(b.w.tenantAppNavigate('settings',true),true,'legacy General route redirects to Profile');await settle();assert.equal(b.w.location.search.includes('view=profile'),true);assert.equal(family.hidden,true);
 b.d.querySelector('.app-avatar-button').click();b.d.querySelector('[data-app-open=settings]').click();await settle();assert.equal(b.w.location.search.includes('view=integrations'),true,'avatar Settings opens the first permitted tab');
 assert.equal(b.d.querySelector('[data-staff-tab=invites]'),null);assert.equal(b.d.querySelector('[data-staff-tab=requests]'),null);
 assert.equal(b.w.getComputedStyle(b.d.querySelector('#companyEmployeesNav')).display,'none');
 b.w.tenantAppNavigate('employees',true);await settle();
 const form=edit(b),tab=form.querySelector('[data-staff-pane-tab=notifications]'),pane=form.querySelector('[data-staff-pane=notifications]');
 assert(tab);assert.equal(pane.hidden,true);tab.click();assert.equal(pane.hidden,false);assert.equal(pane.querySelector('[data-work-channel=push]').checked,true);
 form.querySelector('[data-staff-pane-tab=rights]').click();tab.click();assert.equal(pane.querySelector('[data-work-channel=push]').checked,true);
 assert.equal(form.querySelector('[data-staff-pane=rights] [data-work-event]'),null);assert.equal(form.querySelectorAll('[data-work-event]').length,2);
 console.log('PASS Profile migration and Settings routes retain access and employee preferences: '+language);
}finally{b.close()}}})().catch(e=>{console.error(e);process.exitCode=1});
