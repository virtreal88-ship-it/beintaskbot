/* Explicit new-number pairing. Stable intent ID survives close/reload; no background provisioning. */
(()=>{
  let current;
  const copy={ru:{title:'Добавить WhatsApp-номер',generate:'Сгенерировать QR',repair:'Сначала восстановите существующий webhook Connect в настройках интеграции. Новый webhook автоматически не создаётся.',confirm:'Нажатие «Сгенерировать QR» создаст новый WhatsApp-источник для всей компании или продолжит начатое подключение. После сканирования номер подключится к компании. Существующие номера не изменятся.',loading:'Подготавливаем подключение…',scan:'WhatsApp → Связанные устройства → Привязать устройство. Отсканируйте QR новым номером.',retry:'Повторить проверку',refresh:'Обновить QR',done:'Номер подключён.',close:'Закрыть',unknown:'Результат пока неизвестен. Повторите проверку: новый источник повторно не создаётся.',note:'Закрытие окна останавливает проверку, но не удаляет начатое подключение. Откройте «Добавить номер», чтобы продолжить.',storage:'Не удалось сохранить идентификатор подключения. Разрешите хранилище браузера и повторите.'},az:{title:'WhatsApp nömrəsi əlavə et',generate:'QR yarat',repair:'Əvvəlcə inteqrasiya ayarlarında mövcud Connect webhook bağlantısını bərpa edin. Yeni webhook avtomatik yaradılmır.',confirm:'«QR yarat» bütün şirkət üçün yeni WhatsApp mənbəyi yaradacaq və ya başlanmış bağlantını davam etdirəcək. QR skanından sonra nömrə şirkətə qoşulacaq. Mövcud nömrələr dəyişməyəcək.',loading:'Bağlantı hazırlanır…',scan:'WhatsApp → Əlaqəli cihazlar → Cihazı əlaqələndir. QR kodunu yeni nömrə ilə skan edin.',retry:'Yenidən yoxla',refresh:'QR yenilə',done:'Nömrə qoşuldu.',close:'Bağla',unknown:'Nəticə hələ məlum deyil. Yenidən yoxlayın: mənbə təkrar yaradılmır.',note:'Pəncərənin bağlanması yoxlamanı dayandırır, başlanmış bağlantını silmir. Davam etmək üçün «Nömrə əlavə et» seçin.',storage:'Bağlantı identifikatorunu saxlamaq mümkün olmadı. Brauzer yaddaşına icazə verin.'},en:{title:'Add WhatsApp number',generate:'Generate QR',repair:'Repair the existing Connect webhook in integration settings first. No replacement webhook is created automatically.',confirm:'Generate QR creates a new WhatsApp source for the whole company or resumes the pending connection. Scanning connects the number to this company. Existing numbers will not change.',loading:'Preparing connection…',scan:'WhatsApp → Linked devices → Link a device. Scan the QR with the new number.',retry:'Retry check',refresh:'Refresh QR',done:'Number connected.',close:'Close',unknown:'Outcome unknown. Retry the check: the source will not be created twice.',note:'Closing stops checking, but does not delete the pending connection. Open Add number to resume.',storage:'Cannot save the connection identifier. Allow browser storage and retry.'}};
  const layoutCopy={ru:{subtitle:'Подключение к компании',steps:['Сгенерируйте QR-код для нового номера.','Откройте WhatsApp → Связанные устройства → Привязать устройство.','Отсканируйте код. После подключения номер появится в списке.'],close:'Закрыть окно'},az:{subtitle:'Şirkətə qoşulma',steps:['Yeni nömrə üçün QR kodunu yaradın.','WhatsApp → Əlaqəli cihazlar → Cihazı əlaqələndir bölməsini açın.','Kodu skan edin. Qoşulduqdan sonra nömrə siyahıda görünəcək.'],close:'Pəncərəni bağla'},en:{subtitle:'Connect to your company',steps:['Generate a QR code for the new number.','Open WhatsApp → Linked devices → Link a device.','Scan the code. The connected number will appear in the list.'],close:'Close dialog'}};
  function ensureStyle(){
    if(document.getElementById('connectPairingStyle'))return;
    const style=document.createElement('style');style.id='connectPairingStyle';style.textContent=`
      dialog.connect-pairing-dialog{--pair-bg:var(--app-surface,var(--card,#fff));--pair-ink:var(--app-ink,var(--ink,#18181b));--pair-muted:var(--app-muted,var(--muted,#64748b));--pair-line:var(--app-line,var(--line,#e4e4e7));--pair-soft:var(--app-raised,#f4f4f5);--pair-action:var(--app-accent-hover,var(--brand-deep,#0369a1));--pair-action-ink:#fff;box-sizing:border-box;width:min(520px,calc(100vw - 32px));max-width:none;max-height:calc(100dvh - 32px);margin:auto;padding:0;border:1px solid var(--pair-line);border-radius:16px;background:var(--pair-bg);color:var(--pair-ink);box-shadow:0 24px 80px #0003;font:14px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;overflow:hidden;text-align:left}
      html[data-app-theme=dark] dialog.connect-pairing-dialog{--pair-soft:var(--app-raised,#27272a);--pair-action:var(--app-accent,var(--brand,#38bdf8));--pair-action-ink:#082f49}
      dialog.connect-pairing-dialog[open]{display:flex;flex-direction:column}dialog.connect-pairing-dialog::backdrop{background:#0f172a80}
      dialog.connect-pairing-dialog *,dialog.connect-pairing-dialog *:before{box-sizing:border-box}dialog.connect-pairing-dialog [hidden]{display:none!important}
      dialog.connect-pairing-dialog .pair-header{display:flex;align-items:flex-start;gap:12px;padding:24px 24px 20px;border-bottom:1px solid var(--pair-line);flex:none}
      dialog.connect-pairing-dialog .pair-mark{display:grid;place-items:center;width:36px;height:36px;border-radius:10px;background:var(--pair-soft);color:var(--pair-action);flex:none}
      dialog.connect-pairing-dialog .pair-heading{flex:1;min-width:0}dialog.connect-pairing-dialog h2{margin:0;color:var(--pair-ink);letter-spacing:-.02em;font-family:inherit;font-size:21px;font-weight:600;line-height:1.3}
      dialog.connect-pairing-dialog .pair-subtitle{margin:5px 0 0;color:var(--pair-muted);font-size:13px}
      dialog.connect-pairing-dialog .pair-body{padding:22px 24px;overflow:auto;min-height:0;overscroll-behavior:contain}
      dialog.connect-pairing-dialog .pair-steps{display:grid;gap:17px;padding:0;margin:0 0 22px;list-style:none;counter-reset:pair-step}
      dialog.connect-pairing-dialog .pair-steps li{display:flex;gap:12px;align-items:flex-start;margin:0;color:var(--pair-ink)}
      dialog.connect-pairing-dialog .pair-steps li:before{counter-increment:pair-step;content:counter(pair-step);display:grid;place-items:center;flex:none;width:26px;height:26px;border-radius:50%;background:var(--pair-soft);color:var(--pair-ink);font-size:12px;font-weight:600}
      dialog.connect-pairing-dialog .pair-disclosure{margin:0;padding:14px 16px;background:var(--pair-soft);border-radius:8px;color:var(--pair-ink);font-size:13px;line-height:1.55}
      dialog.connect-pairing-dialog .pair-note{margin:14px 0 0;color:var(--pair-muted);font-size:12px;line-height:1.55}
      dialog.connect-pairing-dialog .pair-status{margin:0 0 18px;padding:12px 14px;border:1px solid var(--pair-line);border-radius:8px;background:var(--pair-soft);overflow-wrap:anywhere}
      dialog.connect-pairing-dialog img{display:block;width:min(280px,100%);height:auto;padding:12px;margin:0 auto 22px;border:1px solid var(--pair-line);border-radius:12px;background:white}
      dialog.connect-pairing-dialog .pair-footer{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:18px 24px;border-top:1px solid var(--pair-line);flex:none;background:var(--pair-bg)}
      dialog.connect-pairing-dialog .pair-actions{display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap}
      dialog.connect-pairing-dialog button{appearance:none;display:inline-flex;align-items:center;justify-content:center;min-height:40px;padding:9px 14px;margin:0;border:1px solid var(--pair-line);border-radius:8px;background:var(--pair-bg);color:var(--pair-ink);font:500 14px/1.3 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;cursor:pointer;box-shadow:0 1px 2px #00000006;transition:filter .12s}
      dialog.connect-pairing-dialog button:hover{filter:brightness(.95)}dialog.connect-pairing-dialog button:focus-visible{outline:2px solid var(--pair-action);outline-offset:3px}dialog.connect-pairing-dialog button:disabled{opacity:.55;cursor:wait}
      dialog.connect-pairing-dialog button.pair-primary{background:var(--pair-action);border-color:var(--pair-action);color:var(--pair-action-ink);font-weight:600}
      dialog.connect-pairing-dialog button.pair-close{width:36px;min-height:36px;padding:0;margin:-6px -8px 0 0;border:0;box-shadow:none;background:transparent;color:var(--pair-muted);flex:none}
      @media(max-width:480px){dialog.connect-pairing-dialog{width:calc(100vw - 20px);max-height:calc(100dvh - 20px);border-radius:12px}dialog.connect-pairing-dialog .pair-header{padding:20px 18px 16px}dialog.connect-pairing-dialog h2{font-size:19px}dialog.connect-pairing-dialog .pair-body{padding:20px 18px}dialog.connect-pairing-dialog .pair-footer{padding:14px 18px;flex-direction:column-reverse;align-items:stretch}dialog.connect-pairing-dialog .pair-actions{flex-direction:column}dialog.connect-pairing-dialog button{min-height:44px}dialog.connect-pairing-dialog button.pair-close{width:44px;min-height:44px}}
      @media(prefers-reduced-motion:reduce){dialog.connect-pairing-dialog button{transition:none}}
    `;document.head.append(style);
  }
  const identity=m=>JSON.stringify([m?.tenant_id,m?.telegram_id]);
  const make=(tag,text)=>{const n=document.createElement(tag);if(text)n.textContent=text;return n;};
  function close(){if(!current)return;const s=current;current=null;clearTimeout(s.timer);s.controller?.abort();s.dialog.close();s.dialog.remove();s.focus?.focus();}
  const valid=s=>current===s&&s.options.isCurrent?.()!==false&&(!window.tenantProfileSnapshot?.member||identity(window.tenantProfileSnapshot.member)===identity(s.member));
  function save(s){sessionStorage.setItem(s.key,JSON.stringify(s.intent));}
  async function run(s,action){
    if(!valid(s)){close();return;}if(s.busy)return;s.busy=true;clearTimeout(s.timer);s.retry.hidden=true;s.refresh.disabled=true;s.status.hidden=false;s.status.textContent=s.t.loading;
    const active=new AbortController();s.controller=active;const timeout=setTimeout(()=>active.abort(),20000);
    try{
      s.intent.lastAction=action;save(s);
      const body={action,request_id:s.intent.request_id,expected_tenant_id:s.member.tenant_id,expected_user_id:s.member.telegram_id,confirm_company_impact:true};
      const response=await fetch('/api/platform/integrations/connect',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:active.signal});
      const data=await response.json();if(!valid(s))return;if(!response.ok||data.success===false){const error=Error(data.error==='connect_pairing_webhook_repair_required'?s.t.repair:data.error||s.t.unknown);error.blocked=['integration_context_changed','connect_pairing_key_changed','connect_pairing_create_unverified','connect_pairing_idempotency_unavailable','connect_pairing_webhook_repair_required'].includes(data.code||data.error_code||data.error)||[401,403].includes(response.status);throw error;}
      const pairing=data.pairing;if(!pairing||!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(pairing.request_id||''))throw Error(s.t.unknown);s.intent.request_id=pairing.request_id;
      s.intent.state=pairing.state;s.intent.channel_id=pairing.channel_id||s.intent.channel_id;save(s);
      s.status.textContent=pairing.state==='complete'?s.t.done:pairing.state==='awaiting_scan'?s.t.scan:s.t.unknown;
      if(pairing.qr){const qr=String(pairing.qr);if(qr.length<=400000&&/^data:image\/(png|jpeg);base64,[A-Za-z0-9+/=]+$/.test(qr)){s.image.src=qr;s.image.hidden=false;s.instructions.hidden=true;s.body.scrollTop=0;}else if(window.tenantRenderPairingQR&&qr.length<=8000){const url=await window.tenantRenderPairingQR(qr);if(!valid(s))return;s.image.src=url;s.image.hidden=false;s.instructions.hidden=true;s.body.scrollTop=0;}else throw Error('QR renderer unavailable');}
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
    ensureStyle();const l=layoutCopy[window.tenantI18n?.language]||layoutCopy.az;
    const dialog=make('dialog');dialog.className='connect-pairing-dialog';dialog.setAttribute('aria-label',t.title);
    const title=make('h2',t.title),status=make('p'),image=make('img'),note=make('p',t.note),dismiss=make('button',t.close),retry=make('button',t.retry),refresh=make('button',t.refresh),generate=make('button',t.generate),disclosure=make('p',t.confirm);
    const header=make('header'),mark=make('span'),heading=make('div'),subtitle=make('p',l.subtitle),topClose=make('button'),body=make('div'),instructions=make('div'),steps=make('ol'),footer=make('footer'),actions=make('div');
    mark.className='pair-mark';mark.setAttribute('aria-hidden','true');mark.innerHTML='<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M20 11.5a8 8 0 0 1-8 8 8 8 0 0 1-4-.9L4 20l1.4-4a8 8 0 1 1 14.6-4.5Z"/><path d="m8 7 2 3-1 1c1 2 2 3 4 4l1-1 3 2"/></svg>';
    header.className='pair-header';heading.className='pair-heading';subtitle.className='pair-subtitle';topClose.className='pair-close';topClose.setAttribute('aria-label',l.close);topClose.innerHTML='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>';
    body.className='pair-body';steps.className='pair-steps';l.steps.forEach(text=>steps.append(make('li',text)));disclosure.className='pair-disclosure';note.className='pair-note';status.className='pair-status';footer.className='pair-footer';actions.className='pair-actions';generate.className='pair-primary';retry.className='pair-primary';
    status.setAttribute('role','status');status.hidden=true;image.alt='WhatsApp QR';image.hidden=true;retry.hidden=true;refresh.hidden=true;for(const b of [dismiss,retry,refresh,generate,topClose])b.type='button';
    heading.append(title,subtitle);header.append(mark,heading,topClose);instructions.append(steps,disclosure);body.append(status,image,instructions,note);actions.append(generate,refresh,retry);footer.append(dismiss,actions);dialog.append(header,body,footer);document.body.append(dialog);
    const s={dialog,body,instructions,status,image,retry,refresh,generate,t,key,intent:null,member:{...member},options,focus:document.activeElement,busy:false};current=s;
    topClose.onclick=close;dismiss.onclick=close;dialog.addEventListener('cancel',e=>{e.preventDefault();close();});retry.onclick=()=>void run(s,s.next||s.intent.lastAction);refresh.onclick=()=>{s.image.hidden=true;void run(s,'pairing_refresh');};dialog.showModal();
    generate.onclick=()=>{
      if(!valid(s)){close();return;}if(s.busy)return;
      try{let intent=JSON.parse(sessionStorage.getItem(key)||'null');if(intent&&(!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(intent.request_id)||!intent.consent))throw Error(t.storage);
        if(!intent){intent={request_id:crypto.randomUUID(),consent:true,lastAction:'pairing_start'};sessionStorage.setItem(key,JSON.stringify(intent));}
        s.intent=intent;generate.hidden=true;void run(s,intent.channel_id?'pairing_refresh':'pairing_start');
      }catch(error){s.status.hidden=false;s.status.textContent=error.message||t.storage;}
    };
  }
  document.addEventListener('tenant-auth-invalid',close);document.addEventListener('tenant-auth-pending',close);document.addEventListener('tenant-profile',()=>{if(current&&!valid(current))close();});document.addEventListener('tenant-integration-panel',e=>{if(!e.detail?.open)close();});window.addEventListener('pagehide',close);
  window.tenantConnectPairing={open,close};
})();
