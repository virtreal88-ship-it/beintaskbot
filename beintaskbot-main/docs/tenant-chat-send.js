/* Chat composer outside the page shell; durable UUIDs and stale-view guards. */
(()=>{
  const copy=(key,fallback)=>window.tenantI18n?.t(key)||fallback;
  const quoteCopy=state=>({ru:{pending:'Принято в очередь. Доставка ещё не подтверждена.',retrying:'Подготовка отправки. Доставка ещё не подтверждена.',unknown:'Результат требует проверки. Повторная отправка отключена.',failed:'Отправка не подтверждена. Проверьте статус перед новым действием.',sent:'Провайдер принял сообщение. Доставка клиенту ещё не подтверждена.',delivered:'Доставка подтверждена.',read:'Сообщение прочитано.'},az:{pending:'Növbəyə qəbul edildi. Çatdırılma təsdiqlənməyib.',retrying:'Göndərmə hazırlanır. Çatdırılma təsdiqlənməyib.',unknown:'Nəticə yoxlanmalıdır. Təkrar göndərmə söndürülüb.',failed:'Göndərmə təsdiqlənməyib. Yeni əməliyyatdan əvvəl vəziyyəti yoxlayın.',sent:'Provayder qəbul etdi. Müştəriyə çatdırılma təsdiqlənməyib.',delivered:'Çatdırılma təsdiqləndi.',read:'Mesaj oxunub.'},en:{pending:'Queued. Delivery is not confirmed.',retrying:'Preparing send. Delivery is not confirmed.',unknown:'Outcome needs checking. Resend disabled.',failed:'Send not confirmed. Check status before another action.',sent:'Provider accepted. Customer delivery is not confirmed.',delivered:'Delivery confirmed.',read:'Message read.'}}[window.tenantI18n?.language]||{})[state]||'Delivery is not confirmed.';
  const language=()=>['ru','az','en'].includes(window.tenantI18n?.language)?window.tenantI18n.language:'az';
  const diagnostic=sent=>{
    const lang=language();
    const reasons={
      quoted_message_unavailable:{ru:'Не удалось однозначно найти исходное сообщение у провайдера. Ответ не отправлялся.',az:'Provayderdə ilkin mesaj dəqiq tapılmadı. Cavab göndərilməyib.',en:'The original message could not be found unambiguously at the provider. The reply was not sent.'},
      quoted_source_scope_conflict:{ru:'Источник цитаты не подтверждён. Ответ не отправлялся.',az:'İstinadın mənbəyi təsdiqlənmədi. Cavab göndərilməyib.',en:'The quoted source was not verified. The reply was not sent.'},
      quoted_source_lookup_unavailable:{ru:'Не удалось проверить источник цитаты. Ответ не отправлялся.',az:'İstinadın mənbəyini yoxlamaq mümkün olmadı. Cavab göndərilməyib.',en:'The quoted source could not be checked. The reply was not sent.'},
      quoted_provider_unavailable:{ru:'Отправка с цитатой недоступна. Ответ не отправлялся.',az:'İstinadla göndərmə əlçatan deyil. Cavab göndərilməyib.',en:'Quoted sending is unavailable. The reply was not sent.'}
    };
    if(sent.delivery_state==='failed'&&sent.retry_safe===true&&!sent.provider_message_id&&reasons[sent.error_code])return reasons[sent.error_code][lang];
    if(sent.delivery_state==='failed'&&sent.provider_message_id)return {ru:'Провайдер сообщил об ошибке доставки. Повторная отправка отключена.',az:'Provayder çatdırılma xətası bildirdi. Təkrar göndərmə söndürülüb.',en:'The provider reported a delivery failure. Resend is disabled.'}[lang];
    return quoteCopy(sent.delivery_state||'unknown');
  };
  let activeReceiptDiagnostics=()=>{},activePrimaryObserver=null;
  function bind({api,identity,leadId,me,isCurrent,reload,provider='kommo',channelId='',onAccepted=()=>{},getQuote=()=>null,onPending=()=>{},onDiscard=()=>{},isQuoteCurrent=()=>true}){
    activeReceiptDiagnostics=()=>{};
    activePrimaryObserver?.disconnect();activePrimaryObserver=null;
    const button=document.getElementById('sendChat'),input=document.getElementById('chatText'),primary=document.getElementById('tcComposerPrimary');
    if(primary){
      button.dataset.composerDelegate='true';button.setAttribute('aria-hidden','true');button.tabIndex=-1;
      const refreshPrimary=()=>{const pending=button.textContent.trim()===(window.tenantI18n?.t?.('checkSend')||'Vəziyyəti yoxla'),hasText=!!input.value.trim(),label=pending?copy('checkSend','Vəziyyəti yoxla'):copy('send','Göndər');primary.textContent='↑';primary.title=label;primary.setAttribute('aria-label',label);primary.disabled=button.disabled||input.disabled||(!hasText&&!pending);primary.classList.toggle('loading',button.classList.contains('loading'));return !primary.disabled;};
      primary.onclick=()=>{if(refreshPrimary())button.click();};input.addEventListener('input',refreshPrimary);
      if(window.MutationObserver){const observer=new MutationObserver(()=>{if(!button.isConnected||!primary.isConnected){observer.disconnect();if(activePrimaryObserver===observer)activePrimaryObserver=null;return;}refreshPrimary();});activePrimaryObserver=observer;observer.observe(button,{attributes:true,childList:true,characterData:true,subtree:true});observer.observe(input,{attributes:true,attributeFilter:['disabled']});}
      refreshPrimary();
    }
    const showAccepted=text=>{
      if(!isCurrent()||!input.isConnected)return;
      let status=document.getElementById('tenantChatSendStatus');
      if(!status){status=document.createElement('p');status.id='tenantChatSendStatus';status.className='sub';status.setAttribute('role','status');status.setAttribute('aria-live','polite');input.before(status);}
      status.textContent=text;
    };
    const key='tenant-chat-send:'+identity+':'+leadId;
    const scoped=provider==='connect'&&String(me.tenant_id)==='e09a5c24-093f-4141-992d-532052e00c90'&&String(channelId)==='96863d75-d272-4c93-a25e-ffb2b16ecae3';
    const readStatus=(path,requestId)=>api('/api/platform/crm/chat/'+path+'?'+new URLSearchParams({
      lead_id:String(leadId),expected_tenant_id:String(me.tenant_id),expected_user_id:String(me.telegram_id),
      ...(requestId?{request_id:requestId}:{})
    }),{method:'GET'});
    const readReceipt=requestId=>readStatus('quote-receipt',requestId);
    async function checkPending(pending){
      const saved=await readStatus('send-status',pending.request_id);
      if(pending.quote_message_id&&provider==='connect'&&['sending','unknown'].includes(saved.state)){
        return readReceipt(pending.request_id);
      }
      return saved;
    }
    function presentPending(pending){
      let saved;
      try{saved=JSON.parse(sessionStorage.getItem(key)||'null');}catch{}
      if(saved?.request_id!==pending.request_id)return;
      onPending(saved,()=>{
        try{
          const current=JSON.parse(sessionStorage.getItem(key)||'null');
          if(current?.request_id===pending.request_id){
            sessionStorage.setItem(key,JSON.stringify({...current,quote_preview_dismissed:true}));
          }
        }catch{}
      });
    }
    function forgetPending(requestId){
      try{
        if(JSON.parse(sessionStorage.getItem(key)||'null')?.request_id!==requestId)return false;
        sessionStorage.removeItem(key);
        return true;
      }catch{return false;}
    }
    const checkCopy=state=>({
      ru:{blocked:'Предыдущая попытка завершилась без отправки. Теперь можно отправить ответ.',pending:'Статус предыдущей отправки пока не подтверждён. Черновик сохранён.'},
      az:{blocked:'Əvvəlki cəhddə mesaj göndərilmədi. İndi cavab göndərə bilərsiniz.',pending:'Əvvəlki göndərişin statusu hələ təsdiqlənməyib. Qaralama saxlanıldı.'},
      en:{blocked:'The previous attempt did not send. You can now send a reply.',pending:'The previous send is still unconfirmed. Your draft is saved.'}
    }[language()][state]);
    let receiptOutput;
    const showDiagnostic=sent=>{
      const text=diagnostic(sent);
      if(receiptOutput){const phase=['reserved','unknown','accepted','not_sent','legacy','unavailable'].includes(sent.delivery_phase)?sent.delivery_phase:'unavailable';const code=['quoted_message_unavailable','quoted_source_scope_conflict','quoted_source_lookup_unavailable','quoted_provider_unavailable'].includes(sent.error_code)?sent.error_code:'';receiptOutput.textContent=text+' · '+phase+(code?' · '+code:'')+(/^[0-9a-f-]{36}$/i.test(sent.request_id||'')?' · UUID: '+sent.request_id:'');}
      return text;
    };
    if(scoped){
      const labels={ru:{title:'Диагностика ответа',check:'Проверить статус ответа'},az:{title:'Cavab diaqnostikası',check:'Cavabın statusunu yoxla'},en:{title:'Reply diagnostics',check:'Check reply status'}}[language()];
      const details=document.createElement('details'),summary=document.createElement('summary'),panel=document.createElement('div'),check=document.createElement('button');details.id='quoteReceiptDiagnostic';details.className='operator-quote-receipt-diagnostic';summary.textContent=labels.title;panel.className='operator-quote-receipt-diagnostic-body';panel.style.cssText='display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:8px 0';check.type='button';check.className='outline';check.textContent=labels.check;check.dataset.quoteReceiptCheck='true';receiptOutput=document.createElement('output');receiptOutput.setAttribute('role','status');receiptOutput.style.cssText='overflow-wrap:anywhere;min-width:0';details.append(summary,panel);panel.append(check,receiptOutput);activeReceiptDiagnostics=container=>{if(container&&isCurrent())container.append(details);};
      check.onclick=async()=>{if(check.disabled||!isCurrent())return;check.disabled=true;try{let saved;try{saved=JSON.parse(sessionStorage.getItem(key)||'null');}catch{}const result=await readReceipt(saved?.quote_message_id?saved.request_id:null);if(isCurrent())showDiagnostic(result);}catch(error){if(isCurrent())receiptOutput.textContent=error.message;}finally{check.disabled=false;}};
    }

    try{const saved=JSON.parse(sessionStorage.getItem(key)||'null');if(saved){button.textContent=copy('checkSend','Vəziyyəti yoxla');if(!saved.quote_message_id||isQuoteCurrent(saved))presentPending(saved);}}catch(e){button.disabled=true;}
    button.onclick=async()=>{
      if(button.disabled||!isCurrent())return;
      let pending;try{pending=JSON.parse(sessionStorage.getItem(key)||'null');}catch(e){alert('Göndərmə yaddaşı oxunmadı.');return;}
      if(!pending&&button.textContent.trim()===copy('checkSend','Vəziyyəti yoxla')){
        onDiscard();button.textContent=copy('send','Göndər');return;
      }
      const checking=Boolean(pending);
      const text=input.value.trim();if(!pending&&!text)return;
      const quote=getQuote();
      if(pending?.quote_message_id&&!isQuoteCurrent(pending)){alert('Əvvəlki mesaj başqa dialoqa aiddir.');return;}
      if(!pending){pending={lead_id:leadId,text,request_id:crypto.randomUUID(),expected_tenant_id:me.tenant_id,expected_user_id:me.telegram_id,...(quote||{})};try{sessionStorage.setItem(key,JSON.stringify(pending));}catch(e){alert('Göndərmə yaddaşı əlçatan deyil.');return;}}
      button.disabled=true;button.classList.add('loading');
      if(!checking&&pending.quote_message_id)presentPending(pending);
      try{
        const sent=checking?await checkPending(pending):await api('/api/platform/crm/chat/send',{method:'POST',body:JSON.stringify(pending)});
        if(checking&&sent.state==='blocked'&&sent.quote_protocol!==true){
          if(forgetPending(pending.request_id)&&isCurrent()){
            onDiscard();button.textContent=copy('send','Göndər');showAccepted(checkCopy('blocked'));
          }
          return;
        }
        if(checking&&sent.state!=='accepted'&&sent.quote_protocol!==true){
          showAccepted(checkCopy('pending'));
          return;
        }
        if(sent.quote_protocol===true)showDiagnostic({...sent,request_id:sent.request_id||pending.request_id});
        if(pending.quote_message_id&&sent.quote_protocol===true&&sent.provider_accepted!==true&&sent.manual_review!==true){
          if(sent.delivery_state==='failed'&&sent.retry_safe===true&&!sent.provider_message_id){if(forgetPending(pending.request_id)&&isCurrent()){onDiscard();button.textContent=copy('send','Göndər');}}
          else if(isCurrent()){presentPending(pending);button.textContent=copy('checkSend','Vəziyyəti yoxla');}
          if(isCurrent()){alert(diagnostic(sent));await reload();}return;
        }
        if(!forgetPending(pending.request_id)||!isCurrent())return;
        onAccepted(pending.text);
        if(input.value.trim()===pending.text){input.value='';input.dispatchEvent(new Event('input',{bubbles:true}));}button.textContent=copy('send','Göndər');
        showAccepted(sent.manual_review?'Administrator əvvəlki mesajı təsdiqləyib; təkrar göndərilmədi.':pending.quote_message_id&&sent.quote_protocol===true?quoteCopy(sent.delivered===true?(sent.delivery_state==='read'?'read':'delivered'):'sent'):copy('sendAccepted','Mesaj qəbul edildi. Tarixçədə çatdırılmanı yoxlayın.'));await reload();
      }catch(e){const discard=!checking&&e.keepRequest===false&&!pending?.quote_message_id;if(discard){if(forgetPending(pending.request_id)&&isCurrent())onDiscard();}else if(isCurrent())presentPending(pending);if(isCurrent()){button.textContent=discard?copy('send','Göndər'):copy('checkSend','Vəziyyəti yoxla');alert(e.message);}}
      finally{button.disabled=false;button.classList.remove('loading');}
    };
    if(provider!=='connect')bindFile({identity,leadId,me,isCurrent,reload,input,button});
  }
  function bindFile({identity,leadId,me,isCurrent,reload,input,button}){
    const key='tenant-chat-file:'+identity+':'+leadId;
    const host=document.createElement('div');host.className='tc-legacy-composer-attachments';host.style.cssText='display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0;min-width:0';
    host.innerHTML='<input type="file" hidden accept="image/jpeg,image/png,image/webp,application/pdf,audio/mpeg,audio/ogg,video/mp4"><button class="outline" type="button">Fayl seç</button><span role="status" style="overflow-wrap:anywhere;min-width:0"></span><button class="outline" type="button" hidden>Faylı göndər</button>';
    const actions=input.closest('.tc-legacy-composer')?.querySelector('.operator-composer-actions'),primary=actions?.querySelector('#tcComposerPrimary');if(actions)actions.insertBefore(host,primary||null);else input.parentElement.after(host);
    const [picker,choose,status,send]=host.children;let file=null,busy=false;
    try{if(sessionStorage.getItem(key))status.textContent='Əvvəlki faylın nəticəsi yoxlanmalıdır. Eyni faylı seçin.';}catch(e){choose.disabled=true;}
    choose.onclick=()=>picker.click();picker.onchange=()=>{
      file=picker.files?.[0]||null;send.hidden=!file;
      if(file&&file.size>5*1024*1024){file=null;picker.value='';send.hidden=true;status.textContent='Maksimum 5 MB.';return;}
      status.textContent=file?.name||'';
    };
    send.onclick=async()=>{
      if(!file||busy||button.disabled||!isCurrent())return;
      busy=true;button.disabled=true;choose.disabled=true;send.disabled=true;input.disabled=true;status.textContent='Fayl hazırlanır…';
      let pending;
      try{
        const bytes=await file.arrayBuffer();if(!isCurrent())return;
        const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
        if(!isCurrent())return;
        pending=JSON.parse(sessionStorage.getItem(key)||'null');
        if(pending&&(pending.sha256!==hash||pending.name!==file.name||pending.mime!==file.type)){throw new Error('Əvvəlki faylın nəticəsi yoxlanmalıdır. Eyni faylı seçin.');}
        if(pending&&input.value.trim()&&input.value.trim()!==pending.text){throw new Error('Əvvəlki faylın nəticəsi yoxlanmalıdır. Mesaj mətni dəyişdirilmədi.');}
        if(!pending){pending={request_id:crypto.randomUUID(),lead_id:leadId,text:input.value.trim(),expected_tenant_id:me.tenant_id,expected_user_id:me.telegram_id,name:file.name,mime:file.type,sha256:hash};sessionStorage.setItem(key,JSON.stringify(pending));}
        const data=new FormData();data.append('command',JSON.stringify(pending));data.append('file',file);
        status.textContent='Yüklənir və göndərilir…';
        // No automatic retry or abort deadline: an interrupted write may be accepted.
        const response=await fetch('/api/platform/crm/chat/send-file',{method:'POST',credentials:'same-origin',body:data});
        const result=await response.json();
        if(!response.ok||!result.success){if(result.keep_request===false)sessionStorage.removeItem(key);throw new Error(result.error||'Nəticəni yoxlayın.');}
        sessionStorage.removeItem(key);if(!isCurrent())return;
        status.textContent='Kommo faylı qəbul etdi. Tarixçədə çatdırılmanı yoxlayın.';
        if(input.value.trim()===pending.text){input.value='';input.dispatchEvent(new Event('input',{bubbles:true}));}file=null;picker.value='';send.hidden=true;await reload();
      }catch(error){if(isCurrent())status.textContent=error.message;}
      finally{busy=false;button.disabled=false;choose.disabled=false;send.disabled=false;input.disabled=false;}
    };
  }
  window.tenantChatSend={bind,renderDiagnostics:container=>activeReceiptDiagnostics(container),clearDiagnostics:()=>{activeReceiptDiagnostics=()=>{};}};
})();
