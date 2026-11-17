const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.SUPPORT_CASES_JSDOM||'jsdom');
const read=p=>fs.readFileSync(p,'utf8'),tick=()=>new Promise(r=>setTimeout(r,0));
async function scenario({confirm=true,fail=null,cycle=false,scopeChange=false}={}){
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://example.test/app?view=dialogs',runScripts:'outside-only'}),w=dom.window,d=w.document;
 const profile={member:{tenant_id:'tenant',telegram_id:33,role:'owner',active:true},capabilities:{modules:{dialogs:true,support:true}}};
 const route=(id,state='waiting',owner=33)=>({provider:'connect',lead_id:id,channel_id:'channel-'+id,conversation_id:'client-'+id,state:'human',agent_name:'Agent',can_send:false,lifecycle_enabled:true,lifecycle:{id:'episode-'+id,state,operator_id:owner,revision:4,can_dismiss:state==='waiting',can_close:state==='accepted'&&owner===33,last_inbound_id:'inbound-'+id}});
 const a=route(1),b=route(2,'accepted'),other=route(3,'accepted',44),closed=route(4,'closed'),group={...a,group_sources:[a,b,other]};
 const routes=new Map([a,b,other,closed].map(r=>[String(r.lead_id),r]));
 const calls=[],prompts=[];let bulk=false;
 w.tenantProfileSnapshot=profile;w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});w.alert=()=>{};w.confirm=text=>{prompts.push(text);return confirm;};
 w.fetch=async(url,options={})=>{
  url=String(url);const body=options.body?JSON.parse(options.body):null;calls.push({url,body});let result;
  if(url.endsWith('dialogs_lifecycle')){
   if(fail&&body.lead_id===1){if(fail==='network')throw Error('Network interrupted');return{ok:false,status:fail,json:async()=>({success:false,error:'Changed'})};}
   result={lifecycle:{state:body.action==='dismiss'?'dismissed':'closed',revision:5}};
  }else if(url.includes('/dialogs/history')){
   const q=new URL(url,'https://example.test').searchParams;result={dialog:routes.get(q.get('lead_id')),messages:[],next_before:null};
   if(scopeChange){w.document.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{...profile,member:{...profile.member,telegram_id:99}}}));}
  }else if(url.includes('/dialogs?')){
   const offset=new URL(url,'https://example.test').searchParams.get('offset');
   result={dialogs:offset==='10'?[a,closed]:[group],channels:[],agents:[],unread_count:17,next_offset:bulk?(cycle?0:offset==='10'?null:10):null};
  }else throw Error('Unexpected request '+url);
  return{ok:true,status:200,json:async()=>({success:true,result})};
 };
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));await tick();await tick();
 await w.tenantOperator.refresh();bulk=true;calls.length=0;
 d.querySelector('#operatorSearch').value='filter ignored';
 const button=d.querySelector('#operatorCompleteAll');assert(button);button.click();button.click();
 for(let i=0;i<30&&button.disabled;i++)await tick();
 const mutations=calls.filter(c=>c.body),notice=d.querySelector('#operatorNotice').textContent;
 assert(!calls.some(c=>c.url.includes('/dialogs/read')),'bulk completion never marks read');
 assert(!calls.some(c=>/send|copilot|support-tickets/.test(c.url)),'bulk never sends messages or creates cases');
 if(cycle||scopeChange){assert.equal(mutations.length,0);assert.equal(prompts.length,0);}
 else{
  assert.equal(prompts.length,1,'double clicks get one confirmation');assert.match(prompts[0],/2/,'deduplicated source count excludes others and closed');
  assert(calls.filter(c=>c.url.includes('/dialogs?')).every(c=>!c.url.includes('filter')));
  if(!confirm)assert.equal(mutations.length,0,'cancel is read-only');
  else{
   assert.equal(mutations.length,fail==='network'||fail===403?1:2,notice);
   assert.equal(mutations[0].body.action,'dismiss');assert.equal(mutations[0].body.message_id,'inbound-1');assert.equal(mutations[0].body.expected_revision,4);
   assert.equal(mutations[0].body.expected_tenant_id,'tenant');assert.equal(mutations[0].body.expected_user_id,33);
   if(mutations[1])assert.equal(mutations[1].body.action,'close');
   assert(mutations.every(c=>[1,2].includes(c.body.lead_id)),'never closes another employee dialogue');
   if(fail===409)assert.match(notice,/1/,'partial conflict result is shown');
   if(fail==='network')assert.match(notice,/1/,'network ambiguity and unprocessed remainder shown');
  }
 }
 assert(!button.disabled);dom.window.close();
}
(async()=>{for(const opts of [{},{confirm:false},{fail:409},{fail:403},{fail:'network'},{cycle:true},{scopeChange:true}])await scenario(opts);console.log('bulk dialogue lifecycle UI: 7 scenarios passed');})().catch(e=>{console.error(e);process.exitCode=1;});
