const {JSDOM}=require(process.env.JSDOM_PATH||'../.support-test-deps/node_modules/jsdom');
const assert=require('node:assert/strict');
const fs=require('node:fs');

const html=fs.readFileSync('docs/tenant-instructions.html','utf8');
const script=fs.readFileSync('docs/tenant-instruction-catalog.js','utf8');
const tenantA='00000000-0000-4000-8000-000000000021',tenantB='00000000-0000-4000-8000-000000000022';
const employee={tenant_id:tenantA,telegram_id:21,role:'employee'};
const manager={tenant_id:tenantA,telegram_id:22,role:'owner'};
const makeResponse=(status,body)=>({ok:status>=200&&status<300,status,json:async()=>body});
const guide=(title,slug)=>({slug,title,category:'Ops',body:'Guide body',body_format:'text',images:[]});
const lesson=(id,title,state='published')=>({id,title,language:'en',operation:'inventory.'+id,state});
const settle=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve}}
function mount(url,fetch){
 const dom=new JSDOM(html,{url:url||'https://crm.pro.az/app/instructions?site_lang=en',runScripts:'dangerously',pretendToBeVisual:true});
 dom.window.confirm=()=>false;
 dom.window.HTMLDialogElement.prototype.showModal=function(){this.open=true};
 dom.window.HTMLDialogElement.prototype.close=function(){this.open=false};
 dom.window.fetch=fetch;
 dom.window.BeinLesson={mount(target,doc){target.textContent='Lesson';return{destroy(){}}}};
 dom.window.eval(script);
 return dom;
}
function apiCall(url){return new URL(String(url),'https://crm.pro.az')}

