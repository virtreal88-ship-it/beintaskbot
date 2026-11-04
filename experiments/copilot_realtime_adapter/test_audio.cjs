const {test}=require('node:test');
const assert=require('node:assert/strict');
const openAudio=require('../../beintaskbot-main/docs/tenant-copilot-voice-audio.js');

function fixture({badDisconnect=false,failTrack=false,badRate=false}={}){
  let first=0,second=0;
  const tracks=[{stop(){first++;if(failTrack && first===1)throw new Error('synthetic track failure');}},
                {stop(){second++;}}];
  const node=()=>({connect(){},disconnect(){if(badDisconnect)throw new Error('synthetic disconnect');}});
  class AudioContext {
    constructor(){this.sampleRate=badRate?44100:24000;this.destination={};this.state='running';}
    async resume(){} createMediaStreamSource(){return node();} createScriptProcessor(){return node();}
    createGain(){return {...node(),gain:{value:0}};} async close(){this.state='closed';}
  }
  return {deps:{AudioContext,navigator:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>tracks,getAudioTracks:()=>tracks})}}},
          counts:()=>[first,second]};
}

test('disconnect exceptions never prevent stopping every track',async()=>{
  const f=fixture({badDisconnect:true});const audio=await openAudio(()=>{},f.deps);
  audio.stopNow();await audio.close();await audio.close();
  assert.deepEqual(f.counts(),[1,1]);
});

test('failed track is retried without skipping other tracks',async()=>{
  const f=fixture({failTrack:true});const audio=await openAudio(()=>{},f.deps);
  assert.throws(()=>audio.stopNow(),/microphone_stop_unconfirmed/);
  assert.deepEqual(f.counts(),[1,1]);
  await audio.close();await audio.close();
  assert.deepEqual(f.counts(),[2,1]);
});

test('failed startup cannot hide unconfirmed track cleanup',async()=>{
  const f=fixture({failTrack:true,badRate:true});
  await assert.rejects(()=>openAudio(()=>{},f.deps),/microphone_stop_unconfirmed/);
  assert.deepEqual(f.counts(),[1,1]);
});
