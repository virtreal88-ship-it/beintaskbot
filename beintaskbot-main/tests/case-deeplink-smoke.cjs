// Real browser check for tenant-authorized case deep links. API data is synthetic.
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const {spawn}=require('node:child_process'),readline=require('node:readline'),assert=require('node:assert/strict'),path=require('node:path');
const CASE='95d67ee2-f936-44c7-bf6b-2b6347e5a803',MESSAGE='11111111-1111-4111-8111-111111111111',CHANNEL='22222222-2222-4222-8222-222222222222';
const server=spawn('python',['-u','-c',`from aiohttp import web
from pathlib import Path
from tenant_web_assets import register_assets
import asyncio
async def main():
 app=web.Application();register_assets(app);app.router.add_get('/app',lambda r:web.FileResponse(Path('docs/tenant-app.html')))
 runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start();print(site._server.sockets[0].getsockname()[1],flush=True);await asyncio.Event().wait()
asyncio.run(main())`],{cwd:path.resolve(__dirname,'..'),stdio:['ignore','pipe','inherit']});
(async()=>{let browser;try{const port=await new Promise(r=>readline.createInterface({input:server.stdout}).once('line',r));browser=await chromium.launch({headless:true});let passed=0;
for(const language of ['ru','az','en'])for(const width of [1440,390]){
 const page=await browser.newPage({viewport:{width,height:900}}),errors=[];let anchored=false;page.on('pageerror',e=>errors.push(e.stack||e.message));
 await page.addInitScript(language=>localStorage.setItem('tenant.site.lang',language),language);
 await page.route('**/api/**',async route=>{const req=route.request(),u=new URL(req.url());let payload={success:true,result:{}};
  if(u.pathname==='/api/platform/me')payload={success:true,member:{tenant_id:'tenant-a',telegram_id:1,role:'owner',active:true},capabilities:{modules:{ai_agents:true,dialogs:true,settings:true,tasks:true,deals:true,customers:true,companies:true,profile:true}},role_permissions:{},account:{},integrations:{}};
  else if(u.pathname.endsWith('/knowledge-candidates/'+CASE+'/conversation')){if(u.searchParams.get('message_id')!==MESSAGE)throw Error('unexpected conversation link '+u.href);payload={success:true,result:{case_id:CASE,message_id:MESSAGE,review_run_id:null,route:{provider:'connect',lead_id:41,channel_id:CHANNEL,conversation_id:'customer'}}};}
  else if(u.pathname.endsWith('/dialogs/history')){if(u.searchParams.get('anchor')===MESSAGE)anchored=true;payload={success:true,result:{dialog:{provider:'connect',lead_id:41,channel_id:CHANNEL,conversation_id:'customer',customer_name:'Synthetic customer',customer_kind:'contact',channel_name:'Support',controls_allowed:false},anchor_message_id:u.searchParams.get('anchor')===MESSAGE?MESSAGE:null,messages:[{id:MESSAGE,external_id:'connect:anchor',direction:'incoming',author_type:'customer',body:'50 vs 49.9992',message_type:'text',happened_at:'2026-10-25T10:00:00Z'}],next_before:null}};}
  else if(u.pathname.endsWith('/dialogs'))payload={success:true,result:{dialogs:[],channels:[],agents:[],unread_count:0,total_count:0,next_offset:null}};
  else if(u.pathname.endsWith('/simulation/issues'))payload={success:true,result:{items:[]}};
  else if(u.pathname.endsWith('/administrator/approvals'))payload={success:true,result:{items:[]}};
  else if(u.pathname.endsWith('/agents'))payload={success:true,result:[]};else if(u.pathname.endsWith('/channels'))payload={success:true,result:{channels:[]}};
  await route.fulfill({json:payload});});
 const url=`http://127.0.0.1:${port}/app?view=ai_administrator&case_id=${CASE}&message_id=${MESSAGE}&site_lang=${language}`;
 await page.goto(url);await page.locator(`[data-message-id="${MESSAGE}"]`).waitFor({state:'visible'});await page.waitForFunction(()=>document.querySelector('[data-message-id]')?.style.outline.includes('solid'));assert.ok(anchored,'deep link requests history anchored to the case message');assert.ok(await page.locator('#view-dialogs').evaluate(el=>el.classList.contains('active')));assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 await page.reload();await page.locator(`[data-message-id="${MESSAGE}"]`).waitFor({state:'visible'});assert.deepEqual(errors.filter(error=>!error.includes('/assets/tenant-chakra-ui.js')),[]);passed++;await page.close();
}console.log(`${passed} Chromium case deep-link cold-load/reload cases passed`);
}finally{await browser?.close();server.kill()}})().catch(e=>{console.error(e);server.kill();process.exitCode=1});
