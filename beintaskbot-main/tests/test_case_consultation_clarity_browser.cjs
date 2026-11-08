/* Case-only layout and explicit-send regression. All endpoints are synthetic. */
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
const base='/api/platform/ai/dialog-consultations';
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||(fs.existsSync('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe')?'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe':fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':chromium.executablePath()),args:['--no-sandbox']});
 try{for(const width of [320,390,1280]){
  const page=await browser.newPage({viewport:{width,height:700}}),calls=[];
  const messages=Array.from({length:4},(_,i)=>({id:'message-'+i,body:i===0?'RAW PRIVATE SOURCE URL https://private.invalid':'',public_text:i===0?'First selected customer message':'',public_speaker:'Müştəri',media_shareable:i!==0&&i!==2,message_type:i===0?'text':'audio',file_name:'voice-'+i+'.ogg',direction:'incoming',happened_at:'2026-11-01T12:00:00Z',public_sender_version:'b'.repeat(64),source_version:'v'+i}));
  await page.route('https://crm.test/',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:8px;font:16px Arial;--line:#ddd;--brand:#3159c8;--panel:#fff}#host{max-width:650px}button{font:inherit}textarea{width:100%}.support-edit{display:grid;gap:10px}h3{margin:0}</style></head><body><div id="host"></div></body></html>'}));
  await page.route('**/api/**',async route=>{const request=route.request(),path=new URL(request.url()).pathname,body=request.postDataJSON();calls.push({path,body});let result={};
   if(path===base+'/preview')result={public_header:{version:1,customer_name:'Synthetic customer',verified_phone:'+15550001111',phone_status:'verified',company_name:'Synthetic client company',operator_name:'Synthetic operator'},public_header_hash:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',messages,outgoing_link_label:'Exact outgoing link label'};
   else if(path===base+'/destinations')result={destinations:[{channel_id:'dev',conversation_id:'developer-group',customer_name:'Developer group',channel_name:'Support WhatsApp'}]};
   else if(path===base+'/replies')result={consultations:[],replies:[]};
   else if(path===base+'/question-draft')result={text:'Please check this audio report.',request_id:'draft-1',developer_question_draft:true,customer_message_sent:false};
   else if(path===base)result={id:'consultation'};
   await route.fulfill({json:{success:true,result}});
  });
  await page.goto('https://crm.test/');
  await page.evaluate(()=>{window.tenantI18n={language:'en',date:value=>value};window.tenantOperator={hasRight:()=>true};});
  await page.addScriptTag({path:'docs/tenant-support-consultations.js'});
  await page.evaluate(()=>window.controller=window.tenantCaseConsultations.mount({host:document.getElementById('host'),member:{tenant_id:'tenant',telegram_id:1,role:'owner'},ticket:{id:'case',channel_id:'source',conversation_id:'customer',agent_id:'agent',status:'new'},getMessages:()=>[],isCurrent:()=>true}));
  await page.locator('#consultCreate').click();await page.locator('[data-send-disabled-reason]').waitFor({state:'visible'});assert.match(await page.locator('[data-send-disabled-reason]').textContent(),/Write a message/);await page.locator('#consultReason').fill('A question for the developer.');
  assert.equal(await page.locator('#caseDeveloperForm input[type=checkbox]').count(),0);
  assert.equal(await page.locator('[data-case-media-check]').count(),0);
  assert.equal(await page.locator('.case-consult-preview').count(),0);
  assert.match(await page.locator('[data-public-scope]').textContent(),/sender and customer names.*anyone with the link.*7 days/);
  await page.locator('.case-consult-materials summary').click();await page.locator('#caseConsultFirst').selectOption('message-0');
  await page.locator('#caseQuestionDraft').click();await page.waitForFunction(()=>document.querySelector('#consultReason').value==='Please check this audio report.');
  await page.waitForFunction(()=>!document.querySelector('#caseConsultSend').disabled);
  assert.equal(calls.filter(c=>c.path===base||c.path.endsWith('/dispatch')).length,0,'AI and editing never send');
  await page.locator('#caseConsultLast').selectOption('message-2');
  assert.equal(await page.locator('#caseConsultSend').isDisabled(),true,'changed source invalidates AI draft');
  await page.locator('#caseQuestionDraft').click();await page.waitForFunction(()=>!document.querySelector('#caseConsultSend').disabled);
  await page.evaluate(()=>document.querySelector('.case-consult-body').scrollTop=10000);
  await page.locator('#caseConsultSend').scrollIntoViewIfNeeded();
  const layout=await page.evaluate(()=>{const send=document.querySelector('#caseConsultSend').getBoundingClientRect(),body=document.querySelector('.case-consult-body'),footer=document.querySelector('.case-consult-footer').getBoundingClientRect();return{send:{top:send.top,bottom:send.bottom,height:send.height},footerBottom:footer.bottom,overflow:document.documentElement.scrollWidth>innerWidth,scrolls:body.scrollHeight>body.clientHeight};});
  assert.equal(layout.overflow,false,'no horizontal page overflow');assert.ok(layout.send.height>=44);assert.ok(layout.send.top>=0&&layout.send.bottom<=700,'send stays visible');if(width<400)assert.equal(layout.scrolls,true);
  if(width===320){await page.setViewportSize({width,height:480});await page.locator('#caseConsultSend').scrollIntoViewIfNeeded();const compact=await page.locator('#caseConsultSend').boundingBox();assert.ok(compact.y>=0&&compact.y+compact.height<=480,'send remains reachable with reduced viewport');await page.screenshot({path:require('node:path').join(require('node:os').tmpdir(),'case-consultation-clarity-320.png')});await page.setViewportSize({width,height:700});}
  await page.locator('.case-consult-footer button').filter({hasText:'Close'}).click();
  await page.locator('#consultCreate').click();await page.locator('#consultReason').waitFor();
  assert.equal(await page.locator('#caseConsultFirst').inputValue(),'message-0');assert.equal(await page.locator('#caseConsultLast').inputValue(),'message-2');
  await page.evaluate(()=>document.querySelector('#caseDeveloperForm').dispatchEvent(new Event('submit',{cancelable:true})));
  assert.equal(calls.filter(c=>c.path===base).length,0,'implicit submit is inert');
  await page.locator('#caseConsultSend').click();await page.waitForFunction(()=>!document.querySelector('#caseDeveloperForm'));
  const sent=calls.find(c=>c.path===base).body;assert.deepEqual(sent.media_ids,['message-1']);assert.equal(sent.include_customer_identity,false);assert.equal(sent.public_header_version,1);assert.equal(sent.public_header_hash,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');assert.deepEqual(Object.keys(sent.public_header_source_versions),sent.message_ids);assert.equal(sent.confirm,true);assert.equal(calls.filter(c=>c.path.endsWith('/dispatch')).length,1);
  await page.close();console.log('PASS: case form preview, bounded media, explicit Send, mobile footer '+width);
 }}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
