// Actual /app assets in Chromium; all tenant data and mutations are synthetic.
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const {spawn}=require('node:child_process'),readline=require('node:readline'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const server=spawn('python',['-u','-c',`from aiohttp import web
from pathlib import Path
from tenant_web_assets import register_assets
import asyncio
async def main():
 app=web.Application();register_assets(app);app.router.add_get('/app',lambda r:web.FileResponse(Path('docs/tenant-app.html')))
 runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start();print(site._server.sockets[0].getsockname()[1],flush=True);await asyncio.Event().wait()
asyncio.run(main())`],{cwd:root,stdio:['ignore','pipe','inherit']});
const candidate={id:'case-1',version:4,status:'pending_review',title:'Demo support case',symptoms:['Demo symptom'],resolution_steps:['Demo step'],cause:{status:'suspected',text:'Demo cause'},outcome:{status:'unconfirmed',text:'Pending',message_ids:[]},source_route:{provider:'connect',channel_id:'channel-1',conversation_id:'conversation-1'},source_message_ids:[],operator_actions:[],publications:[],created_at:'2026-10-24T08:00:00Z',updated_at:'2026-10-24T08:00:00Z',can_approve:false,approval_block_reason:'confirmation_required',work_status:'unknown',case_kind:'technical',case_relation:'new_request'};
(async()=>{let browser;try{const port=await new Promise(resolve=>readline.createInterface({input:server.stdout}).once('line',resolve));browser=await chromium.launch({headless:true});let cases=0;
for(const lang of (process.env.LANGS||'ru,az,en').split(','))for(const theme of (process.env.THEMES||'light,dark').split(','))for(const width of (process.env.WIDTHS||'1440,390').split(',').map(Number)){
 candidate.status='pending_review';candidate.version=4;
 const page=await browser.newPage({viewport:{width,height:900}}),errors=[],writes=[];page.on('pageerror',error=>errors.push(error.stack||error.message));
 await page.addInitScript(({lang,theme})=>{localStorage.setItem('tenant.site.lang',lang);localStorage.setItem('tenant-app-theme',theme)},{lang,theme});
 await page.route('**/api/**',async route=>{const request=route.request(),u=new URL(request.url());let payload={success:true,items:[],rows:[],tasks:[],deals:[],channels:[],companies:[],integrations:[],sessions:[],events:[],requests:[],members:[],result:{}};
   if(u.pathname==='/api/platform/me')payload={success:true,member:{tenant_id:'demo-tenant',telegram_id:1,role:'owner',display_name:'Demo owner',active:true},capabilities:{modules:{tasks:true,deals:true,customers:true,companies:true,settings:true,profile:true,ai_agents:true}},role_permissions:{},account:{},integrations:{}};
   else if(u.pathname.endsWith('/knowledge-candidates'))payload={success:true,result:{candidates:[candidate],next_before:null}};
   else if(u.pathname.endsWith('/knowledge-candidates/case-1'))payload={success:true,result:{candidate,evidence:[],route:candidate.source_route}};
   else if(u.pathname.endsWith('/knowledge-candidates/case-1/archive')){writes.push({path:u.pathname,body:request.postDataJSON()});candidate.status='archived';candidate.version++;payload={success:true,result:{candidate,evidence:[],route:candidate.source_route}};}
   else if(u.pathname.endsWith('/dialogs'))payload={success:true,result:{dialogs:[]}};
   else if(u.pathname.endsWith('/agents'))payload={success:true,result:[]};
   else if(u.pathname.endsWith('/channels'))payload={success:true,result:{channels:[]}};
   await route.fulfill({json:payload});
 });
 await page.goto(`http://127.0.0.1:${port}/app?view=support_cases&site_lang=${lang}`);await page.evaluate(()=>document.querySelector('#supportCasesNav').click());
 await page.locator('[data-case-open="case-1"]').waitFor();await page.locator('[data-case-open="case-1"]').click();
 const archive=page.locator('[data-case-archive]');await archive.waitFor();assert.ok(await archive.isVisible());page.once('dialog',dialog=>dialog.accept());await archive.click();
 await page.locator('#supportCaseModal').waitFor({state:'detached',timeout:5000});assert.equal(writes.length,1);assert.equal(writes[0].path,'/api/platform/ai/knowledge-candidates/case-1/archive');assert.deepEqual(writes[0].body,{expected_tenant_id:'demo-tenant',expected_user_id:1,expected_version:4});
 assert.deepEqual(errors,[]);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'horizontal overflow');cases++;await page.close();
}console.log(`${cases} Chromium support-case trash cases passed`);
}finally{await browser?.close();server.kill()}})().catch(error=>{console.error(error);process.exitCode=1;server.kill()});
