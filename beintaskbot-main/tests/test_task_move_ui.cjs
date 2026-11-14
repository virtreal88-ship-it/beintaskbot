const {JSDOM,VirtualConsole}=require('../.support-test-deps/node_modules/jsdom');
const fs=require('node:fs'),assert=require('node:assert/strict');
const dom=new JSDOM('<html><body><div id="tasksList"></div></body></html>',{url:'https://fixture.test/app',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:new VirtualConsole()});
const w=dom.window,d=w.document;w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});w.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};w.tenantViewDemand={watch(){}};w.tenantI18n={language:'ru'};
const tick=()=>new Promise(r=>setTimeout(r,40));async function until(f){for(let i=0;i<100&&!f();i++)await tick();assert(f());}
const rows=[{id:'1',text:'Call',contact_name:'Client',phone:'+994000000000',due_at:null,status:'open',capabilities:{update:true}}];let calls=[];
const settings={source:'native',canMove:r=>r.capabilities.update,onMove:async(...args)=>calls.push(args)};
(async()=>{try{w.eval(fs.readFileSync('docs/tenant-chakra-ui.js','utf8'));w.tenantChakraCRM.renderTasks(rows,settings);await until(()=>d.querySelector('select'));
assert.match(d.querySelector('[data-task-client]').textContent,/Client.*994/);assert.equal(d.querySelector('[data-task-card]').draggable,true);
const choose=value=>{const select=d.querySelector('select');select.value=value;select.dispatchEvent(new w.Event('change',{bubbles:true}));};choose('tomorrow');await until(()=>d.querySelector('input[type="datetime-local"]'));assert.equal(calls.length,0,'choosing destination does not write before date review');
const buttons=()=>[...d.querySelectorAll('button')];buttons().find(b=>b.textContent==='Отмена').click();await tick();assert.equal(calls.length,0);
choose('tomorrow');await tick();buttons().find(b=>b.textContent==='Сохранить').click();await until(()=>calls.length===1);assert.equal(calls[0][0],'1');assert(!Number.isNaN(new Date(calls[0][1]).getTime()));
w.tenantChakraCRM.renderTasks(rows,{...settings,source:'kommo',canMove:()=>false});await until(()=>!d.querySelector('select'));assert.equal(d.querySelector('[data-task-card]').draggable,false);
assert.equal(calls.length,1);console.log('PASS contact display, explicit date review, cancel, keyboard/mobile select, forbidden source');}finally{w.close();}})().catch(e=>{console.error(e);process.exitCode=1});
