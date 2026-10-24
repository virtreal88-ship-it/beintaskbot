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
const source={id:'source-1',tenant_id:'tenant-a',agent_id:null,title:'Temporary guidance',kind:'text',content_hash:'same',active:true,origin_type:'knowledge_source',origin_id:null,updated_at:'2026-10-24T08:00:00Z'};
(async()=>{let browser;try{const port=await new Promise(resolve=>readline.createInterface({input:server.stdout}).once('line',resolve));browser=await chromium.launch({headless:true});let cases=0;
for(const lang of (process.env.LANGS||'ru,az,en').split(','))for(const theme of (process.env.THEMES||'light,dark').split(','))for(const width of (process.env.WIDTHS||'1440,390').split(',').map(Number)){
 source.active=true;source.provenance=undefined;const writes=[],errors=[];const page=await browser.newPage({viewport:{width,height:900}});page.on('pageerror',error=>errors.push(error.stack||error.message));
 await page.addInitScript(({lang,theme})=>{localStorage.setItem('tenant.site.lang',lang);localStorage.setItem('tenant-app-theme',theme)},{lang,theme});
 await page.route('**/api/**',async route=>{const request=route.request(),u=new URL(request.url());let payload={success:true,result:{}};
  if(u.pathname==='/api/platform/me')payload={success:true,member:{tenant_id:'tenant-a',telegram_id:1,role:'owner',display_name:'Demo owner',active:true},capabilities:{modules:{tasks:true,deals:true,customers:true,companies:true,settings:true,profile:true,ai_agents:true}},role_permissions:{},account:{},integrations:{}};
  else if(u.pathname==='/api/platform/ai/knowledge')payload={success:true,result:[source]};
  else if(u.pathname==='/api/platform/instructions')payload={success:true,result:{items:[],next_offset:null}};
  else if(u.pathname==='/api/platform/ai/knowledge-candidates')payload={success:true,result:{candidates:[],next_before:null}};
  else if(u.pathname==='/api/platform/ai/agents')payload={success:true,result:[]};
  else if(u.pathname==='/api/platform/ai/knowledge/source-1'&&request.method()==='DELETE'){writes.push({method:request.method(),body:request.postDataJSON()});source.active=false;source.provenance={archive:{kind:'manual'}};payload={success:true,result:{archived:true}};}
  else if(u.pathname==='/api/platform/ai/knowledge/source-1/restore'&&request.method()==='POST'){writes.push({method:request.method(),body:request.postDataJSON()});source.active=true;source.provenance=undefined;payload={success:true,result:{restored:true}};}
  else if(u.pathname.endsWith('/dialogs'))payload={success:true,result:{dialogs:[]}};
  else if(u.pathname.endsWith('/channels'))payload={success:true,result:{channels:[]}};
  await route.fulfill({json:payload});
 });
 await page.goto(`http://127.0.0.1:${port}/app?view=knowledge_center&site_lang=${lang}&source_id=source-1`);
 await page.evaluate(()=>document.querySelector('#knowledgeCenterNav').click());
 const remove=page.locator('[data-kc-archive="source-1"]');await remove.waitFor();page.once('dialog',dialog=>dialog.accept());await remove.click();
 await page.waitForFunction(()=>!new URL(location.href).searchParams.has('source_id'));assert.equal(writes.length,1);assert.deepEqual(writes[0],{method:'DELETE',body:{expected_tenant_id:'tenant-a',expected_user_id:1}});
 assert.equal(await page.locator('.kc-card').count(),0,'archived source stays hidden from the normal list');
 const status=page.locator('#kcStatus');await status.selectOption('trash');const restore=page.locator('[data-kc-archive="source-1"]');await restore.waitFor();await restore.click();
 assert.equal(writes.length,2);assert.deepEqual(writes[1],{method:'POST',body:{expected_tenant_id:'tenant-a',expected_user_id:1}});
 await page.locator('.kc-card').waitFor();assert.ok(await page.locator('#knowledgeCenter').isVisible(),'Knowledge Center remains usable after the archive/restore cycle');assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'horizontal overflow');cases++;await page.close();
}
console.log(`${cases} Chromium Knowledge Center archive/restore cases passed`);
}finally{await browser?.close();server.kill()}})().catch(error=>{console.error(error);process.exitCode=1;server.kill()});
