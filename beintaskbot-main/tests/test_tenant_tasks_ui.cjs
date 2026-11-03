/* Keep the original external-create replay guarantee through the actual /app entry. */
process.env.TZ='Asia/Baku';
const assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require(process.env.SUPPORT_CASES_JSDOM||'../.support-test-deps/node_modules/jsdom');
const {appHtml,installFixture}=require('./task_read_save_flow_fixture.cjs');
const settle=async()=>{for(let i=0;i<12;i++)await new Promise(resolve=>setTimeout(resolve,0));};
(async()=>{
  const errors=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error.message));
  const dom=new JSDOM(appHtml(),{url:'https://fixture.test/app?view=tasks&lang=ru',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:console,beforeParse(window){
    window.Response=Response;window.Headers=Headers;window.Request=Request;
    window.matchMedia=()=>({matches:false,addEventListener(){}});
    window.eval('('+installFixture.toString()+')({})');
  }});
  try{
    const w=dom.window,d=w.document,$=id=>d.getElementById(id),f=w.taskFlowFixture;
    const posts=()=>f.calls.filter(call=>call.method==='POST');
    await settle();$('nativeTaskCreate').click();await settle();
    d.querySelector('[data-task-create-source="kommo"]').click();await settle();
    $('taskBody').value='Call client';$('taskDue').value='2031-01-01T10:00';$('taskLead').value='7';
    $('taskLead').dispatchEvent(new w.Event('change'));
    assert.match($('taskRouteHint').textContent,/Воронка и этап сделки сохранятся/);
    f.rawWrite='{response lost after create';$('tenantTaskForm').requestSubmit();await settle();
    assert.equal(posts().length,1);
    const original=JSON.parse(JSON.stringify(posts()[0].body));
    assert.equal(String(original.executor_id),'11');assert.equal(String(original.pipeline_id),'10');
    assert.equal(String(original.lead_id),'7');assert.equal(original.text,'Call client');
    assert.match(original.request_id,/^[a-f\d-]{36}$/i);
    const storageKey='crm-task:'+f.A+':11';
    assert.deepEqual(JSON.parse(w.sessionStorage.getItem(storageKey)),original);
    assert.equal($('taskBody').disabled,true);assert.match($('taskSubmit').textContent,/Проверить/);
    $('taskCancel').click();$('nativeTaskCreate').click();await settle();
    d.querySelector('[data-task-create-source="kommo"]').click();await settle();
    assert.equal($('taskBody').value,'Call client');assert.equal($('taskBody').disabled,true);
    assert.deepEqual(JSON.parse(w.sessionStorage.getItem(storageKey)),original);
    const before=f.calls.length;$('tenantTaskForm').requestSubmit();await settle();
    assert.equal(posts().length,1,'checking the original request never repeats a provider write');
    const check=f.calls.slice(before).find(call=>new URL(call.url,'https://fixture.test').searchParams.get('request_id')===original.request_id);
    assert(check);assert.equal(check.method,'GET');
    const query=new URL(check.url,'https://fixture.test').searchParams;
    assert.equal(query.get('source'),'kommo');assert.equal(query.get('expected_tenant_id'),f.A);assert.equal(query.get('expected_user_id'),'11');
    assert.equal(w.sessionStorage.getItem(storageKey),null);assert.equal($('taskFlowCard').classList.contains('show'),true);
    assert.equal(new URL(w.location.href).searchParams.get('task_id'),'9');assert.match($('taskFlowCard').textContent,/Call client/);
    assert.deepEqual(errors,[]);
    global.console.log('PASS: external task keeps the exact request after lost receipt and reopen; read-only recovery opens the saved task without a second POST.');
  }finally{dom.window.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
