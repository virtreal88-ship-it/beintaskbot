/* Production app shell and UI scripts in Chromium, with synthetic isolated API responses. */
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const {docs,assets,appHtml,installNotificationCaseFixture}=require('./notification_case_targets_fixture.cjs');
const id='01a1b1af-4d2c-71f2-aaeb-f01eccac0031';
function installRiskFixture(id){
 const f=window.notificationCaseFixture,base=window.fetch;
 f.targets=[{id:'10000000-0000-4000-8000-000000000401',event_key:'proactive_risk:'+id,target_state:'legacy',kind:'system',title:'Synthetic risk',body:'Review source',target_url:'/app?view=linear&risk_alert_id='+id,created_at:'2026-11-02T12:00:00Z',read_at:null}];
 f.riskStatus=200;f.riskCalls=0;f.holdRisk=false;f.pendingRisk=[];
 window.fetch=async(input,options={})=>{
  const url=new URL(String(input),location.href);
  if(url.pathname==='/api/platform/risks/alerts/'+id){
   f.riskCalls++;const status=f.riskStatus;
   const result=()=>new Response(JSON.stringify(status===200?{success:true,alert:{source_kind:'linear',source_url:'https://linear.app/acme/issue/BS-1513',context:{identifier:'BS-1513',title:'Verified source title',excerpt:'Synthetic source text',source_mode:'live'}}}:{success:false}),{status,headers:{'Content-Type':'application/json'}});
   if(f.holdRisk)return new Promise(resolve=>f.pendingRisk.push(()=>resolve(result())));return result();
  }
  return base(input,options);
 };
}
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':undefined),args:['--no-sandbox']});
 const server=http.createServer((req,res)=>{const u=new URL(req.url,'http://test');if(u.pathname.startsWith('/assets/')){const name=path.basename(u.pathname);if(![...assets,'tenant-risk-alert-detail.js','tenant-risk-alert-detail.css'].includes(name)){res.writeHead(404);return res.end()}res.writeHead(200,{'content-type':name.endsWith('.css')?'text/css':'text/javascript'});return res.end(fs.readFileSync(path.join(docs,name)))}res.writeHead(200,{'content-type':'text/html'});res.end(appHtml().replace('</body>','<script src="/assets/tenant-risk-alert-detail.js"></script></body>'));});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin='http://127.0.0.1:'+server.address().port;
 async function mount(width,view){const context=await browser.newContext({viewport:{width,height:844}}),page=await context.newPage(),errors=[];await context.addInitScript(installNotificationCaseFixture);await context.addInitScript(installRiskFixture,id);page.on('pageerror',e=>errors.push(e.message));await page.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());await page.goto(origin+'/app?view='+view+'&lang=en');await page.locator('#dialogConsultationBell').waitFor();return{context,page,errors}}
 async function open(page){await page.locator('#dialogConsultationBell').click();await page.locator('[data-notification-id] [data-notification-action=open]').click()}
 try{
  for(const width of [390,1440])for(const view of ['dialogs','support']){
   const b=await mount(width,view);try{const {page}=b,original=page.url();let navigations=0;page.on('framenavigated',frame=>{if(frame===page.mainFrame())navigations++});
    await open(page);await page.locator('.tenant-risk-alert-dialog h2').filter({hasText:'Verified source title'}).waitFor();assert.equal(page.url(),original);assert.equal(navigations,0);assert.equal(await page.locator('#view-'+view).evaluate(e=>e.classList.contains('active')),true);assert.equal(await page.locator('.risk-alert-source-link').getAttribute('href'),'https://linear.app/acme/issue/BS-1513');assert.equal(await page.locator('.risk-alert-source-link').getAttribute('target'),'_blank');
    const box=await page.locator('.tenant-risk-alert-dialog').boundingBox();assert(box.x>=0&&box.x+box.width<=width+1,'dialog fits viewport '+width);
    await page.locator('.risk-alert-close').click();assert.equal(page.url(),original);assert.equal(await page.locator('.tenant-risk-alert-dialog').count(),0);
    await open(page);await page.locator('.tenant-risk-alert-dialog h2').filter({hasText:'Verified source title'}).waitFor();assert.equal(await page.evaluate(()=>notificationCaseFixture.riskCalls),2,'each open revalidates');await page.keyboard.press('Escape');assert.equal(page.url(),original);assert.equal(await page.locator('.tenant-risk-alert-dialog').count(),0);assert.equal(await page.evaluate(()=>document.activeElement?.id),'dialogConsultationBell','closing returns keyboard focus to the bell');
    assert.equal(await page.evaluate(()=>notificationCaseFixture.calls.filter(c=>c.method!=='GET').length),0,'no sends, writes or AI calls');assert.deepEqual(b.errors,[]);console.log('PASS actual app risk bell '+view+' '+width);
   }finally{await b.context.close()}
  }
  for(const status of [404,503]){const b=await mount(390,'dialogs');try{const p=b.page,url=p.url();await p.evaluate(s=>notificationCaseFixture.riskStatus=s,status);await open(p);await p.locator('.tenant-risk-alert-dialog [role=status]').filter({hasText:status===404?'no longer available':'Could not check'}).waitFor();assert.equal(p.url(),url);assert.equal(await p.locator('.risk-alert-source-link').count(),0);assert.equal(await p.locator('.risk-alert-quote').count(),0);assert.deepEqual(b.errors,[]);}finally{await b.context.close()}}
  {const b=await mount(390,'dialogs');try{const p=b.page;await p.evaluate(()=>{history.pushState({},'',location.pathname+'?view=dialogs&lang=en&test=one');notificationCaseFixture.holdRisk=true});await open(p);await p.waitForFunction(()=>notificationCaseFixture.pendingRisk.length===1);await p.goBack();await p.waitForFunction(()=>!document.querySelector('.tenant-risk-alert-dialog'));await p.evaluate(()=>notificationCaseFixture.pendingRisk.shift()());await p.waitForTimeout(100);assert.equal(await p.locator('.tenant-risk-alert-dialog').count(),0);assert(!await p.locator('body').innerText().then(t=>t.includes('Verified source title')));assert.deepEqual(b.errors,[]);}finally{await b.context.close()}}
  {const b=await mount(390,'dialogs');try{const p=b.page;await p.evaluate(()=>{notificationCaseFixture.profile.member.role='member';document.dispatchEvent(new CustomEvent('tenant-profile',{detail:notificationCaseFixture.profile}))});await open(p);await p.locator('#notificationActionError').waitFor();assert.equal(await p.evaluate(()=>notificationCaseFixture.riskCalls),0);assert.equal(await p.locator('.tenant-risk-alert-dialog').count(),0);}finally{await b.context.close()}}
  {const b=await mount(390,'linear');try{const p=b.page;await p.goto(origin+'/app?view=linear&lang=en&risk_alert_id='+id);await p.locator('.tenant-risk-alert-dialog h2').filter({hasText:'Verified source title'}).waitFor();await p.locator('.risk-alert-close').click();assert.equal(new URL(p.url()).searchParams.has('risk_alert_id'),false);assert.equal(new URL(p.url()).searchParams.get('view'),'linear');}finally{await b.context.close()}}
  console.log('PASS failures, Back/stale request, non-owner and deep link');
 }finally{await new Promise(resolve=>server.close(resolve));await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
