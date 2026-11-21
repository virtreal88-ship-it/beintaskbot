/* Real app assets and browser, synthetic identity and dialog APIs. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'../.support-test-deps/node_modules/playwright');
const docs=path.resolve(__dirname,'../docs');
const tenant='00000000-0000-4000-8000-000000000101',channel='00000000-0000-4000-8000-000000000201';
const profile={success:true,member:{tenant_id:tenant,telegram_id:33,role:'owner',active:true,permissions:['dialogs']},
  capabilities:{modules:{tasks:true,dialogs:true},dialog_reply_templates:true,dialog_company_binding:true}};
const dialog={provider:'connect',lead_id:41,channel_id:channel,conversation_id:'994100000777@s.whatsapp.net',
  customer_kind:'contact',customer_name:'Synthetic customer',channel_name:'WhatsApp',state:'human',
  can_send:true,available_actions:[],last_message_id:'initial',last_message_direction:'outgoing'};
const message={id:'initial',direction:'outgoing',body:'Synthetic history',message_type:'text'};
const template={id:'template',name:'Example',body:'Synthetic template',visibility:'personal',version:1,can_edit:true};
function gate(){
  let signal,release;
  const waiting=new Promise(resolve=>signal=resolve),held=new Promise(resolve=>release=resolve);
  return {waiting,release,hold:async()=>{signal();return held;}};
}
(async()=>{
  const browser=await chromium.launch({args:['--no-sandbox']});
  try{
    const page=await browser.newPage({viewport:{width:1280,height:840}});
    let meGate,companyGate,templatesGate,featuresDenied=false,dialogDenied=false,sessionDenied=false,profileReads=0;
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/*',async route=>{
      const name=new URL(route.request().url()).pathname;
      const fulfill=(data,status=200)=>route.fulfill({status,json:data});
      if(name==='/app')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(docs,'tenant-app.html'),'utf8')});
      if(name.startsWith('/assets/')){
        const file=path.join(docs,path.basename(name));
        return fs.existsSync(file)?route.fulfill({path:file}):route.fulfill({status:404,body:''});
      }
      if(name==='/api/platform/me'){
        profileReads++;
        if(meGate)await meGate.hold();
        return sessionDenied?fulfill({success:false},401):fulfill(profile);
      }
      if(name.endsWith('/dialog-company')){
        if(companyGate)await companyGate.hold();
        return featuresDenied?fulfill({success:false},403):fulfill({success:true,binding:{company_id:'company',company_name:'Example CRM',can_manage:true}});
      }
      if(name.endsWith('/reply-templates')){
        if(templatesGate)await templatesGate.hold();
        return featuresDenied?fulfill({success:false},403):fulfill({success:true,result:{available:true,templates:[template],allowed_visibility:['personal']}});
      }
      if(name.endsWith('/dialogs/history'))return dialogDenied?fulfill({success:false},404):fulfill({success:true,result:{dialog,messages:[message],next_before:null}});
      if(name.endsWith('/dialogs'))return fulfill({success:true,result:{dialogs:[dialog],channels:[],agents:[],next_offset:null}});
      return fulfill({success:true,result:{}});
    });
    await page.goto('https://crm.test/app?view=dialogs');
    await page.locator('.operator-row').first().click();
    const company=page.locator('[data-operator-dialog-company]>button');
    const templates=page.locator('#operatorReplyTemplatesOpen');
    const input=page.locator('#operatorConversationContent #chatText');
    await company.waitFor();await templates.waitFor();await input.fill('Unsaved reply');
    await page.evaluate(()=>{
      window.initialNodes=['#operatorDialogs','#chatRows','#chatText','[data-operator-dialog-company]>button','#operatorReplyTemplatesOpen']
        .map(selector=>document.querySelector(selector));
      window.authPauses=0;document.addEventListener('tenant-auth-pending',()=>window.authPauses++);
    });
    const tab=hidden=>page.evaluate(hidden=>{
      Object.defineProperty(document,'hidden',{value:hidden,configurable:true});
      document.dispatchEvent(new Event('visibilitychange'));
      if(!hidden)window.dispatchEvent(new Event('focus'));
    },hidden);
    const mounted=()=>page.evaluate(()=>window.initialNodes.every(node=>node.isConnected)&&
      getComputedStyle(document.querySelector('#operatorDialogs')).visibility==='visible'&&
      !document.querySelector('[data-operator-dialog-company]>button')?.hidden&&
      !document.querySelector('#operatorReplyTemplatesOpen')?.hidden);
    meGate=gate();companyGate=gate();templatesGate=gate();
    const previousProfileReads=profileReads;
    await tab(true);
    assert(await mounted(),'moving to another browser tab does not unmount or conceal the chat');
    await tab(false);await meGate.waiting;
    assert(await mounted(),'the chat stays visible while /me is pending');
    assert.equal(profileReads,previousProfileReads+1,'visibility and focus share one fresh profile request');
    assert.equal(await page.evaluate(()=>window.authPauses),0);
    meGate.release();await Promise.all([companyGate.waiting,templatesGate.waiting]);
    assert(await mounted(),'the confirmed controls remain visible while their data is refreshed');
    companyGate.release();templatesGate.release();meGate=companyGate=templatesGate=null;
    await page.waitForFunction(()=>document.getElementById('operatorConversationContent').getAttribute('aria-busy')!=='true');
    assert(await mounted());assert.equal(await input.inputValue(),'Unsaved reply');
    assert.equal(await input.evaluate(el=>el===document.activeElement),true,'focus and draft stay in the original input');
    // App navigation retains the chat body but must restore controls removed by view exit.
    await page.evaluate(()=>window.tenantAppNavigate('tasks',false));
    await page.evaluate(()=>window.tenantAppNavigate('dialogs',false));
    await company.waitFor();await templates.waitFor();
    assert.equal(await input.inputValue(),'Unsaved reply');
    // API-level access can disappear while the profile capability still says yes.
    featuresDenied=true;
    await tab(true);await tab(false);
    await company.waitFor({state:'hidden'});await templates.waitFor({state:'hidden'});
    assert.equal(await input.inputValue(),'Unsaved reply');
    // A route can disappear independently of the identity returned by /me.
    dialogDenied=true;
    await tab(true);await tab(false);
    await input.waitFor({state:'detached'});
    assert.equal(await page.locator('#chatRows').count(),0);
    dialogDenied=false;featuresDenied=false;
    await tab(true);await tab(false);
    await page.locator('.operator-row').first().click();
    await input.waitFor();await company.waitFor();await templates.waitFor();
    // A revoked browser session clears the actual chat because old data is no longer authorized.
    sessionDenied=true;
    await tab(true);await tab(false);
    await input.waitFor({state:'detached'});
    assert.equal(await page.locator('#chatRows').count(),0);
    assert.equal(await page.evaluate(()=>window.tenantProfileSnapshot),null);
    assert.deepEqual(errors,[]);
    console.log('PASS: tab return keeps the dialog and controls mounted; rechecks permissions; clears denied content.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
