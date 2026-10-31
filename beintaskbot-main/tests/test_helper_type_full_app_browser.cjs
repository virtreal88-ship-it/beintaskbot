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
  for(const [width,height] of [[390,844],[1280,900],[390,500]]){
   const page=await browser.newPage({viewport:{width,height}}),errors=[],calls=[];
   page.on('pageerror',error=>errors.push(error.message));page.on('dialog',()=>assert.fail('Unexpected native dialog'));
   // Production stores question templates in this tenant/employee scoped key.
   await page.addInitScript(({tenant})=>localStorage.setItem(`tenant.operator.copilot-question-templates.${tenant}:33`,JSON.stringify(Array.from({length:12},(_,i)=>({id:'template-'+i,name:'Saved question '+i,prompt:'Saved question'})))),{tenant});
   await page.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url()),name=url.pathname;calls.push({path:name,method:request.method(),body:request.postData()});
    if(name==='/app')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8')});
    if(name.startsWith('/assets/')){const file=path.join(root,'docs',path.basename(name));return fs.existsSync(file)?route.fulfill({path:file}):route.fulfill({status:404,body:''});}
    if(name==='/api/platform/me')return route.fulfill({json:profile});
    let result={};
    if(name.endsWith('/dialogs/history'))result={dialog,messages:[{id:'m1',external_id:'e1',direction:'incoming',message_type:'text',body:'Customer question',happened_at:'2026-10-21T10:00:00Z'}],next_before:null};
    else if(name.endsWith('/dialogs'))result={dialogs:[{...dialog,last_message_id:'m1',last_message_direction:'incoming',last_message_text:'Customer question',last_message_at:'2026-10-21T10:00:00Z'}],channels:[{id:'connect:'+channel,name:'WhatsApp'}],agents:[],next_offset:null};
    else if(name.endsWith('/developer-marker'))result={enabled:false};
    else if(name.endsWith('/replies'))result={replies:[],consultations:[]};
    return route.fulfill({json:{success:true,result}});
   });
   try{
    await page.goto('https://crm.test/app?view=dialogs');
    await page.locator('.operator-row').first().click();
    await page.locator('#dialogHelperType').waitFor({state:'attached'});
    assert.equal(await page.locator('#operatorCopilot').isVisible(),false,'type lives inside the initially closed assistant panel');
    assert.ok(calls.some(call=>call.path==='/assets/tenant-operator-dialogs.js'));
    assert.ok(calls.some(call=>call.path==='/assets/tenant-dialog-consultations.js'));
    await page.locator('#operatorCopilotToggle').click();
    assert.equal(await page.locator('#dialogHelperType').inputValue(),'Helper');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'dialogHelperType','opening starts at the assistant type, avoiding prompt autofocus scrolling');
    await page.locator('[data-copilot-mode=instruction]').click();
    assert.equal(await page.locator('[data-template-use]').count(),12);
    await page.keyboard.press('Escape');
    await page.locator('#operatorCopilotToggle').click();
    const assertVisibleControls=async stage=>{
     const geometry=await page.evaluate(()=>{
      const panel=document.getElementById('operatorCopilot').getBoundingClientRect(),type=document.getElementById('dialogHelperType').getBoundingClientRect(),close=document.getElementById('operatorCopilotClose').getBoundingClientRect();
      const hit=document.elementFromPoint(type.left+type.width/2,type.top+type.height/2);
      return {panel:{top:panel.top,bottom:panel.bottom},type:{top:type.top,bottom:type.bottom},close:{top:close.top,bottom:close.bottom},clickable:hit===document.getElementById('dialogHelperType')};
     });
     assert.ok(geometry.type.top>=geometry.panel.top&&geometry.type.bottom<=geometry.panel.bottom,`${stage}: type clipped at ${width}x${height}: ${JSON.stringify(geometry)}`);
     assert.equal(geometry.clickable,true,stage+': type remains clickable');
     if(width<=900)assert.ok(geometry.close.top>=geometry.panel.top&&geometry.close.bottom<=geometry.type.top,'sticky header keeps close button visible above type');
    };
    await assertVisibleControls('reopened with long templates');
    await page.locator('#operatorCopilotPrompt').focus(); // Keyboard focus must not scroll the selector out of sight.
    await assertVisibleControls('prompt focused');
    await page.locator('#operatorCopilot').evaluate(panel=>panel.scrollTop=panel.scrollHeight);
    await assertVisibleControls('scrolled to end');
    await page.locator('#dialogHelperType').selectOption('Developer');
    assert.equal(await page.locator('#dialogConsultationStart').isVisible(),true);
    assert.equal(await page.locator('#operatorCopilotPrompt').isVisible(),false);
    await page.locator('#dialogHelperType').selectOption('Helper');
    assert.equal(await page.locator('#operatorCopilotPrompt').isVisible(),true);
    if(process.env.HELPER_VISIBILITY_EVIDENCE_DIR){fs.mkdirSync(process.env.HELPER_VISIBILITY_EVIDENCE_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.HELPER_VISIBILITY_EVIDENCE_DIR,`helper-type-${width}-${height}.png`)});}
    await page.keyboard.press('Escape');assert.equal(await page.locator('#operatorCopilot').isVisible(),false);
    assert.deepEqual(errors,[],'all real app scripts load without browser errors');
    assert.equal(calls.some(call=>/\/copilot$|\/dispatch$|chat\/send/.test(call.path)),false,'no AI generation or sends');
    assert.equal(calls.some(call=>call.path.endsWith('/developer-marker')&&call.body&&'enabled' in JSON.parse(call.body)),false,'no tag mutations');
   }catch(error){if(process.env.HELPER_VISIBILITY_EVIDENCE_DIR){fs.mkdirSync(process.env.HELPER_VISIBILITY_EVIDENCE_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.HELPER_VISIBILITY_EVIDENCE_DIR,`failure-${width}-${height}.png`)});}throw error;}finally{await page.close();}
  }
  console.log('Full app Helper type: real HTML/all assets, actual dialog clicks, desktop/mobile/short viewport, long templates, sticky close/type, focus, scroll and no side effects passed');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
