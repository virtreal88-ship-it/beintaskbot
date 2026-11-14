const {JSDOM,VirtualConsole}=require('../.support-test-deps/node_modules/jsdom');
const fs=require('node:fs'),assert=require('node:assert/strict');
const vc=new VirtualConsole(),errors=[];vc.on('jsdomError',e=>{if(!String(e.type).includes('css'))errors.push(e.message)});
const dom=new JSDOM('<html><body><div id="tasksList"></div></body></html>',{url:'https://fixture.test/app',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
const w=dom.window,d=w.document;w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});w.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};w.tenantViewDemand={watch(){}};w.tenantI18n={language:'ru'};
let opened=[],writes=0;w.fetch=()=>{writes++;throw Error('Unexpected request')};
const tick=()=>new Promise(r=>setTimeout(r,30));async function until(f){for(let i=0;i<100;i++){if(f())return;await tick()}assert(f(),'UI did not settle')}
const button=t=>[...d.querySelectorAll('button')].find(n=>(n.getAttribute('aria-label')||n.textContent)===t);
const rows=[{id:'1',text:'Example [CRM:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa]',deal_title:'Deal',due_at:new Date().toISOString(),status:'open'},{id:'2',text:'Completed',status:'completed'},{id:'3',text:'No due',status:'open'}];
const settings={source:'kommo',complete:false,onOpen:id=>opened.push(id)};
(async()=>{w.eval(fs.readFileSync('docs/tenant-chakra-ui.js','utf8'));w.tenantChakraCRM.renderTasks(rows,settings);await until(()=>d.querySelector('[data-task-kanban]'));
assert.equal(d.querySelectorAll('[data-task-card]').length,3);assert.equal(d.querySelectorAll('[data-task-complete]').length,0);assert(!d.body.textContent.includes('[CRM:'));
d.querySelector('[data-task-flow-open="1"]').click();assert.deepEqual(opened,['1']);button('Таблица').click();await until(()=>!d.querySelector('[data-task-kanban]'));assert.equal(d.querySelectorAll('[data-task-flow-open]').length,3);
w.tenantChakraCRM.renderTasks(rows.slice(0,1),settings);await tick();assert.equal(d.querySelector('[data-task-view]').dataset.taskView,'table');button('Канбан').click();await until(()=>d.querySelector('[data-task-kanban]'));assert.equal(d.querySelectorAll('[data-task-card]').length,1);
w.tenantChakraCRM.renderTasks([],{...settings,empty:'Forbidden'});await until(()=>d.body.textContent.includes('Forbidden'));assert.equal(d.querySelectorAll('[data-task-flow-open]').length,0);assert.equal(writes,0);assert.equal(errors.length,0,errors.join('\n'));console.log('PASS shipped bundle: default board, view switch, filter refresh, access clearing, open action, no mutations');})().catch(e=>{console.error(e);process.exitCode=1}).finally(()=>w.close());
