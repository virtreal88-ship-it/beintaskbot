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
