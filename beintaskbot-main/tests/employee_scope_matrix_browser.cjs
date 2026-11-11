/* Real employee controller/shell/CSS in Chromium; all HTTP data is synthetic. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const {nodeHtml,installCompanyEmployeesFixture,docs}=require('./company_employees_entry_fixture.cjs');
const origin='https://scope-fixture.test';
// Keep browser acceptance on the actual server catalog, including every new
// scoped section, instead of a hand-maintained subset that silently goes stale.
const scopeCatalog=JSON.parse(require('node:child_process').execFileSync(
 process.env.PYTHON||'python',['-c','import json; from tenant_scoped_access import SCOPE_ACTION_CATALOG; print(json.dumps(SCOPE_ACTION_CATALOG))'],{encoding:'utf8'}));
const row={tenant_id:'10000000-0000-4000-8000-000000000101',telegram_id:22,display_name:'Synthetic employee',role:'worker',active:true,permissions:['tasks'],explicit_permissions:['tasks'],card_version:'card-v1',department_id:null,department_catalog:[{id:'10000000-0000-4000-8000-000000000501',name:'Support',active:true}],editable_fields:['department_id','access_settings','work_settings'],access_settings:{version:'scope-v1',scoped_access_enabled:true,scope_catalog:{tasks:['create','view','update','delete'],deals:['create','view','update','delete'],customers:['create','view','update','delete'],companies:['create','view','update','delete'],dialogs:['view','update']},scoped_actions:{tasks:{create:'self',view:'department',update:'self',delete:'none'},deals:{view:'all'},customers:{view:null},companies:{view:'none'},dialogs:{view:'self',update:'department'}},legacy_actions:{customers:{view:true}}},work_settings:{version:'work-v1',values:{ai_enabled:true,notifications:{}},defaults:{},services:[],notifications:[{key:'task_assigned',label:'Task',channels:['push']}]}};
row.access_settings.scope_catalog=scopeCatalog;
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
 try{for(const [width,language] of [[360,'ru'],[390,'az'],[768,'en'],[1440,'ru']]){
  const context=await browser.newContext({viewport:{width,height:860}}),page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));page.on('dialog',dialog=>dialog.accept());
  await context.route('**/*',async route=>{const url=new URL(route.request().url());if(url.origin!==origin)return route.abort();if(url.pathname==='/app')return route.fulfill({contentType:'text/html',body:nodeHtml()});if(url.pathname.startsWith('/assets/')){const file=path.join(docs,path.basename(url.pathname));if(fs.existsSync(file))return route.fulfill({contentType:file.endsWith('.css')?'text/css':'application/javascript',body:fs.readFileSync(file)})}return route.fulfill({contentType:'application/json',body:'{}'})});
  await context.addInitScript(installCompanyEmployeesFixture,{role:'owner'});
  await context.addInitScript(({row})=>{const original=window.fetch;window.companyEmployeesFixture.members=[row];window.fetch=async(input,opt={})=>{const u=new URL(String(input),location.href);if(u.pathname!=='/api/platform/members')return original(input,opt);if((opt.method||'GET')==='GET')return new Response(JSON.stringify({success:true,members:[row]}),{headers:{'Content-Type':'application/json'}});const body=JSON.parse(opt.body);window.companyEmployeesFixture.calls.push({path:u.pathname,method:'POST',body});return new Response(JSON.stringify({success:false,code:'member_card_conflict',fields:{expected_version:'version_conflict'}}),{status:409,headers:{'Content-Type':'application/json'}})}} ,{row});
  await page.goto(origin+'/app?view=employees&lang='+language);
  await page.locator('[data-staff-edit="22"]').click();
  const form=page.locator('#staffEditForm');
  await form.locator('[name=department_id]').selectOption(row.department_catalog[0].id);
  await form.locator('[data-staff-pane-tab=rights]').click();
  assert.equal(await form.locator('[data-access-scope]').count(),Object.values(scopeCatalog).reduce((n,actions)=>n+actions.length,0));
  for(const [section,actions] of Object.entries(scopeCatalog)){for(const action of ['create','view','update','delete'])assert.equal(await form.locator('[data-access-module='+section+'][data-access-scope='+action+']').count(),Number(actions.includes(action)),section+' only advertises real '+action);}
  assert.deepEqual(await form.locator('[data-access-module=employees][data-access-scope=create] option').evaluateAll(nodes=>nodes.map(n=>n.value)),['legacy','none','department','all'],'employee creation never offers Self');
  const matrix=form.locator('.staff-scope-matrix');
  assert(await matrix.isVisible());
  for(const selector of ['.staff-scope-matrix','[data-staff-pane=rights]','.staff-modal-actions']){
   assert.equal(await form.locator(selector).evaluate(n=>n.scrollWidth<=n.clientWidth+1),true,selector+' fits '+width);
  }
  assert.equal(await form.locator('[data-access-module=tasks][data-access-scope=view]').inputValue(),'department');
  assert.equal(await form.locator('[data-access-module=tasks][data-access-scope=update]').inputValue(),'self');
  await form.locator('[data-access-module=tasks][data-access-scope=delete]').selectOption('department');
  await form.locator('[type=submit]').click();
  await page.waitForFunction(()=>window.companyEmployeesFixture.calls.some(c=>c.method==='POST'));
  const body=await page.evaluate(()=>window.companyEmployeesFixture.calls.find(c=>c.method==='POST').body);
  assert.equal(body.changes.department_id,row.department_catalog[0].id);assert.deepEqual(body.changes.access_settings.changes,{scopes:{tasks:{delete:'department'}}});
  assert.equal(await form.locator('[data-access-module=tasks][data-access-scope=delete]').inputValue(),'department','Draft remains after conflict');
  await form.locator('[data-staff-pane-tab=rights]').focus();await page.keyboard.press('ArrowLeft');assert.equal(await form.locator('[data-staff-pane-tab=details]').getAttribute('aria-selected'),'true');await page.keyboard.press('ArrowRight');
  const output=process.env.EMPLOYEE_SCOPE_SCREENSHOTS||'/tmp/employee-scoped-matrix-screens';fs.mkdirSync(output,{recursive:true});await page.screenshot({path:path.join(output,`scope-matrix-${width}-${language}.png`)});
  await form.locator('[data-staff-cancel]').click();assert.equal(await page.locator('#staffMemberModal').evaluate(n=>n.classList.contains('show')),false);await page.locator('[data-staff-edit="22"]').click();assert.equal(await page.locator('[data-access-module=tasks][data-access-scope=delete]').inputValue(),'none','Cancel discards draft');
  assert.deepEqual(errors,[]);await context.close();
 }
 console.log('PASS Chromium scoped matrix 360/RU, 390/AZ, 768/EN, 1440/RU: no overflow, sparse independent save, conflict retention, keyboard tabs and cancel/reopen');
 }finally{await browser.close()}
})().catch(error=>{console.error(error.stack);process.exitCode=1});