(async()=>{
 const large=Array.from({length:260},(_,i)=>lesson('public-'+String(i).padStart(3,'0'),'Needle lesson '+String(i).padStart(3,'0')));
 const calls=[];
 const dom=mount(null,async url=>{
  const u=apiCall(url);calls.push(u);
  if(u.pathname==='/api/platform/me')return makeResponse(200,{member:employee});
  if(u.pathname==='/api/guides/public')return makeResponse(200,{success:true,items:[]});
  if(u.pathname==='/api/guides')return makeResponse(403,{success:false,error:'No guide edit access'});
  if(u.pathname==='/api/platform/instruction-catalog'){
   assert.equal(u.searchParams.get('expected_tenant_id'),tenantA);
   assert.equal(u.searchParams.get('expected_user_id'),'21');
   const offset=Number(u.searchParams.get('offset')||0),items=large.slice(offset,offset+50);
   return makeResponse(200,{success:true,result:{items,next_offset:offset+items.length<large.length?offset+items.length:null}});
  }
  if(u.pathname==='/api/platform/instructions')return makeResponse(403,{success:false,error:'Employee is read only'});
  throw Error('Unexpected request '+u.pathname);
 });
 const d=dom.window.document;
 await settle(80);
 assert.equal(calls.filter(x=>x.pathname==='/api/platform/instruction-catalog').length,5,'initial request is capped to five pages');
 assert.deepEqual(calls.filter(x=>x.pathname==='/api/platform/instruction-catalog').map(x=>Number(x.searchParams.get('offset'))),[0,50,100,150,200]);
 assert.equal(d.querySelectorAll('.catalog-card[data-kind="lesson"]').length,250);
 assert.equal(d.querySelector('#catalogLoadMore').hidden,false,'more available is visible after the bounded first batch');
 assert.equal(d.querySelector('#newLesson').hidden,true);
 assert.equal(d.querySelector('#createAI').hidden,true);
 assert.equal(d.querySelector('#catalogState').hidden,true);
 assert.equal(d.querySelector('#catalogGlobalNotice').hidden,false,'the shared global article scope is identified');
 d.querySelector('#catalogLoadMore').click();
 await settle(80);
 assert.equal(d.querySelectorAll('.catalog-card[data-kind="lesson"]').length,260);
 assert.equal(d.querySelector('#catalogLoadMore').hidden,true,'last page removes continuation');
 const search=d.querySelector('#catalogSearch');search.value='needle';search.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
 await settle(330);
 const searched=calls.filter(x=>x.pathname==='/api/platform/instruction-catalog'&&x.searchParams.get('q')==='needle');
 assert.equal(searched[0].searchParams.get('expected_tenant_id'),tenantA);
 assert.equal(searched[0].searchParams.get('expected_user_id'),'21');
 assert.deepEqual(searched.slice(0,5).map(x=>Number(x.searchParams.get('offset'))),[0,50,100,150,200]);
 assert.equal(d.querySelectorAll('.catalog-card[data-kind="lesson"]').length,250,'search retains explicit page continuation');
 dom.window.close();

 const statusRows=[
  lesson('p1','Published lesson','published'),
  lesson('d1','Draft lesson','draft'),
  lesson('r1','Review lesson','pending_review'),
  lesson('a1','Approved lesson','approved'),
  lesson('z1','Archived lesson','archived')
 ];
 const statusCalls=[];
 const statusDom=mount(null,async url=>{
  const u=apiCall(url);statusCalls.push(u);
  if(u.pathname==='/api/platform/me')return makeResponse(200,{member:manager});
  if(u.pathname==='/api/guides/public')return makeResponse(200,{success:true,items:[guide('Global guide','/ops/global')]});
  if(u.pathname==='/api/guides')return makeResponse(200,{success:true,items:[{slug:'/ops/global',published:guide('Global guide','/ops/global')}]});
  if(u.pathname==='/api/platform/instruction-catalog')return makeResponse(200,{success:true,result:{items:[lesson('p1','Published lesson')],next_offset:null}});
  if(u.pathname==='/api/platform/instructions'){
   const state=u.searchParams.get('state')||'';
   return makeResponse(200,{success:true,result:{items:statusRows.filter(x=>!state||x.state===state),next_offset:null}});
  }
  if(u.pathname==='/api/platform/instruction-catalog/p1')return makeResponse(200,{success:true,result:{id:'p1',doc:{schema_version:1,title:'Published lesson',slides:[],subtitles:{slides:[]}}}});
  throw Error('Unexpected request '+u.pathname);
 });
 const sd=statusDom.window.document;await settle(60);
 for(const state of ['draft','pending_review','approved','published','archived']){
  const row=sd.querySelector('.catalog-card[data-kind="lesson"][data-status="'+state+'"]');
  assert(row,'lesson status '+state+' is mapped and visible');
  assert.notEqual(row.querySelector('.catalog-status').textContent,'unknown');
 }
 assert.equal(sd.querySelector('.catalog-global-notice').hidden,false);
 statusDom.window.confirm=()=>false;
 const guideEdit=sd.querySelector('.catalog-card[data-kind="guide"] .catalog-actions button:nth-child(2)');
 assert(guideEdit,'guide edit action exists for guide managers');
 guideEdit.click();
 assert.equal(sd.querySelector('#instructionEditor').open,false,'global guide edit requires and respects confirmation');
 const stateFilter=sd.querySelector('#catalogState');stateFilter.value='pending_review';stateFilter.dispatchEvent(new statusDom.window.Event('change',{bubbles:true}));await settle(50);
 assert.deepEqual([...sd.querySelectorAll('.catalog-card[data-kind="lesson"]')].map(x=>x.dataset.status),['pending_review']);
 assert(statusCalls.some(x=>x.pathname==='/api/platform/instructions'&&x.searchParams.get('state')==='pending_review'));
 statusDom.window.close();

 let meCalls=0,aManagerCalls=0,resolveAStarted;
 const aManagerStarted=new Promise(resolve=>{resolveAStarted=resolve});
 const race=mount(null,async url=>{
  const u=apiCall(url);
  if(u.pathname==='/api/platform/me'){meCalls++;return makeResponse(200,{member:meCalls<3?{tenant_id:tenantA,telegram_id:21,role:'owner'}:{tenant_id:tenantB,telegram_id:23,role:'employee'}})}
  const tenant=u.searchParams.get('expected_tenant_id');
  if(u.pathname==='/api/guides/public')return makeResponse(200,{success:true,items:[guide('Shared guide','/ops/shared')]});
  if(u.pathname==='/api/guides')return makeResponse(403,{success:false,error:'No edit'});
  if(tenant===tenantA&&u.pathname==='/api/platform/instruction-catalog')return makeResponse(200,{success:true,result:{items:[lesson(u.searchParams.get('q')?'late-a-published':'a-published',u.searchParams.get('q')?'Late Company A published lesson':'Company A published lesson')],next_offset:null}});
  if(tenant===tenantA&&u.pathname==='/api/platform/instructions'){
   aManagerCalls++;
   if(aManagerCalls===1)return makeResponse(200,{success:true,result:{items:[lesson('a-draft','Company A private draft','draft')],next_offset:null}});
   resolveAStarted();
   return new Promise(resolve=>{resolveAStarted=()=>resolve(makeResponse(200,{success:true,result:{items:[lesson('late-a-draft','Late Company A private draft','draft')],next_offset:null}}))});
  }
  if(u.pathname==='/api/platform/instruction-catalog')return makeResponse(200,{success:true,result:{items:[lesson('b-only','Late Company B lesson')],next_offset:null}});
  if(u.pathname==='/api/platform/instructions')return makeResponse(403,{success:false,error:'Read only'});
  throw Error('Unexpected race request '+u.pathname);
 });
 await settle(80);
 const rd=race.window.document;
 assert(rd.body.textContent.includes('Company A private draft'));
 assert.equal(rd.querySelector('#newLesson').hidden,false,'original owner controls are visible before switching');
 const searchInput=rd.querySelector('#catalogSearch');searchInput.value='late';searchInput.dispatchEvent(new race.window.Event('input',{bubbles:true}));
 await Promise.race([aManagerStarted,settle(1000).then(()=>{throw Error('A tenant refresh did not start its manager page')})]);
 race.window.document.dispatchEvent(new race.window.CustomEvent('tenant-profile',{detail:{tenant_id:tenantB}}));
 assert.equal(rd.querySelector('#newLesson').hidden,true,'tenant switch hides previous owner controls immediately');
 assert.equal(rd.body.textContent.includes('Company A private draft'),false,'tenant switch clears the prior private lesson immediately');
 await settle(80);
 assert(rd.body.textContent.includes('Late Company B lesson'));
 assert.equal(rd.querySelector('#newLesson').hidden,true,'employee from B remains read only');
 if(resolveAStarted)resolveAStarted();
 await settle(80);
 assert.equal(rd.body.textContent.includes('Late Company A private draft'),false,'late manager response cannot bleed A draft into B');
 assert.equal(rd.body.textContent.includes('Late Company A published lesson'),false,'late published response cannot bleed A lesson into B');
 assert.equal(rd.body.textContent.includes('Late Company B lesson'),true);
 race.window.close();

 let failMe=0;
 const failedRefresh=mount(null,async url=>{
  const u=apiCall(url);
  if(u.pathname==='/api/platform/me'){failMe++;return failMe===1?makeResponse(200,{member:{tenant_id:tenantA,telegram_id:24,role:'employee'}}):makeResponse(503,{error:'temporary failure'})}
  if(u.pathname==='/api/guides/public')return makeResponse(200,{success:true,items:[guide('Old company guide','/ops/old')]});
  if(u.pathname==='/api/guides')return makeResponse(403,{success:false,error:'No edit'});
  if(u.pathname==='/api/platform/instruction-catalog')return makeResponse(200,{success:true,result:{items:[lesson('old-only','Old company lesson')],next_offset:null}});
  if(u.pathname==='/api/platform/instructions')return makeResponse(403,{success:false,error:'Read only'});
  throw Error('Unexpected failure request '+u.pathname);
 });
 await settle(60);
 const fd=failedRefresh.window.document;
 assert(fd.body.textContent.includes('Old company lesson'));
 failedRefresh.window.document.dispatchEvent(new failedRefresh.window.CustomEvent('tenant-profile',{detail:{tenant_id:tenantB}}));
 await settle(60);
 assert.equal(fd.body.textContent.includes('Old company guide'),false,'a failed identity refresh clears the previous global/tenant view');
 assert.equal(fd.body.textContent.includes('Old company lesson'),false,'a failed identity refresh clears prior tenant lessons');
 assert.equal(fd.querySelector('#catalogStatus').textContent.includes('clearCatalogData'),false,'error handling does not throw while clearing prior results');
 failedRefresh.window.close();
 console.log('PASS: bounded lesson continuation, tenant-bound search, status mapping, global guide disclosure, atomic tenant switch, and failed-refresh clearing');
})().catch(error=>{console.error(error);process.exitCode=1});
