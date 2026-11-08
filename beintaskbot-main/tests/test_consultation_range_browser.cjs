/* Isolated Chromium: all endpoints mocked; no production messages or AI. */
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
const base='/api/platform/ai/dialog-consultations',hash='a'.repeat(64);
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':chromium.executablePath()),args:['--no-sandbox']});
 try{for(const kind of ['dialog','case'])for(const language of ['ru','az','en']){
  const page=await browser.newPage({viewport:{width:390,height:740}}),calls=[];
  const messages=Array.from({length:80},(_,i)=>({id:'m'+i,message_type:i===79?'audio':'text',body:'PRIVATE RAW '+i,public_text:'PUBLIC '+i,public_speaker:'Customer',source_version:'v'+i,direction:'incoming',happened_at:new Date(Date.UTC(2026,10,8,10,i)).toISOString(),media_shareable:true,file_name:i===79?'voice.ogg':undefined}));
  await page.route('**/*',async r=>{
   const path=new URL(r.request().url()).pathname;
   if(path==='/')return r.fulfill({contentType:'text/html',body:'<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="host"></div><select data-app-language></select><details id="operatorMoreActions"><summary>Menu</summary><div id="operatorMenuExtra"></div></details><aside id="operatorCopilot"></aside></body></html>'});
   if(!path.startsWith('/api/'))throw Error('Unexpected network request '+path);
   const body=r.request().postDataJSON();calls.push({path,body});let result={};
   if(path===base+'/preview')result={messages,public_header:{version:1,customer_name:'Customer',phone_status:'unknown',company_name:'Company',operator_name:'Operator'},public_header_hash:hash};
   else if(path===base+'/destinations')result={destinations:[{channel_id:'dev',conversation_id:'developer',customer_name:'Developer'}]};
   else if(path===base+'/replies')result={consultations:[],replies:[]};
   else if(path==='/api/platform/notifications')result={notifications:[]};
   else if(path===base+'/question-draft')result={text:'AI question',request_id:body.request_id,developer_question_draft:true,customer_message_sent:false};
   else if(path===base)result={id:'mock-consultation'};
   else if(path.endsWith('/dispatch'))result={state:'sent'};
   await r.fulfill({json:{success:true,result}});
  });
  await page.goto('https://crm.test/');await page.evaluate(language=>{window.tenantI18n={language,date:value=>value};window.tenantOperator={member:{tenant_id:'tenant',telegram_id:1,role:'owner'},hasRight:()=>true,currentDialog:{provider:'connect',agent_id:'agent',agent_mode:'manual',channel_id:'source',conversation_id:'customer'}};},language);
  await page.addScriptTag({path:'docs/tenant-'+(kind==='dialog'?'dialog':'support')+'-consultations.js'});
  if(kind==='case'){await page.evaluate(()=>window.tenantCaseConsultations.mount({host:document.getElementById('host'),member:window.tenantOperator.member,ticket:{...window.tenantOperator.currentDialog,id:'case',status:'new'},getMessages:()=>[],isCurrent:()=>true}));await page.locator('#consultCreate').click();await page.locator('.case-consult-materials summary').click();}
  else{await page.locator('#operatorMoreActions > summary').click();await page.locator('#dialogDeveloperAsk').click();}
  const form=page.locator(kind==='case'?'#caseDeveloperForm':'.dialog-consultation-form'),selects=form.locator('select'),first=selects.nth(1),last=selects.nth(2),send=form.locator('button[type=submit]'),draft=form.locator('[data-question-draft]'),notice=form.locator('[data-range-adjustment]'),value=i=>kind==='dialog'?String(i):'m'+i;
  await form.locator('textarea').fill('Manual developer question');assert.equal(await send.isDisabled(),false);assert.equal(await draft.isDisabled(),false);
  await first.selectOption(value(0));assert.equal(await first.inputValue(),value(0));assert.equal(await last.inputValue(),value(29));assert.equal(await send.isDisabled(),false);assert.equal(await draft.isDisabled(),false);assert.equal(await notice.isVisible(),true);assert.match(await notice.textContent(),/30/);
  const preview=form.locator(kind==='case'?'[data-case-preview-messages]':'.consultation-fragment');assert.equal(await preview.locator(kind==='case'?':scope > p':'article').count(),30);assert.match(await preview.textContent(),/PUBLIC 0/);assert.doesNotMatch(await preview.textContent(),/PRIVATE RAW|PUBLIC 79/);assert.match(await form.locator('[data-public-header-preview]').textContent(),/Company/);
  await last.selectOption(value(79));assert.equal(await first.inputValue(),value(50));assert.equal(await last.inputValue(),value(79));
  await last.selectOption(value(1));assert.equal(await first.inputValue(),value(1));assert.equal(await send.isDisabled(),false);
  await first.selectOption(value(79));assert.equal(await last.inputValue(),value(79));assert.equal(await draft.isDisabled(),false);
  await first.selectOption(value(60));assert.equal(await last.inputValue(),value(79));assert.equal(await send.isDisabled(),false);assert.equal(await draft.isDisabled(),false);assert.equal(await notice.isVisible(),false);
  await draft.click();await page.waitForFunction(()=>document.querySelector('[data-question-draft]').disabled===false);assert.equal(await send.isDisabled(),false);assert.equal(calls.filter(c=>c.path===base+'/question-draft').at(-1).body.message_ids.length,20);
  await first.selectOption(value(0));assert.equal(await send.isDisabled(),true,'stale AI remains blocked');assert.equal(await draft.isDisabled(),false);await form.locator('textarea').fill('Manual edit of stale AI');assert.equal(await send.isDisabled(),true);
  await draft.click();await page.waitForFunction(()=>document.querySelector('[data-question-draft]').disabled===false);assert.equal(await send.isDisabled(),false);
  assert.equal(calls.filter(c=>c.path===base||c.path.endsWith('/dispatch')).length,0);const prepared=page.waitForRequest(request=>new URL(request.url()).pathname===base);await send.click();await prepared;
  const payload=calls.find(c=>c.path===base)?.body;assert.ok(payload);assert.deepEqual(payload.message_ids,Array.from({length:30},(_,i)=>'m'+i));assert.deepEqual(Object.keys(payload.public_header_source_versions),payload.message_ids);assert.equal(payload.public_header_hash,hash);assert.deepEqual(payload.media_ids,[],'old file outside range never reselected');
  await page.close();console.log('PASS: range adjustment, manual/AI, frozen public preview '+kind+' '+language);
 }}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
