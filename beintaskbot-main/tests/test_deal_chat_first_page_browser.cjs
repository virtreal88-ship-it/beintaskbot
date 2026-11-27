const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),cp=require('node:child_process');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const {installFixture}=require('./task_read_save_flow_fixture.cjs');
const baseline=process.env.BASELINE==='1',baselineRef=process.env.BASELINE_REF||'49b639b0';
const source=file=>baseline?cp.execFileSync('git',['show',baselineRef+':beintaskbot-main/'+file],{encoding:'utf8',maxBuffer:6e6}):fs.readFileSync(file,'utf8');
const dir='tests/artifacts/deal-first-page/'+(baseline?'before':'after');fs.mkdirSync(dir,{recursive:true});
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||process.env.CHROMIUM_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});const metrics=[];
try{for(const width of [390,1440])for(const manager of [false,true]){
 const context=await browser.newContext({viewport:{width,height:844}});await context.tracing.start({screenshots:true,snapshots:true});const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(installFixture,{manager});
 await page.addInitScript(()=>{
  const f=taskFlowFixture,original=fetch;f.profile.capabilities.modules.deals=true;f.profile.capabilities.modules.dialogs=true;
  window.testChat={calls:[],denied:false,fail:false,hold:false,releases:[],delay:1200,length:120,rows:[],loaded:0};
  testChat.rows=Array.from({length:120},(_,i)=>({external_id:String(i+1).padStart(4,'0'),body:'Synthetic message '+(i+1),channel:'allowed-source',direction:'incoming',message_type:'text',happened_at:'2026-11-16T00:00:00Z'}));
  const success=data=>new Response(JSON.stringify({success:true,...data}),{headers:{'Content-Type':'application/json'}});
  document.addEventListener('tenant-chat-loaded',e=>{testChat.loaded=performance.now();testChat.count=e.detail.messages.length;testChat.reload=e.detail.reload_cache;});
  window.fetch=async(url,opt={})=>{
   const u=new URL(String(url),location.href),p=u.pathname;
   if(!['/api/platform/crm/deal','/api/platform/crm/chat','/api/platform/crm/chat/history','/api/platform/crm/deal/notes'].includes(p))return original(url,opt);
   if(opt.method&&opt.method!=='GET')throw Error('Unexpected mutation');
   testChat.calls.push(p+u.search);const id=u.searchParams.get('lead_id');
   if(p.endsWith('/deal')){await new Promise(r=>setTimeout(r,id==='9'?700:120));return success({deal:{kommo_lead_id:Number(id),name:'Synthetic deal '+id,contact_name:'Synthetic client '+id,pipeline_id:10,status_id:100,raw:{source:'kommo'},phone:''}});}
   if(p.endsWith('/notes'))return success({notes:[]});
   if(p.endsWith('/chat')){if(testChat.hold)await new Promise(r=>testChat.releases.push(r));else await new Promise(r=>setTimeout(r,testChat.delay));}
   else await new Promise(r=>setTimeout(r,80));
   if(testChat.denied)return new Response(JSON.stringify({success:false,error:'Access revoked'}),{status:403});
   if(p.endsWith('/chat')&&testChat.fail)return new Response(JSON.stringify({success:false,error:'Provider unavailable'}),{status:503});
   const all=testChat.rows.slice(0,testChat.length),cursor=u.searchParams.get('cursor'),limit=Number(u.searchParams.get('limit')||120);
   const eligible=cursor?all.filter(m=>m.external_id<cursor):all,rows=eligible.slice(-limit).map(m=>({...m,body:m.body+' deal '+id}));
   return success({messages:rows,next_cursor:eligible.length>limit?rows[0].external_id:null});
  };
 });
 const names=['tenant-operator-i18n.js','tenant-request-coordinator.js','tenant-view-demand.js','tenant-task-flow.js','tenant-tasks.js','tenant-task-completion.js','tenant-kommo-deal-card.js','tenant-chat-history.js','tenant-deal-chat.js'];
 let html=source('docs/tenant-app.html').replace(/<script\b[^>]*src="([^"]+)"[^>]*><\/script>/gi,(tag,url)=>names.includes(path.basename(url))?'<script>'+source('docs/'+path.basename(url)).replace(/<\/script/gi,'<\\/script')+'</script>':'');
 html=html.replace('</body>',()=>'<script>'+source('docs/tenant-chakra-ui.js').replace(/<\/script/gi,'<\\/script')+'</script></body>');
 await page.route('**/*',r=>r.request().isNavigationRequest()?r.fulfill({contentType:'text/html',body:html}):r.abort());await page.goto('https://fixture.test/app?view=deals&lang=ru');
 await page.waitForFunction(()=>window.taskFlowFixture&&document.getElementById('company').textContent!=='');await page.waitForTimeout(200);
 if(!baseline){
  // An unrelated hidden API-key input must not share the search form. Enter is local filtering, never navigation.
  assert(await page.locator('#dealSearch').evaluate(n=>!!n.form&&!Array.from(n.form.elements).some(e=>e.type==='password')));
  const initialUrl=page.url();await page.evaluate(()=>window.searchPageSentinel=true);
  await page.locator('#dealSearch').focus();await page.locator('#dealSearch').fill('Synthetic');await page.locator('#dealSearch').press('Enter');
  assert.equal(page.url(),initialUrl);assert(await page.evaluate(()=>window.searchPageSentinel));
  assert.equal(await page.locator('#dealSearch').inputValue(),'Synthetic');await page.locator('#dealSearch').focus();await page.locator('#dealSearch').fill('');
 }
 await page.evaluate(()=>{window.tenantChatSend={bind(){}};for(const id of [7,8,9]){const b=document.createElement('button');b.id='fixtureOpen'+id;b.textContent='Open '+id;b.style.cssText='position:fixed;z-index:99999;top:0;left:'+((id-7)*90)+'px';b.onclick=()=>{testChat.started=performance.now();testChat.loaded=0;document.dispatchEvent(new CustomEvent('tenant-duplicate-open',{detail:{lead_id:id}}));};document.body.append(b);}});
 async function open(id=7){await page.locator('#fixtureOpen'+id).click();await page.locator('#dealModal.show').waitFor();const shell=await page.evaluate(()=>performance.now()-testChat.started);await page.waitForFunction(()=>testChat.loaded>0);if(!baseline&&width<900)await page.locator('[data-deal-pane=chat]').click();return {shell_ms:Math.round(shell),messages_ms:Math.round(await page.evaluate(()=>testChat.loaded-testChat.started)),rows:await page.evaluate(()=>testChat.count)};}
 for(const length of [14,120])for(const repeat of [false,true]){await page.evaluate(n=>{testChat.length=n;testChat.calls=[];},length);const result=await open();metrics.push({width,role:manager?'limited':'owner',length,repeat,...result});if(!baseline){assert.equal(result.rows,Math.min(length,40));assert(result.messages_ms<1000,'saved history must not wait for 1200ms provider');assert.equal(await page.evaluate(()=>testChat.calls.filter(p=>p.includes('/notes')).length),0);assert(await page.evaluate(()=>{const v=document.querySelector('.deal-chat-history');return v.scrollHeight-v.clientHeight-v.scrollTop<3;}),'initial view at latest messages');}await page.locator('#closeModal').click();}
 if(!baseline){
  await page.evaluate(()=>{testChat.hold=true;testChat.length=120;testChat.calls=[];});await open();await page.waitForFunction(()=>testChat.releases.length>0);
  await page.locator('#chatText').fill('Unsaved draft');await page.evaluate(()=>{window.originalRow=document.querySelector('[data-chat-id="0120"]');const audio=document.createElement('audio');audio.id='fixtureAudio';originalRow.append(audio);window.originalAudio=audio;document.querySelector('.deal-chat-history').scrollTop=0;});
  await page.waitForFunction(()=>document.querySelectorAll('#chatRows [data-chat-id]').length===80);
  const anchor=await page.evaluate(()=>{const v=document.querySelector('.deal-chat-history'),n=[...document.querySelectorAll('[data-chat-id]')].find(n=>n.getBoundingClientRect().bottom>v.getBoundingClientRect().top);window.readingNode=n;return n.getBoundingClientRect().top;});
  await page.evaluate(()=>{testChat.rows.push({...testChat.rows[0],external_id:'0121',body:'New message'});testChat.length=121;testChat.releases.shift()();});await page.waitForFunction(()=>!!document.querySelector('[data-chat-id="0121"]'));
  assert.equal(await page.locator('#chatText').inputValue(),'Unsaved draft');assert(await page.evaluate(()=>originalRow.isConnected&&originalAudio===document.getElementById('fixtureAudio')));assert(Math.abs(await page.evaluate(()=>readingNode.getBoundingClientRect().top)-anchor)<1.1,'reading anchor retained within subpixel rounding');
  await page.locator('#chatHistoryPages button').click();await page.waitForFunction(()=>document.querySelectorAll('#chatRows [data-chat-id]').length===121);assert.equal(await page.evaluate(()=>new Set([...document.querySelectorAll('#chatRows [data-chat-id]')].map(n=>n.dataset.chatId)).size),121);
  await page.locator('[data-deal-tab=feed]').click();await page.waitForFunction(()=>testChat.calls.some(p=>p.includes('/notes')));await page.locator('[data-deal-tab=chat]').click();await page.locator('[data-deal-tab=feed]').click();assert.equal(await page.evaluate(()=>testChat.calls.filter(p=>p.includes('/notes')).length),1);await page.locator('#closeModal').click();
  // A late A refresh must not enter B; cancellation is deliberately ignored by fixture transport.
  await page.evaluate(()=>testChat.hold=true);await open(7);await page.waitForFunction(()=>testChat.releases.length>0);await page.evaluate(()=>document.getElementById('fixtureOpen8').click());await page.waitForFunction(()=>document.querySelector('#chatRows')?.textContent.includes('deal 8'));await page.evaluate(()=>testChat.releases.splice(0).forEach(r=>r()));await page.waitForTimeout(100);assert(!await page.locator('#chatRows').textContent().then(s=>s.includes('deal 7')));await page.locator('#closeModal').click();
  // A successful-send cache reload is independent of a slow provider refresh.
  await page.evaluate(()=>{testChat.hold=true;testChat.length=120;testChat.calls=[];});await open();await page.waitForFunction(()=>testChat.releases.length>0);
  await page.evaluate(()=>{window.cacheReloaded=false;testChat.reload().then(()=>cacheReloaded=true);});await page.waitForFunction(()=>cacheReloaded,{},{timeout:800});assert.equal(await page.evaluate(()=>testChat.releases.length),1);await page.locator('#closeModal').click();await page.evaluate(()=>testChat.releases.splice(0).forEach(r=>r()));
  // Non-overlapping refresh must expose a gap, not join disconnected pages.
  await page.evaluate(()=>{testChat.hold=true;testChat.length=120;testChat.calls=[];});await open();await page.waitForFunction(()=>testChat.releases.length>0);await page.locator('#chatText').fill('Keep draft across latest');
  await page.evaluate(()=>{for(let n=122;n<182;n++)testChat.rows.push({...testChat.rows[0],external_id:String(n).padStart(4,'0'),body:'Burst '+n});testChat.length=testChat.rows.length;testChat.releases.splice(0).forEach(r=>r());});
  await page.getByText('Показать последние сообщения',{exact:true}).waitFor();assert.equal(await page.locator('#chatRows [data-chat-id]').count(),40);await page.getByText('Показать последние сообщения',{exact:true}).click();await page.waitForFunction(()=>!!document.querySelector('[data-chat-id="0181"]'));assert.equal(await page.locator('#chatText').inputValue(),'Keep draft across latest');assert.equal(await page.locator('#chatRows [data-chat-id]').count(),40);assert.equal(await page.locator('#chatHistoryPages button').count(),1);await page.locator('#closeModal').click();
  // Closing while deal metadata is slow must never reopen the modal.
  await page.locator('#fixtureOpen9').click();await page.locator('#closeModal').click();await page.waitForTimeout(800);assert.equal(await page.locator('#dealModal.show').count(),0);
  await page.evaluate(()=>{testChat.hold=false;testChat.fail=true;testChat.delay=80;testChat.length=14;});await open();await page.waitForFunction(()=>document.getElementById('modalContent').textContent.includes('неактуальной'));assert.equal(await page.locator('#chatRows [data-chat-id]').count(),14);await page.locator('#closeModal').click();
  await page.evaluate(()=>{testChat.fail=false;testChat.hold=true;});await open();await page.waitForFunction(()=>testChat.releases.length>0);await page.evaluate(()=>{testChat.denied=true;testChat.releases.splice(0).forEach(r=>r());});await page.waitForFunction(()=>document.getElementById('modalContent').textContent.includes('Access revoked'));assert.equal(await page.locator('#chatRows [data-chat-id]').count(),0);await page.locator('#closeModal').click();
  await page.evaluate(()=>{testChat.denied=false;testChat.hold=false;testChat.length=0;});await open();assert.equal(await page.locator('#chatRows [data-chat-id]').count(),0);await page.locator('#closeModal').click();
  // A complete short cache can acquire older stored rows during refresh.
  await page.evaluate(()=>{testChat.rows=Array.from({length:14},(_,i)=>({external_id:String(i+81).padStart(4,'0'),body:'Initially cached',channel:'allowed-source'}));testChat.length=14;testChat.hold=true;});await open();await page.waitForFunction(()=>testChat.releases.length>0);
  await page.evaluate(()=>{testChat.rows=Array.from({length:80},(_,i)=>({external_id:String(i+1).padStart(4,'0'),body:'Older stored',channel:'allowed-source'})).concat(testChat.rows);testChat.length=94;testChat.releases.splice(0).forEach(r=>r());});await page.locator('#chatHistoryPages button').waitFor();assert.equal(await page.locator('#chatRows [data-chat-id]').count(),40);await page.locator('#chatHistoryPages button').click();await page.waitForFunction(()=>document.querySelectorAll('#chatRows [data-chat-id]').length===80);await page.locator('#closeModal').click();await page.evaluate(()=>testChat.hold=false);
  await page.evaluate(()=>testChat.length=14);await open();await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-auth-invalid')));assert.equal(await page.locator('#dealModal.show').count(),0);assert.equal(await page.locator('#chatRows [data-chat-id]').count(),0);
 }
 assert.deepEqual(errors,[]);await page.screenshot({path:path.join(dir,`${width}-${manager}.png`)});await context.tracing.stop({path:path.join(dir,`${width}-${manager}.zip`)});await context.close();
}fs.writeFileSync(path.join(dir,'metrics.json'),JSON.stringify(metrics,null,2));console.log(JSON.stringify(metrics));}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
