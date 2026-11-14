/* Local-only HTTP boundary for the actual company/support/candidate UI modules. */
function installSummaryLanguageFixture({role='owner',view='profile',tenant='language-tenant',user=11,durable=null}={}){
  const w=window,d=document,clone=value=>JSON.parse(JSON.stringify(value));
  w.alert=()=>{};w.confirm=()=>true;w.scrollTo=()=>{};w.tenantViewDemand={watch(){}};
  w.tenantProfileSnapshot={member:{tenant_id:tenant,telegram_id:user,role,active:true,permissions:['support','dialogs']},capabilities:{modules:{support:true,dialogs:true,ai_agents:true}}};
  w.tenantCanAccessCompanySettings=()=>w.tenantProfileSnapshot.member?.role==='owner'&&w.tenantProfileSnapshot.member?.active!==false;
  const ticket={id:'a0000000-0000-4000-8000-000000000001',lead_id:41,channel_id:'client-channel',conversation_id:'client-conversation',contact_name:'Test customer',channel_name:'WhatsApp',description:'Saved employee description',summary:'Saxlanmış AZ xülasəsi 49,9992 — Printer PRO',suggestions:'Saved suggestions',next_steps:'Saved next steps',summary_state:'completed',summary_current:true,summary_available:true,summary_hash:'a'.repeat(64),summary_language:{language:'az',method:'customer_detected',policy_version:2,policy_source:'company',detector_version:1,media_incomplete:true},status:'waiting',agent_id:'agent',ai_state:'human',created_at:'2026-11-02T10:00:00Z'};
  const candidate={id:'b0000000-0000-4000-8000-000000000002',version:3,title:'Сохранённый RU кейс',symptoms:['Проблема клиента'],cause:{status:'unknown',text:''},resolution_steps:['Проверить кабель'],operator_actions:['Проверил соединение'],outcome:{status:'unknown',text:'Ожидается проверка',message_ids:[]},case_kind:'technical',resolution_method:'chat',status:'pending_review',content_language:'ru',language_selection:{language:'ru',method:'customer_detected',policy_version:2,media_incomplete:false},source_message_ids:[],route:{provider:'connect',lead_id:41,channel_id:'client-channel',conversation_id:'client-conversation'},can_approve:false,approval_block_reason:'confirmation_required'};
  const f=w.languageFixture={calls:[],policy:{mode:'legacy',version:0,fallback_language:'az',fixed_language:null,source:'legacy'},ticket,candidate,history:[],policyError:false,regenOutcome:'ok',holdRegen:false,generationCalls:0,results:{},improveLanguage:'en',summaryReads:0,pendingByActor:{}};
  if(durable&&durable.tenant===tenant){Object.assign(f.ticket,clone(durable.ticket));f.policy=clone(durable.policy);f.history=clone(durable.history||[]);f.results=clone(durable.results||{});f.pendingByActor=clone(durable.pendingByActor||{});f.generationCalls=durable.generationCalls||0;}else if(durable){f.ticket.summary='Current company summary';f.ticket.summary_hash='c'.repeat(64);}
  const actor=()=>w.tenantProfileSnapshot.member.tenant_id+':'+w.tenantProfileSnapshot.member.telegram_id,pending=()=>f.pendingByActor[actor()]||null;
  f.snapshot=()=>clone({tenant,ticket:f.ticket,policy:f.policy,history:f.history,results:f.results,pendingByActor:f.pendingByActor,generationCalls:f.generationCalls});
  const ok=value=>({ok:true,status:200,json:async()=>clone(value)}),wrapped=result=>ok({success:true,result}),failure=(status,error)=>({ok:false,status,json:async()=>({success:false,error})});
  w.tenantAppNavigate=target=>{d.querySelectorAll('.view').forEach(node=>node.classList.toggle('active',node.id==='view-'+target));d.dispatchEvent(new CustomEvent('tenant-view',{detail:{view:target}}));};
  d.querySelectorAll('.nav [data-view]').forEach(button=>button.onclick=()=>w.tenantAppNavigate(button.dataset.view));w.tenantAppNavigate(view);
  w.fetch=async(url,init={})=>{
    const parsed=new URL(String(url),w.location),path=parsed.pathname,body=init.body?JSON.parse(init.body):null,method=init.method||'GET';f.calls.push({path,url:String(url),method,body});
    if(path==='/api/platform/me')return ok(w.tenantProfileSnapshot);
    if(path==='/api/platform/companies/manage'){
      if(method==='POST'){if(f.policyError)return failure(409,'stale policy');if(body.expected_version!==f.policy.version)return failure(409,'stale policy');f.policy={mode:body.mode,version:f.policy.version+1,fallback_language:body.fallback_language,fixed_language:body.fixed_language,source:'company'};}
      return ok({success:true,summary_language:f.policy});
    }
    if(path==='/api/platform/ai/support-tickets')return wrapped({tickets:[f.ticket],members:[],channels:[],next_offset:null,unresolved_count:1});
    if(path==='/api/platform/ai/support-tickets/'+ticket.id+'/summary'&&method==='POST'){
      if(f.results[body.request_id])return wrapped({...clone(f.results[body.request_id]),ticket:f.ticket,summary_history:f.history.slice(0,10),summary_pending_request:pending()});
      if(body.expected_summary_hash!==f.ticket.summary_hash)return failure(409,'summary text changed');
      f.generationCalls++;if(f.holdRegen)await new Promise(resolve=>f.releaseRegen=resolve);
      const previous={description:f.ticket.description,summary:f.ticket.summary,suggestions:f.ticket.suggestions,next_steps:f.ticket.next_steps},proposal={description:'Generated description',summary:'New summary in '+(body.language||'policy'),suggestions:'Generated suggestions',next_steps:'Generated next steps'};
      const state=f.regenOutcome==='conflict'?'conflict':['unavailable','unknown','blocked','pending','lost'].includes(f.regenOutcome)?'unavailable':'applied';
      const entry={id:'version-'+f.generationCalls,request_id:body.request_id,state,request_state:['unknown','blocked','pending'].includes(f.regenOutcome)?f.regenOutcome:state==='unavailable'?'unknown':state,created_at:'2026-11-02T11:00:00Z',base_hash:body.expected_summary_hash,previous,proposal:state==='unavailable'?null:proposal,language:{language:body.language||'az',method:body.language?'explicit':'customer_detected',policy_version:f.policy.version,media_incomplete:true}};f.history.unshift(entry);
      if(state==='applied'){Object.assign(f.ticket,proposal,{summary_hash:'b'.repeat(64),summary_language:entry.language});}
      f.pendingByActor[actor()]=['unknown','pending'].includes(entry.request_state)?{command:{request_id:body.request_id,confirm_ai_cost:body.confirm_ai_cost,expected_summary_hash:body.expected_summary_hash,language:body.language},state:entry.request_state}:null;if(f.regenOutcome==='pending')f.ticket.summary_state='generating';const result={ticket:clone(f.ticket),messages:[],summary_history:clone(f.history),summary_request:{request_id:body.request_id,state:entry.request_state},summary_pending_request:pending(),expert_results:[],next_before:null};f.results[body.request_id]=result;
      if(f.regenOutcome==='lost')throw Error('Response lost');return wrapped(result);
    }
    if(path==='/api/platform/ai/support-tickets/'+ticket.id){f.summaryReads++;const result={ticket:clone(f.ticket),messages:[],summary_history:clone(f.history.slice(0,10)),summary_pending_request:clone(pending()),expert_results:[],next_before:null};if(f.holdSummaryRead)await new Promise(resolve=>f.releaseSummaryRead=resolve);return wrapped(result);}
    if(path==='/api/platform/ai/dialog-consultations/destinations')return wrapped({destinations:[]});
    if(path==='/api/platform/ai/dialog-consultations/replies')return wrapped({consultations:[],replies:[],auto_dispatches:[]});
    if(path==='/api/platform/ai/internal-consultations')return wrapped({consultations:[]});
    if(path==='/api/platform/ai/dialogs')return wrapped({dialogs:[],channels:[],agents:[],next_offset:null});
    if(path==='/api/platform/ai/agents')return wrapped([{id:'agent',name:'Test agent'}]);
    if(path==='/api/platform/ai/channels')return wrapped({channels:[{channel_id:'client-channel',name:'WhatsApp Test',provider:'connect'}]});
    const base='/api/platform/ai/knowledge-candidates';
    if(path===base)return wrapped({candidates:[f.candidate],next_before:null});
    if(path===base+'/batches'&&method==='POST')return wrapped({batch:{id:'batch',status:'completed',params:body,message_count:1,summary:{},items:[]}});
    if(path===base+'/'+candidate.id+'/improve'&&method==='POST'){f.generationCalls++;const language=body.language||f.improveLanguage;return wrapped({candidate_id:candidate.id,expected_version:f.candidate.version,language,language_selection:{language,method:body.language?'explicit':'customer_detected',policy_version:f.policy.version},requires_explicit_save:true,proposal:{...clone(f.candidate),title:'Reviewed '+language+' proposal',symptoms:['New symptoms'],content_language:language}});}
    if(path===base+'/'+candidate.id){if(method==='PATCH'){Object.assign(f.candidate,body,{version:f.candidate.version+1});}return wrapped({candidate:f.candidate,evidence:[],route:f.candidate.route});}
    if(path.startsWith(base+'/'+candidate.id+'/'))return wrapped({messages:[],history:[],next_before:null});
    if(path.endsWith('/public-shares'))return wrapped([]);
    throw Error('Unexpected UI fixture route '+path);
  };
}
module.exports={installSummaryLanguageFixture};
