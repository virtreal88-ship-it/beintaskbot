const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.SUPPORT_CASES_JSDOM||'jsdom');
const read=p=>fs.readFileSync(p,'utf8'),tick=()=>new Promise(r=>setTimeout(r,0));
(async()=>{
 await verifyImmediateListState(false);await verifyImmediateListState(true);await verifyCompanyCompletion();await verifyOwnerOverview();await verifySidebarTransfer();
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
   if(body.action==='accept'){route={...route,send_block_reason:'',can_send:true,lifecycle:{id:'episode',state:'accepted',operator_id:33,accepted_by_name:'Operator & <b>Example</b>',revision:2,can_accept:false,can_takeover:false,can_start:false,can_dismiss:false,can_submit_case:false,no_reply_needed:false,can_close:true,can_transfer:true,last_inbound_id:'latest-inbound',transfer_targets:[{employee_id:44,name:'Hüseyn'}]},missed_call_pending:false};}
   if(body.action==='start'){route={...route,archive_read_only:false,can_send:true,lifecycle:{id:'new-episode',state:'accepted',operator_id:33,revision:8,can_close:true,can_transfer:true}};}
   if(body.action==='close'){route={...route,can_send:false,lifecycle:{...route.lifecycle,state:'closed',revision:3,can_close:false,can_transfer:false,can_submit_case:true}};}
   if(body.action==='dismiss'){route={...route,lifecycle:{...route.lifecycle,revision:5,no_reply_needed:true,can_dismiss:false}};}
   result={lifecycle:route.lifecycle,controls:{lifecycle:route.lifecycle,can_send:route.can_send},...(body.action==='case'?caseResult:{})};
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
 assert(d.querySelector('[data-client-accepted-by]').hidden,'waiting routes do not display an inferred name');
 failed=true;d.querySelector('[data-operator-lifecycle=accept]').click();await tick();const first=calls.find(c=>c.body?.action==='accept').body;assert.equal(first.expected_revision,1);assert.equal(first.expected_tenant_id,'tenant');assert.equal(first.expected_user_id,33);
 failed=false;hold=true;d.querySelector('[data-operator-lifecycle=accept]').click();d.querySelector('[data-operator-lifecycle=accept]').click();await tick();assert.equal(calls.filter(c=>c.body?.action==='accept').length,2);assert.equal(calls.filter(c=>c.body?.action==='accept')[1].body.request_id,first.request_id,'retry retains exact receipt');hold=false;release();await tick();await tick();assert(!d.querySelector('[data-operator-lifecycle=accept]'));assert.equal(d.querySelector('[data-operator-lifecycle=close]').parentElement.nextElementSibling.id,'operatorAIReply');assert(!d.querySelector('#sendChat').disabled);
 const acceptedBy=d.querySelector('#operatorClientCard [data-client-accepted-by]');
 assert.equal(acceptedBy.querySelector('small').textContent,'Dialoqu qəbul edən');
 assert.equal(acceptedBy.querySelector('span').textContent,'Operator & <b>Example</b>');
 assert(!acceptedBy.querySelector('b'),'names are rendered as text');
 assert(!d.querySelector('#operatorConversationHeader').textContent.includes('Operator &'));
 const composerBeforeNameChange=d.querySelector('#chatText');
 const {accepted_by_name, ...previousLife}=route.lifecycle;
 route={...route,lifecycle:previousLife};
 await w.tenantOperator.openDialog(route);
 assert.equal(d.querySelector('#chatText'),composerBeforeNameChange,'name-only refresh keeps the editor intact');
 assert(d.querySelector('[data-client-accepted-by]').hidden,'clearing the name hides the same row');
 assert.equal(acceptedBy.querySelector('span').textContent,'','clearing the name removes old text');
 assert(d.querySelector('#operatorSidebarTransfer [data-operator-lifecycle=transfer]'));assert(!d.querySelector('#operatorMoreActions [data-operator-lifecycle=transfer]'));assert(!d.querySelector('#operatorLifecycleModal'));
 d.querySelector('[data-operator-lifecycle=close]').click();await tick();await tick();assert(d.querySelector('[data-operator-lifecycle=case]'));assert(!d.querySelector('[data-operator-lifecycle=accept]'));assert(!d.querySelector('[data-operator-lifecycle=close]'));assert(d.querySelector('#sendChat').disabled);assert.equal(calls.filter(c=>c.body?.action==='case').length,0,'close never creates a case');assert(!calls.some(c=>c.url.includes('support-tickets/complete')||c.url.includes('knowledge-candidates')));
 d.querySelector('[data-operator-lifecycle=case]').click();await tick();assert.equal(calls.filter(c=>c.body?.action==='case').length,1);
 route={...route,can_send:false,lifecycle:{state:'waiting',revision:4,can_accept:true,last_inbound_id:'latest-inbound',completed_episodes:[{id:'old-completed',accepted_at:'2026-01-01T00:00:00Z',closed_at:'2026-01-01T00:01:00Z',case_id:null},{id:'already-sent',accepted_at:'2025-01-01T00:00:00Z',closed_at:'2025-01-01T00:01:00Z',case_id:'saved'}]}};
 caseResult={case_state:'not_suitable',case_id:null,suitability:{suitable:false,reason:'Only a greeting; no reusable knowledge'}};await w.tenantOperator.openDialog(route);d.querySelector('[data-operator-lifecycle=case]').click();await tick();assert.equal(calls.filter(c=>c.body?.action==='case').length,1,'opening historical case selector never submits');
 let episodeModal=d.querySelector('#operatorLifecycleModal'),episodeSelect=episodeModal.querySelector('[data-episode-select]');assert(episodeModal.querySelector('[type=submit]').disabled);assert(episodeSelect.querySelector('[value="already-sent"]').disabled);episodeSelect.value='old-completed';episodeSelect.dispatchEvent(new w.Event('change'));episodeModal.querySelector('form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();assert.equal(calls.filter(c=>c.body?.action==='case').at(-1).body.episode_id,'old-completed');assert.equal(calls.filter(c=>c.body?.action==='case').at(-1).body.expected_revision,4);assert.match(d.querySelector('#operatorChatError').textContent,/Only a greeting/);
 denyCase=true;d.querySelector('[data-operator-lifecycle=case]').click();episodeModal=d.querySelector('#operatorLifecycleModal');episodeSelect=episodeModal.querySelector('select');episodeSelect.value='old-completed';episodeSelect.dispatchEvent(new w.Event('change'));episodeModal.querySelector('form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();assert(!d.querySelector('#chatRows'),'stale ownership blocks historical submission and clears chat');assert(!d.querySelector('#operatorLifecycleModal'));denyCase=false;
 d.querySelector('#operatorLifecycle').value='active';
 route={...route,can_send:false,lifecycle:{state:'waiting',revision:4,can_accept:true,last_inbound_id:'latest-inbound',can_dismiss:true}};
 await w.tenantOperator.openDialog(route);assert(!d.querySelector('[data-operator-lifecycle=dismiss]'),'waiting never shows rhetorical even if an older server advertises it');
 route={...route,can_send:true,lifecycle:{state:'accepted',revision:4,operator_id:33,can_close:true,last_inbound_id:'latest-inbound',can_dismiss:true}};
 await w.tenantOperator.openDialog(route);const rhetorical=d.querySelector('[data-operator-lifecycle=dismiss]');
 assert.equal(rhetorical.textContent,'Ritorik sual');assert.equal(rhetorical.previousElementSibling.dataset.operatorLifecycle,'close');
 rhetorical.click();await tick();await tick();assert.equal(calls.find(c=>c.body?.action==='dismiss').body.message_id,'latest-inbound');
 assert(d.querySelector('#chatRows'),'rhetorical mark retains the active conversation');assert(d.querySelector('[data-operator-lifecycle=dismiss]').disabled);assert.equal(d.querySelector('[data-operator-lifecycle=dismiss]').getAttribute('aria-pressed'),'true');
 assert(d.querySelector('[data-operator-lifecycle=close]'),'rhetorical mark keeps explicit completion');assert(!d.querySelector('#sendChat').disabled);
 assert.equal(calls.filter(c=>c.body?.action==='case').length,3);
 assert.equal(calls.find(c=>c.body?.action==='dismiss').body.individual_no_reply,true);
 route={...route,lifecycle:{state:'waiting',revision:5,can_accept:true,last_inbound_id:null}};await w.tenantOperator.openDialog(route);assert(d.querySelector('[data-operator-lifecycle=accept]'),'owner can explicitly accept a waiting dialogue without an incoming message');
 route={...route,archive_read_only:true,can_send:false,lifecycle:{state:'closed',revision:7,operator_id:22,can_start:true}};
 let archiveAcknowledged=0;w.tenantSharedReminders={markRows(){},mountCard(){},dialogOpened(){archiveAcknowledged++;}};
 const beforeArchive=calls.filter(c=>c.url.includes('/dialogs/read')).length;
 await w.tenantOperator.openDialog(route,false,'',{userOpened:true});
 assert(d.querySelector('#chatRows').textContent.includes('Private customer message'));
 assert(!d.querySelector('#operatorConversationNameInput'),'archive readers cannot edit the display name');
 assert(!d.querySelector('[data-operator-lifecycle=transfer]'));
 assert(d.querySelector('#sendChat').disabled);
 assert.equal(archiveAcknowledged,0,'opening read-only history must not acknowledge another operator’s reminder');
 assert.equal(calls.filter(c=>c.url.includes('/dialogs/read')).length,beforeArchive);
 const start=d.querySelector('[data-operator-lifecycle=start]');assert(start,'closed dialog has a start action');assert.equal(start.textContent,'Dialoqa başla','start action is localized');assert.equal(d.querySelector('#operatorAcceptAction').nextElementSibling.classList.contains('operator-composer'),true);start.click();await tick();await tick();assert.equal(calls.find(c=>c.body?.action==='start').body.expected_revision,7);assert(!d.querySelector('[data-operator-lifecycle=start]'));assert(!d.querySelector('#sendChat').disabled);assert(!calls.some(c=>c.url.includes('/send')),'starting a dialog sends no customer message');
 await w.tenantOperator.openDialog(route);denyHistory=true;await w.tenantOperator.openDialog(route);assert(!d.querySelector('#chatRows'),'access rejection clears rendered history');assert(!d.querySelector('#chatText'));assert(!d.querySelector('[data-operator-lifecycle]'));
 dom.window.close();console.log('dialog lifecycle UI passed');
})().catch(error=>{console.error(error);process.exitCode=1;});

async function verifyOwnerOverview(){
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://example.test/app?view=dialogs',runScripts:'outside-only'}),w=dom.window,d=w.document;
 const profile={member:{tenant_id:'tenant',telegram_id:33,role:'owner',active:true},capabilities:{modules:{dialogs:true,support:true}}};
 const observer={provider:'connect',lead_id:77,channel_id:'a',conversation_id:'other',customer_name:'Other',customer_kind:'contact',
  lifecycle_enabled:true,archive_read_only:true,owner_overview_read_only:true,can_send:false,can_use_copilot:false,
  lifecycle:{state:'accepted',revision:1,operator_id:22}};
 const calls=[];let acks=0;
 w.tenantProfileSnapshot=profile;w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});
 w.tenantSharedReminders={markRows(){},dialogOpened(){acks++;}};
 w.fetch=async(value,opt={})=>{
   const url=new URL(String(value),w.location.origin);calls.push({url,method:opt.method||'GET'});
   const success=result=>({ok:true,status:200,json:async()=>({success:true,result})});
   if(url.pathname.endsWith('/dialogs/preferences'))return success({lifecycle:'all'});
   if(url.pathname.endsWith('/dialogs')){
     const isOverview=url.searchParams.get('owner_overview')==='1';
     return success({dialogs:isOverview?[observer]:[],agents:[],channels:[{id:'connect:a',name:'A'}],next_offset:null});
   }
   if(url.pathname.endsWith('/dialogs/history')){
     assert.equal(url.searchParams.get('owner_overview'),'1');assert.equal(url.searchParams.get('expected_user_id'),'33');
     return success({dialog:observer,messages:[{id:'message',direction:'incoming',body:'Visible to owner only'}],next_before:null});
   }
   if(url.pathname.endsWith('/me'))return {ok:true,json:async()=>profile};
   throw Error('Unexpected request '+url);
 };
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));
 d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:profile}));
 await w.tenantOperator.refresh();
 const lifecycle=d.querySelector('#operatorLifecycle');
 assert.equal(d.querySelectorAll('#operatorAdvanced select').length,4);
 assert.deepEqual([...lifecycle.options].map(option=>option.value),['all','active','completed']);
 const channel=d.querySelector('#operatorChannel');channel.value='connect:a';channel.dispatchEvent(new w.Event('change'));await tick();
 const list=calls.filter(c=>c.url.pathname.endsWith('/dialogs')).at(-1).url.searchParams;
 assert.equal(list.get('lifecycle'),'all');assert(!list.has('lifecycle_state'));assert.equal(list.get('channel'),'connect:a');
 d.querySelector('[data-open-operator]').click();await tick();await tick();
 assert(d.querySelector('#chatRows').textContent.includes('Visible to owner only'));
 assert(d.querySelector('#sendChat').disabled);
 assert(!d.querySelector('[data-operator-lifecycle]')&&!d.querySelector('#operatorConversationNameInput'));
 assert.equal(acks,0);assert(!calls.some(c=>c.url.pathname.endsWith('/dialogs/read')));
 lifecycle.value='active';lifecycle.dispatchEvent(new w.Event('change'));await tick();
 assert(!d.querySelector('#chatRows'),'leaving All hides the observational chat');
 assert(!calls.filter(c=>c.url.pathname.endsWith('/dialogs')).at(-1).url.searchParams.has('owner_overview'));
 d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{...profile,member:{...profile.member,role:'operator'}}}));
 await tick();
 assert.equal(d.querySelectorAll('#operatorAdvanced select').length,4);
 assert(!calls.filter(c=>c.url.pathname.endsWith('/dialogs')).at(-1).url.searchParams.has('owner_overview'));
 dom.window.close();
}

