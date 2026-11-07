/* Simulated DOM behavior: scoped loading, escaped cards, exact pending retries. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const elements=new Map(),events={};
class Element {
  constructor(id=''){this.id=id;this.value='';this.hidden=false;this.disabled=false;this.style={};this.dataset={};this.options=[];this.listeners={};const names=new Set();this.classList={add:n=>names.add(n),remove:n=>names.delete(n),contains:n=>names.has(n),toggle:(n,on)=>on?names.add(n):names.delete(n)};if(id)elements.set(id,this);}
  set innerHTML(html){this.html=html;if(this.id==='tlStateFilter')this.value='';for(const match of html.matchAll(/id="([^"]+)"/g))if(!elements.has(match[1]))new Element(match[1]);}
  get innerHTML(){return this.html||'';}
  replaceChildren(){this.html='';}
  insertAdjacentHTML(position,html){this.innerHTML=(this.html||'')+html;}
  querySelectorAll(){return [];}
  addEventListener(name,listener){this.listeners[name]=listener;}
  click(){return this.onclick?.();}
}
const nav=new Element(),main=new Element(),head=new Element(),$=id=>elements.get(id);
const document={getElementById:$,head,querySelector:selector=>selector==='.nav'?nav:main,
  querySelectorAll:selector=>selector==='.view'?[$('view-linear')]:selector==='.nav button'?[$('tlNav')]:[],
  addEventListener:(name,listener)=>events[name]=listener};
const person=(tenant='company-a',role='worker',user=20)=>({member:{tenant_id:tenant,telegram_id:user,role,permissions:['linear']},capabilities:{modules:{linear:true}}});
let profile=person(),deferred=null,fail=true,uuid=0;const requests=[],posts=[],session=new Map(),pages=[];
const reply=(data,status=200)=>({ok:status===200,status,json:async()=>data});
const row={id:'issue',identifier:'BS-1',title:'<Private title>',account:'<Customer>',operator:'operator',description:'text',body:'<Details>',priority:2,
  state:{id:'done',name:'Done'},project:{name:'Project'},createdAt:'2026-10-08T10:00:00Z',updatedAt:'old',
  capabilities:{can_edit:false,can_change_status:false,buttons:[{id:'pass',label:'Test olundu',require_reason:false}]}};
async function fetch(url,options={}){requests.push(url);if(url.endsWith('/me'))return reply(profile);
  if(url.includes('/risks/'))throw new Error('Linear must not request risk reviews');
  if(options.body){posts.push(JSON.parse(options.body));return reply(fail?{success:false,error:'Uncertain'}:{success:true,issue_id:'issue'},fail?503:200);}
  if(pages.length)return pages.shift();
  if(deferred){const saved=deferred;deferred=null;return saved;}
  return reply({success:true,issues:[row],tenant_id:profile.member.tenant_id,user_id:20,config_version:'cfg',capabilities:{can_create:false},pageInfo:{hasNextPage:!url.includes('&after='),endCursor:'cursor'}});
}
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../docs/tenant-linear-board.js'),'utf8'),{window:{},URLSearchParams,document,fetch,JSON,Date,Map,Set,encodeURIComponent,
  crypto:{randomUUID:()=>`uuid-${++uuid}`},sessionStorage:{getItem:key=>session.get(key)||null,setItem:(key,value)=>session.set(key,value),removeItem:key=>session.delete(key)}});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  await flush();assert.equal($('tlNav').hidden,false);await $('tlNav').click();await flush();
  assert($('tlBoard').innerHTML.includes('&lt;Private title&gt;'));assert($('tlBoard').innerHTML.includes('&lt;Customer&gt;'));
  assert(!$('tlBoard').innerHTML.includes('data-edit'));assert(!$('tlBoard').innerHTML.includes('data-status'));
  assert($('tlBoard').innerHTML.includes('data-button-id="pass"'));assert.equal($('tlCreate').hidden,true);
  assert(requests.some(url=>url.includes('&after=cursor')), 'loads remaining pages without a click');
  assert.equal($('tlNext').hidden,true);
  const before=requests.length;$('tlSearch').value='changed';await $('tlNext').click();assert.equal(requests.length,before);
  $('tlSearch').value='';
  $('tlStateFilter').value='done';$('tlStateFilter').onchange();await $('tlRefresh').click();assert.equal($('tlStateFilter').value,'done','refresh preserves selected status');
  $('tlStateFilter').value='';$('tlStateFilter').onchange();
  const page=(issues,info={},config='cfg')=>reply({success:true,issues,tenant_id:profile.member.tenant_id,user_id:20,config_version:config,capabilities:{can_create:false},pageInfo:info});
  pages.push(page([row],{hasNextPage:true,endCursor:'first'}),page([],{hasNextPage:true,endCursor:'empty'}),page([{...row,id:'second',state:{id:'todo',name:'Todo'}}]));
  await $('tlRefresh').click();assert($('tlBoard').innerHTML.includes('Todo'));assert($('tlStateFilter').innerHTML.includes('Todo'));assert.equal($('tlNext').hidden,true);
  pages.push(page([row],{hasNextPage:true,endCursor:'retry'}),reply({success:false,error:'Try again'},503));
  await $('tlRefresh').click();assert($('tlBoard').innerHTML.includes('Private'));assert.equal($('tlNext').hidden,false);
  pages.push(page([{...row,id:'second'}]));await $('tlNext').click();assert(requests.at(-1).includes('&after=retry'));assert.equal($('tlNext').hidden,true);
  pages.push(page([row],{hasNextPage:true,endCursor:'repeat'}),page([],{hasNextPage:true,endCursor:'repeat'}));
  await $('tlRefresh').click();assert($('tlBoardNotice').textContent.includes('Səhifələmə'));assert.equal($('tlNext').hidden,true);
  pages.push(page([row],{hasNextPage:true,endCursor:'old'}),page([row],{},'changed'));
  await $('tlRefresh').click();assert(!$('tlBoard').innerHTML.includes('Private'));assert.equal($('tlNext').hidden,true);
  let resolveOld;deferred=new Promise(resolve=>resolveOld=resolve);const oldLoad=$('tlRefresh').click();
  $('tlSearch').value='new';pages.push(page([{...row,title:'New search'}]));await $('tlFind').click();
  resolveOld(page([row],{hasNextPage:true,endCursor:'stale'}));await oldLoad;assert($('tlBoard').innerHTML.includes('New search'));assert(!requests.some(url=>url.includes('&after=stale')));
  $('tlSearch').value='';await $('tlRefresh').click();
  const card={dataset:{issue:'issue'}};const target={closest:selector=>selector==='[data-issue]'?card:selector==='[data-button-id]'?{dataset:{buttonId:'pass'}}:null};
  await $('tlBoard').onclick({target});assert($('tlTaskModal').classList.contains('show'));
  $('tlTaskSave').click();await flush();await flush();assert.equal(posts.length,1);assert.equal(session.size,1);
  $('tlTaskSave').click();await flush();await flush();assert.equal(posts.length,2);assert.deepEqual(posts[0],posts[1]);
  fail=false;$('tlTaskSave').click();await flush();await flush();assert.equal(session.size,0);assert(!$('tlTaskModal').classList.contains('show'));
  let resolve;deferred=new Promise(r=>resolve=r);const loading=$('tlRefresh').click();
  profile=person('company-b');pages.push(page([]));events['tenant-profile']({detail:profile});resolve(reply({success:true,issues:[row],tenant_id:'company-a',user_id:20,config_version:'cfg',capabilities:{},pageInfo:{}}));await loading;await flush();
  assert(!$('tlBoard').innerHTML.includes('Private'));assert.equal($('tlRefresh').disabled,false);
  profile=person('company-b','master');profile.capabilities.modules.linear=false;events['tenant-profile']({detail:profile});assert.equal($('tlNav').hidden,true);
  profile=person('company-b','owner');events['tenant-profile']({detail:profile});await $('tlRefresh').click();await flush();await flush();
  assert.equal($('tlRiskReview'),undefined);
  assert(!requests.some(url=>url.includes('/risks/')), 'Linear must not fetch risk review data');
  profile=person('company-b','master');profile.capabilities.modules.linear=false;events['tenant-profile']({detail:profile});assert.equal($('tlNav').hidden,true);
  console.log('PASS: tenant Linear board scopes late responses, escapes cards, restricts actions, retries exact commands and leaves risk delivery to the notification center.');
})().catch(error=>{console.error(error);process.exitCode=1;});
