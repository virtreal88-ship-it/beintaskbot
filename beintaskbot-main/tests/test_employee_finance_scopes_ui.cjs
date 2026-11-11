/* Scoped finance read UX, no real financial calls. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const elements=new Map(),events={};
class Element{
  constructor(id=''){this.id=id;this.value='';this.style={};this.dataset={};this.listeners={};this.scrollHeight=120;const names=new Set();this.classList={add:n=>names.add(n),remove:n=>names.delete(n),contains:n=>names.has(n),toggle:(n,on)=>on?names.add(n):names.delete(n)};if(id)elements.set(id,this);}
  set innerHTML(html){this.html=html;for(const match of html.matchAll(/id="([^"]+)"/g))if(!elements.has(match[1]))new Element(match[1]);}
  get innerHTML(){return this.html||'';}
  append(...items){items.forEach(item=>{if(item.id)elements.set(item.id,item);});}
  addEventListener(type,listener){this.listeners[type]=listener;}
  click(){return this.onclick?.()||this.listeners.click?.();}
}
for(const id of ['view-finance','financeBalance','financeMember','financeCredit','financeDebit','financeRefresh','financeNotice','financeEntries','financePrev','financeNext','financePage'])new Element(id);
const nav=new Element(),$=id=>elements.get(id),requests=[],posts=[];
const document={getElementById:$,querySelector:()=>nav,createElement:()=>new Element(),head:new Element(),body:new Element(),addEventListener:(n,f)=>events[n]=f};
function profile(read_scope='department',role='operator'){
 return {success:true,member:{tenant_id:'synthetic-company',telegram_id:41,role,active:true},capabilities:{modules:{finance:false},pages:{finance:read_scope!=='none'},finance:{read_scope,can_write:false,can_select_members:['department','all'].includes(read_scope),can_view_all:read_scope==='all'}}};
}
let current=profile(),deny=false,deferred=null;
const reply=(body,status=200)=>({ok:status===200,status,json:async()=>body});
async function fetch(url,options={}){
 requests.push(url);if(url.endsWith('/me'))return reply(current);
 if(options.body){posts.push(JSON.parse(options.body));return reply({success:false,error:'Forbidden'},403);}
 if(deferred){const pending=deferred;deferred=null;return pending;}
 if(deny)return reply({success:false,error:'Access revoked'},403);
 const metadata={...current.capabilities.finance};
 if(url.includes('members=1'))return reply({success:true,...metadata,has_more:!url.includes('offset='),members:url.includes('offset=')?[{telegram_id:43,display_name:'More synthetic employee',active:true}]:[{telegram_id:41,display_name:'Synthetic self',active:true},{telegram_id:42,display_name:'<Synthetic colleague>',active:true}]});
 const member_id=Number(new URL(url,'https://synthetic.test').searchParams.get('member_id'));
 return reply({success:true,...metadata,member_id,entries:[{id:'entry-'+member_id,amount:'42.00',kind:'credit',note:'Synthetic scoped ledger',created_at:'2026-11-10T00:00:00Z'}],balance:'42.00',currency:'AZN',total:1,display_name:'Synthetic '+member_id,active:true});
}
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../docs/tenant-finance.js'),'utf8'),{document,fetch,JSON,crypto:{randomUUID:()=>{throw Error('No financial command should be created')}},sessionStorage:{getItem:()=>null,setItem:()=>{throw Error('No financial command should be saved')},removeItem:()=>{}}});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 await flush();await flush();assert.equal($('financeMember').hidden,false);assert.equal($('financeCredit').hidden,true);assert.equal($('financeDebit').hidden,true);
 assert($('financeMember').innerHTML.includes('&lt;Synthetic colleague&gt;'));
 $('financeMember').value='42';await $('financeMember').onchange();assert.equal($('financeBalance').textContent,'Synthetic 42: 42.00 AZN');assert(!$('financeEntries').innerHTML.includes('data-finance-reverse'));
 await $('financeCredit').click();assert(!$('financeModal').classList.contains('show'));assert.equal(posts.length,0);
 $('financeMember').value='__more__';await $('financeMember').onchange();assert(requests.at(-1).includes('offset=2'));assert($('financeMember').innerHTML.includes('More synthetic employee'));assert.equal($('financeMember').value,'42');
 deny=true;await $('financeRefresh').click();assert.equal($('financeBalance').textContent,'Balans: —');assert(!$('financeEntries').innerHTML.includes('Synthetic scoped ledger'));assert.equal($('financePage').textContent,'0');assert.equal($('financeCredit').hidden,true);deny=false;
 current=profile('all','admin');events['tenant-profile']({detail:current});await flush();await flush();assert.equal($('financeMember').hidden,false);assert.equal($('financeCredit').hidden,true,'Read-all does not imply an admin write grant');
 current=profile('self');events['tenant-profile']({detail:current});await flush();assert.equal($('financeMember').hidden,true);await nav.click();assert(requests.at(-1).includes('member_id=41'));
 let resolveOld;deferred=new Promise(resolve=>resolveOld=resolve);const pending=nav.click();await flush();const before=requests.length;nav.click();assert.equal(requests.length,before,'Repeated load is bounded');
 current=profile('none');events['tenant-profile']({detail:current});resolveOld(reply({success:true,entries:[{note:'PRIVATE OLD DATA'}],balance:'9999',display_name:'Old',currency:'AZN',total:1,can_write:true}));await pending;
 assert.equal($('financeEntries').innerHTML,'');assert(!$('financeBalance').textContent.includes('9999'));assert.equal($('financeCredit').hidden,true);assert.equal(posts.length,0);
 console.log('PASS: department/all picker, pagination, self, denied/stale reads, and no finance writes from read grants.');
})().catch(error=>{console.error(error);process.exitCode=1});
