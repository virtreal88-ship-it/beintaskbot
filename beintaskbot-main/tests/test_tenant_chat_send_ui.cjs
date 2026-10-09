const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('docs/tenant-app.html', 'utf8');
const body = source.split("$('sendChat').onclick=async()=>{")[1].split('\n      };')[0];

function fixture() {
  const storage = new Map(), sent = [], alerts = [];
  const input = {value:'Salam'}, button = {disabled:false,textContent:'Göndər',classList:{add(){},remove(){}}};
  const env = {
    $:id=>id==='chatText'?input:button, x:{kommo_lead_id:7},
    identity:'tenant:20',revision:1,chatRevision:1,chatIdentity:()=>env.identity,
    state:{me:{tenant_id:'tenant',telegram_id:20}}, crypto:{randomUUID:()=>{env.uuids++;return 'request-id';}},
    sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    alert:text=>alerts.push(text),loadChat:async()=>{},uuids:0,
    api:async(url,options)=>{sent.push(JSON.parse(options.body));return {state:'accepted'};}
  };
  return {env,input,button,storage,sent,alerts,click:vm.runInNewContext('(async()=>{'+body+'})',env)};
}

(async()=>{
  let f=fixture(); await f.click();
  assert.equal(f.sent.length,1);assert.equal(f.sent[0].expected_user_id,20);assert.equal(f.sent[0].expected_tenant_id,'tenant');
  assert.equal(f.storage.size,0);assert.equal(f.input.value,'');
  f=fixture();f.env.api=async(url,options)=>{f.sent.push(JSON.parse(options.body));throw new Error('network lost');};
  await f.click();assert.equal(f.storage.size,1);assert.equal(f.input.value,'Salam');assert.equal(f.button.textContent,'Vəziyyəti yoxla');
  const first=JSON.stringify(f.sent[0]);
  f.env.api=async(url,options)=>{f.sent.push(JSON.parse(options.body));return {state:'accepted'};};
  await f.click();assert.equal(JSON.stringify(f.sent[1]),first);assert.equal(f.env.uuids,1);assert.equal(f.storage.size,0);
  f=fixture();f.env.api=async()=>{throw new Error('unknown');};await f.click();
  f.input.value='Another message';let calls=0;f.env.api=async()=>{calls++;};await f.click();assert.equal(calls,0);assert.equal(f.storage.size,1);
  f=fixture();f.env.api=async()=>{const e=new Error('rejected');e.keepRequest=false;throw e;};await f.click();assert.equal(f.storage.size,0);
  f=fixture();f.env.api=async()=>{f.env.chatIdentity=()=> 'other';f.input.value='New draft';return {state:'accepted'};};
  await f.click();assert.equal(f.input.value,'New draft');assert.equal(f.storage.size,0);
  assert.ok(source.includes('e.keepRequest=d.keep_request!==false'));
  console.log('PASS: exact send receipt recovery, no new UUID after timeout, edits blocked while unknown, late responses scoped.');
})().catch(error=>{console.error(error);process.exitCode=1;});
