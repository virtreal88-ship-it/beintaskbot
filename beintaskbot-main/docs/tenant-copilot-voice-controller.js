/* Voice controls; callers must complete server preflight before mounting. */
(function(root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory;
  else root.mountExperimentalCopilotVoice = factory;
})(typeof window === 'object' ? window : globalThis, function mountVoice(root, {acquireMicrophone, openAudio, connect, lifecycle = globalThis.document, language='ru', onIdle=()=>{}, onState=()=>{}}) {
  if (typeof acquireMicrophone !== 'function' || typeof openAudio !== 'function' || typeof connect !== 'function')
    throw new Error('voice_dependencies_missing');
  const catalog={ru:{off:'Голосовой разговор не начат',start:'Начать голосовой разговор',mute:'Выключить микрофон',unmute:'Включить микрофон',end:'Завершить разговор',uncertain:'Завершение разговора не подтверждено; текстовый чат доступен.',playback:'Ошибка воспроизведения. ',microphone:'Не удалось подтвердить отключение микрофона. Текстовый чат доступен.',connecting:'Подключение…',disconnecting:'Отключаем микрофон…',ended:'Разговор завершён',connectionEnded:'Соединение завершено',responding:'AI отвечает',listening:'Слушаю',denied:'Нет доступа к микрофону. Разрешите микрофон в настройках браузера.',busy:'Микрофон уже используется в другом режиме.',failed:'Не удалось начать голосовой разговор. Проверьте подключение и доступ к микрофону.',ending:'Завершение разговора…',muted:'Микрофон выключен'},
    az:{off:'Səsli söhbət başlamayıb',start:'Səsli söhbətə başla',mute:'Mikrofonu söndür',unmute:'Mikrofonu aç',end:'Söhbəti bitir',uncertain:'Söhbətin bitdiyi təsdiqlənmədi; mətn söhbəti əlçatandır.',playback:'Səsləndirmə xətası. ',microphone:'Mikrofonun söndürüldüyü təsdiqlənmədi. Mətn söhbəti əlçatandır.',connecting:'Qoşulur…',disconnecting:'Mikrofon söndürülür…',ended:'Söhbət bitdi',connectionEnded:'Bağlantı bitdi',responding:'AI cavab verir',listening:'Dinləyirəm',denied:'Mikrofona giriş yoxdur. Brauzer ayarlarında mikrofona icazə verin.',busy:'Mikrofon başqa rejimdə istifadə olunur.',failed:'Səsli söhbəti başlatmaq olmadı. Bağlantını və mikрофон icazəsini yoxlayın.',ending:'Söhbət bitirilir…',muted:'Mikrofon söndürülüb'}};
  const labels=catalog[language]||catalog.ru;
  const document = root.ownerDocument;
  const micIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.93V21h2v-3.07A7 7 0 0 0 19 11h-2Z"/></svg>';
  const muteIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm6-3a6 6 0 0 1-.7 2.8l1.5 1.5A8 8 0 0 0 20 11h-2Zm-2 4.6A6 6 0 0 1 6 11H4a8 8 0 0 0 7 7.93V22h2v-3.07a8 8 0 0 0 2.6-.85l1.5 1.5 1.4-1.42L5.4 6.47 4 7.9l3.3 3.3A4.7 4.7 0 0 0 12 16a4.6 4.6 0 0 0 2.6-.8l1.4 1.4Z"/></svg>';
  const endIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m18.3 5.7-1.4-1.4L12 9.2 7.1 4.3 5.7 5.7 10.6 10.6l-4.9 4.9 1.4 1.4 4.9-4.9 4.9 4.9 1.4-1.4-4.9-4.9 4.9-4.9Z"/></svg>';
  const status = document.createElement('p'); status.className='voice-session-status';status.setAttribute('role','status'); status.textContent=labels.off;
  const buttons=document.createElement('div');buttons.className='tenant-voice-buttons';
  const start = document.createElement('button'); start.type='button';start.className='voice-session-start';start.setAttribute('aria-label',labels.start);start.title=labels.start;start.innerHTML=micIcon;
  const mute = document.createElement('button'); mute.type='button';mute.className='voice-session-mute';mute.setAttribute('aria-label',labels.mute);mute.title=labels.mute;mute.innerHTML=muteIcon;mute.hidden=true;
  const end = document.createElement('button'); end.type='button';end.className='voice-session-end';end.setAttribute('aria-label',labels.end);end.title=labels.end;end.innerHTML=endIcon;end.hidden=true;
  buttons.append(start,mute,end);
  root.append(status,buttons);
  let generation=0, release=null, audio=null, opening=null, channel=null, muted=false, running=false, destroyed=false, ending=false, endTimer=null, endPromise=null, resolveEnd=null, endSent=false, inputEnabled=false, greetingPending=false;
  let serverInitiated=false, voiceBlocked=false, cleanupPending=0, hideAfterConnect=false, playbackStartedItems=new Set();
  const uncertain=labels.uncertain;
  const playbackFailure=labels.playback+uncertain;
  const microphoneUncertain=labels.microphone;
  const setVoiceState=state=>{try{onState(state);}catch(_){}};
  function settleEnd(confirmed){if(resolveEnd){const resolve=resolveEnd;resolveEnd=null;endPromise=null;resolve(confirmed);}}
  function idle(){if(!running && !ending && cleanupPending===0){try{onIdle();}catch(_){}}}
  function send(event){
    if(!channel)return;
    const version=generation;
    try { Promise.resolve(channel.send(event)).catch(()=>{if(version===generation)void stop(uncertain);}); }
    catch(_){if(version===generation)void stop(uncertain);}
  }

  async function stop(reason='ended',confirmed=false,retainMicrophone=false) {
    setVoiceState('stopping');
    if(serverInitiated && !confirmed && reason!==playbackFailure)reason=uncertain;
    if(reason===uncertain || reason===playbackFailure || reason===microphoneUncertain)voiceBlocked=true;
    serverInitiated=false;
    hideAfterConnect=false;
    const stopVersion=++generation;
    settleEnd(confirmed);
    running=false; ending=false; inputEnabled=false;greetingPending=false;cleanupPending++;start.hidden=false;start.disabled=true; mute.hidden=true; end.hidden=true;
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
        status.textContent=reason==='ended'?labels.ended:reason;
      idle();
    }).catch(()=>{
      cleanupPending--;
      voiceBlocked=true;start.disabled=true;
      if(!destroyed)status.textContent=microphoneUncertain;
      idle();
    });
    muted=false;endSent=false;mute.setAttribute('aria-pressed','false');mute.setAttribute('aria-label',labels.mute);mute.title=labels.mute;mute.innerHTML=muteIcon;
    if(!destroyed) status.textContent=voiceBlocked?reason:labels.disconnecting;
    await Promise.race([Promise.all([channelStop,mediaStop]),new Promise(resolve=>setTimeout(resolve,500))]);
  }
  async function begin() {
    if(running || destroyed || voiceBlocked || cleanupPending>0)return;
    running=true;setVoiceState('connecting');start.hidden=true;start.disabled=true;end.hidden=false;status.textContent=labels.connecting;
    const version=++generation;
    playbackStartedItems=new Set();
    try {
      const held=await acquireMicrophone();
      if(version!==generation){if(held)held();return;}
      if(typeof held!=='function')throw new Error('microphone_busy');
      release=held;
      const currentOpening=opening=Promise.resolve(openAudio(bytes=>{if(channel && inputEnabled && !muted)send({type:'audio',pcm:bytes});}));
      const engine=await currentOpening;
      if(opening===currentOpening)opening=null;
      if(version!==generation){await engine.close();return;}
      audio=engine;
      serverInitiated=true;
      const socket=await connect(async event=>{
        if(version!==generation)return;
        if(ending && ['audio','audio_done','interrupt'].includes(event.type))return;
        try {
          if(event.type==='audio'){
            try{
              audio.play(event.item_id,event.pcm);
              if(!playbackStartedItems.has(event.item_id)){
                playbackStartedItems.add(event.item_id);
                const contextState=typeof audio.contextState==='function'?audio.contextState():'unavailable';
                if(['running','suspended'].includes(contextState))send({type:'playback_scheduled',item_id:event.item_id,context_state:contextState});
              }
              if(!greetingPending)inputEnabled=true;setVoiceState('responding');status.textContent=labels.responding;
            }catch(error){
              try{channel?.send({type:'playback_failed',item_id:event.item_id});}catch(_){}
              throw error;
            }
          }
          else if(event.type==='audio_done'){
            Promise.resolve(audio.finished(event.item_id)).then(done=>{
              if(done && version===generation && !ending)send({type:'played_all',item_id:event.item_id});
            }).catch(()=>{if(version===generation && !ending)void stop(playbackFailure);});
          }
          else if(event.type==='interrupt'){
            const ms=audio.interrupt(event.item_id);
            send({type:'played',item_id:event.item_id,played_ms:ms});
            setVoiceState('listening');status.textContent=labels.listening;
          } else if(event.type==='ready'){
            greetingPending=event.greeting_pending===true;
            inputEnabled=!greetingPending;
            setVoiceState(greetingPending?'responding':'listening');
            status.textContent=inputEnabled?labels.listening:labels.responding;
          } else if(event.type==='thinking'){
            // Keep microphone uplink active while the model is preparing its
            // response. Realtime VAD needs the user's speech to interrupt a
            // slow answer before its first audio chunk arrives.
            inputEnabled=!muted && !ending;setVoiceState(muted?'muted':'responding');status.textContent=labels.responding;
          } else if(event.type==='listening'){
            greetingPending=false;inputEnabled=true;setVoiceState('listening');status.textContent=labels.listening;
          }
          else if(event.type==='ended')await stop(labels.connectionEnded,true);
          else if(event.type==='end_unconfirmed')await stop(uncertain);
        } catch (_) { await stop(playbackFailure); }
      });
      if(version!==generation){await socket.close();return;}
      channel=socket;mute.hidden=false;status.textContent=greetingPending?labels.responding:labels.listening;
      if(hideAfterConnect||lifecycle?.visibilityState==='hidden'){
        hideAfterConnect=false;
        finish();
      }
    } catch(error) {
      if(version!==generation)return;
      const code=error?.message==='microphone_stop_unconfirmed'?microphoneUncertain:
        error?.name==='NotAllowedError'?labels.denied:
        error?.message==='microphone_busy'?labels.busy:
        labels.failed;
      await stop(code,false,error?.message==='microphone_stop_unconfirmed');
    }
  }
  start.addEventListener('click',begin);
  function finish(){
    if(!channel){
      if(serverInitiated){hideAfterConnect=true;ending=true;end.disabled=true;status.textContent=labels.ending;if(!endPromise)endPromise=new Promise(resolve=>{resolveEnd=resolve;});return endPromise;}
      void stop(labels.failed,true);return Promise.resolve(false);
    }
    if(endSent)return endPromise||Promise.resolve(false);
    if(!endPromise)endPromise=new Promise(resolve=>{resolveEnd=resolve;});
    endSent=true;
    try{audio?.flush?.();audio?.stopNow?.();}catch(_){}
    muted=true;ending=true;setVoiceState('stopping');end.disabled=true;status.textContent=labels.ending;
    const endingVersion=generation;
    send({type:'end'});endTimer=setTimeout(()=>{if(ending && endingVersion===generation)void stop(uncertain);},2000);
    return endPromise;
  }
  end.addEventListener('click',finish);
  mute.addEventListener('click',()=>{
    if(!audio||!channel)return;
    muted=!muted;audio.mute(muted);send({type:'mute',muted});
    setVoiceState(muted?'muted':(status.textContent===labels.responding?'responding':'listening'));
    mute.setAttribute('aria-pressed',String(muted));mute.setAttribute('aria-label',muted?labels.unmute:labels.mute);mute.title=muted?labels.unmute:labels.mute;mute.innerHTML=muted?micIcon:muteIcon;
    status.textContent=muted?labels.muted:labels.listening;
  });
  const hide=()=>{
    if(lifecycle?.visibilityState!=='hidden'||(!running&&!ending))return;
    if(ending)return;
    if(!serverInitiated){
      // Before the WebSocket attempt, hiding the page only interrupts local
      // microphone startup; no server session exists to end.
      void stop(labels.failed,true);
      return;
    }
    if(!channel){
      // Let the handshake resolve. If it reaches ready, end it cleanly as soon
      // as the channel is available; aborting here would create a disconnect.
      hideAfterConnect=true;
      return;
    }
    finish();
  };
  lifecycle?.addEventListener?.('visibilitychange',hide);
  return {start:begin,stop,end:finish,isActive:()=>running||ending||cleanupPending>0,
          destroy:async()=>{destroyed=true;lifecycle?.removeEventListener?.('visibilitychange',hide);await stop();root.replaceChildren();}};
});
