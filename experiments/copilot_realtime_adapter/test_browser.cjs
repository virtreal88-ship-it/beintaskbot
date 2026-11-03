const {test}=require('node:test');
const assert=require('node:assert/strict');
const mount=require('./browser.js');

function setup({open,connectOverride}={}){
  const lifecycle={visibilityState:'visible',listeners:{},addEventListener(k,v){this.listeners[k]=v;},removeEventListener(k){delete this.listeners[k];}};
  const document={createElement(){return {hidden:false,textContent:'',disabled:false,handlers:{},setAttribute(){},addEventListener(k,f){this.handlers[k]=f;}};}};
  const root={ownerDocument:document,children:[],append(...xs){this.children.push(...xs);},replaceChildren(){this.children=[];}};
  let released=0,closed=0,channelClosed=0,cb;
  const sent=[],heard=[];
  const audio={play:(id,data)=>heard.push([id,data]),interrupt:()=>0,mute:()=>{},close:async()=>{closed++;}};
  const view=mount(root,{lifecycle,acquireMicrophone:async()=>()=>{released++;},openAudio:open|| (async()=>audio),
    connect:connectOverride|| (async onEvent=>{cb=onEvent;return {send:e=>sent.push(e),close:async()=>{channelClosed++;}};})});
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

test('unconfirmed shutdown is not shown as a normal end',async()=>{
  const x=setup();await x.view.start();x.root.children[3].handlers.click();
  assert.deepEqual(x.sent.at(-1),{type:'end'});
  assert.match(x.root.children[0].textContent,/Завершение/);
  await x.emit({type:'end_unconfirmed'});
  assert.match(x.root.children[0].textContent,/не подтверждено; текстовый чат доступен/);
  assert.equal(x.root.children[1].disabled,true);
  assert.equal(x.released(),1);
});

test('playback failure cannot silently re-enable voice before server stop',async()=>{
  const x=setup();await x.view.start();
  x.audio.play=()=>{throw new Error('synthetic playback failure');};
  await x.emit({type:'audio',item_id:'a',pcm:new Uint8Array(2)});
  assert.match(x.root.children[0].textContent,/Ошибка воспроизведения.*не подтверждено/);
  assert.equal(x.root.children[1].disabled,true);
  assert.equal(x.channelClosed(),1);
});

test('playback completion failure also keeps voice disabled',async()=>{
  const x=setup();await x.view.start();
  x.audio.finished=()=>Promise.reject(new Error('synthetic failure'));
  await x.emit({type:'audio_done',item_id:'a'});
  await new Promise(resolve=>setImmediate(resolve));
  assert.match(x.root.children[0].textContent,/Ошибка воспроизведения.*не подтверждено/);
  assert.equal(x.root.children[1].disabled,true);
});

test('late output while ending cannot restart playback or change status',async()=>{
  const x=setup();await x.view.start();x.root.children[3].handlers.click();
  x.audio.play=()=>{throw new Error('audio engine is stopped');};
  await x.emit({type:'audio',item_id:'a',pcm:new Uint8Array(2)});
  assert.match(x.root.children[0].textContent,/Завершение разговора…/);
  assert.equal(x.root.children[1].disabled,true);
  await x.emit({type:'end_unconfirmed'});
  assert.equal(x.root.children[1].disabled,true);
});

test('local stop after server start requires confirmation before restart',async()=>{
  const x=setup();await x.view.start();await x.view.stop();
  assert.match(x.root.children[0].textContent,/не подтверждено/);
  assert.equal(x.root.children[1].disabled,true);
  await x.view.start();assert.equal(x.channelClosed(),1);
  assert.match(x.root.children[0].textContent,/не подтверждено/);
  assert.equal(x.released(),1);
});

test('unknown connect failure keeps voice blocked and releases microphone',async()=>{
  const x=setup({connectOverride:async()=>{throw new Error('synthetic unknown connect');}});
  await x.view.start();
  assert.match(x.root.children[0].textContent,/не подтверждено/);
  assert.equal(x.root.children[1].disabled,true);assert.equal(x.released(),1);
});

test('unconfirmed startup track cleanup keeps shared microphone lease',async()=>{
  const x=setup({open:async()=>{throw new Error('microphone_stop_unconfirmed');}});
  await x.view.start();
  assert.match(x.root.children[0].textContent,/отключение микрофона/);
  assert.equal(x.root.children[1].disabled,true);assert.equal(x.released(),0);
});

test('late unconfirmed capture cleanup never releases shared lease',async()=>{
  let fail;const pending=new Promise((_resolve,reject)=>{fail=reject;});
  const x=setup({open:()=>pending});const starting=x.view.start();
  await new Promise(resolve=>setImmediate(resolve));
  const stopping=x.view.stop();fail(new Error('microphone_stop_unconfirmed'));
  await Promise.all([starting,stopping]);
  assert.equal(x.released(),0);assert.equal(x.root.children[1].disabled,true);
  assert.match(x.root.children[0].textContent,/отключение микрофона/);
});
