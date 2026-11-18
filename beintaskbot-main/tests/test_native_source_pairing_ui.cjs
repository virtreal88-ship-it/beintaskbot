const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const tick=()=>new Promise(r=>setTimeout(r,0));
(async()=>{
 const source='00000000-0000-0000-0000-000000000001';
 const dom=new JSDOM(`<button data-connect-calls="${source}">WhatsApp</button>`,{url:'https://test.invalid',runScripts:'outside-only'}),w=dom.window;
 try{
  w.tenantProfileSnapshot={member:{tenant_id:'test',telegram_id:11,role:'owner'}};
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true};w.HTMLDialogElement.prototype.close=function(){this.open=false};
  let result={unified:true,state:'pairing',activation_pending:true,phone:'123456789',qr:'a'.repeat(24)},reads=[];
  w.fetch=async(url,options)=>{reads.push({url,...options});return {ok:true,json:async()=>({result})}};
  const timers=new Map();let serial=0;w.setTimeout=(fn,ms)=>{const id=++serial;timers.set(id,{fn,ms});return id};w.clearTimeout=id=>timers.delete(id);
  w.eval(fs.readFileSync(path.join(__dirname,'../docs/tenant-call-sources.js'),'utf8'));
  w.document.querySelector('button').click();await tick();
  const dialog=w.document.querySelector('dialog');assert(dialog.open);assert.equal(dialog.querySelector('h2').textContent,'WhatsApp источника');
  assert.match(dialog.textContent,/Один QR подключает сообщения и звонки/);assert.equal(reads[0].method,'GET');
  assert.equal(dialog.querySelector('img').hidden,false);assert.equal(reads.length,1,'opening only reads an explicitly staged binding');
  result={...result,state:'ready',qr:null};let poll=[...timers.values()].find(t=>t.ms===5000);assert(poll);poll.fn();await tick();
  assert.match(dialog.textContent,/после проверки переноса/);assert([...timers.values()].some(t=>t.ms===5000),'paired but unactivated remains polled');
  w.tenantProfileSnapshot.member={tenant_id:'other',telegram_id:11,role:'owner'};w.document.dispatchEvent(new w.CustomEvent('tenant-profile'));
  assert.equal(w.document.querySelector('dialog'),null);assert.equal(timers.size,0);
  console.log('PASS native QR: shared engine, no automatic provisioning, staged activation and identity cleanup');
 }finally{w.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
