/* Synthetic DOM only: never calls a provider or mutates customer data. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync(path.join(__dirname,'../docs/tenant-shared-integration-settings.js'),'utf8');
const tick=()=>new Promise(r=>setTimeout(r,0));
const channels=[{id:'a',name:'<img src=x onerror=alert(1)>',phone:'994501234567',status:'connected',active:true},{id:'b',name:'Offline',phone:'+994502345678',status:'disconnected',active:true},{id:'c',name:'Conflict',status:'connected',phoneConflict:true,linked:true},{id:'d',name:'Available',status:'unknown',active:false,linked:false}];
function harness(actions){
 const dom=new JSDOM('<div id="integrationSettingsHost"></div>',{url:'https://test.invalid',runScripts:'outside-only'}),w=dom.window,calls=[],watchers=[];
 let respond=async()=>({success:true,channels});
 w.tenantI18n={language:'en'};w.tenantViewDemand={watch:item=>watchers.push(item)};w.confirm=()=>true;
 w.fetch=async(url,options)=>{calls.push({url,...options});return {ok:true,json:()=>respond(options)};};w.eval(source);
 const watcher=watchers.find(x=>x.provider==='connect');
 const profile={member:{tenant_id:'tenant',telegram_id:7,role:'worker'},integrations:[{provider:'connect',status:'connected',management_actions:actions}]};
 return {w,calls,watcher,profile,open:()=>watcher.activate(profile),reply:fn=>respond=fn,close:()=>w.close()};
}
(async()=>{
 let h=harness({view:true,update:true,create:false,delete:true});assert.equal(h.calls.length,0,'no background inventory before open');h.open();await tick();
 assert.equal(h.calls.length,1);const read=JSON.parse(h.calls[0].body);assert.equal(read.action,'channels');assert.equal(read.api_key,'');assert.equal(read.expected_tenant_id,'tenant');assert.equal(read.expected_user_id,7);assert.equal(read.confirm_company_impact,undefined);
 assert.equal(h.w.document.querySelectorAll('tbody tr').length,3,'connected/linked inventory before adding');assert.equal(h.w.document.querySelectorAll('tbody img').length,0,'untrusted names escaped');
 assert.equal(h.w.document.querySelectorAll('[data-state=connected]').length,1,'active routing is not proof of connected status');assert.equal(h.w.document.querySelectorAll('[data-state=warning]').length,1);
 assert.equal(h.w.document.querySelector('tbody td:nth-child(3)').textContent,'—','no invented pipeline');assert.equal(h.w.document.querySelector('[data-key]').closest('label').hidden,true);
 h.w.document.querySelector('.connect-number-add').click();await tick();assert.equal(h.calls.length,1,'opening add does not mutate or send credentials');assert.equal(h.w.document.querySelectorAll('tbody tr').length,4);assert.equal(h.w.document.querySelectorAll('tbody input').length,1,'only available safe channel selectable');
 h.w.document.querySelector('[data-key]').value='unsaved-secret';h.watcher.reset();assert.equal(h.w.document.querySelector('.connect-numbers-table'),null);h.close();
 for(const actions of [{view:false,update:true},{view:false,delete:true},{view:false}]){h=harness(actions);h.open();await tick();assert.equal(h.calls.length,0,'no view permission never auto-reads');h.close();}
 h=harness({view:true});h.reply(async()=>{throw Error('Temporary outage');});h.open();await tick();assert.match(h.w.document.querySelector('[data-message]').textContent,/Temporary outage/);const retry=[...h.w.document.querySelectorAll('button')].find(b=>b.textContent==='Retry');assert.equal(retry.hidden,false);h.reply(async()=>({success:true,channels:[]}));retry.click();await tick();assert.match(h.w.document.querySelector('tbody').textContent,/No connected numbers/);assert.equal(h.w.document.querySelector('.connect-number-add'),null);h.close();
 h=harness({view:true,update:true});h.reply(async options=>JSON.parse(options.body).action==='reconnect_channel'?{success:true,qr:'data:image/png;base64,c3ludGhldGlj'}:{success:true,channels});h.open();await tick();[...h.w.document.querySelectorAll('button')].find(b=>b.textContent==='Yenidən qoş').click();await tick();await tick();assert.ok(h.w.document.querySelector('[data-message] img'),'reconnect QR survives inventory refresh');h.close();
 h=harness({view:true,update:true});let release;h.reply(()=>new Promise(r=>release=r));h.open();await tick();h.watcher.reset();release({success:true,channels});await tick();assert.equal(h.w.document.querySelector('.connect-numbers-table'),null,'late response after close cannot resurrect UI');h.close();
 console.log('PASS Connect inventory: automatic scoped read, no writes/secrets, connected vs routing status, safe table, add disclosure, permissions, retry and stale close');
})().catch(e=>{console.error(e);process.exitCode=1});
