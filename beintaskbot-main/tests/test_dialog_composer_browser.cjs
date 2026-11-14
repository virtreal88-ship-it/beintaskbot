/* Unchanged application HTML and real assets; every API/provider request mocked. */
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..');
const tenant='00000000-0000-0000-0000-000000000101',channel='00000000-0000-0000-0000-000000000201';
const dialog={provider:'connect',lead_id:41,channel_id:channel,conversation_id:'994501234567@s.whatsapp.net',customer_kind:'contact',customer_name:'Customer',channel_name:'WhatsApp',display_state:'human',state:'human',ai_availability:'stopped',available_actions:[],can_send:true,controls_allowed:true,agent_status:'manual',agent_mode:'manual',agent_id:'00000000-0000-0000-0000-000000000301',agent_name:'Assigned Helper'};
const profile={success:true,member:{tenant_id:tenant,telegram_id:33,role:'worker',permissions:['dialogs'],active:true},capabilities:{modules:{dialogs:true}}};
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':chromium.executablePath()),args:['--no-sandbox']});
 try{
  for(const [width,height,language] of [[320,760,'ru'],[320,760,'az'],[360,800,'ru'],[360,800,'az'],[390,844,'ru'],[390,844,'az'],[430,900,'ru'],[430,900,'az'],[1280,900,'ru'],[1280,900,'az']]){
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
    if(name.endsWith('/dialogs/history'))result={dialog,messages:[{id:'voice-row',external_id:'voice-1',source:'connect',source_lead_id:41,source_channel_id:channel,source_conversation_id:dialog.conversation_id,direction:'incoming',message_type:'audio',body:'',media_version:'a'.repeat(64),duration_seconds:2,media_size_bytes:0,analysis_state:'failed',happened_at:'2026-10-21T10:00:00Z'},{id:'after-voice',external_id:'after',direction:'incoming',message_type:'text',body:'Message below the voice',happened_at:'2026-10-21T10:01:00Z'}],next_before:null};
    else if(name.endsWith('/dialogs'))result={dialogs:[{...dialog,last_message_id:'m1',last_message_direction:'incoming',last_message_text:'Customer question',last_message_at:'2026-10-21T10:00:00Z'}],channels:[{id:'connect:'+channel,name:'WhatsApp'}],agents:[],next_offset:null};
    else if(name.endsWith('/developer-marker'))result={enabled:false};
    else if(name.endsWith('/replies'))result={replies:[],consultations:[]};
    return route.fulfill({json:{success:true,result}});
   });
   try{
    await page.goto('https://crm.test/app?view=dialogs');await page.locator('[data-app-language]').selectOption(language);
    await page.locator('.operator-row').first().click();
    const primary=page.locator('#operatorComposerPrimary'),ai=page.locator('#operatorAIActionMenu'),input=page.locator('#chatText');
    await primary.waitFor();assert.equal(await page.locator('#sendChat').isVisible(),false);
    await input.fill('Draft');assert.equal(await primary.getAttribute('data-action'),'send');
    const rect=await primary.boundingBox();assert(rect.width>=44&&rect.height>=44);
    await ai.click();const menu=page.locator('#operatorAIActionChoices');assert.equal(await menu.isVisible(),true);assert.equal(await menu.locator('button').count(),3);
    const box=await menu.boundingBox();assert(box.x>=0&&box.x+box.width<=width+1);await page.keyboard.press('Escape');assert.equal(await menu.isVisible(),false);
    await ai.click();await menu.locator('#operatorCopilotToggle').click();assert.equal(await menu.isVisible(),false);assert.equal(await page.locator('#operatorCopilot').isVisible(),true);await page.locator('#operatorCopilotClose').click();
    assert.equal(await input.inputValue(),'Draft');await input.fill('');assert.equal(await primary.getAttribute('data-action'),'voice');
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    assert.deepEqual(errors,[]);assert.equal(calls.some(call=>/chat\/send/.test(call.path)&&call.method==='POST'),false);
    console.log('PASS compact composer '+width+' '+language);
   }finally{await page.close();}
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
