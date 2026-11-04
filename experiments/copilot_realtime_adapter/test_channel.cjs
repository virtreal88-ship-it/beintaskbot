const {test}=require('node:test');
const assert=require('node:assert/strict');
const connect=require('../../beintaskbot-main/docs/tenant-copilot-voice-channel.js');

function fixture(){
  const sockets=[];
  class Socket {
    static OPEN=1;
    constructor(url){this.url=url;this.readyState=0;this.sent=[];sockets.push(this);}
    open(){this.readyState=1;this.onopen?.();}
    message(value){this.onmessage?.({data:JSON.stringify(value)});}
    send(value){this.sent.push(value);}
    close(){this.readyState=3;this.onclose?.();}
  }
  return {sockets,env:{location:{href:'https://example.test/app',origin:'https://example.test'},
                        WebSocket:Socket,setTimeout,clearTimeout,atob}};
}

test('audio channel waits for server admission after websocket open',async()=>{
  const f=fixture(),events=[];let resolved=false;
  const pending=connect('/voice',event=>events.push(event),f.env).then(value=>{resolved=true;return value;});
  const socket=f.sockets[0];socket.open();await Promise.resolve();
  assert.equal(resolved,false);assert.deepEqual(socket.sent,[]);
  socket.message({type:'ready'});const channel=await pending;
  assert.deepEqual(events,[{type:'ready'}]);
  channel.send({type:'audio',pcm:new Uint8Array([1,2])});
  assert.deepEqual([...new Uint8Array(socket.sent[0])],[1,2]);
  await channel.close();
});

test('early audio or close rejects admission before microphone traffic',async()=>{
  for(const event of ['audio','close']){
    const f=fixture(),events=[];const pending=connect('/voice',value=>events.push(value),f.env);
    const rejected=assert.rejects(pending,/voice_not_ready|voice_connect_failed/);
    const socket=f.sockets[0];socket.open();
    if(event==='audio')socket.message({type:'audio',pcm_base64:'AAA='});else socket.close();
    await rejected;assert.deepEqual(socket.sent,[]);assert.deepEqual(events,[]);
  }
});

test('socket errors after admission report unconfirmed end once',async()=>{
  const f=fixture(),events=[];const pending=connect('/voice',event=>events.push(event),f.env);
  const socket=f.sockets[0];socket.open();socket.message({type:'ready'});await pending;
  socket.onerror();socket.onerror();
  assert.deepEqual(events,[{type:'ready'},{type:'end_unconfirmed'}]);
});
