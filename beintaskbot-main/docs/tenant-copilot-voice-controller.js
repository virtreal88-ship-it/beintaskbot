/* Voice controls; callers must complete server preflight before mounting. */
(function(root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory;
  else root.mountExperimentalCopilotVoice = factory;
})(typeof window === 'object' ? window : globalThis, function mountVoice(root, {acquireMicrophone, openAudio, connect, lifecycle = globalThis.document, interruptTarget: providedInterruptTarget=null, language='ru', duplex=false, onIdle=()=>{}, onState=()=>{}, onDismiss=()=>{}}) {
  if (typeof acquireMicrophone !== 'function' || typeof openAudio !== 'function' || typeof connect !== 'function')
    throw new Error('voice_dependencies_missing');
  const catalog={ru:{off:'Голосовой разговор не начат',start:'Начать голосовой разговор',talkStart:'Нажмите, чтобы говорить',talkInterrupt:'Нажмите, чтобы перебить и говорить',talkSend:'Нажмите, чтобы отправить',talking:'Говорите…',sending:'Отправляю вопрос…',empty:'Запись пустая. Нажмите, чтобы попробовать ещё раз.',end:'Завершить сессию',uncertain:'Завершение разговора не подтверждено; текстовый чат доступен.',playback:'Ошибка воспроизведения. ',inputQueueSlow:'Сервер слишком долго подтверждает перебивание. Повторите реплику.',inputRetry:'Эта реплика не отправлена; нажмите микрофон и повторите.',microphone:'Не удалось подтвердить отключение микрофона. Текстовый чат доступен.',connecting:'Подключение…',disconnecting:'Завершаю разговор…',ended:'Разговор завершён',connectionEnded:'Соединение завершено',responding:'AI отвечает',listening:'Говорите. После паузы в 1 секунду AI ответит',denied:'Нет доступа к микрофону. Разрешите микрофон в настройках браузера.',busy:'Микрофон уже используется в другом режиме.',failed:'Не удалось начать голосовой разговор. Проверьте подключение и доступ к микрофону.',ending:'Завершение разговора…',muted:'Микрофон выключен'},
    az:{off:'Səsli söhbət başlamayıb',start:'Səsli söhbətə başla',talkStart:'Danışmaq üçün toxunun',talkInterrupt:'Söhbəti kəsib danışmaq üçün toxunun',talkSend:'Göndərmək üçün toxunun',talking:'Danışın…',sending:'Sual göndərilir…',empty:'Yazı boşdur. Yenidən cəhd etmək üçün toxunun.',end:'Sessiyanı bitir',uncertain:'Söhbətin bitdiyi təsdiqlənmədi; mətn söhbəti əlçatandır.',playback:'Səsləndirmə xətası. ',inputQueueSlow:'Kəsmə təsdiqi çox gecikir. Replikanı təkrar edin.',inputRetry:'Bu replika göndərilmədi; yenidən danışmaq üçün mikrofona toxunun.',microphone:'Mikrofonun söndürüldüyü təsdiqlənmədi. Mətn söhbəti əlçatandır.',connecting:'Qoşulur…',disconnecting:'Söhbət bitirilir…',ended:'Söhbət bitdi',connectionEnded:'Bağlantı bitdi',responding:'AI cavab verir',listening:'Danışın. 1 saniyəlik fasilədən sonra AI cavab verəcək',denied:'Mikrofona giriş yoxdur. Brauzer ayarlarında mikrofona icazə verin.',busy:'Mikrofon başqa rejimdə istifadə olunur.',failed:'Səsli söhbəti başlatmaq olmadı. Bağlantını və mikrofon icazəsini yoxlayın.',ending:'Söhbət bitirilir…',muted:'Mikrofon söndürülüb'}};
  const labels=catalog[language]||catalog.ru;
  const listeningLabel=duplex?(language==='az'?'Danışın, AI sizi dinləyir':'Говорите, AI слушает'):labels.listening;
  const respondingLabel=duplex?(language==='az'?'AI cavab verir, mikrofon aktivdir':'AI отвечает, микрофон включён'):labels.responding;
  const sessionBusy=language==='az'?'Başqa səsli sessiya aktiv ola bilər və ya əvvəlki sessiyanın bitməsi təsdiqlənməyib. Digər açıq pəncərələri yoxlayın.':'Возможно, другой голосовой сеанс ещё открыт или завершение предыдущего не подтверждено. Проверьте другие открытые окна.';
  const document = root.ownerDocument;
  const micIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.93V21h2v-3.07A7 7 0 0 0 19 11h-2Z"/></svg>';
  const endIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m18.3 5.7-1.4-1.4L12 9.2 7.1 4.3 5.7 5.7 10.6 10.6l-4.9 4.9 1.4 1.4 4.9-4.9 4.9 4.9 1.4-1.4-4.9-4.9 4.9-4.9Z"/></svg>';
  const status = document.createElement('p'); status.className='voice-session-status';status.setAttribute('role','status'); status.textContent=labels.off;
  const buttons=document.createElement('div');buttons.className='tenant-voice-buttons';
  const start = document.createElement('button'); start.type='button';start.className='voice-session-start';start.setAttribute('aria-label',labels.start);start.title=labels.start;start.innerHTML=micIcon;
  const end = document.createElement('button'); end.type='button';end.className='voice-session-end';end.setAttribute('aria-label',labels.end);end.title=labels.end;end.innerHTML=endIcon;end.hidden=true;
  buttons.append(start,end);
  root.append(status,buttons);
  const interruptTarget=providedInterruptTarget||root.ownerDocument?.querySelector?.('[data-voice-orb]')||null;
  const interruptLabel=language==='az'?'Cavabı kəs və danış':'Перебить ответ и говорить';
  const orbLabel=language==='az'?'AI səsli söhbəti dinləyir':'Анимация голосового разговора с AI';
  let generation=0, release=null, audio=null, opening=null, channel=null, muted=false, running=false, destroyed=false, ending=false, endTimer=null, endPromise=null, resolveEnd=null, endSent=false, endStatusReason=null, inputEnabled=false, greetingPending=false, talkActive=false, talkPending=false, assistantResponding=false, currentAssistantItem=null;
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

  let speechSamples=0, silenceSamples=0, turnSamples=0, candidate=[], candidateSamples=0, stopAwaiting=false;
  // Allow headroom for the 6s audio boundary and server cleanup. A delayed
  // transport can exceed this window: the deadline never implies success.
  const END_CONFIRM_WAIT_MS=8000;
  const SAMPLE_RATE=24000, END_SILENCE_MS=1000, MIN_END_SILENCE_MS=750, MAX_END_SILENCE_MS=2000;
  const END_SILENCE_SAMPLES=Math.round(SAMPLE_RATE*Math.max(MIN_END_SILENCE_MS,Math.min(MAX_END_SILENCE_MS,END_SILENCE_MS))/1000);
  const MAX_PENDING_MIC_BYTES=SAMPLE_RATE*2*2;
  let pendingMic=[],pendingMicBytes=0,pendingOverflow=false;
  let stoppedItems=new Set();
  const resetDetection=()=>{speechSamples=0;silenceSamples=0;turnSamples=0;candidate=[];candidateSamples=0;};
  const resetPendingMic=()=>{pendingMic=[];pendingMicBytes=0;pendingOverflow=false;};
  function setInterruptEnabled(enabled){
    if(!interruptTarget)return;
    interruptTarget.disabled=!enabled;
    interruptTarget.setAttribute('aria-label',enabled?interruptLabel:orbLabel);
    interruptTarget.title=enabled?interruptLabel:'';
  }
  function flushPendingMic(){
    if(pendingOverflow){resetPendingMic();void stop(labels.inputQueueSlow);return false;}
    const queued=pendingMic;resetPendingMic();
    for(const pcm of queued)send({type:'audio',pcm});
    if(silenceSamples>=END_SILENCE_SAMPLES){
      inputEnabled=false;talkActive=false;talkPending=true;muted=true;
      audio?.mute(true);resetDetection();send({type:'talk_end'});showResponse(false);
    }
    return true;
  }
  function capture(bytes){
    if(duplex){
      if(channel&&running&&!ending&&bytes instanceof Uint8Array&&bytes.byteLength&&bytes.byteLength%2===0)
        send({type:'audio',pcm:bytes});
      return;
    }
    const buffering=talkPending&&!ending;
    if(!channel||(!buffering&&(muted||!inputEnabled||!talkActive||assistantResponding))||ending)return;
    if(!(bytes instanceof Uint8Array)||!bytes.byteLength||bytes.byteLength%2)return;
    const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
    let energy=0;const samples=bytes.byteLength/2;
    for(let i=0;i<samples;i++){const v=view.getInt16(i*2,true)/32768;energy+=v*v;}
    const voiced=Math.sqrt(energy/samples)>=0.015;
    if(!speechSamples){
      if(!voiced){candidate=[];candidateSamples=0;return;}
      candidate.push(bytes.slice());candidateSamples+=samples;
      if(candidateSamples<2400)return;
      speechSamples=candidateSamples;turnSamples=candidateSamples;
      if(buffering){for(const pcm of candidate){pendingMic.push(pcm);pendingMicBytes+=pcm.byteLength;}}
      else for(const pcm of candidate)send({type:'audio',pcm});
      candidate=[];candidateSamples=0;return;
    }
    if(voiced){speechSamples+=samples;silenceSamples=0;}
    else if(speechSamples)silenceSamples+=samples;
    if(!speechSamples)return; // Do not upload idle room silence.
    turnSamples+=samples;
    if(buffering){
      if(!pendingOverflow){pendingMic.push(bytes.slice());pendingMicBytes+=bytes.byteLength;
        if(pendingMicBytes>MAX_PENDING_MIC_BYTES){pendingOverflow=true;pendingMic=[];pendingMicBytes=0;}}
    }else send({type:'audio',pcm:bytes});
    if(!buffering&&silenceSamples>=END_SILENCE_SAMPLES){
      inputEnabled=false;talkActive=false;talkPending=true;muted=true;
      audio?.mute(true);resetDetection();
      send({type:'talk_end'});showResponse(false);
    }
  }
  function showResponse(canStop=true){
    assistantResponding=true;inputEnabled=false;muted=!duplex;if(!duplex)audio?.mute(true);
    setInterruptEnabled(canStop);
    setVoiceState('responding');status.textContent=respondingLabel;
  }
  function listen(){
    if(duplex){
      if(!channel||ending)return;
      assistantResponding=false;muted=false;inputEnabled=true;talkPending=false;talkActive=false;
      audio?.mute(false);setInterruptEnabled(false);setVoiceState('listening');status.textContent=listeningLabel;
      return;
    }
    if(!channel||ending||talkPending||talkActive)return;
    resetDetection();resetPendingMic();muted=false;inputEnabled=false;assistantResponding=false;
    setInterruptEnabled(false);talkPending=true;audio?.mute(false);
    send({type:'talk_start'});
    setVoiceState('listening');status.textContent=labels.listening;
  }
  async function stop(reason='ended',confirmed=false,retainMicrophone=false) {
    setVoiceState('stopping');
    if(serverInitiated && !confirmed && reason!==playbackFailure)reason=uncertain;
    if(reason===uncertain || reason===playbackFailure || reason===microphoneUncertain)voiceBlocked=true;
    serverInitiated=false;
    hideAfterConnect=false;
    const stopVersion=++generation;
    settleEnd(confirmed);
    resetDetection();resetPendingMic();stopAwaiting=false;stoppedItems.clear();
    running=false; ending=false; inputEnabled=false;greetingPending=false;talkActive=false;talkPending=false;assistantResponding=false;currentAssistantItem=null;cleanupPending++;start.hidden=false;start.disabled=true;setInterruptEnabled(false);end.hidden=confirmed;
    if(endTimer){clearTimeout(endTimer);endTimer=null;} end.disabled=false;
    const oldChannel=channel, oldAudio=audio, pendingOpenAudio=opening, oldRelease=release;
    channel=null;audio=null;opening=null;release=null;
    // Stop the server and microphone independently; permission dialogs and
    // AudioContext.close may hang. Keep the shared lease until media is closed.
    const channelStop=Promise.resolve().then(()=>oldChannel?.close()).catch(()=>{});
    try { oldAudio?.stopNow?.(); } catch (_) {}
    const mediaStop=(async()=>{
      if(oldAudio)await oldAudio.close();
      if(pendingOpenAudio){let pending;try{pending=await pendingOpenAudio;}catch(error){
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
    muted=false;endSent=false;
    if(!destroyed) status.textContent=voiceBlocked?reason:labels.disconnecting;
    await Promise.race([Promise.all([channelStop,mediaStop]),new Promise(resolve=>setTimeout(resolve,500))]);
  }
  async function begin() {
    if(running || destroyed || voiceBlocked || cleanupPending>0)return;
    running=true;endStatusReason=null;setVoiceState('connecting');start.hidden=true;start.disabled=true;end.hidden=false;status.textContent=labels.connecting;
    const version=++generation;
    playbackStartedItems=new Set();stoppedItems=new Set();stopAwaiting=false;resetDetection();
    try {
      const held=await acquireMicrophone();
      if(version!==generation){if(held)held();return;}
      if(typeof held!=='function')throw new Error('microphone_busy');
      release=held;
      const currentOpening=opening=Promise.resolve(openAudio(capture));
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
            if(stopAwaiting||stoppedItems.has(event.item_id))return;
            if(talkPending||talkActive){try{audio.interrupt(event.item_id);}catch(_){}return;}
            try{
              audio.play(event.item_id,event.pcm);
              currentAssistantItem=event.item_id;assistantResponding=true;
              if(!playbackStartedItems.has(event.item_id)){
                playbackStartedItems.add(event.item_id);
                const contextState=typeof audio.contextState==='function'?audio.contextState():'unavailable';
                if(['running','suspended'].includes(contextState))send({type:'playback_scheduled',item_id:event.item_id,context_state:contextState});
              }
              showResponse();
            }catch(error){
              try{channel?.send({type:'playback_failed',item_id:event.item_id});}catch(_){}
              throw error;
            }
          }
          else if(event.type==='audio_done'){
            Promise.resolve(audio.finished(event.item_id)).then(done=>{
              if(done && version===generation && !ending && !stopAwaiting && !stoppedItems.has(event.item_id))send({type:'played_all',item_id:event.item_id});
            }).catch(()=>{if(version===generation && !ending)void stop(playbackFailure);});
          }
          else if(event.type==='interrupt'){
            if(duplex && event.item_id)stoppedItems.add(event.item_id);
            const ms=audio.interrupt(event.item_id);
            send({type:'played',item_id:event.item_id,played_ms:ms});
            assistantResponding=false;currentAssistantItem=null;
            setVoiceState('listening');status.textContent=listeningLabel;
          } else if(event.type==='ready'){
            greetingPending=event.greeting_pending===true;
            if(greetingPending)showResponse();else listen();
          } else if(event.type==='talk_ready'){
            stopAwaiting=false;
            talkPending=false;talkActive=true;assistantResponding=false;currentAssistantItem=null;
            inputEnabled=true;muted=false;audio?.mute(false);
            setInterruptEnabled(false);setVoiceState('listening');status.textContent=labels.listening;
            flushPendingMic();
          } else if(event.type==='talk_empty'){
            talkPending=false;talkActive=false;listen();
          } else if(event.type==='thinking'){
            if(stopAwaiting||talkActive)return;
            talkPending=false;talkActive=false;showResponse();
          } else if(event.type==='listening'){
            greetingPending=false;assistantResponding=false;currentAssistantItem=null;
            talkActive=false;talkPending=false;listen();
          }
          else if(event.type==='ended')await stop(endStatusReason||labels.connectionEnded,true);
          else if(event.type==='end_unconfirmed')await stop(uncertain);
        } catch (_) { await stop(playbackFailure); }
      });
      if(version!==generation){await socket.close();return;}
      channel=socket;
      if(greetingPending)showResponse();else listen();
      if(hideAfterConnect||lifecycle?.visibilityState==='hidden'){
        hideAfterConnect=false;
        finish();
      }
    } catch(error) {
      if(version!==generation)return;
      const busy=error?.message==='voice_session_busy';
      const dismissAfterRefusal=busy && hideAfterConnect;
      const code=busy?sessionBusy:error?.message==='microphone_stop_unconfirmed'?microphoneUncertain:
        error?.name==='NotAllowedError'?labels.denied:
        error?.message==='microphone_busy'?labels.busy:
        labels.failed;
      // A lease refusal is definitive for this attempt; it says nothing about
      // whether the previous session actually ended. Leave the local close
      // control available without touching that session or its lease.
      await stop(code,busy,error?.message==='microphone_stop_unconfirmed');
      if(busy && !destroyed)end.hidden=false;
      if(dismissAfterRefusal && !destroyed){try{onDismiss();}catch(_){}}
    }
  }
  start.addEventListener('click',begin);
  function finish(reason=labels.connectionEnded){
    if(!running && !ending){try{onDismiss();}catch(_){}return Promise.resolve(false);}
    endStatusReason=reason;
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
    send({type:'end'});endTimer=setTimeout(()=>{if(ending && endingVersion===generation)void stop(uncertain);},END_CONFIRM_WAIT_MS);
    return endPromise;
  }
  end.addEventListener('click',()=>finish());
  const interrupt=()=>{
    if(!audio||!channel||ending||!assistantResponding||stopAwaiting||talkPending)return;
    const event={type:'stop_response'};
    try{
      const interrupted=audio.interruptForTalk?.();
      if(currentAssistantItem && interrupted?.item_id===currentAssistantItem){event.item_id=interrupted.item_id;event.audio_end_ms=interrupted.audio_end_ms;}
      else if(currentAssistantItem){event.item_id=currentAssistantItem;event.audio_end_ms=0;}
    }catch(_){void stop(playbackFailure);return;}
    if(event.item_id)stoppedItems.add(event.item_id);stopAwaiting=!duplex;
    assistantResponding=false;currentAssistantItem=null;talkPending=!duplex;inputEnabled=duplex;muted=false;
    setInterruptEnabled(false);resetDetection();resetPendingMic();audio?.mute(false);
    send(event);setVoiceState('listening');status.textContent=listeningLabel;
  };
  interruptTarget?.addEventListener?.('click',interrupt);
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
  return {start:begin,stop,end:finish,interrupt,isActive:()=>running||ending||cleanupPending>0,
          destroy:async()=>{destroyed=true;lifecycle?.removeEventListener?.('visibilitychange',hide);interruptTarget?.removeEventListener?.('click',interrupt);await stop();root.replaceChildren();}};
});
