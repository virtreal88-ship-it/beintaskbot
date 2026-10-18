const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.BEINTASKBOT_TEST_DEPS||'../.support-test-deps/node_modules/jsdom');
const root=path.join(__dirname,'..'),read=file=>fs.readFileSync(path.join(root,file),'utf8');
const tick=async()=>{for(let i=0;i<18;i++)await new Promise(done=>setImmediate(done));};
const TENANT='00000000-0000-0000-0000-000000000101',CHANNEL='00000000-0000-0000-0000-000000000201',AGENT='00000000-0000-0000-0000-000000000301',DRAFT='00000000-0000-0000-0000-000000000401';
(async()=>{
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app?site_lang=ru',runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[];w.confirm=()=>true;w.alert=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});w.requestAnimationFrame=fn=>w.setTimeout(fn,0);
 let detail={id:DRAFT,version:4,context_token:'token-1',customer_name:'Али',channel_name:'Ночной WhatsApp',agent_name:'Nizam AI',draft:'Проверьте подключение принтера.',reason:{code:'expert_called'},source_messages:[{id:'00000000-0000-0000-0000-000000000501',text:'Касса не печатает чек',message_type:'text',happened_at:'2026-10-18T10:00:00Z',media_available:false},{id:'00000000-0000-0000-0000-000000000502',text:'Это срочно',message_type:'text',happened_at:'2026-10-18T10:01:00Z'}],dialog_route:{provider:'connect',channel_id:CHANNEL,conversation_id:'group@g.us',source_message_id:'00000000-0000-0000-0000-000000000501'}};
 const result=value=>({success:true,result:value});
 w.fetch=async(url,init={})=>{const u=new URL(String(url),'https://crm.test'),method=init.method||'GET',body=init.body?JSON.parse(init.body):{};calls.push({path:u.pathname,method,body});let out=[];
  if(u.pathname==='/api/platform/me')return{ok:true,json:async()=>({success:true,member:{tenant_id:TENANT,telegram_id:11,role:'owner',active:true},capabilities:{modules:{ai_agents:true}}})};
  if(u.pathname.endsWith('/agents/settings'))out={enabled:true,operating_mode:'active',emergency_stop:false};
  else if(u.pathname.endsWith('/agents/options'))out={pipelines:[],stages:[],channels:[],connect_channels:[{id:CHANNEL,name:'Ночной WhatsApp'}],models:[]};
  else if(u.pathname.endsWith('/agents'))out=[{id:AGENT,name:'Nizam AI',enabled:true,mode:'manual',version:2,scopes:[]}];
  else if(u.pathname.endsWith('/agents/drafts'))out=[{id:DRAFT,agent_id:AGENT,agent_name:'Nizam AI',draft:detail.draft,state:'pending',created_at:'2026-10-18T10:01:00Z',provider:'connect',channel_id:CHANNEL,conversation_id:'group@g.us',send_allowed:true}];
  else if(u.pathname.endsWith('/drafts/'+DRAFT))out=detail;
  else if(u.pathname.endsWith('/drafts/'+DRAFT+'/edit')){assert.equal(body.expected_version,4);detail={...detail,draft:body.draft,version:5,edited_by_employee:true};out=detail;}
  else if(u.pathname.endsWith('/drafts/'+DRAFT+'/send')){assert.equal(body.expected_version,5);assert.equal(body.expected_context_token,'token-1');out={state:'sent'};}
  else if(u.pathname.endsWith('/drafts/'+DRAFT+'/review')){assert.equal(body.expected_version,5);out={state:'rejected'};}
  return{ok:true,status:200,json:async()=>result(out)};
 };
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval([...d.querySelectorAll('script:not([src])')][0].textContent);w.eval(read('docs/tenant-ai-state.js'));w.eval(read('docs/tenant-ai-agents.js'));await tick();
 d.querySelector('[data-edit-agent]').click();await tick();d.querySelector('[data-ai-section-nav=dialogs]').click();await tick();
 assert.match(d.querySelector('#aiDraftList').textContent,/Али/);assert.match(d.querySelector('#aiDraftList').textContent,/Касса не печатает чек/);assert.match(d.querySelector('#aiDraftList').textContent,/Требуется эксперт/);assert.equal(d.querySelector('#aiDraftList').textContent.includes(DRAFT),false,'technical id is not rendered');
 d.querySelector('[data-draft-edit]').click();await tick();const input=d.querySelector('[data-draft-input]');input.value='Проверьте кабель принтера.';input.dispatchEvent(new w.Event('input'));assert.match(d.querySelector('#aiDraftList').textContent,/несохранённые/i);d.querySelector('[data-draft-save]').click();await tick();assert.match(d.querySelector('#aiDraftList').textContent,/Изменено сотрудником/);
 d.querySelector('[data-draft-send]').click();await tick();assert.ok(calls.some(call=>call.path.endsWith('/send')&&call.body.expected_context_token==='token-1'));
 w.tenantI18n.setLanguage('en');d.querySelector('[data-ai-section-nav=dialogs]').click();await tick();assert.match(d.querySelector('#aiDraftList').textContent,/Customer question/);assert.match(d.querySelector('#aiDraftList').textContent,/Send to WhatsApp/);
 assert.ok(!calls.some(call=>call.body.expected_tenant_id!==undefined&&call.body.expected_tenant_id!==TENANT));
 w.close();console.log('PASS: draft detail, source context, inline edit/versioned save/send, no technical IDs, RU/EN.');
})().catch(error=>{console.error(error);process.exitCode=1;});
