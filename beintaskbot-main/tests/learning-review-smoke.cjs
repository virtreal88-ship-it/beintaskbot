// Actual /app and shared message/media renderer; all customer data is synthetic.
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const {spawn}=require('node:child_process'),readline=require('node:readline'),assert=require('node:assert/strict'),path=require('node:path');
const server=spawn('python',['-u','-c',`from aiohttp import web
from pathlib import Path
from tenant_web_assets import register_assets
import asyncio
async def main():
 app=web.Application();register_assets(app);app.router.add_get('/app',lambda r:web.FileResponse(Path('docs/tenant-app.html')))
 runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start();print(site._server.sockets[0].getsockname()[1],flush=True);await asyncio.Event().wait()
asyncio.run(main())`],{cwd:path.resolve(__dirname,'..'),stdio:['ignore','pipe','inherit']});
(async()=>{let browser;try{const port=await new Promise(r=>readline.createInterface({input:server.stdout}).once('line',r));browser=await chromium.launch({headless:true});let passed=0;
for(const language of ['ru','az','en'])for(const theme of ['light','dark'])for(const width of [1440,390]){
 const page=await browser.newPage({viewport:{width,height:900}}),writes=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(({language,theme})=>{localStorage.setItem('tenant.site.lang',language);localStorage.setItem('tenant-app-theme',theme)},{language,theme});
 await page.route('**/api/**',async route=>{const req=route.request(),u=new URL(req.url());let payload={success:true,items:[],rows:[],tasks:[],deals:[],channels:[],companies:[],integrations:[],sessions:[],events:[],requests:[],members:[],result:{}};
 if(u.pathname==='/api/platform/me')payload={success:true,member:{tenant_id:'tenant-a',telegram_id:1,role:'owner',active:true},capabilities:{modules:{ai_agents:true,settings:true,tasks:true,deals:true,customers:true,companies:true,profile:true}},role_permissions:{},account:{},integrations:{}};
 else if(u.pathname.endsWith('/simulation/issues'))payload={success:true,result:{items:[{run_id:'run-1',category:'knowledge',reason:'retrieval_empty',count:1}]}};
 else if(u.pathname.endsWith('/administrator/approvals')){
  if(req.method()==='POST'){writes.push(req.postDataJSON());payload={success:true,result:{id:'draft-1',version:1,state:'draft',body:{content:'Owner resolution'}}};}
  else if(u.searchParams.has('review_run_id'))payload={success:true,result:{run_id:'run-1',source_message_id:'message-1',route:{provider:'connect',lead_id:41,channel_id:'channel-a',conversation_id:'customer'},findings:[{reason:'retrieval_empty'}],outcome:'virtual_reply',virtual_reply:'Unverified answer',messages:[{id:'message-1',external_id:'connect:m1',body:'Printer fails',direction:'incoming',author_type:'customer',message_type:'text',source:'connect',created_at:'2026-10-24T10:00:00Z',review_phase:'trigger'},{id:'message-2',external_id:'connect:m2',source_lead_id:41,body:'Later operator image',direction:'outgoing',author_type:'operator',message_type:'image',source:'connect',created_at:'2026-10-24T10:01:00Z',review_phase:'later'}]}};
  else payload={success:true,result:{items:[]}};
 }else if(u.pathname.endsWith('/chat/attachment')&&u.searchParams.get('message_id')==='connect:available'){await route.fulfill({status:200,contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=','base64')});return;}else if(u.pathname.endsWith('/chat/attachment')){await route.fulfill({status:404,json:{success:false,error:'Unavailable'}});return;}
 else if(u.pathname.endsWith('/agents'))payload={success:true,result:[]};else if(u.pathname.endsWith('/dialogs'))payload={success:true,result:{dialogs:[]}};else if(u.pathname.endsWith('/channels'))payload={success:true,result:{channels:[]}};
 if(payload.result?.messages)payload.result.messages.push({id:'message-3',external_id:'connect:available',source_lead_id:41,body:'Available image',direction:'incoming',author_type:'customer',message_type:'image',source:'connect',created_at:'2026-10-24T10:02:00Z',review_phase:'later'});await route.fulfill({json:payload});});
 await page.goto(`http://127.0.0.1:${port}/app?view=ai_administrator&site_lang=${language}`);await page.evaluate(()=>document.querySelector('#aiAdministratorNav').click());
 await page.locator('[data-review-select] option[value="run-1"]').waitFor({state:'attached'});await page.selectOption('[data-review-select]','run-1');await page.locator('[data-review-trigger]').waitFor({state:'attached'});await page.waitForFunction(()=>document.querySelector('[data-learning-chat]')?.textContent.includes('Later operator image'));
 assert.ok((await page.locator('[data-learning-chat]').textContent()).includes('Later operator image'));  
 await page.fill('[data-review-explanation]','Owner resolution');await page.click('[data-review-prepare]');await page.locator('[data-review-request]').waitFor({state:'visible'});assert.equal(writes.length,1);assert.equal(writes[0].action,'prepare_review_solution');assert.equal(writes[0].run_id,'run-1');
 if(width===390)await page.click('[data-review-tab="chat"]');
 await page.locator('[data-learning-chat] img[data-media-preview]').waitFor({state:'attached'});const media=page.locator('[data-learning-chat] [role="status"]').first();await media.waitFor();await page.waitForFunction(()=>document.querySelector('[data-learning-chat] [role="status"]').textContent.trim().length>0);
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'horizontal overflow');assert.deepEqual(errors,[]);if(language==='ru'&&theme==='light')await page.screenshot({path:width===1440?'learning-review-desktop.png':'learning-review-mobile.png',fullPage:true});passed++;await page.close();
}console.log(`${passed} Chromium Learning review cases passed`);
}finally{await browser?.close();server.kill()}})().catch(e=>{console.error(e);server.kill();process.exitCode=1});
