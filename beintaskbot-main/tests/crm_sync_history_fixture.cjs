/* Local-journal HTTP boundary. It performs no provider reads or mutations. */
function installSyncHistoryFixture(config={}){
 const clone=value=>JSON.parse(JSON.stringify(value)),base=window.fetch;
 const event=(state='pending',index=1)=>({event_id:'20000000-0000-4000-8000-'+String(index).padStart(12,'0'),kind:'scalar',state,attempt_outcome:state==='synced'?'acknowledged':'not_started',created_at:'2026-11-11T08:00:00.000Z',local_revision:'2026-11-11T08:00:00.000Z',recorded_at:'2026-11-11T09:00:00.000Z',issue:'none'});
 const f=window.syncHistoryFixture={calls:[],events:config.events||[event('pending')],replies:[],pending:[],pendingBodies:[],sourceFailures:{},holdSources:false,pendingSources:[]};
 const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});
 window.fetch=async(input,options={})=>{
  const u=new URL(String(input),location.href),method=options.method||'GET';
  if(!u.pathname.endsWith('/sync-history')){if(method==='GET'&&/^\/api\/platform\/crm\/native\/(contacts|deals|companies)\/[^/]+$/.test(u.pathname)){if(f.sourceFailures[u.pathname])return response({success:false,error:'private source error'},f.sourceFailures[u.pathname]);const result=await base(input,options);if(f.holdSources)return new Promise(resolve=>f.pendingSources.push(()=>resolve(result)));return result}return base(input,options)}
  if(method!=='GET')throw Error('History fixture received a write');
  f.calls.push({url:u.pathname+u.search,path:u.pathname,method,cache:options.cache,credentials:options.credentials});
  const reply=f.replies.shift()||{},status=reply.status||200,payload=clone(reply.payload||{success:status===200,...(status===200?{history:{events:f.events,has_more:false,next_cursor:null,external_state_checked:false}}:{error:'PRIVATE PROVIDER ERROR'})});
  if(reply.bodyHold)return new Response(new ReadableStream({start(controller){f.pendingBodies.push(()=>{controller.enqueue(new TextEncoder().encode(JSON.stringify(payload)));controller.close()})}}),{status,headers:{'Content-Type':'application/json'}});
  const finish=()=>response(payload,status);if(reply.hold)return new Promise(resolve=>f.pending.push(()=>resolve(finish())));return finish();
 };
}
module.exports={installSyncHistoryFixture};
