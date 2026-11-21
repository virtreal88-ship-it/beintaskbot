const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.SUPPORT_CASES_JSDOM||'jsdom');
const read=p=>fs.readFileSync(p,'utf8'),tick=()=>new Promise(r=>setTimeout(r,0));
(async()=>{
 await verifyImmediateListState(false);await verifyImmediateListState(true);await verifyCompanyCompletion();
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
   if(body.action==='start'){route={...route,can_send:true,lifecycle:{id:'new-episode',state:'accepted',operator_id:33,revision:8,can_close:true,can_transfer:true}};}
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
 assert.equal(d.querySelector('#operatorSendReason').textContent,'','the accept action stays visible without an extra instruction above the composer');
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
 assert.equal(calls.find(c=>c.body?.action==='dismiss').body.individual_no_reply,true);
 route={...route,lifecycle:{state:'waiting',revision:5,can_accept:true,last_inbound_id:null}};await w.tenantOperator.openDialog(route);assert(!d.querySelector('[data-operator-lifecycle=accept]'),'no incoming message means no accept action');
 route={...route,can_send:false,lifecycle:{state:'closed',revision:7,operator_id:33,can_start:true}};await w.tenantOperator.openDialog(route);const start=d.querySelector('[data-operator-lifecycle=start]');assert(start,'closed dialog has a start action');assert.equal(start.textContent,'Dialoqa başla','start action is localized');assert.equal(d.querySelector('#operatorAcceptAction').nextElementSibling.classList.contains('operator-composer'),true);start.click();await tick();await tick();assert.equal(calls.find(c=>c.body?.action==='start').body.expected_revision,7);assert(!d.querySelector('[data-operator-lifecycle=start]'));assert(!d.querySelector('#sendChat').disabled);assert(!calls.some(c=>c.url.includes('/send')),'starting a dialog sends no customer message');
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


async function verifyCompanyCompletion(){
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://example.test/app?view=dialogs',runScripts:'outside-only'}),w=dom.window,d=w.document;
 const profile={member:{tenant_id:'tenant',telegram_id:33,role:'operator',active:true},capabilities:{modules:{dialogs:true}}};
 let route={provider:'connect',lead_id:41,channel_id:'a',conversation_id:'client',customer_kind:'contact',customer_name:'Synthetic client',can_send:true,lifecycle_enabled:true,
  lifecycle:{id:'episode',state:'accepted',operator_id:33,revision:1,can_close:true,company_completion:{status:'missing',can_choose:true}}};
 const waiting={...route,lead_id:42,conversation_id:'waiting',lifecycle:{state:'waiting',revision:1,can_dismiss:true,last_inbound_id:'other-inbound',bulk_company_gate:'no_reply_separate'}};
 const calls=[];let allowClose=false,prelinked=false;
 w.tenantProfileSnapshot=profile;w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});w.alert=()=>{};
 w.confirm=()=>{throw Error('company and waiting exclusions must not reach bulk confirmation');};
 w.fetch=async(url,opt={})=>{
   url=String(url);const body=opt.body?JSON.parse(opt.body):null;calls.push({url,body});
   if(url.includes('/api/platform/me'))return{ok:true,json:async()=>profile};
   if(url.includes('/api/platform/crm/native/companies')){
     const query=new URL(url,w.location.origin).searchParams;
     assert.equal(query.get('expected_tenant_id'),'tenant');assert.equal(query.get('expected_user_id'),'33');
     return{ok:true,json:async()=>({success:true,companies:[
      {id:'forbidden',name:'Not editable',actions:{update:false}},
      {id:'11111111-1111-4111-8111-111111111111',name:'Editable CRM company',actions:{update:true}}
     ]})};
   }
   let result;
   if(url.includes('/dialogs/history'))result={dialog:route,messages:[{id:'m',body:'Synthetic history',direction:'incoming'}],next_before:null};
   else if(url.includes('/dialogs?'))result={dialogs:[{...route,lifecycle:{...route.lifecycle,bulk_company_gate:prelinked?'prelinked_unverified':'choose'}},waiting],channels:[],agents:[],next_offset:null,completion_preview:true};
   else if(url.endsWith('/dialogs_lifecycle')){
     if(prelinked&&body.action==='close'&&!body.company_id)return {ok:false,status:422,json:async()=>({success:false,error:'Unverified identity'})};
     if(!allowClose||body.action!=='close')throw Error('unexpected mutation');
     route={...route,can_send:false,lifecycle:{id:'episode',state:'closed',operator_id:33,revision:2,can_close:false}};
     result={lifecycle:route.lifecycle};
   }else if(url.includes('/dialogs/read'))result={updated:true};else throw Error('Unexpected '+url);
   return{ok:true,status:200,json:async()=>({success:true,result})};
 };
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));w.tenantI18n.setLanguage('en');
 d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:profile}));
 await w.tenantOperator.refresh();await w.tenantOperator.openDialog(route);await tick();
 assert.equal(d.querySelector('#sendChat').disabled,false,'unlinked active conversation can still reply');
 d.querySelector('#operatorCompleteAll').click();await tick();await tick();
 assert.match(d.querySelector('#operatorBulkCompletionStatus').textContent,/Left open for company verification: 1/);
 assert.match(d.querySelector('#operatorBulkCompletionStatus').textContent,/individual No reply needed decision: 1/);
 assert(!calls.some(c=>c.body?.action),'bulk does not mutate either excluded route');
 prelinked=true;
 w.confirm=prompt=>{assert.match(prompt,/up to 1 accepted dialogues/);assert.match(prompt,/verify each link and dialogue address/);return true;};
 d.querySelector('#operatorCompleteAll').click();await tick();await tick();
 assert.deepEqual(calls.filter(c=>c.body?.action).map(c=>c.body.action),['close']);
 assert.match(d.querySelector('#operatorBulkCompletionStatus').textContent,/Left open for company verification: 1/);
 assert.match(d.querySelector('#chatRows').textContent,/Synthetic history/);
 prelinked=false;calls.length=0;
 d.querySelector('[data-operator-lifecycle=close]').click();
 const dialog=d.querySelector('#operatorLifecycleModal');
 dialog.querySelector('form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();
 const selection=dialog.querySelector('select');assert.deepEqual([...selection.options].map(o=>o.value),['','11111111-1111-4111-8111-111111111111']);
 selection.value='11111111-1111-4111-8111-111111111111';selection.dispatchEvent(new w.Event('change'));
 assert(!calls.some(c=>c.body?.action),'search and choosing emit no writes');
 allowClose=true;
 dialog.querySelector('[data-company-complete]').click();await tick();await tick();
 const mutations=calls.filter(c=>c.body?.action);
 assert.equal(mutations.length,1);assert.equal(mutations[0].body.action,'close');
 assert.equal(mutations[0].body.company_id,'11111111-1111-4111-8111-111111111111');
 assert.equal(mutations[0].body.expected_revision,1);assert.equal(mutations[0].body.channel_id,'a');assert.equal(mutations[0].body.conversation_id,'client');
 assert(!d.querySelector('#operatorLifecycleModal'));
 assert.match(d.querySelector('#chatRows').textContent,/Synthetic history/);
 assert(!calls.some(c=>c.url.includes('/send')||c.url.includes('/operator/dialog-company')));
 route={...route,can_send:true,lifecycle:{state:'accepted',revision:3,can_close:true,company_completion:{status:'missing',can_choose:false,can_skip:true}}};
 await w.tenantOperator.openDialog(route);await tick();calls.length=0;
 d.querySelector('[data-operator-lifecycle=close]').click();
 const noCompany=d.querySelector('#operatorLifecycleModal');
 assert(noCompany.querySelector('form').hidden,'no independent company permissions means no picker');
 assert.equal(noCompany.querySelector('[data-company-skip]').textContent,'Without a company');
 assert.equal(noCompany.querySelector('[data-company-skip]').hidden,false);
 assert(!calls.some(c=>c.body?.action),'the choice is never assumed');
 noCompany.querySelector('[data-company-skip]').click();noCompany.querySelector('[data-company-skip]').click();
 await tick();await tick();
 const skipped=calls.filter(c=>c.body?.action==='close');assert.equal(skipped.length,1);
 assert.equal(skipped[0].body.company_choice,'none');assert.equal('company_id' in skipped[0].body,false);
 assert.equal(skipped[0].body.expected_revision,3);
 assert(!calls.some(c=>c.url.includes('/crm/native/companies')||c.url.includes('/operator/dialog-company')));
 route={...route,can_send:true,lifecycle:{state:'accepted',revision:4,can_close:true,company_completion:{status:'ready',can_choose:false,can_skip:false}}};
 await w.tenantOperator.openDialog(route);await tick();calls.length=0;
 d.querySelector('[data-operator-lifecycle=close]').click();await tick();await tick();
 const existing=calls.filter(c=>c.body?.action==='close');assert.equal(existing.length,1);
 assert.equal('company_choice' in existing[0].body,false);assert.equal('company_id' in existing[0].body,false);
 assert(!d.querySelector('#operatorLifecycleModal'),'existing binding closes without a picker');
 route={...route,can_send:true,lifecycle:{state:'accepted',revision:5,can_close:true,company_completion:{status:'unavailable',required:false,can_choose:false,can_skip:false}}};
 await w.tenantOperator.openDialog(route);await tick();calls.length=0;
 assert(!d.querySelector('[data-company-completion]'),'optional unavailability must not imply that closure is locked');
 d.querySelector('[data-operator-lifecycle=close]').click();await tick();await tick();
 assert.equal(calls.filter(c=>c.body?.action==='close').length,1);
 assert(!calls.filter(c=>c.body?.action==='close')[0].body.company_choice);
 dom.window.close();
}
