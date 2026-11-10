// Scoped stylesheet contract on the real employee DOM. Pixel/layout assertions
// run in employee_card_http_browser.cjs with Chromium and screenshot artifacts.
const assert=require('node:assert/strict'),fs=require('node:fs');
const {mount,edit}=require('./employee_card_fixture.cjs');
(async()=>{const b=await mount({role:'owner',row:{editable_fields:['access_settings'],access_settings:{version:'layout',page_catalog:[{id:'tasks',module:'tasks'}],page_visibility:{},pages:{tasks:true},actions:{tasks:{view:true}},action_catalog:{tasks:['view','create','update','delete']}}}});try{
 const style=b.d.createElement('style');style.textContent=fs.readFileSync(require('node:path').join(__dirname,'../docs/tenant-members-ui.css'),'utf8');b.d.head.append(style);
 const form=edit(b),computed=node=>b.w.getComputedStyle(node);
 assert(form.classList.contains('staff-tabbed-form'));
 assert.equal(computed(form).display,'flex');assert.equal(computed(form).overflow,'hidden');
 assert.equal(computed(form.querySelector('.staff-modal-actions')).position,'sticky');
 assert.equal(computed(form.querySelector('.staff-tab-panel')).overflow,'auto');
 const group=form.querySelector('[data-access-operations]>fieldset');assert.equal(computed(group).gridTemplateColumns,'repeat(4,minmax(0,1fr))');
 assert.equal(computed(group.querySelector('label')).display,'flex');
 assert.equal(computed(form.querySelector('[data-access-pages]>.grid')).gridTemplateColumns,'1fr');
 assert.equal(computed(form.querySelector('[name=first_name]').closest('label')).margin,'0px');
 console.log('PASS scoped employee layout: independent panel scroll, persistent footer, four-column CRUD and compact labels');
}finally{b.close()}})().catch(error=>{console.error(error.stack);process.exitCode=1});
