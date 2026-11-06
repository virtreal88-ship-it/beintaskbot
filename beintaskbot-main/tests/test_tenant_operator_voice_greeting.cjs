const assert=require('node:assert/strict');
const test=require('node:test');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const mountVoice=require('../docs/tenant-copilot-voice-controller.js');

test('pilot speaks first and holds microphone uplink until greeting playback completes',async()=>{
  const dom=new JSDOM('<div id="voice"></div>',{url:'https://crm.test/app'});
  const root=dom.window.document.querySelector('#voice');
  let capture,providerEvent,socket;
  const sent=[];
  const voice=mountVoice(root,{
    acquireMicrophone:async()=>()=>{},
    openAudio:async callback=>{
      capture=callback;
      return {play(){},finished:async()=>true,interrupt(){return 0},stopNow(){},async close(){},mute(){},flush(){}};
    },
    connect:async onEvent=>{
      providerEvent=onEvent;
      onEvent({type:'ready',greeting_pending:true});
      socket={send:event=>sent.push(event),async close(){}};
      return socket;
    },
    lifecycle:{visibilityState:'visible',addEventListener(){},removeEventListener(){}},
  });
  root.querySelector('button').click();
  const deadline=Date.now()+1000;
  while(!socket){assert.ok(Date.now()<deadline,`voice connection did not start: ${root.textContent}`);await new Promise(resolve=>setTimeout(resolve,5));}
  const frame=new Uint8Array(320);
  capture(frame);
  assert.equal(sent.length,0,'initial microphone frames are held during the greeting');
  providerEvent({type:'audio',item_id:'greeting-item',pcm:frame});
  capture(frame);
  assert.equal(sent.length,0,'the mic stays gated until the greeting is played');
  providerEvent({type:'listening'});
  capture(frame);
  assert.equal(sent.length,1);
  assert.equal(sent[0].type,'audio');
  await voice.destroy();
  dom.window.close();
});
