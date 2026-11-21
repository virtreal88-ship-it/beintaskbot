/* Synthetic route/authorization checks for the read-only header company. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'../.support-test-deps/node_modules/playwright');
const docs=path.resolve(__dirname,'../docs');
const html=fs.readFileSync(path.join(docs,'tenant-app.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
const alpha='<b>Company A & Partners</b> '+ 'very long stored name '.repeat(10);
const beta='@verified_account';
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined});
 try{for(const width of [360,1440]){
  const page=await browser.newPage({viewport:{width,height:800}});
  await page.route('**/*',route=>route.request().isNavigationRequest()?route.fulfill({contentType:'text/html',body:html}):route.abort());
  await page.goto('https://example.test/app?view=dialogs');
  await page.evaluate(({alpha,beta})=>{
   document.querySelectorAll('.view').forEach(n=>n.classList.toggle('active',n.id==='view-dialogs'));
   const member={tenant_id:'synthetic',telegram_id:11,role:'owner',active:true};
   const profile={member,capabilities:{modules:{dialogs:true,support:true},dialog_company_binding:true}};
   window.tenantProfileSnapshot=profile;
   const group=(channel_id,lead_id)=>({provider:'connect',channel_id,lead_id,channel_name:'Source '+channel_id,
    conversation_id:'same-synthetic@g.us',customer_name:'Customer Alpha',customer_kind:'group',can_send:false});
   const sources=[group('a',41),group('b',42)];
   const fixtures={a:{company_id:'alpha',company_name:alpha,can_manage:true},
      b:{company_id:'beta',company_name:beta,can_manage:false}};
   window.mode={a:'delayed',b:'masked'};window.releases={};window.bindingGets=[];
   window.fetch=async(target,opt={})=>{
    const u=new URL(target,'https://example.test'),api=u.pathname,q=u.searchParams;
    const ok=result=>({ok:true,json:async()=>({success:true,result})});
    if(api==='/api/platform/me')return {ok:true,json:async()=>profile};
    if(api==='/api/platform/operator/dialog-company'){
     if(opt.method!=='GET')throw Error('The header must never mutate');
     const channel=q.get('channel_id'),route=sources.find(r=>r.channel_id===channel);
     if(!route||q.get('provider')!==route.provider||q.get('lead_id')!==String(route.lead_id)||
       q.get('conversation_id')!==route.conversation_id||q.get('expected_tenant_id')!==member.tenant_id||
       q.get('expected_user_id')!==String(member.telegram_id))throw Error('Inexact company read');
     window.bindingGets.push(channel);
     const mode=window.mode[channel];
     if(mode==='delayed')await new Promise(release=>{window.releases[channel]=release;});
     if(mode==='denied'||mode==='offline')return {ok:false,status:mode==='denied'?403:503,json:async()=>({success:false})};
     const binding=mode==='masked'?{company_id:null,company_name:null,can_manage:false}:fixtures[channel];
     return {ok:true,json:async()=>({success:true,binding})};
    }
    if(api.endsWith('/dialogs/history'))return ok({dialog:sources.find(r=>r.channel_id===q.get('channel_id')),messages:[],next_before:null});
    if(api.endsWith('/dialogs'))return ok({dialogs:[{...sources[0],group_sources:sources}],channels:[],agents:[],next_offset:null});
    if(api.endsWith('/dialogs/group-participants'))return ok({status:'available',phones:[],total:0});
    return ok({});
   };
  },{alpha,beta});
  for(const file of ['tenant-operator-i18n.js','tenant-dialog-company.js','tenant-operator-dialogs.js'])
    await page.addScriptTag({path:path.join(docs,file)});
  await page.evaluate(()=>window.tenantOperator.refresh());
  await page.locator('.operator-row').first().click();
  const name=page.locator('#operatorDialogCompanyName'),head=page.locator('#operatorConversationHeader');
  await page.waitForFunction(()=>Boolean(window.releases.a));
  assert(!await name.isVisible());
  assert.equal(await name.textContent(),'');
  assert.equal(await name.getAttribute('title'),null);
  const height=await head.evaluate(n=>n.offsetHeight);
  await page.evaluate(()=>{window.mode.a='visible';window.releases.a();});
  await name.waitFor();
  assert.equal(await name.textContent(),alpha,'stored name is preserved as literal text');
  assert.equal(await name.locator('b').count(),0);
  assert.equal(await name.getAttribute('title'),alpha);
  assert.equal(await head.evaluate(n=>n.offsetHeight),height,'the tag never adds a header row');
  assert.equal(await name.evaluate(n=>n.previousElementSibling.id),'operatorDialogTitleText');
  assert(await name.evaluate(n=>n.scrollWidth>n.clientWidth&&getComputedStyle(n).textOverflow==='ellipsis'));
  assert.equal(await head.locator('.operator-channel-label').count(),0);
  assert.deepEqual(await page.evaluate(()=>window.bindingGets),['a'],'header and side panel reuse one exact GET');
  assert.equal(await page.locator('#operatorClientCompanyHost [data-company-value]').textContent(),alpha);
  assert.equal(await page.locator('#operatorClientCard').isVisible(),false,'header cannot open the side editor');
  await page.locator('#operatorClientCardOpen').click();
  assert(await page.locator('#operatorClientCard').isVisible());
  await page.locator('#operatorCancelConversationName').click();

  const reauthorize=async(mode,channel='a')=>page.evaluate(({mode,channel})=>{
   window.mode[channel]=mode;
   document.dispatchEvent(new Event('tenant-dialog-reauthorized'));
  },{mode,channel});
  await reauthorize('offline');
  await name.waitFor({state:'hidden'});
  assert.equal(await name.textContent(),'');
  await page.waitForFunction(()=>window.bindingGets.length>=2);
  await reauthorize('visible');
  await name.waitFor();
  await page.evaluate(()=>{delete window.releases.a;});
  await reauthorize('delayed');
  await page.waitForFunction(()=>Boolean(window.releases.a));
  assert(!await name.isVisible(),'revalidation immediately hides the prior result');
  const picker=page.locator('#operatorComposerSourceSelect');
  await picker.selectOption('1');
  await page.waitForFunction(()=>window.bindingGets.at(-1)==='b');
  await page.evaluate(()=>window.releases.a());
  assert(!await name.isVisible()&&await name.textContent()==='','masked source and delayed previous source cannot populate the header');
  await reauthorize('visible','b');
  await name.waitFor();
  assert.equal(await name.textContent(),beta,'visible company does not require edit permission');
  await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-view',{detail:{view:'tasks'}})));
  assert(!await name.isVisible()&&!(await name.textContent()),'leaving Dialogs clears company name');
  await page.evaluate(()=>{window.mode.b='denied';document.dispatchEvent(new CustomEvent('tenant-view',{detail:{view:'dialogs'}}));});
  await page.waitForFunction(()=>window.bindingGets.filter(c=>c==='b').length>=3);
  assert(!await name.isVisible());
  await reauthorize('visible','b');
  await name.waitFor();
  await page.evaluate(()=>document.dispatchEvent(new Event('tenant-auth-pending')));
  assert(!await name.isVisible()&&!(await name.textContent()),'identity transition clears company name');
  await page.close();
 }
 console.log('PASS Chromium company header authorization, no extra GETs, compact geometry, 360/1440px');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
