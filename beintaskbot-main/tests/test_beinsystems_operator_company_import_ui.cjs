const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync(path.join(__dirname,'../docs/tenant-operator-company-import.js'),'utf8');
const html = fs.readFileSync(path.join(__dirname,'../docs/tenant-app.html'),'utf8');
assert.ok(html.includes('<script src="/assets/tenant-operator-company-import.js"></script>'));
const fixture = {member:{role:'owner',tenant_id:'tenant-synthetic',telegram_id:7},
  integrations:[{provider:'beinsystems_operator'}]};
const pause = () => new Promise(resolve => setImmediate(resolve));
(async()=>{
  const dom = new JSDOM('<section id="view-companies" class="active"><div id="nativeCompaniesList"></div></section>',
    {url:'https://crm.test/app?view=companies',runScripts:'outside-only'});
  const w = dom.window;
  const calls = [];
  let job = null, completed = 0;
  w.tenantI18n = {language:'ru'};
  w.tenantProfileSnapshot = fixture;
  w.addEventListener('operator-company-import-finished',()=>completed++);
  w.fetch = async (url, opts) => {
    const body = opts.body && JSON.parse(opts.body);
    calls.push({url,body});
    const response = data => ({ok:true,json:async()=>({success:true,...data})});
    if (!body) return response({integration:{configured:true,first_company_batch_plan_enabled:true}});
    assert.equal(body.expected_tenant_id,fixture.member.tenant_id);
    assert.equal(body.expected_user_id,fixture.member.telegram_id);
    if (body.action === 'company_import_status') return response({company_import_job:job});
    if (body.action === 'preview_company_import_all') return response({
      company_batch_preview:{received:104,already:0,remaining:104,conflicts:[],receipt:'opaque'}});
    if (body.action === 'confirm_company_import_all') {
      assert.equal(body.receipt,'opaque');
      assert.equal(calls.filter(c=>c.body?.action==='confirm_company_import_all').length,1);
      job = {id:'job',state:'queued',processed:0,total:104,created:0,reused:0,conflicts:0};
      return response({company_import_job:job});
    }
    assert.fail('Unexpected request: '+body.action);
  };
  const profile = data => w.document.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:data}));
  const view = view => w.document.dispatchEvent(new w.CustomEvent('tenant-view',{detail:{view}}));
  try{
    w.eval(source); await pause();
    const panel = w.document.getElementById('operatorCompanyImport');
    const preview = panel.querySelector('[data-preview]'), confirm = panel.querySelector('[data-confirm]');
    const status = panel.querySelector('[role=status]');
    assert.equal(panel.hidden,false);
    preview.click(); await pause();
    assert.match(status.textContent,/К импорту: 104/);
    assert.equal(confirm.hidden,false);
    confirm.click(); confirm.click(); await pause();
    assert.match(status.textContent,/Импорт выполняется: 0 \/ 104/);
    assert.equal(preview.disabled,true);
    view('deals');
    job = {...job,state:'completed',processed:104,created:104};
    view('companies'); await pause();
    assert.match(status.textContent,/Создано: 104/);
    assert.equal(completed,1);
    assert.equal(preview.disabled,false);
    profile({...fixture,member:{...fixture.member,role:'admin'}});
    assert.equal(panel.hidden,true);
    assert.equal(status.textContent,'');
  } finally {w.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
