const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.SUPPORT_CASES_JSDOM||'jsdom');
const read=p=>fs.readFileSync(p,'utf8'),tick=()=>new Promise(r=>setTimeout(r,0));
(async()=>{
 await verifyImmediateListState(false);await verifyImmediateListState(true);
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://example.test/app?view=dialogs',runScripts:'outside-only'}),w=dom.window,d=w.document;
 const profile={member:{tenant_id:'tenant',telegram_id:33,role:'owner',active:true},capabilities:{modules:{dialogs:true,support:true}}};
 let route={provider:'connect',lead_id:41,channel_id:'channel',conversation_id:'client',agent_id:'agent',agent_name:'Agent',state:'human',can_send:false,send_block_reason:'dialog_accept_required',controls_allowed:true,available_actions:['resume'],lifecycle_enabled:true,lifecycle:{id:'episode',state:'waiting',revision:1,can_accept:true,last_inbound_id:'latest-inbound'}};
 let failed=false,hold=false,release,denyHistory=false,denyCase=false;let caseResult={case_state:'completed',case_id:'draft'};const calls=[];
 w.tenantProfileSnapshot=profile;w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});w.alert=()=>{};
 w.fetch=async(url,options={})=>{url=String(url);const body=options.body?JSON.parse(options.body):null;calls.push({url,body});let result;
  if(url.endsWith('dialogs_lifecycle')){
   if(body.action==='case'&&denyCase)return{ok:false,status:404,json:async()=>({success:false,error:'Ownership changed'})};
   if(hold)await new Promise(r=>release=r);
   if(failed)throw Error('Network interrupted');
   if(body.action==='accept'){route={...route,can_send:true,lifecycle:{id:'episode',state:'accepted',operator_id:33,revision:2,can_close:true,can_transfer:true,transfer_targets:[{employee_id:44,name:'Hüseyn'}]}};}
   if(body.action==='close'){route={...route,can_send:false,lifecycle:{...route.lifecycle,state:'closed',revision:3,can_close:false,can_transfer:false,can_submit_case:true}};}
   result={lifecycle:route.lifecycle,...(body.action==='case'?caseResult:{})};
  }else if(url.includes('/dialogs/history')){if(denyHistory)return{ok:false,status:404,json:async()=>({success:false,error:'Unavailable'})};result={dialog:{...route},messages:[{id:'message',body:'Private customer message',direction:'incoming'}],next_before:null};}
  else if(url.includes('/dialogs?'))result={dialogs:[],channels:[],agents:[],attention_count:0,next_offset:null};
  else if(url.includes('/api/platform/me'))return{ok:true,json:async()=>profile};
  else if(url.includes('/dialogs/read'))result={updated:true};
  else throw Error('Unexpected request '+url);
  return{ok:true,status:200,json:async()=>({success:true,result})};
 };
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));await tick();await tick();
 await w.tenantOperator.openDialog(route);await tick();
 assert.equal(d.querySelector('#operatorSendReason').textContent,w.tenantI18n.t('dialog_accept_required'));assert.notEqual(d.querySelector('#operatorSendReason').textContent,'dialog_accept_required');
 assert(d.querySelector('#operatorAcceptAction [data-operator-lifecycle=accept]'));assert(d.querySelector('#operatorAcceptAction').nextElementSibling.classList.contains('operator-composer'),'accept action is directly above composer, outside scrolling history');assert(!d.querySelector('.operator-chat-scrollarea #operatorAcceptAction'));assert(!d.querySelector('[data-operator-complete]'));assert(!d.querySelector('[data-operator-create-ticket]'));assert(!d.querySelector('[data-operator-action=resume]'));assert(d.querySelector('#sendChat').disabled);assert(d.querySelector('#operatorTraining').hidden);
 failed=true;d.querySelector('[data-operator-lifecycle=accept]').click();await tick();const first=calls.find(c=>c.body?.action==='accept').body;assert.equal(first.expected_revision,1);assert.equal(first.expected_tenant_id,'tenant');assert.equal(first.expected_user_id,33);
 failed=false;hold=true;d.querySelector('[data-operator-lifecycle=accept]').click();d.querySelector('[data-operator-lifecycle=accept]').click();await tick();assert.equal(calls.filter(c=>c.body?.action==='accept').length,2);assert.equal(calls.filter(c=>c.body?.action==='accept')[1].body.request_id,first.request_id,'retry retains exact receipt');hold=false;release();await tick();await tick();assert(!d.querySelector('[data-operator-lifecycle=accept]'));assert.equal(d.querySelector('[data-operator-lifecycle=close]').nextElementSibling.id,'operatorAIReply');assert(!d.querySelector('#sendChat').disabled);
 d.querySelector('[data-operator-lifecycle=transfer]').click();assert.equal(d.querySelector('#operatorLifecycleModal select option:last-child').textContent,'Hüseyn');d.querySelector('#operatorLifecycleModal [data-cancel]').click();assert(!d.querySelector('#operatorLifecycleModal'));assert.equal(calls.filter(c=>c.body?.action==='transfer').length,0);
 d.querySelector('[data-operator-lifecycle=close]').click();await tick();await tick();assert(d.querySelector('[data-operator-lifecycle=case]'));assert(!d.querySelector('[data-operator-lifecycle=accept]'));assert(!d.querySelector('[data-operator-lifecycle=close]'));assert(d.querySelector('#sendChat').disabled);assert.equal(calls.filter(c=>c.body?.action==='case').length,0,'close never creates a case');assert(!calls.some(c=>c.url.includes('support-tickets/complete')||c.url.includes('knowledge-candidates')));
 d.querySelector('[data-operator-lifecycle=case]').click();await tick();assert.equal(calls.filter(c=>c.body?.action==='case').length,1);
 route={...route,can_send:false,lifecycle:{state:'waiting',revision:4,can_accept:true,last_inbound_id:'latest-inbound',completed_episodes:[{id:'old-completed',accepted_at:'2026-01-01T00:00:00Z',closed_at:'2026-01-01T00:01:00Z',case_id:null},{id:'already-sent',accepted_at:'2025-01-01T00:00:00Z',closed_at:'2025-01-01T00:01:00Z',case_id:'saved'}]}};
 caseResult={case_state:'not_suitable',case_id:null,suitability:{suitable:false,reason:'Only a greeting; no reusable knowledge'}};await w.tenantOperator.openDialog(route);d.querySelector('[data-operator-lifecycle=case]').click();await tick();assert.equal(calls.filter(c=>c.body?.action==='case').length,1,'opening historical case selector never submits');
 let episodeModal=d.querySelector('#operatorLifecycleModal'),episodeSelect=episodeModal.querySelector('[data-episode-select]');assert(episodeModal.querySelector('[type=submit]').disabled);assert(episodeSelect.querySelector('[value="already-sent"]').disabled);episodeSelect.value='old-completed';episodeSelect.dispatchEvent(new w.Event('change'));episodeModal.querySelector('form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();assert.equal(calls.filter(c=>c.body?.action==='case').at(-1).body.episode_id,'old-completed');assert.equal(calls.filter(c=>c.body?.action==='case').at(-1).body.expected_revision,4);assert.match(d.querySelector('#operatorChatError').textContent,/Only a greeting/);
 denyCase=true;d.querySelector('[data-operator-lifecycle=case]').click();episodeModal=d.querySelector('#operatorLifecycleModal');episodeSelect=episodeModal.querySelector('select');episodeSelect.value='old-completed';episodeSelect.dispatchEvent(new w.Event('change'));episodeModal.querySelector('form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();assert(!d.querySelector('#chatRows'),'stale ownership blocks historical submission and clears chat');assert(!d.querySelector('#operatorLifecycleModal'));denyCase=false;
 d.querySelector('#operatorLifecycle').value='active';
 route={...route,can_send:false,lifecycle:{state:'waiting',revision:4,can_accept:true,last_inbound_id:'latest-inbound',can_dismiss:true,last_inbound_id:'latest-inbound'}};await w.tenantOperator.openDialog(route);d.querySelector('[data-operator-lifecycle=dismiss]').click();await tick();await tick();assert.equal(calls.find(c=>c.body?.action==='dismiss').body.message_id,'latest-inbound');assert(!d.querySelector('#chatRows'),'dismiss removes the active conversation without a case');assert.equal(calls.filter(c=>c.body?.action==='case').length,3);
 route={...route,lifecycle:{state:'waiting',revision:5,can_accept:true,last_inbound_id:null}};await w.tenantOperator.openDialog(route);assert(!d.querySelector('[data-operator-lifecycle=accept]'),'no incoming message means no accept action');
 await w.tenantOperator.openDialog(route);denyHistory=true;await w.tenantOperator.openDialog(route);assert(!d.querySelector('#chatRows'),'access rejection clears rendered history');assert(!d.querySelector('#chatText'));assert(!d.querySelector('[data-operator-lifecycle]'));
 dom.window.close();console.log('dialog lifecycle UI passed');
})().catch(error=>{console.error(error);process.exitCode=1;});

