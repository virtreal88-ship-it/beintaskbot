/* Explicit customer recording and one-shot delivery, scoped to the open dialog. */
(()=>{
  if (!window.createTenantVoiceRecorder || !window.createTenantVoiceStore || !window.acquireTenantMicrophone) return;
  const store=window.createTenantVoiceStore();
  const words={
    ru:{mic:'Записать голосовое',stop:'Остановить',cancel:'Отменить',discard:'Удалить запись',retrySave:'Сохранить запись снова',send:'Отправить голосовое',check:'Проверить статус',
      requesting:'Ожидаем разрешение на микрофон…',recording:'Запись',saving:'Сохраняем запись на устройстве…',draft:'Прослушайте запись перед отправкой. Она хранится на устройстве до отправки, удаления или выхода из аккаунта.',
      sending:'Отправляем…',unknown:'Результат неизвестен. Повторная отправка отключена; проверьте статус.',accepted:'Провайдер принял сообщение. Доставка клиенту пока не подтверждена.',
      delivered:'Доставка подтверждена.',read:'Сообщение прочитано.',failed:'Провайдер сообщил об ошибке доставки. Проверьте историю.',
      denied:'Доступ к микрофону запрещён. Разрешите микрофон в настройках сайта браузера и попробуйте снова.',
      unavailable:'Микрофон недоступен. Проверьте устройство и разрешения браузера.',unsupported:'Этот браузер не поддерживает безопасную запись. Попробуйте обновить браузер.',
      busy:'Микрофон уже используется в другой вкладке или голосовом режиме.',storage:'Не удалось сохранить запись на устройстве. Отправка недоступна.',limit:'Превышен размер записи. Запишите более короткое сообщение.',error:'Не удалось записать или проверить голосовое сообщение.',
      conflict:'В этом диалоге уже есть другая сохранённая запись. Откройте её перед продолжением.',disabled:'Отправка голосовых временно отключена.',
      voice_correlation_unavailable:'Отправка не выполнена: Connect пока не может гарантировать точную связь голосового с сообщением WhatsApp. Запись сохранена.',
      voice_attribution_unresolved:'Отправка не выполнена: в этом диалоге ещё не разрешён статус предыдущего голосового сообщения. Запись сохранена.'},
    az:{mic:'Səs yaz',stop:'Dayandır',cancel:'Ləğv et',discard:'Yazını sil',retrySave:'Yazını yenidən saxla',send:'Səsi göndər',check:'Vəziyyəti yoxla',
      requesting:'Mikrofon icazəsi gözlənilir…',recording:'Yazılır',saving:'Yazı cihazda saxlanılır…',draft:'Göndərməzdən əvvəl dinləyin. Yazı göndərilənə, silinənə və ya hesabdan çıxana qədər cihazda saxlanılır.',
      sending:'Göndərilir…',unknown:'Nəticə məlum deyil. Təkrar göndərmə bağlıdır; vəziyyəti yoxlayın.',accepted:'Provayder qəbul etdi. Müştəriyə çatdırılma hələ təsdiqlənməyib.',
      delivered:'Çatdırılma təsdiqləndi.',read:'Mesaj oxunub.',failed:'Provayder çatdırılma xətası bildirdi. Tarixçəni yoxlayın.',
      denied:'Mikrofon icazəsi verilməyib. Brauzerdə sayt üçün mikrofon icazəsini açıb yenidən yoxlayın.',
      unavailable:'Mikrofon əlçatan deyil. Cihazı və brauzer icazələrini yoxlayın.',unsupported:'Bu brauzer təhlükəsiz səs yazısını dəstəkləmir. Brauzeri yeniləyin.',
      busy:'Mikrofon başqa vərəqdə və ya səs rejimində istifadə olunur.',storage:'Yazını cihazda saxlamaq mümkün olmadı. Göndərmə əlçatan deyil.',limit:'Yazının ölçüsü böyükdür. Daha qısa səs yazın.',error:'Səsi yazmaq və ya vəziyyətini yoxlamaq mümkün olmadı.',
      conflict:'Bu dialoq üçün başqa saxlanmış yazı var. Davam etməzdən əvvəl onu açın.',disabled:'Səs göndərilməsi müvəqqəti bağlıdır.',
      voice_correlation_unavailable:'Göndərilmədi: Connect WhatsApp səsi ilə mesaj arasında dəqiq əlaqəni hələ təmin edə bilmir. Yazı saxlanılıb.',
      voice_attribution_unresolved:'Göndərilmədi: bu dialoqda əvvəlki səs mesajının vəziyyəti hələ aydın deyil. Yazı saxlanılıb.'},
    en:{mic:'Record voice message',stop:'Stop',cancel:'Cancel',discard:'Delete recording',retrySave:'Try saving again',send:'Send voice message',check:'Check status',
      requesting:'Waiting for microphone permission…',recording:'Recording',saving:'Saving recording on this device…',draft:'Listen before sending. The recording stays on this device until sent, deleted or you sign out.',
      sending:'Sending…',unknown:'The outcome is unknown. Resending is disabled; check the status.',accepted:'The provider accepted the message. Customer delivery is not yet confirmed.',
      delivered:'Delivery confirmed.',read:'Message read.',failed:'The provider reported a delivery failure. Check the history.',
      denied:'Microphone access is blocked. Allow the microphone in your browser’s site settings and try again.',
      unavailable:'Microphone unavailable. Check the device and browser permissions.',unsupported:'This browser cannot record safely. Try updating your browser.',
      busy:'The microphone is already in use in another tab or voice mode.',storage:'The recording could not be saved on this device. Sending is unavailable.',limit:'The recording is too large. Record a shorter message.',error:'Could not record or check the voice message.',
      conflict:'Another recording is saved for this dialog. Open it before continuing.',disabled:'Voice sending is temporarily unavailable.',
      voice_correlation_unavailable:'Not sent: Connect cannot yet guarantee an exact link between the voice note and the WhatsApp message. The recording is saved.',
      voice_attribution_unresolved:'Not sent: the previous voice message in this dialog still has an unresolved status. The recording is saved.'}
  };
  const lang=()=>words[window.tenantI18n?.language]||words.en;
  const text=key=>lang()[key]||lang().error;
  const operator=()=>window.tenantOperator;
  function scope(){
    const member=operator()?.member, route=operator()?.currentDialog;
    if(!member || !member.tenant_id || !member.telegram_id || !route || route.provider!=='connect' || !route.lead_id || !route.channel_id || !route.conversation_id) return null;
    const key=JSON.stringify([String(member.tenant_id),String(member.telegram_id),String(route.lead_id),String(route.channel_id),String(route.conversation_id)]);
    const policy=JSON.stringify([member.role,member.active,member.permissions,window.tenantProfileSnapshot?.capabilities]);
    return {key,policy,tenant:String(member.tenant_id),user:String(member.telegram_id),lead:String(route.lead_id)};
  }
  let current=null, revision=0, activeStatusChecks=0, lastUser=null;
  const statusChecks=new Map();
  function same(session){const active=scope();return current===session && active?.key===session.scope.key && active?.policy===session.scope.policy && session.host?.isConnected;}
  function release(session){if(session.release){session.release();session.release=null;}}
  function setStatus(session,value){session.message=value;if(same(session))session.status.textContent=value;}
  function duration(value){const seconds=Math.max(0,Math.floor((value||0)/1000));return Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0');}
  function draw(session){
    if(!same(session))return;
    const snap=session.recorder.snapshot(), entry=session.entry, recording=['requesting','recording','stopping'].includes(snap.state);
    const unstored=snap.state==='ready'&&!entry;
    session.mic.hidden=!session.maySend||recording||unstored||!!entry||session.saving;
    session.stop.hidden=snap.state!=='recording';session.cancel.hidden=!recording;
    session.discard.hidden=!entry||entry.state!=='draft';session.send.hidden=!session.maySend||!entry||entry.state!=='draft';
    session.retrySave.hidden=!unstored;session.discardUnstored.hidden=!unstored;
    session.check.hidden=!entry||entry.state==='draft';
    for(const node of [session.mic,session.stop,session.cancel,session.discard,session.retrySave,session.discardUnstored,session.send,session.check]) node.disabled=!!session.busy||!!session.saving;
    session.audio.hidden=!entry?.blob;
    if(entry?.blob && session.renderedId!==entry.request_id){
      if(session.url)URL.revokeObjectURL(session.url);
      session.url=URL.createObjectURL(entry.blob);session.audio.src=session.url;session.renderedId=entry.request_id;
    }
    if(!entry?.blob && session.url){URL.revokeObjectURL(session.url);session.url='';session.audio.removeAttribute('src');session.renderedId='';}
    session.duration.textContent=entry?duration(entry.durationMs):snap.state==='recording'?duration(snap.durationMs):'';
    session.status.textContent=session.message || (session.saving?text('saving'):snap.state==='requesting'?text('requesting'):
      snap.state==='recording'?text('recording'):entry?.state==='draft'?text('draft'):entry?text('unknown'):'');
  }
  async function saveStopped(session){
    const blob=session.recorder.recording();if(!blob || session.saving)return;
    session.saving=true;session.message='';draw(session);
    try{
      const entry={key:session.scope.key,request_id:crypto.randomUUID(),state:'draft',blob,mime:String(blob.type||'').split(';')[0].toLowerCase(),durationMs:session.recorder.snapshot().durationMs,createdAt:Date.now()};
      if(!['audio/ogg','audio/webm','audio/mp4'].includes(entry.mime))throw Error('unsupported');
      if(await store.createScopedDraft(entry,session.scope.tenant,session.scope.user,session.epoch))session.entry=entry;
      else{session.entry=await store.get(session.scope.key);session.message=text('conflict');}
      session.recorder.cancel();
    }catch(error){session.message=text(error.message==='unsupported'?'unsupported':'storage');}
    finally{session.saving=false;draw(session);}
  }
  function recorderChanged(session,snapshot){
    if(['ready','error','idle','disposed'].includes(snapshot.state))release(session);
    if(snapshot.state==='ready')void saveStopped(session);
    if(snapshot.state==='error')session.message=text(({permission_denied:'denied',microphone_unavailable:'unavailable',unsupported:'unsupported',size_limit:'limit'})[snapshot.error]||'error');
    draw(session);
  }
  function closeSession(session,discard=false){
    if(!session)return;
    if(session.timer)clearInterval(session.timer);
    if(session.scroller&&session.onScroll)session.scroller.removeEventListener('scroll',session.onScroll);
    if(session.scrollTimer)clearTimeout(session.scrollTimer);
    const state=session.recorder.snapshot().state;
    if(discard)session.recorder.cancel();
    else if(state==='recording')session.recorder.stop();
    else if(state==='requesting')session.recorder.cancel();
    else if(state!=='stopping'&&state!=='ready')session.recorder.dispose();
    if(session.url){URL.revokeObjectURL(session.url);session.url='';}
    session.mic?.remove();session.host?.remove();
  }
  function node(tag,label,handler){const n=document.createElement(tag);if(label)n.textContent=text(label);if(tag==='button'){n.type='button';n.className='outline';n.onclick=handler;}return n;}
  function create(scopeValue,maySend){
    const session={scope:scopeValue,maySend,entry:null,busy:false,saving:false,message:'',release:null,host:node('section'),url:'',renderedId:''};
    session.host.className='operator-customer-voice';session.host.setAttribute('aria-label',text('mic'));session.host.style.cssText='display:grid;gap:8px;min-width:0;margin-top:8px';
    session.mic=node('button','mic',()=>void start(session));session.mic.classList.add('operator-voice-mic');session.mic.textContent='🎙';session.mic.title=text('mic');session.mic.setAttribute('aria-label',text('mic'));session.mic.style.cssText='min-width:44px;min-height:44px';
    session.stop=node('button','stop',()=>session.recorder.stop());session.cancel=node('button','cancel',()=>{session.message='';session.recorder.cancel();draw(session);});
    session.discard=node('button','discard',()=>void discard(session));session.retrySave=node('button','retrySave',()=>void saveStopped(session));
    session.discardUnstored=node('button','discard',()=>{session.message='';session.recorder.cancel();draw(session);});
    session.send=node('button','send',()=>void send(session));session.check=node('button','check',()=>void check(session));
    session.audio=node('audio');session.audio.controls=true;session.audio.preload='metadata';session.audio.style.cssText='width:100%;max-width:100%';
    session.duration=node('span');session.status=node('p');session.status.setAttribute('role','status');session.status.setAttribute('aria-live','polite');
    const actions=node('div');actions.style.cssText='display:flex;flex-wrap:wrap;align-items:center;gap:8px';
    actions.append(session.stop,session.cancel,session.discard,session.retrySave,session.discardUnstored,session.send,session.check,session.duration);
    session.host.append(session.audio,actions,session.status);
    session.recorder=window.createTenantVoiceRecorder({onChange:snapshot=>recorderChanged(session,snapshot)});
    session.timer=setInterval(()=>{const state=session.recorder.snapshot().state;if(state==='recording')draw(session);
      const interval=['recording','requesting'].includes(state)?5000:30000;
      if(Date.now()-(session.capabilityCheckedAt||0)>interval)void refreshCapability(session);},1000);
    return session;
  }
  async function refreshCapability(session){
    if(session.capabilityPromise)return session.capabilityPromise;
    session.capabilityPromise=(async()=>{
      const query=new URLSearchParams({lead_id:session.scope.lead,expected_tenant_id:session.scope.tenant,expected_user_id:session.scope.user});
      const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),8000);
      try{
        const response=await fetch('/api/platform/crm/chat/voice-capability?'+query,{credentials:'same-origin',cache:'no-store',signal:controller.signal}),result=await readJson(response);
        if(!same(session))return false;
        if(response.status===401||(response.status===409&&result.error_code==='voice_identity_changed')){await forgetUser();return false;}
        if(response.status===403){stopCurrent(true);return false;}
        session.maySend=!!(response.ok&&result.enabled===true);session.capabilityCheckedAt=Date.now();
      }catch{if(!same(session))return false;session.maySend=false;session.capabilityCheckedAt=Date.now();}finally{clearTimeout(timeout);}
      if(!session.maySend){const state=session.recorder.snapshot().state;if(state==='recording')session.recorder.stop();else if(state==='requesting')session.recorder.cancel();session.message=text('disabled');}
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
    await store.removeAccepted(session.scope.key,entry.request_id);
    session.entry=await store.get(session.scope.key)||null;session.recorder.cancel();
    session.message=text(result.delivery_state==='read'?'read':result.delivery_state==='delivered'?'delivered':result.delivery_state==='failed'?'failed':'accepted');
    draw(session);if(same(session))await operator()?.openDialog?.({...operator().currentDialog},true);
  }
  async function send(session){
    const entry=session.entry;if(!same(session)||!session.maySend||!entry||entry.state!=='draft'||session.busy)return;
    session.busy=true;session.message='';draw(session);
    try{
      if(!await refreshCapability(session))return;
      if(!await store.claim(session.scope.key,entry.request_id)){session.entry=await store.get(session.scope.key)||null;session.message=text('unknown');return;}
      session.entry={...entry,state:'sending'};draw(session);
      const command={request_id:entry.request_id,lead_id:Number(session.scope.lead),expected_tenant_id:session.scope.tenant,expected_user_id:session.scope.user};
      const data=new FormData();data.append('command',JSON.stringify(command));data.append('file',entry.blob,'voice');
      let response,result;
      try{response=await fetch('/api/platform/crm/chat/send-voice',{method:'POST',credentials:'same-origin',body:data});result=await readJson(response);}
      catch{await store.markUnknown(session.scope.key,entry.request_id);session.entry=await store.get(session.scope.key)||null;session.message=text('unknown');return;}
      if(response.ok&&result.success&&result.provider_accepted===true){await accepted(session,entry,result);return;}
      if(response.status===401||response.status===403||(response.status===409&&result.error_code==='voice_identity_changed')){
        if(result.keep_request===false)await store.releaseDraft(session.scope.key,entry.request_id,crypto.randomUUID());
        else await store.markUnknown(session.scope.key,entry.request_id);
        if(!same(session))return;
        if(response.status===401||response.status===409)await forgetUser();else stopCurrent(true);return;
      }
      if(response.status===409&&result.keep_request===false&&['voice_correlation_unavailable','voice_attribution_unresolved'].includes(result.error_code)){
        await store.releaseDraft(session.scope.key,entry.request_id,crypto.randomUUID());
        session.entry=await store.get(session.scope.key)||null;session.message=text(result.error_code);return;
      }
      if(!response.ok&&response.status<500&&result.keep_request===false){await store.releaseDraft(session.scope.key,entry.request_id,crypto.randomUUID());session.entry=await store.get(session.scope.key)||null;session.message=result.error||text('error');return;}
      await store.markUnknown(session.scope.key,entry.request_id);session.entry=await store.get(session.scope.key)||null;session.message=text('unknown');
    }catch{session.message=text('storage');}
    finally{session.busy=false;draw(session);}
  }
  async function check(session){
    const entry=session.entry;if(!same(session)||!entry||entry.state==='draft'||session.busy)return;
    session.busy=true;session.message='';draw(session);
    try{
      const query=new URLSearchParams({lead_id:session.scope.lead,request_id:entry.request_id,expected_tenant_id:session.scope.tenant,expected_user_id:session.scope.user});
      const response=await fetch('/api/platform/crm/chat/voice-receipt?'+query,{credentials:'same-origin',cache:'no-store'}),result=await readJson(response);
      if(!same(session)){if(response.ok&&result.success&&result.provider_accepted===true)await store.removeAccepted(session.scope.key,entry.request_id);return;}
      if(response.status===401||(response.status===409&&result.error_code==='voice_identity_changed')){await forgetUser();return;}
      if(response.status===403){stopCurrent(true);return;}
      if(response.ok&&result.success&&result.provider_accepted===true)await accepted(session,entry,result);
      else session.message=text('unknown');
    }catch{session.message=text('unknown');}finally{session.busy=false;draw(session);}
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
    if(current?.scope.key===route.key&&current.scope.policy===route.policy){actions.insertBefore(current.mic,sendButton);composer.append(current.host);draw(current);scanStatuses(current);if(Date.now()-(current.capabilityCheckedAt||0)>30000)void refreshCapability(current);return;}
    const token=++revision;if(current){const old=current;current=null;closeSession(old);}
    const query=new URLSearchParams({lead_id:route.lead,expected_tenant_id:route.tenant,expected_user_id:route.user});
    try{
      const ownerVersion=await store.ownerVersion();
      const response=await fetch('/api/platform/crm/chat/voice-capability?'+query,{credentials:'same-origin',cache:'no-store'}),result=await readJson(response);
      if(token!==revision||scope()?.key!==route.key||scope()?.policy!==route.policy||!response.ok)return;
      if(!result.enabled&&(!result.recovery_enabled||!await store.get(route.key)))return;
      if(!await store.activate(route.tenant,route.user,ownerVersion)||token!==revision)return;
      const session=create(route,result.enabled===true);session.saving=true;current=session;
      actions.insertBefore(session.mic,sendButton);composer.append(session.host);draw(session);
      try{voiceBroadcast?.postMessage({active:true,tenant:route.tenant,user:route.user});session.entry=await store.get(route.key)||null;}
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
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&current?.recorder.snapshot().state==='recording')current.recorder.stop();else if(!document.hidden&&current)void refreshCapability(current);});
  window.addEventListener('pagehide',()=>{if(current?.recorder.snapshot().state==='recording')current.recorder.stop();});
  void attach();
})();
