const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const read=n=>fs.readFileSync(path.join(__dirname,'../docs',n),'utf8');
const dom=new JSDOM(read('tenant-app.html'),{url:'https://fixture.test',runScripts:'outside-only'}),w=dom.window,d=w.document;
(async()=>{try{
 let wide=true,notes=0;const listeners=new Set();w.matchMedia=()=>({get matches(){return wide},addEventListener:(_,f)=>listeners.add(f),removeEventListener:(_,f)=>listeners.delete(f)});
 w.tenantTaskFlow={capture:()=>({}),current:()=>true,read:async()=>({source:'kommo',tasks:[],sources:[{source:'kommo',view:true}]}),api:async(url)=>{if(url.endsWith('/deal-board-preferences'))return {catalog:[]};notes++;return {notes:[]}}};
 w.eval(read('tenant-kommo-deal-card.js'));d.getElementById('dealModal').classList.add('show');
 const host=d.getElementById('modalContent');
 const mount=()=>{host.innerHTML='<p>Details</p><div class="chat-placeholder"><div id="chatRows">Message</div><div><textarea id="chatText"></textarea></div></div>';w.tenantKommoDealCard.mount({host,deal:{kommo_lead_id:7},isCurrent:()=>true});};
 mount();assert.equal(d.getElementById('dealInfoPanel').hidden,false);assert.equal(d.querySelector('.chat-placeholder').hidden,false);assert.equal(notes,0);
 d.getElementById('chatText').value='draft';wide=false;listeners.forEach(f=>f());assert(!d.getElementById('dealInfoPanel').hidden);assert(d.querySelector('.chat-placeholder').hidden);d.querySelector('[data-deal-pane=chat]').click();assert(d.getElementById('dealInfoPanel').hidden);assert(!d.querySelector('.chat-placeholder').hidden);
 wide=true;listeners.forEach(f=>f());assert(!d.querySelector('.chat-placeholder').hidden);assert(!d.getElementById('dealInfoPanel').hidden);assert.equal(d.getElementById('chatText').value,'draft');assert.equal(notes,0);d.querySelector('[data-deal-tab=notes]').click();assert.equal(notes,1);
 d.getElementById('dealModal').dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert(!d.getElementById('dealInfoPanel').hidden);
 mount();await Promise.resolve();assert.equal(listeners.size,1);assert.equal(d.querySelectorAll('#dealInfoPanel').length,1);assert.equal(notes,1);
 console.log('PASS desktop sidebar: initial split, mobile toggle, resize/draft, GET once and listener cleanup');
}finally{w.close()}})().catch(e=>{console.error(e);process.exitCode=1;});
