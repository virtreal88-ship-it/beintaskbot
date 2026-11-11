const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const read=name=>fs.readFileSync(path.join(__dirname,'../docs',name),'utf8');
const settle=async()=>{for(let n=0;n<12;n++)await new Promise(resolve=>setImmediate(resolve))};
(async()=>{
 const dom=new JSDOM('<head></head><body><nav class="nav"></nav><main></main></body>',{url:'https://fixture.test/app?view=news',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,calls=[],profile={member:{tenant_id:'company-a',telegram_id:32,active:true,role:'operator',permissions:[]},capabilities:{pages:{news:true},modules:{linear:false}},integrations:[]};
 w.tenantProfileSnapshot=profile;w.tenantI18n={language:'ru'};w.Response=Response;w.confirm=()=>true;w.tenantAppNavigate=view=>{w.document.querySelectorAll('.view').forEach(node=>node.classList.toggle('active',node.id==='view-'+view));w.document.dispatchEvent(new w.CustomEvent('tenant-view',{detail:{view}}))};
 let held=null;w.fetch=async(url,options={})=>{calls.push({url,options});if(String(url).includes('news?')){const response=()=>new Response(JSON.stringify({success:true,tenant_id:profile.member.tenant_id,user_id:profile.member.telegram_id,news:[{id:'n1',title:'Shared published news',summary:'Safe text',published_at:'2026-11-10',href:'',url:'',project_name:'Test'}]}));if(held)return new Promise(resolve=>held.resolve=()=>resolve(response()));return response()}throw Error('Unexpected endpoint '+url)};
 try{
  w.eval(read('tenant-view-demand.js'));w.eval(read('tenant-news.js'));await settle();
  assert.equal(w.tenantViewDemand.newsAvailable(profile),true);assert.equal(w.tenantViewDemand.newsReviewAvailable(profile),false);
  assert(calls.some(call=>call.url==='/api/platform/news?feed=published'));assert.equal(calls.some(call=>call.url.includes('/telegram')),false);
  assert.match(w.document.getElementById('tnRows').textContent,/Shared published news/);
  for(const id of ['tnCreate','tnStatus','tnRecover'])assert(w.document.getElementById(id).hidden,id);
  assert.equal(w.document.querySelector('[data-edit],[data-reject],[data-telegram],[data-cancel-telegram]'),null);
  assert.doesNotMatch(w.document.getElementById('tnRows').textContent,/Telegram|AI:/);
  const count=calls.length;w.document.getElementById('tnCreate').click();w.document.getElementById('tnRecover').click();assert.equal(w.document.getElementById('tnModal').classList.contains('show'),false);assert.equal(calls.length,count);
  held={};w.document.getElementById('tnRefresh').click();await settle();w.document.dispatchEvent(new w.CustomEvent('tenant-auth-invalid'));held.resolve();await settle();assert.equal(w.document.getElementById('tnNav').hidden,true);assert.doesNotMatch(w.document.getElementById('tnRows').textContent,/Shared published news/,'Late read cannot resurrect revoked content');
  console.log('PASS common news reader without Linear: safe feed only, no review/Telegram calls or controls, forged clicks inert and auth revoke fences');
 }finally{dom.window.close()}
})().catch(error=>{console.error(error.stack);process.exitCode=1});
