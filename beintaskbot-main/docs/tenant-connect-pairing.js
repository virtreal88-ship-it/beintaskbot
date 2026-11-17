/* Explicit new-number pairing. Stable intent ID survives close/reload; no background provisioning. */
(()=>{
  let current;
  const copy={ru:{title:'Добавить WhatsApp-номер',generate:'Сгенерировать QR',repair:'Сначала восстановите существующий webhook Connect в настройках интеграции. Новый webhook автоматически не создаётся.',confirm:'Нажатие «Сгенерировать QR» создаст новый WhatsApp-источник для всей компании или продолжит начатое подключение. После сканирования номер подключится к компании. Существующие номера не изменятся.',loading:'Подготавливаем подключение…',scan:'WhatsApp → Связанные устройства → Привязать устройство. Отсканируйте QR новым номером.',retry:'Повторить проверку',refresh:'Обновить QR',done:'Номер подключён.',close:'Закрыть',unknown:'Результат пока неизвестен. Повторите проверку: новый источник повторно не создаётся.',note:'Закрытие окна останавливает проверку, но не удаляет начатое подключение. Откройте «Добавить номер», чтобы продолжить.',storage:'Не удалось сохранить идентификатор подключения. Разрешите хранилище браузера и повторите.'},az:{title:'WhatsApp nömrəsi əlavə et',generate:'QR yarat',repair:'Əvvəlcə inteqrasiya ayarlarında mövcud Connect webhook bağlantısını bərpa edin. Yeni webhook avtomatik yaradılmır.',confirm:'«QR yarat» bütün şirkət üçün yeni WhatsApp mənbəyi yaradacaq və ya başlanmış bağlantını davam etdirəcək. QR skanından sonra nömrə şirkətə qoşulacaq. Mövcud nömrələr dəyişməyəcək.',loading:'Bağlantı hazırlanır…',scan:'WhatsApp → Əlaqəli cihazlar → Cihazı əlaqələndir. QR kodunu yeni nömrə ilə skan edin.',retry:'Yenidən yoxla',refresh:'QR yenilə',done:'Nömrə qoşuldu.',close:'Bağla',unknown:'Nəticə hələ məlum deyil. Yenidən yoxlayın: mənbə təkrar yaradılmır.',note:'Pəncərənin bağlanması yoxlamanı dayandırır, başlanmış bağlantını silmir. Davam etmək üçün «Nömrə əlavə et» seçin.',storage:'Bağlantı identifikatorunu saxlamaq mümkün olmadı. Brauzer yaddaşına icazə verin.'},en:{title:'Add WhatsApp number',generate:'Generate QR',repair:'Repair the existing Connect webhook in integration settings first. No replacement webhook is created automatically.',confirm:'Generate QR creates a new WhatsApp source for the whole company or resumes the pending connection. Scanning connects the number to this company. Existing numbers will not change.',loading:'Preparing connection…',scan:'WhatsApp → Linked devices → Link a device. Scan the QR with the new number.',retry:'Retry check',refresh:'Refresh QR',done:'Number connected.',close:'Close',unknown:'Outcome unknown. Retry the check: the source will not be created twice.',note:'Closing stops checking, but does not delete the pending connection. Open Add number to resume.',storage:'Cannot save the connection identifier. Allow browser storage and retry.'}};
  const identity=m=>JSON.stringify([m?.tenant_id,m?.telegram_id]);
  const make=(tag,text)=>{const n=document.createElement(tag);if(text)n.textContent=text;return n;};
  function close(){if(!current)return;const s=current;current=null;clearTimeout(s.timer);s.controller?.abort();s.dialog.close();s.dialog.remove();s.focus?.focus();}
  const valid=s=>current===s&&s.options.isCurrent?.()!==false&&(!window.tenantProfileSnapshot?.member||identity(window.tenantProfileSnapshot.member)===identity(s.member));
  function save(s){sessionStorage.setItem(s.key,JSON.stringify(s.intent));}
  async function run(s,action){
    if(!valid(s)){close();return;}if(s.busy)return;s.busy=true;clearTimeout(s.timer);s.retry.hidden=true;s.refresh.disabled=true;s.status.textContent=s.t.loading;
    const active=new AbortController();s.controller=active;const timeout=setTimeout(()=>active.abort(),20000);
    try{
      s.intent.lastAction=action;save(s);
      const body={action,request_id:s.intent.request_id,expected_tenant_id:s.member.tenant_id,expected_user_id:s.member.telegram_id,confirm_company_impact:true};
      const response=await fetch('/api/platform/integrations/connect',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:active.signal});
      const data=await response.json();if(!valid(s))return;if(!response.ok||data.success===false){const error=Error(data.error==='connect_pairing_webhook_repair_required'?s.t.repair:data.error||s.t.unknown);error.blocked=['integration_context_changed','connect_pairing_key_changed','connect_pairing_create_unverified','connect_pairing_idempotency_unavailable','connect_pairing_webhook_repair_required'].includes(data.code||data.error_code||data.error)||[401,403].includes(response.status);throw error;}
      const pairing=data.pairing;if(!pairing||!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(pairing.request_id||''))throw Error(s.t.unknown);s.intent.request_id=pairing.request_id;
      s.intent.state=pairing.state;s.intent.channel_id=pairing.channel_id||s.intent.channel_id;save(s);
      s.status.textContent=pairing.state==='complete'?s.t.done:pairing.state==='awaiting_scan'?s.t.scan:s.t.unknown;
      if(pairing.qr){const qr=String(pairing.qr);if(qr.length<=400000&&/^data:image\/(png|jpeg);base64,[A-Za-z0-9+/=]+$/.test(qr)){s.image.src=qr;s.image.hidden=false;}else if(window.tenantRenderPairingQR&&qr.length<=8000){const url=await window.tenantRenderPairingQR(qr);if(!valid(s))return;s.image.src=url;s.image.hidden=false;}else throw Error('QR renderer unavailable');}
      s.refresh.hidden=pairing.state!=='awaiting_scan';
      if(pairing.state==='complete'){sessionStorage.removeItem(s.key);s.image.hidden=true;s.retry.hidden=true;await s.options.onComplete?.(pairing);}
      else if(pairing.state==='connected'){s.next='pairing_complete';s.timer=setTimeout(()=>void run(s,s.next),0);}
      else if(['creating','awaiting_scan'].includes(pairing.state)){s.next='pairing_status';s.timer=setTimeout(()=>void run(s,s.next),2500);}
      else{s.next=pairing.channel_id?action:'pairing_start';s.retry.hidden=false;}
    }catch(error){if(valid(s)){s.status.textContent=error.name==='AbortError'?s.t.unknown:error.message;s.next=action;s.retry.hidden=!!error.blocked;if(error.blocked){s.refresh.hidden=true;s.image.hidden=true;s.image.removeAttribute('src');}}}
    finally{clearTimeout(timeout);s.busy=false;if(valid(s))s.refresh.disabled=false;}
  }
  function open(member,options={}){
    if(current){current.dialog.focus();return;}if(!member?.tenant_id||!member?.telegram_id||options.isCurrent?.()===false)return;
    const t=copy[window.tenantI18n?.language]||copy.az,key='tenant.connect.pairing:'+identity(member);
    const dialog=make('dialog');dialog.className='connect-pairing-dialog';dialog.setAttribute('aria-label',t.title);
    const title=make('h2',t.title),status=make('p',t.scan),image=make('img'),note=make('p',t.note),dismiss=make('button',t.close),retry=make('button',t.retry),refresh=make('button',t.refresh),generate=make('button',t.generate),disclosure=make('p',t.confirm);
    status.setAttribute('role','status');image.alt='WhatsApp QR';image.hidden=true;retry.hidden=true;refresh.hidden=true;for(const b of [dismiss,retry,refresh,generate])b.type='button';dialog.append(title,disclosure,status,image,note,generate,refresh,retry,dismiss);document.body.append(dialog);
    const s={dialog,status,image,retry,refresh,generate,t,key,intent:null,member:{...member},options,focus:document.activeElement,busy:false};current=s;
    dismiss.onclick=close;dialog.addEventListener('cancel',e=>{e.preventDefault();close();});retry.onclick=()=>void run(s,s.next||s.intent.lastAction);refresh.onclick=()=>{s.image.hidden=true;void run(s,'pairing_refresh');};dialog.showModal();
    generate.onclick=()=>{
      if(!valid(s)){close();return;}if(s.busy)return;
      try{let intent=JSON.parse(sessionStorage.getItem(key)||'null');if(intent&&(!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(intent.request_id)||!intent.consent))throw Error(t.storage);
        if(!intent){intent={request_id:crypto.randomUUID(),consent:true,lastAction:'pairing_start'};sessionStorage.setItem(key,JSON.stringify(intent));}
        s.intent=intent;generate.hidden=true;void run(s,intent.channel_id?'pairing_refresh':'pairing_start');
      }catch(error){s.status.textContent=error.message||t.storage;}
    };
  }
  document.addEventListener('tenant-auth-invalid',close);document.addEventListener('tenant-auth-pending',close);document.addEventListener('tenant-profile',()=>{if(current&&!valid(current))close();});document.addEventListener('tenant-integration-panel',e=>{if(!e.detail?.open)close();});window.addEventListener('pagehide',close);
  window.tenantConnectPairing={open,close};
})();
