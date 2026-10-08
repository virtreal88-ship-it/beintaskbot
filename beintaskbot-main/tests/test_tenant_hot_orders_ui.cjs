/* Simulated DOM/network: no writes to actual customers or Telegram. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const elements=new Map(),events={};
class Element {
  constructor(id='') {this.id=id;this.value='';this.style={};this.disabled=false;this.hidden=false;this.scrollHeight=160;this.dataset={};this.listeners={};const names=new Set();this.classList={add:n=>names.add(n),remove:n=>names.delete(n),contains:n=>names.has(n),toggle:(n,on)=>on ? names.add(n) : names.delete(n)};if(id)elements.set(id,this);}
  set innerHTML(html) {this.html=html;for(const match of html.matchAll(/id="([^"]+)"/g))if(!elements.has(match[1]))new Element(match[1]);if(this.id==='hotService')this.value=(html.match(/<option value="([^"]+)"/) || [,''])[1];}
  get innerHTML() {return this.html || '';}
  append(...items) {for(const item of items)if(item.id)elements.set(item.id,item);}
  addEventListener(type,listener) {this.listeners[type]=listener;}
  reset() {for(const id of ['hotClient','hotPhone','hotAddress','hotDescription'])elements.get(id).value='';elements.get('hotPriority').value='normal';}
  click() {return this.onclick?.();}
}
const ids=['view-hot_orders','hotOrderCount','hotOrderCreate','hotOrderRefresh','hotOrderNotice','hotOrderList','hotOrderPrev','hotOrderNext','hotOrderPage'];
ids.forEach(id=>new Element(id));const nav=new Element();
const document={getElementById:id=>elements.get(id),querySelector:()=>nav,head:new Element(),body:new Element(),createElement:()=>new Element(),addEventListener:(event,listener)=>events[event]=listener};
const $=id=>elements.get(id),session=new Map(),requests=[],posts=[];
function me(tenant='company-a',enabled=true) {return {success:true,member:{tenant_id:tenant,telegram_id:20,role:'owner',active:true,permissions:['hot_orders']},capabilities:{modules:{hot_orders:enabled},hot_orders:{can_create:enabled,can_claim:false,services:[{id:'repair',name:'Təmir'}]}}};}
const row=(tenant,client='Client')=>({id:'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa',tenant_id:tenant,client_name:client,description:'<script>unsafe</script>',service_id:'repair',service_name:'Təmir',status:'open',created_at:'2026-10-08T12:00:00Z',actions:{claim:false,cancel:true,release:false}});
const reply=(body,status=200)=>({ok:status===200,status,json:async()=>body});
let current=me(),postFails=true,deferred=null,uuid=0;
async function fetch(url,options={}) {
  requests.push(url);
  if(url.endsWith('/me'))return reply(current);
  if(options.body) {posts.push(JSON.parse(options.body));return reply(postFails ? {success:false,error:'Network problem'} : {success:true,order:row(current.member.tenant_id)},postFails ? 503 : 200);}
  if(deferred) {const wait=deferred;deferred=null;return wait;}
  return reply({success:true,orders:[row(current.member.tenant_id,'<Client>')],total:75});
}
const context={window:{},document,fetch,Date,JSON,confirm:()=>true,crypto:{randomUUID:()=>`bbbbbbbb-bbbb-4bbb-bbbb-${String(++uuid).padStart(12,'0')}`},sessionStorage:{getItem:key=>session.get(key)||null,setItem:(key,value)=>session.set(key,value),removeItem:key=>session.delete(key)}};
const source=fs.readFileSync(path.join(__dirname,'../docs/tenant-hot-orders.js'),'utf8');vm.runInNewContext(source,context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  await flush();assert.equal($('hotOrderCreate').hidden,false);
  await nav.listeners.click();
  assert.ok($('hotOrderList').innerHTML.includes('&lt;Client&gt;'));
  assert.ok($('hotOrderList').innerHTML.includes('&lt;script&gt;unsafe&lt;/script&gt;'));
  assert.ok(!$('hotOrderList').innerHTML.includes('data-hot-action="claim"'));
  assert.equal($('hotOrderNext').disabled,false);await $('hotOrderNext').click();
  assert.ok(requests.at(-1).endsWith('offset=50'));
  await $('hotOrderCreate').click();$('hotClient').value='Client';$('hotDescription').value='Printer repair';
  await $('hotOrderForm').onsubmit({preventDefault(){}});
  assert.equal(posts.length,1);assert.equal(session.size,1);assert.equal($('hotDescription').disabled,true);
  assert.equal($('hotSubmit').textContent,'Eyni sorğunu yoxla');
  assert.equal(posts[0].expected_tenant_id,'company-a');assert.equal(posts[0].expected_user_id,20);
  postFails=false;await $('hotOrderForm').onsubmit({preventDefault(){}});
  assert.deepEqual(posts[0],posts[1]);assert.equal(session.size,0);assert.equal($('hotOrderModal').classList.contains('show'),false);
  assert.equal($('hotOrderModal').onclick,undefined,'Backdrop cannot discard the form');
  let resolveOld;deferred=new Promise(resolve=>resolveOld=resolve);
  const oldLoad=$('hotOrderRefresh').click();await flush();
  current=me('company-b');$('view-hot_orders').classList.add('active');events['tenant-profile']({detail:current});await flush();
  resolveOld(reply({success:true,orders:[row('company-a','OLD SECRET')],total:999}));await oldLoad;
  assert.ok(!$('hotOrderList').innerHTML.includes('OLD SECRET'));assert.equal($('hotOrderCount').textContent,'75 sifariş');
  events['tenant-profile']({detail:me('company-b',false)});
  assert.equal($('hotOrderCreate').hidden,true);assert.equal($('hotOrderList').innerHTML,'');
  assert.ok(source.includes('max-height:calc(100dvh - 32px)'));assert.ok(source.includes('position:sticky;bottom:-22px'));
  const app=fs.readFileSync(path.join(__dirname,'../docs/tenant-app.html'),'utf8');
  assert.ok(app.includes("['tasks','customers','deals','hot_orders','settings']"));assert.ok(app.includes('/assets/tenant-hot-orders.js'));
  console.log('PASS: tenant hot-order UI escapes text, follows server actions, paginates, retries exact requests and rejects old-company responses.');
})().catch(error=>{console.error(error);process.exitCode=1;});
