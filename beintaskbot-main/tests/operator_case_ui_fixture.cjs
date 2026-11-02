/* Local-only API fixture shared by real operator DOM and Chromium journeys. */
function installOperatorCaseFixture({mode='manual',cached=false,storage={}}={}){
  const w=window,d=document;
  d.querySelectorAll('.view').forEach(node=>node.classList.toggle('active',node.id==='view-dialogs'));
  w.alert=()=>{};
  w.tenantViewDemand={watch(){}};
  w.tenantProfileSnapshot={member:{tenant_id:'ui-tenant',telegram_id:11,role:'owner',active:true},capabilities:{modules:{dialogs:true,support:true}}};
  w.caseUI={route:{provider:'connect',lead_id:41,channel_id:'client-channel',conversation_id:'client-conversation',customer_name:'Client',customer_kind:'contact',agent_id:'agent',agent_name:'Agent',agent_mode:mode,agent_status:mode,can_send:true,controls_allowed:true,available_actions:[]},calls:[],createOutcome:'ok',responseChange:null,holdHistory:false,holdSend:false};
  for(const [key,value]of Object.entries(storage))sessionStorage.setItem(key,value);
  const fixture=w.caseUI,ok=result=>({ok:true,status:200,json:async()=>({success:true,result})});
  const message={id:'client-message',direction:'incoming',author_type:'customer',body:'Printer needs help',message_type:'text',happened_at:'2026-11-01T12:00:00Z'};
  w.fetch=async(url,options={})=>{
    const path=new URL(String(url),w.location).pathname,body=options.body?JSON.parse(options.body):undefined;
    fixture.calls.push({path,method:options.method||'GET',body});
    if(path==='/api/platform/me')return{ok:true,status:200,json:async()=>w.tenantProfileSnapshot};
    if(path==='/api/platform/ai/dialogs')return ok({dialogs:[fixture.route],channels:[],agents:[],next_offset:null,total_count:1});
    if(path==='/api/platform/ai/dialogs/history'){
      if(fixture.holdHistory)await new Promise(resolve=>fixture.releaseHistory=resolve);
      return ok({dialog:{...fixture.route},messages:[message],next_before:null});
    }
    if(path==='/api/platform/ai/dialogs/read')return ok({updated:true});
    if(path==='/api/platform/ai/dialog-consultations/destinations')return ok({destinations:[{channel_id:'developer-a-channel',conversation_id:'developer-a',customer_name:'Developer A'},{channel_id:'developer-b-channel',conversation_id:'developer-b',customer_name:'Developer B'}]});
    if(path==='/api/platform/ai/support-tickets'&&options.method==='POST'){
      if(fixture.createOutcome==='lost')throw Error('Response lost after create');
      if(fixture.createOutcome==='rejected')return{ok:false,status:422,json:async()=>({success:false,error:'Developer unavailable'})};
      const ticket={id:'created-case',tenant_id:body.expected_tenant_id,create_request_id:body.request_id,lead_id:body.lead_id,channel_id:body.channel_id,conversation_id:body.conversation_id,assigned_developer_channel_id:body.assigned_developer_channel_id,assigned_developer_conversation_id:body.assigned_developer_conversation_id,...fixture.responseChange};return ok({ticket});
    }
    if(path==='/api/platform/ai/dialog-consultations/replies')return ok({consultations:[{id:'consultation',case_id:'case-with-reply',state:'sent',recipient:'Developer A'}],auto_dispatches:[],replies:[{id:'developer-reply',consultation_id:'consultation',source_kind:'provider',author:'Developer A',draft:'Private developer follow-up draft',draft_state:'completed',body:'Will review tomorrow',happened_at:'2026-11-01T13:00:00Z',handled_at:'2026-11-01T13:01:00Z'}]});
    if(path==='/api/platform/ai/internal-consultations')return ok({consultations:[]});
    if(path==='/api/platform/ai/support-tickets')return ok({tickets:[],members:[],channels:[],next_offset:null});
    if(path==='/api/platform/crm/chat/send'){
      if(fixture.holdSend)await new Promise(resolve=>fixture.releaseSend=resolve);
      return ok({state:'accepted'});
    }
    if(path.endsWith('/public-shares'))return ok([]);
    throw Error('Unexpected fixture route '+path);
  };
  w.tenantAppNavigate=view=>{d.querySelectorAll('.view').forEach(node=>node.classList.toggle('active',node.id==='view-'+view));d.dispatchEvent(new CustomEvent('tenant-view',{detail:{view}}));};
}
module.exports={installOperatorCaseFixture};
