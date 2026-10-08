/* Ledger UX contracts in a simulated DOM; no production writes. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const elements=new Map(),events={};
class Element{
  constructor(id=''){this.id=id;this.value='';this.style={};this.dataset={};this.listeners={};this.scrollHeight=160;const names=new Set();this.classList={add:n=>names.add(n),remove:n=>names.delete(n),contains:n=>names.has(n),toggle:(n,on)=>on?names.add(n):names.delete(n)};if(id)elements.set(id,this);}
  set innerHTML(html){this.html=html;for(const match of html.matchAll(/id="([^"]+)"/g))if(!elements.has(match[1]))new Element(match[1]);}
  get innerHTML(){return this.html||'';}
  append(...items){items.forEach(item=>{if(item.id)elements.set(item.id,item);});}
  addEventListener(type,listener){this.listeners[type]=listener;}
  click(){return this.onclick?.()||this.listeners.click?.();}
}
['view-finance','financeBalance','financeMember','financeCredit','financeDebit','financeRefresh','financeNotice','financeEntries','financePrev','financeNext','financePage'].forEach(id=>new Element(id));
const nav=new Element(),$=id=>elements.get(id),posts=[],requests=[],session=new Map();
const document={getElementById:$,querySelector:()=>nav,createElement:()=>new Element(),head:new Element(),body:new Element(),addEventListener:(name,listener)=>events[name]=listener};
const profile=(tenant='company-a',role='owner',enabled=true)=>({success:true,member:{tenant_id:tenant,telegram_id:20,role,active:true},capabilities:{modules:{finance:enabled}}});
let current=profile(),fails=true,deferred=null,uuid=0;
const reply=(data,status=200)=>({ok:status===200,status,json:async()=>data});
async function fetch(url,options={}){
 requests.push(url);if(url.endsWith('/me'))return reply(current);
 if(options.body){posts.push(JSON.parse(options.body));return reply(fails?{success:false,error:'Network'}:{success:true,entry:{}},fails?503:200);}
 if(url.includes('members=1'))return reply({success:true,members:[{telegram_id:20,display_name:'<Owner>',active:true},{telegram_id:30,display_name:'Worker',active:true}]});
 if(deferred){const waiting=deferred;deferred=null;return waiting;}
 return reply({success:true,entries:[{id:'entry',amount:'100.00',kind:'credit',note:'<Payment>',created_at:'2026-10-08T12:00:00+00:00'}],balance:'100.00',currency:'AZN',total:75,display_name:'Owner',active:true});
}
const context={document,fetch,JSON,crypto:{randomUUID:()=>`bbbbbbbb-bbbb-4bbb-bbbb-${String(++uuid).padStart(12,'0')}`},sessionStorage:{getItem:key=>session.get(key)||null,setItem:(key,value)=>session.set(key,value),removeItem:key=>session.delete(key)}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../docs/tenant-finance.js'),'utf8'),context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 await flush();await flush();assert.equal($('financeCredit').hidden,false);assert($('financeMember').innerHTML.includes('&lt;Owner&gt;'));
 await nav.click();assert($('financeEntries').innerHTML.includes('&lt;Payment&gt;'));assert.equal($('financeBalance').textContent,'Owner: 100.00 AZN');
 await $('financeNext').click();assert(requests.at(-1).endsWith('offset=50'));
 await $('financeCredit').click();await $('financeForm').onsubmit({preventDefault(){}});assert.equal(posts.length,0);
 $('financeAmount').value='100.00';$('financeNote').value='Credit reason';await $('financeForm').onsubmit({preventDefault(){}});
 assert.equal(session.size,1);assert.equal($('financeNote').disabled,true);assert.equal(posts[0].amount,'100.00');
 assert.equal(posts[0].expected_tenant_id,'company-a');assert.equal(posts[0].expected_user_id,20);
 fails=false;await $('financeForm').onsubmit({preventDefault(){}});assert.deepEqual(posts[0],posts[1]);assert.equal(session.size,0);
 assert.equal($('financeModal').onclick,undefined,'Backdrop never discards a monetary command');
 let resolveOld;deferred=new Promise(resolve=>resolveOld=resolve);const old=$('financeRefresh').click();await flush();
 current=profile('company-b','worker');events['tenant-profile']({detail:current});await flush();
 resolveOld(reply({success:true,entries:[],balance:'999999.00',currency:'AZN',total:999,display_name:'PRIVATE OLD'}));await old;
 assert.equal($('financeCredit').hidden,true);assert.equal($('financeMember').hidden,true);assert(!$('financeBalance').textContent.includes('PRIVATE'));
 await nav.click();assert(requests.at(-1).includes('member_id=20'));assert(!$('financeEntries').innerHTML.includes('data-finance-reverse'));
 events['tenant-profile']({detail:profile('company-b','worker',false)});assert.equal($('financeEntries').innerHTML,'');assert.equal($('financeNote').value,'');
 console.log('PASS: finance UI scopes balances, escapes entries, paginates, freezes uncertain commands and prevents duplicate writes.');
})().catch(error=>{console.error(error);process.exitCode=1;});
