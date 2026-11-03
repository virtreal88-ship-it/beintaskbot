/* Same-origin transport for the isolated gateway; not registered in /app. */
(function(root,factory){
  if(typeof module==='object' && module.exports)module.exports=factory;
  else root.connectExperimentalCopilotVoice=factory;
})(typeof window==='object'?window:globalThis, async function connectVoice(path,onEvent,env=globalThis){
  const url=new URL(path,env.location.href);
  if(url.origin!==env.location.origin || !['https:','http:'].includes(url.protocol))throw new Error('voice_origin_invalid');
  if(url.protocol==='http:' && !['127.0.0.1','localhost'].includes(url.hostname))throw new Error('voice_https_required');
  url.protocol=url.protocol==='https:'?'wss:':'ws:';
  const socket=new env.WebSocket(url.toString());
  socket.binaryType='arraybuffer';
  await new Promise((resolve,reject)=>{
    const timer=env.setTimeout(()=>{socket.close();reject(new Error('voice_connect_timeout'));},5000);
    socket.onopen=()=>{env.clearTimeout(timer);resolve();};
    socket.onerror=()=>{env.clearTimeout(timer);socket.close();reject(new Error('voice_connect_failed'));};
  });
  socket.onmessage=event=>{
    try{
      const msg=JSON.parse(event.data);
      if(msg.type==='audio'){
        if(typeof msg.pcm_base64!=='string'||msg.pcm_base64.length>12800)throw new Error('invalid_audio');
        const raw=env.atob(msg.pcm_base64),pcm=Uint8Array.from(raw,ch=>ch.charCodeAt(0));
        onEvent({type:'audio',item_id:msg.item_id,pcm});
      }else if(['ready','interrupt','audio_done','ended'].includes(msg.type))onEvent(msg);
      else if(msg.type==='error')onEvent({type:'ended'});
    }catch(_){socket.close();onEvent({type:'ended'});}
  };
  socket.onclose=()=>onEvent({type:'ended'});
  return {
    send(event){
      if(socket.readyState!==env.WebSocket.OPEN)throw new Error('voice_disconnected');
      if(event.type==='audio' && event.pcm instanceof Uint8Array && event.pcm.byteLength<=9600){
        socket.send(event.pcm.buffer.slice(event.pcm.byteOffset,event.pcm.byteOffset+event.pcm.byteLength));return;
      }
      if(['mute','played','played_all','end'].includes(event.type)){socket.send(JSON.stringify(event));return;}
      throw new Error('voice_event_invalid');
    },
    async close(){socket.onclose=null;socket.close();},
  };
});
