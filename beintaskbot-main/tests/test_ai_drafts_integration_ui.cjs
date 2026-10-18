const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const root=path.join(__dirname,'..'),read=f=>fs.readFileSync(path.join(root,f),'utf8');
const tick=async()=>{for(let i=0;i<20;i++)await new Promise(setImmediate)};
(async()=>{
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app?site_lang=ru',runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[];
 w.confirm=()=>true;w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});w.HTMLElement.prototype.scrollIntoView=()=>{};w.requestAnimationFrame=fn=>w.setTimeout(fn,0);
 const tenant='00000000-0000-0000-0000-000000000101',agent='00000000-0000-0000-0000-000000000301',id='00000000-0000-0000-0000-000000000401',channel='00000000-0000-0000-0000-000000000201';
 let detail={id,agent_id:agent,version:4,context_token:'t1',state:'pending',provider:'connect',edit_allowed:true,send_allowed:true,has_new_messages:false,customer_name:'Али',channel_name:'WhatsApp main',agent_name:'Nizam AI',draft:'Original',reason:{code:'unknown'},source_messages:[{id:'source',text:'Вопрос',media_available:true,message_type:'audio'}],dialog_route:{provider:'connect',lead_id:41,channel_id:channel,conversation_id:'verified@g.us',source_message_id:'source'}};
 const response=(result,status=200,error='')=>({ok:status===200,status,json:async()=>status===200?{success:true,result}:{success:false,error}});
 w.fetch=async(url,init={})=>{const u=new URL(url,'https://crm.test'),method=init.method||'GET',body=init.body?JSON.parse(init.body):{};calls.push({url:u.pathname+u.search,method,body});
  if(u.pathname==='/api/platform/me')return {ok:true,json:async()=>({member:{tenant_id:tenant,telegram_id:11,role:'owner',active:true},capabilities:{modules:{ai_agents:true}}})};
  if(u.pathname.endsWith('/agents/settings'))return response({enabled:true,operating_mode:'active'});
  if(u.pathname.endsWith('/agents/options'))return response({pipelines:[],stages:[],channels:[],models:[]});
  if(u.pathname.endsWith('/agents'))return response([{id:agent,name:'Nizam AI',enabled:true,mode:'manual',version:1,scopes:[]}]);
  if(u.pathname.endsWith('/agents/drafts'))return response([{...detail}]);
  if(u.pathname.endsWith('/drafts/'+id))return response({...detail});
  if(u.pathname.endsWith('/edit')){if(body.expected_version!==detail.version)return response(null,409,'draft_version_conflict');detail={...detail,draft:body.draft,edited_by:11,edited_at:'2026-10-18',version:detail.version+1};return response({...detail});}
  if(u.pathname.endsWith('/send')){assert.equal(body.expected_version,detail.version);assert.equal(body.expected_context_token,detail.context_token);assert.equal(body.confirm_new_messages,true);detail={...detail,state:'approved',edit_allowed:false,send_allowed:false};return response({state:'accepted'});}
  if(u.pathname.endsWith('/dialogs/history')){assert.equal(u.searchParams.has('anchor'),false,'review uses LATEST context');assert.equal(u.searchParams.get('channel_id'),channel);return response({messages:[{body:'Новое сообщение'}]});}
  return response([]);
 };
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval([...d.querySelectorAll('script:not([src])')][0].textContent);w.eval(read('docs/tenant-ai-state.js'));w.eval(read('docs/tenant-ai-agents.js'));await tick();
 d.querySelector('[data-edit-agent]').click();await tick();d.querySelector('[data-ai-section-nav=dialogs]').click();await tick();
 assert.equal(calls.filter(c=>c.url.endsWith('/drafts/'+id)).length,0,'enriched list avoids detail N+1');
 d.querySelector('[data-draft-edit]').click();await tick();let input=d.querySelector('[data-draft-input]');input.value='My unsaved answer';input.dispatchEvent(new w.Event('input'));
 assert.equal(d.querySelector('[data-draft-send]').disabled,true);
 detail={...detail,version:5,draft:'Other employee'};d.querySelector('[data-draft-refresh]').click();await tick();d.querySelector('[data-draft-save]').click();await tick();
 assert.equal(calls.filter(c=>c.url.endsWith('/edit')).at(-1).body.expected_version,4,'refresh cannot adopt new edit version');
 assert.equal(d.querySelector('[data-draft-input]').value,'My unsaved answer');assert.match(d.querySelector('#aiAgentsNotice').textContent,/изменился/);
 const unloading=new w.Event('beforeunload',{cancelable:true});w.dispatchEvent(unloading);assert.equal(unloading.defaultPrevented,true);
 d.querySelector('[data-draft-cancel]').click();d.querySelector('[data-draft-edit]').click();input=d.querySelector('[data-draft-input]');input.value='Approved edit';input.dispatchEvent(new w.Event('input'));d.querySelector('[data-draft-save]').click();await tick();
 assert.equal(detail.version,6);assert.match(d.querySelector('#aiDraftList').textContent,/Изменено сотрудником/);
 detail={...detail,has_new_messages:true,context_token:'t2'};d.querySelector('[data-draft-refresh]').click();await tick();assert.equal(d.querySelector('[data-draft-send]').disabled,true);
 d.querySelector('[data-draft-live-history]').click();await tick();assert.match(d.querySelector('#aiDraftList').textContent,/Новое сообщение/);
 const ack=d.querySelector('[data-draft-ack]');ack.checked=true;ack.dispatchEvent(new w.Event('change'));assert.equal(d.querySelector('[data-draft-send]').disabled,false);
 d.querySelector('[data-draft-send]').click();await tick();assert.equal(calls.filter(c=>c.url.endsWith('/send')).length,1);assert.equal(d.querySelector('[data-draft-edit]').disabled,true);assert.equal(d.querySelector('[data-draft-send]').disabled,true);
 w.tenantI18n.setLanguage('az');await tick();assert.match(d.querySelector('#aiDraftList').textContent,/Müştərinin sualı/);
 w.tenantI18n.setLanguage('en');await tick();assert.match(d.querySelector('#aiDraftList').textContent,/Customer question/);
 d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{member:{tenant_id:'other',telegram_id:22},capabilities:{modules:{ai_agents:false}}}}));await tick();assert.equal(d.querySelector('#aiDraftList').textContent,'');
 w.close();console.log('PASS: frozen edit version, 409 preservation, stale-context confirmation, no unsaved send, no N+1, tenant reset, RU/AZ/EN.');
})().catch(e=>{console.error(e);process.exitCode=1});
