/* Real Chromium, IndexedDB, Web Locks and synthetic Web Audio. No provider traffic. */
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { chromium, firefox, webkit } = require('playwright');

const docs = path.resolve(__dirname, '../docs');
const scripts = ['tenant-voice-recorder.js','tenant-microphone-lease.js','tenant-customer-voice-store.js','tenant-customer-voice-ui.js'];
const html = `<!doctype html><meta charset="utf-8"><div id="view-dialogs" class="active"><div class="operator-chat-scrollarea" style="height:80px;overflow:auto"><div id="chatRows"><article data-customer-voice-status="true" data-customer-voice-lead="7" data-message-id="6b9f1ac1-d693-4f9f-a400-d9b8372306f8">voice</article></div></div><div class="operator-composer">
 <textarea id="chatText"></textarea><div class="operator-composer-actions"><button id="sendChat">send</button></div></div></div>
 <script>window.tenantI18n={language:'ru'};const lead=new URLSearchParams(location.search).get('lead')||'7';
 window.fixtureRoute={provider:'connect',lead_id:Number(lead),channel_id:'channel',conversation_id:'chat-'+lead};
 window.tenantOperator={member:{tenant_id:'tenant',telegram_id:20},get currentDialog(){return window.fixtureRoute},openDialog:async()=>{window.reloads=(window.reloads||0)+1},updateCustomerVoiceDelivery:(id,state)=>{window.deliveryUpdate={id,state}}};
 const contexts=[];Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{if(window.denyMicrophone)throw new DOMException('denied','NotAllowedError');const c=new AudioContext(),o=c.createOscillator(),d=c.createMediaStreamDestination();o.connect(d);o.start();contexts.push([c,o]);return d.stream;}});</script>
 ${scripts.map(s=>`<script src="/assets/${s}"></script>`).join('')}
 <script>document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'))</script>`;

