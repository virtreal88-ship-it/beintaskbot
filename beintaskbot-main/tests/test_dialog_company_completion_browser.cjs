/* Real completion modal, with no external requests or company/store writes. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'../.support-test-deps/node_modules/playwright');
const docs=path.resolve(__dirname,'../docs');
const html=fs.readFileSync(path.join(docs,'tenant-app.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
const companyId='11111111-1111-4111-8111-111111111111';
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined});
  try{
    for(const width of [360,1440]){
      const mobile=width===360,page=await browser.newPage({viewport:{width,height:800},hasTouch:mobile});
      try{
        await page.route('**/*',route=>route.request().isNavigationRequest()
          ?route.fulfill({contentType:'text/html',body:html}):route.abort());
        await page.goto('https://example.test/app?view=dialogs');
        await page.addStyleTag({path:path.resolve(docs,'../ui/src/app-theme.css')});
        await page.evaluate(companyId=>{
          document.querySelectorAll('.view').forEach(n=>n.classList.toggle('active',n.id==='view-dialogs'));
          const profile={member:{tenant_id:'synthetic-tenant',telegram_id:33,role:'operator',active:true},capabilities:{modules:{dialogs:true}}};
          window.tenantProfileSnapshot=profile;window.closeWrites=[];window.historyReads=0;window.companySearchReads=0;window.atClose=[];
          let route={provider:'connect',lead_id:41,channel_id:'synthetic-source',conversation_id:'synthetic-contact',
            customer_name:'Synthetic client',can_send:true,lifecycle_enabled:true,available_actions:[],
            lifecycle:{id:'episode',state:'accepted',revision:1,operator_id:33,can_close:true,
              company_completion:{status:'missing',can_choose:true,can_skip:true}}};
          window.routeForTest=route;
          window.setScopedClose=(revision,binding,proof={})=>{
            route={...route,...proof,can_send:true,company_binding:binding,
              lifecycle:{id:'scoped-episode',state:'accepted',revision,operator_id:33,can_close:true,
                company_completion:{status:'choice_available',required:false,can_choose:false,can_skip:true}}};
            window.routeForTest=route;
          };
          window.fetch=async(target,opt={})=>{
            const url=new URL(target,location.href),body=opt.body&&JSON.parse(opt.body);
            const good=result=>({ok:true,status:200,json:async()=>({success:true,result})});
            if(url.pathname.endsWith('/crm/native/companies')){
              window.companySearchReads++;
              if(body||url.searchParams.get('search')!=='Synthetic'||url.searchParams.get('expected_tenant_id')!=='synthetic-tenant')
                throw Error('Inexact company lookup');
              return {ok:true,status:200,json:async()=>({success:true,companies:[
                {id:companyId,name:'Synthetic company',actions:{update:true}},{id:'denied',name:'Denied row',actions:{update:false}}]})};
            }
            if(url.pathname.endsWith('/dialogs_lifecycle')){
              window.atClose.push(window.historyReads);window.closeWrites.push(body);
              route={...route,can_send:false,lifecycle:{...route.lifecycle,state:'closed',revision:route.lifecycle.revision+1,can_close:false}};
              return good({lifecycle:route.lifecycle});
            }
            if(url.pathname.endsWith('/dialogs/history')){
              window.historyReads++;
              if(window.holdCompletionProof){window.holdCompletionProof=false;
                const captured=route;
                await new Promise(resolve=>{window.releaseCompletionProof=resolve;});
                window.completionProofReleased=true;
                return good({dialog:captured,messages:[]});
              }
              return good({dialog:route,messages:[]});
            }
            if(url.pathname.endsWith('/dialogs'))return good({dialogs:[route],channels:[],agents:[]});
            if(body&&!url.pathname.endsWith('/dialogs/read'))throw Error('Unexpected mutation '+url.pathname);
            return good({});
          };
        },companyId);
        for(const file of ['tenant-operator-i18n.js','tenant-operator-dialogs.js'])
          await page.addScriptTag({path:path.join(docs,file)});
        await page.evaluate(async()=>{window.tenantI18n.setLanguage('en');await window.tenantOperator.refresh();
          await window.tenantOperator.openDialog(window.routeForTest);});
        const launch=page.locator('[data-operator-lifecycle="close"]');
        const modal=page.getByRole('dialog',{name:'Company for dialogue completion',exact:true});
        const perform=async target=>{if(mobile)await target.tap();else await target.click();};
        await perform(launch);
        const input=modal.getByRole('combobox',{name:'Find a CRM company'}),done=modal.getByRole('button',{name:'Complete',exact:true});
        assert.deepEqual(await modal.getByRole('button').allTextContents(),['Complete','Cancel']);
        assert.equal(await modal.getByRole('heading').count(),0);
        const [box,search,finish,cancel]=await Promise.all([modal.boundingBox(),input.boundingBox(),done.boundingBox(),
          modal.getByRole('button',{name:'Cancel',exact:true}).boundingBox()]);
        assert(box.width<=400&&box.height<=130&&box.x>=0&&box.x+box.width<=width);
        assert(search.y+search.height<=finish.y&&Math.abs(finish.y-cancel.y)<=1);
        await perform(modal.getByRole('button',{name:'Cancel',exact:true}));
        await modal.waitFor({state:'hidden'});
        assert.deepEqual(await page.evaluate(()=>window.closeWrites),[]);
        await perform(launch);
        await input.fill('Synthetic');
        const item=modal.getByRole('option',{name:'Synthetic company'});
        await item.waitFor();assert.equal(await modal.getByRole('option').count(),1);
        if(mobile){await perform(item);await page.keyboard.press('Escape');await modal.waitFor({state:'hidden'});await perform(launch);}
        else {await input.press('ArrowDown');await input.press('Enter');assert.equal(await input.inputValue(),'Synthetic company');}
        assert.deepEqual(await page.evaluate(()=>window.closeWrites),[],'choosing and cancelling do not send a write');
        await perform(done);await modal.waitFor({state:'hidden'});
        const [close]=await page.evaluate(()=>window.closeWrites);
        assert.equal(await page.evaluate(()=>window.closeWrites.length),1);
        assert.equal(close.action,'close');assert.equal(close.channel_id,'synthetic-source');assert.equal(close.expected_revision,1);
        if(mobile){assert.equal(close.company_choice,'none');assert(!('company_id' in close));}
        else{assert.equal(close.company_id,companyId);assert(!('company_choice' in close));}

        // This accepted operator has no CRM company-selection right. Another
        // actor binds a company after the chat shell has been mounted.
        await page.evaluate(async()=>{window.setScopedClose(3,null);await window.tenantOperator.openDialog(window.routeForTest);});
        const staleReadCount=await page.evaluate(()=>window.historyReads);
        await page.evaluate(()=>window.setScopedClose(3,{company_id:'other-actor',company_name:'Synthetic existing company'}));
        await perform(launch);
        await page.waitForFunction(()=>window.closeWrites.length===2);
        assert.equal(await modal.count(),0);
        const replay=await page.evaluate(()=>({body:window.closeWrites[1],atClose:window.atClose[1]}));
        assert.equal(replay.atClose,staleReadCount+1,'ambiguous stale shell takes one exact read');
        assert.equal(replay.body.expected_revision,3);
        assert(!('company_id' in replay.body)&&!('company_choice' in replay.body),'never resend existing company data');

        // Known active binding takes the old direct-close path with no
        // extra read before the POST.
        await page.evaluate(async()=>{window.setScopedClose(5,{company_id:'other-actor'});await window.tenantOperator.openDialog(window.routeForTest);});
        const knownReadCount=await page.evaluate(()=>window.historyReads);
        await perform(launch);
        await page.waitForFunction(()=>window.closeWrites.length===3);
        assert.equal(await page.evaluate(()=>window.atClose[2]),knownReadCount);
        assert.equal(await modal.count(),0);

        // Missing, archived, and identity-conflicted are not known-active.
        const searchBefore=await page.evaluate(()=>window.companySearchReads);
        for(const [binding,proof] of [[null,{}],[{company_id:null},{}],[{company_id:'other-actor'},{identity_conflict:true}]]){
          await page.evaluate(async ({binding,proof})=>{window.setScopedClose(7,binding,{identity_conflict:false,...proof});await window.tenantOperator.openDialog(window.routeForTest);},{binding,proof});
          await perform(launch);
          await modal.waitFor({state:'visible'});
          assert.equal(await modal.getByRole('combobox').count(),0,'does not grant CRM lookup');
          await perform(modal.getByRole('button',{name:'Cancel',exact:true}));
          assert.equal(await page.evaluate(()=>window.closeWrites.length),3,'no automatic mutation without active company proof');
        }
        assert.equal(await page.evaluate(()=>window.companySearchReads),searchBefore);

        // Scope changes while a valid prior-episode proof is in flight.
        await page.evaluate(async()=>{window.setScopedClose(7,null,{identity_conflict:false});await window.tenantOperator.openDialog(window.routeForTest);
          window.setScopedClose(7,{company_id:'old-episode'});
          window.holdCompletionProof=true;});
        await perform(launch);
        await page.waitForFunction(()=>typeof window.releaseCompletionProof==='function');
        await page.evaluate(async()=>{window.setScopedClose(8,null);await window.tenantOperator.openDialog(window.routeForTest);
          window.releaseCompletionProof();});
        await page.waitForFunction(()=>window.completionProofReleased&&
          !document.querySelector('[data-operator-lifecycle="close"]').disabled);
        assert.equal(await modal.count(),0);
        assert.equal(await page.evaluate(()=>window.closeWrites.length),3,'late previous episode proof cannot close the new episode');
      }finally{await page.close();}
    }
    console.log('PASS Chromium compact completion dialog at 360/1440, explicit chosen/blank Finish and cancellation');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
