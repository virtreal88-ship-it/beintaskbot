/* Real browser, mocked network only: developer question drafts never send by themselves. */
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
const base='/api/platform/ai/dialog-consultations';
const message={id:'11111111-1111-4111-8111-111111111111',body:'Printer is unavailable',message_type:'text',direction:'incoming',happened_at:'2026-11-01T12:00:00Z',source_version:'preview-version'};
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||(fs.existsSync('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe')?'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe':fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':chromium.executablePath()),args:['--no-sandbox']});
 try{for(const kind of ['dialog','case'])for(const width of [390,1280]){
  const page=await browser.newPage({viewport:{width,height:844}}),calls=[];let release,gate;
  await page.route('https://crm.test/',route=>route.fulfill({contentType:'text/html',body:'<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:12px;font:16px Arial}select,textarea{box-sizing:border-box;max-width:100%}button{min-height:40px;max-width:100%;white-space:normal}#host{max-width:650px}.support-edit{display:grid;gap:8px}textarea{width:100%}</style></head><body><div id="host"></div><select data-app-language></select><details id="operatorMoreActions"><summary>More</summary><div id="operatorMenuExtra"></div></details><aside id="operatorCopilot"></aside></body></html>'}));
  await page.route('**/api/**',async route=>{const request=route.request(),path=new URL(request.url()).pathname,body=request.postDataJSON();calls.push({path,body});let result={};
   if(path===base+'/preview')result={public_header:{version:1,customer_name:'Synthetic customer',verified_phone:'+15550001111',phone_status:'verified',company_name:'Synthetic client company',operator_name:'Synthetic operator'},public_header_hash:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',messages:[message]};
   else if(path===base+'/destinations')result={destinations:[{channel_id:'developer-channel',conversation_id:'developer',customer_name:'Developer'}]};
   else if(path===base+'/question-draft'){if(gate)await gate;result={text:'Which printer setting should we check?',request_id:'22222222-2222-4222-8222-222222222222',developer_question_draft:true,customer_message_sent:false,context_usage:{missing_media:[]}};}
   else if(path===base)result={id:'consultation'};
   else if(path.endsWith('/dispatch'))result={state:'sent'};
   else if(path===base+'/replies')result={replies:[],consultations:[]};
   else if(path==='/api/platform/notifications')result={notifications:[],unread_count:0};
   await route.fulfill({json:{success:true,result}});
  });
  await page.goto('https://crm.test/');
  await page.evaluate(()=>{window.tenantI18n={language:'en',date:value=>value};window.tenantOperator={member:{tenant_id:'tenant',telegram_id:33,role:'owner'},hasRight:()=>true,currentDialog:{provider:'connect',agent_mode:'manual',agent_id:'agent',channel_id:'channel',conversation_id:'source'}};});
  await page.addScriptTag({path:'docs/tenant-'+(kind==='dialog'?'dialog':'support')+'-consultations.js'});
  if(kind==='case')await page.evaluate(()=>window.controller=window.tenantCaseConsultations.mount({host:document.getElementById('host'),member:window.tenantOperator.member,ticket:{id:'33333333-3333-4333-8333-333333333333',agent_id:'agent',channel_id:'channel',conversation_id:'source',status:'new'},getMessages:()=>[],isCurrent:()=>true}));
  const opener=page.locator(kind==='dialog'?'#dialogDeveloperAsk':'#consultCreate');async function openForm(){if(kind==='dialog')await page.locator('#operatorMoreActions > summary').click();await opener.click();}await openForm();
  const form=page.locator(kind==='dialog'?'form.dialog-consultation-form':'#caseDeveloperForm'),summary=form.locator('textarea'),draft=form.locator('[data-question-draft]'),review=form.locator(kind==='dialog'?'[data-consultation-confirm]':'[data-question-draft-review]'),send=form.locator('button[type=submit]');
  assert.equal(await review.count(),0,'review uses the explicit send button, not a checkbox');
  await summary.fill('Employee context');gate=new Promise(resolve=>release=resolve);await draft.click();assert.equal(await draft.isDisabled(),true);assert.equal(await send.isDisabled(),true);await summary.fill('Keep this employee edit');release();gate=null;await draft.isEnabled();await page.waitForFunction(()=>!document.querySelector('[data-question-draft]').disabled);assert.equal(await summary.inputValue(),'Keep this employee edit');
  assert.equal(calls.filter(call=>call.path===base||call.path.endsWith('/dispatch')).length,0);
  await draft.click();await page.waitForFunction(()=>document.querySelector('form textarea').value==='Which printer setting should we check?');assert.equal(await send.isEnabled(),true);assert.equal(calls.filter(call=>call.path===base||call.path.endsWith('/dispatch')).length,0);
  const firstId=calls.find(call=>call.path===base+'/question-draft').body.request_id;
  await form.getByRole('button',{name:'Close',exact:true}).click();await openForm();await summary.waitFor();assert.equal(await summary.inputValue(),'Which printer setting should we check?');assert.equal(calls.filter(call=>call.path===base).length,0,'reopening does not send');
  await summary.fill('Reviewed employee wording');assert.equal(await send.isEnabled(),true);
  const overflow=await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(el=>el.getBoundingClientRect().right>innerWidth).map(el=>({tag:el.tagName,id:el.id,class:el.className,width:el.getBoundingClientRect().width,right:el.getBoundingClientRect().right,text:el.textContent.slice(0,80)})));
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,kind+' mobile form fits viewport '+JSON.stringify(overflow));
  await send.click();await page.waitForTimeout(50);assert.equal(calls.filter(call=>call.path===base).length,1);assert.equal(calls.filter(call=>call.path.endsWith('/dispatch')).length,1);assert.equal(calls.find(call=>call.path===base).body.question_draft_request_id,'22222222-2222-4222-8222-222222222222');assert.equal(calls.some(call=>/analy[sz]e|transcrib|messages\/send/.test(call.path)),false);
  assert.ok(firstId);await page.close();
 }
 console.log('PASS: real Chrome mock-only dialog/case question draft at 390px/1280px, editable late results, dismissal, review and explicit send');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
