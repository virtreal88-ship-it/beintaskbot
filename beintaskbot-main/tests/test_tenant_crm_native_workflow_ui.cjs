const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const fs=require('node:fs'),assert=require('node:assert/strict');
let contacts=[],deals=[],tasks=[],writes=[],optionFailure=false;
const member={tenant_id:'tenant-a',telegram_id:7,role:'manager',display_name:'Operator'};
const result=(value={})=>({ok:true,status:200,json:async()=>({success:true,...value})});
const dom=new JSDOM(fs.readFileSync('docs/tenant-app.html','utf8').replace(/<script[^>]+src="[^"]+"[^>]*><\/script>/g,''),{
 url:'https://example.test/app?view=tasks',runScripts:'dangerously',beforeParse(w){
  w.confirm=()=>true;w.alert=()=>{};w.tenantI18n={language:'az',t:key=>key};w.fetch=async(url,opt={})=>{
   const path=new URL(url,'https://example.test').pathname,method=opt.method||'GET',body=opt.body?JSON.parse(opt.body):{};writes.push({path,method,body});
   if(path==='/api/platform/me')return result({member,capabilities:{modules:{tasks:true,deals:true,customers:true}}});
   if(path==='/api/platform/crm/native/contacts'&&method==='GET')return result({data:contacts});
   if(path==='/api/platform/crm/native/contacts'&&method==='POST'){const x={id:'contact-'+(contacts.length+1),display_name:body.name,phone:body.phone,email:body.email};contacts.push(x);return result({data:x});}
   if(path==='/api/platform/crm/native/companies')return result({companies:[]});
   if(optionFailure&&method==='GET'&&new URL(url,'https://example.test').searchParams.has('options')&&(path.endsWith('/native/deals')||path.endsWith('/native/tasks')))return {ok:false,status:503,json:async()=>({success:false,error:'Mock option service unavailable'})};
   if(path==='/api/platform/crm/native/deals'&&method==='GET'&&new URL(url,'https://example.test').searchParams.has('options'))return result({workflows:[{pipeline_id:'pipeline-1',pipeline_name:'Pipeline A',stage_id:'stage-1',stage_name:'New'}],contacts,companies:[],members:[{telegram_id:7,display_name:'Operator'}]});
   if(path==='/api/platform/crm/native/deals'&&method==='GET')return result({deals});
   if(path==='/api/platform/crm/native/deals'&&method==='POST'){const x={id:'deal-'+(deals.length+1),title:body.title,pipeline_id:body.pipeline_id,stage_id:body.stage_id};deals.push(x);return result({deal:x});}
   if(path==='/api/platform/crm/native/tasks'&&method==='GET'&&new URL(url,'https://example.test').searchParams.has('options'))return result({deals:[{id:'deal-1',title:'Deal A'}],members:[{telegram_id:7,display_name:'Operator'}]});
   if(path==='/api/platform/crm/native/tasks'&&method==='GET')return result({tasks});
   if(path==='/api/platform/crm/native/tasks'&&method==='POST'){const x={id:'task-'+(tasks.length+1),...body};tasks.push(x);return result({task:x});}
   if(path==='/api/platform/crm/tasks')return result({tasks:[]});
   if(path==='/api/platform/crm/deals')return result({deals:[]});
   return result({});
  };
 }});
const w=dom.window,d=w.document,wait=()=>new Promise(resolve=>setTimeout(resolve,20));
(async()=>{
 await wait();await wait();
 for(const [button,modal] of [['nativeContactCreate','nativeContactModal'],['nativeDealCreate','nativeDealModal'],['nativeTaskCreate','nativeTaskModal']]){
  d.getElementById(button).click();await wait();assert(d.getElementById(modal).classList.contains('show'),`${button} opens the modal`);
 }
 d.getElementById('nativeContactName').value='New contact';d.getElementById('nativeContactPhone').value='+994501234567';d.getElementById('nativeContactForm').requestSubmit();await wait();await wait();
 assert.equal(contacts.length,1);assert(d.getElementById('nativeContactsList').textContent.includes('New contact'),'contact list refreshes after API save');
 d.getElementById('nativeDealTitle').value='New deal';d.getElementById('nativeDealPipeline').value='pipeline-1';d.getElementById('nativeDealStage').value='stage-1';d.getElementById('nativeDealForm').requestSubmit();await wait();await wait();
 assert.equal(deals.length,1);assert(d.getElementById('nativeDealsList').textContent.includes('New deal'),'deal list refreshes after API save');
 d.getElementById('nativeTaskDeal').value='deal-1';d.getElementById('nativeTaskText').value='Past due test task';d.getElementById('nativeTaskResponsible').value='7';d.getElementById('nativeTaskDue').value='2020-01-01T10:00';d.getElementById('nativeTaskForm').requestSubmit();await wait();await wait();
 assert.equal(tasks.length,1);assert.equal(writes.find(x=>x.path==='/api/platform/crm/native/tasks'&&x.method==='POST').body.due_at,'2020-01-01T06:00:00.000Z');assert(d.getElementById('nativeTasksList').textContent.includes('Past due test task'),'task list refreshes after API save');
 assert(writes.some(x=>x.path==='/api/platform/crm/native/contacts'&&x.method==='POST'));assert(writes.some(x=>x.path==='/api/platform/crm/native/deals'&&x.method==='POST'));assert(writes.some(x=>x.path==='/api/platform/crm/native/tasks'&&x.method==='POST'));
 optionFailure=true;d.getElementById('nativeDealCreate').click();await wait();await wait();assert(d.getElementById('nativeDealModal').classList.contains('show'));assert(d.getElementById('nativeDealModalError').textContent.includes('Mock option service unavailable'),'option API errors are shown in the opened form');
 console.log('PASS: CRM contact/deal/task forms open, save through mocked Native CRM APIs and refresh lists; past task due dates are accepted.');w.close();
})().catch(e=>{console.error(e);process.exitCode=1;w.close();});