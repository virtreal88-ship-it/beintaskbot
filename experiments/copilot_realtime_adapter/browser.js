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
  let serverInitiated=false, voiceBlocked=false, cleanupPending=0;
  const uncertain='Завершение разговора не подтверждено; текстовый чат доступен.';
  const playbackFailure='Ошибка воспроизведения. '+uncertain;
  const microphoneUncertain='Не удалось подтвердить отключение микрофона. Текстовый чат доступен.';
  function send(event){
    if(!channel)return;
    const version=generation;
    try { Promise.resolve(channel.send(event)).catch(()=>{if(version===generation)void stop(uncertain);}); }
    catch(_){if(version===generation)void stop(uncertain);}
  }

  async function stop(reason='ended',confirmed=false,retainMicrophone=false) {
    if(serverInitiated && !confirmed && reason!==playbackFailure)reason=uncertain;
    if(reason===uncertain || reason===playbackFailure || reason===microphoneUncertain)voiceBlocked=true;
    serverInitiated=false;
    const stopVersion=++generation;
    running=false; ending=false; cleanupPending++;start.disabled=true; mute.hidden=true; end.hidden=true;
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
        if(pending && pending!==oldAudio){try{pending.stopNow?.();}catch(_){} await pending.close();}}
      if(oldRelease && !retainMicrophone)oldRelease();
    })().then(()=>{
      cleanupPending--;start.disabled=voiceBlocked || cleanupPending>0;
      if(!destroyed && !voiceBlocked && cleanupPending===0 && generation===stopVersion)
        status.textContent=reason==='ended'?'Разговор завершён':reason;
    }).catch(()=>{
      cleanupPending--;
      voiceBlocked=true;start.disabled=true;
      if(!destroyed)status.textContent=microphoneUncertain;
    });
    muted=false;mute.textContent='Выключить микрофон';
    if(!destroyed) status.textContent=voiceBlocked?reason:'Отключаем микрофон…';
    await Promise.race([Promise.all([channelStop,mediaStop]),new Promise(resolve=>setTimeout(resolve,500))]);
  }
  async function begin() {
    if(running || destroyed || voiceBlocked || cleanupPending>0)return;
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
              if(done && version===generation && !ending)send({type:'played_all',item_id:event.item_id});
            }).catch(()=>{if(version===generation && !ending)void stop(playbackFailure);});
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
    const endingVersion=generation;
    send({type:'end'});endTimer=setTimeout(()=>{if(ending && endingVersion===generation)void stop(uncertain);},2000);
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
