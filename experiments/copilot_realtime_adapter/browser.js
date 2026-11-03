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
  let generation=0, release=null, audio=null, opening=null, channel=null, muted=false, running=false, destroyed=false;
  function send(event){
    if(!channel)return;
    try { Promise.resolve(channel.send(event)).catch(()=>stop('Соединение потеряно')); }
    catch(_){void stop('Соединение потеряно');}
  }

  async function stop(reason='ended') {
    generation++; running=false; start.disabled=false; mute.hidden=true; end.hidden=true;
    const oldChannel=channel, oldAudio=audio, pendingAudio=opening, oldRelease=release;
    channel=null;audio=null;opening=null;release=null;
    try { if(oldAudio) await oldAudio.close(); } catch (_) {}
    try { if(pendingAudio){const pending=await pendingAudio;if(pending && pending!==oldAudio)await pending.close();} } catch (_) {}
    if(oldRelease) oldRelease();
    try { if(oldChannel) await oldChannel.close(); } catch (_) {}
    muted=false;mute.textContent='Выключить микрофон';
    if(!destroyed) status.textContent=reason==='ended'?'Разговор завершён':reason;
  }
  async function begin() {
    if(running || destroyed)return;
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
      const socket=await connect(async event=>{
        if(version!==generation)return;
        try {
          if(event.type==='audio'){audio.play(event.item_id,event.pcm);status.textContent='AI отвечает';}
          else if(event.type==='interrupt'){
            const ms=audio.interrupt(event.item_id);
            send({type:'played',item_id:event.item_id,played_ms:ms});
            status.textContent='Слушаю';
          } else if(event.type==='ready')status.textContent='Слушаю';
          else if(event.type==='ended')await stop('Соединение завершено');
        } catch (_) { await stop('Ошибка воспроизведения'); }
      });
      if(version!==generation){await socket.close();return;}
      channel=socket;mute.hidden=false;status.textContent='Слушаю';
    } catch(error) {
      if(version!==generation)return;
      const code=error?.name==='NotAllowedError'?'Нет доступа к микрофону. Разрешите микрофон в настройках браузера.':
        error?.message==='microphone_busy'?'Микрофон уже используется в другом режиме.':
        'Не удалось начать голосовой разговор. Проверьте подключение и доступ к микрофону.';
      await stop(code);
    }
  }
  start.addEventListener('click',begin);
  end.addEventListener('click',()=>stop());
  mute.addEventListener('click',()=>{
    if(!audio||!channel)return;
    muted=!muted;audio.mute(muted);send({type:'mute',muted});
    mute.textContent=muted?'Включить микрофон':'Выключить микрофон';
    status.textContent=muted?'Микрофон выключен':'Слушаю';
  });
  const hide=()=>{if(lifecycle?.visibilityState==='hidden')void stop('Разговор завершён');};
  lifecycle?.addEventListener?.('visibilitychange',hide);
  return {start:begin,stop,destroy:async()=>{destroyed=true;lifecycle?.removeEventListener?.('visibilitychange',hide);await stop();root.replaceChildren();}};
});
