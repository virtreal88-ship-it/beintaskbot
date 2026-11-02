/* HTTP fixtures only. The /app router, request coordinator, demand controller,
   employee controller and source shell are the current production modules. */
const assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('../.support-test-deps/node_modules/jsdom');
const {nodeHtml,installCompanyEmployeesFixture}=require('./company_employees_entry_fixture.cjs');
const tenant='10000000-0000-4000-8000-000000000101';
const version='2026-11-02T10:00:00.123456+00:00';
const clone=value=>JSON.parse(JSON.stringify(value));
const serverRow=(extra={})=>({tenant_id:tenant,telegram_id:22,first_name:'Aysel',last_name:'Testova',login:'aysel.test',email:'aysel@example.test',phone:'+994501234567',avatar_url:'https://example.test/avatar.png',account_status:'active',password_change_available:false,display_name:'PRIVATE EMPLOYEE A',role:'worker',active:true,permissions:['tasks','legacy:retained'],explicit_permissions:['tasks','legacy:retained'],effective_permissions:['tasks'],legacy_permissions:['legacy:retained'],whatsapp_numbers:[{phone:'+994507654321',recognition_enabled:true}],card_version:version,editable_fields:['display_name','role','permissions','active','whatsapp_numbers'],read_only_reasons:Object.fromEntries(['first_name','last_name','login','email','phone','avatar_url','account_status'].map(key=>[key,'global_account_identity'])),...extra});
const settle=async()=>{for(let n=0;n<14;n++)await new Promise(resolve=>setImmediate(resolve))};
const until=async predicate=>{for(let n=0;n<120;n++){if(predicate())return;await new Promise(resolve=>setTimeout(resolve,5))}assert(predicate(),'actual employee app did not settle')};
async function mount(config={}){
 const errors=[],vc=new VirtualConsole();vc.on('jsdomError',error=>errors.push(error.message));
 const dom=new JSDOM(nodeHtml(),{url:'https://fixture.test/app?view=employees&lang='+(config.language||'en'),runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,beforeParse(w){
  w.Response=Response;w.Headers=Headers;w.Request=Request;w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
  w.eval('('+installCompanyEmployeesFixture.toString()+')('+JSON.stringify(config)+')');
  const f=w.companyEmployeesFixture,base=w.fetch;
  f.members=[serverRow(config.row)];f.profile.members=clone(f.members);
  f.writeResult={status:200,data:{success:true,applied:{telegram_id:22,card_version:'opaque-next-version'}}};
  f.afterWriteMembers=null;f.readFailure=null;f.meFailure=null;f.holdWrite=false;f.holdReadAfterWrite=false;f.transportError=false;
  w.fetch=async(input,options={})=>{
   const url=new URL(String(input),w.location.href),method=options.method||'GET';
   if(url.pathname==='/api/platform/me'&&f.meFailure){f.calls.push({path:url.pathname,url:url.pathname+url.search,method,body:null});return new Response(JSON.stringify({success:false,error:'Synthetic current profile failure'}),{status:f.meFailure,headers:{'Content-Type':'application/json'}})}
   if(url.pathname!=='/api/platform/members')return base(input,options);
   const call={path:url.pathname,url:url.pathname+url.search,method,body:options.body?JSON.parse(options.body):null};f.calls.push(call);
   const response=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
   if(method==='GET'){
    const data={success:true,members:clone(f.members)},failure=f.readFailure;
    const finish=()=>failure?response({success:false,error:failure.code},failure.status):response(data);
    if(f.holdMembers||f.holdNextMembers){f.holdNextMembers=false;return new Promise(resolve=>f.pendingMembers.push(()=>resolve(finish())))}return finish();
   }
   assert.equal(method,'POST','only the intended isolated employee write is allowed');
   const result=clone(f.writeResult),transport=f.transportError;
   const finish=()=>{if(transport)throw TypeError('Synthetic lost response');if(result.data?.success&&f.afterWriteMembers)f.members=clone(f.afterWriteMembers);if(f.holdReadAfterWrite)f.holdNextMembers=true;return result.raw!==undefined?new Response(result.raw,{status:result.status,headers:{'Content-Type':'application/json'}}):response(result.data,result.status)};
   if(f.holdWrite)return new Promise((resolve,reject)=>f.pendingWrites.push(()=>{try{resolve(finish())}catch(error){reject(error)}}));return finish();
  };
 }});
 await settle();const b={w:dom.window,d:dom.window.document,f:dom.window.companyEmployeesFixture,errors,close:()=>dom.window.close()};
 await until(()=>b.d.querySelector('[data-staff-edit="22"]'));return b;
}
const writes=b=>b.f.calls.filter(call=>call.path==='/api/platform/members'&&call.method==='POST');
const reads=b=>b.f.calls.filter(call=>call.path==='/api/platform/members'&&call.method==='GET');
function edit(b,pane='profile',id=22){b.d.querySelector(pane==='numbers'?`[data-staff-number="${id}"]`:`[data-staff-edit="${id}"]`).click();return b.d.getElementById('staffEditForm')}
function input(b,form,key,value){const node=form.elements.namedItem(key);if(node.type==='checkbox')node.checked=value;else node.value=value;node.dispatchEvent(new b.w.Event('input',{bubbles:true}));return node}
function create(b){b.d.getElementById('staffAdd').click();const form=b.d.getElementById('staffInviteForm');for(const [key,value]of Object.entries({first_name:'Synthetic',last_name:'Employee',login:'synthetic.employee',email:'new@example.test',phone:'+994509999999',password:'Controlled-test-credential-1!',confirm_password:'Controlled-test-credential-1!'}))input(b,form,key,value);return form}
function submit(b,form){form.dispatchEvent(new b.w.Event('submit',{bubbles:true,cancelable:true}))}
async function reauthorize(b,change){change(b.f);await b.w.tenantRequestCoordinator.revalidateProfile();await settle()}
module.exports={mount,serverRow,version,tenant,settle,until,writes,reads,edit,input,create,submit,reauthorize};
