const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const read=file=>fs.readFileSync(path.join(__dirname,'..',file),'utf8');
const settle=async()=>{for(let i=0;i<15;i++)await new Promise(r=>setImmediate(r));};
const A='00000000-0000-0000-0000-000000000101',AGENT='00000000-0000-0000-0000-000000000301',ID='00000000-0000-0000-0000-000000000401';
function setup(){
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app?site_lang=ru',runScripts:'outside-only'}),w=dom.window,calls=[],errors=[];
 const member={tenant_id:A,telegram_id:11,role:'owner',active:true};w.confirm=()=>true;w.HTMLElement.prototype.scrollIntoView=function(){};w.addEventListener('error',e=>errors.push(e.error));
 let denyNext=false,holdNext=false,release=null;
 let session={session:{id:ID,agent_id:AGENT,agent_name:'AI seller',state:'active',revision:1,block_reason:'',reason:''},turns:[],simulation:true};
 w.fetch=async(url,init={})=>{const u=new URL(String(url),'https://crm.test'),method=init.method||'GET',body=init.body?JSON.parse(init.body):{};calls.push({url:String(url),path:u.pathname,method,body});let value=[];
  if(u.pathname==='/api/platform/me')return{ok:true,json:async()=>({success:true,member,capabilities:{modules:{ai_agents:true}}})};
  if(u.pathname.endsWith('/agents'))value=[{id:AGENT,name:'AI seller',enabled:true,mode:'manual',model:'fixture-model',scopes:[{provider:'connect',channel_id:'channel-demo'}]}];
  else if(u.pathname.endsWith('/testing/sessions')&&method==='GET')value=[session.session];
  else if(u.pathname.endsWith('/testing/sessions')&&method==='POST')value=session;
  else if(u.pathname.endsWith('/turn')){if(denyNext){denyNext=false;return{ok:false,status:403,json:async()=>({success:false,error:'Access revoked'})};}if(holdNext){holdNext=false;return await new Promise(resolve=>{release=()=>resolve({ok:true,status:200,json:async()=>({success:true,result:structuredClone(session)})});});}session.turns.push({id:body.request_id,client_text:body.text,response:'Test <script>unsafe</script>',state:'completed',trace:{simulation:true,model:'fixture-model',agent_mode:'manual',delay_seconds:2,knowledge:[{title:'Returns',content:'Return within 14 days'}],triggers:[{name:'Price question'}],actions:[{action:'call_expert',state:'simulated',simulation:true}]}});session.session.revision++;value=session;}
  else if(u.pathname.endsWith('/control')){session.session.state={pause:'paused',handoff:'human',resume:'active'}[body.action];session.session.revision++;value=session;}
  else if(u.pathname.includes('/testing/sessions/'))value=session;
  return{ok:true,status:200,json:async()=>({success:true,result:structuredClone(value)})};
 };
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-ai-agents.js'));w.eval(read('docs/tenant-ai-testing.js'));
 return{w,dom,calls,errors,member,session,deny(){denyNext=true},hold(){holdNext=true},release(){release?.();}};
}
async function test(){
 const app=setup(),{w,calls}=app,d=w.document,$=id=>d.getElementById(id);await settle();await w.tenantAITesting.open();await settle();
 assert.ok($('view-ai_testing').classList.contains('active'));assert.equal($('aiTestingCenter').parentElement.id,'aiTestingPage');assert.ok(!$('aiAgentsModalBody').contains($('aiTestingCenter')));
 assert.equal($('testingVirtualAgent').value,AGENT);assert.equal($('testingTabKommo'),null);assert.equal($('testingTabConnect'),null);assert.equal($('aiTestModePanel'),null);assert.match($('aiTestingCenter').textContent,/расходует лимит компании/);
 w.tenantI18n.setLanguage('en');await settle();assert.equal($('aiTestingCenter').querySelector('h1').textContent,'AI testing');assert.match($('testingNewSession').textContent,/New test chat/);w.tenantI18n.setLanguage('ru');await settle();
 $('testingNewSession').click();await settle();assert.equal($('testingChatTitle').textContent,'AI seller');const create=calls.find(c=>c.method==='POST'&&c.path.endsWith('/testing/sessions'));assert.equal(create.body.agent_id,AGENT);assert.equal(create.body.expected_tenant_id,A);assert.equal(create.body.scope_index,null);
 app.session.session.block_reason='server_ai_disabled';$('testingRead').click();await settle();w.tenantI18n.setLanguage('az');await settle();assert.match($('testingSessionState').textContent,/Aktiv · virtual sessiya/);assert.match($('testingSessionState').textContent,/AI serverdə söndürülüb/);assert.equal($('testingVirtualScope').options[0].textContent,'Xarici mənbə yoxdur');w.tenantI18n.setLanguage('en');await settle();assert.match($('testingSessionState').textContent,/AI is disabled on the server/);w.tenantI18n.setLanguage('ru');await settle();assert.match($('testingSessionState').textContent,/AI выключен на сервере/);app.session.session.block_reason='';$('testingRead').click();await settle();
 $('testingText').value='What is your return policy?';$('testingSend').click();await settle();assert.match($('testingHistory').textContent,/Test <script>/);assert.equal($('testingHistory').querySelector('script,img'),null);assert.match($('testingHistory').textContent,/Returns/);assert.match($('testingHistory').textContent,/Price question/);assert.match($('testingHistory').textContent,/Вызвать внутреннего эксперта/);assert.match($('testingHistory').textContent,/Внешние отправки и изменения: 0/);w.tenantI18n.setLanguage('az');await settle();assert.match($('testingHistory').textContent,/Daxili ekspert çağır/);w.tenantI18n.setLanguage('en');await settle();assert.match($('testingHistory').textContent,/Call internal expert/);w.tenantI18n.setLanguage('ru');await settle();
 const turn=calls.find(c=>c.path.endsWith('/turn'));assert.equal(turn.body.expected_tenant_id,A);assert.equal(calls.filter(c=>c.path.endsWith('/turn')).length,1);assert.ok(!calls.some(c=>c.path.includes('/crm/chat/send')||c.path.includes('/conversations/')));
 $('testingPause').click();await settle();assert.equal($('testingSend').disabled,true);$('testingResume').click();await settle();assert.equal($('testingSend').disabled,false);$('testingHandoff').click();await settle();assert.match($('testingSessionState').textContent,/Передано сотруднику/);$('testingResume').click();await settle();
 // Failed network outcome retries the same request id only after explicit staff action.
 app.hold();$('testingText').value='Same uncertain request';$('testingSend').click();await settle();assert.equal($('testingRetry').hidden,false);const first=calls.filter(c=>c.path.endsWith('/turn')).at(-1);app.release();await settle();
 // Permission loss/tenant switch invalidates the pending UI data.
 app.hold();$('testingText').value='Old company request';$('testingSend').click();await settle();w.document.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{member:{...app.member,tenant_id:'00000000-0000-0000-0000-000000000102'},capabilities:{modules:{ai_agents:false}}}}));app.release();await settle();assert.equal($('testingHistory').textContent,'');assert.equal($('testingText').value,'');assert.equal($('testingSend').disabled,true);
 assert.ok(!calls.some(c=>c.path.includes('/crm/chat/send')||c.path.includes('/linear/')||c.path.includes('/agents/test-mode')||c.path.includes('/agents/drafts')||c.path.includes('/testing/kommo-context')));assert.equal(app.errors.length,0,String(app.errors[0]));w.close();
 const denied=setup();await settle();await denied.w.tenantAITesting.open();await settle();denied.deny();denied.w.document.getElementById('testingText').value='Revoked rights';denied.w.document.getElementById('testingSend').click();await settle();assert.equal(denied.w.document.getElementById('testingHistory').textContent,'');assert.equal(denied.errors.length,0);denied.w.close();console.log('PASS: virtual-only UI, session creation/history, selected agent, trace/XSS, explicit simulated controls, retry idempotency, tenant switch, no CRM/send/test-binding endpoints.');
}
if(require.main===module)test().catch(e=>{console.error(e);process.exitCode=1});module.exports={setup};
