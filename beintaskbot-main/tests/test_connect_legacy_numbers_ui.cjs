const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
const html=fs.readFileSync(path.join(__dirname,'../docs/tenant-app.html'),'utf8');
const code=html.slice(html.indexOf('  let connectChannelsRead=0;'),html.indexOf('  async function runConnectChannelAction'));
const tick=()=>new Promise(r=>setTimeout(r,0));
function setup(role='owner',scoped=false){const dom=new JSDOM('<input id="connectApiKey" value="unsaved-secret"><div id="connectChannelList"></div>',{runScripts:'outside-only'}),w=dom.window,calls=[];
w.$=id=>w.document.getElementById(id);w.esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
w.tenantProfileSnapshot={member:{role,tenant_id:'a',telegram_id:1}};w.tenantSharedIntegrations={scoped:()=>scoped};w.runConnectChannelAction=()=>{};
w.api=async(url,options)=>{calls.push(JSON.parse(options.body));return {channels:[{id:'a',name:'<img onerror=evil>',phone:'99450123',status:'connected',active:true},{id:'b',name:'Offline',status:'disconnected',linked:true},{id:'c',name:'Unknown',active:true}]}};w.eval(code);
return {w,calls,open:()=>w.document.dispatchEvent(new w.CustomEvent('tenant-integration-panel',{detail:{provider:'connect',open:true}})),close:()=>dom.window.close()};}
(async()=>{for(const [role,scoped] of [['worker',false],['owner',true]]){const h=setup(role,scoped);h.open();await tick();assert.equal(h.calls.length,0);h.close();}
let h=setup();assert.equal(h.calls.length,0);h.open();await tick();assert.equal(h.calls.length,1);assert.deepEqual(h.calls[0],{action:'channels',api_key:''});assert.equal(h.w.document.querySelectorAll('tbody tr').length,3);assert.equal(h.w.document.querySelectorAll('img').length,0);assert.equal(h.w.document.querySelectorAll('[data-state=connected]').length,1);assert.equal(h.w.document.querySelectorAll('[data-state=warning]').length,1);assert.equal(h.w.document.querySelectorAll('[data-state=other]').length,1);assert.equal(h.w.$('connectApiKey').value,'unsaved-secret');h.close();
h=setup();let resolve;h.w.api=()=>new Promise(r=>resolve=r);h.open();h.w.document.dispatchEvent(new h.w.CustomEvent('tenant-integration-panel',{detail:{open:false}}));resolve({channels:[{id:'stale'}]});await tick();assert.equal(h.w.document.querySelector('tbody'),null);h.close();
h=setup();h.w.api=()=>new Promise(r=>resolve=r);h.open();h.w.tenantProfileSnapshot.member.tenant_id='b';resolve({channels:[{id:'stale'}]});await tick();assert.equal(h.w.document.querySelector('tbody'),null);h.close();
console.log('PASS legacy WhatsApp auto list: saved key only, permission gate, safe status/table, close/account stale guards');})().catch(e=>{console.error(e);process.exitCode=1});
