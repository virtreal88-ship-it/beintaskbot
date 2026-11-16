const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../ui/src/app-shell.js'),'utf8').split('function installMobileBottomNav(')[1];
let open=false,active=true,editing=false,modal=false,observer,frame;
const style=new Map(),sidebar={dataset:{},inert:false,getBoundingClientRect:()=>({height:72})};
const nav={setAttribute(){},contains:()=>false,querySelector:()=>null,addEventListener(){}};
const body={isConnected:true};const root={style:{getPropertyValue:k=>style.get(k),setProperty:(k,v)=>style.set(k,v)}};
const doc={body,documentElement:root,get activeElement(){return {matches:()=>editing}},querySelector:s=>open&&active?{}:null,querySelectorAll:()=>modal?[{getClientRects:()=>[{}]}]:[],addEventListener(){}};
const mobile={matches:true,addEventListener(){}};
const ctx={document:doc,window:{document:doc},getComputedStyle:()=>({visibility:'visible'}),requestAnimationFrame:fn=>(frame=fn,1),MutationObserver:class{constructor(fn){observer=fn}observe(el,options){assert(options.attributeFilter.includes('data-conversation-open'))}}};
vm.runInNewContext('function installMobileBottomNav('+source+';installMobileBottomNav({sidebar,nav,mobile})',Object.assign(ctx,{sidebar,nav,mobile}));
function check(expected,height){frame();assert.equal(sidebar.dataset.bottomNavSuppressed,String(expected));assert.equal(sidebar.inert,expected);assert.equal(style.get('--app-bottom-nav-height'),height+'px');}
check(false,72);open=true;observer();check(true,0);editing=true;observer();check(true,0);editing=false;observer();check(true,0);active=false;observer();check(false,72);active=true;mobile.matches=false;observer();check(false,0);mobile.matches=true;open=false;observer();check(false,72);modal=true;observer();check(true,0);modal=false;observer();check(false,72);
console.log('PASS chat state, focus/blur, route leave, desktop, modal restoration and reserved height');
