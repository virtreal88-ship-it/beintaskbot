/* Real application HTML/CSS. All HTTP/provider/AI boundaries are synthetic. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const root=path.join(__dirname,'..'),tenant='00000000-0000-0000-0000-000000000101',channel='00000000-0000-0000-0000-000000000201';
const dialog={provider:'connect',lead_id:41,channel_id:channel,conversation_id:'synthetic@s.whatsapp.net',customer_kind:'contact',customer_name:'Synthetic customer',channel_name:'WhatsApp',display_state:'human',state:'human',ai_availability:'stopped',available_actions:[],can_send:true,controls_allowed:true,agent_status:'manual',agent_mode:'manual',agent_id:'00000000-0000-0000-0000-000000000301',agent_name:'Assigned Helper'};
const message=(id='voice-a',kind='audio',version='a')=>({id,external_id:id,source:'connect',source_lead_id:41,source_channel_id:channel,source_conversation_id:dialog.conversation_id,direction:'incoming',message_type:kind,media_version:version.repeat(64),body:'',duration_seconds:30,analysis_state:'transcription_unknown',happened_at:'2026-10-21T10:00:00Z'});
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aHZkAAAAASUVORK5CYII=','base64');
const wav=Buffer.alloc(44+480000);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(8000,24);wav.writeUInt32LE(16000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(wav.length-44,40);
const deferred=()=>{let release;const promise=new Promise(resolve=>release=resolve);return {promise,release};};
const server=()=>({cache:new Map(),flights:new Map(),requests:[],upstream:[],gates:new Map(),status:200,networkFailure:false,active:0,maxActive:0,self429:0,paidOrSend:0});
const launch=()=>chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||process.env.CHROMIUM_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':chromium.executablePath()),args:['--no-sandbox']});
async function fixture(browser,srv,{width=390,height=844,withoutLocks=false,messages=[message()]}={}){
 const context=await browser.newContext({viewport:{width,height}}),page=await context.newPage(),errors=[];
 await context.addInitScript(()=>{window.fixtureVisibleMedia=new Set();const Original=IntersectionObserver;window.IntersectionObserver=class extends Original{constructor(callback,options){super((entries,observer)=>{callback(entries,observer);for(const entry of entries)if(entry.isIntersecting&&entry.target.dataset.chatAttachment)window.fixtureVisibleMedia.add(entry.target.dataset.chatAttachment);},options);}};});
 if(withoutLocks)await context.addInitScript(()=>Object.defineProperty(navigator,'locks',{value:undefined,configurable:true}));
 const state={authed:true,tenant,user:33,dialog:{...dialog},messages,navigations:0};page.on('pageerror',error=>errors.push(error.message));page.setDefaultTimeout(10000);
 await context.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),name=url.pathname;
  if(name==='/app'){state.navigations++;return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8')});}
  if(name==='/login')return route.fulfill({contentType:'text/html',body:'<p>Signed out fixture</p>'});
  if(name==='/api/platform/logout'){state.authed=false;return route.fulfill({json:{success:true}});}
  if(name.startsWith('/assets/')){const file=path.join(root,'docs',path.basename(name));return fs.existsSync(file)?route.fulfill({path:file}):route.fulfill({status:404,body:''});}
  if(name==='/api/platform/me')return route.fulfill({json:state.authed?{success:true,member:{tenant_id:state.tenant,telegram_id:state.user,role:'worker',permissions:['dialogs'],active:true},capabilities:{modules:{dialogs:true}}}:{success:false}});
  if(name.endsWith('/crm/chat/attachment')){
   const id=url.searchParams.get('message_id'),cacheOnly=url.searchParams.get('cache_only')==='1',automatic=url.searchParams.get('auto_preview')==='1';
   srv.requests.push({id,cacheOnly,automatic,url:url.href});
   assert.equal(req.method(),'GET');assert.equal(state.authed,true);assert.equal(url.searchParams.get('expected_tenant_id'),state.tenant);assert.equal(url.searchParams.get('expected_user_id'),String(state.user));
   const key=['expected_tenant_id','channel_id','conversation_id','message_id','expected_media_version'].map(k=>url.searchParams.get(k)).join('|');
   const item=state.messages.find(m=>m.external_id===id)||message(id),binary=item.message_type==='image'?{mime:'image/png',data:png}:{mime:'audio/wav',data:wav};
   const send=async value=>route.fulfill({contentType:value.mime,body:value.data}).catch(()=>{});
   if(cacheOnly)return srv.cache.has(key)?send(srv.cache.get(key)):route.fulfill({status:204,body:''});
   if(srv.networkFailure)return route.abort('failed');
   if(!srv.flights.has(key)){
    if(automatic&&srv.active>=1){srv.self429++;return route.fulfill({status:429,headers:{'Retry-After':'1'},json:{error:'Synthetic automatic slot busy'}});}
    if(automatic){srv.active++;srv.maxActive=Math.max(srv.maxActive,srv.active);}
    srv.flights.set(key,(async()=>{try{srv.upstream.push({key,id,automatic});if(srv.gates.has(id))await srv.gates.get(id).promise;if(srv.status===200)srv.cache.set(key,binary);return srv.status;}finally{if(automatic)srv.active--;}})());
   }
   const status=await srv.flights.get(key);srv.flights.delete(key);
   return status===200?send(binary):route.fulfill({status,headers:{'Retry-After':'17'},json:{error:'Synthetic upstream failure'}}).catch(()=>{});
  }
  if(req.method()!=='GET'&&/copilot|transcrib|dispatch|chat\/send/.test(name)){srv.paidOrSend++;assert.fail('Opening media dispatched AI/ASR/send');}
  let result={};if(name.endsWith('/dialogs/history'))result={dialog:state.dialog,messages:state.messages,next_before:null};
  else if(name.endsWith('/dialogs'))result={dialogs:[{...state.dialog,last_message_id:'last',last_message_direction:'incoming',last_message_text:'Synthetic voice',last_message_at:'2026-10-21T10:00:00Z'}],channels:[{id:'connect:'+state.dialog.channel_id,name:'WhatsApp'}],agents:[],next_offset:null};
  else if(name.endsWith('/developer-marker'))result={enabled:false};else if(name.endsWith('/replies'))result={replies:[],consultations:[]};
  return route.fulfill({json:{success:true,result}});
 });
 const card=id=>page.locator('[data-chat-attachment="'+id+'"]');
 const loaded=id=>page.waitForFunction(id=>{const audio=document.querySelector('[data-chat-attachment="'+id+'"] audio');return !!audio?.getAttribute('src')&&audio.readyState>=1;},id);
 const open=async beforeSelect=>{await page.goto('https://crm.test/app?view=dialogs');if(beforeSelect)await beforeSelect();await page.locator('.operator-row').first().click();};
 const refresh=()=>page.evaluate(()=>window.tenantOperator.openDialog(window.tenantOperator.currentDialog));
 const action=async(id,command='load',mode='mouse')=>{
  if(mode==='keyboard'){await page.locator('article[data-message-id="'+id+'"]').focus();await page.keyboard.press('Shift+F10');}
  else if(mode==='touch'){
   await card(id).locator('.operator-audio-heading').evaluate(n=>{const r=n.getBoundingClientRect();n.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerType:'touch',isPrimary:true,pointerId:7,clientX:r.x+12,clientY:r.y+8}));});
   await page.waitForTimeout(600);
   await card(id).locator('.operator-audio-heading').evaluate(n=>n.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,pointerType:'touch',isPrimary:true,pointerId:7})));
  }else await card(id).locator('.operator-audio-heading').click({button:'right'});
  const menu=page.locator('.operator-reply-context-menu');await menu.waitFor();await menu.locator('[data-context-file-action="'+command+'"]').click();
 };
 return {page,context,state,errors,card,loaded,open,refresh,action};
}
module.exports={fixture,server,message,launch,deferred,tenant,channel,dialog};
