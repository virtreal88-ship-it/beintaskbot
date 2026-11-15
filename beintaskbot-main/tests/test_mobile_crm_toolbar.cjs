const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.TEST_DEPS ? process.env.TEST_DEPS+'/jsdom' : '../.support-test-deps/node_modules/jsdom');
const html=fs.readFileSync(process.env.TEST_HTML||'docs/tenant-app.html','utf8').replace(/<script\b[\s\S]*?<\/script>/gi,'');
const dom=new JSDOM(html,{url:'https://test.invalid/app',runScripts:'outside-only'}),w=dom.window,d=w.document;
let changed;const media={matches:true,addEventListener:(event,fn)=>changed=fn};w.matchMedia=()=>media;
const source=d.getElementById('taskFeedSource'),input=d.getElementById('taskSearch'),create=d.getElementById('nativeTaskCreate'),refresh=d.getElementById('tasksRefresh'),controls=d.getElementById('nativeTaskViewControls');
const originals=[source,input,create,refresh,controls].map(node=>({node,parent:node.parentNode,next:node.nextSibling}));
let hits=0;refresh.onclick=()=>hits++;input.value='client';source.value='kommo';
w.eval(fs.readFileSync(process.env.TEST_SCRIPT||'docs/tenant-mobile-crm-toolbar.js','utf8'));
assert.equal(d.querySelectorAll('.crm-mobile-toolbar').length,2);
const panel=d.querySelector('#view-tasks .crm-mobile-toolbar'),filter=panel.querySelector('.crm-mobile-filter'),more=panel.querySelector('.crm-mobile-more');
assert.equal(panel.querySelectorAll(':scope > button').length,3);assert.equal(input.closest('.crm-mobile-popover'),null);assert.equal(input.closest('.crm-mobile-toolbar'),panel);
filter.click();assert.equal(filter.getAttribute('aria-expanded'),'true');assert.equal(d.activeElement===input,false,'opening filter must not summon keyboard');more.click();assert.equal(filter.getAttribute('aria-expanded'),'false');assert.equal(more.getAttribute('aria-expanded'),'true');more.dispatchEvent(new w.FocusEvent('focusout',{bubbles:true,relatedTarget:refresh}));assert.equal(more.getAttribute('aria-expanded'),'true','moving focus to an action must not close it before click');refresh.click();assert.equal(hits,1);
create.hidden=true;create.disabled=true;assert(create.hidden&&create.disabled);create.hidden=false;create.disabled=false;
for(let i=0;i<4;i++){media.matches=false;changed();assert.equal(d.querySelectorAll('.crm-mobile-toolbar').length,0);for(const {node,parent,next} of originals){assert.equal(node.parentNode,parent);assert.equal(node.nextElementSibling,next?.nodeType===1?next:node.nextElementSibling);}media.matches=true;changed();assert.equal(d.querySelectorAll('.crm-mobile-toolbar').length,2);assert.equal(d.getElementById('taskSearch'),input);assert.equal(input.value,'client');assert.equal(source.value,'kommo');}
filter.click();filter.focus();filter.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(filter.getAttribute('aria-expanded'),'false');assert.equal(d.activeElement,filter);
filter.click();d.dispatchEvent(new w.Event('tenant-view'));assert.equal(filter.getAttribute('aria-expanded'),'false');
w.tenantI18n={language:'ru'};d.dispatchEvent(new w.Event('tenant-language'));assert.equal(filter.getAttribute('aria-label'),'Фильтры');
console.log('PASS three mobile controls, original nodes/state/callbacks, repeated breakpoints, disclosures, keyboard, language');setTimeout(()=>dom.window.close(),0);
