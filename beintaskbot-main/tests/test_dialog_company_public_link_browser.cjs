/* Real Chromium clipboard, synthetic same-origin response. No external traffic. */
const assert=require('node:assert/strict'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'../.support-test-deps/node_modules/playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined});
 try{
  const origin='https://crm.test',url='https://1000.az/a-_12X',calls=[];
  const context=await browser.newContext({viewport:{width:360,height:800}});
  await context.grantPermissions(['clipboard-read','clipboard-write'],{origin});
  const page=await context.newPage();
  await page.route('**/*',async route=>{
   const request=route.request(),address=new URL(request.url());
   if(address.origin!==origin)return route.abort();
   if(request.isNavigationRequest())return route.fulfill({contentType:'text/html',
    body:'<dialog><div id="host"></div></dialog><span id="name"></span>'});
   if(address.pathname!=='/api/platform/operator/dialog-company/public-link')return route.abort();
   calls.push({method:request.method(),body:request.postDataJSON()});
   await new Promise(resolve=>setTimeout(resolve,150));
   return route.fulfill({json:{success:true,public_link:{state:'available',url}}});
  });
  await page.goto(origin+'/app');
  await page.addScriptTag({path:path.resolve(__dirname,'../docs/tenant-dialog-company.js')});
  await page.evaluate(()=>{
   window.tenantI18n={language:'az'};
   window.tenantDialogueCompany.mount({host:document.querySelector('#host'),nameHost:document.querySelector('#name'),
     route:{provider:'connect',lead_id:42,channel_id:'source',conversation_id:'peer',
       company_binding:{company_id:'company',company_name:'Synthetic'}},
     member:{tenant_id:'tenant',telegram_id:33},isCurrent:()=>true});
   document.querySelector('dialog').open=true;
   window.tenantDialogueCompany.refreshPublicLink();
  });
  const row=page.locator('[data-company-public-link]'),icon=row.getByRole('button',{name:'Linki kopyala'});
  assert.equal(await row.locator('span').textContent(),'Müştəri linki');
  assert.equal(calls.length,0,'opening never calls issuance');
  await icon.click();
  await row.getByText('Link kopyalandı',{exact:true}).waitFor();
  assert.deepEqual(calls,[{method:'POST',body:{provider:'connect',lead_id:'42',channel_id:'source',
    conversation_id:'peer',expected_company_id:'company',expected_tenant_id:'tenant',expected_user_id:'33'}}]);
  assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),url);
  assert(!((await row.innerHTML()).includes(url)));
  await icon.click();await row.getByText('Link kopyalandı',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),url);
  assert.equal(calls.length,1,'second click does not reissue');
  console.log('PASS: Chromium 360px one-click clipboard and same-link reuse');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
