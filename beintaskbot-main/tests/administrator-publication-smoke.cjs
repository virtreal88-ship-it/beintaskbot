// Real /app assets, synthetic owner session. Never talks to production.
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
 else if(u.pathname.endsWith('/administrator/approvals')){
  if(req.method()==='POST'){writes.push(req.postDataJSON());payload={success:true,result:{grant_id:'grant-1',state:'granted'}};}
  else if(u.searchParams.has('publication_draft_id'))payload={success:true,result:{draft_id:'draft-1',version:3,kind:'knowledge',materials:[{id:'source-1',material_hash:'exact-server-hash',preview:{title:'Reviewed guidance',content:'Verified ERP facts',version:2}}],agents:[{id:'agent-1',name:'Primary'}],channels:[{id:'channel-1',name:'Test channel'}]}};
  else if(u.searchParams.has('publication_grants'))payload={success:true,result:{items:[{id:'grant-1',kind:'knowledge',object_id:'source-1',state:'granted',expires_at:'2026-10-24T20:00:00Z'}]}};
  else payload={success:true,result:{items:[{id:'receipt-1',draft_id:'draft-1',draft_version:3,kind:'knowledge',title:'Verified guidance',state:'consumed',draft_state:'applied',body:{content:'Verified ERP facts'},target:{}}]}};
 }else if(u.pathname.endsWith('/agents'))payload={success:true,result:[]};else if(u.pathname.endsWith('/dialogs'))payload={success:true,result:{dialogs:[]}};else if(u.pathname.endsWith('/channels'))payload={success:true,result:{channels:[]}};
 await route.fulfill({json:payload});});
 await page.goto(`http://127.0.0.1:${port}/app?view=ai_administrator&site_lang=${language}`);await page.evaluate(()=>document.querySelector('#aiAdministratorNav').click());
 await page.locator('[data-publication-delegate]').click();const editor=page.locator('[data-publication-editor]');await editor.locator('select').first().waitFor();
 assert.ok((await editor.textContent()).includes('Verified ERP facts'));
 if(language==='ru'&&theme==='light'&&width===390)await page.screenshot({path:'publication-owner-mobile.png',fullPage:true});
 const grant=editor.locator('button');assert.ok(await grant.isDisabled());await editor.locator('input[type=checkbox]').check();assert.ok(await grant.isEnabled());await grant.click();
 await page.waitForFunction(()=>!document.querySelector('[data-publication-editor]'));
 assert.equal(writes.length,1);assert.deepEqual(writes[0],{action:'publication_grant',draft_id:'draft-1',version:3,kind:'knowledge',object_id:'source-1',material_hash:'exact-server-hash',agent_id:'agent-1',channel_id:'channel-1',ttl_minutes:30,review_confirmed:true});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'horizontal overflow');assert.deepEqual(errors,[]);
 if(language==='ru'&&theme==='light'&&width===1440)await page.screenshot({path:'publication-owner-smoke.png',fullPage:true});
 passed++;await page.close();
}console.log(`${passed} Chromium owner publication cases passed`);
}finally{await browser?.close();server.kill()}})().catch(e=>{console.error(e);server.kill();process.exitCode=1});
