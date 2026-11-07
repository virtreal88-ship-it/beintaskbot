const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
assert(fs.readFileSync(path.join(__dirname,'../tenant_web_assets.py'),'utf8').includes("('/assets/tenant-horizontal-scroll.js', 'tenant-horizontal-scroll.js', 'no-cache')"),'controller must have a production asset route');
assert(fs.readFileSync(path.join(__dirname,'../docs/tenant-app.html'),'utf8').includes('<script src="/assets/tenant-horizontal-scroll.js"></script>'),'page loads scroll controller');
const reactSource=fs.readFileSync(path.join(__dirname,'../ui/src/main.jsx'),'utf8');
assert(reactSource.includes('<Table.ScrollArea data-horizontal-scroll="">'),'Chakra table scroll wrapper is discoverable');
assert(reactSource.includes('<Flex data-horizontal-scroll="" gap="4" align="flex-start" overflowX="auto"'),'CRM kanban is discoverable');
const dom=new JSDOM('<body><main><section class="view"><h1>Linear</h1><div class="tl-board" id="board"></div><div class="table" id="list"><div class="row"></div></div></section></main><div class="modal" id="dealModal"></div></body>',{runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window,d=w.document,board=d.getElementById('board'),list=d.getElementById('list');
let shown=true,contentWidth=1600;
for(const el of [board,list]){
 Object.defineProperty(el,'clientWidth',{get:()=>shown?500:0});
 Object.defineProperty(el,'scrollWidth',{get:()=>contentWidth});
 el.getBoundingClientRect=()=>({top:100,bottom:500,left:20,right:520,width:shown?500:0,height:400});
}
w.ResizeObserver=class{observe(){}};
const observers=[],NativeMutationObserver=w.MutationObserver;
w.MutationObserver=class extends NativeMutationObserver{constructor(callback){super(callback);observers.push(this);}};
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
 const modal=d.getElementById('dealModal');
 Object.defineProperty(modal,'clientWidth',{get:()=>modal.classList.contains('show')?600:0});
 modal.getBoundingClientRect=()=>({top:0,bottom:700,left:0,right:600,width:600,height:700});
 // API completion opens a legacy modal outside main without focus or pointer events.
 await new Promise(resolve=>w.setTimeout(()=>{modal.classList.add('show');resolve();},0));
 await tick();assert(control.hidden,'async body-level modal hides the control without user interaction');
 modal.classList.remove('show');await tick();assert(!control.hidden,'closing body-level modal restores the control');
 const portal=d.createElement('div');portal.setAttribute('role','dialog');portal.setAttribute('aria-modal','true');
 Object.defineProperty(portal,'clientWidth',{value:600});portal.getBoundingClientRect=modal.getBoundingClientRect;
 d.body.append(portal);await tick();assert(control.hidden,'new body-level dialog portal hides the control');
 portal.remove();await tick();assert(!control.hidden,'removing dialog portal restores the control');
 console.log('PASS: compact control follows native scrolling, changes only the selected region, and hides without overflow or behind navigation and asynchronous body-level dialogs.');
 observers.forEach(observer=>observer.disconnect());dom.window.close();
})().catch(error=>{console.error(error);observers.forEach(observer=>observer.disconnect());dom.window.close();process.exitCode=1;});
