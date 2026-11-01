/* Unchanged application HTML and real assets; every API/provider request mocked. */
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..');
const tenant='00000000-0000-0000-0000-000000000101',channel='00000000-0000-0000-0000-000000000201';
const dialog={provider:'connect',lead_id:41,channel_id:channel,conversation_id:'994501234567@s.whatsapp.net',customer_kind:'contact',customer_name:'Очень длинное имя клиента / Uzun müştəri adı',channel_name:'WhatsApp',display_state:'human',state:'human',ai_availability:'stopped',available_actions:[],can_send:true,controls_allowed:true,agent_status:'manual',agent_mode:'manual',agent_id:'00000000-0000-0000-0000-000000000301',agent_name:'Назначенный AI-агент с длинным именем / Uzun agent adı',assignee_name:'Очень длинное имя сотрудника / Uzun əməkdaş adı'};
const profile={success:true,member:{tenant_id:tenant,telegram_id:33,role:'worker',permissions:['dialogs'],active:true},capabilities:{modules:{dialogs:true}}};
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':chromium.executablePath()),args:['--no-sandbox']});
 try{
  for(const [width,height,language] of [[320,800,'ru'],[320,800,'az'],[360,800,'ru'],[360,800,'az'],[390,844,'ru'],[390,844,'az'],[430,900,'ru'],[430,900,'az'],[390,844,'en'],[1280,900,'ru']]){
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
    if(name==='/api/platform/notifications')result={unread_count:2,notifications:[],next_before:null};
    else if(name.endsWith('/dialogs/history'))result={dialog,messages:[{id:'m1',external_id:'e1',direction:'incoming',message_type:'text',body:'Customer question',happened_at:'2026-10-21T10:00:00Z'}],next_before:null};
    else if(name.endsWith('/dialogs'))result={dialogs:[{...dialog,last_message_id:'m1',last_message_direction:'incoming',last_message_text:'Customer question',last_message_at:'2026-10-21T10:00:00Z'}],channels:[{id:'connect:'+channel,name:'WhatsApp'}],agents:[],next_offset:null};
    else if(name.endsWith('/developer-marker'))result={enabled:false};
    else if(name.endsWith('/replies'))result={replies:[],consultations:[]};
    return route.fulfill({json:{success:true,result}});
   });
   try{
    await page.goto('https://crm.test/app?view=dialogs');await page.locator('.app-topbar').waitFor();await page.locator('[data-app-language]').selectOption(language);
    const read=()=>page.evaluate(()=>{const r=selector=>{const n=document.querySelector(selector),box=n.getBoundingClientRect();return {x:box.x,y:box.y,w:box.width,h:box.height,position:getComputedStyle(n).position};};return {search:r('#operatorSearch'),trigger:r('#operatorToolsMenu>summary'),details:r('#operatorToolsMenu'),filter:r('#operatorFiltersToggle'),toolbar:r('.operator-filters'),firstAction:r('#operatorSoundToggle')};});
    await page.evaluate(()=>document.fonts.ready);await page.waitForFunction(()=>document.querySelector('#operatorSearch')?.offsetWidth>0);await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const before=await read(),trigger=page.locator('#operatorToolsMenu>summary'),popover=page.locator('#operatorToolsPopover');
    assert.equal(await page.locator('#operatorSearch').getAttribute('placeholder'),{ru:'Поиск',az:'Axtarış',en:'Search'}[language]);
    await trigger.click();await popover.waitFor();const after=await read();
    for(const name of ['search','trigger','details','filter','toolbar'])assert.deepEqual(after[name],before[name],name+' stays fixed on menu open');
    assert.equal(after.trigger.h,44);if(width<=900)assert.equal(after.filter.h,44);
    const checkBounds=async()=>{const box=await popover.boundingBox();assert.ok(box.x>=7&&box.y>=7&&box.x+box.width<=width-7&&box.y+box.height<=height-7,JSON.stringify(box));};
    await checkBounds();assert.equal(await trigger.getAttribute('aria-expanded'),'true');
    await page.locator('#operatorSoundToggle').focus();await page.keyboard.press('End');assert.equal(await page.evaluate(()=>document.activeElement.id),'operatorMarkAllRead');
    await page.keyboard.press('Home');assert.equal(await page.evaluate(()=>document.activeElement.id),'operatorSoundToggle');
    const nextId=await popover.evaluate(n=>[...n.querySelectorAll('button')].filter(b=>!b.disabled&&b.getClientRects().length)[1].id);await page.keyboard.press('ArrowDown');assert.equal(await page.evaluate(()=>document.activeElement.id),nextId);
    await page.keyboard.press('Escape');assert.equal(await popover.isVisible(),false);assert.equal(await trigger.getAttribute('aria-expanded'),'false');assert.equal(await trigger.evaluate(n=>n===document.activeElement),true);
    await page.keyboard.press('ArrowDown');await popover.waitFor();assert.equal(await page.evaluate(()=>document.activeElement.id),'operatorSoundToggle');
    await page.evaluate(()=>{document.querySelector('#operatorSoundStatus').textContent='Очень длинный статус / Uzun bildiriş mətni '.repeat(20);});await page.waitForTimeout(40);await checkBounds();
    await page.locator('#operatorSearch').click();assert.equal(await popover.isVisible(),false);assert.deepEqual((await read()).toolbar,before.toolbar);
    if(width<=900){await page.locator('#operatorFiltersToggle').click();assert.equal(await page.locator('#operatorFiltersToggle').getAttribute('aria-expanded'),'true');await page.locator('#operatorFiltersToggle').click();}
    await trigger.click();await popover.waitFor();await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-view',{detail:{view:'dialogs'}})));assert.equal(await popover.isVisible(),false);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    assert.equal(calls.some(c=>c.method!=='GET'),false,'menu/keyboard/layout never performs a mutation');assert.deepEqual(errors,[]);
    await page.screenshot({path:'/tmp/list-toolbar-'+width+'-'+language+'.png'});
    console.log('PASS list toolbar '+width+' '+language+': full HTML/CSS, fixed geometry, short search, viewport bounds, keyboard/focus/outside, no mutations');
   }finally{await page.close();}
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
