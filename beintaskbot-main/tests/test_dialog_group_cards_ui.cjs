const fs=require('node:fs'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom');
const tick=()=>new Promise(r=>setTimeout(r,10));
(async()=>{
const dom=new JSDOM(fs.readFileSync('docs/tenant-app.html','utf8'),{url:'https://example.test/app?view=dialogs',runScripts:'outside-only'}),w=dom.window,d=w.document;
const profile={member:{tenant_id:'tenant',telegram_id:33,role:'owner',active:true},capabilities:{modules:{dialogs:true,support:true}}};
const source=(channel,lead)=>({provider:'connect',lead_id:lead,channel_id:channel,channel_name:channel,channel_active:true,conversation_id:'120363432608782851@g.us',customer_kind:'group',customer_name:'Synthetic group',state:'human',can_send:false,lifecycle_enabled:true,lifecycle:{state:'waiting',revision:0,can_accept:true}});
const a=source('Source A',41),b=source('Source B',42);let visible=[a,b], single=false;const calls=[];
w.tenantProfileSnapshot=profile;w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});w.alert=()=>{};
w.fetch=async(url,options={})=>{url=String(url);calls.push({url,method:options.method||'GET'});let result;
if(url.includes('/dialogs/history')){const q=new URL(url,'https://example.test').searchParams;result={dialog:q.get('channel_id')===a.channel_id?a:b,messages:[],next_before:null};}
else if(url.includes('/dialogs?'))result={dialogs:[{...visible[0],unread_count:2,...(single?{}:{group_sources:visible})}],channels:[],agents:[],attention_count:0,total_count:1,next_offset:url.includes('offset=50')?null:50};
else if(url.includes('/api/platform/me'))return{ok:true,json:async()=>profile};
else if(url.includes('/dialogs/read'))result={updated:true};else throw Error(url);
return{ok:true,status:200,json:async()=>({success:true,result})};};
w.eval(fs.readFileSync('docs/tenant-operator-i18n.js','utf8'));w.eval(fs.readFileSync('docs/tenant-operator-dialogs.js','utf8'));
d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:profile}));d.dispatchEvent(new w.CustomEvent('tenant-view',{detail:{view:'dialogs'}}));
for(let i=0;i<50&&!d.querySelector('[data-open-operator]');i++)await tick();
d.querySelector('#operatorMore').click();await tick();await tick();assert.equal(d.querySelectorAll('.operator-row').length,1);assert.equal(d.querySelectorAll('[data-group-source]').length,0);d.querySelector('[data-open-operator]').click();await tick();await tick();assert(d.querySelector('.operator-composer #operatorComposerSourceSelect'));assert.equal(d.querySelectorAll('#operatorComposerSourceSelect option').length,2);
for(const [index,route] of [a,b].entries()){const picker=d.querySelector('#operatorComposerSourceSelect');picker.value=String(index);picker.dispatchEvent(new w.Event('change'));await tick();await tick();const url=new URL(calls.filter(c=>c.url.includes('/dialogs/history')).at(-1).url,'https://example.test');assert.equal(url.searchParams.get('channel_id'),route.channel_id);assert.equal(url.searchParams.get('lead_id'),String(route.lead_id));}
// Switching sources uses the existing route-scoped draft storage, never copies a draft to another source.
const switchSource=async index=>{const picker=d.querySelector('#operatorComposerSourceSelect');picker.value=String(index);picker.dispatchEvent(new w.Event('change'));await tick();await tick();};
await switchSource(0);d.querySelector('#chatText').value='Draft A';d.querySelector('#chatText').dispatchEvent(new w.Event('input'));
await switchSource(1);assert.equal(d.querySelector('#chatText').value,'');
await switchSource(0);assert.equal(d.querySelector('#chatText').value,'Draft A');
await switchSource(1);
const before=calls.filter(c=>c.url.includes('/dialogs/history')).length;d.dispatchEvent(new w.CustomEvent('tenant-view',{detail:{view:'dialogs'}}));await tick();await tick();assert.equal(d.querySelectorAll('.operator-row').length,1);assert(calls.filter(c=>c.url.includes('/dialogs/history')).slice(before).every(c=>new URL(c.url,'https://example.test').searchParams.get('channel_id')===b.channel_id));
assert.equal(d.querySelectorAll('.operator-row [data-lifecycle-state="waiting"]').length,1);
assert.equal(d.querySelectorAll('[data-group-source-status]').length,0);
assert.equal(d.querySelector('.operator-unread-count').textContent,'2');
// Disabled historical sources stay in the server response but not the reply picker or card status.
const inactive={...source('Disabled 2135103',43),channel_active:false};
visible=[inactive,{...a,lifecycle:{...a.lifecycle,state:'accepted'}},b];
await w.tenantOperator.refresh();await tick();
assert.equal(d.querySelectorAll('.operator-row .operator-lifecycle-badge').length,1);
assert.equal(d.querySelector('.operator-row .operator-lifecycle-badge').dataset.lifecycleState,'waiting');
assert(!d.querySelector('.operator-row-sub').textContent.includes('2135103'));
assert.deepEqual([...d.querySelectorAll('#operatorComposerSourceSelect option')].map(o=>o.textContent),['Source A','Source B']);
d.querySelector('[data-open-operator]').click();await tick();await tick();
assert.notEqual(new URL(calls.filter(c=>c.url.includes('/dialogs/history')).at(-1).url,'https://example.test').searchParams.get('channel_id'),inactive.channel_id);
const stalePicker=d.querySelector('#operatorComposerSourceSelect');
visible=[{...b,channel_active:false},{...a,lifecycle:{...a.lifecycle,state:'accepted'}}];
await w.tenantOperator.refresh();await tick();
assert.equal(d.querySelector('.operator-row .operator-lifecycle-badge').dataset.lifecycleState,'accepted','disabled waiting route cannot mark active accepted group new');
assert.equal(visible.length,2,'history routes are not deleted');
const historyBeforeStale=calls.filter(c=>c.url.includes('/dialogs/history')).length;
stalePicker.value='1';stalePicker.dispatchEvent(new w.Event('change'));await tick();
assert.equal(calls.filter(c=>c.url.includes('/dialogs/history')).length,historyBeforeStale,'captured picker cannot open now-disabled source');
const onlyActivePicker=d.querySelector('#operatorComposerSourceSelect');
assert(onlyActivePicker,'one active source remains selectable when current source was removed');
assert.equal(onlyActivePicker.options.length,2,'placeholder plus one active source');
assert(onlyActivePicker.options[0].disabled);
onlyActivePicker.value='0';onlyActivePicker.dispatchEvent(new w.Event('change'));await tick();await tick();
assert.equal(new URL(calls.filter(c=>c.url.includes('/dialogs/history')).at(-1).url,'https://example.test').searchParams.get('channel_id'),a.channel_id);
visible=[inactive];await w.tenantOperator.refresh();await tick();
assert.equal(d.querySelectorAll('.operator-row').length,1,'disabled-only history remains accessible');
assert.equal(d.querySelectorAll('.operator-row .operator-lifecycle-badge').length,0);
assert(d.querySelector('#operatorComposerSource').hidden);

