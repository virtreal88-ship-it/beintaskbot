/* Simulated DOM behavior: scoped loading, escaped cards, exact pending retries. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const elements=new Map(),events={};
class Element {
  constructor(id=''){this.id=id;this.value='';this.hidden=false;this.disabled=false;this.style={};this.dataset={};this.options=[];this.listeners={};const names=new Set();this.classList={add:n=>names.add(n),remove:n=>names.delete(n),contains:n=>names.has(n),toggle:(n,on)=>on?names.add(n):names.delete(n)};if(id)elements.set(id,this);}
  set innerHTML(html){this.html=html;for(const match of html.matchAll(/id="([^"]+)"/g))if(!elements.has(match[1]))new Element(match[1]);}
  get innerHTML(){return this.html||'';}
  insertAdjacentHTML(position,html){this.innerHTML=(this.html||'')+html;}
  querySelectorAll(){return [];}
  addEventListener(name,listener){this.listeners[name]=listener;}
  click(){return this.onclick?.();}
}
const nav=new Element(),main=new Element(),head=new Element(),$=id=>elements.get(id);
const document={getElementById:$,head,querySelector:selector=>selector==='.nav'?nav:main,
  querySelectorAll:selector=>selector==='.view'?[$('view-linear')]:selector==='.nav button'?[$('tlNav')]:[],
  addEventListener:(name,listener)=>events[name]=listener};
const person=(tenant='company-a',role='worker')=>({member:{tenant_id:tenant,telegram_id:20,role,permissions:['linear']},capabilities:{modules:{linear:true}}});
let profile=person(),deferred=null,fail=true,uuid=0;const requests=[],posts=[],session=new Map();
const reply=(data,status=200)=>({ok:status===200,status,json:async()=>data});
const row={id:'issue',identifier:'BS-1',title:'<Private title>',account:'<Customer>',operator:'operator',description:'text',body:'<Details>',priority:2,
  state:{id:'done',name:'Done'},project:{name:'Project'},createdAt:'2026-10-08T10:00:00Z',updatedAt:'old',
  capabilities:{can_edit:false,can_change_status:false,buttons:[{id:'pass',label:'Test olundu',require_reason:false}]}};
async function fetch(url,options={}){requests.push(url);if(url.endsWith('/me'))return reply(profile);
  if(options.body){posts.push(JSON.parse(options.body));return reply(fail?{success:false,error:'Uncertain'}:{success:true,issue_id:'issue'},fail?503:200);}
  if(deferred){const saved=deferred;deferred=null;return saved;}
  return reply({success:true,issues:[row],tenant_id:profile.member.tenant_id,user_id:20,config_version:'cfg',capabilities:{can_create:false},pageInfo:{hasNextPage:true,endCursor:'cursor'}});
}
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../docs/tenant-linear-board.js'),'utf8'),{document,fetch,JSON,Date,Map,Set,encodeURIComponent,
  crypto:{randomUUID:()=>`uuid-${++uuid}`},sessionStorage:{getItem:key=>session.get(key)||null,setItem:(key,value)=>session.set(key,value),removeItem:key=>session.delete(key)}});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  await flush();assert.equal($('tlNav').hidden,false);await $('tlNav').click();await flush();
  assert($('tlBoard').innerHTML.includes('&lt;Private title&gt;'));assert($('tlBoard').innerHTML.includes('&lt;Customer&gt;'));
  assert(!$('tlBoard').innerHTML.includes('data-edit'));assert(!$('tlBoard').innerHTML.includes('data-status'));
  assert($('tlBoard').innerHTML.includes('data-button-id="pass"'));assert.equal($('tlCreate').hidden,true);
  const before=requests.length;$('tlSearch').value='changed';await $('tlNext').click();assert.equal(requests.length,before);
  $('tlSearch').value='';
  const card={dataset:{issue:'issue'}};const target={closest:selector=>selector==='[data-issue]'?card:selector==='[data-button-id]'?{dataset:{buttonId:'pass'}}:null};
  await $('tlBoard').onclick({target});assert($('tlTaskModal').classList.contains('show'));
  $('tlTaskSave').click();await flush();await flush();assert.equal(posts.length,1);assert.equal(session.size,1);
  $('tlTaskSave').click();await flush();await flush();assert.equal(posts.length,2);assert.deepEqual(posts[0],posts[1]);
  fail=false;$('tlTaskSave').click();await flush();await flush();assert.equal(session.size,0);assert(!$('tlTaskModal').classList.contains('show'));
  let resolve;deferred=new Promise(r=>resolve=r);const loading=$('tlRefresh').click();
  profile=person('company-b');events['tenant-profile']({detail:profile});resolve(reply({success:true,issues:[row],tenant_id:'company-a',user_id:20,config_version:'cfg',capabilities:{},pageInfo:{}}));await loading;await flush();
  assert(!$('tlBoard').innerHTML.includes('Private'));assert.equal($('tlRefresh').disabled,false);
  profile=person('company-b','master');profile.capabilities.modules.linear=false;events['tenant-profile']({detail:profile});assert.equal($('tlNav').hidden,true);
  console.log('PASS: tenant Linear board scopes late responses, escapes cards, restricts actions and retries exact persisted commands.');
})().catch(error=>{console.error(error);process.exitCode=1;});
