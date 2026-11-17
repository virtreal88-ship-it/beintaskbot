const {JSDOM,VirtualConsole}=require('../.support-test-deps/node_modules/jsdom');
const fs=require('node:fs'),assert=require('node:assert/strict');
const tick=()=>new Promise(r=>setTimeout(r,30));async function until(f){for(let i=0;i<100;i++){if(f())return;await tick()}assert(f(),'UI did not settle')}
(async()=>{
for(const scenario of ['confirmed','unknown','missing','wrong','tenant','same','unrelated','denied','early']){
 const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>{if(!String(e.type).includes('css'))errors.push(e.message)});
 const dom=new JSDOM('<html lang="ru"><body><div id="dealsList"></div></body></html>',{url:'https://fixture.test/app',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc}),w=dom.window,d=w.document;
 try{
 w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});w.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};w.tenantViewDemand={watch(){}};
 const row={source_revision:'a'.repeat(64),kommo_lead_id:7,name:'Deal',pipeline_id:10,status_id:100,raw:{updated_at:1000}};let mode='unknown',requests=[],release;
 w.fetch=async(url,options={})=>{
  if(url.includes('move-stage')){
   requests.push(options.method||'GET');
   if(mode==='hold'||(scenario==='early'&&options.method==='POST'))await new Promise(r=>release=r);
   if(mode==='denied')return {ok:false,json:async()=>({success:false,error:'kommo_stage_forbidden',keep_request:false})};
   return mode==='confirmed'||mode==='wrong'||(scenario==='early'&&requests.length>=3)?{ok:true,json:async()=>({success:true,state:'confirmed',lead_id:mode==='wrong'?8:7,pipeline_id:10,status_id:101,updated_at:1001,source_revision:'b'.repeat(64)})}:{ok:false,json:async()=>({success:false,error:'board_outcome_unknown',keep_request:true})};
  }
  return {ok:true,json:async()=>({success:true,catalog:[{pipeline_id:10,name:'Sales',stages:[{stage_id:100,name:'New'},{stage_id:101,name:'Working'}]}],hidden_stage_keys:[],move_stage_targets:[{pipeline_id:10,stage_id:100},{pipeline_id:10,stage_id:101}],move_stage_sources:[{pipeline_id:10,stage_id:100},{pipeline_id:10,stage_id:101}],move_context:{tenant_id:'a',user_id:7}})};
 };
 w.eval(fs.readFileSync('docs/tenant-chakra-ui.js','utf8'));
 const render=rows=>w.tenantChakraCRM.renderDeals('dealsList',rows,false,()=>{});
 render([row]);await until(()=>d.querySelector('select'));
 const select=d.querySelector('select');select.value='101';select.dispatchEvent(new w.Event('change',{bubbles:true}));
 if(scenario==='early'){
  await until(()=>release);render([{...row,status_id:101,source_revision:'b'.repeat(64),raw:{updated_at:1001}}]);await tick();release();
  await until(()=>requests.length===3&&!d.querySelector('[role=alert]')&&w.sessionStorage.length===0);
  assert.deepEqual(requests,['POST','GET','GET']);assert.equal(d.querySelector('select').value,'101');assert.deepEqual(errors,[]);
  console.log('PASS stale stage notice refreshed before failure');continue;
 }
 await until(()=>d.querySelector('[role=alert]')&&!d.querySelector('select').disabled);
 assert.deepEqual(requests,['POST','GET']);assert.equal(w.sessionStorage.length,1);
 if(scenario==='missing')w.sessionStorage.clear();
 mode=scenario==='tenant'?'hold':scenario;
 render([{...row,status_id:scenario==='same'?100:101,pipeline_id:scenario==='unrelated'?20:10,source_revision:'b'.repeat(64),raw:{updated_at:1001}}]);
 if(scenario==='confirmed'){
  await until(()=>!d.querySelector('[role=alert]'));
  assert.deepEqual(requests,['POST','GET','GET']);assert.equal(w.sessionStorage.length,0);assert.equal(d.querySelector('select').value,'101');
 }else if(scenario==='tenant'){
  await until(()=>release);d.dispatchEvent(new w.CustomEvent('tenant-auth-invalid'));
  mode='confirmed';release();await tick();await tick();
  assert(!d.querySelector('[role=alert]'));assert(!d.querySelector('select'),'late receipt cannot restore old tenant rows');
 }else{
  await tick();await tick();assert(d.querySelector('[role=alert]'),'unresolved outcome must stay visible');
  assert.equal(requests.filter(x=>x==='POST').length,1,'refresh never posts another mutation');
  if(['missing','same','unrelated'].includes(scenario))assert.equal(requests.length,2,'no applicable command performs no network call');
  if(scenario==='missing'){assert.match(d.querySelector('[role=alert]').textContent,/запрос отсутствует/);assert(![...d.querySelectorAll('button')].some(x=>x.textContent==='Проверить'),'unverifiable command does not lock board behind a dead check button');}
  if(scenario==='denied'){assert.equal(w.sessionStorage.length,0);assert.match(d.querySelector('[role=alert]').textContent,/воронки/);}
  if(scenario==='unknown'){
   mode='confirmed';[...d.querySelectorAll('button')].find(x=>x.textContent==='Проверить').click();await until(()=>!d.querySelector('[role=alert]'));
   assert.deepEqual(requests,['POST','GET','GET','GET']);
  }
 }
 assert.deepEqual(errors,[]);console.log('PASS stale stage notice '+scenario);
 }finally{w.close();}
}
})().catch(e=>{console.error(e);process.exitCode=1});
