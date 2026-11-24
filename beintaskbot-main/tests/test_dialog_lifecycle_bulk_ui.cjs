const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.SUPPORT_CASES_JSDOM||'jsdom');
const read=p=>fs.readFileSync(p,'utf8'),tick=()=>new Promise(r=>setTimeout(r,0));
async function scenario({confirm=true,fail=null,cycle=false,scopeChange=false,oldServer=false,large=false,ineligible=false,onlyIneligible=false,unknownInbound=false,selected=false,stale=false,leaveView=false}={}){
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://example.test/app?view=dialogs&lang=ru',runScripts:'outside-only'}),w=dom.window,d=w.document;
 if(selected)d.querySelector('#view-dialogs').classList.add('active');
 const profile={member:{tenant_id:'tenant',telegram_id:33,role:'owner',active:true},capabilities:{modules:{dialogs:true,support:true}}};
 const route=(id,state='waiting',owner=33)=>({provider:'connect',lead_id:id,channel_id:'channel-'+id,conversation_id:'client-'+id,state:'human',agent_name:'Agent',can_send:false,lifecycle_enabled:true,lifecycle:{id:'episode-'+id,state,operator_id:owner,revision:4,can_dismiss:state==='waiting',can_close:state==='accepted'&&owner===33,last_inbound_id:'inbound-'+id}});
 const a=route(1),b=route(2,'accepted'),other=route(3,'accepted',44),closed=route(4,'closed'),group={...a,group_sources:[a,b,other]};
 const noInbound={...route(5),lifecycle:{...route(5).lifecycle,last_inbound_id:null,has_inbound:false,can_dismiss:false}};
 if(unknownInbound)noInbound.lifecycle.has_inbound=undefined;
 const unavailable={...route(6),lifecycle:{...route(6).lifecycle,can_dismiss:false}};
 if(ineligible||onlyIneligible)group.group_sources=onlyIneligible?[other,noInbound,unavailable]:[a,b,other,noInbound,unavailable];
 const routes=new Map([a,b,other,closed].map(r=>[String(r.lead_id),r]));
 const calls=[],prompts=[];let bulk=false,staleHistory,staleOpen,reminderOpens=0;
 if(selected){
  // Model browser-dispatched programmatic scroll, which JSDOM otherwise
  // doesn't emit, and make the pending read cursor eligible only during bulk.
  Object.defineProperty(d,'hidden',{get:()=>!bulk});
  Object.defineProperty(w.HTMLElement.prototype,'scrollTop',{
   get(){return this._top||0;},set(top){this._top=top;if(this.id==='chatRows')w.queueMicrotask(()=>this.dispatchEvent(new w.Event('scroll')));}
  });
  w.tenantSharedReminders={dialogOpened(){reminderOpens++;},markRows(){}};
 }
 const historyPage=dialog=>({dialog,messages:selected?[{id:'m1',body:'existing message',direction:'incoming'}]:[],next_before:null,...(selected?{read_through_message_id:'m1'}:{})});
 w.tenantProfileSnapshot=profile;w.matchMedia=()=>({matches:selected,addListener(){},removeListener(){}});w.alert=()=>{};w.confirm=text=>{prompts.push(text);return confirm;};
 w.fetch=async(url,options={})=>{
  url=String(url);const body=options.body?JSON.parse(options.body):null;calls.push({url,body});let result;
  if(url.endsWith('dialogs_lifecycle')){
   if(fail&&body.lead_id===2){if(fail==='network')throw Error('Network interrupted');return{ok:false,status:fail,json:async()=>({success:false,error:'Changed'})};}
   result={lifecycle:{state:'closed',revision:5}};
   // The bulk mutation receipt intentionally lacks action capabilities.
   if(selected&&body.lead_id===2){
    routes.set('2',{...b,lifecycle:{...b.lifecycle,...result.lifecycle,can_start:true,can_close:false,can_transfer:true,
      transfer_targets:[{employee_id:44,name:'Recipient'}]}});
    if(leaveView)d.querySelector('#view-dialogs').classList.remove('active');
   }
  }else if(url.includes('/dialogs/history')){
   const q=new URL(url,'https://example.test').searchParams;
   if(bulk&&stale&&!staleHistory)return new Promise(resolve=>{
    staleHistory=()=>resolve({ok:true,status:200,json:async()=>({success:true,result:historyPage(b)})});
   });
   result=historyPage(routes.get(q.get('lead_id')));
  }else if(url.includes('/dialogs/read')){
   result={updated:true};
  }else if(url.includes('/dialogs?')){
   const offset=new URL(url,'https://example.test').searchParams.get('offset');
   const preview=new URL(url,'https://example.test').searchParams.get('completion_preview')==='1';
   result={completion_preview:preview&&!oldServer,dialogs:large&&preview?Array.from({length:50},(_,i)=>route(Number(offset)+i+10,'accepted')):offset==='10'?(onlyIneligible?[closed]:[a,closed]):[group],channels:[],agents:[],unread_count:17,next_offset:large&&preview?(Number(offset)<450?Number(offset)+50:null):bulk?(cycle?0:offset==='10'?null:10):null};
   if(scopeChange&&preview){w.document.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{...profile,member:{...profile.member,telegram_id:99}}}));}
  }else throw Error('Unexpected request '+url);
  return{ok:true,status:200,json:async()=>({success:true,result})};
 };
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));await tick();await tick();
 await w.tenantOperator.refresh();if(selected){assert(await w.tenantOperator.openDialog(b));await tick();}bulk=true;calls.length=0;if(stale)staleOpen=w.tenantOperator.openDialog(b);
 d.querySelector('#operatorSearch').value='filter ignored';
 const button=d.querySelector('#operatorCompleteAll');assert(button);button.click();button.click();
 for(let i=0;i<30&&button.disabled;i++)await tick();
 const mutations=calls.filter(c=>c.body),notice=d.querySelector('#operatorBulkCompletionStatus').textContent;
 assert(!calls.some(c=>c.url.includes('/dialogs/read')),'bulk completion never marks read');
 assert(!calls.some(c=>/send|copilot|support-tickets/.test(c.url)),'bulk never sends messages or creates cases');
 if(selected){
  if(stale){assert.equal(typeof staleHistory,'function');staleHistory();assert.equal(await staleOpen,false,'stale same-route history may not downgrade a completed receipt');}
  await tick();
  const reads=calls.filter(c=>c.url.includes('/dialogs/history'));
  assert.equal(reads.length,leaveView?0:stale?2:1,'only the still-open completed dialogue is refreshed');
  assert(reads.every(read=>new URL(read.url,'https://example.test').searchParams.get('lead_id')==='2'));
  assert.equal(!!d.querySelector('[data-operator-lifecycle="start"]'),!leaveView,'authoritative mobile history renders Start only in the active dialogue view');
  assert.equal(!!d.querySelector('[data-operator-lifecycle="transfer"]'),!leaveView,'the completed dialogue can be transferred without opening a new episode');
  assert(!calls.some(c=>c.url.includes('/dialogs/read')),'capability refresh is read-only');
  assert.equal(reminderOpens,0,'bulk refresh never acknowledges reminders');
 }else assert(!calls.some(c=>c.url.includes('/dialogs/history')),'preparation must not make one history request per dialogue');
 if(cycle||scopeChange||oldServer){assert.equal(mutations.length,0);assert.equal(prompts.length,0);}
 else if(onlyIneligible){
  assert.equal(mutations.length,0);assert.equal(prompts.length,0);
  assert.match(notice,/Нет доступных диалогов/);assert.match(notice,/Не включено в операцию: 3/);
  assert.match(notice,/нет входящего сообщения: 1/);assert.match(notice,/принято другим сотрудником: 1/);assert.match(notice,/действие недоступно: 1/);
 }
 else{
  assert.equal(prompts.length,1,'double clicks get one confirmation');assert.match(prompts[0],large?/500/:/1/,'exact eligible source count');
  assert(calls.filter(c=>c.url.includes('/dialogs?')).every(c=>!c.url.includes('filter')));
  assert.match(prompts[0],new RegExp('Не включено в операцию: '+(large?0:ineligible?4:2)));
  if(ineligible){assert.match(prompts[0],new RegExp('нет входящего сообщения: '+(unknownInbound?0:1)));assert.match(prompts[0],new RegExp('действие недоступно: '+(unknownInbound?2:1)));}
  if(!confirm){assert.equal(mutations.length,0,'cancel is read-only');if(large)assert.equal(calls.length,10,'500 routes require 10 paged previews, not 500 histories');}
  else{
   assert.equal(mutations.length,1,notice);
   assert.equal(mutations[0].body.action,'close');assert(!('message_id' in mutations[0].body));assert.equal(mutations[0].body.expected_revision,4);
   assert.equal(mutations[0].body.expected_tenant_id,'tenant');assert.equal(mutations[0].body.expected_user_id,33);
   assert.equal(mutations[0].body.lead_id,2,'only owned accepted routes close; waiting routes are never marked no-reply in bulk');
   if(fail!==403)assert.match(notice,new RegExp('Не включено в операцию: '+(ineligible?4:2)));
   if(fail===409)assert.match(notice,/Пропущено из-за изменений или доступа: 1/,'conflicts remain separate from exclusions');
   if(!fail)assert.match(notice,/Пропущено из-за изменений или доступа: 0/);
   if(ineligible&&!fail){assert.match(notice,/Завершено: 1/);assert.match(notice,new RegExp('нет входящего сообщения: '+(unknownInbound?0:1)));assert.match(notice,/принято другим сотрудником: 1/);assert.match(notice,new RegExp('действие недоступно: '+(unknownInbound?2:1)));}

   if(fail==='network')assert.match(notice,/1/,'network ambiguity and unprocessed remainder shown');
  }
 }
 assert(!button.disabled);dom.window.close();
}
(async()=>{for(const opts of [{},{selected:true},{selected:true,stale:true},{selected:true,leaveView:true},{ineligible:true},{ineligible:true,unknownInbound:true},{ineligible:true,fail:409},{ineligible:true,fail:'network'},{onlyIneligible:true},{confirm:false},{fail:409},{fail:403},{fail:'network'},{cycle:true},{scopeChange:true},{oldServer:true},{large:true,confirm:false}])await scenario(opts);console.log('bulk dialogue lifecycle UI: 17 scenarios passed');})().catch(e=>{console.error(e);process.exitCode=1;});
