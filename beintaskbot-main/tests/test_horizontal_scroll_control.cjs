const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
assert(fs.readFileSync(path.join(__dirname,'../tenant_web_assets.py'),'utf8').includes("('/assets/tenant-horizontal-scroll.js', 'tenant-horizontal-scroll.js', 'no-cache')"),'controller must have a production asset route');
assert(fs.readFileSync(path.join(__dirname,'../docs/tenant-app.html'),'utf8').includes('<script src="/assets/tenant-horizontal-scroll.js"></script>'),'page loads scroll controller');
const reactSource=fs.readFileSync(path.join(__dirname,'../ui/src/main.jsx'),'utf8');
assert(reactSource.includes('<Table.ScrollArea data-horizontal-scroll="">'),'Chakra table scroll wrapper is discoverable');
assert(reactSource.includes('<Flex data-horizontal-scroll="" gap="4" align="flex-start" overflowX="auto"'),'CRM kanban is discoverable');
const dom=new JSDOM('<body><main><section class="view"><h1>Linear</h1><div class="tl-board" id="board"></div><div class="table" id="list"><div class="row"></div></div></section></main></body>',{runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window,d=w.document,board=d.getElementById('board'),list=d.getElementById('list');
let shown=true,contentWidth=1600;
for(const el of [board,list]){
 Object.defineProperty(el,'clientWidth',{get:()=>shown?500:0});
 Object.defineProperty(el,'scrollWidth',{get:()=>contentWidth});
 el.getBoundingClientRect=()=>({top:100,bottom:500,left:20,right:520,width:shown?500:0,height:400});
}
w.ResizeObserver=class{observe(){}};
w.eval(fs.readFileSync(path.join(__dirname,'../docs/tenant-horizontal-scroll.js'),'utf8'));
const tick=()=>new Promise(r=>w.requestAnimationFrame(()=>w.requestAnimationFrame(r)));
(async()=>{
 await tick();const control=d.getElementById('appHorizontalScroll'),slider=control.querySelector('input');
 assert(!control.hidden);assert.equal(slider.type,'range');assert(slider.getAttribute('aria-label'));
 slider.value='500';slider.dispatchEvent(new w.Event('input'));assert.equal(board.scrollLeft,550);assert.equal(list.scrollLeft,0);
 board.scrollLeft=1100;board.dispatchEvent(new w.Event('scroll'));await tick();assert.equal(slider.value,'1000');
 list.dispatchEvent(new w.Event('pointerover',{bubbles:true}));await tick();slider.value='250';slider.dispatchEvent(new w.Event('input'));assert.equal(list.scrollLeft,275);assert.equal(board.scrollLeft,1100);
 shown=false;w.dispatchEvent(new w.Event('resize'));await tick();assert(control.hidden,'navigation hides orphaned control');
 shown=true;contentWidth=500;w.dispatchEvent(new w.Event('resize'));await tick();assert(control.hidden,'no control without horizontal overflow');
 contentWidth=1600;w.dispatchEvent(new w.Event('resize'));await tick();assert(!control.hidden);
 d.body.dataset.mobileMenu='open';await tick();assert(control.hidden,'navigation overlay is not covered');
 delete d.body.dataset.mobileMenu;await tick();assert(!control.hidden);
 console.log('PASS: compact control follows native scrolling, changes only the selected region, and hides without overflow or behind navigation.');
 dom.window.close();
})().catch(error=>{console.error(error);dom.window.close();process.exitCode=1;});
