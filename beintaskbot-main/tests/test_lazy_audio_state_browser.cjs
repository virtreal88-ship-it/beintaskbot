const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'),root=path.join(__dirname,'..');
(async()=>{const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined,args:['--no-sandbox']});try{for(const width of [390,1440]){
 const page=await browser.newPage({viewport:{width,height:900}});
 await page.route('https://test.local/**',r=>r.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')}));await page.goto('https://test.local/app');
 await page.evaluate(()=>{
  document.querySelectorAll('.view').forEach(e=>e.classList.remove('active'));document.getElementById('view-dialogs').classList.add('active');
  window.testRoute={provider:'connect',lead_id:41,agent_id:'assigned-agent',channel_id:'channel-a',conversation_id:'123456789012345@lid',customer_name:'Synthetic contact',phone:'+994500001234',customer_kind:'contact',state:'human',can_send:true,controls_allowed:true,available_actions:[]};
  window.tenantProfileSnapshot={member:{tenant_id:'tenant-a',telegram_id:33,role:'owner',active:true},capabilities:{modules:{dialogs:true,support:true}}};
  window.testMessages=[{id:'voice-row',external_id:'connect:voice',source:'connect',source_lead_id:41,source_channel_id:'channel-a',source_conversation_id:'123456789012345@lid',direction:'incoming',message_type:'audio',body:'',media_version:'a'.repeat(64),duration_seconds:9,analysis_state:'failed',author_type:'customer'}];
  const bytes=new Uint8Array(44+16000),wav=new DataView(bytes.buffer),ascii=(at,value)=>[...value].forEach((c,i)=>bytes[at+i]=c.charCodeAt(0));ascii(0,'RIFF');wav.setUint32(4,bytes.length-8,true);ascii(8,'WAVEfmt ');wav.setUint32(16,16,true);wav.setUint16(20,1,true);wav.setUint16(22,1,true);wav.setUint32(24,8000,true);wav.setUint32(28,16000,true);wav.setUint16(32,2,true);wav.setUint16(34,16,true);ascii(36,'data');wav.setUint32(40,16000,true);window.testWav=bytes;window.requests=[];window.mediaCount=0;window.partial=false;window.rateLimit=false;
  window.fetch=async(url,options={})=>{url=String(url);window.requests.push(url);if(url.includes('/crm/chat/attachment?')){window.mediaCount++;if(window.rateLimit)return new Response('{}',{status:429,headers:{'Content-Type':'application/json','Retry-After':'60'}});return new Response(window.testWav,{headers:{'Content-Type':'audio/wav'}});}
   if(url.includes('/api/platform/me'))return {ok:true,json:async()=>window.tenantProfileSnapshot};
   let result={};if(url.includes('/dialogs/history')){const dialog={...window.testRoute};if(window.partial)delete dialog.phone;result={dialog,messages:structuredClone(window.testMessages)};}else if(url.includes('/dialogs?'))result={dialogs:[],channels:[],agents:[]};return {ok:true,json:async()=>({success:true,result})};};
  // Exercise lifetime/rerender contracts without requiring a valid audio codec.
  Object.defineProperty(HTMLMediaElement.prototype,'pause',{value(){window.pauseCalls=(window.pauseCalls||0)+1;}});
 });
 for(const file of ['docs/tenant-operator-i18n.js','docs/tenant-operator-dialogs.js','docs/tenant-chat-attachments.js'])await page.addScriptTag({path:path.join(root,file)});
 await page.evaluate(()=>{document.dispatchEvent(new CustomEvent('tenant-profile',{detail:window.tenantProfileSnapshot}));return window.tenantOperator.openDialog(window.testRoute);});
 const voice=()=>page.locator('[data-message-id="voice-row"]');
 assert.match(await page.locator('.operator-client-phone').innerText(),/994500001234/);
 assert.equal(await page.evaluate(()=>window.mediaCount),0,'lazy history never downloads automatically');
 assert.equal(await voice().locator('audio').getAttribute('src'),null);
 assert.match(await voice().locator('[data-media-load-status]').innerText(),/не загружено|not loaded|yüklənməyib/);
 await voice().locator('[data-media-open]').click();await page.waitForFunction(()=>document.querySelector('audio.operator-native-audio')?.getAttribute('src')?.startsWith('blob:'));
 assert.equal(await page.evaluate(()=>window.mediaCount),1);
 assert.match(await voice().locator('[data-media-meta]').innerText(),/KB/);
 await page.evaluate(()=>{window.staleAudio=document.querySelector('audio.operator-native-audio');window.staleError=window.staleAudio.onerror;document.dispatchEvent(new CustomEvent('tenant-chat-history-reset'));document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));});
 await page.waitForFunction(()=>document.querySelector('audio.operator-native-audio')?.getAttribute('src')?.startsWith('blob:'));
 assert.equal(await page.evaluate(()=>window.mediaCount),1,'reset restores in-tab bytes without a request');
 await page.evaluate(()=>{const before=window.pauseCalls||0;window.staleError?.();if((window.pauseCalls||0)!==before)throw Error('stale decoder error paused restored audio');window.partial=true;});
 await page.evaluate(()=>window.tenantOperator.openDialog(window.testRoute));
 await page.waitForFunction(()=>document.querySelector('audio.operator-native-audio')?.getAttribute('src')?.startsWith('blob:'));
 assert.equal(await page.evaluate(()=>window.mediaCount),1,'background rerender and stale decoder error preserve cache');
 assert.match(await page.locator('.operator-client-phone').innerText(),/994500001234/,'partial DTO preserves verified phone');
 await page.evaluate(()=>{Object.defineProperty(document,'hidden',{value:true,configurable:true});document.dispatchEvent(new Event('visibilitychange'));Object.defineProperty(document,'hidden',{value:false,configurable:true});document.dispatchEvent(new Event('visibilitychange'));});
 await page.waitForFunction(()=>document.querySelector('audio.operator-native-audio')?.getAttribute('src')?.startsWith('blob:'));
 assert.equal(await page.evaluate(()=>window.mediaCount),1);
 await page.evaluate(()=>{window.partial=false;window.testRoute.phone='';window.testMessages[0].media_version='b'.repeat(64);});
 await page.evaluate(()=>window.tenantOperator.openDialog(window.testRoute));
 assert.equal(await voice().locator('audio').getAttribute('src'),null,'changed source version cannot reuse old bytes');
 assert.doesNotMatch(await page.locator('.operator-client-phone').innerText(),/123456789012345|994500001234/,'explicit blank and LID never fabricate phone');
 await page.evaluate(()=>{window.rateLimit=true;});await voice().locator('[data-media-open]').click();await page.waitForFunction(()=>window.mediaCount===2);
 await voice().locator('[data-media-open]').click();assert.equal(await page.evaluate(()=>window.mediaCount),2,'429 cooldown prevents repeated downloads');
 await page.evaluate(()=>{document.dispatchEvent(new CustomEvent('tenant-profile',{detail:{member:{tenant_id:'other-tenant',telegram_id:34,role:'owner',active:true}}}));});
 assert.equal(await page.locator('audio[src^="blob:"]').count(),0,'identity change revokes previous previews even when the dialog is removed');
 assert.equal((await page.evaluate(()=>window.requests)).filter(u=>/copilot|transcrib|\/asr|\/send/.test(u)).length,0);
 console.log('PASS lazy audio state '+width+': explicit load, reset/rerender/visibility memory reuse, stale decoder guard, source/identity fencing, phone/LID and 429');await page.close();
}}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
