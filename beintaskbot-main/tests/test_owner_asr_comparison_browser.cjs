/* Actual operator diagnostics clicks; only synthetic approved-source API mocks. */
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {chromium}=require('../.support-test-deps/node_modules/playwright'),root=path.join(__dirname,'..');
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':chromium.executablePath()),args:['--no-sandbox']});try{
 for(const width of [390,1440]){
  const page=await browser.newPage({viewport:{width,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',()=>assert.fail('No native confirmations or hidden requests'));
  await page.route('https://test.local/**',r=>r.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')}));await page.goto('https://test.local/app?view=dialogs');
  await page.evaluate(()=>{
   document.querySelectorAll('.view').forEach(e=>e.classList.remove('active'));document.getElementById('view-dialogs').classList.add('active');
   window.fixtureRoute={provider:'connect',lead_id:41,agent_id:'synthetic-agent',channel_id:'synthetic-channel',conversation_id:'synthetic-chat',customer_name:'Synthetic customer',agent_name:'Assigned agent',agent_mode:'manual',state:'human',can_send:true,controls_allowed:true,available_actions:[]};
   window.tenantProfileSnapshot={member:{tenant_id:'synthetic-tenant',telegram_id:11,role:'owner',active:true},capabilities:{modules:{dialogs:true}}};window.requests=[];window.executed=false;
   window.fetch=async(url,options={})=>{url=String(url);const body=options.body?JSON.parse(options.body):null;window.requests.push({url,method:options.method||'GET',body});let result={};
    if(url.includes('/asr-comparison')){if(body){window.executed=true;result={admitted:true,can_execute:false,historical:false,variants:[{model:'whisper-1',state:'unknown',reserve_microusd:30000,accounting_state:'unknown',code:'provider_outcome_unknown'},{model:'gpt-4o-mini-transcribe',state:'completed',reserve_microusd:30000,accounting_state:'settled',parameters:{language:'az',response_format:'json',include:['logprobs'],prompt_supplied:false},result:{transcript:'Synthetic ASR <img src=x onerror=alert(1)>',usage:{input_tokens:10,output_tokens:5},diagnostics:{}}}]};window.savedResult=result;}
     else result=window.executed?window.savedResult:{admitted:false,can_execute:true,version:'frozen-version',variants:[{model:'whisper-1',reserve_microusd:30000},{model:'gpt-4o-mini-transcribe',reserve_microusd:30000}]};
    }else if(url.includes('/dialogs/history'))result={dialog:window.fixtureRoute,messages:[{id:'synthetic-row',external_id:'connect:synthetic-source',source:'connect',message_type:'audio',direction:'incoming',body:'',author_type:'customer',happened_at:'2026-11-01T00:00:00Z'}]};
    else if(url.includes('/dialogs?'))result={dialogs:[window.fixtureRoute],channels:[],agents:[]};
    else if(url.includes('/state'))result={agents:[],events:[]};
    else if(url.includes('/api/platform/me'))return{ok:true,json:async()=>window.tenantProfileSnapshot};
    return{ok:true,json:async()=>({success:true,result})};};
  });
  for(const file of ['tenant-operator-i18n.js','tenant-operator-dialogs.js'])await page.addScriptTag({path:path.join(root,'docs',file)});
  const synthetic=crypto.createHash('sha256').update(JSON.stringify(['synthetic-tenant','synthetic-channel','synthetic-chat','connect:synthetic-source'])).digest('hex');
  const source=fs.readFileSync(path.join(root,'docs/tenant-owner-asr-comparison.js'),'utf8').replace(/const approved='[a-f0-9]+'/,`const approved='${synthetic}'`);
  await page.addScriptTag({content:source});await page.locator('.operator-row').first().click();
  await page.locator('#operatorMoreActions summary').click();await page.locator('[data-operator-diagnostics]').click();
  const comparison=page.locator('#ownerASRComparison');await comparison.waitFor();
  assert.equal((await page.evaluate(()=>window.requests)).filter(r=>r.url.includes('/asr-comparison')).length,0,'mount does not even preview automatically');
  await comparison.getByRole('button').first().click();
  const execute=comparison.locator('[data-owner-asr-execute]');await execute.waitFor();assert.equal(await execute.isDisabled(),true);
  assert.equal((await page.evaluate(()=>window.requests)).filter(r=>r.method==='POST'&&r.url.includes('/asr-comparison')).length,0);
  await comparison.locator('[data-owner-asr-confirm]').check();await execute.click();await comparison.getByText(/Synthetic ASR/).waitFor();
  assert.equal(await comparison.locator('img').count(),0);assert.equal(await comparison.locator('[data-owner-asr-execute]').count(),0,'unknown result cannot replay');
  await comparison.getByRole('button').first().click();
  const requests=await page.evaluate(()=>window.requests),posts=requests.filter(r=>r.method==='POST'&&r.url.includes('/asr-comparison'));
  assert.equal(posts.length,1);assert.deepEqual(Object.keys(posts[0].body).sort(),['channel_id','conversation_id','message_id','version','confirmed','max_calls','max_cost_microusd','expected_tenant_id','expected_user_id'].sort());
  assert.equal(posts[0].body.max_calls,2);assert.equal(posts[0].body.max_cost_microusd,60000);assert.equal(posts[0].body.message_id,'connect:synthetic-source');
  assert.equal(requests.some(r=>/\/copilot|transcriptions|\/send/.test(r.url)),false);
  await page.locator('#operatorDiagnosticsClose').click();
  await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-profile',{detail:{member:{tenant_id:'other-tenant',telegram_id:99,role:'admin',active:true},capabilities:{modules:{dialogs:true}}}})));
  assert.equal(await page.locator('#ownerASRComparison').count(),0);
  assert.deepEqual(errors,[]);console.log('PASS owner ASR comparison '+width+': ordinary diagnostics, explicit bounded confirmation, owner source only, read-only unknown results, no AI replay');await page.close();
 }
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1);});
