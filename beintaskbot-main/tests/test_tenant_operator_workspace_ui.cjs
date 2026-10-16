const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const ROOT=path.join(__dirname,'..');
const read=file=>fs.readFileSync(path.join(ROOT,file),'utf8');
const settle=async()=>{for(let n=0;n<12;n++)await new Promise(resolve=>setImmediate(resolve));};
async function waitFor(check,label){for(let n=0;n<100;n++){if(check())return;await settle();}throw new Error('Not ready: '+label);}
const A='00000000-0000-0000-0000-000000000101',CA='00000000-0000-0000-0000-000000000201',AGENT='00000000-0000-0000-0000-000000000301',ID='00000000-0000-0000-0000-000000000401';
function setup(role='owner'){
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app?site_lang=ru',runScripts:'outside-only'}),w=dom.window,calls=[],alerts=[],errors=[];
 w.HTMLElement.prototype.scrollIntoView=function(){};w.alert=value=>alerts.push(value);w.confirm=()=>true;
 w.addEventListener('error',event=>errors.push(event.error));
 const member={tenant_id:A,telegram_id:11,role,permissions:['dialogs','support'],active:true,display_name:'Operator'};
 const modules={dialogs:true,support:true,ai_agents:role==='owner',tasks:false,deals:false,customers:false,settings:false,profile:true};
 let settings={enabled:true,operating_mode:'active',emergency_stop:false,auto_send_enabled:true,crm_actions_enabled:false,max_replies_per_chat:20,company_instructions:'Company policy',server_ai_agents_enabled:true,server_auto_send_enabled:true,runtime:{daily_ai_limit:50,platform_token_limit:5000}};
 let agent={id:AGENT,name:'Nizam Ai',enabled:true,mode:'manual',model:'test-model',instructions:'Original instructions',delay_seconds:40,language:'ru',style:'balanced',version:2,scopes:[{provider:'connect',channel_id:CA,pipeline_id:'',stage_id:'',instructions:'Keep existing scope rule'}],schedule:{timezone:'Asia/Baku',days:[0,1,2,3,4],start:'22:00',end:'08:00'}};
 let binding={enabled:false,verified:false,emergency_stop:true,connected_account_domain:'',company_phone:'+994100',client_phone:'+994200',channel_id:'kommo-channel',lead_id:55,contact_id:66,talk_id:'77',chat_id:'kommo-chat',connect:{enabled:true,verified:true,emergency_stop:false,channel_id:CA,conversation_id:'client',draft_only:true}};
 const dialog={provider:'connect',lead_id:41,channel_id:CA,conversation_id:'client',customer_name:'<img src=x onerror=alert(1)>',customer_kind:'contact',phone:'+994123456789',channel_name:'Night WhatsApp',agent_id:AGENT,agent_name:'Nizam Ai',state:'active',display_state:'active',ai_availability:'available',available_actions:['pause','handoff'],controls_allowed:true,resume_allowed:false,can_send:true,connected:true,channel_active:true,last_message_at:'2026-10-16T01:00:00Z',state_changed_at:'2026-10-16T01:00:00Z'};
 const ticket={id:ID,channel_id:CA,conversation_id:'client',lead_id:41,contact_name:'Customer',channel_name:'Night WhatsApp',description:'No internet',summary:'Router did not restart',suggestions:'Restart router',next_steps:'Check line',summary_state:'completed',source:'ai_handoff',status:'waiting',assigned_to:null,agent_id:AGENT,agent_name:'Nizam Ai',ai_state:'active',channel_active:true,created_at:'2026-10-16T00:00:00Z',updated_at:'2026-10-16T01:00:00Z'};
 const messages=[{id:'00000000-0000-0000-0000-000000000501',direction:'incoming',body:'Нет интернета',message_type:'text',happened_at:'2026-10-16T01:00:00Z'}];
 const result=value=>({success:true,result:value});
 w.fetch=async(url,init={})=>{
   const full=String(url),u=new URL(full,'https://crm.test'),method=init.method||'GET',body=init.body?JSON.parse(init.body):{};calls.push({url:full,method,body});let payload;
   if(u.pathname==='/api/platform/me')payload={success:true,member,capabilities:{modules},role_permissions:{},members:[],integrations:[]};
   else if(u.pathname.endsWith('/agents/settings')){if(method==='PATCH')settings={...settings,...body};payload=result(settings);}
   else if(u.pathname.endsWith('/agents/test-mode')){if(method==='PATCH'){if(body.provider==='connect')binding.connect={...binding.connect,...body};else binding={...binding,...body};}payload=result(binding);}
   else if(u.pathname.endsWith('/agents/options'))payload=result({pipelines:[],stages:[],channels:[],connect_channels:[{id:CA,name:'Night WhatsApp'}],models:['test-model']});
   else if(u.pathname.endsWith('/agents'))payload=result([agent]);
   else if(u.pathname.endsWith('/agents/'+AGENT)){agent={...agent,...body,version:3};payload=result(agent);}
   else if(u.pathname.includes('/knowledge')||u.pathname.endsWith('/triggers')||u.pathname.endsWith('/drafts'))payload=result([]);
   else if(u.pathname.endsWith('/dialogs/history'))payload=result({dialog:{...dialog},messages,next_before:null});
   else if(u.pathname.endsWith('/dialogs'))payload=result({dialogs:[{...dialog}],channels:[{id:'connect:'+CA,name:'Night WhatsApp'}],agents:[{id:AGENT,name:'Nizam Ai'}],attention_count:dialog.state==='active'?0:1,next_offset:null});
   else if(u.pathname.includes('/conversations/')){const action=u.pathname.split('/').pop();dialog.state={handoff:'human',pause:'paused',resume:'active'}[action];dialog.display_state=dialog.state;dialog.ai_availability=dialog.state==='active'?'available':'stopped';dialog.available_actions=dialog.state==='active'?['pause','handoff']:['resume'];dialog.resume_allowed=dialog.state!=='active';ticket.ai_state=dialog.state;payload=result({state:dialog.state});}
   else if(u.pathname.endsWith('/support-tickets'))payload=result({tickets:[{...ticket}],members:[{telegram_id:11,display_name:'Operator'}],channels:[{id:CA,name:'Night WhatsApp'}],unresolved_count:ticket.status==='resolved'?0:1,next_offset:null});
   else if(u.pathname.includes('/support-tickets/')){if(method==='PATCH')Object.assign(ticket,body);payload=result({ticket:{...ticket},messages,next_before:null});}
   else if(u.pathname.endsWith('/crm/chat/send'))payload={success:true,state:'accepted',message_id:'sent-1',origin:'connect'};
   else if(u.pathname.endsWith('/companies/manage'))payload={success:true,companies:[]};
   else if(u.pathname.endsWith('/connect/access'))payload={success:true,members:[],entitlement:{}};
   else if(u.pathname.endsWith('/integrations/connect'))payload={success:true,connected:true,channels:[]};
   else payload={success:true};
   return{ok:true,status:200,json:async()=>structuredClone(payload)};
 };
 w.eval(read('docs/tenant-operator-i18n.js'));
 const inline=[...w.document.querySelectorAll('script:not([src])')][0];w.eval(inline.textContent);
 for(const file of ['tenant-chat-send.js','tenant-ai-agents.js','tenant-operator-dialogs.js','tenant-support-inbox.js'])w.eval(read('docs/'+file));
 return{dom,w,calls,alerts,errors,member,modules,dialog,ticket,get agent(){return agent},get binding(){return binding}};
}
(async()=>{
 const app=setup(),{w,calls}=app,d=w.document;
 await waitFor(()=>d.querySelector('[data-edit-agent]')&&!d.querySelector('[data-ai-overview-settings]').disabled&&d.querySelector('[data-support-id]'),'workspace bootstrap');
 assert.equal(app.errors.length,0,String(app.errors[0]));
 d.querySelector('[data-view=dialogs]').click();await settle();assert.ok(d.querySelector('#view-dialogs').classList.contains('active'));assert.equal(d.querySelector('#operatorRows img'),null);d.querySelector('#operatorFiltersToggle').click();assert.equal(d.querySelector('#operatorFiltersToggle').getAttribute('aria-expanded'),'true');
 d.querySelector('[data-view=support]').click();await settle();assert.ok(d.querySelector('#view-support').classList.contains('active'));assert.ok(!d.querySelector('#view-ai_agents #supportInbox'));
 d.querySelector('[data-support-id]').click();await settle();const prior=calls.filter(call=>call.url.includes('/conversations/')&&call.method==='POST').length;
 d.querySelector('#supportTake').click();await settle();assert.equal(app.ticket.status,'in_progress');assert.equal(app.ticket.assigned_to,11);assert.equal(app.dialog.state,'active');assert.equal(calls.filter(call=>call.url.includes('/conversations/')&&call.method==='POST').length,prior);assert.match(d.querySelector('.support-ai-state').textContent,/AI не остановлен/);
 d.querySelector('#supportTakeover').click();await settle();assert.equal(app.dialog.state,'human');assert.equal(calls.filter(call=>call.url.includes('/crm/chat/send')).length,0);assert.equal(d.querySelector('#supportTakeover').disabled,true);
 d.querySelector('#supportOpenChat').click();await settle();assert.ok(d.querySelector('#dealModal').classList.contains('show'));assert.match(d.querySelector('#chatRows').textContent,/Нет интернета/);assert.ok(calls.some(call=>call.url.includes('/dialogs/history?')&&call.url.includes(CA)));assert.ok(!d.querySelector('#modalContent input[type=file]'));
 await d.querySelector('[data-operator-action=resume]').onclick();await settle();assert.equal(app.dialog.state,'active');await d.querySelector('[data-operator-action=pause]').onclick();await settle();assert.equal(app.dialog.state,'paused');
 d.querySelector('#chatText').value='Доброе утро';await d.querySelector('#sendChat').onclick();await settle();assert.ok(calls.some(call=>call.url.endsWith('/crm/chat/send')&&call.body.text==='Доброе утро'));assert.ok(app.alerts.some(text=>/Сообщение принято/.test(text)));
 d.querySelector('#closeModal').click();d.querySelector('[data-view=ai_agents]').click();await settle();
 assert.equal(d.querySelector('#aiOverviewCompanyMode').textContent,'Active Mode');assert.ok(!d.querySelector('#aiOverviewStatus'));assert.equal(d.querySelector('[data-ai-overview-settings]').textContent,'Настройки компании');
 d.querySelector('[data-ai-overview-settings]').click();assert.equal(d.querySelector('#aiAgentsModalTitle').textContent,'Настройки компании');assert.ok(d.querySelector('#aiAgentsModalBody #aiCompanySettings'));assert.ok(!d.querySelector('#aiCompanySettings #aiAgentMode'));
 d.querySelector('#aiCompanyInstructions').value='Updated company';await d.querySelector('#aiSaveSettings').onclick();assert.ok(calls.some(call=>call.method==='PATCH'&&call.url.endsWith('agents/settings')&&call.body.company_instructions==='Updated company'));
 d.querySelector('#aiOpenTestMode').click();await settle();assert.equal(d.querySelector('#aiTestSource').value,'connect');assert.equal(d.querySelector('#aiTestLead').closest('label').hidden,true);assert.equal(d.querySelector('#aiConnectTestChannel').tagName,'SELECT');assert.equal(d.querySelector('#aiTestModeEnabled').checked,true,'Connect flags must not inherit Kommo flags');assert.equal(d.querySelector('#aiEmergencyStop').checked,false);
 await d.querySelector('#aiTestModeSave').onclick();assert.ok(calls.some(call=>call.method==='PATCH'&&call.url.endsWith('/agents/test-mode')&&call.body.provider==='connect'&&call.body.channel_id===CA));assert.ok(!calls.some(call=>call.url.includes('/test-mode/verify')||call.url.endsWith('agents/options')),'Connect setup must not invoke Kommo refresh/verification');
 d.querySelector('#aiTestSource').value='kommo';d.querySelector('#aiTestSource').dispatchEvent(new w.Event('change'));assert.equal(d.querySelector('#aiTestLead').closest('label').hidden,false);assert.equal(d.querySelector('#aiTestModeEnabled').checked,false);assert.equal(d.querySelector('#aiEmergencyStop').checked,true);
 d.querySelector('#aiAgentsModalClose').click();d.querySelector('[data-edit-agent]').click();assert.ok(d.querySelector('#aiAgentsModalBody #aiAgentEditor'));assert.ok(!d.querySelector('#aiAgentEditor #aiOperatingMode'));
 d.querySelector('#aiTab-basics').focus();d.querySelector('#aiTab-basics').dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true,cancelable:true}));assert.equal(d.querySelector('#aiTab-scope').getAttribute('aria-selected'),'true');d.querySelector('#aiAgentInstructions').value='Updated agent instructions';d.querySelector('[data-ai-section-nav=schedule]').click();d.querySelector('[data-ai-section-nav=knowledge]').click();d.querySelector('[data-ai-section-nav=basics]').click();assert.equal(d.querySelector('#aiAgentInstructions').value,'Updated agent instructions');await d.querySelector('#aiSaveAgent').onclick();await settle();assert.equal(app.agent.mode,'manual');assert.equal(app.agent.delay_seconds,40);assert.equal(app.agent.instructions,'Updated agent instructions');assert.equal(app.agent.schedule.start,'22:00');assert.equal(app.agent.scopes[0].channel_id,CA);assert.equal(app.agent.scopes[0].instructions,'Keep existing scope rule','agent edits must preserve existing scope instructions');
 w.tenantI18n.setLanguage('en');await settle();assert.equal(d.querySelector('#aiOverviewCompanyMode').textContent,'Active Mode');assert.equal(d.querySelector('[data-ai-overview-settings]').textContent,'Company settings');w.tenantI18n.setLanguage('az');await settle();assert.equal(d.querySelector('[data-ai-overview-settings]').textContent,'Şirkət ayarları');assert.equal(app.errors.length,0,String(app.errors[0]));w.close();
 const operator=setup('manager');await waitFor(()=>operator.w.document.querySelector('[data-open-operator]'),'ordinary operator');assert.equal(operator.w.document.querySelector('#aiAgentsNav').hidden,true);operator.w.document.querySelector('[data-view=dialogs]').click();assert.ok(operator.w.document.querySelector('#view-dialogs').classList.contains('active'));assert.ok(operator.w.document.querySelector('#supportNav').hidden===false);operator.w.close();
 console.log('PASS: main navigation, rights, three settings levels, Connect/Kommo tests, take vs handoff, Pause/Resume, linked chat, setting preservation and RU/AZ/EN.');
})().catch(error=>{console.error(error);process.exitCode=1;});