single=true;
for(const state of ['waiting','accepted','closed']){
 visible=[{...a,lifecycle:{...a.lifecycle,state}}];
 await w.tenantOperator.refresh();await tick();
 assert.equal(d.querySelector('.operator-row-indicators > .operator-lifecycle-badge').dataset.lifecycleState,state);assert(d.querySelector('#operatorComposerSource').hidden,'single source has no picker');
 assert.equal(d.querySelectorAll('.operator-unread-count').length,1);
 assert.equal(d.querySelector('.operator-unread-count').textContent,'2');
}
// Only an explicit server-proven absence suppresses New, never missing metadata.
for(const [state,hasInbound,expected] of [['waiting',false,null],['waiting',undefined,'waiting'],['waiting',null,'waiting'],['waiting',true,'waiting'],['accepted',false,'accepted'],['closed',false,'closed']]){
 visible=[{...a,lifecycle:{...a.lifecycle,state,has_inbound:hasInbound}}];
 await w.tenantOperator.refresh();await tick();
 assert.equal(d.querySelector('.operator-row .operator-lifecycle-badge')?.dataset.lifecycleState??null,expected);
 assert.equal(d.querySelectorAll('.operator-row').length,1,'no-inbound history is retained');
 assert.equal(d.querySelector('.operator-unread-count').textContent,'2','badge eligibility never changes unread counters');
}
single=false;
const outgoingOnly={...a,lifecycle:{...a.lifecycle,has_inbound:false}};
for(const [second,expected] of [
 [{...b,lifecycle:{...b.lifecycle,state:'accepted',has_inbound:true}},'accepted'],
 [{...b,lifecycle:{...b.lifecycle,state:'closed',has_inbound:false}},'closed'],
 [{...b,lifecycle:{...b.lifecycle,has_inbound:false}},null],
 [{...b,lifecycle:{...b.lifecycle,has_inbound:undefined}},'waiting'],
 [{...b,lifecycle:{...b.lifecycle,has_inbound:true}},'waiting']]){
 visible=[outgoingOnly,second];await w.tenantOperator.refresh();await tick();
 assert.equal(d.querySelector('.operator-row .operator-lifecycle-badge')?.dataset.lifecycleState??null,expected,'outgoing-only source must not mask actionable group state');
 assert.equal(d.querySelectorAll('.operator-row').length,1);
 assert.equal(d.querySelector('.operator-unread-count').textContent,'2');
}
single=true;
// Queue mode is visible outside advanced filters; completed history stays searchable.
const queue=d.querySelector('#operatorLifecycle');assert(queue);assert(!queue.closest('#operatorAdvanced'));assert.equal(queue.value,'all');
visible=[{...a,lifecycle:{...a.lifecycle,state:'dismissed',revision:10,has_inbound:true,can_accept:false,can_dismiss:false}}];
await w.tenantOperator.refresh();await tick();
assert.equal(d.querySelector('.operator-row .operator-lifecycle-badge').dataset.lifecycleState,'dismissed');
for(const mode of ['completed','active','all']){
 queue.value=mode;queue.dispatchEvent(new w.Event('change'));await tick();await tick();
 const query=new URL(calls.filter(c=>c.url.includes('/dialogs?')).at(-1).url,'https://example.test').searchParams;
 assert.equal(query.get('lifecycle'),mode);
 assert.equal(d.querySelectorAll('.operator-row').length,mode==='active'?0:1);
 await w.tenantOperator.refresh();assert.equal(queue.value,mode,'refresh preserves queue selection');
}
queue.value='completed';d.querySelector('#operatorSearch').value='Synthetic';d.querySelector('#operatorState').value='no_agent';
await w.tenantOperator.refresh();
const search=new URL(calls.filter(c=>c.url.includes('/dialogs?')).at(-1).url,'https://example.test').searchParams;
assert.equal(search.get('lifecycle'),'completed');assert.equal(search.get('search'),'Synthetic');assert.equal(search.get('state'),'no_agent','AI-state filter stays independent');
d.querySelector('[data-open-operator]').click();await tick();await tick();assert(calls.some(c=>c.url.includes('/dialogs/history')),'completed history can be opened without reopen');
d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{...profile,member:{...profile.member,telegram_id:44}}}));await tick();await tick();assert.equal(queue.value,'all','new identity resets view selection');
queue.value='all';
visible=[{...a,lifecycle_enabled:false}];await w.tenantOperator.refresh();await tick();assert(!d.querySelector('.operator-lifecycle-badge'),'legacy route gets no invented lifecycle');
assert(!calls.some(c=>/send|dialogs_lifecycle/.test(c.url)));dom.window.close();console.log('PASS grouped card explicit source selection, refresh, no send or lifecycle mutation');
})().catch(e=>{console.error(e);process.exitCode=1;});
