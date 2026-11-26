const {chromium}=require('../.support-test-deps/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
 try {
  for(const kind of ['native','kommo']){
   const page=await browser.newPage({viewport:{width:390,height:850},hasTouch:true});
   const errors=[];page.on('pageerror',error=>errors.push(error.stack||error.message));
   // Use the shipped shell: app-shell initializes .shell > aside > .nav.
   // Strip all scripts so only the bundle under test runs, with mocked reads below.
   const shell=fs.readFileSync('docs/tenant-app.html','utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
   // A real origin is required by the production bundle's browser storage APIs.
   // Fulfill only this fixture document; every other network request is blocked.
   await page.route('**/*',route=>route.request().url()==='https://fixture.test/app'?route.fulfill({contentType:'text/html',body:shell}):route.abort());
   await page.goto('https://fixture.test/app');
   await page.evaluate(kind=>{
    document.documentElement.lang='en';
    document.querySelectorAll('.view').forEach(node=>node.classList.toggle('active',node.id==='view-deals'));
    document.getElementById('nativeDealSourcePanel').hidden=kind!=='native';
    document.getElementById('kommoDealSourcePanel').hidden=kind!=='kommo';
    document.getElementById('aiDashboard')?.remove(); // unrelated dashboard not mounted in this test
    const outside=document.createElement('div');outside.id='outside';outside.textContent='Fixture drop cancellation area';
    outside.style.cssText='position:fixed;bottom:0;left:0;width:100%;height:60px;background:white;z-index:9999';
    document.body.append(outside);
    window.opened=[];window.edited=[];window.writes=[];window.drags=[];
    window.tenantViewDemand={watch(){}};
    document.addEventListener('dragstart',()=>window.drags.push('start'));
    document.addEventListener('dragend',()=>window.drags.push('end'));
    window.fetch=async(url,options={})=>{
     if(window.testStageDrop && options.method==='POST' && String(url).endsWith('/crm/deals/move-stage')){
       const payload=JSON.parse(options.body);window.writes.push(payload.stage_id);
       return {ok:true,json:async()=>({success:true,state:'confirmed',lead_id:'7',pipeline_id:10,
         status_id:Number(payload.stage_id),updated_at:1001,source_revision:'b'.repeat(64)})};
     }
     if(options.method && options.method!=='GET'){window.writes.push(url);throw Error('Unexpected write');}
     return {ok:true,json:async()=>({success:true,catalog:[{pipeline_id:10,name:'Fixture',stages:[{stage_id:100,name:'New'},{stage_id:101,name:'Working'},{stage_id:102,name:'Done'}]}],hidden_stage_keys:[],move_stage_targets:[{pipeline_id:10,stage_id:100},{pipeline_id:10,stage_id:101}],move_stage_sources:[{pipeline_id:10,stage_id:100}],move_context:{tenant_id:'fixture',user_id:'7'}})};
    };
   },kind);
   await page.addScriptTag({content:fs.readFileSync('docs/tenant-chakra-ui.js','utf8')});
   await page.waitForFunction(()=>typeof window.tenantChakraCRM?.renderNativeDeals==='function'&&typeof window.tenantChakraCRM?.renderDeals==='function').catch(error=>{throw new Error('CRM fixture boot failed: '+errors.join('; ')+'; '+error.message);});
   assert.deepEqual(errors,[],kind+' bundle initializes without browser errors');
   await page.evaluate(kind=>{
    const onOpen=id=>window.opened.push(id);
    if(kind==='native')window.tenantChakraCRM.renderNativeDeals([{id:'1',title:'Fixture deal',contact_name:'Fixture client',pipeline_id:10,stage_id:100,updated_at:'2026-11-14T00:00:00Z',actions:{update:true}}],{tenantId:'fixture',userId:7,canUpdate:true,onOpen,onEdit:id=>window.edited.push(id),onMove:(row,stage)=>{if(!window.testStageDrop)throw Error('Unexpected move');window.writes.push(String(stage));return {...row,stage_id:stage};},workflows:[100,101,102].map(stage_id=>({pipeline_id:10,stage_id,stage_name:'Stage '+stage_id}))});
    else window.tenantChakraCRM.renderDeals('dealsList',[{source_revision:'a'.repeat(64),kommo_lead_id:7,name:'Fixture deal',contact_name:'Fixture client',pipeline_id:10,status_id:100,raw:{updated_at:1000}}],false,onOpen);
   },kind);
   const card=page.locator(kind==='native'?'[data-native-deal-card]':'[data-deal-open-surface]').first();
   await card.waitFor();
   await card.locator('select').waitFor();
   await card.scrollIntoViewIfNeeded();
   const count=()=>page.evaluate(()=>window.opened.length);
   // Real pointer on padding, not the title.
   await card.click({position:{x:5,y:5}});assert.equal(await count(),1,kind+' surface opens');
   await card.focus();await page.keyboard.press('Enter');await page.keyboard.press('Space');
   assert.equal(await count(),3,kind+' keyboard opens');
   // Existing select receives focus without opening or changing the deal.
   await card.locator('select').click();await page.keyboard.press('Escape');
   assert.equal(await count(),3,kind+' select does not open');
   if(kind==='native'){
    await card.getByRole('button',{name:'Edit',exact:true}).click();
    assert.equal(await page.evaluate(()=>window.edited.length),1);
    assert.equal(await count(),3,'edit does not also open');
    await card.getByRole('button',{name:'Open',exact:true}).click();
    assert.equal(await count(),4,'nested open fires exactly once');
   }
   const before=await count();
   // Actual Chromium drag gesture released outside all drop targets.
   const box=await card.boundingBox(),outside=await page.locator('#outside').boundingBox();
   await page.mouse.move(box.x+10,box.y+10);await page.mouse.down();
   await page.mouse.move(box.x+30,box.y+25,{steps:5});
   await page.mouse.move(outside.x+20,outside.y+50,{steps:12});await page.mouse.up();
   assert.deepEqual(await page.evaluate(()=>window.drags),['start','end'],kind+' native drag events fired');
   assert.equal(await count(),before,kind+' drag does not open');
   await page.addScriptTag({content:fs.readFileSync('docs/tenant-horizontal-scroll.js','utf8')});
   const scroll=page.locator('.app-kanban-swipe').first();await scroll.waitFor();
   const b=await card.boundingBox(),cdp=await page.context().newCDPSession(page);
   // Real touch starts on card content, not its explicit move handle.
   const x=b.x+b.width-12,y=b.y+16,end=b.x+12;
   await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});
   for(let i=1;i<=12;i++){await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x+(end-x)*i/12,y}]});await page.waitForTimeout(20);}
   await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
   await page.waitForFunction(()=>document.querySelector('.app-kanban-swipe').scrollLeft>50);
   assert.equal(await count(),before,kind+' horizontal touch swipe does not open');
   assert.deepEqual(await page.evaluate(()=>window.writes),[],kind+' no mutations');
   assert.deepEqual(errors,[],kind+' interactions have no browser errors');
   // Drop on an authorized column using the desktop pointer.
   await page.setViewportSize({width:1440,height:950});
   await page.evaluate(()=>{window.testStageDrop=true;document.querySelector('.app-kanban-swipe').scrollLeft=0;});
   const column=page.locator(kind==='native'?'[data-native-stage="101"]':'[data-kommo-stage-key="10:101"]');
   await card.dragTo(column,{sourcePosition:{x:12,y:12},targetPosition:{x:24,y:24}});
   await column.locator(kind==='native'?'[data-native-deal-card]':'[data-deal-open-surface]').waitFor();
   assert.deepEqual(await page.evaluate(()=>window.writes.map(String)),['101']);
   assert.equal(await count(),before,kind+' successful drop does not open the card');
   await cdp.detach();await page.close();
  }
  console.log('PASS real Chromium deal surface/keyboard/nested controls, authorized stage drops, drag cancellation and mobile swipe');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