async function verifyImmediateListState(grouped){
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://example.test/app?view=dialogs',runScripts:'outside-only'}),w=dom.window,d=w.document;
 const profile={member:{tenant_id:'tenant',telegram_id:33,role:'owner',active:true},capabilities:{modules:{dialogs:true}}};
 const base={provider:'connect',lead_id:41,channel_id:'a',channel_name:'A',conversation_id:'client',customer_kind:grouped?'group':'contact',customer_name:'Synthetic',can_send:false,lifecycle_enabled:true};
 let state={state:'waiting',revision:1,can_accept:true,last_inbound_id:'in-1'},listState={...state},holdHistory=false,releaseHistory,fail=false;
 const sibling={...base,channel_id:'b',channel_name:'B',lead_id:42,lifecycle:{state:'closed',revision:8}};
 w.tenantProfileSnapshot=profile;w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});w.alert=()=>{};
 w.fetch=async(url,opt={})=>{url=String(url);let result;
  if(url.endsWith('dialogs_lifecycle')){if(fail)throw Error('Synthetic failure');const action=JSON.parse(opt.body).action;state=action==='accept'?{state:'accepted',revision:2,can_close:true}:{state:'closed',revision:3};result={lifecycle:state};}
  else if(url.includes('/dialogs/history')){if(holdHistory)await new Promise(r=>releaseHistory=r);result={dialog:{...base,lifecycle:{...state}},messages:[],next_before:null};}
  else if(url.includes('/dialogs?'))result={dialogs:[{...base,lifecycle:{...listState},unread_count:7,...(grouped?{group_sources:[{...base,lifecycle:{...listState}},sibling]}:{})}],channels:[],agents:[],total_count:1};
  else if(url.includes('/api/platform/me'))return{ok:true,json:async()=>profile};
  else if(url.includes('/dialogs/read'))result={updated:true};else throw Error(url);
  return{ok:true,status:200,json:async()=>({success:true,result})};
 };
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));
 d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:profile}));await w.tenantOperator.refresh();await w.tenantOperator.openDialog({...base,lifecycle:state});
 const badge=()=>d.querySelector('.operator-row-indicators [data-lifecycle-state]');
 assert.equal(badge().dataset.lifecycleState,'waiting');fail=true;d.querySelector('[data-operator-lifecycle=accept]').click();await tick();await tick();assert.equal(badge().dataset.lifecycleState,'waiting','failed action must not show accepted');
 fail=false;holdHistory=true;d.querySelector('[data-operator-lifecycle=accept]').click();await tick();await tick();assert.equal(badge().dataset.lifecycleState,'accepted','receipt updates badge before history or list refresh');assert.equal(d.querySelector('.operator-unread-count').textContent,'7');
 holdHistory=false;releaseHistory();await tick();await tick();await w.tenantOperator.refresh();assert.equal(badge().dataset.lifecycleState,'accepted','older list snapshot cannot restore New');
 d.querySelector('[data-operator-lifecycle=close]').click();await tick();await tick();assert.equal(badge().dataset.lifecycleState,'closed');assert.equal(d.querySelector('.operator-unread-count').textContent,'7');
 state={state:'waiting',revision:4,can_accept:true,last_inbound_id:'in-2'};listState={...state};await w.tenantOperator.refresh();assert.equal(badge().dataset.lifecycleState,'waiting','new inbound after completion restores New');assert.equal(d.querySelector('.operator-unread-count').textContent,'7');if(grouped){sibling.lifecycle={state:'waiting',revision:9};state={state:'accepted',revision:5};listState={...state};await w.tenantOperator.refresh();assert.equal(badge().dataset.lifecycleState,'waiting','accepting one source never accepts another waiting source');}dom.window.close();
}
