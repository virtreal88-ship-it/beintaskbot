const {JSDOM}=require('../.support-test-deps/node_modules/jsdom'),fs=require('fs'),assert=require('assert/strict');
assert(fs.readFileSync('tenant_web_assets.py','utf8').includes("('/assets/tenant-mobile-crm-toolbar.js', 'tenant-mobile-crm-toolbar.js', 'no-store')"));
const html=fs.readFileSync('docs/tenant-app.html','utf8').replace(/<script\b[\s\S]*?<\/script>/gi,'');
const dom=new JSDOM(html,{url:'https://test.invalid/app',runScripts:'outside-only'}),w=dom.window,d=w.document;
let changed;const media={matches:true,addEventListener:(event,fn)=>changed=fn};w.matchMedia=()=>media;
const input=d.getElementById('taskSearch'),source=d.getElementById('taskFeedSource');input.value='query';source.value='kommo';let hits=0;d.getElementById('tasksRefresh').onclick=()=>hits++;
w.eval(fs.readFileSync('docs/tenant-mobile-crm-toolbar.js','utf8'));
assert.equal(d.querySelectorAll('.crm-mobile-toolbar').length,2);assert.equal(d.getElementById('taskSearch'),input);d.getElementById('tasksRefresh').click();assert.equal(hits,1);assert.equal(d.querySelector('#view-tasks .crm-mobile-search-toggle').getAttribute('aria-expanded'),'true');
for(let i=0;i<3;i++){media.matches=false;changed();assert.equal(d.querySelectorAll('.crm-mobile-toolbar').length,0);assert.equal(d.querySelector('#view-tasks .top #tasksRefresh').id,'tasksRefresh');media.matches=true;changed();assert.equal(d.querySelectorAll('.crm-mobile-toolbar').length,2);assert.equal(d.getElementById('taskSearch'),input);assert.equal(input.value,'query');assert.equal(source.value,'kommo');}
console.log('PASS original nodes, callbacks, values and repeated responsive layout');dom.window.close();
