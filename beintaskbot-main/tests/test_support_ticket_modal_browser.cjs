const assert=require('node:assert/strict');
const fs=require('node:fs');
const http=require('node:http');
const path=require('node:path');
const {chromium}=require(process.env.SUPPORT_CASES_PLAYWRIGHT||'../.support-test-deps/node_modules/playwright');
const root=path.join(__dirname,'..');
const ticketId='c0ffee00-0000-4000-8000-000000000001';
const ticket={id:ticketId,contact_name:'Synthetic overnight ticket',channel_id:'channel-a',channel_name:'Synthetic WhatsApp',conversation_id:'conversation-a',lead_id:41,description:'Synthetic issue description',summary:'Synthetic persisted summary',summary_state:'completed',suggestions:'Synthetic check already done',next_steps:'Synthetic next step',status:'waiting',assigned_to:null,assignee_name:'Support',source:'ai_handoff',reason:'Synthetic reason',created_at:'2026-10-31T08:00:00Z',updated_at:'2026-10-31T08:30:00Z',last_message_at:'2026-10-31T08:30:00Z',ai_state:'human',agent_id:'agent-a'};
const message={id:'d0ffee00-0000-4000-8000-000000000001',external_id:'connect:synthetic-photo',direction:'incoming',body:'Synthetic attachment metadata',message_type:'image',happened_at:'2026-10-31T08:25:00Z',media_filename:'synthetic.png',media_size_bytes:3};
let mediaRequests=0;
const server=http.createServer((req,res)=>{
  if(req.url.startsWith('/app')){res.writeHead(200,{'content-type':'text/html'});res.end('<!doctype html><html><head><style>:root{--card:#fff;--ink:#17212b;--line:#d8e0ea;--paper:#f4f7fb;--muted:#64748b;--brand:#0f766e;--app-warning-bg:#fffbeb;--app-warning-ink:#92400e}body{font:15px system-ui,sans-serif;color:var(--ink);background:#f3f5f7;margin:24px}button,input,select,textarea{font:inherit}button{border:1px solid var(--line);border-radius:8px;padding:8px 12px;background:var(--card);color:var(--ink)}#operatorSupport{max-width:1200px;margin:auto}</style></head><body><section id="view-support"><div id="operatorSupport"></div></section></body></html>');return;}if(req.url==='/favicon.ico'){res.writeHead(204);res.end();return;}
  const headers={'content-type':'application/json','cache-control':'no-store'};
  if(req.url.startsWith('/api/platform/ai/support-tickets?')){res.writeHead(200,headers);res.end(JSON.stringify({success:true,result:{tickets:[ticket],members:[{telegram_id:11,display_name:'Synthetic Support'}],channels:[],next_offset:null,unresolved_count:1}}));return;}
  if(req.url==='/api/platform/ai/support-tickets/'+ticketId){res.writeHead(200,headers);res.end(JSON.stringify({success:true,result:{ticket,messages:[message],attached_media:[],expert_results:[],next_before:null}}));return;}
  if(req.url.startsWith('/api/platform/ai/internal-consultations')){res.writeHead(200,headers);res.end(JSON.stringify({success:true,result:{consultations:[],groups:[]}}));return;}
  if(req.url.startsWith('/api/platform/support-cases/'+ticketId+'/public-shares')){res.writeHead(200,headers);res.end(JSON.stringify({success:true,result:[]}));return;}
  if(req.url.startsWith('/api/platform/crm/chat/attachment?')){mediaRequests++;res.writeHead(200,{'content-type':'image/png','cache-control':'no-store','content-disposition':'inline; filename="synthetic.png"'});res.end(Buffer.from([1,2,3]));return;}
  res.writeHead(404,headers);res.end(JSON.stringify({success:false,error:'synthetic route not found'}));
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;
  try{
    browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
    const base='http://127.0.0.1:'+server.address().port;
    async function openPage(width,height,url='/app?view=support'){
      const context=await browser.newContext({viewport:{width,height}}),page=await context.newPage();
      await page.addInitScript(({ticketId})=>{
        window.tenantProfileSnapshot={member:{tenant_id:'synthetic-company',telegram_id:11,role:'owner'}};
        window.tenantOperator={hasRight:permission=>permission==='support'||permission==='dialogs'};
      },{ticketId});
      page.on('console',message=>console.log('browser console:',message.text()));page.on('pageerror',error=>console.log('browser pageerror:',error.message));page.on('requestfailed',request=>console.log('browser requestfailed:',request.url(),request.failure()?.errorText));
      await page.goto(base+url);
      await page.addScriptTag({path:path.join(root,'docs/tenant-operator-i18n.js')});
      // All calls use this local synthetic fixture server.
      await page.addScriptTag({path:path.join(root,'docs/tenant-support-inbox.js')});
      try{await page.waitForSelector('[data-support-id]',{timeout:5000});}catch(error){console.log('page html:',(await page.locator('body').innerText()).slice(0,1000));throw error;}
      return {context,page};
    }
    // Use only the local synthetic fixture server.
    const desktop=await openPage(1280,900);
    await desktop.page.locator('[data-support-id]').click();
    await desktop.page.waitForSelector('#supportTicketTitle');
    assert.equal(await desktop.page.locator('#supportDetail').getAttribute('aria-modal'),'true');
    assert.equal(new URL(desktop.page.url()).searchParams.get('support_ticket_id'),ticketId);
    assert.equal(await desktop.page.locator('#supportRows').count(),1,'filtered queue stays mounted behind the modal');
    const bounds=await desktop.page.locator('#supportDetail').boundingBox();assert.ok(bounds.width>1000&&bounds.height>850,'desktop modal is roomy and centered');await desktop.page.screenshot({path:'/tmp/support-ticket-modal-desktop.png'});await desktop.page.keyboard.press('Shift+Tab');assert.equal(await desktop.page.evaluate(()=>document.getElementById('supportDetail').contains(document.activeElement)),true,'focus stays inside the dialog');
    assert.equal(mediaRequests,0,'ticket open does not fetch attachment bytes');
    assert.equal(await desktop.page.locator('article.support-message img,article.support-message video,article.support-message audio').count(),0,'media src is absent before an explicit click');
    await desktop.page.locator('[data-ticket-media-open]').click();
    await desktop.page.waitForSelector('.support-media-preview img');assert.equal(mediaRequests,1);
    await desktop.page.keyboard.press('Escape');await desktop.page.locator('#supportDetail').waitFor({state:'hidden'});
    await desktop.page.waitForFunction(()=>!new URL(location.href).searchParams.has('support_ticket_id'));
    assert.equal(await desktop.page.locator('[data-support-id]').evaluate(node=>node===document.activeElement),true,'closing restores the invoking control');
    await desktop.page.locator('[data-support-id]').click();await desktop.page.waitForSelector('#supportTicketTitle');await desktop.page.locator('#supportDescription').fill('Unsaved synthetic draft');await desktop.page.keyboard.press('Escape');await desktop.page.locator('#supportDetail').waitFor({state:'hidden'});await desktop.page.locator('[data-support-id]').click();await desktop.page.waitForSelector('#supportTicketTitle');assert.equal(await desktop.page.locator('#supportDescription').inputValue(),'Unsaved synthetic draft','closing and reopening keeps the unsaved draft');
    await desktop.context.close();

    const mobile=await openPage(390,844,'/app?view=support&support_ticket_id='+ticketId);
    await mobile.page.waitForSelector('#supportTicketTitle');assert.equal(await mobile.page.locator('#supportDetail').getAttribute('aria-modal'),'true');
    const mobileBounds=await mobile.page.locator('#supportDetail').boundingBox();assert.equal(Math.round(mobileBounds.width),390);assert.equal(Math.round(mobileBounds.height),844);await mobile.page.screenshot({path:'/tmp/support-ticket-modal-mobile.png'});
    assert.equal(mediaRequests,1,'deep link open also avoids loading attachment bytes');
    await mobile.page.locator('#supportClose').click();await mobile.page.locator('#supportDetail').waitFor({state:'hidden'});
    await mobile.context.close();
    console.log('PASS: synthetic desktop/mobile ticket modal, deep link, history close, focus restore, and click-only attachment loading.');
  }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
