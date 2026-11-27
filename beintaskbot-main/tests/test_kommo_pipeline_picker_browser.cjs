const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
(async()=>{const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.CHROMIUM_EXECUTABLE_PATH}:{}),args:['--no-sandbox']});try{
 const html=fs.readFileSync(path.join(__dirname,'../docs/tenant-app.html'),'utf8').replace(/<script\b[\s\S]*?<\/script>/gi,'');
 for(const width of [320,390,768,1440])for(const theme of ['light','dark']){
  const page=await browser.newPage({viewport:{width,height:850}});await page.setContent(html);await page.addStyleTag({content:fs.readFileSync(path.join(__dirname,'../ui/src/app-theme.css'),'utf8')});
  await page.addScriptTag({content:fs.readFileSync(path.join(__dirname,'../docs/tenant-kommo-deal-card.js'),'utf8')});
  await page.evaluate(theme=>{
   document.documentElement.dataset.appTheme=theme;window.tenantI18n={language:'ru'};window.moves=[];
   window.tenantKommoStageMove={canIdentify:()=>true,move:async(row,target,context,pipeline)=>{window.moves.push([pipeline,target]);return {...row,pipeline_id:Number(pipeline),status_id:Number(target)};}};
   window.tenantTaskFlow={capture:()=>({}),current:()=>true,read:async()=>({source:'kommo',tasks:[],sources:[{source:'kommo',view:true}]}),api:async()=>({notes:[]})};
   const host=document.getElementById('modalContent');host.innerHTML='<div class="meta"><div>Client</div><div>Phone</div><div><b>Stage</b>New</div></div><div class="chat-placeholder"><div id="chatRows">History</div><div><textarea id="chatText"></textarea></div></div>';document.getElementById('dealModal').classList.add('show');
   window.tenantKommoDealCard.mount({host,deal:{kommo_lead_id:7,pipeline_id:1,status_id:10,stage_name:'Новое',pipeline_name:'Продажи',source_revision:'a'.repeat(64)},isCurrent:()=>true,api:async()=>({move_context:{tenant_id:'tenant',user_id:'actor'},catalog:[{pipeline_id:1,name:'Продажи',stages:[{stage_id:10,name:'Новое',color:'#ffe08b'},{stage_id:11,name:'Обсуждение',color:'#88aaff'}]},{pipeline_id:2,name:'Другая доступная воронка',stages:[{stage_id:20,name:'Длинное название этапа '.repeat(8),color:'#aaffaa'}]}],move_stage_sources:[{pipeline_id:1,stage_id:10},{pipeline_id:1,stage_id:11},{pipeline_id:2,stage_id:20}],move_stage_targets:[{pipeline_id:1,stage_id:10},{pipeline_id:1,stage_id:11},{pipeline_id:2,stage_id:20}]})});
  },theme);
  // Capture the finished UI, not the opacity-zero first frame of app-appear.
  await page.evaluate(async()=>{
   await document.fonts.ready;
   const modal=document.getElementById('dealModal');
   await Promise.all(modal.getAnimations({subtree:true}).map(a=>a.finished.catch(()=>{})));
   await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  });
  await page.waitForFunction(()=>{
   const card=document.querySelector('#dealModal .modal-card'),css=getComputedStyle(card);
   return css.opacity==='1'&&css.transform==='none'&&card.getAnimations().every(a=>a.playState==='finished');
  });
  const painted=await page.locator('#dealModal .modal-card').evaluate(n=>({opacity:getComputedStyle(n).opacity,background:getComputedStyle(n).backgroundColor}));
  assert.equal(painted.opacity,'1','screenshot captures opaque finished card');
  assert(/^rgb\(/.test(painted.background),'card background is opaque');
  fs.mkdirSync(path.join(__dirname,'artifacts'),{recursive:true});
  await page.screenshot({path:path.join(__dirname,`artifacts/kommo-stage-reference-${width}-${theme}.png`)});
  const diagnostics=await page.locator('#dealModal .modal-card,.deal-stage-bar,.deal-stage-trigger').evaluateAll(ns=>ns.map(n=>{const r=n.getBoundingClientRect(),c=getComputedStyle(n);return {className:n.className,x:r.x,y:r.y,width:r.width,height:r.height,border:c.border,padding:c.padding,boxSizing:c.boxSizing};}));
  console.log('STAGE GEOMETRY',width,theme,JSON.stringify(diagnostics));
  fs.writeFileSync(path.join(__dirname,`artifacts/kommo-stage-reference-${width}-${theme}.json`),JSON.stringify(diagnostics,null,2));
  const pipeline=page.locator('[data-deal-pipeline]');
  assert.equal(await pipeline.textContent(),'Продажи');
  const mobile=width<900;
  const trigger=page.locator((mobile?'.modal-head':'.deal-info-panel')+' .deal-stage-trigger');
  const geometry=await trigger.evaluate((n,mobile)=>{
   const bounds=n.getBoundingClientRect(),surface=n.closest('.modal-head,.deal-info-panel').getBoundingClientRect();
   const limit=mobile?n.closest('.modal-head').querySelector('.close').getBoundingClientRect().left:surface.right;
   return {width:bounds.width,height:bounds.height,left:bounds.left,right:bounds.right,maxWidth:surface.width*(mobile ? 0.4 : 1),limit};
  },mobile);
  assert.equal(diagnostics[0].width,width,'full-viewport card');
  assert(geometry.height>=44,'stage control has a touch target');
  assert(geometry.width>0&&geometry.width<=geometry.maxWidth+1,'stage stays within its allotted width');
  assert(geometry.left>=0&&geometry.right<=geometry.limit,'stage stays in its panel without overlapping Close');
  assert.equal(await trigger.getAttribute('title'),await trigger.textContent(),'full stage available when truncated');
  await trigger.click();
  assert(await page.locator('.deal-stage-sheet').isVisible(),'stage control opens the existing picker');

  await page.locator('[data-deal-pipeline-picker]').selectOption('2');assert.deepEqual(await page.evaluate(()=>moves),[],'choosing a pipeline does not write');
  const box=await page.locator('.deal-stage-sheet').boundingBox();assert(box.x>=0&&box.x+box.width<=width+1);assert(box.y>=0&&box.y+box.height<=851);assert(await page.locator('.deal-stage-sheet').evaluate(n=>n.scrollWidth<=n.clientWidth+1));
  assert((await page.locator('[data-stage-id="20"]').boundingBox()).height>=44);
  fs.mkdirSync(path.join(__dirname,'artifacts'),{recursive:true});await page.screenshot({path:path.join(__dirname,`artifacts/kommo-pipeline-${width}-${theme}.png`)});
  await page.keyboard.press('Escape');assert.equal(await page.locator('.deal-stage-picker').isVisible(),false);assert.equal(await page.locator('.deal-stage-trigger').evaluate(n=>n===document.activeElement),true);
  await trigger.click();await page.locator('[data-deal-pipeline-picker]').selectOption('2');await page.locator('[data-stage-id="20"]').click();
  await page.waitForFunction(()=>document.querySelector('[data-deal-pipeline]').textContent==='Другая доступная воронка');
  assert.equal(await trigger.getAttribute('title'),'Длинное название этапа '.repeat(8));
  assert((await trigger.boundingBox()).width<=geometry.maxWidth+1,'long stage stays within its allotted width');
  assert.deepEqual(await page.evaluate(()=>moves),[['2','20']]);assert.equal(await page.locator('.deal-stage-picker').isVisible(),false);await page.close();
 }
 console.log('PASS pipeline chooser reference layout, light/dark 320–1440, no implicit write, Escape and explicit mapped stage');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
