const {chromium}=require(process.env.PLAYWRIGHT_PATH||'../.support-test-deps/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});try{
 for(const width of [320,390,430,1440]){
 const page=await browser.newPage({viewport:{width,height:900},hasTouch:width<700,timezoneId:'Asia/Baku'});
 await page.clock.setFixedTime(new Date('2026-11-17T08:00:00Z'));
 await page.setContent('<html><body style="margin:0"><div id="tasksList"></div></body></html>');
 await page.addScriptTag({content:fs.readFileSync('docs/tenant-chakra-ui.js','utf8')});
 await page.addStyleTag({content:fs.readFileSync('ui/src/app-theme.css','utf8')});
 await page.evaluate(()=>{document.body.dataset.appUi='kwid';window.tenantI18n={language:'ru'};window.moves=[];window.opened=[];window.taskRows=[{id:'1',text:'Original task description',contact_name:'Client',due_at:new Date(Date.now()+3600000).toISOString(),status:'open'}];window.taskSettings={source:'native',canMove:()=>true,onOpen:id=>window.opened.push(id),onMove:async(...args)=>window.moves.push(args)};window.tenantChakraCRM.renderTasks(window.taskRows,window.taskSettings);});
 const card=page.locator('[data-task-card]'),handle=card.locator('[data-task-move-handle]');await handle.waitFor({state:'attached'});await card.scrollIntoViewIfNeeded();
 if(width<700){
 assert.equal(await card.locator('[data-task-move-select]').isVisible(),false,'no footer select');assert.equal(await card.locator('[data-task-move-caption]').isVisible(),false,'no repeated footer caption');
 assert.equal(await card.locator('[data-task-move-grip]').isVisible(),true);
 const a=await handle.boundingBox(),b=await card.boundingBox();assert(a.width>=44&&a.height>=44);assert(a.y-b.y<12&&a.x+a.width<=b.x+b.width,'handle stays in top corner');
 await handle.focus();await page.keyboard.press('Enter');assert.deepEqual(await page.evaluate(()=>opened),['1'],'keyboard opens existing editor without changing deadline');assert.equal(await page.evaluate(()=>moves.length),0);
 assert.equal(await card.evaluate(el=>el.closest('[data-task-column]').dataset.taskColumn),'today','fixture must drag from today to a different day');
 const cdp=await page.context().newCDPSession(page);await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:a.x+a.width/2,y:a.y+a.height/2}]});
 await page.locator('[data-task-column="tomorrow"]').evaluate(el=>el.scrollIntoView({block:'nearest',inline:'center'}));
 const dest=await page.locator('[data-task-column="tomorrow"]').boundingBox();const x=Math.max(10,Math.min(width-10,dest.x+dest.width/2)),y=dest.y+25;
 await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y}]});await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 await page.locator('input[type="datetime-local"]').waitFor();assert.equal(await page.evaluate(()=>moves.length),0,'drag still requires date review');await page.getByRole('button',{name:'Отмена',exact:true}).click();assert.equal(await page.evaluate(()=>moves.length),0);
 }else{assert.equal(await card.locator('[data-task-move-select]').isVisible(),false);assert.equal(await card.locator('[data-task-move-caption]').isVisible(),false);assert.equal(await handle.isVisible(),false);assert.equal(await card.getAttribute('draggable'),'true');await card.dragTo(page.locator('[data-task-column="tomorrow"]'));await page.locator('input[type="datetime-local"]').waitFor();assert.equal(await page.evaluate(()=>moves.length),0);await page.getByRole('button',{name:'Отмена',exact:true}).click();}
 await card.scrollIntoViewIfNeeded();fs.mkdirSync('artifacts/mobile-task-grip',{recursive:true});await page.screenshot({path:`artifacts/mobile-task-grip/${width}.png`});
 await page.evaluate(()=>window.tenantChakraCRM.renderTasks(window.taskRows,{...window.taskSettings,canMove:()=>false}));await handle.waitFor({state:'detached'});assert.equal(await card.getAttribute('draggable'),'false');
 await page.close();console.log('PASS compact task grip',width);
 }
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
