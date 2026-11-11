/* Synthetic HTTP boundary for the real /app controller. No access-policy replacement. */
const fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..'),docs=path.join(root,'docs');
const assets=['tenant-operator-i18n.js','tenant-crm-sync-history.js','tenant-request-coordinator.js','tenant-view-demand.js','tenant-workspace.js','tenant-members-ui.js'];
function appHtml(inline=false){return fs.readFileSync(path.join(docs,'tenant-app.html'),'utf8').replace(/<script\b([^>]*)src="([^"]+)"[^>]*><\/script>/gi,(tag,attributes,url)=>{const name=path.basename(url);return assets.includes(name)?inline?'<script>'+fs.readFileSync(path.join(docs,name),'utf8').replace(/<\/script/gi,'<\\/script')+'</script>':tag:'';});}
function installNativeTaskFixture(config={}){
  const tenant='00000000-0000-0000-0000-000000000101',dealA='00000000-0000-0000-0000-000000000201',dealB='00000000-0000-0000-0000-000000000202',taskId='00000000-0000-0000-0000-000000000301';
  const profile={success:true,member:{tenant_id:tenant,telegram_id:11,role:config.role||'owner',active:true,display_name:'Synthetic actor',permissions:['tasks']},capabilities:{can_manage_employees:config.staff!==false,modules:{tasks:true,settings:config.settings!==false}},role_permissions:{}};
  const fixture=window.nativeTaskFixture={tenant,dealA,dealB,taskId,profile,calls:[],pendingOptions:[],pendingSaves:[],holdNextOptions:false,holdSave:false,optionError:null,saveError:null,optionOverrides:{},tasks:config.edit?[{id:taskId,deal_id:dealB,deal_title:'Existing linked deal',text:'Existing task text',responsible_id:22,responsible_name:'Eligible editor',due_at:'2026-11-02T06:30:00.000Z',status:'open'}]:[]};
  const privileged=config.role!=='manager'&&config.role!=='operator',assignment={scope:privileged?'tenant':'self',criterion:'active_tasks_view_update',status:'ready',can_manage_employees:config.staff!==false,settings_available:config.settings!==false};
  const deals=[{id:dealA,title:'Selected sales deal'},{id:dealB,title:'Different pipeline deal'}];
  // These are explicit server response fixtures. Inactive, foreign-company, view-only,
  // and granular-only/module-hidden employees never occur in the eligible response.
  fixture.options={deals,members:privileged?[{telegram_id:11,display_name:'Company owner'},{telegram_id:22,display_name:'Eligible editor'}]:[{telegram_id:11,display_name:'Synthetic actor'}],selected_deal_id:dealA,assignment};
  const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
  const success=value=>response({success:true,...value});
  window.confirm=()=>{fixture.confirmations=(fixture.confirmations||0)+1;return fixture.confirmResult!==false};window.alert=()=>{};window.scrollTo=()=>{};
  if(!Element.prototype.scrollIntoView)Element.prototype.scrollIntoView=function(){};
  window.fetch=async(input,options={})=>{
    const url=new URL(String(input),location.href),method=options.method||'GET',body=options.body?JSON.parse(options.body):null,call={url:url.pathname+url.search,path:url.pathname,method,body};fixture.calls.push(call);
    if(url.pathname==='/api/platform/me')return success(JSON.parse(JSON.stringify(fixture.profile)));
    if(url.pathname.startsWith('/api/platform/crm/native/tasks')){
      const identity=body||Object.fromEntries(url.searchParams);
      if(identity.expected_tenant_id&&identity.expected_tenant_id!==fixture.profile.member.tenant_id||identity.expected_user_id&&String(identity.expected_user_id)!==String(fixture.profile.member.telegram_id))return response({success:false,error:'request_scope_changed'},409);
      if(method==='GET'&&url.searchParams.get('options')==='1'){
        if(fixture.optionError)return response({success:false,error:fixture.optionError},503);
        const requested=url.searchParams.get('deal_id')||(url.searchParams.get('task_id')?dealB:dealA),result=JSON.parse(JSON.stringify({...fixture.options,selected_deal_id:fixture.options.assignment?.status==='no_deals'?null:requested,...fixture.optionOverrides[requested]}));
        if(fixture.holdNextOptions){fixture.holdNextOptions=false;return await new Promise(resolve=>fixture.pendingOptions.push(()=>resolve(success(result))))}return success(result);
      }
      if(method==='GET'&&url.pathname.endsWith('/'+taskId)){const result={task:JSON.parse(JSON.stringify(fixture.tasks.find(row=>row.id===taskId)))};if(fixture.holdNextDetail){fixture.holdNextDetail=false;return await new Promise(resolve=>(fixture.pendingDetails||(fixture.pendingDetails=[])).push(()=>resolve(success(result))))}return success(result);}
      if(method==='GET')return success({tasks:fixture.tasks});
      if(method==='POST'){
        if(fixture.saveError)return response({success:false,error:fixture.saveError},fixture.saveError==='assignee_unavailable'?409:403);
        const save=()=>{let task=fixture.tasks.find(row=>row.id===taskId&&url.pathname.endsWith('/'+taskId));if(task)Object.assign(task,body);else{task=fixture.tasks.find(row=>row.request_id===body.request_id);if(!task){task={id:taskId,status:'open',deal_title:'Selected sales deal',...body};fixture.tasks.push(task)}}return success({task})};
        if(fixture.holdSave)return await new Promise(resolve=>fixture.pendingSaves.push(()=>resolve(save())));return save();
      }
    }
    if(method!=='GET')throw new Error('Unexpected write '+url.pathname);
    if(url.pathname==='/api/platform/members')return success({members:[]});
    if(url.pathname==='/api/platform/settings')return success({members:[],access_requests:[],integrations:[]});
    if(url.pathname==='/api/platform/crm/tasks')return success({tasks:[]});
    if(url.pathname==='/api/platform/crm/deals')return success({deals:[]});
    if(url.pathname==='/api/platform/workspace')return success({workspaces:[]});
    return success({companies:[],data:[],deals:[],workflows:[],members:[],integrations:[],access_requests:[]});
  };
}
module.exports={docs,assets,appHtml,installNativeTaskFixture};
