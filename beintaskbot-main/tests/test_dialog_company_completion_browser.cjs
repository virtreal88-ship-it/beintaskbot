/* Single-click acceptance and completion on a synthetic chat at both widths. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'../.support-test-deps/node_modules/playwright');
const docs=path.resolve(__dirname,'../docs');
const html=fs.readFileSync(path.join(docs,'tenant-app.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
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
        await page.evaluate(()=>{
          // The normal recurring refresh is independent of either button.
          Object.defineProperty(document,'hidden',{get:()=>true});
          document.querySelectorAll('.view').forEach(node=>node.classList.toggle('active',node.id==='view-dialogs'));
          window.tenantProfileSnapshot={member:{tenant_id:'synthetic-tenant',telegram_id:33,role:'operator',active:true},capabilities:{modules:{dialogs:true}}};
          window.calls=[];
          const route={provider:'connect',lead_id:41,channel_id:'synthetic-source',conversation_id:'synthetic-contact',
            customer_name:'Synthetic client',can_send:false,lifecycle_enabled:true,available_actions:[],
            lifecycle:{state:'waiting',revision:0,can_accept:true,last_inbound_id:'m1'}};
          window.routeForTest=route;
          window.fetch=async(target,opt={})=>{
            const url=new URL(target,location.href),body=opt.body&&JSON.parse(opt.body);
            window.calls.push({path:url.pathname,body});
            const good=result=>({ok:true,status:200,json:async()=>({success:true,result})});
            if(url.pathname.endsWith('/dialogs_lifecycle')){
              const accepting=body.action==='accept';
              const lifecycle={id:'episode',operator_id:33,state:accepting?'accepted':'closed',revision:accepting?1:2};
              const episode={id:'episode',accepted_at:'2026-01-01T00:00:00Z',closed_at:'2026-01-01T00:01:00Z',case_id:null};
              await new Promise(resolve=>{window.releaseAction=resolve;});
              window.releaseAction=null;
              return good({lifecycle,controls:{can_send:accepting,
                lifecycle:{...lifecycle,can_close:accepting,can_start:!accepting,can_submit_case:!accepting},
                ...(!accepting?{closed_episode:episode}:{})}});
            }
            if(url.pathname.endsWith('/dialogs/history')){
              if(window.blockHistory)await new Promise(()=>{});
              return good({dialog:route,messages:[{id:'m1',direction:'incoming',body:'Existing test message'}],next_before:null});
            }
            if(url.pathname.endsWith('/dialogs'))return good({dialogs:[route],channels:[],agents:[]});
            if(body&&!url.pathname.endsWith('/dialogs/read'))throw Error('Unexpected mutation '+url.pathname);
            return good({});
          };
        });
        for(const file of ['tenant-operator-i18n.js','tenant-operator-dialogs.js'])
          await page.addScriptTag({path:path.join(docs,file)});
        await page.evaluate(async()=>{
          window.tenantI18n.setLanguage('en');
          await window.tenantOperator.refresh();
          await window.tenantOperator.openDialog(window.routeForTest);
          window.calls=[];
          window.blockHistory=true;
        });
        const action=kind=>page.locator('[data-operator-lifecycle="'+kind+'"]');
        const perform=target=>mobile?target.tap():target.click();
        await perform(action('accept'));
        await page.waitForFunction(()=>Boolean(window.releaseAction));
        assert(await action('accept').isDisabled());
        await page.evaluate(()=>window.releaseAction());
        await action('close').waitFor();
        assert(!(await page.locator('#chatText').isDisabled()));
        await page.locator('#chatText').fill('Draft to preserve');
        await perform(action('close'));
        await page.waitForFunction(()=>Boolean(window.releaseAction));
        assert(await action('close').isDisabled());
        assert.equal(await page.locator('#operatorLifecycleModal').count(),0);
        await page.evaluate(()=>window.releaseAction());
        await action('start').waitFor();
        assert(await page.locator('#chatText').isDisabled());
        assert.equal(await page.locator('#chatText').inputValue(),'Draft to preserve');
        assert.match(await page.locator('#chatRows').textContent(),/Existing test message/);
        const calls=await page.evaluate(()=>window.calls);
        assert.deepEqual(calls.map(call=>call.path.split('/').at(-1)),['dialogs_lifecycle','dialogs_lifecycle']);
        assert.equal(calls[1].body.company_choice,'unchanged');
        assert.equal(calls[1].body.expected_revision,1);
        assert(!('company_id' in calls[1].body));
      }finally{await page.close();}
    }
    console.log('PASS Chromium 360/1440: accept and finish update on receipt, with no modal, lookup or history request');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
