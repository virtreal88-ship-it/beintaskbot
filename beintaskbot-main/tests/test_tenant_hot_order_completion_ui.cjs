/* Isolated DOM/network contracts; never contacts customers or production. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const elements=new Map(),events={};
class Element {
  constructor(id=''){this.id=id;this.value='';this.style={};this.dataset={};this.listeners={};this.scrollHeight=180;const names=new Set();this.classList={add:n=>names.add(n),remove:n=>names.delete(n),contains:n=>names.has(n),toggle:(n,on)=>on?names.add(n):names.delete(n)};if(id)elements.set(id,this);}
  set innerHTML(html){this.html=html;for(const match of html.matchAll(/id="([^"]+)"/g))if(!elements.has(match[1]))new Element(match[1]);}
  get innerHTML(){return this.html||'';}
  append(...items){items.forEach(item=>{if(item.id)elements.set(item.id,item);});}
  addEventListener(type,listener){this.listeners[type]=listener;}
  click(){return this.onclick?.()||this.listeners.click?.();}
}
['systemApprovalsHost','hotApprovalsHost','systemApprovalTab','hotApprovalTab','hotApprovalRefresh','hotApprovalNotice','hotApprovalList','hotApprovalMore'].forEach(id=>new Element(id));
const nav=new Element(),$=id=>elements.get(id),posts=[],session=new Map();
const document={getElementById:$,querySelector:()=>nav,querySelectorAll:()=>[],createElement:()=>new Element(),head:new Element(),body:new Element(),
  addEventListener:(name,listener)=>events[name]=listener,dispatchEvent:event=>events[event.type]?.(event)};
const profile=(tenant='company-a',role='owner',enabled=true)=>({success:true,member:{tenant_id:tenant,telegram_id:20,role},capabilities:{modules:{hot_orders:enabled,tasks:enabled}}});
const row={id:'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa',client_name:'<Client>',service_name:'Təmir',description:'<unsafe>',result_text:'<Result>',updated_at:'2026-10-08T12:00:00+00:00',actions:{complete:true,approve:true,reject:true}};
let current=profile(),fails=true,deferred=null,uuid=0;
const reply=(data,status=200)=>({ok:status===200,status,json:async()=>data});
async function fetch(url,options={}){
  if(url.endsWith('/me'))return reply(current);
  if(options.body){posts.push(JSON.parse(options.body));return reply(fails?{success:false,error:'Network'}:{success:true,order:{}},fails?503:200);}
  if(deferred){const waiting=deferred;deferred=null;return waiting;}
  return reply({success:true,orders:[row],total:1});
}
const context={document,window:{},fetch,URLSearchParams,JSON,CustomEvent:class{constructor(type,options={}){this.type=type;this.detail=options.detail;}},
  crypto:{randomUUID:()=>`bbbbbbbb-bbbb-4bbb-bbbb-${String(++uuid).padStart(12,'0')}`},
  sessionStorage:{getItem:key=>session.get(key)||null,setItem:(key,value)=>session.set(key,value),removeItem:key=>session.delete(key)}};
const source=fs.readFileSync(path.join(__dirname,'../docs/tenant-hot-order-completion.js'),'utf8');vm.runInNewContext(source,context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  await flush();assert.equal(nav.hidden,false);await $('hotApprovalTab').click();await flush();
  assert.ok($('hotApprovalList').innerHTML.includes('&lt;Client&gt;'));assert.ok($('hotApprovalList').innerHTML.includes('&lt;Result&gt;'));
  events['tenant-hot-order-command']({detail:{row,action:'complete'}});
  assert.ok($('hotCompletionModal').classList.contains('show'));assert.equal($('hotCompletionModal').onclick,undefined);
  await $('hotCompletionForm').onsubmit({preventDefault(){}});assert.equal(posts.length,0,'Result is mandatory');
  $('hotCompletionText').value='Completed work';await $('hotCompletionForm').onsubmit({preventDefault(){}});
  assert.equal(posts.length,1);assert.equal(session.size,1);assert.equal($('hotCompletionText').disabled,true);
  assert.equal(posts[0].expected_updated_at,row.updated_at);assert.equal(posts[0].expected_tenant_id,'company-a');
  await $('hotCompletionClose').click();events['tenant-profile']({detail:profile('company-b')});await flush();
  assert.equal($('hotCompletionText').value,'');assert.equal($('hotCompletionResult').textContent,'');
  current=profile();events['tenant-profile']({detail:current});await flush();
  assert.ok($('hotCompletionModal').classList.contains('show'),'Uncertain command resumes on the original membership');
  fails=false;await $('hotCompletionForm').onsubmit({preventDefault(){}});
  assert.deepEqual(posts[0],posts[1]);assert.equal(session.size,0);assert.ok(!$('hotCompletionModal').classList.contains('show'));
  events['tenant-hot-order-command']({detail:{row,action:'reject'}});$('hotCompletionText').value='';
  await $('hotCompletionForm').onsubmit({preventDefault(){}});assert.equal(posts.length,2,'Return reason is mandatory');
  $('hotCompletionText').value='Fix result';await $('hotCompletionForm').onsubmit({preventDefault(){}});assert.equal(posts.at(-1).reason,'Fix result');
  let resolveOld;deferred=new Promise(resolve=>resolveOld=resolve);const old=$('hotApprovalRefresh').click();await flush();
  current=profile('company-b','master',false);events['tenant-profile']({detail:current});
  resolveOld(reply({success:true,orders:[{...row,client_name:'OLD SECRET'}],total:1}));await old;
  assert.equal(nav.hidden,true);assert.ok(!$('hotApprovalList').innerHTML.includes('OLD SECRET'));
  assert.ok(source.includes('100dvh'));assert.ok(source.includes('position:sticky'));
  console.log('PASS: completion/review UI requires results/reasons, escapes cards, retries exact commands and discards old-company responses.');
})().catch(error=>{console.error(error);process.exitCode=1;});
