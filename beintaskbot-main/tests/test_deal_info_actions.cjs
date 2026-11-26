const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const read=name=>fs.readFileSync(path.join(__dirname,'../docs',name),'utf8');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 const dealTemplate=read('tenant-app.html').split('async function openDeal(id)')[1].split('const profileCopy=')[0];
 assert(!dealTemplate.includes('<div class="meta">'),'Info does not repeat client, phone, stage or internal ID');
 assert(dealTemplate.includes('data-duplicate-search'),'duplicate search remains available');
 const dom=new JSDOM(read('tenant-app.html'),{url:'https://fixture.test/app?view=deals&lang=ru',runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[],errors=[],manual=[];
 try{
 w.addEventListener('error',e=>errors.push(e.error));w.alert=()=>{};w.confirm=()=>false;
 const profile={success:true,member:{tenant_id:'fixture',telegram_id:11,role:'manager',active:true,permissions:['deals','support','dialogs']},capabilities:{modules:{deals:true,support:true,dialogs:true}}};
 w.fetch=async(url,options={})=>{calls.push({url:String(url),method:options.method||'GET'});return {ok:true,json:async()=>String(url).includes('/me')?profile:{success:true,result:{tickets:[],members:[],channels:[],unresolved_count:0},items:[]}};};
 for(const name of ['tenant-operator-i18n.js','tenant-kommo-deal-card.js','tenant-deal-completion.js','tenant-support-inbox.js'])w.eval(read(name));
 d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:profile}));
 const host=d.getElementById('modalContent');
 function open(withInfo=true){
 host.innerHTML='<div class="meta">Deal fields</div><div class="chat-placeholder"><div id="chatRows"></div><div><textarea id="chatText"></textarea></div></div>';
 if(!withInfo)d.querySelectorAll('[data-deal-header-extra]').forEach(node=>{if(!node.classList.contains('deal-chat-identity'))node.remove();});
 if(withInfo)w.tenantKommoDealCard.mount({host,deal:{kommo_lead_id:7,contact_name:'Client',phone:'+994000'},api:async()=>({notes:[]}),isCurrent:()=>true});
 d.dispatchEvent(new w.CustomEvent('tenant-chat-open',{detail:{lead_id:7,channel_id:'channel',conversation_id:'conversation'}}));
 }
 open();await settle();
 const info=d.getElementById('dealInfoPanel'),support=d.getElementById('supportCreateFromChat'),complete=[...host.querySelectorAll('button')].find(b=>b.textContent==='Sövdələşməni tamamla'),chat=d.querySelector('[data-deal-pane=chat]'),data=d.querySelector('[data-deal-pane=data]');
assert.equal(complete,undefined);assert(support);assert.equal(support.parentElement.className,'deal-card-data-actions');assert.equal(support.textContent,'Создать обращение');assert.equal(support.getAttribute('aria-label'),'Создать обращение');assert.equal(support.title,'Создать обращение');assert.equal(info.hidden,false);assert.equal(host.querySelector(':scope > button'),null);
 const input=d.getElementById('chatText');input.value='Unsent draft';assert.equal(info.hidden,false);assert.equal(d.querySelector('.chat-placeholder').hidden,true);

 w.tenantOperator={hasRight:()=>true,openManualCaseCreate:async(...args)=>manual.push(args)};support.click();await settle();assert.equal(manual.length,1);assert.deepEqual(JSON.parse(JSON.stringify(manual[0][0])),{provider:'connect',lead_id:7,channel_id:'channel',conversation_id:'conversation'});assert.equal(manual[0][1],support);assert.equal(manual[0][2](),true);
 chat.click();assert.equal(info.hidden,true);assert.equal(input.value,'Unsent draft');assert.equal(d.querySelector('.chat-placeholder').hidden,false);assert.equal(calls.filter(c=>c.method!=='GET').length,0,'opening Info and cancelled completion never writes');
 open();assert.equal(d.querySelectorAll('#supportCreateFromChat').length,1,'reopening does not duplicate header action');assert.equal(support.isConnected,false);assert.equal(manual[0][2](),false,'old opener invalidated on navigation');
 open(false);assert.equal(d.getElementById('supportCreateFromChat').parentElement,host,'legacy card fallback preserved');
 delete w.tenantOperator;profile.capabilities.modules={};d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:profile}));open();assert.equal(d.getElementById('supportCreateFromChat'),null);assert.equal([...host.querySelectorAll('button')].some(b=>b.textContent==='Sövdələşməni tamamla'),false,'existing capability gates preserved');
 assert.deepEqual(errors,[]);console.log('PASS deal actions and header plus: hidden initially, toggle, draft, handlers, no writes, legacy fallback and capability gates');
 }finally{w.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
