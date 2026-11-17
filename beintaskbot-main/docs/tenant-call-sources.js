/* Owner pairing uses the confirmed Connect source; no WhatsApp secrets persist in the browser. */
(()=>{
  'use strict';let current=null;
  const profile=()=>window.tenantOperator?.member||window.tenantProfileSnapshot?.member;
  const identity=p=>String(p?.tenant_id||'')+':'+String(p?.telegram_id||'');
  const node=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
  function close(){if(!current)return;clearTimeout(current.timer);current.controller?.abort();current.dialog.close();current.dialog.remove();current=null;}
  async function update(state,provision=false){
    if(current!==state||identity(profile())!==state.identity){close();return;}
    state.button.disabled=true;clearTimeout(state.timer);state.image.removeAttribute('src');state.image.hidden=true;
    const q=new URLSearchParams({expected_tenant_id:state.owner.tenant_id,expected_user_id:state.owner.telegram_id});
    const controller=new AbortController();state.controller=controller;const timeout=setTimeout(()=>controller.abort(),30000);
    try{
      const response=await fetch('/api/platform/call-sources/'+encodeURIComponent(state.channel)+'?'+q,{method:provision?'POST':'GET',credentials:'same-origin',cache:'no-store',signal:controller.signal,
        headers:provision?{'Content-Type':'application/json'}:undefined,body:provision?JSON.stringify({confirm_source_pairing:true}):undefined});
      const payload=await response.json();if(!response.ok)throw new Error(payload.error||'Не удалось проверить подключение');
      if(current!==state||identity(profile())!==state.identity)return;
      const result=payload.result;
      const labels={not_configured:'Звонки для источника ещё не подключены.',provisioning:'Подготавливаем сервер звонков…',pairing:'Откройте WhatsApp нужного номера → Связанные устройства → Привязать устройство.',ready:'Звонки подключены. Сотрудники с доступом к этому источнику могут звонить из диалога.',offline:'Номер привязан, но соединение с WhatsApp временно потеряно.',identity_mismatch:'Подключён другой номер. Звонки заблокированы: номер должен совпадать с источником.'};
      state.status.textContent=(result.phone?'Номер источника: +'+result.phone+'. ':'')+(labels[result.state]||'Проверяем подключение…');
      state.button.hidden=result.state==='ready';state.button.textContent=result.state==='not_configured'?'Подключить звонки':'Обновить QR';
      if(result.state==='pairing'&&/^[A-Za-z0-9+/=]{16,24000}$/.test(result.qr||'')){state.image.src='data:image/png;base64,'+result.qr;state.image.hidden=false;}
      if(result.state==='ready')document.dispatchEvent(new CustomEvent('tenant-calls-updated',{detail:{channel_id:state.channel}}));
      else if(result.state!=='not_configured'&&result.state!=='identity_mismatch')state.timer=setTimeout(()=>void update(state),5000);
    }catch(error){if(current===state){state.status.textContent=error.name==='AbortError'?'Сервер ещё не ответил. Можно повторить проверку.':error.message;state.timer=setTimeout(()=>void update(state),10000);}}
    finally{clearTimeout(timeout);if(current===state)state.button.disabled=false;}
  }
  function open(channel){
    if(profile()?.role!=='owner'||!/^[0-9a-f-]{36}$/i.test(channel))return;
    close();const dialog=node('dialog');dialog.className='browser-call-panel';dialog.setAttribute('aria-label','Звонки источника WhatsApp');
    const title=node('h2','Звонки источника'),dismiss=node('button','×');dismiss.type='button';dismiss.className='browser-call-close';dismiss.setAttribute('aria-label','Закрыть настройки звонков');dismiss.onclick=close;
    const status=node('p','Проверяем источник…');status.setAttribute('role','status');const image=node('img');image.alt='QR подключения звонков WhatsApp';image.hidden=true;image.style.cssText='display:block;max-width:100%;width:240px;margin:auto';
    const info=node('p','Номер звонка будет совпадать с источником диалога. Переписки и существующее подключение не меняются. Записи обеих сторон хранятся 30 дней.');
    const button=node('button','Подключить звонки');button.type='button';button.className='primary';
    dialog.append(dismiss,title,status,image,info,button);document.body.append(dialog);
    current={dialog,status,image,button,channel,owner:{...profile()},identity:identity(profile())};const state=current;
    button.onclick=()=>void update(state,true);dialog.addEventListener('cancel',event=>{event.preventDefault();close();});dialog.showModal();void update(state);
  }
  document.addEventListener('click',event=>{const button=event.target.closest('[data-connect-calls]');if(button)open(button.dataset.connectCalls);});
  document.addEventListener('tenant-auth-invalid',close);document.addEventListener('tenant-profile',()=>{if(current&&identity(profile())!==current.identity)close();});
  window.addEventListener('pagehide',close);
})();