async function verifyImmediateListState(grouped){
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://example.test/app?view=dialogs',runScripts:'outside-only'}),w=dom.window,d=w.document;
 const profile={member:{tenant_id:'tenant',telegram_id:33,role:'owner',active:true},capabilities:{modules:{dialogs:true}}};
 const base={provider:'connect',lead_id:41,channel_id:'a',channel_name:'A',conversation_id:'client',customer_kind:grouped?'group':'contact',customer_name:'Synthetic',can_send:false,lifecycle_enabled:true};
 let state={state:'waiting',revision:1,can_accept:true,last_inbound_id:'in-1'},listState={...state},historyReads=0,fail=false;
 const sibling={...base,channel_id:'b',channel_name:'B',lead_id:42,lifecycle:{state:'closed',revision:8}};
 w.tenantProfileSnapshot=profile;w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});w.alert=()=>{};
 w.fetch=async(url,opt={})=>{url=String(url);let result;
  if(url.endsWith('dialogs_lifecycle')){if(fail)throw Error('Synthetic failure');const action=JSON.parse(opt.body).action;state=action==='accept'?{state:'accepted',revision:2,operator_id:33,can_close:true}:{state:'closed',revision:3,operator_id:33};result={lifecycle:state};}
  else if(url.includes('/dialogs/history')){historyReads++;result={dialog:{...base,lifecycle:{...state}},messages:[],next_before:null};}
  else if(url.includes('/dialogs?'))result={dialogs:[{...base,lifecycle:{...listState},unread_count:7,...(grouped?{group_sources:[{...base,lifecycle:{...listState}},sibling]}:{})}],channels:[],agents:[],total_count:1};
  else if(url.includes('/api/platform/me'))return{ok:true,json:async()=>profile};
  else if(url.includes('/dialogs/read'))result={updated:true};else throw Error(url);
  return{ok:true,status:200,json:async()=>({success:true,result})};
 };
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));
 d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:profile}));await w.tenantOperator.refresh();await w.tenantOperator.openDialog({...base,lifecycle:state});
 const badge=()=>d.querySelector('.operator-row-indicators [data-lifecycle-state]');
 assert.equal(badge().dataset.lifecycleState,'waiting');fail=true;d.querySelector('[data-operator-lifecycle=accept]').click();await tick();await tick();assert.equal(badge().dataset.lifecycleState,'waiting','failed action must not show accepted');
 fail=false;const priorReads=historyReads;d.querySelector('[data-operator-lifecycle=accept]').click();await tick();await tick();assert.equal(badge().dataset.lifecycleState,'accepted','receipt updates badge before history or list refresh');assert.equal(d.querySelector('.operator-unread-count').textContent,'7');
 assert.equal(historyReads,priorReads,'accept does not reload history');await w.tenantOperator.refresh();assert.equal(badge().dataset.lifecycleState,'accepted','older list snapshot cannot restore New');
 d.querySelector('[data-operator-lifecycle=close]').click();await tick();await tick();assert.equal(badge().dataset.lifecycleState,'closed');assert.equal(d.querySelector('.operator-unread-count').textContent,'7');
 state={state:'waiting',revision:4,can_accept:true,last_inbound_id:'in-2'};listState={...state};await w.tenantOperator.refresh();assert.equal(badge().dataset.lifecycleState,'waiting','new inbound after completion restores New');assert.equal(d.querySelector('.operator-unread-count').textContent,'7');if(grouped){sibling.lifecycle={state:'waiting',revision:9};state={state:'accepted',revision:5};listState={...state};await w.tenantOperator.refresh();assert.equal(badge().dataset.lifecycleState,'waiting','accepting one source never accepts another waiting source');}dom.window.close();
}


