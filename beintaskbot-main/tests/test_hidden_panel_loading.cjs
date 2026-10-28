/* Real shipped scripts/bundle with synthetic HTTP only: no accounts, providers or production writes. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.join(__dirname,'..');
const read=(name,ref)=>ref?execFileSync('git',['show',`${ref}:beintaskbot-main/${name}`],{cwd:root,encoding:'utf8'}):fs.readFileSync(path.join(root,name),'utf8');
const tick=async()=>{for(let i=0;i<12;i++)await new Promise(r=>setImmediate(r));await new Promise(r=>setTimeout(r,70));};
const owner={success:true,member:{tenant_id:'tenant-a',telegram_id:11,role:'owner',active:true,permissions:['linear']},capabilities:{modules:{dialogs:true,linear:true,settings:true,integrations:true}},integrations:[],companies:[],members:[],account:{first_name:'Synthetic A'}};
function fixture({view='dialogs',profile=owner,ref,core=false,workspace=false,hold=()=>false}={}){
 const errors=[],console=new VirtualConsole();console.on('jsdomError',e=>{if(e.type!=='css parsing')errors.push(e);});
 const dom=new JSDOM(read('docs/tenant-app.html',ref),{url:`https://fixture.test/app?view=${view}`,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
 const w=dom.window,requests=[];let current=profile,clock=1000;
 w.document.body.dataset.appUi='fixture';w.document.querySelectorAll('.view').forEach(n=>n.classList.toggle('active',n.id==='view-'+view));
 w.Headers=Headers;w.Response=Response;w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
 w.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};w.Date.now=()=>clock;
 w.tenantI18n={language:'en',t:k=>k,source:t=>t};w.confirm=()=>false;w.alert=message=>errors.push(new Error(message));
 const result=url=>url==='/api/platform/me'?current:url.includes('/news?')?{tenant_id:current.member.tenant_id,user_id:current.member.telegram_id,news:[{id:'synthetic-news',title:'Synthetic '+current.member.tenant_id,status:'pending',summary:'Fixture',project_name:'Fixture'}]}:url.includes('beinsystems')?{integration:{}}:url.includes('linear/settings')?{members:[],settings:{},status:'not_connected'}:url.includes('/companies')?{companies:[]}:url.includes('/news/telegram')?{chat_id:'@synthetic',timezone:'Asia/Baku',start:'09:00',end:'18:00',interval_minutes:30}:{};
 w.fetch=(url,options={})=>new Promise(resolve=>{const request={url:String(url),options,reply:(data=result(String(url)),status=200)=>resolve(new Response(JSON.stringify({success:status<400,...data}),{status,headers:{'Content-Type':'application/json'}}))};requests.push(request);if(!hold(request))request.reply();});
 const run=name=>w.eval(read('docs/'+name,ref));run('tenant-request-coordinator.js');if(!ref)run('tenant-view-demand.js');
 for(const name of ['tenant-linear-rules.js','tenant-linear-settings.js','tenant-news.js','tenant-news-ai-settings.js','tenant-news-telegram-settings.js','tenant-chat-ai-settings.js'])run(name);
 if(workspace)run('tenant-workspace.js');
 if(core)w.eval([...w.document.scripts].find(s=>s.textContent.includes('const savedView=sessionStorage')).textContent);
 run('tenant-chakra-ui.js');
 const emit=(type,detail)=>w.document.dispatchEvent(new w.CustomEvent(type,{detail}));
 const show=view=>{if(core)w.tenantAppNavigate(view,true);else{w.document.querySelectorAll('.view').forEach(n=>n.classList.toggle('active',n.id==='view-'+view));emit('tenant-view',{view});}};
 return {w,requests,errors,emit,show,advance:ms=>clock+=ms,profile:data=>{current=data;emit('tenant-profile',data);},nonProfile:()=>requests.filter(r=>r.url!=='/api/platform/me'),close:()=>{w.close();assert.deepEqual(errors,[]);}};
}
async function measurement(ref){const b=fixture({ref});try{await tick();await tick();const urls=b.nonProfile().map(r=>r.url);assert.equal(urls.length,ref?12:0);console.log(JSON.stringify({ref:ref||'working-tree',hiddenDataGets:urls.length,urls}));}finally{b.close();}}
async function delayedBootstrap(){
 const b=fixture({core:true,hold:r=>r.url==='/api/platform/me'});try{
  await tick();assert.equal(b.requests.length,1);assert.equal(b.w.document.getElementById('tnNav').hidden,true);
  b.requests[0].reply();await tick();assert.equal(b.w.document.getElementById('tnNav').hidden,false);
  assert.equal(b.nonProfile().length,0,'menu is available immediately after authorization without hidden data dependencies');
  b.w.eval(read('docs/tenant-push.js'));await tick();assert.ok(b.requests.some(r=>r.url.endsWith('/push/devices')),'background Push setup is not gated on Settings');
 }finally{b.close();}
 console.log('PASS delayed /me: one shared authorization request, News menu needs no secondary read; Push remains eager');
}
async function navigation(){
 for(const view of ['news','integrations','settings']){
  const b=fixture({view,core:true,workspace:true});try{
   await tick();await tick();assert.ok(b.w.document.getElementById('view-'+view).classList.contains('active'),`direct ${view}`);
   if(view==='news')assert.match(b.w.document.getElementById('tnRows').textContent,/Synthetic tenant-a/);
   if(view==='integrations')assert.ok(b.requests.some(r=>r.url.endsWith('/integrations/connect')));
   if(view==='settings'){
    assert.ok(!b.requests.some(r=>r.url.endsWith('/sessions')),'hidden Diagnostics stays deferred even on Settings');
    b.w.tenantOpenSettingsSection('advanced');await tick();assert.ok(b.requests.some(r=>r.url.endsWith('/sessions')));
   }
   const before=b.nonProfile().length;b.show('dialogs');b.w.history.back();await tick();assert.ok(b.w.document.getElementById('view-'+view).classList.contains('active'),'real back restores view');
   if(view!=='settings')assert.equal(b.nonProfile().length,before,'warm back reuses rendered data');
   b.w.history.forward();await tick();assert.ok(b.w.document.getElementById('view-dialogs').classList.contains('active'),'real forward');
  }finally{b.close();}
 }
 const b=fixture({core:true});try{await tick();assert.equal(b.w.document.getElementById('tnNav').hidden,false,'menu uses existing binding, without reading news');
  b.w.document.getElementById('tnNav').click();await tick();assert.equal(b.w.location.search,'?view=news');assert.match(b.w.document.getElementById('tnRows').textContent,/Synthetic/);
  const count=b.requests.length;b.show('dialogs');b.show('news');await tick();assert.equal(b.requests.length,count,'warm news reuses data');
  b.show('dialogs');b.advance(30001);b.show('news');await tick();assert.equal(b.requests.length,count+2,'expired news visit reloads');
  for(const id of ['tnClose','tnCancel']){b.w.document.getElementById('tnCreate').click();assert.ok(b.w.document.getElementById('tnModal').classList.contains('show'));b.w.document.getElementById(id).click();assert.ok(!b.w.document.getElementById('tnModal').classList.contains('show'));}
  b.w.document.getElementById('tnRefresh').click();await tick();assert.equal(b.requests.length,count+4,'explicit refresh bypasses retention');
  assert.ok(b.requests.every(r=>(r.options.method||'GET')==='GET'));
 }finally{b.close();}
 console.log('PASS direct links, actual back/forward, menu, warm/expired visits, explicit refresh, News Close/Cancel');
}
async function integrations(){
 const b=fixture({workspace:true});try{
  await tick();const host=b.w.document.createElement('div');host.id='fixture-host';b.w.document.body.append(host);
  const open=provider=>b.emit('tenant-integration-panel',{provider,hostId:host.id,open:true});
  open('telegram');await tick();assert.ok(host.querySelector('#tnChannelPanel'));assert.equal(b.nonProfile().length,1);
  b.w.document.getElementById('tnChannelAddress').value='unsaved';b.emit('tenant-integration-panel',{open:false});assert.equal(host.children.length,0);
  open('telegram');await tick();assert.equal(b.nonProfile().length,1);assert.equal(b.w.document.getElementById('tnChannelAddress').value,'unsaved');
  open('openai');await tick();assert.ok(host.querySelector('#tcAiPanel'));assert.ok(host.querySelector('#tnAiPanel'));assert.equal(b.nonProfile().length,3);
  open('linear');await tick();assert.ok(host.querySelector('#tenantLinearPanel'));assert.equal(b.nonProfile().length,3,'Linear catalog remains explicit');
  b.w.document.getElementById('tlLoad').click();await tick();assert.equal(b.nonProfile().length,4);
  b.emit('tenant-integration-panel',{open:false});assert.ok(b.requests.every(r=>(r.options.method||'GET')==='GET'),'opening/closing never saves, probes or sends');
  // Delayed placement cannot run after Close, even when the host appears later.
  b.emit('tenant-integration-panel',{provider:'telegram',hostId:'late-host',open:true});b.emit('tenant-integration-panel',{open:false});
  const late=b.w.document.createElement('div');late.id='late-host';b.w.document.body.append(late);await tick();assert.equal(late.children.length,0);
 }finally{b.close();}
 console.log('PASS legacy integration panels, warm form retention, explicit Linear load, Close/cancelled placement');
}
async function marketplaceModal(){
 const b=fixture({view:'integrations',core:true,workspace:true});try{
  await tick();await tick();
  let card=[...b.w.document.querySelectorAll('#chakra-integrations-root *')].find(n=>n.childElementCount===0&&n.textContent==='OpenAI');
  while(card&&!card.querySelector('button'))card=card.parentElement;
  assert.ok(card);card.querySelector('button').click();await tick();
  const dialog=[...b.w.document.querySelectorAll('[role="dialog"]')].find(n=>n.textContent.includes('OpenAI'));assert.ok(dialog);
  const settings=[...dialog.querySelectorAll('[role="tab"]')].find(n=>n.textContent==='Settings');assert.ok(settings);
  settings.click();await tick();await tick();assert.ok(b.w.document.querySelector('#integrationSettingsHost #tcAiPanel'));
  const before=b.requests.length;
  const close=[...dialog.querySelectorAll('button')].find(n=>n.textContent==='Close');assert.ok(close);close.click();await tick();
  assert.equal(b.w.document.querySelector('#integrationSettingsRepository #tcAiPanel')?.hidden,true);
  assert.equal(b.requests.length,before);
  card.querySelector('button').click();await tick();
  [...b.w.document.querySelectorAll('[role="tab"]')].find(n=>n.textContent==='Settings').click();await tick();
  assert.ok(b.w.document.querySelector('#integrationSettingsHost #tcAiPanel'),'real modal can reopen retained panels');
  b.emit('tenant-auth-invalid');await tick();assert.equal(b.w.document.getElementById('tcAiPanel'),null,'cleanup cannot resurrect an auth-reset panel');
  assert.ok(b.requests.every(r=>(r.options.method||'GET')==='GET'));
 }finally{b.close();}
 console.log('PASS real React marketplace modal: Settings mounts legacy panels, Close restores them without writes');
}
async function dirtyReauthorization(){
 let holdAuth=false;
 const b=fixture({view:'integrations',core:true,workspace:true,hold:r=>holdAuth&&r.url==='/api/platform/me'});try{
  await tick();await tick();
  let card=[...b.w.document.querySelectorAll('#chakra-integrations-root *')].find(n=>n.childElementCount===0&&n.textContent==='OpenAI');
  while(card&&!card.querySelector('button'))card=card.parentElement;
  card.querySelector('button').click();await tick();
  const tab=[...b.w.document.querySelectorAll('#integrationMarketplaceDialog [role="tab"]')].find(n=>n.textContent==='Settings');tab.click();await tick();await tick();
  const key=b.w.document.getElementById('tcAiKey');key.value='synthetic-unsaved-key';key.dispatchEvent(new b.w.Event('input',{bubbles:true}));
  const fresh={...owner,member:{...owner.member,display_name:'Changed display label'},integrations:[{provider:'connect',updated_at:'new-status'}],capabilities:{...owner.capabilities,deal_board:[{name:'Renamed pipeline'}]}};
  b.profile(fresh);await tick();assert.equal(b.w.document.getElementById('tcAiKey'),key,'background freshness retains edited node');assert.equal(key.value,'synthetic-unsaved-key');
  holdAuth=true;b.w.dispatchEvent(new b.w.Event('focus'));await tick();
  assert.ok(b.w.document.documentElement.hasAttribute('data-tenant-panels-pending'));
  assert.ok(b.w.document.getElementById('integrationMarketplaceDialog').hasAttribute('inert'));
  const before=b.requests.length;b.w.document.getElementById('tcAiSave').click();await tick();assert.equal(b.requests.length,before,'auth-pending blocks even programmatic DOM click dispatch');
  b.requests.filter(r=>r.url==='/api/platform/me').at(-1).reply(fresh);await tick();await tick();
  assert.equal(b.w.document.getElementById('tcAiKey'),key);assert.equal(key.value,'synthetic-unsaved-key');assert.ok(b.w.document.querySelector('#integrationSettingsHost #tnAiPanel'),'clean sibling returns to retained portal');assert.ok(!b.w.document.documentElement.hasAttribute('data-tenant-panels-pending'));
  [...b.w.document.querySelectorAll('#integrationMarketplaceDialog button')].find(n=>n.textContent==='Close').click();await tick();
  b.advance(30001);card.querySelector('button').click();await tick();
  [...b.w.document.querySelectorAll('#integrationMarketplaceDialog [role="tab"]')].find(n=>n.textContent==='Settings').click();await tick();
  assert.equal(b.w.document.getElementById('tcAiKey'),key,'TTL does not discard dirty modal');assert.equal(key.value,'synthetic-unsaved-key');
  b.w.dispatchEvent(new b.w.Event('focus'));await tick();b.requests.filter(r=>r.url==='/api/platform/me').at(-1).reply({},401);await tick();
  assert.equal(b.w.document.getElementById('tcAiKey'),null,'failed reauthorization destroys dirty retained state');
 }finally{b.close();}
 const news=fixture({view:'news',core:true});try{
  await tick();news.w.document.getElementById('tnCreate').click();const title=news.w.document.getElementById('tnTitle');title.value='Unsaved news';title.dispatchEvent(new news.w.Event('input',{bubbles:true}));
  news.emit('tenant-auth-pending');assert.ok(news.w.document.getElementById('tnModal').hasAttribute('inert'));
  news.profile({...owner,member:{...owner.member,display_name:'New name'}});await tick();assert.equal(news.w.document.getElementById('tnTitle'),title);assert.equal(title.value,'Unsaved news');
  news.w.document.getElementById('tnCancel').click();const reads=news.requests.length;news.show('dialogs');news.advance(30001);news.show('news');await tick();assert.equal(news.requests.length,reads+2,'cancelled draft does not disable News refresh');
  news.w.document.getElementById('tnCreate').click();
  news.profile({...owner,member:{...owner.member,telegram_id:22}});assert.equal(news.w.document.getElementById('tnTitle'),null,'user change destroys editor');await tick();
 }finally{news.close();}
 const loading=fixture({hold:r=>r.url.endsWith('/news/telegram/settings')});try{
  await tick();loading.emit('tenant-integration-panel',{provider:'telegram',open:true});const old=loading.requests.at(-1);
  loading.emit('tenant-auth-pending');old.reply({chat_id:'OLD PENDING VALUE'});await tick();assert.ok(loading.w.document.documentElement.hasAttribute('data-tenant-panels-pending'));
  loading.profile(owner);await tick();const replacement=loading.requests.at(-1);assert.notEqual(replacement,old,'interrupted clean initial load restarts');replacement.reply({chat_id:'FRESH VALUE'});await tick();assert.equal(loading.w.document.getElementById('tnChannelAddress').value,'FRESH VALUE');
 }finally{loading.close();}
 console.log('PASS dirty forms: background freshness, same-user reauth, portal/News conceal, blocked actions, TTL retention, failed/changed auth reset, initial-load restart');
}
async function isolation(){
 const b=fixture({hold:r=>r.url.includes('/news?')||r.url.endsWith('/news/telegram/settings')});try{
  await tick();b.show('news');await tick();const old=b.requests.find(r=>r.url.includes('/news?'));
  b.emit('tenant-integration-panel',{provider:'telegram',open:true});const channel=b.requests.at(-1);
  const next={...owner,member:{...owner.member,tenant_id:'tenant-b',telegram_id:22}};b.profile(next);await tick();
  old.reply({tenant_id:'tenant-a',user_id:11,news:[{id:'old',title:'OLD TENANT'}]});channel.reply({chat_id:'OLD CHANNEL'});await tick();
  assert.doesNotMatch(b.w.document.body.textContent,/OLD TENANT|OLD CHANNEL/);
  b.emit('tenant-auth-invalid');assert.equal(b.w.document.getElementById('tnRows').textContent.includes('Synthetic'),false);assert.equal(b.w.document.getElementById('tnChannelPanel'),null);
  const count=b.requests.length;b.show('news');b.emit('tenant-integration-panel',{provider:'telegram',open:true});await tick();assert.equal(b.requests.length,count,'logout cannot reactivate retained data');
 }finally{b.close();}
 for(const allowed of [false,true]){
  const worker={...owner,member:{...owner.member,role:'worker'},integrations:[{provider:'linear',status:'not_connected',metadata:{settings:{members:{11:{can_review_news:allowed}}}}}]};
  const b=fixture({profile:worker,core:true});try{await tick();assert.equal(b.w.document.getElementById('tnNav').hidden,!allowed);assert.equal(b.nonProfile().length,0);if(allowed){b.show('news');await tick();assert.match(b.w.document.getElementById('tnRows').textContent,/Synthetic/);}b.profile({...worker,integrations:[]});assert.equal(b.w.document.getElementById('tnNav').hidden,true);assert.doesNotMatch(b.w.document.getElementById('tnRows').textContent,/Synthetic/);}finally{b.close();}
 }
 const focus=fixture({view:'integrations',core:true});try{for(let i=0;i<10&&focus.nonProfile().length<5;i++)await tick();const before=focus.nonProfile().length;assert.equal(before,5);const search=focus.w.document.querySelector('#chakra-integrations-root input');search.value='OpenAI';search.dispatchEvent(new focus.w.Event('input',{bubbles:true}));focus.w.dispatchEvent(new focus.w.Event('focus'));await tick();await tick();assert.equal(focus.nonProfile().length,before+5,'same-scope reauthorization reloads visible integration status');focus.profile({...owner,member:{...owner.member,tenant_id:'tenant-b'},account:{first_name:'Synthetic B'}});await tick();assert.ok(!focus.w.document.body.textContent.includes('Synthetic A'));}finally{focus.close();}
 console.log('PASS tenant/user switch, delayed responses, logout, Linear-bound worker visibility/revocation, same-scope focus');
}
(async()=>{const index=process.argv.indexOf('--baseline-ref');if(index>=0)return measurement(process.argv[index+1]);await measurement();await delayedBootstrap();await navigation();await integrations();await marketplaceModal();await dirtyReauthorization();await isolation();})().catch(e=>{console.error(e);process.exitCode=1;});
