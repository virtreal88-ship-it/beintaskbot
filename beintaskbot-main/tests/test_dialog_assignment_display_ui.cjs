const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const root=path.resolve(__dirname,'..'),read=p=>fs.readFileSync(path.join(root,p),'utf8');
const settle=async()=>{for(let i=0;i<20;i++)await new Promise(r=>setImmediate(r));};
(async()=>{
 for(const language of ['ru','az','en'])for(const assigned of [true,false])for(const restricted of [false,true]){
  const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app?site_lang='+language,runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[];
  const member={tenant_id:'t',telegram_id:11,role:'employee',permissions:['dialogs'],active:true};
  w.tenantProfileSnapshot={member,capabilities:{modules:{dialogs:true},chat_ai:false}};
  const route={provider:'connect',agent_id:!restricted&&assigned?'agent':null,agent_name:!restricted&&assigned?'Support AI':null,agent_status:restricted?'restricted':'manual',assignment_display:restricted&&assigned?{assigned:true,name:'Support AI'}:undefined,channel_id:'channel',conversation_id:'conversation',lead_id:41,customer_name:'Customer',channel_name:'Support',state:'active',display_state:'manual',controls_allowed:false,can_send:true,can_use_copilot:false,available_actions:[]};
  w.fetch=async(url,options={})=>{const p=new URL(url,w.location).pathname;calls.push(p);let result={};if(p.endsWith('/dialogs/history'))result={dialog:route,messages:[],next_before:null};else if(p.endsWith('/dialogs'))result={dialogs:[route],channels:[],agents:[],attention_count:0};return{ok:true,status:200,json:async()=>({success:true,result})};};
  w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));await settle();await w.tenantOperator.openDialog(route);await settle();
  const expected=restricted?{ru:'Сведения об агенте недоступны',az:'Agent məlumatları əlçatan deyil',en:'Agent details unavailable'}[language]:assigned?{ru:'Нет права на использование AI-помощника',az:'AI köməkçidən istifadə icazəsi yoxdur',en:'You do not have permission to use the AI assistant'}[language]:{ru:'Сначала назначьте агента диалогу',az:'Əvvəlcə dialoqa agent təyin edin',en:'Assign an agent to this conversation first'}[language];
  assert.equal(d.querySelector('#operatorAIReplyStatus').textContent,expected);
  for(const id of ['operatorAIReply','operatorCopilotToggle','operatorCopilotVoiceToggle','operatorCopilotAsk']){const button=d.getElementById(id);assert.equal(button.disabled,true);assert.equal(button.title,expected);button.click();}
  // Programmatic handlers cannot circumvent the permission guard either.
  d.getElementById('operatorCopilotVoiceToggle').onclick();await settle();
  assert.equal(calls.some(p=>p.includes('/copilot')||p.endsWith('/messages/send')),false);
  if(assigned)assert.match(d.querySelector('.operator-conversation-header').textContent,/Support AI/);
  if(restricted&&!assigned)assert.doesNotMatch(d.querySelector('.operator-conversation-header').textContent,/Агент не назначен|Agent təyin edilməyib|No agent assigned/);
  dom.window.close();
 }
 console.log('Copilot permission labels: RU/AZ/EN, assigned and unassigned; no dispatch passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
