const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'),root=path.join(__dirname,'..');
(async()=>{const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined,args:['--no-sandbox']});try{for(const width of [390,1440]){
 const page=await browser.newPage({viewport:{width,height:900}});
 await page.route('https://test.local/**',r=>r.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')}));await page.goto('https://test.local/app');
 await page.evaluate(()=>{
  document.querySelectorAll('.view').forEach(e=>e.classList.remove('active'));document.getElementById('view-dialogs').classList.add('active');window.confirm=()=>true;
  window.testRoute={provider:'connect',lead_id:41,channel_id:'channel-a',conversation_id:'client-a',agent_id:'agent-a',agent_version:1,assignment_revision:1,agent_mode:'manual',state:'human',can_send:true,controls_allowed:true,available_actions:[]};
  window.tenantProfileSnapshot={member:{tenant_id:'tenant-a',telegram_id:11,role:'owner',active:true},capabilities:{modules:{dialogs:true,support:true}}};
  window.testMessages=[{id:'incoming-a',external_id:'connect:a',source:'connect',direction:'incoming',message_type:'text',body:'Synthetic customer request',author_type:'customer'}];window.calls=[];window.failure='unknown';window.keep=true;
  window.fetch=async(url,options={})=>{url=String(url);let result={};
   if(url.includes('/dialogs/copilot')){const body=JSON.parse(options.body);window.calls.push(body);if(window.failure==='lost')throw new TypeError('Synthetic lost response');if(window.failure!=='success')return {ok:false,status:window.keep?503:409,json:async()=>({success:false,error:'Synthetic unresolved operation',keep_request:window.keep})};result={text:'Synthetic answer',knowledge_sources:[]};}
   else if(url.includes('/dialogs/history'))result={dialog:structuredClone(window.testRoute),messages:structuredClone(window.testMessages)};
   else if(url.includes('/dialogs?'))result={dialogs:[],channels:[],agents:[]};
   else if(url.includes('/api/platform/me'))return {ok:true,json:async()=>window.tenantProfileSnapshot};
   return {ok:true,status:200,json:async()=>({success:true,result})};};
 });
 for(const file of ['docs/tenant-operator-i18n.js','docs/tenant-operator-dialogs.js'])await page.addScriptTag({path:path.join(root,file)});
 const open=()=>page.evaluate(()=>window.tenantOperator.openDialog(window.testRoute));await open();
 const call=async(mode,prompt='',double=false)=>{await page.evaluate(({mode,prompt,double})=>{if(mode==='reply'){document.getElementById('operatorAIReply').click();if(double)document.getElementById('operatorAIReply').click();}else{if(document.getElementById('operatorCopilot').hidden)document.getElementById('operatorCopilotToggle').click();if(mode==='instruction')document.querySelector('[data-copilot-mode="instruction"]').click();const input=document.getElementById('operatorCopilotPrompt');input.value=prompt;input.dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('operatorCopilotAsk').click();if(double)document.getElementById('operatorCopilotAsk').click();}},{mode,prompt,double});await page.waitForFunction(()=>!document.querySelector('[data-operator-dialog]')?.hasAttribute('data-copilot-busy'));return page.evaluate(()=>window.calls.at(-1));};
 const first=await call('reply','',true);assert.equal(await page.evaluate(()=>window.calls.length),1,'busy flag precedes asynchronous SHA work');
 assert.equal((await call('reply')).request_id,first.request_id,'same reply retries keep UUID after unknown');
 const chat=await call('chat','  Synthetic question  ');assert.notEqual(chat.request_id,first.request_id);assert.equal(chat.prompt,'Synthetic question');
 const edit=await call('chat','Changed question');assert.notEqual(edit.request_id,chat.request_id);
 assert.equal((await call('chat','Synthetic question')).request_id,chat.request_id,'return to an unresolved logical request keeps its UUID');
 await open();assert.equal((await call('chat','Synthetic question')).request_id,chat.request_id,'background rerender preserves pending UUID');
 await page.evaluate(()=>{window.failure='lost';});assert.equal((await call('chat','Synthetic question')).request_id,chat.request_id);
 await page.evaluate(()=>{window.keep=undefined;});const omitted=await call('chat','Missing keep flag');assert.equal((await call('chat','Missing keep flag')).request_id,omitted.request_id,'missing flag retains UUID');await page.evaluate(()=>{window.keep=null;});assert.equal((await call('chat','Missing keep flag')).request_id,omitted.request_id,'null flag cannot release UUID');await page.evaluate(()=>{window.keep=true;});
 await page.evaluate(()=>{window.failure='unknown';window.testMessages.push({id:'incoming-b',external_id:'connect:b',source:'connect',direction:'incoming',message_type:'image',body:'',author_type:'customer'});});await open();
 const media=await call('reply');assert.notEqual(media.request_id,first.request_id);assert.equal(media.include_media,true);
 const newMessage=await call('chat','Synthetic question');assert.notEqual(newMessage.request_id,chat.request_id);
 await page.evaluate(()=>window.testRoute.agent_version++);await open();assert.notEqual((await call('chat','Synthetic question')).request_id,newMessage.request_id);
 await page.evaluate(()=>{window.testRoute.conversation_id='client-b';window.testRoute.lead_id=42;});await open();const other=await call('chat','Synthetic question');assert.notEqual(other.request_id,newMessage.request_id);
 await page.evaluate(()=>{window.tenantProfileSnapshot.member={...window.tenantProfileSnapshot.member,telegram_id:12};document.dispatchEvent(new CustomEvent('tenant-profile',{detail:window.tenantProfileSnapshot}));});await open();const actor=await call('chat','Synthetic question');assert.notEqual(actor.request_id,other.request_id);
 await page.evaluate(()=>{window.keep=false;});const safe=await call('chat','Safe terminal request');assert.notEqual((await call('chat','Safe terminal request')).request_id,safe.request_id,'known safe failure releases only its own identity');
 await page.evaluate(()=>{window.failure='success';});const success=await call('chat','Known success');assert.notEqual((await call('chat','Known success')).request_id,success.request_id,'fresh explicit completed operation gets a fresh UUID');
 const instruction=await call('instruction','Synthetic question');assert.equal(instruction.mode,'instruction');assert.notEqual(instruction.request_id,chat.request_id);
 await page.evaluate(()=>{window.failure='unknown';window.keep=true;window.tenantProfileSnapshot.role_permissions={dialogs:true,revision:2};document.dispatchEvent(new CustomEvent('tenant-profile',{detail:window.tenantProfileSnapshot}));window.calls=[];});await open();
 const ids=[];for(let i=0;i<21;i++){const response=await call('chat','Pending '+i);if(i<20)ids.push(response.request_id);}
 assert.equal(await page.evaluate(()=>window.calls.length),20,'bounded pending map refuses a new operation rather than evicting uncertainty');
 assert.equal((await call('chat','Pending 0')).request_id,ids[0],'full map still allows exact idempotent retry');
 assert.equal(new Set(ids).size,20);
 assert.equal(await page.evaluate(()=>Object.keys(sessionStorage).some(k=>/copilot.*request|pending.*copilot/i.test(k))),false,'request map is memory-only');
 console.log('PASS helper request identity '+width+': stable retry, mode/prompt/media/context/new-message scopes, rerender, double-click, actor purge and bounded uncertainty');await page.close();
}}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
