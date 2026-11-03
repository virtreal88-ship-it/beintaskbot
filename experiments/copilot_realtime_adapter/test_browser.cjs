const {test}=require('node:test');
const assert=require('node:assert/strict');
const mount=require('./browser.js');

function setup({open}={}){
  const lifecycle={visibilityState:'visible',listeners:{},addEventListener(k,v){this.listeners[k]=v;},removeEventListener(k){delete this.listeners[k];}};
  const document={createElement(){return {hidden:false,textContent:'',disabled:false,handlers:{},setAttribute(){},addEventListener(k,f){this.handlers[k]=f;}};}};
  const root={ownerDocument:document,children:[],append(...xs){this.children.push(...xs);},replaceChildren(){this.children=[];}};
  let released=0,closed=0,channelClosed=0,cb;
  const sent=[],heard=[];
  const audio={play:(id,data)=>heard.push([id,data]),interrupt:()=>0,mute:()=>{},close:async()=>{closed++;}};
  const view=mount(root,{lifecycle,acquireMicrophone:async()=>()=>{released++;},openAudio:open|| (async()=>audio),
    connect:async onEvent=>{cb=onEvent;return {send:e=>sent.push(e),close:async()=>{channelClosed++;}};}});
  return {view,root,lifecycle,sent,heard,audio,emit:e=>cb(e),released:()=>released,closed:()=>closed,channelClosed:()=>channelClosed};
}

test('controls open, mute, interrupt and release audio and shared lease',async()=>{
  const x=setup();await x.view.start();assert.match(x.root.children[0].textContent,/Слушаю/);
  x.root.children[2].handlers.click();assert.deepEqual(x.sent[0],{type:'mute',muted:true});
  await x.emit({type:'audio',item_id:'a',pcm:new Uint8Array(2)});
  await x.emit({type:'interrupt',item_id:'a'});
  assert.deepEqual(x.sent[1],{type:'played',item_id:'a',played_ms:0});
  await x.view.stop();assert.equal(x.closed(),1);assert.equal(x.released(),1);assert.equal(x.channelClosed(),1);
});

test('stop while permission is pending retains lease until capture closes',async()=>{
  let complete;const pending=new Promise(resolve=>complete=resolve);let lateClosed=0;
  const x=setup({open:()=>pending});const start=x.view.start();await new Promise(resolve=>setImmediate(resolve));
  const stopping=x.view.stop();assert.equal(x.released(),0);
  complete({close:async()=>{lateClosed++;}});await Promise.all([start,stopping]);
  assert.ok(lateClosed>=1);assert.equal(x.released(),1);assert.equal(x.channelClosed(),0);
});

test('permission denial releases shared lease and explains how to recover',async()=>{
  const x=setup({open:async()=>{const e=new Error('denied');e.name='NotAllowedError';throw e;}});
  await x.view.start();assert.equal(x.released(),1);assert.match(x.root.children[0].textContent,/настройках браузера/);
});

test('unavailable shared lease prevents microphone opening',async()=>{
  let opened=0;
  const document={createElement(){return {handlers:{},setAttribute(){},addEventListener(k,v){this.handlers[k]=v;}};}};
  const root={ownerDocument:document,children:[],append(...x){this.children.push(...x);},replaceChildren(){}};
  const view=mount(root,{acquireMicrophone:async()=>null,openAudio:async()=>{opened++;},connect:async()=>{}});
  await view.start();assert.equal(opened,0);assert.match(root.children[0].textContent,/другом режиме/);
});

test('server closes promptly even if audio shutdown is stuck; lease stays held',async()=>{
  let finish;const pending=new Promise(resolve=>finish=resolve);let stopped=0;
  const x=setup({open:async()=>({stopNow(){stopped++;},close:()=>pending,mute(){}})});
  await x.view.start();await x.view.stop();
  assert.equal(stopped,1);assert.equal(x.channelClosed(),1);assert.equal(x.released(),0);
  finish();await new Promise(resolve=>setImmediate(resolve));assert.equal(x.released(),1);
});
