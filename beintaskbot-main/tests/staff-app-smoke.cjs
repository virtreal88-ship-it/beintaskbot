// Actual /app HTML + production aiohttp asset registry, synthetic API data only.
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const {spawn}=require('node:child_process'),readline=require('node:readline'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const root=path.resolve(__dirname,'..');
const server=spawn('python',['-u','-c',`from aiohttp import web
from pathlib import Path
from tenant_web_assets import register_assets
import asyncio
async def main():
 app=web.Application();register_assets(app)
 app.router.add_get('/app',lambda r:web.FileResponse(Path('docs/tenant-app.html')))
 runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
 print(site._server.sockets[0].getsockname()[1],flush=True)
 await asyncio.Event().wait()
asyncio.run(main())`],{cwd:root,stdio:['ignore','pipe','inherit']});
const labels={ru:['Сотрудники','+ Добавить сотрудника','Редактировать','Права'],az:['Əməkdaşlar','+ Əməkdaş əlavə et','Redaktə et','Hüquqlar'],en:['Employees','+ Add employee','Edit','Permissions']};
(async()=>{let browser;try{const port=await new Promise(resolve=>readline.createInterface({input:server.stdout}).once('line',resolve));browser=await chromium.launch();let cases=0;
for(const lang of ['ru','az','en'])for(const theme of ['light','dark'])for(const width of [1440,390]){
 const page=await browser.newPage({viewport:{width,height:900}}),errors=[],staffAssets=[],writes=[];let profileCalls=0;
 const selectSettings=async key=>{const button=page.locator(`[data-settings-tab="${key}"]`);if(await button.isVisible())await button.click();else await page.locator('#view-settings > .app-tab-picker').selectOption({label:await button.textContent()})};
 page.on('pageerror',e=>errors.push(e.stack));page.on('response',r=>{if(r.url().includes('tenant-members-ui.'))staffAssets.push(r.status())});
 await page.addInitScript(({lang,theme})=>{localStorage.setItem('tenant.site.lang',lang);localStorage.setItem('tenant-app-theme',theme)},{lang,theme});
 await page.route('**/api/**',async route=>{let u=new URL(route.request().url());assert.equal(u.origin,`http://127.0.0.1:${port}`);if(route.request().method()!=='GET')writes.push(u.pathname);
 const member={tenant_id:'synthetic-tenant',telegram_id:1,role:'owner',display_name:'Demo owner',active:true};let payload={success:true,items:[],rows:[],tasks:[],deals:[],channels:[],companies:[],integrations:[],sessions:[],events:[],requests:[],members:[member,{...member,telegram_id:2,role:'worker',display_name:'Demo operator',email:'demo@example.test',whatsapp_numbers:[]}]};
 if(u.pathname.startsWith('/api/platform/ai/'))payload.result=/\/(agents|channels)$/.test(u.pathname)?[]:u.pathname.endsWith('/agents/options')?{channels:[],pipelines:[],stages:[],models:[]}:u.pathname.endsWith('/support-tickets')?{tickets:[],members:[],channels:[]}:{};
 if(u.pathname==='/api/platform/me'){profileCalls++;payload={...payload,member,capabilities:{can_manage_employees:true,modules:{settings:true,profile:true,tasks:true,deals:true,customers:true,ai_agents:false}},role_permissions:{},account:{},integrations:{}}}
 await route.fulfill({json:payload})});
 await page.goto(`http://127.0.0.1:${port}/app?view=settings&site_lang=${lang}`);
 const staff=page.locator('#staffManagement');await staff.locator('[data-staff-edit="2"]').waitFor();
 await page.evaluate(({lang})=>{window.tenantI18n.language=lang;document.documentElement.lang=lang;document.dispatchEvent(new CustomEvent('tenant-language',{detail:{language:lang}}))},{lang});
 await selectSettings('employees');assert.ok(await staff.isVisible());assert.equal(await staff.count(),1);assert.ok(await staff.evaluate(el=>el.parentElement.id==='view-settings'));
 assert.ok((await staff.locator('#staffAdd').boundingBox()).y<450,'employee actions start near the top');
 const opener=staff.locator('[data-staff-edit="2"]');await opener.click();await page.locator('[data-pane="numbers"]').click();await page.locator('[data-pane="rights"]').click();await page.locator('[data-pane="profile"]').click();await page.locator('[data-staff-cancel]').click();assert.ok(await opener.evaluate(el=>el===document.activeElement));
 await staff.locator('#staffAdd').click();await page.locator('#staffInviteForm input[name="display_name"]').fill('Unsaved demo');page.once('dialog',d=>d.accept());await page.keyboard.press('Escape');assert.ok(!await page.locator('#staffMemberModal').evaluate(el=>el.classList.contains('show')));
 await staff.locator('#staffAdd').click();await page.locator('[data-staff-close]').first().click();
 await staff.locator('#staffAdd').click();await page.locator('#staffMemberModal').click({position:{x:3,y:3}});assert.ok(!await page.locator('#staffMemberModal').evaluate(el=>el.classList.contains('show')));
 await selectSettings('advanced');await selectSettings('employees');assert.ok(await staff.isVisible());
 await page.reload();await staff.locator('[data-staff-edit="2"]').waitFor();await selectSettings('employees');assert.ok(await staff.isVisible());
 assert.ok(profileCalls>=2);assert.ok(staffAssets.length>=4&&staffAssets.every(s=>s===200||s===304));assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'horizontal overflow');
 assert.equal(await page.locator('html').getAttribute('data-app-theme'),theme);
 assert.equal(await staff.locator('#staffAdd').innerText(),labels[lang][1]);
 const out=path.join(root,'.support-test-deps/staff-smoke');fs.mkdirSync(out,{recursive:true});await page.screenshot({path:path.join(out,`${lang}-${theme}-${width}.png`),fullPage:true});cases++;await page.close();
}console.log(`${cases} full /app Chromium cases passed`);
}finally{await browser?.close();server.kill()}})().catch(e=>{console.error(e);process.exitCode=1;server.kill()});
