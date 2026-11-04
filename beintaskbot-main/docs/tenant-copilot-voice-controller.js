/* Voice controls; callers must complete server preflight before mounting. */
(function(root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory;
  else root.mountExperimentalCopilotVoice = factory;
})(typeof window === 'object' ? window : globalThis, function mountVoice(root, {acquireMicrophone, openAudio, connect, lifecycle = globalThis.document, language='ru', onIdle=()=>{}}) {
  if (typeof acquireMicrophone !== 'function' || typeof openAudio !== 'function' || typeof connect !== 'function')
    throw new Error('voice_dependencies_missing');
  const catalog={ru:{off:'Голосовой разговор не начат',start:'Начать голосовой разговор',mute:'Выключить микрофон',unmute:'Включить микрофон',end:'Завершить разговор',uncertain:'Завершение разговора не подтверждено; текстовый чат доступен.',playback:'Ошибка воспроизведения. ',microphone:'Не удалось подтвердить отключение микрофона. Текстовый чат доступен.',connecting:'Подключение…',disconnecting:'Отключаем микрофон…',ended:'Разговор завершён',connectionEnded:'Соединение завершено',responding:'AI отвечает',listening:'Слушаю',denied:'Нет доступа к микрофону. Разрешите микрофон в настройках браузера.',busy:'Микрофон уже используется в другом режиме.',failed:'Не удалось начать голосовой разговор. Проверьте подключение и доступ к микрофону.',ending:'Завершение разговора…',muted:'Микрофон выключен'},
    az:{off:'Səsli söhbət başlamayıb',start:'Səsli söhbətə başla',mute:'Mikrofonu söndür',unmute:'Mikrofonu aç',end:'Söhbəti bitir',uncertain:'Söhbətin bitdiyi təsdiqlənmədi; mətn söhbəti əlçatandır.',playback:'Səsləndirmə xətası. ',microphone:'Mikrofonun söndürüldüyü təsdiqlənmədi. Mətn söhbəti əlçatandır.',connecting:'Qoşulur…',disconnecting:'Mikrofon söndürülür…',ended:'Söhbət bitdi',connectionEnded:'Bağlantı bitdi',responding:'AI cavab verir',listening:'Dinləyirəm',denied:'Mikrofona giriş yoxdur. Brauzer ayarlarında mikrofona icazə verin.',busy:'Mikrofon başqa rejimdə istifadə olunur.',failed:'Səsli söhbəti başlatmaq olmadı. Bağlantını və mikрофон icazəsini yoxlayın.',ending:'Söhbət bitirilir…',muted:'Mikrofon söndürülüb'}};
  const labels=catalog[language]||catalog.ru;
  const document = root.ownerDocument;
  const status = document.createElement('p'); status.setAttribute('role','status'); status.textContent=labels.off;
  const start = document.createElement('button'); start.type='button';start.textContent=labels.start;
  const mute = document.createElement('button'); mute.type='button';mute.textContent=labels.mute;mute.hidden=true;
  const end = document.createElement('button'); end.type='button';end.textContent=labels.end;end.hidden=true;
  root.append(status,start,mute,end);
  let generation=0, release=null, audio=null, opening=null, channel=null, muted=false, running=false, destroyed=false, ending=false, endTimer=null;
  let serverInitiated=false, voiceBlocked=false, cleanupPending=0, hideAfterConnect=false;
  const uncertain=labels.uncertain;
  const playbackFailure=labels.playback+uncertain;
  const microphoneUncertain=labels.microphone;
  function idle(){if(!running && !ending && cleanupPending===0){try{onIdle();}catch(_){}}}
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
    hideAfterConnect=false;
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
        status.textContent=reason==='ended'?labels.ended:reason;
      idle();
    }).catch(()=>{
      cleanupPending--;
      voiceBlocked=true;start.disabled=true;
      if(!destroyed)status.textContent=microphoneUncertain;
      idle();
    });
    muted=false;mute.textContent=labels.mute;
    if(!destroyed) status.textContent=voiceBlocked?reason:labels.disconnecting;
    await Promise.race([Promise.all([channelStop,mediaStop]),new Promise(resolve=>setTimeout(resolve,500))]);
  }
  async function begin() {
    if(running || destroyed || voiceBlocked || cleanupPending>0)return;
    running=true;start.disabled=true;end.hidden=false;status.textContent=labels.connecting;
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
          if(event.type==='audio'){audio.play(event.item_id,event.pcm);status.textContent=labels.responding;}
          else if(event.type==='audio_done'){
            Promise.resolve(audio.finished(event.item_id)).then(done=>{
              if(done && version===generation && !ending)send({type:'played_all',item_id:event.item_id});
            }).catch(()=>{if(version===generation && !ending)void stop(playbackFailure);});
          }
          else if(event.type==='interrupt'){
            const ms=audio.interrupt(event.item_id);
            send({type:'played',item_id:event.item_id,played_ms:ms});
            status.textContent=labels.listening;
          } else if(event.type==='ready')status.textContent=labels.listening;
          else if(event.type==='ended')await stop(labels.connectionEnded,true);
          else if(event.type==='end_unconfirmed')await stop(uncertain);
        } catch (_) { await stop(playbackFailure); }
      });
      if(version!==generation){await socket.close();return;}
      channel=socket;mute.hidden=false;status.textContent=labels.listening;
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
      if(serverInitiated){hideAfterConnect=true;ending=true;end.disabled=true;status.textContent=labels.ending;return;}
      void stop(labels.failed,true);return;
    }
    try{audio?.stopNow?.();}catch(_){}
    muted=true;ending=true;end.disabled=true;status.textContent=labels.ending;
    const endingVersion=generation;
    send({type:'end'});endTimer=setTimeout(()=>{if(ending && endingVersion===generation)void stop(uncertain);},2000);
  }
  end.addEventListener('click',finish);
  mute.addEventListener('click',()=>{
    if(!audio||!channel)return;
    muted=!muted;audio.mute(muted);send({type:'mute',muted});
    mute.textContent=muted?labels.unmute:labels.mute;
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
