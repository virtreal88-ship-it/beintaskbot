const {revealAccountControl,selectAppLanguage}=require('./account_controls_browser.cjs');
/* Unchanged application HTML and real assets; every API/provider request mocked. */
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'../.support-test-deps/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..');
const tenant='00000000-0000-0000-0000-000000000101',channel='00000000-0000-0000-0000-000000000201';
const dialog={provider:'connect',lead_id:41,channel_id:channel,conversation_id:'994501234567@s.whatsapp.net',customer_kind:'contact',customer_name:'Customer',channel_name:'WhatsApp',display_state:'human',state:'human',ai_availability:'stopped',available_actions:[],can_send:true,controls_allowed:true,agent_status:'manual',agent_mode:'manual',agent_id:'00000000-0000-0000-0000-000000000301',agent_name:'Assigned Helper'};
const profile={success:true,member:{tenant_id:tenant,telegram_id:33,role:'worker',permissions:['dialogs'],active:true},capabilities:{modules:{dialogs:true}}};
const extraDialogs=Array.from({length:20},(_,i)=>({...dialog,lead_id:42+i,conversation_id:`synthetic-${i}`,customer_name:`Customer ${i+2}`}));
const extraMessages=Array.from({length:24},(_,i)=>({id:`synthetic-message-${i}`,direction:'incoming',message_type:'text',body:`Earlier message ${i+1}`,happened_at:new Date(Date.UTC(2026,9,21,9,i)).toISOString()}));

async function checkWorkspace(page,{desktop,open,navigationVisible}){
 const geometry=await page.evaluate(({open,navigationVisible})=>{
  const view=document.querySelector('#view-dialogs').getBoundingClientRect();
  const header=document.querySelector('.app-topbar').getBoundingClientRect();
  const nav=document.querySelector('.shell>aside').getBoundingClientRect();
  const scroller=document.querySelector(open?'#chatRows':matchMedia('(max-width:900px)').matches?'#operatorListScroll':'#operatorRows');
  const control=document.querySelector(open?'#operatorComposerPrimary':'#operatorMore').getBoundingClientRect();
  const paneScroll=scroller.scrollHeight-scroller.clientHeight;
  const originalScroll=scroller.scrollTop;
  scroller.scrollTop=originalScroll>40?originalScroll-40:originalScroll+40;
  const scrolled=scroller.scrollTop!==originalScroll;
  scroller.scrollTop=originalScroll;
  return {
   overflow:document.scrollingElement.scrollHeight-innerHeight,
   topGap:view.top-header.bottom,
   paneScroll,scrolled,
   controlClearance:(navigationVisible?nav.top:innerHeight)-control.bottom
  };
 },{open,navigationVisible});
 const layout=JSON.stringify(geometry);
 assert.ok(geometry.overflow<=1,`dialogues fit in the viewport: ${layout}`);
 if(desktop)assert.ok(geometry.topGap>=0&&geometry.topGap<=12,`workspace sits just below the desktop header: ${layout}`);
 assert.ok(geometry.paneScroll>0&&geometry.scrolled,`the ${open?'history':'list'} scrolls independently: ${layout}`);
 assert.ok(geometry.controlClearance>=-1,`the ${open?'composer':'pagination'} stays within the viewport: ${layout}`);
}

