const assert=require('node:assert/strict'),fs=require('node:fs'),
 {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
(async()=>{
 const dom=new JSDOM('<div id="integrationSettingsHost"></div>',
  {url:'https://crm.test/app?view=integrations',runScripts:'outside-only'});
 const w=dom.window,d=w.document,calls=[];
 let watch,ask=false,finish;
 const state={status:'not_connected',pending_auth_received:true,pending_auth_received_at:'rev-a',
  pending_public_link_proof_available:false};
 const response=(value,status=200)=>({ok:status===200,status,json:async()=>value});
 w.tenantViewDemand={watch(value){watch=value;}};
 w.confirm=()=>ask;
 w.fetch=async(path,opt)=>{
  if(path==='/api/platform/integrations/beinsystems-operator'&&opt.method==='POST'){
   calls.push(JSON.parse(opt.body));
   return new Promise(resolve=>{finish=resolve;});
  }
  if(path==='/api/platform/integrations/beinsystems-operator')return response({success:true,integration:{...state}});
  return response({success:true,accounts:[],links:[],members:[]});
 };
 w.eval(fs.readFileSync('docs/tenant-beinsystems-operator-settings.js','utf8'));
 const profile={member:{tenant_id:'tenant',telegram_id:33,role:'owner'},integrations:[{provider:'beinsystems_operator'}]};
 const section=()=>d.querySelector('[data-public-link-proof]');
 const button=()=>d.querySelector('[data-verify-public-link]');
 await watch.activate(profile);
 assert(section().hidden);assert.deepEqual(calls,[]);
 state.pending_public_link_proof_available=true;
 await watch.activate({...profile,member:{...profile.member,role:'operator'}});
 assert(!section());assert.deepEqual(calls,[]);
 await watch.activate(profile);assert(!section().hidden);assert.deepEqual(calls,[]);
 button().click();assert.equal(calls.length,0,'declining confirmation does not call API');
 ask=true;button().click();button().click();
 assert.deepEqual(calls,[{action:'verify_public_link_proof',candidate_received_at:'rev-a',
  expected_tenant_id:'tenant',expected_user_id:33}]);
 finish(response({success:true,public_link_proof:{verified:true,candidate_received_at:'rev-a'}}));await tick();
 assert.match(section().textContent,/Интеграция не активирована/);
 assert(button().disabled);
 state.pending_auth_received_at='rev-b';
 await watch.activate(profile);button().click();
 state.pending_auth_received_at='rev-c';
 await watch.activate(profile);
 finish(response({success:true,public_link_proof:{verified:true,candidate_received_at:'rev-b'}}));await tick();
 assert(!section().textContent.includes('Интеграция не активирована'),'late previous candidate is never displayed');
 button().click();finish(response({success:false,error:'Проверка не подтверждена'},502));await tick();
 assert(button().disabled,'ambiguous outcome is never automatically retried');
 assert.equal(calls.length,3);
 dom.window.close();console.log('PASS: owner-only explicit demo proof UI and revision fences');
})().catch(err=>{console.error(err);process.exitCode=1;});
