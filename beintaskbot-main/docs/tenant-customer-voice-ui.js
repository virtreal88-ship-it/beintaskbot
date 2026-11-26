/* Explicit customer recording and one-shot delivery, scoped to the open dialog. */
(()=>{
  if (!window.createTenantVoiceRecorder || !window.createTenantVoiceStore || !window.acquireTenantMicrophone) return;
  const store=window.createTenantVoiceStore();
  const words={
    ru:{mic:'Записать голосовое',stop:'Остановить и прослушать',cancel:'Отменить',discard:'Удалить запись',retrySave:'Сохранить запись снова',send:'Отправить голосовое',check:'Проверить статус',manualSeen:'Нашёл в WhatsApp',manualNotSeen:'Не вижу в WhatsApp',manualUnsure:'Не уверен',manualSaved:'Ручная проверка сохранена отдельно от статуса провайдера.',manualValue_seen:'Вы отметили: сообщение найдено в WhatsApp.',manualValue_not_seen:'Вы отметили: сообщение не найдено.',manualValue_unsure:'Вы отметили: результат ручной проверки неясен.',
      requesting:'Ожидаем разрешение на микрофон…',recording:'Запись',paused:'Запись на паузе',pause:'Пауза',resume:'Продолжить запись',saving:'Сохраняем запись на устройстве…',
      sending:'Отправляем…',unknown:'Результат неизвестен. Проверьте чат WhatsApp вручную и не отправляйте повторно.',
      failed:'Провайдер сообщил об ошибке доставки. Проверьте историю.',
      denied:'Доступ к микрофону запрещён. Разрешите микрофон в настройках сайта браузера и попробуйте снова.',
      unavailable:'Микрофон недоступен. Проверьте устройство и разрешения браузера.',unsupported:'Этот браузер не поддерживает безопасную запись. Попробуйте обновить браузер.',
      busy:'Микрофон уже используется в другой вкладке или голосовом режиме.',storage:'Не удалось сохранить запись на устройстве. Отправка недоступна.',limit:'Превышен размер записи. Запишите более короткое сообщение.',error:'Не удалось записать или проверить голосовое сообщение.',
      conflict:'В этом диалоге уже есть другая сохранённая запись. Откройте её перед продолжением.',disabled:'Отправка голосовых временно отключена.',
      groupAccess:'Права на отправку в этой группе изменились. Запись сохранена.',
      native_group_session_unavailable:'Выбранный номер WhatsApp отключён. Запись сохранена.',
      native_group_membership_unconfirmed:'Не удалось подтвердить участие выбранного номера в группе. Запись сохранена.',
      native_group_check_unavailable:'Проверка членства сейчас недоступна. Запись сохранена.',
      voice_manual_mode_unavailable:'Отправка не выполнена: безопасный режим отправки недоступен. Запись сохранена.',
      voice_attribution_unresolved:'Отправка не выполнена: в этом диалоге ещё не разрешён статус предыдущего голосового сообщения. Запись сохранена.'},
    az:{mic:'Səs yaz',stop:'Dayandır və dinlə',cancel:'Ləğv et',discard:'Yazını sil',retrySave:'Yazını yenidən saxla',send:'Səsi göndər',check:'Vəziyyəti yoxla',manualSeen:'WhatsApp-da tapdım',manualNotSeen:'WhatsApp-da görünmür',manualUnsure:'Əmin deyiləm',manualSaved:'Əl ilə yoxlama provayder statusundan ayrı saxlanıldı.',manualValue_seen:'Qeyd etdiniz: mesaj WhatsApp-da tapıldı.',manualValue_not_seen:'Qeyd etdiniz: mesaj tapılmadı.',manualValue_unsure:'Qeyd etdiniz: əl ilə yoxlamanın nəticəsi aydın deyil.',
      requesting:'Mikrofon icazəsi gözlənilir…',recording:'Yazılır',paused:'Yazı dayandırılıb',pause:'Fasilə ver',resume:'Yazıya davam et',saving:'Yazı cihazda saxlanılır…',
      sending:'Göndərilir…',unknown:'Nəticə məlum deyil. WhatsApp çatını əl ilə yoxlayın və mesajı təkrar göndərməyin.',
      failed:'Provayder çatdırılma xətası bildirdi. Tarixçəni yoxlayın.',
      denied:'Mikrofon icazəsi verilməyib. Brauzerdə sayt üçün mikrofon icazəsini açıb yenidən yoxlayın.',
      unavailable:'Mikrofon əlçatan deyil. Cihazı və brauzer icazələrini yoxlayın.',unsupported:'Bu brauzer təhlükəsiz səs yazısını dəstəkləmir. Brauzeri yeniləyin.',
      busy:'Mikrofon başqa vərəqdə və ya səs rejimində istifadə olunur.',storage:'Yazını cihazda saxlamaq mümkün olmadı. Göndərmə əlçatan deyil.',limit:'Yazının ölçüsü böyükdür. Daha qısa səs yazın.',error:'Səsi yazmaq və ya vəziyyətini yoxlamaq mümkün olmadı.',
      conflict:'Bu dialoq üçün başqa saxlanmış yazı var. Davam etməzdən əvvəl onu açın.',disabled:'Səs göndərilməsi müvəqqəti bağlıdır.',
      groupAccess:'Bu qrupa göndərmə icazəsi dəyişib. Yazı saxlanılıb.',
      native_group_session_unavailable:'Seçilmiş WhatsApp nömrəsi bağlı deyil. Yazı saxlanılıb.',
      native_group_membership_unconfirmed:'Seçilmiş nömrənin qrupda iştirakı təsdiqlənmədi. Yazı saxlanılıb.',
      native_group_check_unavailable:'Qrup üzvlüyünü hazırda yoxlamaq olmur. Yazı saxlanılıb.',
      voice_manual_mode_unavailable:'Göndərilmədi: təhlükəsiz göndərmə rejimi əlçatan deyil. Yazı saxlanılıb.',
      voice_attribution_unresolved:'Göndərilmədi: bu dialoqda əvvəlki səs mesajının vəziyyəti hələ aydın deyil. Yazı saxlanılıb.'},
    en:{mic:'Record voice message',stop:'Stop and preview',cancel:'Cancel',discard:'Delete recording',retrySave:'Try saving again',send:'Send voice message',check:'Check status',manualSeen:'I found it in WhatsApp',manualNotSeen:'I do not see it',manualUnsure:'Not sure',manualSaved:'Your manual check is stored separately from provider status.',manualValue_seen:'You marked the message as found in WhatsApp.',manualValue_not_seen:'You marked the message as not found.',manualValue_unsure:'You marked the manual check as inconclusive.',
      requesting:'Waiting for microphone permission…',recording:'Recording',paused:'Recording paused',pause:'Pause recording',resume:'Resume recording',saving:'Saving recording on this device…',
      sending:'Sending…',unknown:'The outcome is unknown. Check the WhatsApp chat manually; do not resend.',
      failed:'The provider reported a delivery failure. Check the history.',
      denied:'Microphone access is blocked. Allow the microphone in your browser’s site settings and try again.',
      unavailable:'Microphone unavailable. Check the device and browser permissions.',unsupported:'This browser cannot record safely. Try updating your browser.',
      busy:'The microphone is already in use in another tab or voice mode.',storage:'The recording could not be saved on this device. Sending is unavailable.',limit:'The recording is too large. Record a shorter message.',error:'Could not record or check the voice message.',
      conflict:'Another recording is saved for this dialog. Open it before continuing.',disabled:'Voice sending is temporarily unavailable.',
      groupAccess:'Your permission to send to this group has changed. The recording is saved.',
      native_group_session_unavailable:'The selected WhatsApp number is disconnected. The recording is saved.',
      native_group_membership_unconfirmed:'Membership of the selected number in this group could not be verified. The recording is saved.',
      native_group_check_unavailable:'Group membership cannot be checked right now. The recording is saved.',
      voice_manual_mode_unavailable:'Not sent: the safe sending mode is unavailable. The recording is saved.',
      voice_attribution_unresolved:'Not sent: the previous voice message in this dialog still has an unresolved status. The recording is saved.'}
  };
  const lang=()=>words[window.tenantI18n?.language]||words.en;
  const text=key=>lang()[key]||lang().error;
  // Successful delivery states are represented by the message's existing checkmarks.
  const acceptedNotice=state=>state==='failed'?text('failed'):'';
  const operator=()=>window.tenantOperator;
  function groupRefusal(response,result){
    if(response.status===403)return text('groupAccess');
    if(['native_group_session_unavailable','native_group_membership_unconfirmed','native_group_check_unavailable'].includes(result.error_code))return text(result.error_code);
    return '';
  }
  function scope(){
    const member=operator()?.member, route=operator()?.currentDialog;
    if(!member || !member.tenant_id || !member.telegram_id || !route || route.provider!=='connect' || !route.lead_id || !route.channel_id || !route.conversation_id) return null;
    const key=JSON.stringify([String(member.tenant_id),String(member.telegram_id),String(route.lead_id),String(route.channel_id),String(route.conversation_id)]);
    const policy=JSON.stringify([member.role,member.active,member.permissions,window.tenantProfileSnapshot?.capabilities]);
    const conversation=String(route.conversation_id), channel=String(route.channel_id);
    return {key,policy,tenant:String(member.tenant_id),user:String(member.telegram_id),lead:String(route.lead_id),
      group:conversation.endsWith('@g.us'),channel,conversation};
  }
  let current=null, revision=0, activeStatusChecks=0, lastUser=null;
  const statusChecks=new Map();
  function same(session){const active=scope();return current===session && active?.key===session.scope.key && active?.policy===session.scope.policy && session.host?.isConnected;}
  function release(session){if(session.release){session.release();session.release=null;}}
  const isCapturing=state=>state==='recording'||state==='paused';
  const buttonsChanged=()=>document.dispatchEvent(new Event('tenant-composer-voice'));
  function duration(value){const seconds=Math.max(0,Math.floor((value||0)/1000));return Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0');}
  function draw(session){
    if(!same(session))return;
    const snap=session.recorder.snapshot(), entry=session.entry, receipt=session.receipt;
    const capturing=isCapturing(snap.state), recording=capturing||['requesting','stopping'].includes(snap.state);
    const unstored=snap.state==='ready'&&!entry;
    const editor=recording||unstored||!!entry, editorChanged=session.host.dataset.voiceEditor!==String(editor);
    const recovering=!editor&&receipt?.state==='unknown';
    const busy=session.busy||session.saving;
    session.host.dataset.voiceEditor=String(editor);
    session.host.dataset.voiceBusy=String(editor||session.saving);
    session.mic.hidden=!session.maySend||editor||session.saving;
    session.stop.hidden=!capturing;session.cancel.hidden=!recording;
    session.pause.hidden=!capturing||!snap.canPause;
    if(capturing&&snap.canPause)setIcon(session.pause,snap.state==='paused'?'resume':'pause',snap.state==='paused'?'play':'pause');
    session.discard.hidden=entry?.state!=='draft';
    session.send.hidden=!session.maySend||!(capturing||entry?.state==='draft'||entry?.state==='sending'||session.sendWhenStored);
    session.retrySave.hidden=!unstored;session.discardUnstored.hidden=!unstored;
    session.check.hidden=!recovering;
    for(const button of session.manualButtons){button.hidden=!recovering;button.disabled=busy;}
    for(const control of [session.mic,session.stop,session.pause,session.discard,session.retrySave,session.discardUnstored,session.send,session.check]) control.disabled=busy;
    session.cancel.disabled=snap.state==='stopping';
    session.send.disabled=busy||session.sendWhenStored||snap.state==='stopping'||entry?.state==='sending';
    const playback=entry?.blob?entry:recovering&&receipt.blob?receipt:null;
    session.audio.hidden=!playback;
    if(playback?.blob && session.renderedId!==playback.request_id){
      if(session.url)URL.revokeObjectURL(session.url);
      session.url=URL.createObjectURL(playback.blob);session.audio.src=session.url;session.renderedId=playback.request_id;
    }
    if(!playback && session.url){URL.revokeObjectURL(session.url);session.url='';session.audio.removeAttribute('src');session.renderedId='';}
    session.meter.hidden=!recording&&!unstored;
    session.meter.dataset.recording=String(snap.state==='recording');
    session.meter.setAttribute('aria-label',text(snap.state==='paused'?'paused':'recording'));
    session.duration.textContent=duration(snap.durationMs);
    session.status.textContent=session.message || (session.sendWhenStored||entry?.state==='sending'?text('sending'):
      session.saving?text('saving'):snap.state==='requesting'?text('requesting'):
      entry?.state==='draft'&&!session.maySend?text('disabled'):recovering&&receipt.manualConfirmation?.outcome?text('manualValue_'+receipt.manualConfirmation.outcome):recovering?text('unknown'):
      receipt?.state==='accepted'?acceptedNotice(receipt.deliveryState):'');
    session.status.hidden=!session.status.textContent;
    for(const panel of [session.toolbar,session.voiceActions])panel.hidden=[...panel.children].every(child=>child.hidden);
    session.host.hidden=session.toolbar.hidden&&session.voiceActions.hidden&&session.status.hidden;
    if(editorChanged)buttonsChanged();
  }
  async function saveStopped(session){
    const blob=session.recorder.recording();if(!blob || session.saving)return;
    session.saving=true;session.message='';draw(session);
    let saved=false;
    try{
      const entry={key:session.scope.key,request_id:crypto.randomUUID(),state:'draft',blob,mime:String(blob.type||'').split(';')[0].toLowerCase(),durationMs:session.recorder.snapshot().durationMs,createdAt:Date.now()};
      if(!['audio/ogg','audio/webm','audio/mp4'].includes(entry.mime))throw Error('unsupported');
      saved=await store.createScopedDraft(entry,session.scope.tenant,session.scope.user,session.epoch);
      if(saved)session.entry=entry;
      else{session.entry=await store.get(session.scope.key);session.message=text('conflict');}
      session.recorder.cancel();
    }catch(error){session.message=text(error.message==='unsupported'?'unsupported':'storage');}
    finally{
      const sendNow=session.sendWhenStored&&saved;
      session.sendWhenStored=false;session.saving=false;draw(session);
      if(sendNow&&same(session))void send(session);
    }
  }
  function recorderChanged(session,snapshot){
    if(['ready','error','idle','disposed'].includes(snapshot.state))release(session);
    if(snapshot.state==='ready')void saveStopped(session);
    if(snapshot.state==='error'){
      session.sendWhenStored=false;
      session.message=text(({permission_denied:'denied',microphone_unavailable:'unavailable',unsupported:'unsupported',size_limit:'limit'})[snapshot.error]||'error');
    }
    draw(session);
  }
  function closeSession(session,discard=false){
    if(!session)return;
    session.sendWhenStored=false;
    if(session.timer)clearInterval(session.timer);
    if(session.scroller&&session.onScroll)session.scroller.removeEventListener('scroll',session.onScroll);
    if(session.scrollTimer)clearTimeout(session.scrollTimer);
    const state=session.recorder.snapshot().state;
    if(discard)session.recorder.cancel();
    else if(isCapturing(state))session.recorder.stop();
    else if(state==='requesting')session.recorder.cancel();
    else if(state!=='stopping'&&state!=='ready')session.recorder.dispose();
    if(session.url){URL.revokeObjectURL(session.url);session.url='';}
    session.mic?.remove();session.host?.remove();buttonsChanged();
  }
  function node(tag,label,handler){const n=document.createElement(tag);if(label)n.textContent=text(label);if(tag==='button'){n.type='button';n.className='outline';n.onclick=handler;}return n;}
  const icons={
    trash:'<path d="M4 6h16m-14 0 1 15h10l1-15M9 6V3h6v3m-5 4v7m4-7v7"/>',
    pause:'<path d="M9 5v14m6-14v14" stroke-width="3"/>',
    play:'<path d="M8 5v14l11-7-11-7Z" fill="currentColor" stroke="none"/>',
    stop:'<rect x="7" y="7" width="10" height="10" rx="2"/>',
    send:'<path d="m3.6 3.8 17.6 7.3a1 1 0 0 1 0 1.8L3.6 20.2a.9.9 0 0 1-1.2-1l1.3-5.4 9-1.8-9-1.8-1.3-5.4a.9.9 0 0 1 1.2-1Z" fill="currentColor" stroke="none"/>'
  };
  function setIcon(button,label,icon){
    if(button.dataset.icon===icon)return;
    button.dataset.icon=icon;
    button.title=text(label);button.setAttribute('aria-label',text(label));
    button.innerHTML='<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'+icons[icon]+'</svg>';
  }
  function iconButton(label,icon,handler,primary=false){
    const button=node('button',null,handler);
    button.className=(primary?'primary':'outline')+' operator-voice-control'+(primary?' operator-voice-send':'');
    setIcon(button,label,icon);return button;
  }
  function requestSend(session){
    if(!same(session)||!session.maySend||session.busy||session.saving||session.sendWhenStored)return;
    if(isCapturing(session.recorder.snapshot().state)){
      session.message='';session.sendWhenStored=true;
      if(!session.recorder.stop()){session.sendWhenStored=false;draw(session);}
    }else void send(session);
  }
  function create(scopeValue,maySend){
    const session={scope:scopeValue,maySend,entry:null,receipt:null,busy:false,saving:false,sendWhenStored:false,message:'',release:null,host:node('section'),url:'',renderedId:''};
    session.host.className='operator-customer-voice';session.host.setAttribute('aria-label',text('mic'));
    session.mic=node('button','mic',()=>void start(session));session.mic.classList.add('operator-voice-mic');session.mic.textContent='🎙';session.mic.title=text('mic');session.mic.setAttribute('aria-label',text('mic'));session.mic.style.cssText='min-width:44px;min-height:44px';
    session.pause=iconButton('pause','pause',()=>{if(session.recorder.snapshot().state==='paused')session.recorder.resume();else session.recorder.pause();});
    session.stop=iconButton('stop','stop',()=>session.recorder.stop());session.cancel=iconButton('cancel','trash',()=>{session.message='';session.recorder.cancel();draw(session);});
    session.discard=iconButton('discard','trash',()=>void discard(session));session.retrySave=node('button','retrySave',()=>void saveStopped(session));
    session.discardUnstored=iconButton('discard','trash',()=>{session.message='';session.recorder.cancel();draw(session);});
    session.send=iconButton('send','send',()=>requestSend(session),true);session.check=node('button','check',()=>void check(session));
    session.manualButtons=[['seen','manualSeen'],['not_seen','manualNotSeen'],['unsure','manualUnsure']].map(([outcome,label])=>{
      const button=node('button',label,()=>void manualConfirmation(session,outcome));button.dataset.manualOutcome=outcome;return button;
    });
    session.audio=node('audio');session.audio.controls=true;session.audio.preload='metadata';
    session.duration=node('span');session.duration.setAttribute('role','timer');session.status=node('p');session.status.setAttribute('role','status');session.status.setAttribute('aria-live','polite');
    const indicator=node('span'),track=node('span');indicator.className='operator-voice-indicator';track.className='operator-voice-track';
    for(const ornament of [indicator,track])ornament.setAttribute('aria-hidden','true');
    session.meter=node('div');session.meter.className='operator-voice-meter';session.meter.setAttribute('role','group');session.meter.append(indicator,session.duration,track);
    session.toolbar=node('div');session.toolbar.className='operator-voice-toolbar';
    session.toolbar.append(session.cancel,session.discard,session.discardUnstored,session.meter,session.audio,session.pause,session.stop,session.send);
    const actions=node('div');actions.className='operator-customer-voice-actions';session.voiceActions=actions;
    actions.append(session.retrySave,session.check,...session.manualButtons);
    session.host.append(session.toolbar,actions,session.status);
    session.recorder=window.createTenantVoiceRecorder({onChange:snapshot=>recorderChanged(session,snapshot)});
    session.timer=setInterval(()=>{const state=session.recorder.snapshot().state;if(state==='recording')draw(session);
      const interval=isCapturing(state)||state==='requesting'?5000:30000;
      if(Date.now()-(session.capabilityCheckedAt||0)>interval)void refreshCapability(session);},1000);
    return session;
  }
  async function refreshCapability(session){
    if(session.capabilityPromise)return session.capabilityPromise;
    session.capabilityPromise=(async()=>{
      const query=new URLSearchParams({lead_id:session.scope.lead,expected_tenant_id:session.scope.tenant,expected_user_id:session.scope.user});
      const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),8000);
      let refusal='';
      try{
        const response=await fetch('/api/platform/crm/chat/voice-capability?'+query,{credentials:'same-origin',cache:'no-store',signal:controller.signal}),result=await readJson(response);
        if(!same(session))return false;
        if(response.status===401||(response.status===409&&result.error_code==='voice_identity_changed')){await forgetUser();return false;}
        if(response.status===403&&!session.scope.group){stopCurrent(true);return false;}
        if(session.scope.group)refusal=groupRefusal(response,result);
        session.maySend=!!(response.ok&&result.enabled===true);session.capabilityCheckedAt=Date.now();
      }catch{if(!same(session))return false;session.maySend=false;session.capabilityCheckedAt=Date.now();}finally{clearTimeout(timeout);}
      if(!session.maySend){const state=session.recorder.snapshot().state;if(isCapturing(state))session.recorder.stop();else if(state==='requesting')session.recorder.cancel();session.message=refusal||text('disabled');}
      draw(session);return session.maySend;
    })();
    try{return await session.capabilityPromise;}finally{session.capabilityPromise=null;}
  }
  async function start(session){
    if(!same(session)||session.busy||session.entry)return;
    session.busy=true;session.message='';draw(session);
    try{
      if(!await refreshCapability(session))return;
      if(!voiceBroadcast)throw Error('microphone_exclusion_unsupported');
      session.epoch=await store.activeEpoch(session.scope.tenant,session.scope.user);
      if(!same(session))return;
      const releaseLock=await window.acquireTenantMicrophone();
      if(!same(session)){releaseLock?.();return;}
      if(!releaseLock){session.message=text('busy');return;}
      session.release=releaseLock;
      if(await store.activeEpoch(session.scope.tenant,session.scope.user)!==session.epoch||!same(session)){release(session);return;}
      await session.recorder.start();
    }catch(error){session.message=text(error?.message==='microphone_exclusion_unsupported'?'unsupported':'unavailable');release(session);}
    finally{session.busy=false;draw(session);}
  }
  async function discard(session){
    if(!same(session)||!session.entry||session.entry.state!=='draft'||session.busy)return;
    session.busy=true;draw(session);
    try{await store.removeDraft(session.scope.key,session.entry.request_id);session.entry=await store.get(session.scope.key)||null;session.message=session.entry?text('conflict'):'';}
    catch{session.message=text('storage');}finally{session.busy=false;draw(session);}
  }
  async function readJson(response){try{return await response.json();}catch{return {success:false,keep_request:true};}}
  async function accepted(session,entry,result){
    await store.removeAccepted(session.scope.key,entry.request_id,result.delivery_state);
    session.entry=await store.get(session.scope.key)||null;session.receipt=await store.getReceipt(session.scope.key,entry.request_id);session.recorder.cancel();
    session.message=acceptedNotice(result.delivery_state);
    draw(session);if(same(session))await operator()?.openDialog?.({...operator().currentDialog},true);
  }
  async function send(session){
    const entry=session.entry;if(!same(session)||!session.maySend||!entry||entry.state!=='draft'||session.busy)return;
    session.busy=true;session.message='';draw(session);
    try{
      if(!await refreshCapability(session)||!same(session))return;
      const epoch=await store.activeEpoch(session.scope.tenant,session.scope.user);
      if(!same(session))return;
      if(!await store.claimScoped(session.scope.key,entry.request_id,session.scope.tenant,session.scope.user,epoch)){
        session.entry=await store.get(session.scope.key)||null;session.message=text('unknown');return;
      }
      if(!same(session)){await store.releaseDraft(session.scope.key,entry.request_id,crypto.randomUUID());return;}
      session.audio.pause();
      session.entry={...entry,state:'sending'};draw(session);
      const command={request_id:entry.request_id,lead_id:Number(session.scope.lead),expected_tenant_id:session.scope.tenant,expected_user_id:session.scope.user};
      if(session.scope.group)Object.assign(command,{expected_channel_id:session.scope.channel,expected_conversation_id:session.scope.conversation});
      const data=new FormData();data.append('command',JSON.stringify(command));data.append('file',entry.blob,'voice');
      let response,result;
      try{response=await fetch('/api/platform/crm/chat/send-voice',{method:'POST',credentials:'same-origin',body:data});result=await readJson(response);}
      catch{await store.markUnknown(session.scope.key,entry.request_id);session.entry=await store.get(session.scope.key)||null;session.receipt=await store.getReceipt(session.scope.key,entry.request_id);session.message=text('unknown');return;}
      if(response.ok&&result.success&&result.provider_accepted===true){await accepted(session,entry,result);return;}
      if(response.status===401||(response.status===403&&!session.scope.group)||(response.status===409&&result.error_code==='voice_identity_changed')){
        if(result.keep_request===false)await store.releaseDraft(session.scope.key,entry.request_id,crypto.randomUUID());
        else await store.markUnknown(session.scope.key,entry.request_id);
        if(!same(session))return;
        if(result.keep_request!==false){session.entry=await store.get(session.scope.key)||null;session.receipt=await store.getReceipt(session.scope.key,entry.request_id);}
        if(response.status===401||response.status===409)await forgetUser();else stopCurrent(true);return;
      }
      if(response.status===409&&result.keep_request===false&&['voice_manual_mode_unavailable'].includes(result.error_code)){
        await store.releaseDraft(session.scope.key,entry.request_id,crypto.randomUUID());
        session.entry=await store.get(session.scope.key)||null;session.message=text(result.error_code);return;
      }
      if(!response.ok&&result.keep_request===false&&(session.scope.group||response.status<500)){
        await store.releaseDraft(session.scope.key,entry.request_id,crypto.randomUUID());
        session.entry=await store.get(session.scope.key)||null;
        if(response.status===403)session.maySend=false;
        session.message=(session.scope.group?groupRefusal(response,result):'')||result.error||text('error');
        return;
      }
      await store.markUnknown(session.scope.key,entry.request_id);session.entry=await store.get(session.scope.key)||null;session.receipt=await store.getReceipt(session.scope.key,entry.request_id);session.message=text('unknown');
    }catch{session.message=text('storage');}
    finally{session.busy=false;draw(session);}
  }
  async function check(session){
    const entry=session.receipt;if(!same(session)||!entry||entry.state!=='unknown'||session.busy)return;
    session.busy=true;session.message='';draw(session);
    try{
      const query=new URLSearchParams({lead_id:session.scope.lead,request_id:entry.request_id,expected_tenant_id:session.scope.tenant,expected_user_id:session.scope.user});
      const response=await fetch('/api/platform/crm/chat/voice-receipt?'+query,{credentials:'same-origin',cache:'no-store'}),result=await readJson(response);
      if(!same(session)){if(response.ok&&result.success&&result.provider_accepted===true)await store.removeAccepted(session.scope.key,entry.request_id);return;}
      if(response.status===401||(response.status===409&&result.error_code==='voice_identity_changed')){await forgetUser();return;}
      if(response.status===403){stopCurrent(true);return;}
      if(response.ok&&result.success&&result.provider_accepted===true)await accepted(session,entry,result);
      else {session.receipt={...entry,manualConfirmation:result.manual_confirmation||entry.manualConfirmation};await store.updateReceipt(session.scope.key,entry.request_id,{manualConfirmation:session.receipt.manualConfirmation});session.message=text('unknown');}
    }catch{session.message=text('unknown');}finally{session.busy=false;draw(session);}
  }
  async function manualConfirmation(session,outcome){
    const receipt=session.receipt;if(!same(session)||!receipt||receipt.state!=='unknown'||session.busy)return;
    session.busy=true;session.message='';draw(session);
    try{
      const response=await fetch('/api/platform/crm/chat/voice-manual-confirmation',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({request_id:receipt.request_id,lead_id:Number(session.scope.lead),expected_tenant_id:session.scope.tenant,expected_user_id:session.scope.user,outcome})});
      const result=await readJson(response);
      if(!same(session))return;
      if(response.status===401||(response.status===409&&result.error_code==='voice_identity_changed')){await forgetUser();return;}
      if(response.status===403){stopCurrent(true);return;}
      if(!response.ok||!result.success)throw Error('manual_check_unavailable');
      session.receipt={...receipt,manualConfirmation:result.manual_confirmation};
      await store.updateReceipt(session.scope.key,receipt.request_id,{manualConfirmation:session.receipt.manualConfirmation});
      session.message=text('manualSaved');
    }catch{session.message=text('error');}finally{session.busy=false;draw(session);}
  }
  function scanStatuses(session){
    if(!same(session)||document.hidden)return;
    const scroller=document.querySelector('.operator-chat-scrollarea');if(!scroller)return;
    if(session.scroller!==scroller){
      if(session.scroller&&session.onScroll)session.scroller.removeEventListener('scroll',session.onScroll);
      session.scroller=scroller;session.onScroll=()=>{clearTimeout(session.scrollTimer);session.scrollTimer=setTimeout(()=>scanStatuses(session),300);};
      scroller.addEventListener('scroll',session.onScroll,{passive:true});
    }
    const box=scroller.getBoundingClientRect();if(!box.width||!box.height)return;
    for(const article of scroller.querySelectorAll('[data-customer-voice-status="true"]')){
      if(activeStatusChecks>=3)break;
      const rect=article.getBoundingClientRect();if(rect.bottom<box.top||rect.top>box.bottom)continue;
      const id=article.dataset.messageId,lead=article.dataset.customerVoiceLead;
      if(!/^[0-9a-f-]{36}$/i.test(id||'')||!/^\d+$/.test(lead||''))continue;
      const key=JSON.stringify([session.scope.key,lead,id]),prior=statusChecks.get(key)||0;
      if(Date.now()-prior<60000)continue;
      statusChecks.set(key,Date.now());if(statusChecks.size>200)statusChecks.delete(statusChecks.keys().next().value);
      activeStatusChecks++;
      const query=new URLSearchParams({lead_id:lead,message_row_id:id,expected_tenant_id:session.scope.tenant,expected_user_id:session.scope.user});
      void fetch('/api/platform/crm/chat/voice-status?'+query,{credentials:'same-origin',cache:'no-store'})
        .then(async response=>({response,result:await readJson(response)}))
        .then(({response,result})=>{if(same(session)&&response.ok&&result.success)operator()?.updateCustomerVoiceDelivery?.(id,result.delivery_state);})
        .catch(()=>{}).finally(()=>{activeStatusChecks--;if(same(session))scanStatuses(session);});
    }
  }
  async function attach(){
    const route=scope(),actions=document.querySelector('.operator-composer-actions'),composer=document.querySelector('.operator-composer'),sendButton=document.getElementById('sendChat');
    if(!route||!actions||!composer||!sendButton||sendButton.disabled){if(current){const old=current;current=null;closeSession(old);}return;}
    lastUser={tenant:route.tenant,user:route.user};
    const mount=session=>{
      actions.insertBefore(session.mic,sendButton);
      const input=composer.querySelector('#chatText');
      if(input)input.before(session.host);else composer.append(session.host);
      draw(session);buttonsChanged();
    };
    if(current?.scope.key===route.key&&current.scope.policy===route.policy){mount(current);scanStatuses(current);if(Date.now()-(current.capabilityCheckedAt||0)>30000)void refreshCapability(current);return;}
    const token=++revision;if(current){const old=current;current=null;closeSession(old);}
    const query=new URLSearchParams({lead_id:route.lead,expected_tenant_id:route.tenant,expected_user_id:route.user});
    try{
      const ownerVersion=await store.ownerVersion();
      const response=await fetch('/api/platform/crm/chat/voice-capability?'+query,{credentials:'same-origin',cache:'no-store'}),result=await readJson(response);
      if(token!==revision||scope()?.key!==route.key||scope()?.policy!==route.policy||!response.ok)return;
      if(!result.enabled&&(!result.recovery_enabled||(!await store.get(route.key)&&!(await store.latestReceipt(route.key)))))return;
      if(!await store.activate(route.tenant,route.user,ownerVersion)||token!==revision)return;
      const session=create(route,result.enabled===true);session.saving=true;current=session;
      mount(session);
      try{voiceBroadcast?.postMessage({active:true,tenant:route.tenant,user:route.user});session.entry=await store.get(route.key)||null;session.receipt=await store.latestReceipt(route.key);}
      catch{session.message=text('storage');}
      finally{session.saving=false;if(same(session)){draw(session);scanStatuses(session);}}
    }catch{/* Unavailable capability remains hidden. */}
  }
  const stopCurrent=(discard=false)=>{revision++;if(current){const old=current;current=null;closeSession(old,discard);}};
  const voiceBroadcast=typeof BroadcastChannel==='function'?new BroadcastChannel('beintaskbot-customer-voice-logout'):null;
  if(voiceBroadcast)voiceBroadcast.onmessage=event=>{if(!lastUser)return;const sameUser=event.data?.tenant===lastUser.tenant&&event.data?.user===lastUser.user;if(event.data?.active?!sameUser:sameUser){lastUser=null;stopCurrent(true);}};
  const forgetUser=async()=>{const member=operator()?.member;const old=lastUser||(member?.tenant_id&&member?.telegram_id?{tenant:String(member.tenant_id),user:String(member.telegram_id)}:null);lastUser=null;stopCurrent(true);if(old){voiceBroadcast?.postMessage(old);await store.purgeUser(old.tenant,old.user);}};
  window.tenantCustomerVoiceForget=forgetUser;
  document.addEventListener('tenant-chat-attachments-ready',()=>void attach());
  document.addEventListener('tenant-chat-close',()=>stopCurrent());
  document.addEventListener('tenant-auth-invalid',()=>{void forgetUser().catch(()=>{});});
  document.addEventListener('tenant-profile',()=>{const active=scope();if(lastUser&&active&&(active.tenant!==lastUser.tenant||active.user!==lastUser.user))void forgetUser().catch(()=>{});else if(current&&(active?.key!==current.scope.key||active?.policy!==current.scope.policy))stopCurrent();});
  document.addEventListener('tenant-view',event=>{if(event.detail?.view!=='dialogs')stopCurrent();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&isCapturing(current?.recorder.snapshot().state))current.recorder.stop();else if(!document.hidden&&current)void refreshCapability(current);});
  window.addEventListener('pagehide',()=>{if(isCapturing(current?.recorder.snapshot().state))current.recorder.stop();});
  const style=document.createElement('style');
  style.textContent=`
    .operator-customer-voice { display:grid; gap:6px; min-width:0 }
    .operator-voice-toolbar {
      display:flex; align-items:center; min-width:0; gap:clamp(4px,1.4vw,10px);
      padding:4px 6px; border:1px solid var(--line,#d8e0dc); border-radius:16px;
      background:var(--card,#fff); color:var(--ink,#132621);
    }
    .operator-voice-toolbar .operator-voice-control {
      display:grid; place-items:center; flex:none; width:44px; height:44px;
      min-width:44px; min-height:44px; padding:0; border:0; border-radius:50%; box-shadow:none;
      background:transparent; color:inherit;
    }
    .operator-voice-toolbar .operator-voice-control:not(:disabled):hover { background:var(--paper,#eef3f1) }
    .operator-voice-toolbar .operator-voice-control:focus-visible { outline:2px solid var(--brand,#087f5b); outline-offset:1px }
    .operator-voice-toolbar .operator-voice-control:disabled { opacity:.5 }
    .operator-voice-toolbar .operator-voice-control svg { width:21px; height:21px; pointer-events:none }
    .operator-voice-toolbar .operator-voice-control[data-icon="pause"] { color:var(--danger,#b42343) }
    .operator-voice-toolbar .operator-voice-send,
    .operator-voice-toolbar .operator-voice-send:not(:disabled):hover {
      margin-inline-start:auto; background:var(--ink,#132621); color:var(--card,#fff)
    }
    .operator-voice-meter { display:flex; align-items:center; flex:1; min-width:0; gap:clamp(4px,1.5vw,10px); font-variant-numeric:tabular-nums }
    .operator-voice-indicator { flex:none; width:8px; height:8px; border-radius:50%; background:currentColor; opacity:.45 }
    .operator-voice-meter[data-recording="true"] .operator-voice-indicator { background:var(--danger,#b42343); opacity:1 }
    .operator-voice-track { flex:1; min-width:0; border-top:2px dotted currentColor; opacity:.45 }
    .operator-voice-toolbar audio { display:block; flex:1; width:0; min-width:0; height:44px }
    .operator-customer-voice-actions { display:flex; flex-wrap:wrap; align-items:center; gap:8px }
    .operator-customer-voice [role=status] { margin:0; overflow-wrap:anywhere }
    .operator-customer-voice[hidden],.operator-customer-voice [hidden] { display:none!important }
  `;
  document.head.append(style);
  void attach();
})();
