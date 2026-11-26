const {chromium}=require('../.support-test-deps/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
const {appHtml,installFixture}=require('./task_read_save_flow_fixture.cjs');
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});try{for(const width of [320,390,1080,1440]){
 let saved='kommo',writes=0;const context=await browser.newContext({viewport:{width,height:900},timezoneId:'Asia/Baku'}),page=await context.newPage();
 await page.addInitScript(()=>{window.realFetch=window.fetch});await page.addInitScript(installFixture,{hasMore:true});await page.addInitScript(()=>{const original=window.fetch;window.fetch=(url,opt)=>String(url).includes('/task-view-preferences')?window.realFetch(url,opt):original(url,opt)});
 const html=appHtml().replace('</body>',()=>'<script>'+fs.readFileSync('docs/tenant-chakra-ui.js','utf8').replace(/<\/script/gi,'<\\/script')+'</script></body>');
 await page.route('**/*',async route=>{const req=route.request();if(req.url().includes('/task-view-preferences')){if(req.method()==='POST'){saved=req.postDataJSON().source;writes++}return route.fulfill({json:{success:true,source:saved}})}return req.isNavigationRequest()?route.fulfill({contentType:'text/html',body:html}):route.abort()});
 await page.goto('https://fixture.test/app?view=tasks&lang=ru');await page.waitForFunction(()=>taskFeedSource.value==='kommo'&&!taskFeedSource.disabled);assert.equal(await page.locator('.task-source-panel:visible').count(),1);assert.equal(await page.locator('#tasksList').isVisible(),true);
 await page.locator('#kommoTaskCount').waitFor();
 assert.equal(await page.locator('#view-tasks .task-source-panel>h2').count(),0);
 assert.equal(await page.locator('#view-tasks [data-task-flow-label="intro"]').count(),0);
 assert.equal(await page.locator('#view-tasks .top #kommoTaskCount').isVisible(),true);
 assert.equal(await page.locator('#nativeTaskCount').isVisible(),false);
 assert.match(await page.locator('#kommoTaskCount').getAttribute('aria-label'),/ограничен/);
 // Compare heading geometry while accounting for the Tasks counter's own padding.
 const heading=await page.evaluate(()=>{
  const task=document.querySelector('#view-tasks .top'),deal=document.querySelector('#view-deals .top');
  const clone=deal.cloneNode(true);clone.querySelector('h1').textContent=task.querySelector('h1').textContent;clone.querySelector('h1').className=task.querySelector('h1').className;
  clone.querySelector('#dealCount').textContent=document.querySelector('#kommoTaskCount').textContent;
  // On mobile the real toolbar moves both pages' actions out of .top.
  const actions=task.querySelector('.crm-toolbar-actions'),clonedActions=clone.querySelector('.crm-toolbar-actions');
  if(actions){if(clonedActions)clonedActions.replaceWith(actions.cloneNode(true));else clone.append(actions.cloneNode(true));}else clonedActions?.remove();
  clone.style.width=task.getBoundingClientRect().width+'px';clone.style.position='absolute';clone.style.visibility='hidden';
  document.querySelector('#view-tasks').append(clone);
  const chrome=node=>{const css=getComputedStyle(node);return ['paddingTop','paddingBottom','borderTopWidth','borderBottomWidth'].reduce((sum,key)=>sum+parseFloat(css[key]),0);};
  const a=task.getBoundingClientRect(),b=clone.getBoundingClientRect();
  const countSpacing=chrome(task.querySelector('.task-source-count:not([hidden])'))-chrome(clone.querySelector('#dealCount'));
  clone.remove();return {task:a.height,deals:b.height,countSpacing};
 });
 assert(Math.abs(heading.task-heading.deals-heading.countSpacing)<=2,JSON.stringify({width,heading}));
 await page.locator('#kommoTaskViewControls button').first().waitFor();
 assert.equal(await page.locator('.task-view-controls:visible').count(),1);
 const assertBoardGap=async()=>{const gap=await page.evaluate(()=>{const view=document.querySelector('#view-tasks');const mobile=view.querySelector('.crm-mobile-toolbar');const toolbar=mobile&&mobile.getBoundingClientRect().height?mobile:view.querySelector(':scope>.task-flow-filters');const panel=Array.from(view.querySelectorAll(':scope>.task-source-panel')).find(el=>!el.hidden);return panel.getBoundingClientRect().top-toolbar.getBoundingClientRect().bottom;});assert(gap>=11&&gap<=20,JSON.stringify({width,gap,message:'filters and task board need a compact visible gap'}));};
 await assertBoardGap();
 const controls=page.locator('#kommoTaskViewControls button');assert.equal(await controls.count(),2);
 for(const button of await controls.all()){assert.equal((await button.innerText()).trim(),'');assert(await button.getAttribute('aria-label'));assert(await button.getAttribute('title'));const box=await button.boundingBox();assert(box.width>=44&&box.height>=44);}
 if(width>=1080){const boxes=await Promise.all(['#taskSearch','#taskStatus','#kommoTaskViewControls'].map(s=>page.locator(s).boundingBox()));assert(Math.abs(boxes[0].y-boxes[1].y)<1);assert(Math.abs(boxes[0].y-boxes[2].y)<1);}
 assert.equal(await page.locator('#tasksList [data-task-view-controls]').count(),0);
 await page.selectOption('#taskFeedSource','native');await page.waitForFunction(()=>!taskFeedSource.disabled);assert.equal(await page.locator('#nativeTaskCount').isVisible(),true);assert.equal(await page.locator('#kommoTaskCount').isVisible(),false);assert.equal(saved,'native');assert.equal(writes,1);assert.equal(await page.locator('#nativeTasksList').isVisible(),true);await page.getByRole('button',{name:'Таблица',exact:true}).click();assert.equal(await page.locator('#nativeTasksList [data-task-view]').getAttribute('data-task-view'),'table');
 await page.selectOption('#taskFeedSource','kommo');await page.waitForFunction(()=>!taskFeedSource.disabled);await page.selectOption('#taskFeedSource','native');await page.waitForFunction(()=>!taskFeedSource.disabled);assert.equal(await page.locator('#nativeTasksList [data-task-view]').getAttribute('data-task-view'),'table');
 await page.reload();await page.waitForFunction(()=>taskFeedSource.value==='native'&&!taskFeedSource.disabled);assert.equal(await page.locator('.task-source-panel:visible').count(),1);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));fs.mkdirSync('tests/artifacts/task-source',{recursive:true});await page.screenshot({path:'tests/artifacts/task-source/'+width+'.png',fullPage:true});{await page.addScriptTag({content:fs.readFileSync('docs/tenant-mobile-crm-toolbar.js','utf8')});
 await page.waitForTimeout(100);
 assert.equal(await page.locator('#nativeTaskCount').isVisible(),true,'active count stays visible with real mobile toolbar and shell');
 assert.equal(await page.locator('#kommoTaskCount').isVisible(),false);
 const heading=await page.evaluate(()=>{
  const task=document.querySelector('#view-tasks .top'),deal=document.querySelector('#view-deals .top');
  const clone=deal.cloneNode(true);clone.querySelector('h1').textContent=task.querySelector('h1').textContent;clone.querySelector('h1').className=task.querySelector('h1').className;
  clone.querySelector('#dealCount').textContent=document.querySelector('.task-header-counts p:not([hidden])').textContent;
  // On mobile the real toolbar moves both pages' actions out of .top.
  const actions=task.querySelector('.crm-toolbar-actions'),clonedActions=clone.querySelector('.crm-toolbar-actions');
  if(actions){if(clonedActions)clonedActions.replaceWith(actions.cloneNode(true));else clone.append(actions.cloneNode(true));}else clonedActions?.remove();
  clone.style.width=task.getBoundingClientRect().width+'px';clone.style.position='absolute';clone.style.visibility='hidden';
  document.querySelector('#view-tasks').append(clone);
  const chrome=node=>{const css=getComputedStyle(node);return ['paddingTop','paddingBottom','borderTopWidth','borderBottomWidth'].reduce((sum,key)=>sum+parseFloat(css[key]),0);};
  const a=task.getBoundingClientRect(),b=clone.getBoundingClientRect();
  const countSpacing=chrome(task.querySelector('.task-source-count:not([hidden])'))-chrome(clone.querySelector('#dealCount'));
  clone.remove();return {task:a.height,deals:b.height,countSpacing};
 });
 assert(Math.abs(heading.task-heading.deals-heading.countSpacing)<=2,JSON.stringify({width,heading}));

 if(width<=700){
  assert.equal(await page.locator('#view-tasks>.app-mobile-title-row').isVisible(),true);
  await page.locator('#view-tasks .crm-mobile-filter').click();
 }
 await page.selectOption('#taskFeedSource','kommo');await page.waitForFunction(()=>!taskFeedSource.disabled);
 assert.equal(await page.locator('#kommoTaskCount').isVisible(),true,'Kommo count remains visible on mobile');
 assert.equal(await page.locator('#nativeTaskCount').isVisible(),false);
 if(width<=700) await page.locator('#view-tasks .crm-mobile-filter').click();
 await assertBoardGap();
 await context.close();}
}console.log('PASS real app + shipped board: source persistence/reload, one panel, table mode retained, mobile bounds');}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
