const {chromium}=require('../.support-test-deps/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
 try{
  for(const width of [320,360,390,430,1280]){
   const page=await browser.newPage({viewport:{width,height:850},hasTouch:true});
   const columns=attr=>Array.from({length:3},(_,i)=>'<section '+attr+'="'+i+'" style="flex:0 0 280px;width:280px;max-width:360px;padding:8px;box-sizing:border-box"><h2>Stage '+i+'</h2><button style="touch-action:none" data-drag>Move</button><div style="height:1000px">Long content</div></section>').join('');
   for(const kind of ['tasks','kommo','native']){
    const board=kind==='tasks'?'<div data-task-kanban data-horizontal-scroll>':kind==='kommo'?'<div data-horizontal-scroll>':'<div data-native-deal-board><div>';
    await page.setContent('<style>body{margin:0}main{padding:16px;box-sizing:border-box}main div:has(>section){display:flex;overflow-x:auto;gap:16px;max-width:100%}</style><body data-app-ui="kwid"><main><section id="view-'+(kind==='tasks'?'tasks':'deals')+'"><article class="panel task-source-panel"><h2 id="sourceHeading">Kommo</h2><div id="tasksList"></div>'+board+columns(kind==='tasks'?'data-task-column':kind==='kommo'?'data-kommo-stage-key':'data-native-stage')+'</div>'+(kind==='native'?'</div>':'')+'</article></section></main></body>');
    await page.addScriptTag({content:fs.readFileSync('docs/tenant-horizontal-scroll.js','utf8')});
    await page.addStyleTag({content:fs.readFileSync('ui/src/app-theme.css','utf8')});
    const scroll=page.locator('.app-kanban-swipe');await scroll.waitFor();
    assert.equal(await scroll.locator('[data-drag]').first().evaluate(e=>getComputedStyle(e).touchAction),'none');
    const mobile=width<=700;
    const geometry=await scroll.evaluate(e=>({width:e.clientWidth,column:e.firstElementChild.getBoundingClientRect().width,snap:getComputedStyle(e).scrollSnapType}));
    if(!mobile){assert.equal(geometry.column,280);assert.equal(geometry.snap,'none');continue;}
    assert.equal(geometry.width,width,'screen width without redundant gutters despite late app theme');
    const bounds=await scroll.boundingBox();assert(Math.abs(bounds.x)<1&&Math.abs(bounds.x+bounds.width-width)<1,'board meets both viewport edges');
    assert.equal(geometry.column,geometry.width,'one complete column');
    assert.equal(geometry.snap,'x mandatory');
    if(kind==='tasks')assert.equal(await page.locator('#sourceHeading').isVisible(),false);
    assert(await page.locator('#appHorizontalScroll').evaluate(e=>e.hidden),'no floating slider on mobile kanban');
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    const cdp=await page.context().newCDPSession(page);
    async function swipe(x1,y1,x2,y2){
     await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:x1,y:y1}]});
     for(let i=1;i<=12;i++){await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x1+(x2-x1)*i/12,y:y1+(y2-y1)*i/12}]});await page.waitForTimeout(20);}
     await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
     await page.waitForTimeout(650);
    }
    const swipeY=bounds.y+250;
    await swipe(width-30,swipeY,35,swipeY);
    const step=geometry.width+8;
    await page.waitForFunction(step=>Math.abs(document.querySelector('.app-kanban-swipe').scrollLeft-step)<2,step);
    await swipe(35,swipeY,width-30,swipeY);
    await page.waitForFunction(()=>document.querySelector('.app-kanban-swipe').scrollLeft<2);
    await scroll.focus();await page.keyboard.press('ArrowRight');
    await page.waitForFunction(step=>Math.abs(document.querySelector('.app-kanban-swipe').scrollLeft-step)<2,step);
    await swipe(width/2,600,width/2,200);
    assert(await page.evaluate(()=>scrollY>100),'native vertical page scrolling preserved');
    await cdp.detach();
   }
   await page.close();
  }
  console.log('PASS mobile full-width Kanban, real touch next/previous, keyboard, vertical scroll and desktop geometry');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
