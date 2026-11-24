const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.JSDOM_PATH||'../.support-test-deps/node_modules/jsdom');
const root=path.join(__dirname,'..'),read=file=>fs.readFileSync(path.join(root,file),'utf8');
const tenant='00000000-0000-0000-0000-000000000101';
const shell='<button class="app-avatar-button" type="button" aria-controls="appUserMenu">SE</button><div id="appUserMenu"><div><span id="appUserName">Synthetic Employee</span></div></div>';
const doc=read('docs/tenant-app.html').replace('</body>',shell+'</body>');
const tick=async()=>{for(let i=0;i<18;i++)await new Promise(resolve=>setImmediate(resolve));};
const response=(status,data)=>({ok:status===200,status,json:async()=>data});
const profile={member:{tenant_id:tenant,telegram_id:33,role:'operator',permissions:['dialogs'],active:true}};
const unavailable=response(200,{success:true,result:{available:false}});

async function run({globalReply=unavailable,canUpdate=true,canOffline=false,routingEnforced=true}={}){
 const dom=new JSDOM(doc,{url:'https://crm.test/app?view=dialogs',runScripts:'outside-only'}),w=dom.window;
 let status=null;
 const calls=[],posts=[];
 w.tenantProfileSnapshot=profile;
 w.tenantI18n={language:'ru'};
 w.fetch=async(url,options={})=>{
  const target=new URL(url,w.location.origin);
  calls.push(target.pathname);
  if(target.pathname==='/api/platform/ai/profile/availability')return globalReply;
  assert.equal(target.pathname,'/api/platform/ai/dialogs/presence');
  if(options.method==='POST'){
   const body=JSON.parse(options.body);posts.push(body);status=body.status;
   return response(200,{success:true,result:{available:true,status,configured:true,can_set_offline:canOffline,routing_enforced:routingEnforced}});
  }
  assert.equal(target.searchParams.get('expected_tenant_id'),tenant);
  assert.equal(target.searchParams.get('expected_user_id'),'33');
  return response(200,{success:true,result:{available:true,status:status??'online',configured:status!==null,can_update:canUpdate,can_set_offline:canOffline,routing_enforced:routingEnforced}});
 };
 w.eval(read('docs/tenant-profile-work-status.js'));await tick();
 return {w,calls,posts,el:id=>w.document.getElementById(id)};
}

(async()=>{
 assert.doesNotMatch(read('docs/tenant-operator-dialogs.js'),/operatorPresenceOpen|operatorPresenceModal/);
 const {w,calls,posts,el}=await run();
 assert.deepEqual(calls,['/api/platform/ai/profile/availability','/api/platform/ai/dialogs/presence']);
 const control=el('appWorkStatus'),trigger=el('appWorkStatusTrigger'),options=el('appWorkStatusOptions');
 assert.equal(control.hidden,false,'legacy operator retains status access in the header');
 assert.match(trigger.textContent,/В сети/);assert.equal(posts.length,0);
 trigger.click();assert.equal(options.hidden,false);
 assert.equal(options.querySelector('button[data-status=offline]'),null,'legacy endpoint cannot select Offline');
 assert.match(el('appWorkStatusConfirm').textContent,/Подтвердите статус/);
 options.querySelector('button[data-status=online]').click();await tick();
 assert.deepEqual(posts,[{expected_tenant_id:tenant,expected_user_id:33,status:'online'}],'legacy default must be explicitly confirmable without a status change');
 assert.equal(options.hidden,true);assert.equal(el('appWorkStatusConfirm').hidden,true);
 trigger.click();options.querySelector('button[data-status=lunch]').click();await tick();
 assert.equal(posts.at(-1).status,'lunch');
 assert.match(trigger.textContent,/Обед/);
 w.close();
 for(const settings of [{canUpdate:false},{globalReply:response(403,{success:false})},{globalReply:response(503,{success:false})}]){
  const blocked=await run(settings);assert.equal(blocked.el('appWorkStatus').hidden,true);
  if(settings.globalReply)assert.equal(blocked.calls.length,1,'no fallback on profile auth/temporary errors');
  blocked.w.close();
 }
 const expanded=await run({canOffline:true});expanded.el('appWorkStatusTrigger').click();
 assert.ok(expanded.el('appWorkStatusOptions').querySelector('button[data-status=offline]'));
 expanded.w.close();
 console.log('PASS: legacy avatar fallback, tenant scope, explicit default confirmation, capability and error handling.');
})().catch(error=>{console.error(error);process.exitCode=1;});
