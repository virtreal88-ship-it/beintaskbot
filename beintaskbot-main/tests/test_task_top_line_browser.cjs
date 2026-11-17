const {chromium}=require('../.support-test-deps/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});try{
for(const width of [390,1440])for(const theme of ['light','dark'])for(const source of ['native','kommo']){
 const page=await browser.newPage({viewport:{width,height:900}});
 const shell=fs.readFileSync('docs/tenant-app.html','utf8');
 await page.setContent('<html data-app-theme="'+theme+'"><head>'+[...shell.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map(m=>m[0]).join('')+'</head><body><section id="view-tasks"><article class="panel task-source-panel"><div id="'+(source==='native'?'nativeTasksList':'tasksList')+'" class="table"></div></article></section><article id="other" class="panel">Other</article></body></html>');
 await page.evaluate(()=>{window.tenantViewDemand={watch(){}};window.tenantI18n={language:'ru'};});
 await page.addScriptTag({content:fs.readFileSync('docs/tenant-chakra-ui.js','utf8')});
 await page.addStyleTag({content:fs.readFileSync('ui/src/app-theme.css','utf8')});
 await page.evaluate(({source})=>{document.body.dataset.appUi='kwid';window.tenantChakraCRM.renderTasks([{id:'one',text:'Example task',status:'open',due_at:'2020-01-01'}],{source,target:source==='native'?'nativeTasksList':'tasksList'});},{source});
 await page.locator('[data-task-kanban]').waitFor();
 for(const selector of ['.task-source-panel','.task-source-panel>.table'])assert.equal(await page.locator(selector).evaluate(n=>getComputedStyle(n).borderTopWidth),'0px','no redundant upper line '+source);
 assert.equal(await page.locator('[data-task-stage-header]').first().evaluate(n=>getComputedStyle(n).borderBottomWidth),'2px','stage underline retained');
 assert.equal(await page.locator('#other').evaluate(n=>getComputedStyle(n).borderTopWidth),'1px','unrelated panel unchanged');
 await page.getByRole('button',{name:'Таблица',exact:true}).click();
 assert.equal(await page.locator('.task-source-panel').evaluate(n=>getComputedStyle(n).borderTopWidth),'1px','table frame unchanged');
 await page.getByRole('button',{name:'Канбан',exact:true}).click();
 assert.equal(await page.locator('.task-source-panel').evaluate(n=>getComputedStyle(n).borderTopWidth),'0px');
 fs.mkdirSync('tests/artifacts/task-top-line',{recursive:true});await page.screenshot({path:`tests/artifacts/task-top-line/${source}-${theme}-${width}.png`});
 await page.close();
}console.log('PASS task upper line: native/Kommo, light/dark, mobile/desktop, repeated view toggle, unrelated frame');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
