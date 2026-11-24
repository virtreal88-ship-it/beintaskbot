const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const read=f=>fs.readFileSync(path.join(__dirname,'..','docs',f),'utf8');
const fakeMenu='<button class="app-avatar-button" type="button" aria-controls="appUserMenu">SE</button><div id="appUserMenu"><div><span id="appUserName">Synthetic Employee</span></div><button data-app-open="profile">Profile</button><button data-app-logout>Logout</button></div>';
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
 const {JSDOM,VirtualConsole}=require(process.env.JSDOM_PATH||'../.support-test-deps/node_modules/jsdom');
 const init=(profile,view)=>{const w=new JSDOM(page.replace('</body>',fakeMenu+'</body>'),{url:'https://profile.test/app?view='+view,runScripts:'outside-only'}).window;
   w.tenantProfileSnapshot=profile;w.tenantI18n={language:'az'};w.document.querySelector('#view-'+view).classList.add('active');return w;};
 const w=init(fixture.worker,'tasks'),results=[];let stored=null,release;
 w.fetch=async(url,options={})=>{
   const u=new URL(url,w.location.origin);assert.equal(u.pathname,'/api/platform/ai/profile/availability');
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
 const control=el('appWorkStatus'),trigger=el('appWorkStatusTrigger'),options=el('appWorkStatusOptions');
 const avatar=w.document.querySelector('.app-avatar-button');
 assert.equal(control.hidden,false,'worker without Dialogs can use the global status control');
 assert.equal(avatar.dataset.workStatus,'online');
 assert.match(trigger.textContent,/İş statusuOnlayn/);
 assert.equal(options.hidden,true,'options do not clutter the closed menu');
 trigger.click();
 assert.equal(trigger.getAttribute('aria-expanded'),'true');
 assert.deepEqual([...options.querySelectorAll('button')].map(o=>o.textContent),['Onlayn','Kənarda','Nahar','Oflayn']);
 options.querySelector('[data-status=online]').click();await tick();assert.equal(results.length,0);
 trigger.click();options.querySelector('[data-status=offline]').click();await tick();
 assert.deepEqual(results,[{expected_tenant_id:A,expected_user_id:41,status:'offline'}]);
 assert.equal(avatar.dataset.workStatus,'offline');
 assert.equal(el('appWorkStatusFeedback').textContent,'Status saxlanıldı.');
 assert.equal(options.hidden,true);
 w.document.dispatchEvent(new w.CustomEvent('tenant-view',{detail:{view:'profile'}}));
 assert.equal(avatar.dataset.workStatus,'offline','status is global, not view-specific');
 trigger.click();options.querySelector('[data-status=away]').click();await tick();
 w.document.dispatchEvent(new w.CustomEvent('tenant-auth-invalid'));release();await tick();
 assert.equal(control.hidden,true);assert.equal(avatar.dataset.workStatus,undefined);
 assert.equal(el('appWorkStatusFeedback').textContent,'','stale write cannot surface a success in a new session');
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
 const virtualConsole=new VirtualConsole(),shellErrors=[];virtualConsole.on('jsdomError',error=>shellErrors.push(error.message));
 const actual=new JSDOM(page,{url:'https://profile.test/app?view=tasks',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole}).window;
 actual.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
 actual.tenantViewDemand={watch(){}};
 actual.tenantProfileSnapshot=fixture.worker;
 actual.fetch=async()=>response(snapshot('online'));
 actual.eval(statusAsset);
 actual.eval(read('tenant-chakra-ui.js'));
 await tick();
 const accountMenu=actual.document.querySelector('#appUserMenu'),accountAvatar=actual.document.querySelector('.app-avatar-button');
 assert.equal(accountAvatar.dataset.workStatus,'online','status mounted after the real shell generated the avatar');
 accountAvatar.click();
 assert.equal(accountMenu.hidden,false);
 const actions=actual.document.querySelector('.app-topbar-actions');
 const accountStatus=actions.querySelector('#appWorkStatusTrigger'),accountOptions=actions.querySelector('#appWorkStatusOptions');
 assert.equal(accountMenu.querySelector('#appWorkStatus'),null);
 accountStatus.click();assert.equal(accountOptions.hidden,false);
 assert.equal(accountMenu.hidden,true,'opening desktop status closes the avatar menu');
 accountOptions.dispatchEvent(new actual.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
 assert.equal(accountOptions.hidden,true);assert.equal(actual.document.activeElement,accountStatus);
 assert.equal(accountMenu.querySelectorAll('[data-app-logout]').length,1);
 assert.deepEqual(shellErrors,[]);
 actual.close();
 console.log('PASS: desktop header placement, AZ labels, stale-session hide, and owner-only roster.');
}
async function browserTest(){
 const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||undefined,headless:true});
 const html=page.replace(/<script\b[^>]*>[^]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
 try{
  for(const width of [360,1440])for(const view of ['profile','employees']){
   const tab=await browser.newPage({viewport:{width,height:850}});
   const shellErrors=[];tab.on('pageerror',error=>shellErrors.push(error.message));
   let stored='online',posted=0;
   await tab.route('https://profile.test/**',async route=>{
    const url=new URL(route.request().url());
    if(url.pathname==='/app')return route.fulfill({contentType:'text/html',body:html+'<style>'+read('tenant-members-ui.css')+'</style>'});
    if(url.pathname==='/api/platform/ai/profile/availability'){
     if(route.request().method()==='POST'){posted++;stored=route.request().postDataJSON().status;}
     return route.fulfill({json:snapshot(stored)});
    }
    if(url.pathname==='/api/platform/members')return route.fulfill({json:{success:true,members:memberRows}});
    if(url.pathname.startsWith('/api/platform/notifications'))return route.fulfill({json:{success:true,result:{items:[],unread_count:0}}});
    return route.abort();
   });
   await tab.route('**/*',async route=>{if(new URL(route.request().url()).hostname!=='profile.test')return route.abort();return route.fallback();});
   await tab.goto('https://profile.test/app?view='+view);
   await tab.evaluate(({profile,view})=>{window.tenantProfileSnapshot=profile;window.tenantI18n={language:'az',t:key=>key};document.querySelector('#view-'+view).classList.add('active');},{profile:view==='profile'?fixture.worker:fixture.owner,view});
   await tab.evaluate(()=>{window.tenantViewDemand={watch(){}};});
   await tab.addScriptTag({content:read('tenant-chakra-ui.js')});
   await tab.addScriptTag({content:statusAsset});
   assert.deepEqual(shellErrors,[]);
   if(view==='profile'){
    const avatar=tab.locator('.app-avatar-button');
    await avatar.waitFor({state:'visible'});
    await tab.waitForFunction(()=>document.querySelector('.app-avatar-button').dataset.workStatus==='online');
    await tab.addScriptTag({content:read('tenant-dialog-consultations.js')});
    const mobile=width<1024;
    const trigger=tab.locator('#appWorkStatusTrigger'),options=tab.locator('#appWorkStatusOptions');
    assert.equal(await trigger.isVisible(),!mobile,'status is immediately available on desktop');
    assert.equal(await avatar.evaluate(node=>getComputedStyle(node,'::after').content!=='none'),mobile,'avatar retains the status dot on phones');
    if(mobile){
     await avatar.click();
     await tab.locator('#appUserMenu #dialogConsultationBell').waitFor();
     assert.equal(await tab.locator('#appUserMenu #appWorkStatusTrigger').count(),1);
    }else{
     await tab.locator('.app-topbar-actions #appWorkStatus + #dialogConsultationBell').waitFor();
    }
    assert.equal(await options.isVisible(),false);
    const box=await trigger.boundingBox();assert.ok(box&&box.x>=0&&box.x+box.width<=width+1);
    assert.equal(posted,0);await trigger.click();
    assert.equal(await options.isVisible(),true);
    const popup=await options.boundingBox();assert.ok(popup&&popup.x>=0&&popup.x+popup.width<=width+1);
    await tab.keyboard.press('Escape');assert.equal(await options.isVisible(),false);
    assert.equal(await trigger.isVisible(),true,'Escape closes only the nested picker');
    await trigger.click();
    await options.locator('[data-status=offline]').last().click();
    await tab.waitForFunction(()=>document.getElementById('appWorkStatusFeedback').textContent==='Status saxlanıldı.');
    assert.equal(stored,'offline');assert.equal(posted,1);
    assert.equal(await avatar.getAttribute('data-work-status'),'offline');
    assert.equal(await tab.locator('#appUserMenu [data-app-open=profile]').isVisible(),mobile);
    assert.equal(await tab.locator('#appUserMenu [data-app-logout]').count(),1);
    if(!mobile){
     await tab.setViewportSize({width:390,height:850});
     await tab.locator('#appUserMenu #appWorkStatusTrigger').waitFor({state:'attached'});
     assert.equal(await trigger.isVisible(),false);
     await avatar.click();
     assert.equal(await trigger.isVisible(),true);
     await tab.setViewportSize({width,height:850});
     await tab.locator('.app-topbar-actions #appWorkStatus + #dialogConsultationBell').waitFor();
     assert.match(await trigger.getAttribute('aria-label'),/Oflayn/);
     assert.equal(posted,1,'responsive placement does not save again');
    }
   }else{
    await tab.evaluate(()=>delete window.tenantViewDemand);
    await tab.addScriptTag({content:rosterAsset});
    const badge=tab.locator('#staffContent [data-work-status=offline]');
    await badge.waitFor({state:'visible'});assert.equal(await badge.textContent(),'Oflayn');
    const box=await badge.boundingBox();assert.ok(box&&box.x>=0&&box.x+box.width<=width+1);
    assert.equal(await tab.locator('#staffContent [data-work-status]').count(),1);assert.equal(posted,0);
   }
   assert.deepEqual(shellErrors,[]);await tab.close();
  }
 }finally{await browser.close();}
 console.log('PASS: 360/1440 Chromium layout and manual selection, synthetic network only.');
}
(process.argv.includes('--browser')?browserTest:domTest)().catch(error=>{console.error(error);process.exitCode=1;});
