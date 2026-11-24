/* Real renderer and Accept control, without contacting a provider or customer. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'../.support-test-deps/node_modules/playwright');
const docs=path.resolve(__dirname,'../docs');
const html=fs.readFileSync(path.join(docs,'tenant-app.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:850}});
    await page.route('**/*',route=>route.request().isNavigationRequest()
      ?route.fulfill({contentType:'text/html',body:html}):route.abort());
    await page.goto('https://example.test/app?view=dialogs');
    await page.evaluate(()=>{
      document.querySelectorAll('.view').forEach(n=>n.classList.toggle('active',n.id==='view-dialogs'));
      window.tenantProfileSnapshot={member:{tenant_id:'t',telegram_id:17,role:'operator',active:true},capabilities:{modules:{dialogs:true}}};
      const waiting={state:'waiting',revision:0,can_accept:true,has_inbound:false};
      const accepted={state:'accepted',revision:1,id:'episode',operator_id:17,can_close:true};
      const route={provider:'connect',lead_id:41,channel_id:'exact-source',conversation_id:'call-only',
        customer_name:'Synthetic caller',can_send:false,lifecycle_enabled:true,last_message_direction:'system',
        last_message_type:'call_missed',last_message_id:'missed-call',last_message_at:'2026-11-18T10:00:00Z'};
      window.testRead=false;window.testAcceptance=null;
      let state=waiting;
      window.fetch=async(target,opt={})=>{
        const api=new URL(target,location.href),body=opt.body?JSON.parse(opt.body):null;
        const ok=result=>({ok:true,status:200,json:async()=>({success:true,result})});
        if(api.pathname.endsWith('/dialogs/history'))return ok({dialog:{...route,lifecycle:state},messages:[{
          id:'missed-call',external_id:'missed-call',source:'connect',source_channel_id:route.channel_id,source_lead_id:41,
          channel:'connect:'+route.channel_id,direction:'system',message_type:'call_missed',body:'',happened_at:route.last_message_at}],
          read_through_message_id:'missed-call'});
        if(api.pathname.endsWith('/dialogs/read')){
          window.testRead=body.message_id==='missed-call'&&body.channel_id===route.channel_id;
          return ok({updated:true});
        }
        if(api.pathname.endsWith('/dialogs_lifecycle')){
          window.testAcceptance=body;
          await new Promise(resolve=>{window.finishAccept=resolve;});
          state=accepted;return ok({lifecycle:accepted});
        }
        if(api.pathname.endsWith('/dialogs'))return ok({dialogs:[{...route,lifecycle:state,
          missed_call_pending:state===waiting,unread_count:window.testRead?0:1}],channels:[],agents:[],total_count:1});
        if(body)throw Error('Unexpected synthetic write: '+api.pathname);
        return ok({});
      };
    });
    for(const file of ['tenant-operator-i18n.js','tenant-operator-dialogs.js'])
      await page.addScriptTag({path:path.join(docs,file)});
    await page.evaluate(async()=>{window.tenantI18n.setLanguage('en');await window.tenantOperator.refresh();});
    const row=page.getByRole('option').filter({hasText:'Synthetic caller'});
    const red=row.locator('.operator-row-preview-missed-pending');
    await red.waitFor({state:'visible'});
    const before=await red.evaluate(n=>getComputedStyle(n).color);
    await row.click();
    await page.waitForFunction(()=>window.testRead);
    await row.locator('.operator-unread-count').waitFor({state:'detached'});
    assert.equal(await red.evaluate(n=>getComputedStyle(n).color),before,'plain opening still shows the danger color');
    assert.equal(await page.locator('[data-missed-call]').textContent(),'Missed call');
    assert.equal(await page.locator('[data-operator-lifecycle="dismiss"]').count(),0);
    await page.getByRole('button',{name:'Accept dialogue',exact:true}).click();
    await page.waitForFunction(()=>typeof window.finishAccept==='function');
    assert.equal(await red.evaluate(n=>getComputedStyle(n).color),before,'an in-flight acceptance is not confirmation');
    const action=await page.evaluate(()=>window.testAcceptance);
    assert.equal(action.action,'accept');assert.equal(action.channel_id,'exact-source');
    assert.equal(action.conversation_id,'call-only');assert.equal(action.expected_revision,0);
    await page.evaluate(()=>window.finishAccept());
    await red.waitFor({state:'detached'});
    const preview=row.locator('.operator-row-preview');
    assert.equal(await preview.textContent(),'Missed call');
    assert.notEqual(await preview.evaluate(n=>getComputedStyle(n).color),before);
    console.log('PASS Chromium: missed-call preview stays red after read and changes only after exact successful acceptance');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
