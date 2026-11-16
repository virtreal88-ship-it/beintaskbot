const {JSDOM,VirtualConsole}=require('../.support-test-deps/node_modules/jsdom');
const fs=require('node:fs'),assert=require('node:assert/strict');
const vc=new VirtualConsole(),errors=[];vc.on('jsdomError',e=>{if(!String(e.type).includes('css'))errors.push(e.message)});
const dom=new JSDOM('<html lang="en"><body><div id="dealsList"></div></body></html>',{url:'https://fixture.test/app',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});const w=dom.window,d=w.document;
w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});w.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};w.tenantViewDemand={watch(){}};
const tick=()=>new Promise(r=>setTimeout(r,30));async function until(f){for(let i=0;i<100;i++){if(f())return;await tick()}assert(f(),'UI did not settle')}
const rows=[{source_revision:'a'.repeat(64),kommo_lead_id:7,name:'Assigned deal',pipeline_id:10,status_id:100,raw:{updated_at:1000}},{kommo_lead_id:8,name:'Own deal outside assigned funnel',pipeline_id:20,status_id:200,raw:{updated_at:1000}}];let writes=[];
w.fetch=async(url,options={})=>{if(url.includes('move-stage')){const body=JSON.parse(options.body);writes.push(body);return {ok:true,json:async()=>({source_revision:'b'.repeat(64),success:true,state:'confirmed',lead_id:'7',pipeline_id:10,status_id:101,updated_at:1001})};}return {ok:true,json:async()=>({success:true,catalog:[{pipeline_id:10,name:'Assigned',stages:[{stage_id:100,name:'New'},{stage_id:101,name:'Working'},{stage_id:142,name:'Complete'}]}],hidden_stage_keys:[],move_stage_targets:[{pipeline_id:10,stage_id:100},{pipeline_id:10,stage_id:101}],move_stage_sources:[{pipeline_id:10,stage_id:100},{pipeline_id:10,stage_id:101},{pipeline_id:10,stage_id:142}],move_stage_completion_requires_approval:true,move_context:{tenant_id:'a',user_id:'7'}})}};
(async()=>{w.eval(fs.readFileSync('docs/tenant-chakra-ui.js','utf8'));const opened=[];w.tenantChakraCRM.renderDeals('dealsList',rows,false,id=>opened.push(id));await until(()=>d.querySelector('select'));
require('./deal_card_activation_assertions.cjs')(w,d.querySelector('[data-deal-open-surface]'),opened);assert.equal(d.querySelectorAll('select').length,1);assert.equal(d.querySelector('[data-kommo-stage-key="10:100"] [data-deal-stage-badge]'),null);assert(d.querySelector('[data-kommo-stage-key="10:100"] [data-deal-stage-control]'));assert(d.querySelector('[data-kommo-stage-key="20:200"] [data-deal-stage-badge]'));assert.equal(d.querySelector('select').options.length,2);assert(d.body.textContent.includes('approval is required'));
let select=d.querySelector('select');select.value='101';select.dispatchEvent(new w.Event('change',{bubbles:true}));await until(()=>writes.length===1&&!d.querySelector('select').disabled&&d.querySelector('select').value==='101');assert.equal(writes[0].expected_status_id,'100');assert.equal(writes[0].expected_tenant_id,'a');assert.equal(writes[0].stage_id,'101');select=d.querySelector('select');assert.equal(select.value,'101');assert.deepEqual(errors,[]);
const previewRows=[
 {kommo_lead_id:78561803,name:'Lead #78561803',contact_name:'Client A',phone:'+994501234567',pipeline_id:20,status_id:200},
 {kommo_lead_id:9,name:'Custom project title',contact_name:'Client B',pipeline_id:20,status_id:200},
 {kommo_lead_id:10,name:'Lead #10',contact_name:'Client C',last_message:'Order 123 confirmed',pipeline_id:20,status_id:200}
];
w.tenantChakraCRM.renderDeals('dealsList',previewRows,false,()=>{});
await until(()=>d.body.textContent.includes('Client C'));
assert(!d.body.textContent.includes('78561803'),'technical deal number is not duplicated on card');
assert(d.body.textContent.includes('+994501234567'),'client phone remains');
assert(d.body.textContent.includes('Custom project title'),'meaningful title preview remains');
assert(d.body.textContent.includes('Order 123 confirmed'),'actual message numbers remain');
assert.equal(previewRows[0].kommo_lead_id,78561803,'routing identity remains intact');
console.log('PASS Kommo board assigned-funnel controls, completion approval and exact stage command');dom.window.close();})().catch(e=>{console.error(e);dom.window.close();process.exitCode=1});
