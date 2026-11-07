/* PCM audio is transient and never written to disk. */
(function(root,factory){
  if(typeof module==='object' && module.exports)module.exports=factory;
  else root.openExperimentalCopilotAudio=factory;
})(typeof window==='object'?window:globalThis,async function openAudio(onPcm, deps=globalThis, onSpeechStart=()=>{}, options={}){
  const media=deps.navigator?.mediaDevices;
  const Ctx=deps.AudioContext||deps.webkitAudioContext;
  if(!media?.getUserMedia || !Ctx)throw new Error('audio_unsupported');
  let stream,ctx,processor,source,silence,closed=false, currentItem=null,nextTime=0,itemStartTime=null,itemEndTime=null,segments=[],waiters=[];
  let captureParts=[],captureBytes=0,speechFrames=0,localSpeechLatched=false,localMuteTimer=null,localOutputMuted=false;
  const setTimer=deps.setTimeout||setTimeout,clearTimer=deps.clearTimeout||clearTimeout;
  const MAX_PCM_CHUNK_BYTES=2048; // 1 x 1024-sample callback, about 43 ms at 24 kHz, for quicker server-VAD interruption.
  const stoppedTracks=new Set();
  const flush=()=>{
    if(!captureBytes)return;
    const pcm=new Uint8Array(captureBytes);let offset=0;
    for(const part of captureParts){pcm.set(part,offset);offset+=part.byteLength;}
    captureParts=[];captureBytes=0;
    for(let start=0;start<pcm.byteLength;start+=MAX_PCM_CHUNK_BYTES)
      onPcm(pcm.subarray(start,Math.min(pcm.byteLength,start+MAX_PCM_CHUNK_BYTES)));
  };
  const stopTracks=()=>{
    let unconfirmed=false;
    let tracks=[];
    try{tracks=stream?.getTracks()||[];}catch(_){unconfirmed=true;}
    for(const track of tracks){
      if(stoppedTracks.has(track))continue;
      try{track.stop();stoppedTracks.add(track);}catch(_){unconfirmed=true;}
    }
    if(unconfirmed)throw new Error('microphone_stop_unconfirmed');
  };
  try {
    stream=await media.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
    ctx=new Ctx({sampleRate:24000});
    if(ctx.sampleRate!==24000 || !ctx.createScriptProcessor)throw new Error('pcm_24000_unsupported');
    await ctx.resume();
    source=ctx.createMediaStreamSource(stream);
    processor=ctx.createScriptProcessor(1024,1,1);
    silence=ctx.createGain();silence.gain.value=0;
    processor.onaudioprocess=event=>{
      if(closed)return;
      const floats=event.inputBuffer.getChannelData(0),out=new Int16Array(floats.length);
      for(let i=0;i<floats.length;i++){const v=Math.max(-1,Math.min(1,floats[i]));out[i]=v<0?v*32768:v*32767;}
      let sumSquares=0,crossings=0,previous=0;
      for(let i=0;i<floats.length;i++){
        const value=floats[i];sumSquares+=value*value;
        if(i&&((value>=0)!==(previous>=0)))crossings++;
        previous=value;
      }
      const rms=floats.length?Math.sqrt(sumSquares/floats.length):0;
      const zcr=floats.length>1?crossings/(floats.length-1):0;
      // Browser-side fallback pauses queued output quickly on sustained
      // speech-like microphone energy. Echo/noise suppression is enabled at
      // capture; provider VAD remains authoritative for cancelling the turn.
      const speechLike=rms>=0.035&&zcr>=0.01&&zcr<=0.35;
      speechFrames=speechLike?speechFrames+1:0;
      if(options.pushToTalk!==true&&speechFrames>=2&&!localSpeechLatched&&segments.length){
        localSpeechLatched=true;localOutputMuted=true;
        finishWaiters(false);for(const node of segments){try{node.stop();}catch(_){}}
        segments=[];currentItem=null;nextTime=ctx.currentTime;
        if(localMuteTimer)clearTimer(localMuteTimer);
        localMuteTimer=setTimer(()=>{localMuteTimer=null;localOutputMuted=false;localSpeechLatched=false;},750);
        try{onSpeechStart();}catch(_){}
      }else if(!speechLike)localSpeechLatched=false;
      const pcm=new Uint8Array(out.buffer);
      captureParts.push(pcm);captureBytes+=pcm.byteLength;
      if(captureBytes>=MAX_PCM_CHUNK_BYTES)flush();
    };
    source.connect(processor);processor.connect(silence);silence.connect(ctx.destination);
  } catch(error) {
    try{stopTracks();}catch(_){
      // The caller must retain the microphone lease when cleanup is unknown.
      const failure=new Error('microphone_stop_unconfirmed');
      if(ctx)await ctx.close().catch(()=>{});
      throw failure;
    }
    if(ctx)await ctx.close().catch(()=>{});
    throw error;
  }
  const finishWaiters=value=>{for(const done of waiters)done(value);waiters=[];};
  const stopNow=()=>{
    if(!closed){
      closed=true;finishWaiters(false);
      if(localMuteTimer){clearTimer(localMuteTimer);localMuteTimer=null;}
      captureParts=[];captureBytes=0;
      for(const node of segments){try{node.stop();}catch(_){}}
      segments=[];
      try{processor.onaudioprocess=null;}catch(_){}
      for(const node of [source,processor,silence]){try{node.disconnect();}catch(_){}}
    }
    // Retry tracks whose stop threw on a previous call, while still attempting
    // all other tracks. Never signal successful cleanup if any is unconfirmed.
    stopTracks();
  };
  return {
    flush,
    contextState(){return ctx?.state||'unavailable';},
    mute(value){for(const track of stream.getAudioTracks())track.enabled=!value;},
    play(item,pcm){
      if(localOutputMuted)return;
      if(closed || !(pcm instanceof Uint8Array) || !pcm.byteLength || pcm.byteLength%2)throw new Error('invalid_audio');
      if(currentItem!==null && currentItem!==item){
        if(ctx.currentTime<nextTime)throw new Error('overlapping_output_items');
        currentItem=null;itemStartTime=null;itemEndTime=null;segments=[];
      }
      if(currentItem===null){currentItem=item;itemStartTime=null;itemEndTime=null;}
      const length=pcm.byteLength/2,buf=ctx.createBuffer(1,length,24000),channel=buf.getChannelData(0),view=new DataView(pcm.buffer,pcm.byteOffset,pcm.byteLength);
      for(let i=0;i<length;i++)channel[i]=view.getInt16(i*2,true)/32768;
      const node=ctx.createBufferSource();node.buffer=buf;node.connect(ctx.destination);
      const at=Math.max(ctx.currentTime,nextTime);segments.push(node);
      if(itemStartTime===null)itemStartTime=at;
      node.onended=()=>{segments=segments.filter(s=>s!==node);if(!segments.length)finishWaiters(true);};
      node.start(at);nextTime=at+length/24000;itemEndTime=nextTime;
    },
    finished(item){
      if(closed||currentItem!==item)return Promise.resolve(false);
      if(!segments.length)return Promise.resolve(true);
      return new Promise(resolve=>waiters.push(resolve));
    },
    interrupt(item){
      // The server can deliver an interrupt after the browser has already
      // advanced or cleared its local item (for example, when onended races
      // speech_started). Treat the command as "stop all current output";
      // throwing here makes the controller close the whole voice session.
      if(localMuteTimer){clearTimer(localMuteTimer);localMuteTimer=null;}
      localOutputMuted=false;localSpeechLatched=false;
      finishWaiters(false);for(const node of segments){try{node.stop();}catch(_){}}
      segments=[];currentItem=null;itemStartTime=null;itemEndTime=null;nextTime=ctx.currentTime;
      // Browser APIs do not prove what reached the listener, particularly with
      // wireless devices. Conservatively discard the whole interrupted item.
      return 0;
    },
    interruptForTalk(){
      const item=currentItem;
      let audioEndMs=0;
      if(item!==null&&itemStartTime!==null&&itemEndTime!==null){
        const totalMs=Math.max(0,(itemEndTime-itemStartTime)*1000);
        const deviceLatency=Number.isFinite(ctx.outputLatency)?ctx.outputLatency:
          (Number.isFinite(ctx.baseLatency)?ctx.baseLatency:0);
        // Bias the offset behind the output clock so unheard audio queued in a
        // browser/device buffer is not retained in Realtime conversation context.
        const safelyPlayedMs=Math.max(0,(ctx.currentTime-itemStartTime-deviceLatency-0.08)*1000);
        audioEndMs=Math.floor(Math.min(totalMs,safelyPlayedMs));
      }
      finishWaiters(false);for(const node of segments){try{node.stop();}catch(_){}}
      segments=[];currentItem=null;itemStartTime=null;itemEndTime=null;nextTime=ctx.currentTime;
      localOutputMuted=false;localSpeechLatched=false;
      return item===null?null:{item_id:item,audio_end_ms:audioEndMs};
    },
    interruptLocalSpeech(){
      if(!segments.length)return false;
      localSpeechLatched=true;localOutputMuted=true;
      finishWaiters(false);for(const node of segments){try{node.stop();}catch(_){}}
      segments=[];currentItem=null;itemStartTime=null;itemEndTime=null;nextTime=ctx.currentTime;
      if(localMuteTimer)clearTimer(localMuteTimer);
      localMuteTimer=setTimer(()=>{localMuteTimer=null;localOutputMuted=false;localSpeechLatched=false;},750);
      return true;
    },
    stopNow,
    async close(){
      let failure=null;
      try{stopNow();}catch(error){failure=error;}
      try{if(ctx.state!=='closed')await ctx.close();}catch(error){if(!failure)failure=error;}
      if(failure)throw failure;
    },
  };
});
