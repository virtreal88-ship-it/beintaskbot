// Real modal + real stage command helper, synthetic transport only.
const assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 const dom=new JSDOM(fs.readFileSync('docs/tenant-app.html','utf8'),{url:'https://fixture.test/app',runScripts:'outside-only'}),w=dom.window,d=w.document;
 try{
 d.documentElement.lang='ru';w.tenantI18n={language:'ru'};
 w.eval(fs.readFileSync('ui/src/kommo-deal-move.js','utf8').replace(/export function /g,'function ').replace(/export async function /g,'async function ')+'\nwindow.tenantKommoStageMove={canIdentify:canIdentifyKommoDeal,move:moveKommoDeal};');
 w.eval(fs.readFileSync('docs/tenant-kommo-deal-card.js','utf8'));
 const context={tenant_id:'fixture',user_id:11},calls=[];let mode='confirmed',command;
 w.fetch=async(url,options={})=>{
  calls.push({url:String(url),method:options.method||'GET'});
  if(options.method==='POST'){
   command=JSON.parse(options.body);
   return {ok:false,json:async()=>({success:false,error:mode==='denied'?'kommo_stage_forbidden':'board_outcome_unknown',keep_request:mode!=='denied'})};
  }
  assert.equal(new URL(String(url),w.location.origin).searchParams.get('request_id'),command.request_id);
  return mode==='confirmed'?{ok:true,json:async()=>({success:true,state:'confirmed',lead_id:7,pipeline_id:1,status_id:Number(command.stage_id),updated_at:101,source_revision:'b'.repeat(64)})}:{ok:false,json:async()=>({success:false,error:'board_outcome_unknown',keep_request:true})};
 };
 w.tenantTaskFlow={capture:()=>({}),current:()=>true};
 const policy={move_context:context,catalog:[{pipeline_id:1,name:'Sales',stages:[{stage_id:10,name:'Новое'},{stage_id:11,name:'В работе'}]}],move_stage_sources:[{pipeline_id:1,stage_id:10},{pipeline_id:1,stage_id:11}],move_stage_targets:[{pipeline_id:1,stage_id:10},{pipeline_id:1,stage_id:11}]};
 function open(){
  d.getElementById('dealModal').classList.add('show');
  const host=d.getElementById('modalContent');host.innerHTML='<div class="chat-placeholder"><div id="chatRows"></div><textarea id="chatText">draft</textarea></div>';
  w.tenantKommoDealCard.mount({host,deal:{kommo_lead_id:7,pipeline_id:1,status_id:10,stage_name:'Новое',raw:{updated_at:100},source_revision:'a'.repeat(64)},api:async()=>policy,isCurrent:()=>true});
 }
 async function choose(){d.querySelector('.deal-stage-trigger').click();d.querySelector('[data-stage-id="11"]').click();await settle();await settle();}
 open();await settle();await choose();
 assert.deepEqual(calls.map(x=>x.method),['POST','GET']);
 assert.equal(d.querySelector('.deal-stage-trigger').textContent,'В работе');assert.equal(w.sessionStorage.length,0);
 assert.equal(d.getElementById('chatText').value,'draft');assert.equal(d.querySelector('.deal-info-panel .meta'),null);
 // Uncertain outcome does not lie about state or issue another mutation on retry.
 calls.length=0;mode='unknown';open();await settle();await choose();
 assert.equal(d.querySelector('.deal-stage-trigger').textContent,'Новое');assert.equal(w.sessionStorage.length,1);
 await choose();assert.deepEqual(calls.map(x=>x.method),['POST','GET','GET']);
 assert.match(d.querySelector('.deal-stage-bar [role=status]').textContent,/неизвестен/);
 // Known preflight refusal is not auto-retried/read-verified.
 w.sessionStorage.clear();calls.length=0;mode='denied';open();await settle();await choose();
 assert.deepEqual(calls.map(x=>x.method),['POST']);assert.equal(w.sessionStorage.length,0);
 assert.equal(d.querySelector('.deal-stage-trigger').textContent,'Новое');
 console.log('PASS actual deal modal + command helper: bounded reconciliation, header update, draft, unresolved GET-only retry, refused preflight');
 }finally{w.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