async function verifyCompanyCompletion(){
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://example.test/app?view=dialogs',runScripts:'outside-only'}),w=dom.window,d=w.document;
 d.getElementById('view-dialogs').classList.add('active');
 const profile={member:{tenant_id:'tenant',telegram_id:33,role:'operator',active:true},capabilities:{modules:{dialogs:true}}};
 let route={provider:'connect',lead_id:41,channel_id:'a',conversation_id:'client',customer_kind:'contact',customer_name:'Synthetic client',can_send:true,lifecycle_enabled:true,
  lifecycle:{id:'episode',state:'accepted',operator_id:33,revision:1,can_close:true,company_completion:{status:'missing',can_choose:true}}};
 const waiting={...route,lead_id:42,conversation_id:'waiting',lifecycle:{state:'waiting',revision:1,can_dismiss:false,has_inbound:true,last_inbound_id:'other-inbound',bulk_company_gate:'no_reply_separate'}};
 const calls=[];let allowClose=false,prelinked=false,failClose=null;
 w.tenantProfileSnapshot=profile;w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});w.alert=()=>{};
 w.confirm=()=>{throw Error('company and waiting exclusions must not reach bulk confirmation');};
 w.fetch=async(url,opt={})=>{
   url=String(url);const body=opt.body?JSON.parse(opt.body):null;calls.push({url,body});
   if(url.includes('/api/platform/me'))return{ok:true,json:async()=>profile};
   let result;
   if(url.includes('/dialogs/history'))result={dialog:route,messages:[{id:'m',body:'Synthetic history',direction:'incoming'}],next_before:null};
   else if(url.includes('/dialogs?'))result={dialogs:[{...route,lifecycle:{...route.lifecycle,bulk_company_gate:prelinked?'prelinked_unverified':'choose'}},waiting],channels:[],agents:[],next_offset:null,completion_preview:true};
   else if(url.endsWith('/dialogs_lifecycle')){
     if(prelinked&&body.action==='close'&&!body.company_id)return {ok:false,status:422,json:async()=>({success:false,error:'Unverified identity'})};
     if(!allowClose||body.action!=='close')throw Error('unexpected mutation');
     if(failClose)return {ok:false,status:422,json:async()=>({success:false,error:failClose})};
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
 assert.match(d.querySelector('#operatorBulkCompletionStatus').textContent,/Not yet accepted: 1/);
 assert(!calls.some(c=>c.body?.action),'bulk does not mutate either excluded route');
 prelinked=true;
 w.confirm=prompt=>{assert.match(prompt,/up to 1 accepted dialogues/);assert.match(prompt,/verify each link and dialogue address/);return true;};
 d.querySelector('#operatorCompleteAll').click();await tick();await tick();
 assert.deepEqual(calls.filter(c=>c.body?.action).map(c=>c.body.action),['close']);
 assert.match(d.querySelector('#operatorBulkCompletionStatus').textContent,/Left open for company verification: 1/);
 assert.match(d.querySelector('#chatRows').textContent,/Synthetic history/);
 prelinked=false;calls.length=0;
 const historyCount=()=>calls.filter(c=>c.url.includes('/dialogs/history')).length;
 const closeButton=()=>d.querySelector('[data-operator-lifecycle=close]');
 const initialReads=historyCount();
 allowClose=true;failClose='Synthetic server rejection';
 closeButton().click();closeButton().click();await tick();
 assert.equal(calls.filter(c=>c.body?.action==='close').length,1);
 assert.equal(historyCount(),initialReads);
 assert.equal(d.querySelector('#operatorChatError').textContent,failClose);
 assert.equal(d.querySelector('#sendChat').disabled,false,'failed close keeps the chat active');
 failClose=null;closeButton().click();await tick();
 const [first,retry]=calls.filter(c=>c.body?.action==='close').map(c=>c.body);
 assert.deepEqual(first,retry,'close reuses the same receipt after an error');
 assert.equal(retry.company_choice,'unchanged');
 assert.equal(retry.expected_revision,1);assert.equal(retry.channel_id,'a');
 assert(!('company_id' in retry));
 assert.equal(historyCount(),initialReads);
 assert.equal(d.querySelector('#sendChat').disabled,true);
 assert(!closeButton());assert(!d.querySelector('#operatorLifecycleModal'));
 assert.match(d.querySelector('#chatRows').textContent,/Synthetic history/);
 assert(!calls.some(c=>c.url.includes('/send')||c.url.includes('/crm/native/companies')));
 dom.window.close();
}


async function verifySidebarTransfer(){
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://example.test/app?view=dialogs',runScripts:'outside-only'}),w=dom.window,d=w.document;
 const profile={member:{tenant_id:'tenant',telegram_id:33,role:'operator',active:true},capabilities:{modules:{dialogs:true}}};
 const own={provider:'connect',lead_id:41,channel_id:'exact-source',conversation_id:'client',can_send:true,lifecycle_enabled:true,
  lifecycle:{state:'accepted',operator_id:33,revision:3,can_transfer:true}};
 const passive={...own,channel_id:'passive',lead_id:42,archive_read_only:true};
 let mode='empty',failed=false,release;const requests=[];
 w.tenantProfileSnapshot=profile;w.matchMedia=()=>({matches:true,addListener(){},removeListener(){}});w.alert=()=>{};
 w.confirm=()=>{throw Error('transfer must not ask for a second confirmation');};
 w.fetch=async(url,opt={})=>{
   const path=new URL(url,w.location.origin),body=opt.body?JSON.parse(opt.body):null;
   const ok=result=>({ok:true,status:200,json:async()=>({success:true,result})});
   if(path.pathname.endsWith('/dialogs/history'))return ok({dialog:path.searchParams.get('channel_id')==='passive'?passive:own,messages:[]});
   if(path.pathname.endsWith('/dialogs/transfer-candidates')){
     assert.equal(path.searchParams.get('channel_id'),'exact-source');assert.equal(path.searchParams.get('expected_revision'),'3');
     assert.equal(path.searchParams.get('expected_user_id'),'33');
     if(mode==='delay')await new Promise(r=>release=r);
     return ok({targets:mode==='empty'?[]:[{employee_id:33,name:'Self'},{employee_id:44,name:'Online operator'}]});
   }
   if(path.pathname.endsWith('/dialogs_lifecycle')){
     requests.push(body);
     if(failed)return{ok:false,status:403,json:async()=>({success:false})};
     return ok({lifecycle:{...own.lifecycle,revision:4,operator_id:44}});
   }
   if(path.pathname.endsWith('/dialogs/company-panel/preferences'))return ok({open:body?.open||false});
   if(path.pathname.endsWith('/dialogs'))return ok({dialogs:[],channels:[],agents:[]});
   return ok({});
 };
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));
 w.tenantI18n.setLanguage('en');
 d.querySelector('#view-dialogs').classList.add('active');
 d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:profile}));await w.tenantOperator.refresh();
 await w.tenantOperator.openDialog(own);
 d.querySelector('#operatorCompanyPanelOpen').click();
 assert(d.querySelector('#operatorCompanyPanel').open);
 const mobilePanel=d.querySelector('#operatorCompanyPanel'),workspace=d.querySelector('#operatorWorkspace');
 assert.equal(mobilePanel.parentElement.id,'operatorDialogs','sheet is not clipped by the animated chat');
 assert(!mobilePanel.inert&&workspace.inert,'the panel stays interactive while the workspace is inert');
 assert.equal(d.activeElement.id,'operatorCompanyPanelClose','mobile close receives focus when opened');
 d.querySelector('#operatorCompanyPanelClose').click();
 assert(!mobilePanel.open&&!workspace.inert,'closing the sheet re-enables the conversation');
 assert.equal(d.activeElement.id,'operatorCompanyPanelOpen','focus returns to the original trigger');
 d.querySelector('#operatorCompanyPanelOpen').click();
 const menu=d.querySelector('#operatorSidebarTransfer details');menu.open=true;
 await tick();await tick();
 assert.match(menu.textContent,/No eligible operators online/);
 const refresh=()=>{menu.open=false;return tick().then(()=>{menu.open=true;return tick();}).then(tick);};
 mode='online';await refresh();
 assert.deepEqual([...menu.querySelectorAll('[data-transfer-targets] button')].map(b=>b.textContent),['Online operator']);
 assert(!d.querySelector('#operatorLifecycleModal'));
 failed=true;menu.querySelector('[data-transfer-targets] button').click();await tick();
 assert.match(menu.querySelector('[role=status]').textContent,/status changed/);
 assert(!menu.querySelector('[data-transfer-retry]').hidden);
 assert(d.querySelector('#chatRows'),'rejected recipient does not close sender’s chat');
 mode='delay';menu.querySelector('[data-transfer-retry]').click();await tick();
 await w.tenantOperator.openDialog(passive);
 mode='online';release();await tick();
 assert(!d.querySelector('#operatorSidebarTransfer'),'archive route cannot use a late candidate response');
 await w.tenantOperator.openDialog(own);await tick();await tick();
 assert(!d.querySelector('#operatorCompanyPanel').open,'mobile still opens with the sidebar closed on another chat');
 d.querySelector('#operatorCompanyPanelOpen').click();
 const next=d.querySelector('#operatorSidebarTransfer details');next.open=true;await tick();await tick();
 failed=false;const target=next.querySelector('[data-transfer-targets] button');target.click();target.click();await tick();await tick();
 assert.equal(requests.length,2,'one POST per confirmed choice, even with rapid repeated click');
 assert.equal(requests[1].online_only,true);assert.equal(requests[1].target_employee_id,44);assert.equal(requests[1].expected_revision,3);
 assert.equal(requests[1].channel_id,'exact-source');assert(!d.querySelector('#operatorLifecycleModal'));
 assert(!d.querySelector('#chatRows'),'successful transfer leaves the transferred chat');
 dom.window.close();
}
