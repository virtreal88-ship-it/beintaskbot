/* Current full editor and actual Save click; only the local API boundary is replaced. */
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const settle=async()=>{for(let i=0;i<16;i++)await new Promise(resolve=>setImmediate(resolve));};
(async()=>{
 const dom=new JSDOM(fs.readFileSync('docs/tenant-app.html','utf8'),{url:'https://crm.test/app?view=ai_agents',runScripts:'outside-only'}),w=dom.window,d=w.document;
 try{
  d.querySelectorAll('.view').forEach(node=>node.classList.toggle('active',node.id==='view-ai_agents'));w.confirm=()=>true;w.scrollTo=()=>{};w.tenantViewDemand={watch(){}};w.tenantProfileSnapshot={member:{tenant_id:'settings-tenant',telegram_id:11,role:'owner',active:true},capabilities:{modules:{ai_agents:true}}};
  const targets=Array.from({length:6},(_,index)=>({channel_id:'dev-channel-'+index,conversation_id:'dev-'+index,customer_name:'Developer '+index}));let agents=[],calls=[];
  const ok=result=>({ok:true,status:200,json:async()=>({success:true,result})});
  w.fetch=async(url,options={})=>{const path=new URL(String(url),w.location).pathname,body=options.body?JSON.parse(options.body):undefined;calls.push({path,method:options.method||'GET',body});if(path.endsWith('/agents/settings'))return ok({enabled:true,auto_send_enabled:true});if(path.endsWith('/simulation/settings'))return ok({});if(path.endsWith('/agents/options'))return ok({connect_channels:[{id:'client-channel',name:'Client channel'}],models:['local-model'],pipelines:[],stages:[],channels:[]});if(path.endsWith('/dialog-consultations/destinations'))return ok({destinations:targets});if(path.endsWith('/agents')&&options.method==='POST'){const saved={...body,id:'agent-saved',version:1};agents=[saved];return ok(saved);}if(path.endsWith('/agents/agent-saved')&&options.method==='PATCH'){const saved={...body,id:'agent-saved',version:(agents[0]?.version||1)+1};agents=[saved];return ok(saved);}if(path.endsWith('/agents'))return ok(agents);if(path.endsWith('/pilot'))return ok({});throw Error('Unexpected fixture '+path);};
  w.eval(fs.readFileSync('docs/tenant-operator-i18n.js','utf8'));w.tenantI18n.setLanguage('ru');w.eval(fs.readFileSync('docs/tenant-ai-agents.js','utf8'));await settle();d.getElementById('aiNewAgent').click();
  const toggle=d.getElementById('aiCaseAutoDispatch'),picker=d.getElementById('aiCaseDeveloperRecipients'),reserve=d.getElementById('aiCaseDeveloperReserve'),mode=d.getElementById('aiAgentMode');
  const change=node=>node.dispatchEvent(new w.Event('change'));const save=async()=>{d.getElementById('aiSaveAgent').click();await settle();};const mutations=()=>calls.filter(call=>['POST','PATCH'].includes(call.method)&&/\/agents(?:\/agent-saved)?$/.test(call.path));
  assert.equal(toggle.checked,false);assert.equal(toggle.disabled,true);assert.equal(picker.multiple,false);assert.equal(reserve.value,'');
  d.getElementById('aiAgentName').value='Local agent';d.getElementById('aiAgentEnabled').checked=true;d.getElementById('aiAgentProvider').value='connect';change(d.getElementById('aiAgentProvider'));d.getElementById('aiChannelId').options[0].selected=true;d.getElementById('aiAgentModel').value='local-model';
  mode.value='automatic';change(mode);toggle.checked=true;change(toggle);await save();assert.equal(mutations().length,0,'primary is required');
  picker.value='dev-channel-0|dev-0';change(picker);reserve.value=picker.value;change(reserve);await save();assert.equal(mutations().length,0,'same primary and reserve is rejected');
  reserve.value='dev-channel-1|dev-1';change(reserve);await save();assert.equal(mutations().length,1);let policy=mutations()[0].body.response_policy;
  assert.equal(policy.developer_routing,'primary_reserve');assert.deepEqual(policy.developer_destinations,[{channel_id:'dev-channel-0',conversation_id:'dev-0'}]);assert.deepEqual(policy.developer_reserve_destination,{channel_id:'dev-channel-1',conversation_id:'dev-1'});assert.equal(policy.auto_case_dispatch_enabled,true);assert.equal('auto_case_dispatch_owner_id'in policy,false);
  assert.equal(picker.value,'dev-channel-0|dev-0');assert.equal(reserve.value,'dev-channel-1|dev-1','saved optional reserve restored');
  mode.value='manual';change(mode);await save();assert.equal(mutations().length,2);assert.equal(mutations()[1].body.response_policy.auto_case_dispatch_enabled,false);
  agents[0].mode='automatic';agents[0].response_policy={auto_case_dispatch_enabled:true,developer_routing:'primary_reserve',developer_destinations:[{channel_id:'revoked-channel',conversation_id:'opaque@g.us'}],developer_reserve_destination:null};
  const reopen=async()=>{d.dispatchEvent(new w.CustomEvent('tenant-ai-edit-created-agent',{detail:{agentId:'agent-saved'}}));await settle();};await reopen();
  assert.match(d.getElementById('aiCaseDispatchStatus').textContent,/Отправка заблокирована/);assert.doesNotMatch(d.getElementById('aiCaseDispatchStatus').textContent,/opaque@g.us|revoked-channel/);await save();assert.equal(mutations().length,2);
  mode.value='manual';change(mode);await save();assert.equal(mutations().length,3,'revoked unchanged route never blocks safe Manual stop');assert.equal(mutations()[2].body.response_policy.auto_case_dispatch_enabled,false);
  reserve.value='dev-channel-1|dev-1';change(reserve);await save();assert.equal(mutations().length,3,'changing reserve while primary revoked still revalidates routes');
  agents[0].mode='automatic';agents[0].response_policy={auto_case_dispatch_enabled:true,developer_destinations:[targets[0],targets[1]].map(({channel_id,conversation_id})=>({channel_id,conversation_id}))};await reopen();
  assert.equal(picker.value,'','legacy multiple selection is not silently migrated');await save();assert.equal(mutations().length,3,'unrelated save cannot silently pick first legacy recipient');
  mode.value='manual';change(mode);await save();assert.equal(mutations().length,4,'legacy list can be safely disabled unchanged');assert.equal(mutations()[3].body.response_policy.developer_destinations.length,2);assert.equal('developer_routing'in mutations()[3].body.response_policy,false);
  picker.value='dev-channel-1|dev-1';change(picker);await save();assert.equal(mutations().length,5);assert.equal(mutations()[4].body.response_policy.developer_destinations.length,1,'explicit primary selection migrates legacy list');
  assert.equal(calls.some(call=>/chat\/send|dialog-consultations$/.test(call.path)),false,'editor configuration never dispatches');
  console.log('PASS: primary/reserve editor preserves explicit routing, safe stop, revoked access and legacy migration');
 }finally{dom.window.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
