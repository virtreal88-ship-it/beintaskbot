const fs = require('node:fs'), assert = require('node:assert/strict'), path = require('node:path');
const {JSDOM} = require('../.support-test-deps/node_modules/jsdom');
const source = fs.readFileSync(path.join(__dirname, '../docs/tenant-ai-state.js'), 'utf8');
const tick = async () => { for (let i=0; i<4; i++) await new Promise(resolve => setImmediate(resolve)); };
(async () => {
  const dom = new JSDOM('<main></main>', {url:'https://crm.test/app',runScripts:'outside-only'});
  const w = dom.window, calls = [], pending = [], opened = [];
  w.fetch = (url, options) => {
    calls.push(url); assert.equal(options.credentials, 'same-origin'); assert.equal(options.cache, 'no-store');
    return new Promise(resolve => pending.push(resolve));
  };
  const event = {agent_id:'a',channel:{name:'WhatsApp First'},status:'skipped',reason_code:'manager_replied',occurred_at:'2026-10-17T12:00:00Z',dialog:{provider:'connect',channel_id:'c1',lead_id:41}};
  const result = {
    schema_version:1,generated_at:'2026-10-17',summary:{service:'available',enabled_agents:1,connected_channels:2,ai_errors_24h:0,unconfirmed_sends_24h:0,quarantined_legacy_dialogues:4},
    agents:[{id:'a',name:'<img src=x onerror="leak()">',enabled:true,mode:'manual',state:'manual',schedule:{},channels:[{name:'WhatsApp First',state:'manual',reason_codes:['manual_mode'],provider:'connect',group_messages_allowed:false,dialogue_states:{human:1}}],recent_events:[event]}],events:[event],can_view_technical:true
  };
  const response = data => ({ok:true,json:async()=>({success:true,result:data})});
  w.eval(source); assert.equal(calls.length,0); assert.equal(w.document.querySelector('button'),null);
  const panel = w.tenantAIState.mount(w.document.querySelector('main'), {profile:{tenant_id:'t1',telegram_id:11},language:'ru',onOpenDialog:route=>opened.push(route)});
  assert.match(calls[0], /expected_tenant_id=t1/); assert.match(calls[0], /expected_user_id=11/);
  pending.shift()(response(result)); await tick();
  assert.match(w.document.body.textContent, /Состояние AI/); assert.match(w.document.body.textContent,/Диалог передан сотруднику/);
  assert.match(w.document.body.textContent,/Исторические ошибки/); assert.match(w.document.body.textContent,/черновики/);
  assert.equal(w.document.querySelector('img'),null); assert.equal(calls.length,1);
  [...w.document.querySelectorAll('button')].find(b=>b.textContent==='Открыть диалог').click(); assert.equal(opened[0].channel_id,'c1');
  assert.equal([...w.document.querySelectorAll('.tenant-ai-state article p')].filter(n=>n.textContent==='Готовит черновики — отправляет сотрудник').length,1,'mode/state/reason are not repeated');
  assert.equal(w.document.querySelectorAll('.tenant-ai-event').length,1,'events appear once, not repeated in each agent');
  assert.equal(w.document.querySelector('.tenant-ai-event').dataset.tone,'normal');
  assert.match(w.document.querySelector('.tenant-ai-event-help').textContent,/нормальн/i);
  assert.equal(w.document.querySelector('[data-metric=stuck] dd').textContent,'Нет данных');
  assert.equal(w.document.querySelector('[data-metric=errors] dd').textContent,'0');
  const details = w.document.querySelector('details'); details.open = true; const technical = details.ontoggle();
  await tick(); assert.equal(calls.length,2); assert.match(calls[1], /state\/technical/);
  let copied = '';
  Object.defineProperty(w.navigator, 'clipboard', {value:{writeText:async value=>{copied=value;}}});
  pending.shift()(response({...result,technical:{tenant_id:'t1',deployment_id:'safe-id',queue:{queued:0},reason_codes:['manager_replied']}}));
  await technical; await tick();
  [...w.document.querySelectorAll('button')].find(b=>b.textContent==='Копировать безопасный отчёт').click(); await tick();
  assert.match(copied, /tenant_id/); assert.doesNotMatch(copied, /WhatsApp First|lead_id|<img|agent_name/);
  const lang = panel.setLanguage('az'); pending.shift()(response({...result,can_view_technical:false})); await lang;
  assert.match(w.document.body.textContent,/AI vəziyyəti/); assert.match(w.document.body.textContent,/Dialoq əməkdaşa ötürülüb/);
  assert.equal(w.document.querySelector('details'),null);
  const english = panel.setLanguage('en'); pending.shift()(response({...result,can_view_technical:false})); await english;
  assert.match(w.document.body.textContent,/AI state/); assert.match(w.document.body.textContent,/Dialogue handed to an employee/);
  const absent = panel.refresh();pending.shift()(response({...result,summary:{},can_view_technical:false,events:[
    {...event,status:'error'},
    {...event,reason_code:'chat_ai_http_500',status:'error',dialog:{provider:'connect',lead_id:41}},
    {...event,reason_code:'send_result_unknown',status:'error',dialog:{provider:'connect',channel_id:'c2',lead_id:42}}
  ]}));await absent;
  assert.ok([...w.document.querySelectorAll('.tenant-ai-metric dd')].every(n=>n.textContent==='No data'));
  assert.equal(w.document.querySelectorAll('.tenant-ai-event[data-tone=normal]').length,1);
  assert.equal(w.document.querySelectorAll('.tenant-ai-event[data-tone=error]').length,2);
  assert.equal(w.document.querySelectorAll('.tenant-ai-event button').length,2,'unscoped dialogue never gets a navigation button');
  const measured = panel.refresh();pending.shift()(response({...result,summary:{...result.summary,stuck_jobs:2},can_view_technical:false}));await measured;
  assert.equal(w.document.querySelector('[data-metric=stuck] dd').textContent,'2');
  const stale = panel.refresh(), staleResponse = pending.shift();
  w.document.dispatchEvent(new w.CustomEvent('tenant-profile', {detail:{member:{tenant_id:'t2',telegram_id:22}}}));
  staleResponse(response(result)); await stale; assert.equal(w.document.querySelector('section'),null);
  const prior = calls.length; await panel.refresh(); assert.equal(calls.length, prior);
  const second = w.tenantAIState.mount(w.document.querySelector('main'), {profile:{tenant_id:'t2',telegram_id:22},language:'en'});
  pending.shift()({ok:false,json:async()=>({error:'PRIVATE-PROVIDER-SECRET'})}); await tick();
  assert.match(w.document.body.textContent,/unavailable/); assert.doesNotMatch(w.document.body.textContent,/PRIVATE-PROVIDER-SECRET/);
  second.destroy(); dom.window.close();
  console.log('PASS: unified state identity guards, lazy technical access, redacted copying, RU/AZ/EN and safe rendering.');
})().catch(error => { console.error(error); process.exitCode=1; });
