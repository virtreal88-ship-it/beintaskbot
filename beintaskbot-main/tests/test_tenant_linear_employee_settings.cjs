// Isolated DOM contract tests. No provider/network calls.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync(require('node:path').join(__dirname,'../docs/tenant-linear-settings.js'),'utf8');
const events=['linear_done','linear_status_changed','proactive_risk','task_assigned'].map(key=>({key,label:key,channels:['telegram','push']}));
const makeRow=(id,role='manager')=>({telegram_id:id,role,display_name:'Employee',permissions:['tasks','linear:read'],card_version:'card1',editable_fields:['permission_changes','work_settings'],work_settings:{version:'work1',notifications:events,values:{notifications:{linear_done:{telegram:true},task_assigned:{push:true}}}}});
const flush=()=>new Promise(resolve=>setTimeout(resolve,5));
async function harness(){
 const dom=new JSDOM('<div id="view-settings"></div>',{runScripts:'outside-only',url:'https://isolated.invalid'}),w=dom.window;
 const state={rows:[makeRow(2),makeRow(1,'owner')],posts:[],memberReads:0,fail:false,pending:null};
 let watcher;
 w.tenantViewDemand={watch:x=>watcher=x,clean:()=>{}};
 w.TenantLinearRules={render:()=>{},read:()=>({})};
 w.fetch=async(url,options)=>{
  if(options.method==='POST'){
   const data=JSON.parse(options.body);state.posts.push({url,data});
   if(url==='/api/platform/members'&&state.applyWrites){
    const row=state.rows.find(row=>row.telegram_id===data.telegram_id);
    if(data.changes.permission_changes)row.permissions=data.changes.permission_changes.linear?['tasks','linear']:['tasks'];
    const notifications=data.changes.work_settings?.changes.notifications||{};
    for(const [event,channels] of Object.entries(notifications))row.work_settings.values.notifications[event]={...row.work_settings.values.notifications[event],...channels};
    row.card_version='card2';row.work_settings.version='work2';
   }
   if(state.pending)return state.pending;
   if(state.fail)throw new Error('Network interrupted');
   return {ok:true,json:async()=>({success:true,applied:{telegram_id:data.telegram_id,card_version:'card2'}})};
  }
  if(url==='/api/platform/members'){state.memberReads++;return {ok:true,json:async()=>({success:true,members:state.rows})};}
  return {ok:true,json:async()=>({success:true,status:'disconnected',updated_at:'v1',settings:{},members:state.rows})};
 };
 w.eval(source);watcher.activate({member:{tenant_id:'A',telegram_id:1,role:'owner'}});
 w.document.getElementById('tlLoad').click();await flush();
 return {w,state,watcher,root:id=>w.document.querySelector(`[data-tl-employee="${id}"]`)};
}
(async()=>{
 let h=await harness(),{w,state}=h,root=h.root(2);
 assert.equal(state.posts.length,0,'Initial load must not write');
 assert.equal(root.querySelector('[data-tl-grant]').indeterminate,true,'Scoped permission must not become a full grant');
 assert.equal(root.querySelector('[data-tl-event="task_assigned"]'),null,'Unrelated prefs absent');
 assert.equal(root.querySelector('[data-field]'),null,'Independent controls never enter Linear binding serialization');
 assert.equal(h.root(1).querySelector('[data-tl-grant]').disabled,true,'Owner grant stays protected after load');
 assert.equal(h.root(1).querySelector('[data-tl-event="proactive_risk"]').disabled,false,'Owner risk prefs remain editable');
 root.querySelector('[data-tl-save]').click();await flush();assert.equal(state.posts.length,0,'Unchanged save must not write');
 w.document.querySelector('[data-member="2"] [data-field="account"]').value='Unsaved binding';
 root.querySelector('[data-tl-event="linear_done"][data-tl-channel="push"]').click();
 root.querySelector('[data-tl-save]').click();await flush();
 assert.equal(w.document.querySelector('[data-member="2"] [data-field="account"]').value,'Unsaved binding','Preference save must preserve binding draft');
 assert.equal(state.posts.length,1);assert.equal(state.memberReads,2,'Successful save rereads members');
 assert.deepEqual(state.posts[0],{url:'/api/platform/members',data:{action:'update_member_card',telegram_id:2,expected_tenant_id:'A',expected_user_id:1,expected_version:'card1',changes:{work_settings:{expected_version:'work1',changes:{notifications:{linear_done:{push:true}}}}}}});
 assert.equal(h.root(1).querySelector('[data-tl-grant]').disabled,true);
 h=await harness();root=h.root(2);root.querySelector('[data-tl-grant]').click();root.querySelector('[data-tl-save]').click();await flush();
 assert.deepEqual(h.state.posts[0].data.changes,{permission_changes:{linear:true}});
 h=await harness();root=h.root(2);h.state.applyWrites=true;
 const draft=root.querySelector('[data-tl-event="linear_done"][data-tl-channel="push"]');draft.click();
 const binding=h.w.document.querySelector('[data-member="2"] [data-field="account"]');binding.value='Unsaved provider draft';
 h.w.document.getElementById('tlSave').click();await flush();
 assert.equal(h.state.posts.length,0,'Main save must not discard employee drafts');assert.equal(draft.checked,true);
 assert.match(h.w.document.getElementById('tlMessage').textContent,/Əvvəlcə əməkdaşın/);
 root.querySelector('[data-tl-save]').click();await flush();
 assert.equal(binding.value,'Unsaved provider draft','Employee save preserves provider draft');
 h.w.document.getElementById('tlSave').click();await flush();
 assert.equal(h.state.posts.length,2);assert.equal(h.state.posts[1].url,'/api/platform/linear/settings');
 assert.equal(h.state.posts[1].data.settings.members['2'].account,'Unsaved provider draft');
 h=await harness();root=h.root(2);h.state.fail=true;root.querySelector('[data-tl-grant]').click();root.querySelector('[data-tl-save]').click();await flush();
 assert.equal(root.dataset.tlBlocked,'true');assert.equal(root.querySelector('[data-tl-save]').disabled,true);
 root.querySelector('[data-tl-save]').click();await flush();assert.equal(h.state.posts.length,1,'Uncertain write must not retry');
 h=await harness();root=h.root(2);let finish;h.state.pending=new Promise(resolve=>finish=resolve);
 root.querySelector('[data-tl-grant]').click();root.querySelector('[data-tl-save]').click();await flush();
 h.watcher.activate({member:{tenant_id:'B',telegram_id:1,role:'owner'}});
 finish({ok:true,json:async()=>({success:true,applied:{telegram_id:2,card_version:'new'}})});await flush();
 assert.equal(h.w.document.querySelector('[data-tl-employee]'),null,'Old reply cannot paint a new company');
 assert.equal(h.state.memberReads,1,'No stale context refresh after switch');
 console.log('PASS: Linear per-member grants and notifications, sparse saves, protected owner, uncertain outcomes and tenant switching.');
})().catch(error=>{console.error(error);process.exitCode=1;});
