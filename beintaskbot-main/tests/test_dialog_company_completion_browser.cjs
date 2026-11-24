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
          window.tenantProfileSnapshot=profile;window.closeWrites=[];
          let route={provider:'connect',lead_id:41,channel_id:'synthetic-source',conversation_id:'synthetic-contact',
            customer_name:'Synthetic client',can_send:true,lifecycle_enabled:true,available_actions:[],
            lifecycle:{id:'episode',state:'accepted',revision:1,operator_id:33,can_close:true,
              company_completion:{status:'missing',can_choose:true,can_skip:true}}};
          window.routeForTest=route;
          window.fetch=async(target,opt={})=>{
            const url=new URL(target,location.href),body=opt.body&&JSON.parse(opt.body);
            const good=result=>({ok:true,status:200,json:async()=>({success:true,result})});
            if(url.pathname.endsWith('/crm/native/companies')){
              if(body||url.searchParams.get('search')!=='Synthetic'||url.searchParams.get('expected_tenant_id')!=='synthetic-tenant')
                throw Error('Inexact company lookup');
              return {ok:true,status:200,json:async()=>({success:true,companies:[
                {id:companyId,name:'Synthetic company',actions:{update:true}},{id:'denied',name:'Denied row',actions:{update:false}}]})};
            }
            if(url.pathname.endsWith('/dialogs_lifecycle')){
              window.closeWrites.push(body);
              route={...route,can_send:false,lifecycle:{id:'episode',state:'closed',revision:2,operator_id:33,can_close:false}};
              return good({lifecycle:route.lifecycle});
            }
            if(url.pathname.endsWith('/dialogs/history'))return good({dialog:route,messages:[]});
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
      }finally{await page.close();}
    }
    console.log('PASS Chromium compact completion dialog at 360/1440, explicit chosen/blank Finish and cancellation');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
