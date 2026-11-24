/* Owner creation in the real CRM renderer; every API boundary is synthetic. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'../.support-test-deps/node_modules/playwright');
const docs=path.resolve(__dirname,'../docs');
const html=fs.readFileSync(path.join(docs,'tenant-app.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined});
  try{
    for(const width of [360,1440]){
      const mobile=width===360;
      const page=await browser.newPage({viewport:{width,height:800},hasTouch:mobile});
      try{
        await page.route('**/*',request=>request.request().isNavigationRequest()
          ?request.fulfill({contentType:'text/html',body:html}):request.abort());
        await page.goto('https://example.test/app?view=dialogs');
        await page.evaluate(()=>{
          document.querySelectorAll('.view').forEach(n=>n.classList.toggle('active',n.id==='view-dialogs'));
          const member={tenant_id:'synthetic-test-only',telegram_id:11,role:'owner',active:true};
          const profile={member,capabilities:{modules:{dialogs:true},dialog_company_binding:true,
            shared_dialog_reminder_creation:true}};
          const closed={provider:'connect',lead_id:41,channel_id:'exact-source',conversation_id:'synthetic',
            customer_name:'Synthetic customer',can_send:false,archive_read_only:true,lifecycle_enabled:true,
            lifecycle:{state:'closed',revision:2},shared_reminder:{can_schedule:true},company_binding:{company_id:null}};
          window.tenantProfileSnapshot=profile;window.reminderRequests=[];window.testDialog=closed;
          window.fetch=async(target,opt={})=>{
            const u=new URL(target,location.href),api=u.pathname,body=opt.body?JSON.parse(opt.body):null;
            const ok=result=>({ok:true,status:200,json:async()=>({success:true,result})});
            if(api.endsWith('/dialogs/shared-reminders')){window.reminderRequests.push({method:opt.method,body});return ok({id:body?.id});}
            if(api.endsWith('/dialogs/company-panel/preferences'))return ok({open:true});
            if(api.endsWith('/operator/dialog-company'))return {ok:true,json:async()=>({success:true,binding:{company_id:null}})};
            if(api.endsWith('/dialogs/history'))return ok({dialog:closed,messages:[]});
            if(api.endsWith('/dialogs'))return ok({dialogs:[closed],channels:[],agents:[]});
            return ok({});
          };
        });
        for(const file of ['tenant-operator-i18n.js','tenant-operator-dialogs.js','tenant-dialog-company.js','tenant-shared-dialog-reminders.js'])
          await page.addScriptTag({path:path.join(docs,file)});
        await page.evaluate(async()=>{window.tenantI18n.setLanguage('en');await window.tenantOperator.refresh();
          if(!await window.tenantOperator.openDialog(window.testDialog))throw Error('Synthetic dialog did not open');});
        const panel=page.locator('#operatorCompanyPanel'),create=panel.locator('#operatorSharedReminderCreate');
        if(!await panel.isVisible()){
          const toggle=page.locator('#operatorCompanyPanelOpen');
          if(mobile)await toggle.tap();else await toggle.click();
        }
        await create.waitFor({state:'visible',timeout:10000});
        assert.equal(await create.textContent(),'Create reminder');
        assert(await create.evaluate(button=>button.parentElement===document.querySelector('#operatorCompanyView-main').lastElementChild));
        assert.equal(await page.locator('#operatorClientCard #operatorSharedReminderCreate').count(),0);
        assert.deepEqual(await page.evaluate(()=>window.reminderRequests),[],'creation-only does not call the bell feed');
        if(mobile)await create.tap();else await create.click();
        const modal=page.getByRole('dialog',{name:'Create reminder',exact:true});
        await modal.waitFor({state:'visible'});
        assert.deepEqual(await page.evaluate(()=>window.reminderRequests),[],'opening does not submit');
        if(mobile){
          assert(await modal.getByLabel('Date and time').isEditable());
          await modal.getByRole('button',{name:'Close',exact:true}).tap();
          await modal.waitFor({state:'hidden'});
        }else{
          await modal.getByLabel('Date and time').fill('2099-11-24T12:00');
          await modal.getByLabel('Shared note').fill('Synthetic note');
          assert.deepEqual(await page.evaluate(()=>window.reminderRequests),[]);
          await modal.getByRole('button',{name:'Schedule',exact:true}).click();
          await modal.getByText('Reminder scheduled',{exact:true}).waitFor();
          const requests=await page.evaluate(()=>window.reminderRequests);
          assert.equal(requests.length,1);
          const {method,body}=requests[0];
          assert.equal(method,'POST');assert.equal(body.action,'schedule');
          assert.equal(body.expected_user_id,'11');assert.equal(body.expected_tenant_id,'synthetic-test-only');
          assert.equal(body.channel_id,'exact-source');assert.equal(body.conversation_id,'synthetic');assert.equal(body.lead_id,41);
          assert.equal(body.note,'Synthetic note');
        }
      }finally{await page.close();}
    }
  }finally{await browser.close();}
  console.log('PASS owner closed-archive Company reminder at 360/1440, no bell, synthetic schedule');
})().catch(error=>{console.error(error);process.exitCode=1;});
