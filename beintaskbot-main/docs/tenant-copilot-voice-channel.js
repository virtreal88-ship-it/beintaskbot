/* Same-origin transport for the private Copilot voice gateway. */
(function(root,factory){
  if(typeof module==='object' && module.exports)module.exports=factory;
  else root.connectExperimentalCopilotVoice=factory;
})(typeof window==='object'?window:globalThis, async function connectVoice(path,onEvent,env=globalThis){
  const url=new URL(path,env.location.href);
  if(url.origin!==env.location.origin || !['https:','http:'].includes(url.protocol))throw new Error('voice_origin_invalid');
  if(url.protocol==='http:' && !['127.0.0.1','localhost'].includes(url.hostname))throw new Error('voice_https_required');
  url.protocol=url.protocol==='https:'?'wss:':'ws:';
  const socket=new env.WebSocket(url.toString());
  let terminal=false,ready=false,opened=false,settled=false,resolveReady,rejectReady;
  socket.binaryType='arraybuffer';
  const admission=new Promise((resolve,reject)=>{resolveReady=resolve;rejectReady=reject;});
  // Worker startup bounds provider creation and the Realtime handshake at
  // 12 seconds. Allow a small transport/scheduling margin before failing.
  const timer=env.setTimeout(()=>fail('voice_connect_timeout'),15000);
  function fail(code){
    if(!settled){settled=true;env.clearTimeout(timer);rejectReady(new Error(code));}
    terminal=true;socket.close();
  }
  socket.onopen=()=>{opened=true;};
  socket.onerror=()=>{if(!ready)fail('voice_connect_failed');
                      else if(!terminal){terminal=true;socket.close();onEvent({type:'end_unconfirmed'});}};
  socket.onmessage=event=>{
    try{
      const msg=JSON.parse(event.data);
      if(!ready){
        if(!opened || msg.type!=='ready')throw new Error('voice_not_ready');
        ready=true;settled=true;env.clearTimeout(timer);resolveReady();onEvent({type:'ready',greeting_pending:msg.greeting_pending===true});return;
      }
      if(msg.type==='audio'){
        if(typeof msg.pcm_base64!=='string'||msg.pcm_base64.length>12800)throw new Error('invalid_audio');
        const raw=env.atob(msg.pcm_base64),pcm=Uint8Array.from(raw,ch=>ch.charCodeAt(0));
        onEvent({type:'audio',item_id:msg.item_id,pcm});
      }else if(['ready','thinking','listening','talk_ready','talk_empty','interrupt','audio_done','ended','end_unconfirmed'].includes(msg.type)){
        if(['ended','end_unconfirmed'].includes(msg.type))terminal=true;
        onEvent(msg);
      } else if(msg.type==='error'){terminal=true;onEvent({type:'end_unconfirmed'});}
    }catch(_){if(!ready)fail('voice_not_ready');else{terminal=true;socket.close();onEvent({type:'end_unconfirmed'});}}
  };
  socket.onclose=()=>{if(!ready){if(!settled){settled=true;env.clearTimeout(timer);rejectReady(new Error('voice_connect_failed'));}}
                       else if(!terminal)onEvent({type:'end_unconfirmed'});};
  await admission;
  return {
    send(event){
      if(socket.readyState!==env.WebSocket.OPEN)throw new Error('voice_disconnected');
      if(event.type==='audio' && event.pcm instanceof Uint8Array && event.pcm.byteLength<=9600){
        socket.send(event.pcm.buffer.slice(event.pcm.byteOffset,event.pcm.byteOffset+event.pcm.byteLength));return;
      }
      if(['mute','talk_start','stop_response','talk_end','played','played_all','playback_scheduled','playback_failed','end'].includes(event.type)){socket.send(JSON.stringify(event));return;}
      throw new Error('voice_event_invalid');
    },
    async close(){socket.onclose=null;socket.close();},
  };
});
