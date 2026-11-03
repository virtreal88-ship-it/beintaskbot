/* HTTP-boundary fixtures for the current /app inline and standalone controllers. */
const fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..'),docs=path.join(root,'docs');
function appHtml(){const names=['tenant-operator-i18n.js','tenant-request-coordinator.js','tenant-view-demand.js','tenant-task-flow.js','tenant-tasks.js','tenant-task-completion.js'];return fs.readFileSync(path.join(docs,'tenant-app.html'),'utf8').replace(/<script\b[^>]*src="([^"]+)"[^>]*><\/script>/gi,(tag,url)=>{const name=path.basename(url);return names.includes(name)?'<script>'+fs.readFileSync(path.join(docs,name),'utf8').replace(/<\/script/gi,'<\\/script')+'</script>':'';});}
function installFixture(config={}){
  const A='00000000-0000-0000-0000-000000000101',B='00000000-0000-0000-0000-000000000102',deal='00000000-0000-0000-0000-000000000201',task='00000000-0000-0000-0000-000000000301';
  const initial={id:task,source:'native',deal_id:deal,deal_title:'Sales deal',text:'PRIVATE task A',responsible_id:22,responsible_name:'Eligible employee',due_at:'2031-01-01T06:00:00+00:00',status:'open',created_by:11,updated_at:'2026-11-03T00:00:00+00:00'};
  const profile={success:true,member:{tenant_id:A,telegram_id:11,role:config.manager?'manager':'owner',active:true,permissions:['tasks'],workflow:{}},capabilities:{modules:{tasks:true}},role_permissions:{}};
  const f=window.taskFlowFixture={A,B,deal,task,profile,calls:[],native:config.native||(config.empty?[]:[initial]),kommo:config.kommo||(config.empty?[]:[{source:'kommo',id:'9007199254740993',kommo_task_id:'9007199254740993',text:'Saved external task',status:'open',completed:false,due_at:null}]),commands:config.commands||{},pending:[],hold:null,rawWrite:null,saveStatus:null,readErrors:{},externalState:'created',confirmations:0};
  if(config.nativeOnly){profile.capabilities.modules.tasks=false;profile.member.permissions=['tasks:view','tasks:create','tasks:update'];}
  if(config.manager)initial.responsible_id=11;
  f.flags=['native','kommo'].map(source=>({source,view:true,create:true,update:source==='native',complete:true,archive:source==='native',sync:source==='kommo'}));if(config.nativeOnly)Object.assign(f.flags[1],{view:false,create:false,update:false,complete:false,archive:false,sync:false});
  for(const [key,value] of Object.entries(config.storage||{}))sessionStorage.setItem(key,value);
  window.confirm=()=>{f.confirmations++;return true;};window.alert=()=>{};window.scrollTo=()=>{};if(!Element.prototype.scrollIntoView)Element.prototype.scrollIntoView=function(){};
  const response=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}}),success=data=>response({success:true,...data});
  window.fetch=async(input,options={})=>{
    const url=new URL(String(input),location.href),method=options.method||'GET',body=options.body?JSON.parse(options.body):null;f.calls.push({path:url.pathname,url:url.pathname+url.search,method,body});
    const pair=body?.expected_tenant_id?body:Object.fromEntries(url.searchParams),mismatch=pair.expected_tenant_id&&pair.expected_tenant_id!==f.profile.member.tenant_id||pair.expected_user_id&&pair.expected_user_id!==String(f.profile.member.telegram_id);
    if(url.pathname==='/api/platform/me')return success(JSON.parse(JSON.stringify(f.profile)));
    if(mismatch)return response({success:false,error:'task_context_changed'},409);
    if(method==='POST'&&f.dropNextWrite){f.dropNextWrite=false;throw new TypeError('Connection lost before delivery');}
    let result;
    if(url.pathname==='/api/platform/crm/tasks'&&url.searchParams.get('flow')==='1'&&method==='GET'){
      const source=url.searchParams.get('source'),items=f[source];if(f.readErrors[source])return response({success:false,error:'task_unavailable'},503);if(!f.flags.find(row=>row.source===source)?.view)return response({success:false,error:'task_forbidden'},403);
      result={source,sources:f.flags,shown:items.length,limit:source==='native'?100:200,has_more:false,scope:'tenant',state:items.length?'ready':'empty'};
      if(url.searchParams.has('request_id')){const request=url.searchParams.get('request_id');result.command=f.commands[request]||{source,request_id:request,state:'not_confirmed',task_id:null,task:null,cache_state:'not_applicable'};}
      else if(url.searchParams.has('task_id')){result.task=items.find(row=>row.id===url.searchParams.get('task_id'));if(!result.task)return response({success:false,error:'task_not_found'},404);}
      else result.tasks=items;
    }else if(url.pathname==='/api/platform/crm/tasks/options')result={executors:[{id:'11',name:'Owner',pipelines:[{pipeline_id:10,name:'Personal pipeline'}]},{id:'22',name:'No pipeline employee',pipelines:[]}],deals:[{id:'7',name:'Saved lead'}]};
    else if(url.pathname==='/api/platform/crm/tasks'&&method==='POST'){
      const existing=f.commands[body.request_id],row={source:'kommo',id:'9',text:body.text,status:'open',completed:false,due_at:body.due_at};
      result={command:existing||{source:'kommo',request_id:body.request_id,state:f.externalState,task_id:f.externalState==='created'?'9':null,task:f.externalState==='created'?row:null,cache_state:f.externalState==='created'?'available':'not_applicable'}};f.commands[body.request_id]=result.command;if(result.command.state==='created'&&!f.kommo.some(value=>value.id==='9'))f.kommo.push(row);
    }else if(url.pathname==='/api/platform/crm/tasks/complete'&&method==='POST'){result=f.completeReceipt||{task_id:body.task_id,completed:true};if(result.completed&&String(result.task_id)===String(body.task_id)){const row=f.kommo.find(value=>value.id===String(body.task_id));if(row){row.completed=true;row.status='completed';}}
    }else if(url.pathname.startsWith('/api/platform/crm/native/tasks')){
      if(url.searchParams.get('options')==='1'&&f.optionsError)return response({success:false,error:f.optionsError.error},f.optionsError.status);
      if(url.searchParams.get('options')==='1')result={deals:[{id:deal,title:'Sales deal'}],members:config.manager?[{telegram_id:11,display_name:'Manager'}]:[{telegram_id:11,display_name:'Owner'},{telegram_id:22,display_name:'Eligible employee'}],selected_deal_id:deal,assignment:{scope:config.manager?'self':'tenant',criterion:'active_tasks_view_update',status:'ready',can_manage_employees:false,settings_available:false}};
      else if(method==='GET'&&url.pathname.endsWith('/'+task))result={task:f.native.find(row=>row.id===task)};
      else if(method==='GET')result={tasks:f.native};
      else {if(f.saveStatus)return response({success:false,error:f.saveStatus.code},f.saveStatus.status);let row=f.native.find(value=>value.id===task&&url.pathname.endsWith('/'+task));if(row&&body.expected_updated_at!==undefined&&body.expected_updated_at!==row.updated_at)return response({success:false,error:'task_conflict'},409);if(!row){row=f.native.find(value=>value.request_id===body.request_id);if(!row){row={...initial,...body,id:task,request_id:body.request_id,text:body.text.trim(),source:'native',updated_at:'2026-11-03T01:00:00+00:00'};f.native.push(row);}f.commands[body.request_id]={source:'native',request_id:body.request_id,state:'created',task_id:task,task:row,cache_state:'available'};}else if(body.action==='archive')f.native=f.native.filter(value=>value!==row);else{Object.assign(row,body);if(body.action==='complete')row.status='completed';if(body.action==='reopen')row.status='open';row.updated_at='2026-11-03T01:00:00+00:00';}result=body.action==='archive'?{archived:true}:{task:row};}
    }else if(url.pathname==='/api/platform/crm/tasks')result={tasks:[]};
    else if(url.pathname==='/api/platform/crm/deals')result={deals:[]};
    else result={companies:[],data:[],deals:[],tasks:[],workflows:[],members:[],integrations:[],access_requests:[]};
    let data=JSON.parse(JSON.stringify({success:true,...result}));
    if(method==='POST'&&f.transformWrite)data=f.transformWrite(data);
    if(method==='POST'&&f.rawWrite!==null){const raw=f.rawWrite;f.rawWrite=null;return new Response(raw,{status:200,headers:{'Content-Type':'application/json'}});}
    if(f.hold&&f.hold(url,method)){f.hold=null;return {ok:true,status:200,json:()=>new Promise(resolve=>f.pending.push(()=>resolve(data)))};}
    return response(data);
  };
}
module.exports={appHtml,installFixture};
