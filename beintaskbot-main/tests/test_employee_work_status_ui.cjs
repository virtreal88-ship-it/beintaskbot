const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const read=f=>fs.readFileSync(path.join(__dirname,'..','docs',f),'utf8');
const page=read('tenant-app.html'),statusAsset=read('tenant-profile-work-status.js'),rosterAsset=read('tenant-members-ui.js');
const A='00000000-0000-0000-0000-000000009201',B='00000000-0000-0000-0000-000000009202';
const fixture={worker:{member:{tenant_id:A,telegram_id:41,active:true,role:'worker',permissions:['tasks']},capabilities:{pages:{profile:true,dialogs:false}}},
 owner:{member:{tenant_id:A,telegram_id:11,active:true,role:'owner'},capabilities:{can_view_employees:true,can_create_employees:true}},
 manager:{member:{tenant_id:A,telegram_id:31,active:true,role:'admin'},capabilities:{can_view_employees:true}}};
const snapshot=(status,configured=true)=>({success:true,result:{available:true,status,configured,can_update:true,can_set_offline:true,routing_enforced:false}});
const memberRows=[
 {tenant_id:A,telegram_id:41,active:true,role:'worker',display_name:'Synthetic employee',work_status:'offline'},
 {tenant_id:A,telegram_id:42,active:false,role:'worker',display_name:'Deactivated employee',work_status:'away'}
];
const tick=async()=>{for(let i=0;i<12;i++)await new Promise(resolve=>setImmediate(resolve));};
const response=value=>({ok:true,json:async()=>value});
async function domTest(){
 const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
 const init=(profile,view)=>{const w=new JSDOM(page,{url:'https://profile.test/app?view='+view,runScripts:'outside-only'}).window;
   w.tenantProfileSnapshot=profile;w.tenantI18n={language:'az'};w.document.querySelector('#view-'+view).classList.add('active');return w;};
 const w=init(fixture.worker,'profile'),results=[];let stored=null,release;
 w.fetch=async(url,options={})=>{
   const u=new URL(url,w.location.origin);assert.equal(u.pathname,'/api/platform/profile/availability');
   if(options.method==='POST'){
     const input=JSON.parse(options.body);results.push(input);stored=input.status;
     if(stored==='away')return new Promise(resolve=>{release=()=>resolve(response(snapshot(stored)));});
     return response(snapshot(stored));
   }
   assert.equal(u.searchParams.get('expected_tenant_id'),A);assert.equal(u.searchParams.get('expected_user_id'),'41');
   return response(snapshot(stored??'online',!!stored));
 };
 w.eval(statusAsset);await tick();
 const el=id=>w.document.getElementById(id);
 assert.equal(el('profileWorkStatusForm').hidden,false,'worker without Dialogs sees the control');
 assert.equal(el('profileWorkStatusTitle').textContent,'İş statusu');
 const select=el('profileWorkStatusSelect'),form=el('profileWorkStatusForm');
 assert.equal(select.value,'online');assert.deepEqual([...select.options].map(o=>o.text),['Onlayn','Kənarda','Nahar','Oflayn']);
 assert.equal(el('profileWorkStatusSave').disabled,true);
 form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();assert.equal(results.length,0);
 select.value='offline';select.dispatchEvent(new w.Event('change'));assert.equal(el('profileWorkStatusSave').disabled,false);
 form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();
 assert.deepEqual(results,[{expected_tenant_id:A,expected_user_id:41,status:'offline'}]);
 assert.equal(el('profileWorkStatusFeedback').textContent,'Status saxlanıldı.');
 w.document.dispatchEvent(new w.CustomEvent('tenant-view',{detail:{view:'tasks'}}));assert.equal(form.hidden,true);
 w.document.dispatchEvent(new w.CustomEvent('tenant-view',{detail:{view:'profile'}}));await tick();assert.equal(select.value,'offline');
 select.value='away';select.dispatchEvent(new w.Event('change'));form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();
 w.document.dispatchEvent(new w.CustomEvent('tenant-auth-invalid'));release();await tick();
 assert.equal(form.hidden,true);assert.equal(el('profileWorkStatusFeedback').textContent,'','stale write cannot surface a success in a new session');
 assert.equal(results.length,2);w.close();

 for(const [kind,profile] of Object.entries({owner:fixture.owner,manager:fixture.manager})){
   const m=init(profile,'employees'),calls=[];
   m.fetch=async url=>{calls.push(new URL(url,m.location.origin));return response({success:true,members:memberRows});};
   m.eval(statusAsset);m.eval(rosterAsset);await tick();
   assert.ok(m.document.querySelector('#staffContent .staff-person'));
   const badges=[...m.document.querySelectorAll('#staffContent [data-work-status]')];
   assert.deepEqual(badges.map(n=>n.textContent),kind==='owner'?['Oflayn']:[]);
   const request=calls.find(u=>u.pathname==='/api/platform/members');
   if(kind==='owner')assert.equal(request.searchParams.get('include'),'work-status');
   else assert.equal(request.searchParams.has('include'),false);
   m.close();
 }
 console.log('PASS: shared self profile, AZ labels, stale-session hide, and owner-only roster.');
}
async function browserTest(){
 const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||undefined,headless:true});
 const html=page.replace(/<script\b[^>]*>[^]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
 try{
  for(const width of [360,1440])for(const view of ['profile','employees']){
   const tab=await browser.newPage({viewport:{width,height:850}});
   let stored='online',posted=0;
   await tab.route('https://profile.test/**',async route=>{
    const url=new URL(route.request().url());
    if(url.pathname==='/app')return route.fulfill({contentType:'text/html',body:html+'<style>'+read('tenant-members-ui.css')+'</style>'});
    if(url.pathname==='/api/platform/profile/availability'){
     if(route.request().method()==='POST'){posted++;stored=route.request().postDataJSON().status;}
     return route.fulfill({json:snapshot(stored)});
    }
    if(url.pathname==='/api/platform/members')return route.fulfill({json:{success:true,members:memberRows}});
    return route.abort();
   });
   await tab.route('**/*',async route=>{if(new URL(route.request().url()).hostname!=='profile.test')return route.abort();return route.fallback();});
   await tab.goto('https://profile.test/app?view='+view);
   await tab.evaluate(({profile,view})=>{window.tenantProfileSnapshot=profile;window.tenantI18n={language:'az'};document.querySelector('#view-'+view).classList.add('active');},{profile:view==='profile'?fixture.worker:fixture.owner,view});
   await tab.addScriptTag({content:statusAsset});
   if(view==='profile'){
    const picker=tab.locator('#profileWorkStatusSelect');
    await picker.waitFor({state:'visible'});
    const box=await picker.boundingBox();assert.ok(box&&box.x>=0&&box.x+box.width<=width+1);
    assert.equal(await picker.inputValue(),'online');assert.equal(posted,0);
    await picker.selectOption('offline');await tab.locator('#profileWorkStatusSave').click();
    await tab.getByText('Status saxlanıldı.',{exact:true}).waitFor();
    assert.equal(stored,'offline');assert.equal(posted,1);
   }else{
    await tab.addScriptTag({content:rosterAsset});
    const badge=tab.locator('#staffContent [data-work-status=offline]');
    await badge.waitFor({state:'visible'});assert.equal(await badge.textContent(),'Oflayn');
    const box=await badge.boundingBox();assert.ok(box&&box.x>=0&&box.x+box.width<=width+1);
    assert.equal(await tab.locator('[data-work-status]').count(),1);assert.equal(posted,0);
   }
   await tab.close();
  }
 }finally{await browser.close();}
 console.log('PASS: 360/1440 Chromium layout and manual selection, synthetic network only.');
}
(process.argv.includes('--browser')?browserTest:domTest)().catch(error=>{console.error(error);process.exitCode=1;});