/* Passive inventory indicators: this endpoint never pairs, provisions or heartbeats. */
(()=>{
  const mounts=new WeakMap();
  const phone=value=>String(value||'').replace(/\D/g,'');
  const identity=member=>JSON.stringify([member?.tenant_id,member?.telegram_id]);
  const labels={ru:{online:'Звонки подключены',offline:'Звонки: источник не в сети',pilot_online:'Звонки: тестовое подключение активно',pilot_offline:'Звонки: тестовое подключение не в сети',unavailable:'Звонки недоступны для этого источника',unknown:'Статус подключения звонков неизвестен',mismatch:'Звонки: номер источника не совпадает'},az:{online:'Zənglər qoşulub',offline:'Zənglər: mənbə oflayndır',pilot_online:'Zənglər: sınaq bağlantısı aktivdir',pilot_offline:'Zənglər: sınaq bağlantısı oflayndır',unavailable:'Bu mənbə üçün zənglər əlçatan deyil',unknown:'Zəng bağlantısının statusu məlum deyil',mismatch:'Zənglər: mənbənin nömrəsi uyğun deyil'},en:{online:'Calls connected',offline:'Calls: source offline',pilot_online:'Calls: test connection online',pilot_offline:'Calls: test connection offline',unavailable:'Calls unavailable for this source',unknown:'Call connection status unknown',mismatch:'Calls: source number mismatch'}};
  function state(channel,sources){
    if(!Array.isArray(sources))return 'unknown';
    const matches=sources.filter(s=>String(s.channel_id)===String(channel.id));
    if(!matches.length)return 'unavailable';if(matches.length!==1||channel.phoneConflict)return 'unknown';
    const source=matches[0],number=phone(channel.phone),actual=phone(source.number);
    if(!number||!actual)return 'unknown';if(number!==actual)return 'mismatch';
    if(source.permanent===false)return source.online===true?'pilot_online':source.online===false?'pilot_offline':'unknown';if(source.permanent!==true)return 'unknown';
    return source.online===true?'online':source.online===false?'offline':'unknown';
  }
  function mount(host,channels,member,options={}){
    mounts.get(host)?.();let stopped=false,serial=0,controller,pollTimer;
    const owner={...member},scope=identity(owner);
    const valid=()=>!stopped&&host.isConnected&&options.isCurrent?.()!==false&&(!window.tenantProfileSnapshot?.member||identity(window.tenantProfileSnapshot.member)===scope);
    const paint=sources=>{for(const icon of host.querySelectorAll('[data-call-status-channel]')){
      const channel=channels.find(c=>String(c.id)===icon.dataset.callStatusChannel),value=channel?state(channel,sources):'unknown';
      const title=(labels[window.tenantI18n?.language]||labels.az)[value];icon.classList.add('connect-call-status');icon.dataset.state=value;icon.title=title;icon.setAttribute('role','img');icon.setAttribute('aria-label',title);
      icon.innerHTML='<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M5 3h4l2 5-3 2c1.5 3 3 4.5 6 6l2-3 5 2v4c0 1-1 2-2 2C10 20 4 14 3 5c0-1 1-2 2-2Z"/></svg>';
    }};
    const refresh=async()=>{
      clearTimeout(pollTimer);if(!valid()||document.visibilityState==='hidden'||!host.querySelector('[data-call-status-channel]'))return;const request=++serial;controller?.abort();const active=new AbortController();controller=active;paint(null);
      if(!owner.tenant_id||!owner.telegram_id)return;
      const timer=setTimeout(()=>active.abort(),15000);
      try{const query=new URLSearchParams({expected_tenant_id:owner.tenant_id,expected_user_id:owner.telegram_id,heartbeat:'0'});
        const response=await fetch('/api/platform/browser-calls?'+query,{method:'GET',credentials:'same-origin',cache:'no-store',signal:active.signal});
        const data=await response.json();if(!response.ok||data.success===false)throw Error('unavailable');if(valid()&&request===serial)paint(data.result?.enabled===false?[]:data.result?.sources);
      }catch{if(valid()&&request===serial)paint(null);}finally{clearTimeout(timer);if(valid()&&request===serial&&document.visibilityState!=='hidden')pollTimer=setTimeout(refresh,15000);}
    };
    const visibility=()=>{if(document.visibilityState==='hidden'){clearTimeout(pollTimer);serial++;controller?.abort();paint(null);}else void refresh();};
    const stop=()=>{paint(null);stopped=true;serial++;clearTimeout(pollTimer);controller?.abort();window.removeEventListener('pagehide',stop);document.removeEventListener('visibilitychange',visibility);document.removeEventListener('tenant-calls-updated',refresh);document.removeEventListener('tenant-auth-invalid',stop);document.removeEventListener('tenant-profile',changed);};
    const changed=()=>{if(!valid()){paint(null);stop();}};
    mounts.set(host,stop);window.addEventListener('pagehide',stop);document.addEventListener('visibilitychange',visibility);document.addEventListener('tenant-calls-updated',refresh);document.addEventListener('tenant-auth-invalid',stop);document.addEventListener('tenant-profile',changed);
    void refresh();return stop;
  }
  window.tenantCallSourceIndicators={mount,state};
})();
