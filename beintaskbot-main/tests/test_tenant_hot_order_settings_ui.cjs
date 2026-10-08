const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require('node:path').join(__dirname,'../docs/tenant-hot-order-settings.js'),'utf8');
const context={window:{}};vm.createContext(context);vm.runInContext(source,context);
const ui=context.window.tenantHotOrderSettings;
const rendered=ui.render({services:[{id:'stable',name:'<unsafe>',active:false}],create_roles:[],claim_roles:[]});
assert.ok(rendered.includes('&lt;unsafe&gt;'));assert.ok(rendered.includes('Bank köçürməsi edilmir'));
assert.ok(rendered.includes('data-hot-reward'));assert.ok(rendered.includes('value="0.00"'));
assert.ok(!rendered.includes('selected'),'Explicit empty roles must remain empty');
assert.ok(rendered.includes('data-hot-completion checked'));
const selectors={
  '[data-hot-member-services]':{selectedOptions:[{value:'stable'}]},
  '[data-hot-member="create"]':{value:'inherit'},'[data-hot-member="claim"]':{value:'false'},'[data-hot-member="completion_requires_admin"]':{value:'inherit'}
};
const member=ui.readMember({querySelector:key=>selectors[key]},{hot_order_create:true,unrelated:'keep'});
assert.ok(!('hot_order_create' in member));assert.equal(member.hot_order_claim,false);assert.equal(member.unrelated,'keep');
const row={dataset:{hotService:'stable'},querySelector:key=>key==='[data-hot-name]' ? {value:' Renamed '} : key==='[data-hot-reward]' ? {value:'25.50'} : {checked:false}};
const root={querySelectorAll:()=>[row],querySelector:()=>({selectedOptions:[],checked:false})};
const next=ui.read(root,{services:[{id:'stable',name:'Before',custom:'keep'}],unrelated:true});
assert.equal(next.services[0].id,'stable');assert.equal(next.services[0].name,'Renamed');
assert.equal(next.services[0].reward_amount,'25.50');
assert.equal(next.services[0].active,false);assert.equal(next.services[0].custom,'keep');assert.equal(next.unrelated,true);
row.querySelector=key=>key==='[data-hot-name]' ? {value:' '} : key==='[data-hot-reward]' ? {value:'25.00'} : {checked:true};
assert.throws(()=>ui.read(root,{}));
const app=fs.readFileSync(require('node:path').join(__dirname,'../docs/tenant-app.html'),'utf8');
assert.ok(app.indexOf('/assets/tenant-hot-order-settings.js')<app.indexOf('/assets/tenant-workflow.js'));
console.log('PASS: hot-order settings preserve service IDs, explicit denials and unrelated policies; names escaped.');
