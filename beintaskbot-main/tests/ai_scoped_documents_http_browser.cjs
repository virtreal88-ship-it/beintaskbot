/* Real Chromium lifecycle, production controller and localhost synthetic HTTP.
   Assertions execute as DOM events in the page, not physical pointer tests. */
const assert=require('node:assert/strict');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const origin=process.env.AI_RUNTIME_ORIGIN,width=Number(process.env.AI_RUNTIME_WIDTH||1100);
assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(origin||''),'isolated localhost origin required');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:[
  '--no-sandbox','--disable-dev-shm-usage','--no-proxy-server','--disable-background-networking',
  '--disable-component-update','--no-first-run','--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1']});
 try{
  const context=await browser.newContext({viewport:{width,height:950}});
  const page=await context.newPage(),errors=[],failed=[],responses=[];
  page.on('response',response=>responses.push({url:response.url(),status:response.status()}));
  page.on('pageerror',error=>errors.push(error.message));
  page.on('requestfailed',request=>failed.push({url:request.url(),failure:request.failure()?.errorText}));
  async function diagnostics(){
   const state=await page.evaluate(async()=>({
    href:location.href,readyState:document.readyState,
    acceptance:document.getElementById('aiAcceptanceResult')?.textContent,
    host:document.getElementById('aiScopedDocuments')?.outerHTML,
    activeView:document.getElementById('view-ai_agents')?.className,
    bootstrapErrors:window.AI_ACCEPTANCE_ERRORS,
    profile:window.tenantProfileSnapshot,
    httpState:await fetch('/test/state').then(r=>r.json()).catch(error=>String(error)),
    bodyText:document.body.innerText.slice(-4000)
   })).catch(error=>({diagnosticError:String(error)}));
   return JSON.stringify({errors,failed,responses,state});
  }
  await page.goto(origin+'/app',{waitUntil:'domcontentloaded',timeout:15000});
  try{await page.locator('#aiAcceptanceResult[data-result=passed], #aiAcceptanceResult[data-result=failed]').waitFor({state:'attached',timeout:45000});}
  catch(error){throw new Error('Browser acceptance did not finish: '+await diagnostics());}
  const result=page.locator('#aiAcceptanceResult'),message=await result.textContent();
  assert.equal(await result.getAttribute('data-result'),'passed',message+'\n'+(await result.getAttribute('data-result')==='passed'?'':await diagnostics()));
  assert.deepEqual(errors,[]);console.log(message);
 }finally{await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
