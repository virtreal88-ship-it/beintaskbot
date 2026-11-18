/* Extend the synthetic operator API only; no external service or mutations. */
function installActiveCaseFixture(){
 const f=window.caseUI,base=window.fetch;
 f.route={...f.route,lifecycle_enabled:true,lifecycle:{id:'episode-a',state:'accepted',revision:7,operator_id:11,can_close:true}};
 f.conflict=false;
 window.fetch=async(url,options={})=>{if(f.conflict&&String(url).endsWith('/support-tickets')&&options.method==='POST'){f.calls.push({path:'/api/platform/ai/support-tickets',method:'POST',body:JSON.parse(options.body)});f.route={...f.route,can_send:false,lifecycle:{...f.route.lifecycle,state:'waiting',revision:8}};return{ok:false,status:409,json:async()=>({success:false,error:'Accepted dialogue changed'})};}return base(url,options);};
}
module.exports={installActiveCaseFixture};
