const assert=require('node:assert/strict'),fs=require('node:fs'),
 {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
(async()=>{
 const dom=new JSDOM('<div id="integrationSettingsHost"></div>',{
  url:'https://crm.test/app?view=integrations',runScripts:'outside-only'});
 const w=dom.window,d=w.document,calls=[];
 let watch;
 w.tenantViewDemand={watch(value){watch=value;}};
 w.fetch=async(path,opt={})=>{
  calls.push({path,method:opt.method});
  const integration={status:'not_connected',connected:false,configured:false,pending_auth_received:true,
   pending_auth_received_at:'rev-a',pending_public_link_proof_available:true};
  return {ok:true,json:async()=>({success:true,integration,accounts:[],links:[],members:[]})};
 };
 w.eval(fs.readFileSync('docs/tenant-beinsystems-operator-settings.js','utf8'));
 const owner={member:{tenant_id:'tenant',telegram_id:33,role:'owner'},integrations:[{provider:'beinsystems_operator'}]};
 await watch.activate(owner);
 assert(!d.querySelector('[data-public-link-proof]'));
 assert(!d.querySelector('[data-verify-public-link]'));
 assert(!calls.some(x=>x.method==='POST'),'a supported proof capability does not trigger a background verification');
 await watch.activate({...owner,member:{...owner.member,role:'operator'}});
 assert(!d.querySelector('[data-public-link-proof]'));
 assert(!calls.some(x=>x.method==='POST'));
 dom.window.close();console.log('PASS: public-link diagnostics are not part of the minimal Operator card');
})().catch(error=>{console.error(error);process.exitCode=1;});