async function main(){
  const server=http.createServer((req,res)=>{
    const name=(req.url||'').split('?')[0].replace(/^\/assets\//,'');
    if(scripts.includes(name)){res.setHeader('content-type','application/javascript');return res.end(fs.readFileSync(path.join(docs,name)));}
    res.setHeader('content-type','text/html; charset=utf-8');res.end(html);
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin=`http://127.0.0.1:${server.address().port}`;
  const engine=process.env.VOICE_BROWSER_ENGINE||'chromium',browserType={chromium,firefox,webkit}[engine];
  if(!browserType)throw Error('Unsupported test browser');
  const executablePath=engine==='chromium'?(process.env.CHROMIUM_EXECUTABLE_PATH || (fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':undefined)):undefined;
  let browser;
  try{browser=await browserType.launch({headless:true,timeout:15000,...(executablePath?{executablePath}:{}),...(engine==='chromium'?{args:['--no-sandbox']}:{})});}
  catch(error){await new Promise(resolve=>server.close(resolve));throw error;}
  let posts=0,checks=0,capabilityCalls=0,accepted=false,enabled=true,capabilityDenied=false,capabilityStalled=false,holdSend=false,holdReceipt=false,releaseSend,releaseReceipt;
  const context=await browser.newContext();
  await context.route('**/api/platform/crm/chat/**',async route=>{
    const url=new URL(route.request().url());
    if(url.pathname.endsWith('/voice-capability')){
      capabilityCalls++;
      if(capabilityStalled){await new Promise(resolve=>setTimeout(resolve,10000));try{await route.abort('failed');}catch{}return;}
      return capabilityDenied?route.fulfill({status:403,json:{success:false}}):route.fulfill({json:{success:true,enabled,recovery_enabled:true}});
    }
    if(url.pathname.endsWith('/voice-status'))return route.fulfill({json:{success:true,delivery_state:'read'}});
    if(url.pathname.endsWith('/send-voice')){posts++;if(holdSend){await new Promise(resolve=>{releaseSend=resolve;});return route.fulfill({status:401,json:{success:false,keep_request:false}});}return route.abort('connectionfailed');}
    if(url.pathname.endsWith('/voice-receipt')){checks++;if(holdReceipt){await new Promise(resolve=>{releaseReceipt=resolve;});return route.fulfill({status:409,json:{success:false,error_code:'voice_identity_changed'}});}return route.fulfill({json:accepted?
      {success:true,state:'accepted',provider_accepted:true,delivery_state:'sent',delivered:false}:
      {success:true,state:'unknown',provider_accepted:false,retry_safe:false}});}
    throw Error('Unexpected request '+url.pathname);
  });
  try{
    const page=await context.newPage();await page.setViewportSize({width:390,height:844});await page.goto(origin);await page.locator('.operator-voice-mic').waitFor({state:'visible'});
    await page.waitForFunction(()=>window.deliveryUpdate?.state==='read');
    await page.locator('.operator-voice-mic').click();await page.getByRole('button',{name:'Остановить'}).waitFor({state:'visible'});
    const other=await context.newPage();await other.goto(origin+'/?lead=8');await other.locator('.operator-voice-mic').click();
    await other.getByText('Микрофон уже используется в другой вкладке или голосовом режиме.').waitFor({state:'visible'});
    await page.getByRole('button',{name:'Отменить'}).click();
    await other.locator('.operator-voice-mic').click();await other.getByRole('button',{name:'Остановить'}).waitFor({state:'visible'});
    await other.getByRole('button',{name:'Отменить'}).click();await other.close();
    assert.equal(posts,0,'cancellation never sends');

    await page.locator('.operator-voice-mic').click();await page.getByRole('button',{name:'Остановить'}).waitFor({state:'visible'});
    await page.evaluate(()=>{document.dispatchEvent(new CustomEvent('tenant-chat-render'));document.querySelector('.operator-composer').innerHTML='<textarea id="chatText"></textarea><div class="operator-composer-actions"><button id="sendChat">send</button></div>';document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));});
    await page.getByRole('button',{name:'Остановить'}).waitFor({state:'visible'});
    await page.waitForTimeout(350);await page.getByRole('button',{name:'Остановить'}).click();
    await page.getByRole('button',{name:'Отправить голосовое'}).waitFor({state:'visible'});
    assert.equal(await page.locator('audio').isVisible(),true);assert.equal(posts,0,'preview never sends');
    capabilityDenied=true;await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
    await page.locator('.operator-customer-voice').waitFor({state:'detached'});
    assert.equal(await page.locator('audio').count(),0,'revoked scope hides the saved recording');
    capabilityDenied=false;await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready')));
    await page.getByRole('button',{name:'Отправить голосовое'}).waitFor({state:'visible'});

    await page.evaluate(()=>{document.dispatchEvent(new CustomEvent('tenant-chat-close'));window.fixtureRoute={...window.fixtureRoute,lead_id:9,conversation_id:'chat-9'};document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));});
    await page.locator('.operator-voice-mic').waitFor({state:'visible'});
    assert.equal(await page.locator('.operator-customer-voice button').filter({hasText:'Отправить голосовое'}).isVisible(),false,'other dialog cannot send old audio');
    await page.evaluate(()=>{document.dispatchEvent(new CustomEvent('tenant-chat-close'));window.fixtureRoute={...window.fixtureRoute,lead_id:7,conversation_id:'chat-7'};document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));});
    await page.getByRole('button',{name:'Отправить голосовое'}).waitFor({state:'visible'});

    await page.evaluate(()=>{const button=[...document.querySelectorAll('.operator-customer-voice button')].find(n=>n.textContent==='Отправить голосовое');button.click();button.click();});
    await page.getByRole('button',{name:'Проверить статус'}).waitFor({state:'visible'});
    await page.getByText('Результат неизвестен. Проверьте чат WhatsApp вручную и не отправляйте повторно.').waitFor({state:'visible'});
    assert.equal(posts,1,'one explicit send');
    await page.reload();await page.getByRole('button',{name:'Проверить статус'}).waitFor({state:'visible'});
    await Promise.all([page.waitForResponse(r=>r.url().includes('/voice-receipt?')),page.getByRole('button',{name:'Проверить статус'}).click()]);assert.equal(posts,1,'unknown check is read-only');assert.equal(checks,1);
    accepted=true;await Promise.all([page.waitForResponse(r=>r.url().includes('/voice-receipt?')),page.getByRole('button',{name:'Проверить статус'}).click()]);
    await page.getByText('Провайдер принял сообщение. Доставка клиенту пока не подтверждена.').waitFor({state:'visible'});
    assert.equal(posts,1);assert.equal(checks,2);

    await page.evaluate(()=>{window.denyMicrophone=true;});await page.locator('.operator-voice-mic').click();
    await page.getByText('Доступ к микрофону запрещён. Разрешите микрофон в настройках сайта браузера и попробуйте снова.').waitFor({state:'visible'});
    enabled=false;await page.locator('.operator-voice-mic').click();
    await page.getByText('Отправка голосовых временно отключена.').waitFor({state:'visible'});
    assert.equal(await page.locator('.operator-voice-mic').isVisible(),false,'existing tab refreshes capability before recording');
    enabled=true;await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await page.locator('.operator-voice-mic').waitFor({state:'visible'});
    const live=await context.newPage();await live.goto(origin+'/?lead=13');await live.locator('.operator-voice-mic').click();
    await live.getByRole('button',{name:'Остановить'}).waitFor({state:'visible'});capabilityDenied=true;
    await live.locator('.operator-customer-voice').waitFor({state:'detached',timeout:9000});
    assert.equal(await live.evaluate(async()=>!!await createTenantVoiceStore().get(JSON.stringify(['tenant','20','13','channel','chat-13']))),false,'revoked capture is discarded');
    capabilityDenied=false;await live.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready')));
    await live.locator('.operator-voice-mic').click();await live.getByRole('button',{name:'Остановить'}).waitFor({state:'visible'});enabled=false;
    await live.getByRole('button',{name:'Остановить'}).waitFor({state:'hidden',timeout:9000});
    assert.equal(await live.getByRole('button',{name:'Отправить голосовое'}).isVisible(),false,'active recording stops and cannot be sent after kill switch');
    enabled=true;await live.close();
    const offline=await context.newPage();await offline.goto(origin+'/?lead=14');await offline.locator('.operator-voice-mic').click();
    await offline.getByRole('button',{name:'Остановить'}).waitFor({state:'visible'});capabilityStalled=true;
    await offline.getByRole('button',{name:'Остановить'}).waitFor({state:'hidden',timeout:16000});
    assert.equal(await offline.getByRole('button',{name:'Отправить голосовое'}).isVisible(),false,'stalled permission refresh stops capture');
    const afterTimeout=capabilityCalls;await offline.waitForTimeout(1500);assert.equal(capabilityCalls,afterTimeout,'offline checks are throttled after timeout');
    capabilityStalled=false;await offline.close();
    const race=await context.newPage();await race.goto(origin+'/?lead=11');
    const create=async p=>p.evaluate(async()=>createTenantVoiceStore().createDraft({key:'synthetic-storage-race',request_id:crypto.randomUUID(),state:'draft',blob:new Blob(['synthetic'])}));
    assert.equal((await Promise.all([create(page),create(race)])).filter(Boolean).length,1,'concurrent drafts cannot overwrite each other');
    const id=await page.evaluate(async()=>(await createTenantVoiceStore().get('synthetic-storage-race')).request_id);
    const claim=async p=>p.evaluate(async id=>createTenantVoiceStore().claim('synthetic-storage-race',id),id);
    assert.equal((await Promise.all([claim(page),claim(race)])).filter(Boolean).length,1,'only one tab claims a draft');
    const safeRetry=await page.evaluate(async id=>{const s=createTenantVoiceStore();await s.releaseDraft('synthetic-storage-race',id,'new-safe-id');return s.get('synthetic-storage-race');},id);
    assert.equal(safeRetry.request_id,'new-safe-id');assert.equal(safeRetry.state,'draft');assert.ok(safeRetry.blob);
    assert.equal(await page.evaluate(()=>createTenantVoiceStore().claim('synthetic-storage-race','new-safe-id')),true);
    const activeId='new-safe-id';
    await page.evaluate(async id=>{const s=createTenantVoiceStore();await s.markUnknown('synthetic-storage-race',id);if(await s.removeDraft('synthetic-storage-race',id))throw Error('unknown removed as draft');await s.removeAccepted('synthetic-storage-race',id);},activeId);
    await race.locator('.operator-voice-mic').click();await race.getByRole('button',{name:'Остановить'}).waitFor({state:'visible'});
    const logout=await page.evaluate(async()=>{
      const s=createTenantVoiceStore(),a=JSON.stringify(['tenant','20','101','channel','a']),b=JSON.stringify(['tenant','20','102','channel','b']);
      const generation=await s.epoch('tenant','20');
      await s.createDraft({key:a,request_id:'draft-id',state:'draft',blob:new Blob(['private'])});
      await s.createDraft({key:b,request_id:'pending-id',state:'draft',blob:new Blob(['private'])});await s.claim(b,'pending-id');
      await window.tenantCustomerVoiceForget();const late=await s.createScopedDraft({key:a,request_id:'late',state:'draft',blob:new Blob(['private'])},'tenant','20',generation);
      let startBlocked=false;try{await s.activeEpoch('tenant','20');}catch{startBlocked=true;}
      const later=await s.createScopedDraft({key:a,request_id:'later',state:'draft',blob:new Blob(['private'])},'tenant','20',await s.epoch('tenant','20'));
      return {draft:await s.get(a),pending:await s.get(b),late,later,startBlocked};
    });
    assert.equal(logout.draft,undefined);assert.equal(logout.pending.state,'unknown');assert.equal(logout.pending.blob,null);assert.equal(logout.late,false);assert.equal(logout.later,false);assert.equal(logout.startBlocked,true);
    await race.waitForTimeout(200);assert.equal(await race.evaluate(async()=>!!await createTenantVoiceStore().get(JSON.stringify(['tenant','20','11','channel','chat-11']))),false,'logout cannot resurrect another tab recording');
    const switched=await page.evaluate(async()=>{const s=createTenantVoiceStore();await s.activate('tenant','20',await s.ownerVersion());const before=await s.epoch('tenant','20'),oldVersion=await s.ownerVersion();await s.activate('tenant','21',oldVersion);const after=await s.epoch('tenant','20');let startBlocked=false;try{await s.activeEpoch('tenant','20');}catch{startBlocked=true;}return {before,after,startBlocked,stale:await s.activate('tenant','20',oldVersion),late:await s.createScopedDraft({key:'switch-test',request_id:'id',state:'draft',blob:new Blob(['private'])},'tenant','20',before),later:await s.createScopedDraft({key:'switch-test',request_id:'id',state:'draft',blob:new Blob(['private'])},'tenant','20',after)};});
    assert.equal(switched.after,switched.before+1);assert.equal(switched.late,false);assert.equal(switched.later,false);assert.equal(switched.stale,false);assert.equal(switched.startBlocked,true,'account switch invalidates old capability responses and writes');
    enabled=false;const disabled=await context.newPage();await disabled.goto(origin+'/?lead=10');await disabled.waitForTimeout(250);
    assert.equal(await disabled.locator('.operator-voice-mic').count(),0,'disabled capability is not shown');
    await page.evaluate(async()=>{const s=createTenantVoiceStore(),key=JSON.stringify(['tenant','20','12','channel','chat-12']);
      await s.createDraft({key,request_id:crypto.randomUUID(),state:'draft',blob:new Blob(['private'])});const entry=await s.get(key);await s.claim(key,entry.request_id);await s.markUnknown(key,entry.request_id);});
    const recovery=await context.newPage();await recovery.goto(origin+'/?lead=12');
    await recovery.getByRole('button',{name:'Проверить статус'}).waitFor({state:'visible'});
    assert.equal(await recovery.locator('.operator-voice-mic').isVisible(),false,'disabled send stays hidden during receipt recovery');
    const beforeRecovery=posts;await Promise.all([recovery.waitForResponse(r=>r.url().includes('/voice-receipt?')),recovery.getByRole('button',{name:'Проверить статус'}).click()]);
    assert.equal(posts,beforeRecovery,'disabled recovery never sends');
    enabled=true;
    await page.evaluate(async()=>{const s=createTenantVoiceStore();for(const lead of [15,16,17,18]){const key=JSON.stringify(['tenant','20',String(lead),'channel','chat-'+lead]);
      await s.createDraft({key,request_id:crypto.randomUUID(),state:'draft',blob:new Blob(['private'],{type:'audio/ogg'})});
      if(lead===17){const item=await s.get(key);await s.claim(key,item.request_id);await s.markUnknown(key,item.request_id);}}});
    const stale=await context.newPage();await stale.goto(origin+'/?lead=15');await stale.getByRole('button',{name:'Отправить голосовое'}).waitFor({state:'visible'});
    holdSend=true;await stale.getByRole('button',{name:'Отправить голосовое'}).click();
    for(let i=0;i<100&&!releaseSend;i++)await stale.waitForTimeout(20);assert.ok(releaseSend,'old send is held');
    const switchLead=async lead=>stale.evaluate(lead=>{document.dispatchEvent(new CustomEvent('tenant-chat-close'));window.fixtureRoute={...window.fixtureRoute,lead_id:lead,conversation_id:'chat-'+lead};document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));},lead);
    await switchLead(16);await stale.getByRole('button',{name:'Отправить голосовое'}).waitFor({state:'visible'});
    releaseSend();holdSend=false;await stale.waitForTimeout(300);
    assert.equal(await stale.getByRole('button',{name:'Отправить голосовое'}).isVisible(),true,'stale send auth failure cannot close current dialog');
    assert.ok(await stale.evaluate(async()=>await createTenantVoiceStore().get(JSON.stringify(['tenant','20','16','channel','chat-16']))),'stale send cannot erase current draft');
    await switchLead(17);await stale.getByRole('button',{name:'Проверить статус'}).waitFor({state:'visible'});
    holdReceipt=true;await stale.getByRole('button',{name:'Проверить статус'}).click();
    for(let i=0;i<100&&!releaseReceipt;i++)await stale.waitForTimeout(20);assert.ok(releaseReceipt,'old receipt is held');
    await switchLead(18);await stale.getByRole('button',{name:'Отправить голосовое'}).waitFor({state:'visible'});
    releaseReceipt();holdReceipt=false;await stale.waitForTimeout(300);
    assert.equal(await stale.getByRole('button',{name:'Отправить голосовое'}).isVisible(),true,'stale receipt identity failure cannot close current dialog');
    assert.ok(await stale.evaluate(async()=>await createTenantVoiceStore().get(JSON.stringify(['tenant','20','18','channel','chat-18']))),'stale receipt cannot erase current draft');
    console.log('PASS '+engine+': cancellation, two-tab exclusion, preview, dialog isolation, unknown recovery, permission error, disabled feature');
  }finally{await context.close();await browser.close();await new Promise(resolve=>server.close(resolve));}
}
main().catch(error=>{console.error(error);process.exitCode=1});
