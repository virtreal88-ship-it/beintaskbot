const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const read=file=>fs.readFileSync(path.join(__dirname,'..',file),'utf8');
const settle=async()=>{for(let i=0;i<15;i++)await new Promise(r=>setImmediate(r));};
const A='00000000-0000-0000-0000-000000000101',CA='00000000-0000-0000-0000-000000000201',AGENT='00000000-0000-0000-0000-000000000301',ID='00000000-0000-0000-0000-000000000401';
function setup(){
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app?site_lang=ru',runScripts:'outside-only'}),w=dom.window,calls=[],errors=[];
 const member={tenant_id:A,telegram_id:11,role:'owner',active:true};w.confirm=()=>true;w.alert=()=>{};w.HTMLElement.prototype.scrollIntoView=function(){};w.addEventListener('error',e=>errors.push(e.error));
 let denyNext=false,failNext=false,holdNext=false,release=null,savedBinding={};
 let session={session:{id:ID,agent_id:AGENT,agent_name:'Support <img src=x>',state:'active',revision:1,block_reason:'',reason:''},turns:[],simulation:true};
 w.fetch=async(url,init={})=>{
  const u=new URL(String(url),'https://crm.test'),method=init.method||'GET',body=init.body?JSON.parse(init.body):{};calls.push({url:String(url),path:u.pathname,method,body});let value=[];
  if(u.pathname==='/api/platform/me')return {ok:true,json:async()=>({success:true,member,capabilities:{modules:{ai_agents:true}}})};
  if(u.pathname.endsWith('/agents/settings'))value={enabled:true,operating_mode:'test',emergency_stop:false,max_replies_per_chat:20};
  else if(u.pathname.endsWith('/agents/test-mode')){if(method==='PATCH')savedBinding=body;value={enabled:false,verified:false,company_phone:'+15555550102',client_phone:'+15555550101',connect:{enabled:true,verified:true,channel_id:CA,conversation_id:'client',agent_id:AGENT}};}
  else if(u.pathname.endsWith('/agents'))value=[{id:AGENT,name:'Support',enabled:true,mode:'manual',model:'fixture-model',scopes:[{provider:'connect',channel_id:CA}]}];
  else if(u.pathname.endsWith('/agents/options'))value={pipelines:[],stages:[],channels:[],connect_channels:[{id:CA,name:'Тестовый WhatsApp'}],models:['fixture-model']};
  else if(u.pathname.endsWith('/dialogs'))value={dialogs:[{conversation_id:'client',customer_name:'Тестовый клиент',phone:'+15555550101'}],next_offset:null};
  else if(u.pathname.endsWith('/testing/kommo-context'))value=u.searchParams.has('lead_id')?{account_domain:'own.kommo.com',contacts:[{id:8,name:'Client'}],talks:[{talk_id:'9',chat_id:'own-chat',channel_id:'wa'}]}:{account_domain:'own.kommo.com',deals:[{id:7,name:'Own deal'}]};
  else if(u.pathname.endsWith('/testing/sessions'))value=method==='POST'?session:[session.session];
  else if(u.pathname.endsWith('/turn')){
   if(denyNext){denyNext=false;return {ok:false,status:403,json:async()=>({success:false,error:'Access revoked'})};}
   if(failNext){failNext=false;throw new Error('Network error');}
   if(holdNext){holdNext=false;return await new Promise(resolve=>{release=()=>resolve({ok:true,status:200,json:async()=>({success:true,result:structuredClone(session)})});});}
   session.turns.push({id:body.request_id,client_text:body.text,response:'Test <script>unsafe</script>',state:'completed',trace:{simulation:true,agent_mode:'manual',delay_seconds:40,knowledge:[{title:'Router',content:'Reset <img src=x>'}],triggers:[{name:'First'}],actions:[{action:'create_task',state:'simulated',simulation:true}]}});session.session.revision++;value=session;
  }else if(u.pathname.endsWith('/control')){session.session.state={pause:'paused',handoff:'human',resume:'active'}[body.action];session.session.revision++;value=session;}
  else if(u.pathname.includes('/testing/sessions/'))value=session;
  return {ok:true,status:200,json:async()=>({success:true,result:structuredClone(value)})};
 };
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-ai-agents.js'));w.eval(read('docs/tenant-ai-testing.js'));
 return {w,dom,calls,errors,member,get binding(){return savedBinding},deny(){denyNext=true},fail(){failNext=true},hold(){holdNext=true},release(){release();}};
}
async function test(){
 const app=setup(),{w,calls}=app,d=w.document,$=id=>d.getElementById(id);await settle();await w.tenantAITesting.open();await settle();
 assert.ok($('aiAgentsModalBody').contains($('aiTestingCenter')));assert.equal($('testingTabVirtual').getAttribute('aria-selected'),'true');assert.equal($('testingReal').hidden,true);w.tenantI18n.setLanguage('en');await settle();assert.equal($('aiAgentsModalTitle').textContent,'Тестирование AI Agents');w.tenantI18n.setLanguage('ru');await settle();
 assert.equal($('testingVirtualAgent').value,AGENT);assert.match($('aiTestingCenter').textContent,/Test Mode/);assert.match($('aiTestingCenter').textContent,/Active Mode/);
 $('testingNewSession').click();await settle();assert.equal($('testingSessionHost').hidden,false);
 let create=calls.find(c=>c.method==='POST'&&c.path.endsWith('/testing/sessions'));assert.equal(create.body.agent_id,AGENT);assert.equal(create.body.expected_tenant_id,A);assert.equal(create.body.scope_index,null);
 $('testingText').value='Hello';$('testingSend').click();await settle();assert.match($('testingHistory').textContent,/Test <script>/);assert.equal($('testingHistory').querySelector('script,img'),null);assert.match($('testingHistory').textContent,/Router/);assert.match($('testingHistory').textContent,/First/);assert.match($('testingHistory').textContent,/Симуляция/);
 $('testingPause').click();await settle();assert.equal($('testingSend').disabled,true);$('testingResume').click();await settle();assert.equal($('testingSend').disabled,false);$('testingHandoff').click();await settle();assert.match($('testingSessionState').textContent,/Human Mode/);$('testingResume').click();await settle();
 app.fail();$('testingText').value='Same uncertain request';$('testingSend').click();await settle();assert.equal($('testingText').disabled,true);assert.equal($('testingRetry').hidden,false);const first=calls.filter(c=>c.path.endsWith('/turn')).at(-1);$('testingRetry').click();await settle();const retried=calls.filter(c=>c.path.endsWith('/turn')).at(-1);assert.equal(retried.body.request_id,first.body.request_id);assert.equal($('testingText').disabled,false);
 $('testingTabConnect').click();await settle();assert.equal($('aiTestSource').value,'connect');assert.equal($('testingKommoChooser').hidden,true);assert.match($('testingRealMode').textContent,/черновики/);$('aiTestModeSave').click();await settle();assert.equal(app.binding.agent_id,AGENT);assert.equal(app.binding.channel_id,CA);assert.equal(app.binding.conversation_id,'client');
 $('testingTabKommo').click();await settle();assert.equal($('testingKommoAccount').value,'own.kommo.com');$('testingKommoLead').value='7';$('testingKommoLead').dispatchEvent(new w.Event('change'));await settle();$('testingKommoContact').value='8';$('testingKommoContact').dispatchEvent(new w.Event('change'));$('testingKommoTalk').value='0';$('testingKommoTalk').dispatchEvent(new w.Event('change'));assert.equal($('aiTestLead').value,'7');assert.equal($('aiTestContact').value,'8');assert.equal($('aiTestTalk').value,'9');assert.equal($('aiTestChat').value,'own-chat');assert.equal($('aiTestChannel').value,'wa');assert.equal($('aiTestChat').readOnly,true);
 $('testingTabKommo').focus();$('testingTabKommo').dispatchEvent(new w.KeyboardEvent('keydown',{key:'End',bubbles:true,cancelable:true}));assert.equal($('testingTabVirtual').getAttribute('aria-selected'),'true');
 // A response from the previous tenant must never repopulate a switched account.
 app.hold();$('testingText').value='Old company draft';$('testingSend').click();await settle();w.document.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{member:{...app.member,tenant_id:'00000000-0000-0000-0000-000000000102'},capabilities:{modules:{ai_agents:false}}}}));app.release();await settle();assert.equal($('testingHistory').textContent,'');assert.equal($('testingText').value,'');assert.equal($('testingSessionHost').hidden,true);assert.equal($('aiTestChat').value,'');
 assert.ok(!calls.some(c=>c.path.includes('/crm/chat/send')||c.path.includes('/linear/')||c.path.includes('/conversations/')||c.path.endsWith('/agents/settings')&&c.method!=='GET'));
 assert.equal(app.errors.length,0,String(app.errors[0]));w.close();const denied=setup();await settle();await denied.w.tenantAITesting.open();denied.w.document.getElementById('testingNewSession').click();await settle();denied.deny();denied.w.document.getElementById('testingText').value='Revoked rights';denied.w.document.getElementById('testingSend').click();await settle();assert.equal(denied.w.document.getElementById('testingSessionHost').hidden,true);assert.equal(denied.w.document.getElementById('testingHistory').textContent,'');assert.equal(denied.errors.length,0);denied.w.close();console.log('PASS: testing tabs, selected agent, strict routes, private generation/traces, XSS, Pause/Handoff/Resume, uncertain retry, tenant switch, zero send/settings side effects.');
}
if(require.main===module)test().catch(e=>{console.error(e);process.exitCode=1});
module.exports={setup};
