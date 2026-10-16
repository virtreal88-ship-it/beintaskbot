const fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const source=fs.readFileSync(path.join(__dirname,'../docs/tenant-ai-reliability.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 const dom=new JSDOM('<div id="aiAgentsOverview"></div><button data-ai-overview-diagnostics>Check</button>',{url:'https://crm.test',runScripts:'outside-only'});
 const w=dom.window,pending=[];
 w.fetch=(url,options)=>{
  assert.equal(options.cache,'no-store');assert.equal(options.credentials,'same-origin');
  if(url==='/api/platform/me')return Promise.resolve({ok:true,json:async()=>({member:{tenant_id:'a'}})});
  return new Promise(resolve=>pending.push(resolve));
 };
 w.eval(source);await tick();
 const click=()=>w.document.querySelector('[data-ai-reliability-open]').click();
 const response=text=>({ok:true,json:async()=>({success:true,result:{
  health:{label:'Worker доступен',blockers:[],operating_mode:'active',automatic_send_enabled:true},
  metrics:{queued:1,processing:0,overdue:0,expired_leases:0,ai_errors_24h:1,unsuccessful_sends_24h:1},
  messages:[{status:'error',provider:'connect',message_id:text,reason:'Connect: результат отправки неизвестен',checkpoints:{received:true,queued:true,processing:true,generated:true,sent:false},attempts:1,received_at:'2026-10-16'}]
 }})});
 click();pending.shift()(response('<img src=x onerror="window.leak=1">'));await tick();
 const panel=w.document.getElementById('aiReliability');
 assert.match(panel.textContent,/Работоспособность AI/);assert.match(panel.textContent,/Получено → В очереди → Обрабатывается → Ответ создан/);
 assert.equal(panel.querySelector('img'),null);assert.equal(w.leak,undefined);
 click();const stale=pending.shift();
 w.document.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{member:{tenant_id:'b'}}}));
 assert.equal(w.document.getElementById('aiReliability'),null);
 stale(response('PRIVATE-TENANT-A'));await tick();assert.doesNotMatch(w.document.body.textContent,/PRIVATE-TENANT-A/);
 click();pending.shift()({ok:false,json:async()=>({error:'secret provider body'})});await tick();
 assert.match(w.document.getElementById('aiReliability').textContent,/Диагностика недоступна/);
 assert.doesNotMatch(w.document.body.textContent,/secret provider body/);
 w.close();console.log('PASS: reliability panel renders stages safely, rejects stale tenant responses, and redacts API errors.');
})().catch(error=>{console.error(error);process.exitCode=1});
