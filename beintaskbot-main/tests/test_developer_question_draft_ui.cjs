/* Mock-only question drafting: deliberate AI, durable identities and explicit sending. */
const assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const settle=async()=>{for(let i=0;i<20;i++)await new Promise(resolve=>setImmediate(resolve));};
const base='/api/platform/ai/dialog-consultations';
const ids=['11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222'];
const messages=ids.map((id,index)=>({id,message_type:index?'audio':'text',body:index?'':'Printer stopped',direction:'incoming',happened_at:'2026-11-01T12:00:00Z',source_version:'version-'+index}));
const ok=result=>({ok:true,json:async()=>({success:true,result})});
async function setup(kind,{language='en',versions=true,agent=true,targets=true}={}){
 const dom=new JSDOM('<html><head></head><body><div id="host"></div><section id="view-dialogs" class="active"></section><select data-app-language></select><details id="operatorMoreActions"><summary>More</summary><div id="operatorMenuExtra"></div></details><aside id="operatorCopilot"></aside><textarea id="chatText">Private customer text</textarea></body></html>',{url:'https://crm.test/app',runScripts:'outside-only'});
 const w=dom.window,d=w.document,calls=[],member={tenant_id:'tenant',telegram_id:33,role:'owner'},route={provider:'connect',agent_id:agent?'agent-a':null,agent_mode:'manual',channel_id:'source-channel',conversation_id:'source',lead_id:41},ticket={...route,id:'33333333-3333-4333-8333-333333333333',status:'new'};
 const state={hold:null,error:false,current:true,reply:'Please investigate the printer.',missing:true,preview:messages.map(message=>({...message,...(!versions?{source_version:undefined}:{})}))};
 w.tenantI18n={language,date:value=>String(value)};w.tenantOperator={member,currentDialog:route,hasRight:()=>true};w.tenantProfileSnapshot={member};
 w.fetch=async(url,options={})=>{
  const path=new URL(url,w.location).pathname,body=options.body?JSON.parse(options.body):undefined;calls.push({path,body});
  if(path===base+'/preview')return ok({messages:state.preview});
  if(path===base+'/destinations')return ok({destinations:!targets?[]:[{channel_id:'dev-channel',conversation_id:'developer-a',customer_name:'Developer A'},{channel_id:'dev-channel',conversation_id:'developer-b',customer_name:'Developer B'}]});
  if(path===base+'/question-draft'){
   if(state.hold)await state.hold;
   if(state.error)throw Error('Response lost');
   return ok({text:state.reply,request_id:'44444444-4444-4444-8444-444444444444',developer_question_draft:true,customer_message_sent:false,context_usage:{missing_media:state.missing?[{id:ids[1],kind:'audio',reason:'not_analyzed'}]:[]}});
  }
  if(path===base)return ok({id:'consultation',request_id:body?.request_id});
  if(path.endsWith('/dispatch'))return ok({state:'sent'});
  if(path===base+'/replies')return ok({replies:[],consultations:[]});
  if(path==='/api/platform/notifications')return ok({notifications:[],unread_count:0});
  return ok({});
 };
 w.eval(fs.readFileSync('docs/tenant-'+(kind==='dialog'?'dialog':'support')+'-consultations.js','utf8'));
 let controller;
 if(kind==='case')controller=w.tenantCaseConsultations.mount({host:d.querySelector('#host'),ticket,member,getMessages:()=>state.preview,isCurrent:()=>state.current});
 await settle();
 const ctx={kind,dom,w,d,calls,state,route,ticket,controller,
  async open(){d.querySelector(kind==='dialog'?'#dialogDeveloperAsk':'#consultCreate').click();await settle();return d.querySelector(kind==='dialog'?'.dialog-consultation-form[role=dialog]':'#caseDeveloperForm');},
  closeForm(){const form=d.querySelector(kind==='dialog'?'.dialog-consultation-form[role=dialog]':'#caseDeveloperForm');[...form.querySelectorAll('button')].find(node=>['Close','Закрыть','Bağla'].includes(node.textContent)).click();},
  close(){controller?.dispose();dom.window.close();}
 };
 return ctx;
}
function controls(ctx,form){const selects=form.querySelectorAll('select');return{form,summary:form.querySelector('textarea'),recipient:selects[0],first:selects[1],last:selects[2],draft:form.querySelector('[data-question-draft]'),send:form.querySelector('button[type=submit]'),media:form.querySelector(ctx.kind==='dialog'?'[data-index]':'[data-case-media-check]'),status:form.querySelector('[data-question-draft-status]')};}
function change(ctx,node,value,type='change'){if('checked' in node)node.checked=value;else node.value=value;node.dispatchEvent(new ctx.w.Event(type,{bubbles:true}));}
function fill(ctx,c,text='Printer stopped'){change(ctx,c.recipient,'0');change(ctx,c.first,ctx.kind==='dialog'?'0':ids[0]);change(ctx,c.last,ctx.kind==='dialog'?'1':ids[1]);change(ctx,c.summary,text,'input');change(ctx,c.media,true);}
const questionCalls=ctx=>ctx.calls.filter(call=>call.path===base+'/question-draft');
const prepareCalls=ctx=>ctx.calls.filter(call=>call.path===base&&call.body);
function assertNoSend(ctx){assert.equal(prepareCalls(ctx).length,0);assert.equal(ctx.calls.some(call=>call.path.endsWith('/dispatch')||/messages\/send|dialogs\/send|analy[sz]e|transcrib/.test(call.path)),false);assert.equal(ctx.d.querySelector('#chatText').value,'Private customer text');}
(async()=>{
 const unselected=await setup('dialog'),unselectedForm=await unselected.open();
 unselectedForm.querySelector('textarea').value='Saved question';unselectedForm.querySelector('textarea').dispatchEvent(new unselected.w.Event('input'));
 assert.equal(unselectedForm.querySelector('select').value,'');unselected.closeForm();const reopenedUnselected=await unselected.open();assert.equal(reopenedUnselected.querySelector('select').value,'','dismissal must not silently choose the first recipient');assert.equal(reopenedUnselected.querySelector('button[type=submit]').disabled,true);assertNoSend(unselected);unselected.close();
 for(const kind of ['dialog','case']){
  const availability=await setup(kind),aform=controls(availability,await availability.open());
  const reason=()=>aform.form.querySelector('[data-send-disabled-reason]');
  assert.match(reason().textContent,/Choose a developer/);assert.equal(aform.send.disabled,true);
  assert.ok(aform.send.getAttribute('aria-describedby').split(' ').includes(reason().id));
  change(availability,aform.recipient,'0');assert.match(reason().textContent,/Write a message/);
  change(availability,aform.summary,'   ','input');assert.equal(aform.send.disabled,true);assert.match(reason().textContent,/Write a message/);
  change(availability,aform.summary,'Explicit manual question','input');assert.equal(aform.send.disabled,false);assert.equal(reason().hidden,true);
  change(availability,aform.last,kind==='dialog'?'0':ids[0]);assert.equal(aform.send.disabled,true);assert.match(reason().textContent,/1 to 30/);
  change(availability,aform.first,kind==='dialog'?'0':ids[0]);assert.equal(aform.send.disabled,false);assert.equal(reason().textContent,'');
  assertNoSend(availability);assert.equal(questionCalls(availability).length,0);availability.close();
  const noTargets=await setup(kind,{targets:false}),nt=controls(noTargets,await noTargets.open());assert.equal(nt.send.disabled,true);assert.match(nt.form.querySelector('[data-send-disabled-reason]').textContent,/No accessible Developers|No accessible dialogues/);assertNoSend(noTargets);noTargets.close();

  const ctx=await setup(kind),c=controls(ctx,await ctx.open());fill(ctx,c);assert.equal(questionCalls(ctx).length,0,'opening and editing never use paid AI');
  assert.match(c.form.textContent,/only already saved analysis/);assert.match(c.form.textContent,/No new file analysis/);
  let release;ctx.state.hold=new Promise(resolve=>release=resolve);c.draft.click();c.draft.click();c.form.dispatchEvent(new ctx.w.Event('submit',{cancelable:true}));await settle();
  assert.equal(questionCalls(ctx).length,1);assert.equal(c.draft.disabled,true);assert.equal(c.send.disabled,true);assert.equal(c.summary.disabled,false,'user edits remain available while drafting');assert.match(c.form.querySelector('[data-send-disabled-reason]').textContent,/AI is drafting/);assertNoSend(ctx);
  release();await settle();ctx.state.hold=null;
  assert.equal(c.summary.value,ctx.state.reply);assert.equal(c.form.querySelectorAll('input[type=checkbox]').length,0);assert.equal(c.send.disabled,false,'AI output still requires explicit send click');assert.match(c.status.textContent,/contents were not used/);assertNoSend(ctx);
  const firstRequest=questionCalls(ctx)[0].body;assert.deepEqual(firstRequest.message_ids,ids);assert.deepEqual(firstRequest.media_ids,[ids[1]]);assert.deepEqual(firstRequest.source_versions,{[ids[0]]:'version-0',[ids[1]]:'version-1'});assert.equal(!!firstRequest.case_id,kind==='case');
  c.draft.click();await settle();assert.equal(questionCalls(ctx)[1].body.request_id,firstRequest.request_id,'unchanged generated text rechecks the original request');assert.equal(questionCalls(ctx)[1].body.summary,'Printer stopped','retry preserves the original input payload');
  change(ctx,c.summary,'Edited employee question','input');assert.equal(c.form.querySelectorAll('input[type=checkbox]').length,0);assert.equal(c.send.disabled,false);
  c.send.click();await settle();assert.equal(prepareCalls(ctx).length,1);assert.equal(ctx.calls.filter(call=>call.path.endsWith('/dispatch')).length,1);assert.equal(prepareCalls(ctx)[0].body.summary,'Edited employee question');assert.equal(prepareCalls(ctx)[0].body.question_draft_request_id,'44444444-4444-4444-8444-444444444444');assert.deepEqual(prepareCalls(ctx)[0].body.source_versions,firstRequest.source_versions);ctx.close();

  for(const edit of ['summary','selection','media','recipient']){
   const late=await setup(kind),f=controls(late,await late.open());fill(late,f);let release;late.state.hold=new Promise(resolve=>release=resolve);f.draft.click();await settle();
   if(edit==='summary')change(late,f.summary,'Keep my edit','input');if(edit==='selection')change(late,f.first,kind==='dialog'?'1':ids[1]);if(edit==='media')change(late,f.media,false);if(edit==='recipient')change(late,f.recipient,'1');
   const expected=f.summary.value;release();await settle();assert.equal(f.summary.value,expected,kind+' late result after '+edit+' cannot overwrite');assert.equal(f.form.querySelectorAll('input[type=checkbox]').length,0);assertNoSend(late);late.close();
  }

  const lost=await setup(kind),f=controls(lost,await lost.open());fill(lost,f);lost.state.error=true;f.draft.click();await settle();const requestId=questionCalls(lost)[0].body.request_id;assert.match(f.status.textContent,/same request/);lost.closeForm();const reopened=controls(lost,await lost.open());assert.equal(reopened.summary.value,'Printer stopped');assert.equal(reopened.media.checked,true);lost.state.error=false;reopened.draft.click();await settle();assert.equal(questionCalls(lost)[1].body.request_id,requestId,'close/reopen keeps uncertain identity');assert.equal(reopened.summary.value,lost.state.reply);assertNoSend(lost);
  change(lost,reopened.media,false);assert.equal(reopened.summary.value,lost.state.reply,'selection change does not erase generated text');assert.equal(reopened.send.disabled,true,'stale AI sources cannot send');assert.match(reopened.status.textContent,/changed/);assert.match(reopened.form.querySelector('[data-send-disabled-reason]').textContent,/changed/);reopened.form.dispatchEvent(new lost.w.Event('submit',{cancelable:true}));await settle();assertNoSend(lost);lost.close();

  const closePending=await setup(kind),p=controls(closePending,await closePending.open());fill(closePending,p);let finish;closePending.state.hold=new Promise(resolve=>finish=resolve);p.draft.click();await settle();closePending.closeForm();const pendingReopened=controls(closePending,await closePending.open());assert.equal(pendingReopened.draft.disabled,true,'reopened pending generation is not duplicated');assert.equal(pendingReopened.send.disabled,true);finish();await settle();closePending.state.hold=null;assert.equal(pendingReopened.summary.value,'Printer stopped','dismissed form output is not silently accepted by reopened form');assert.equal(pendingReopened.draft.disabled,false);pendingReopened.draft.click();await settle();assert.equal(questionCalls(closePending)[0].body.request_id,questionCalls(closePending)[1].body.request_id);assertNoSend(closePending);closePending.close();

  const changed=await setup(kind),r=controls(changed,await changed.open());fill(changed,r);let finishRoute;changed.state.hold=new Promise(resolve=>finishRoute=resolve);r.draft.click();await settle();if(kind==='dialog'){changed.route.conversation_id='other';changed.d.dispatchEvent(new changed.w.CustomEvent('tenant-chat-attachments-ready'));}else{changed.ticket.id='other-case';}finishRoute();await settle();assert.equal(r.summary.value,'Printer stopped');assertNoSend(changed);changed.close();

  const profile=await setup(kind),v=controls(profile,await profile.open());fill(profile,v);let finishProfile;profile.state.hold=new Promise(resolve=>finishProfile=resolve);v.draft.click();await settle();profile.w.tenantOperator.member={tenant_id:'other',telegram_id:55,role:'owner'};profile.d.dispatchEvent(new profile.w.CustomEvent('tenant-profile',{detail:{member:profile.w.tenantOperator.member}}));finishProfile();await settle();assert.equal(v.summary.value,'Printer stopped');assertNoSend(profile);profile.close();

  const revised=await setup(kind),versioned=controls(revised,await revised.open());fill(revised,versioned);versioned.draft.click();await settle();revised.closeForm();revised.state.preview[1].source_version='new-analysis-version';const refreshed=controls(revised,await revised.open());assert.equal(refreshed.summary.value,revised.state.reply);assert.equal(refreshed.send.disabled,true,'changed saved analysis invalidates generated draft provenance');refreshed.draft.click();await settle();assert.equal(questionCalls(revised).at(-1).body.source_versions[ids[1]],'new-analysis-version');assert.notEqual(questionCalls(revised)[0].body.request_id,questionCalls(revised).at(-1).body.request_id);assertNoSend(revised);revised.close();

  const reassigned=await setup(kind),a=controls(reassigned,await reassigned.open());fill(reassigned,a);let endAssignment;reassigned.state.hold=new Promise(resolve=>endAssignment=resolve);a.draft.click();await settle();if(kind==='dialog')reassigned.route.agent_id='new-agent';else reassigned.ticket.agent_id='new-agent';endAssignment();await settle();assert.equal(a.summary.value,'Printer stopped','agent reassignment rejects late generation');assertNoSend(reassigned);reassigned.close();

  const roundtrip=await setup(kind),rt=controls(roundtrip,await roundtrip.open());fill(roundtrip,rt);let endRoundtrip;roundtrip.state.hold=new Promise(resolve=>endRoundtrip=resolve);rt.draft.click();await settle();change(roundtrip,rt.summary,'Temporary edit','input');change(roundtrip,rt.summary,'Printer stopped','input');endRoundtrip();await settle();assert.equal(rt.summary.value,'Printer stopped','changing back still invalidates late result');assert.match(rt.status.textContent,/not applied/);assertNoSend(roundtrip);roundtrip.close();

  const noVersions=await setup(kind,{versions:false}),nv=controls(noVersions,await noVersions.open());assert.equal(nv.draft.disabled,true,'AI fails closed without explicit preview versions');noVersions.close();
  for(const [language,label] of [['ru','Сформулировать вопрос с AI'],['az','Sualı AI ilə tərtib et'],['en','Draft question with AI']]){const localized=await setup(kind,{language}),lf=controls(localized,await localized.open());assert.equal(lf.draft.textContent,label);assert.match(lf.form.querySelector('[data-send-disabled-reason]').textContent,language==='ru'?/Выберите разработчика/:language==='az'?/tərtibatçı seçin/:/Choose a developer/);localized.close();}
 }
 const noAgent=await setup('case',{agent:false}),c=controls(noAgent,await noAgent.open());assert.equal(c.draft.disabled,true);noAgent.close();
 console.log('PASS: dialog/case question drafts, RU/AZ/EN, bounded saved evidence, stale response guards, duplicate clicks, durable retries, close/reopen and separate reviewed send');
})().catch(error=>{console.error(error);process.exitCode=1;});
