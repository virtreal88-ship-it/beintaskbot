/* Experimental view/controller. Not registered in /app or connected to a server. */
(function(root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory;
  else root.mountExperimentalCopilotVoice = factory;
})(typeof window === 'object' ? window : globalThis, function mountVoice(root, {acquireMicrophone, openAudio, connect, lifecycle = globalThis.document}) {
  if (typeof acquireMicrophone !== 'function' || typeof openAudio !== 'function' || typeof connect !== 'function')
    throw new Error('voice_dependencies_missing');
  const document = root.ownerDocument;
  const status = document.createElement('p'); status.setAttribute('role','status'); status.textContent='Голосовой режим выключен';
  const start = document.createElement('button'); start.type='button';start.textContent='Начать голосовой разговор';
  const mute = document.createElement('button'); mute.type='button';mute.textContent='Выключить микрофон';mute.hidden=true;
  const end = document.createElement('button'); end.type='button';end.textContent='Завершить разговор';end.hidden=true;
  root.append(status,start,mute,end);
  let generation=0, release=null, audio=null, opening=null, channel=null, muted=false, running=false, destroyed=false, ending=false, endTimer=null;
  let serverInitiated=false, voiceBlocked=false;
  const uncertain='Завершение разговора не подтверждено; текстовый чат доступен.';
  const playbackFailure='Ошибка воспроизведения. '+uncertain;
  const microphoneUncertain='Не удалось подтвердить отключение микрофона. Текстовый чат доступен.';
  function send(event){
    if(!channel)return;
    try { Promise.resolve(channel.send(event)).catch(()=>stop(uncertain)); }
    catch(_){void stop(uncertain);}
  }

  async function stop(reason='ended',confirmed=false,retainMicrophone=false) {
    if(serverInitiated && !confirmed && reason!==playbackFailure)reason=uncertain;
    if(reason===uncertain || reason===playbackFailure || reason===microphoneUncertain)voiceBlocked=true;
    serverInitiated=false;
    generation++; running=false; ending=false; start.disabled=voiceBlocked; mute.hidden=true; end.hidden=true;
    if(endTimer){clearTimeout(endTimer);endTimer=null;} end.disabled=false;
    const oldChannel=channel, oldAudio=audio, pendingAudio=opening, oldRelease=release;
    channel=null;audio=null;opening=null;release=null;
    // Stop the server and microphone independently; permission dialogs and
    // AudioContext.close may hang. Keep the shared lease until media is closed.
    const channelStop=Promise.resolve().then(()=>oldChannel?.close()).catch(()=>{});
    try { oldAudio?.stopNow?.(); } catch (_) {}
    const mediaStop=(async()=>{
      if(oldAudio)await oldAudio.close();
      if(pendingAudio){let pending;try{pending=await pendingAudio;}catch(error){
          if(error?.message==='microphone_stop_unconfirmed'){
            retainMicrophone=true;voiceBlocked=true;start.disabled=true;
            if(!destroyed)status.textContent=microphoneUncertain;
          }
        }
        if(pending && pending!==oldAudio){pending.stopNow?.();await pending.close();}}
      if(oldRelease && !retainMicrophone)oldRelease();
    })().catch(()=>{});
    muted=false;mute.textContent='Выключить микрофон';
    if(!destroyed) status.textContent=reason==='ended'?'Разговор завершён':reason;
    await Promise.race([Promise.all([channelStop,mediaStop]),new Promise(resolve=>setTimeout(resolve,500))]);
  }
  async function begin() {
    if(running || destroyed || voiceBlocked)return;
    running=true;start.disabled=true;end.hidden=false;status.textContent='Подключение…';
    const version=++generation;
    try {
      const held=await acquireMicrophone();
      if(version!==generation){if(held)held();return;}
      if(typeof held!=='function')throw new Error('microphone_busy');
      release=held;
      const currentOpening=opening=Promise.resolve(openAudio(bytes=>{if(channel && !muted)send({type:'audio',pcm:bytes});}));
      const engine=await currentOpening;
      if(opening===currentOpening)opening=null;
      if(version!==generation){await engine.close();return;}
      audio=engine;
      serverInitiated=true;
      const socket=await connect(async event=>{
        if(version!==generation)return;
        if(ending && ['audio','audio_done','interrupt'].includes(event.type))return;
        try {
          if(event.type==='audio'){audio.play(event.item_id,event.pcm);status.textContent='AI отвечает';}
          else if(event.type==='audio_done'){
            Promise.resolve(audio.finished(event.item_id)).then(done=>{
              if(done && version===generation)send({type:'played_all',item_id:event.item_id});
            }).catch(()=>stop(playbackFailure));
          }
          else if(event.type==='interrupt'){
            const ms=audio.interrupt(event.item_id);
            send({type:'played',item_id:event.item_id,played_ms:ms});
            status.textContent='Слушаю';
          } else if(event.type==='ready')status.textContent='Слушаю';
          else if(event.type==='ended')await stop('Соединение завершено',true);
          else if(event.type==='end_unconfirmed')await stop(uncertain);
        } catch (_) { await stop(playbackFailure); }
      });
      if(version!==generation){await socket.close();return;}
      channel=socket;mute.hidden=false;status.textContent='Слушаю';
    } catch(error) {
      if(version!==generation)return;
      const code=error?.message==='microphone_stop_unconfirmed'?microphoneUncertain:
        error?.name==='NotAllowedError'?'Нет доступа к микрофону. Разрешите микрофон в настройках браузера.':
        error?.message==='microphone_busy'?'Микрофон уже используется в другом режиме.':
        'Не удалось начать голосовой разговор. Проверьте подключение и доступ к микрофону.';
      await stop(code,false,error?.message==='microphone_stop_unconfirmed');
    }
  }
  start.addEventListener('click',begin);
  end.addEventListener('click',()=>{
    if(!channel){void stop(uncertain);return;}
    try{audio?.stopNow?.();}catch(_){}
    muted=true;ending=true;end.disabled=true;status.textContent='Завершение разговора…';
    send({type:'end'});endTimer=setTimeout(()=>stop(uncertain),2000);
  });
  mute.addEventListener('click',()=>{
    if(!audio||!channel)return;
    muted=!muted;audio.mute(muted);send({type:'mute',muted});
    mute.textContent=muted?'Включить микрофон':'Выключить микрофон';
    status.textContent=muted?'Микрофон выключен':'Слушаю';
  });
  const hide=()=>{if(lifecycle?.visibilityState==='hidden')void stop(uncertain);};
  lifecycle?.addEventListener?.('visibilitychange',hide);
  return {start:begin,stop,destroy:async()=>{destroyed=true;lifecycle?.removeEventListener?.('visibilitychange',hide);await stop();root.replaceChildren();}};
});
