/* Actual app renderer, synthetic authorized route bindings, real Chromium. */
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const {docs,assets,appHtml,installNotificationCaseFixture}=require('./notification_case_targets_fixture.cjs');
function installAliasFixture(mode){
 const f=window.notificationCaseFixture,base=window.fetch;
 f.historyCalls=[];f.casePages=[];f.historyMode=mode;
 window.fetch=async(input,options={})=>{
  const url=new URL(String(input),location);
  if(url.pathname.endsWith('/dialogs/history')){f.historyCalls.push(String(url));throw Error('Unbounded history must never be requested');}
  const response=await base(input,options);
  if(!url.pathname.startsWith('/api/platform/ai/support-tickets/')||!response.ok)return response;
  const data=await response.json();if(!data.result?.ticket)return new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
  const earlier=url.searchParams.has('before');f.casePages.push({path:url.pathname,before:url.searchParams.get('before')});
  if(earlier&&mode==='rejected')return new Response(JSON.stringify({success:false,error:'Case history access revoked'}),{status:403,headers:{'Content-Type':'application/json'}});
  data.result.messages=Array.from({length:earlier?20:50},(_,index)=>({id:index===0?(earlier?'older-case-message':'case-message'):(earlier?'older-':'current-')+index,direction:'incoming',author_name:'Customer',body:(earlier?'Earlier accepted-interval message ':'Pinned case message ')+index,message_type:'text',happened_at:'2026-11-02T10:00:00Z'}));
  data.result.next_before=earlier?null:'case-cursor';data.result.history_context={boundary_status:mode==='fallback'?'boundary_unknown':'accepted_interval',accepted_at:mode==='fallback'?null:'2026-11-02T09:00:00Z',created_at:'2026-11-02T12:00:00Z',fallback:mode==='fallback'?'saved_materials':null};
  return new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
 };
}
(async()=>{
 const server=http.createServer((req,res)=>{const u=new URL(req.url,'http://test');if(u.pathname.startsWith('/assets/')){const name=path.basename(u.pathname);if(!assets.includes(name)){res.writeHead(404);res.end();return;}res.setHeader('content-type','text/javascript');res.end(fs.readFileSync(path.join(docs,name)));return;}res.setHeader('content-type','text/html');res.end(appHtml());});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||process.env.CHROMIUM_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':fs.existsSync('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe')?'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe':undefined)});
 async function mount(mode){
  const context=await browser.newContext({viewport:{width:390,height:900}}),page=await context.newPage(),errors=[];
  await context.addInitScript(installNotificationCaseFixture);await context.addInitScript(installAliasFixture,mode);page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
  await page.goto(origin+'/app?view=support&lang=ru');await page.locator('[data-support-id]').first().click();
  await page.locator('#supportHistory [data-message-id=case-message]').waitFor();
  return {context,page,errors};
 }
 try{
  for(const mode of ['accepted','fallback','rejected']){const {context,page,errors}=await mount(mode);try{
   assert.equal(await page.locator('#supportHistory [data-message-id=case-message]').count(),1);
   assert.equal(await page.locator('#supportDialogueHistory,#supportDialogueRefresh').count(),0,'retired full-dialogue history controls are absent');
   if(mode==='fallback')assert.match(await page.locator('#supportPanel-history').textContent(),/Время принятия не сохранено/);
   await page.locator('#supportEarlier').click();
   if(mode==='rejected'){await page.locator('#supportDialogueNotice').filter({hasText:'Case history access revoked'}).waitFor();assert.equal(await page.locator('#supportHistory [data-message-id]').count(),50,'denied pagination does not append or erase the accepted page');}
   else{await page.locator('#supportHistory [data-message-id=older-case-message]').waitFor();assert.equal(await page.locator('#supportHistory [data-message-id]').count(),70,'entire accepted cycle exceeding 50 is retained without truncation');assert.equal(await page.locator('#supportEarlier').isVisible(),false);}
   const pages=await page.evaluate(()=>notificationCaseFixture.casePages);assert.equal(pages.length,2);assert.equal(pages[1].path,pages[0].path,'pagination remains bound to the exact case ID');assert.equal(pages[1].before,'case-cursor');
   assert.equal(await page.evaluate(()=>notificationCaseFixture.historyCalls.length),0,'alias expansion cannot escape the case snapshot');assert.deepEqual(errors,[]);
  }finally{await context.close();}}
  console.log('PASS bounded case history: exact-case cursor, saved-material fallback, denied pagination, no unrestricted alias fetch');
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