(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':chromium.executablePath()),args:['--no-sandbox']});
 try{
  for(const [width,height,language] of [[1280,900,'ru'],[1024,600,'ru'],[1023,900,'ru'],[320,760,'ru'],[390,844,'az'],[430,900,'ru']]){
   const page=await browser.newPage({viewport:{width,height}}),errors=[],calls=[];let mediaRequests=0,mediaStatus=503,mediaGate=null,releaseMedia=null;
   // Hold visibility delivery so this suite exercises the explicit menu fallback.
   await page.addInitScript(()=>{window.IntersectionObserver=class{observe(){}unobserve(){}disconnect(){}};});
   page.on('pageerror',error=>errors.push(error.message));page.on('dialog',()=>assert.fail('Unexpected native dialog'));
   // Production stores question templates in this tenant/employee scoped key.
   await page.addInitScript(({tenant})=>localStorage.setItem(`tenant.operator.copilot-question-templates.${tenant}:33`,JSON.stringify(Array.from({length:12},(_,i)=>({id:'template-'+i,name:'Saved question '+i,prompt:'Saved question'})))),{tenant});
   await page.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url()),name=url.pathname;calls.push({path:name,method:request.method(),body:request.postData()});
    if(name==='/app')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8')});
    if(name.startsWith('/assets/')){const file=path.join(root,'docs',path.basename(name));return fs.existsSync(file)?route.fulfill({path:file}):route.fulfill({status:404,body:''});}
    if(name.endsWith('/crm/chat/attachment')){mediaRequests++;if(mediaGate)await mediaGate;if(mediaStatus!==200)return route.fulfill({status:mediaStatus,headers:{'Retry-After':'60'},json:{error:'Synthetic temporary file failure'}});const bytes=Buffer.alloc(44+32000);bytes.write('RIFF');bytes.writeUInt32LE(bytes.length-8,4);bytes.write('WAVEfmt ',8);bytes.writeUInt32LE(16,16);bytes.writeUInt16LE(1,20);bytes.writeUInt16LE(1,22);bytes.writeUInt32LE(8000,24);bytes.writeUInt32LE(16000,28);bytes.writeUInt16LE(2,32);bytes.writeUInt16LE(16,34);bytes.write('data',36);bytes.writeUInt32LE(32000,40);return route.fulfill({contentType:'audio/wav',body:bytes});}
    if(name==='/api/platform/me')return route.fulfill({json:profile});
    let result={};
    if(name.endsWith('/dialogs/history'))result={dialog,messages:[...extraMessages,{id:'voice-row',external_id:'voice-1',source:'connect',source_lead_id:41,source_channel_id:channel,source_conversation_id:dialog.conversation_id,direction:'incoming',message_type:'audio',body:'',media_version:'a'.repeat(64),duration_seconds:2,media_size_bytes:0,analysis_state:'failed',happened_at:'2026-10-21T10:00:00Z'},{id:'after-voice',external_id:'after',direction:'incoming',message_type:'text',body:'Message below the voice',happened_at:'2026-10-21T10:01:00Z'}],next_before:null};
    else if(name.endsWith('/dialogs'))result={dialogs:[{...dialog,last_message_id:'m1',last_message_direction:'incoming',last_message_text:'Customer question',last_message_at:'2026-10-21T10:00:00Z'},...extraDialogs],channels:[{id:'connect:'+channel,name:'WhatsApp'}],agents:[],next_offset:21};
    else if(name.endsWith('/developer-marker'))result={enabled:false};
    else if(name.endsWith('/replies'))result={replies:[],consultations:[]};
    return route.fulfill({json:{success:true,result}});
   });
   try{
    await page.goto('https://crm.test/app?view=dialogs');await selectAppLanguage(page,language);
    const nav=page.locator('.shell>aside');await page.locator('.operator-row').first().waitFor();
    assert.equal(await nav.isVisible(),true,'list retains navigation');
    const desktop=width>=1024;
    if(width<=900)await page.locator('#operatorMore').scrollIntoViewIfNeeded();
    await checkWorkspace(page,{desktop,open:false,navigationVisible:!desktop});
    for(let cycle=0;cycle<2;cycle++){
      await page.locator('.operator-row').first().click();await page.locator('#operatorComposerPrimary').waitFor();
      await page.waitForFunction(()=>document.getElementById('operatorDialogs').dataset.conversationOpen==='true');
      if(width<=1023){
        await page.waitForFunction(()=>document.querySelector('.shell>aside').dataset.bottomNavSuppressed==='true');
        assert.equal(await nav.isVisible(),false);assert.equal(await nav.evaluate(el=>el.inert),true);
        assert.equal(await page.evaluate(()=>document.documentElement.style.getPropertyValue('--app-bottom-nav-height')),'0px');
        await checkWorkspace(page,{desktop,open:true,navigationVisible:false});
        const input=page.locator('#chatText');await input.fill('unsent draft');await page.setViewportSize({width,height:450});await input.blur();await page.waitForTimeout(80);
        assert.equal(await nav.isVisible(),false,'keyboard dismissal does not expose nav in chat');
        if(width<=900){const b=await page.locator('#operatorComposerPrimary').boundingBox();assert(b.y+b.height<=451,'composer in reduced visual viewport');}
        await page.setViewportSize({width,height});
      }else{
        assert.equal(await nav.isVisible(),true);assert.equal(await nav.evaluate(el=>el.inert),false);
        await checkWorkspace(page,{desktop,open:true,navigationVisible:false});
      }
      if(cycle===0)await page.evaluate(()=>window.tenantOperator.closeDialog());
      else if(width<=900)await page.goBack();else await page.evaluate(()=>window.tenantOperator.closeDialog());
      await page.waitForFunction(()=>document.getElementById('operatorDialogs').dataset.conversationOpen!=='true');
      await page.waitForTimeout(80);assert.equal(await nav.isVisible(),true,'back restores navigation');
    }
    assert.deepEqual(errors,[]);assert(!calls.some(c=>c.method==='POST'&&/send|dispatch/.test(c.path)),'no outgoing messages');
    console.log('PASS real conversation navigation',width,language);
   }finally{await page.close();}
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
