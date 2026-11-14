/* Synthetic API boundary; renderer, form, modal lifecycle and capabilities are the real /app code. */
const {docs,assets,appHtml,installNativeTaskFixture}=require('./native_task_assignee_fixture.cjs');
function installNativeCompanyDealFixture(config={}){
 const f=window.nativeTaskFixture,ids={company:'10000000-0000-4000-8000-000000000601',otherCompany:'10000000-0000-4000-8000-000000000602',deal:'10000000-0000-4000-8000-000000000701',otherDeal:'10000000-0000-4000-8000-000000000702',contact:'10000000-0000-4000-8000-000000000501'};
 Object.assign(f,{ids,scoped:config.scoped!==false,pendingRelations:[],holdRelations:false,relationError:null});
 const action=(read=true,update=false)=>({view:read,create:false,update,delete:false});
 f.profile.capabilities={modules:{deals:config.dealRead!==false,customers:config.contactRead!==false},actions:{companies:action(config.companyRead!==false,config.companyUpdate===true),deals:{...action(config.dealRead!==false,config.dealUpdate===true),create:config.dealCreate===true},customers:action(config.contactRead!==false,false),tasks:action(false,false)}};
 // Match production's explicit scoped page admission; module flags remain
 // independent provider/read grants and must not stand in for native C/U/D.
 const a=f.profile.capabilities.actions;
 f.profile.capabilities.pages={profile:true,deals:Object.values(a.deals).some(Boolean),companies:Object.values(a.companies).some(Boolean),customers:Object.values(a.customers).some(Boolean),tasks:false};
 const rel=(source,kind)=>({['link_'+kind]:source,['unlink_'+kind]:source});
 f.companies=[{id:ids.company,name:'Source company',contacts:[],deals:[{id:ids.deal,title:'Existing linked deal'}],actions:{update:config.companyUpdate===true},relation_actions:{...rel(config.companyUpdate===true,'contact'),...rel(config.companyUpdate===true,'deal')}},{id:ids.otherCompany,name:'Available company',contacts:[],deals:[]}];
 f.deals=[{id:ids.deal,title:'Source deal',pipeline_id:'1',stage_id:'2',contact_id:ids.contact,contact_name:'Readable contact context',company_id:ids.company,company_name:'Source company',description:'',scoped_relations:f.scoped,actions:{update:config.dealUpdate===true},relation_actions:rel(config.dealUpdate===true,'company')},{id:ids.otherDeal,title:'Available deal',pipeline_id:'1',stage_id:'2',contact_id:null,company_id:null,scoped_relations:f.scoped,actions:{update:false},relation_actions:rel(false,'company')}];
 if(!f.scoped)for(const company of f.companies)delete company.relation_actions;
 f.options={workflows:[{pipeline_id:'1',pipeline_name:'Main',stage_id:'2',stage_name:'Open'}],members:[],contacts:config.contactRead===false?[]:[{id:ids.contact,display_name:'Readable contact'}],scoped_relations:f.scoped,relation_actions:rel(config.dealUpdate===true,'company')};
 const base=window.fetch,clone=value=>JSON.parse(JSON.stringify(value)),response=(data,status=200)=>new Response(JSON.stringify({success:status===200,...data}),{status,headers:{'Content-Type':'application/json'}});
 window.fetch=async(input,options={})=>{
  const u=new URL(String(input),location.href),method=options.method||'GET',body=options.body?JSON.parse(options.body):null;
  if(u.pathname==='/api/platform/crm/deal-view-preferences'&&method==='GET')return response({source:'native'});
  if(!/^\/api\/platform\/crm\/native\/(companies|deals|contacts)(\/|$)/.test(u.pathname))return base(input,options);
  f.calls.push({url:u.pathname+u.search,path:u.pathname,method,body});
  const kind=u.pathname.split('/')[5],id=u.pathname.split('/')[6];
  if(method==='GET'){
   if(u.pathname.endsWith('/sync-history'))return response({history:{events:[],has_more:false,next_cursor:null,external_state_checked:false}});
   if(kind==='deals'&&u.searchParams.get('options')==='1')return response(clone(f.options));
   if(kind==='contacts')return f.profile.capabilities.actions.customers.view?response({data:clone(f.options.contacts)}):response({error:'forbidden'},403);
   if(!f.profile.capabilities.actions[kind].view||f[kind+'Failure'])return response({error:'forbidden'},403);
   const rows=f[kind];return response(id?{[kind==='deals'?'deal':'company']:clone(rows.find(row=>row.id===id))}:{[kind]:clone(rows)});
  }
  const relation=u.pathname.endsWith('/company-link')||['link_deal','unlink_deal','link_contact','unlink_contact'].includes(body?.action);
  const finish=()=>{
   if(relation){
    if(f.relationError)return response({error:f.relationError},409);
    const deal=f.deals.find(row=>row.id===(kind==='deals'?id:body.entity_id)),company=kind==='deals'?body.company_id:id,unlink=kind==='deals'?body.unlink:body.action==='unlink_deal';
    if(deal){deal.company_id=unlink?null:company;deal.company_name=unlink?null:f.companies.find(row=>row.id===company)?.name;for(const c of f.companies)c.deals=f.deals.filter(d=>d.company_id===c.id).map(d=>({id:d.id,title:d.title}))}
    return response({data:{id:deal?.id,company_id:company,linked:!unlink}});
   }
   if(kind==='deals'){let row=f.deals.find(row=>row.id===id);if(row)Object.assign(row,body);else{row={id:'10000000-0000-4000-8000-000000000703',...body};f.deals.push(row)}return response({deal:clone(row)})}
   throw new Error('Unexpected write '+u.pathname);
  };
  if(relation&&f.holdRelations)return new Promise(resolve=>f.pendingRelations.push(()=>resolve(finish())));return finish();
 };
}
module.exports={docs,assets,appHtml,installNativeTaskFixture,installNativeCompanyDealFixture};
