/* Chat composer outside the page shell; durable UUIDs and stale-view guards. */
(()=>{
  const copy=(key,fallback)=>window.tenantI18n?.t(key)||fallback;
  function bind({api,identity,leadId,me,isCurrent,reload,provider='kommo'}){
    const button=document.getElementById('sendChat'),input=document.getElementById('chatText');
    const key='tenant-chat-send:'+identity+':'+leadId;
    try{if(sessionStorage.getItem(key))button.textContent=copy('checkSend','Vəziyyəti yoxla');}catch(e){button.disabled=true;}
    button.onclick=async()=>{
      if(button.disabled||!isCurrent())return;
      let pending;try{pending=JSON.parse(sessionStorage.getItem(key)||'null');}catch(e){alert('Göndərmə yaddaşı oxunmadı.');return;}
      const text=input.value.trim();if(!pending&&!text)return;
      if(pending&&text&&text!==pending.text){alert('Əvvəlki mesajın vəziyyəti yoxlanmalıdır. Yeni mesaj göndərilmədi.');return;}
      if(!pending){pending={lead_id:leadId,text,request_id:crypto.randomUUID(),expected_tenant_id:me.tenant_id,expected_user_id:me.telegram_id};try{sessionStorage.setItem(key,JSON.stringify(pending));}catch(e){alert('Göndərmə yaddaşı əlçatan deyil.');return;}}
      button.disabled=true;button.classList.add('loading');
      try{
        const sent=await api('/api/platform/crm/chat/send',{method:'POST',body:JSON.stringify(pending)});
        sessionStorage.removeItem(key);if(!isCurrent())return;
        if(input.value.trim()===pending.text)input.value='';button.textContent=copy('send','Göndər');
        alert(sent.manual_review?'Administrator əvvəlki mesajı təsdiqləyib; təkrar göndərilmədi.':copy('sendAccepted','Mesaj qəbul edildi. Tarixçədə çatdırılmanı yoxlayın.'));await reload();
      }catch(e){if(e.keepRequest===false)sessionStorage.removeItem(key);if(isCurrent()){button.textContent=e.keepRequest===false?copy('send','Göndər'):copy('checkSend','Vəziyyəti yoxla');alert(e.message);}}
      finally{button.disabled=false;button.classList.remove('loading');}
    };
    if(provider!=='connect')bindFile({identity,leadId,me,isCurrent,reload,input,button});
  }
  function bindFile({identity,leadId,me,isCurrent,reload,input,button}){
    const key='tenant-chat-file:'+identity+':'+leadId;
    const host=document.createElement('div');host.style.cssText='display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px';
    host.innerHTML='<input type="file" hidden accept="image/jpeg,image/png,image/webp,application/pdf,audio/mpeg,audio/ogg,video/mp4"><button class="outline" type="button">Fayl seç</button><span role="status" style="overflow-wrap:anywhere;min-width:0"></span><button class="outline" type="button" hidden>Faylı göndər</button>';
    input.parentElement.after(host);
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
        if(input.value.trim()===pending.text)input.value='';file=null;picker.value='';send.hidden=true;await reload();
      }catch(error){if(isCurrent())status.textContent=error.message;}
      finally{busy=false;button.disabled=false;choose.disabled=false;send.disabled=false;input.disabled=false;}
    };
  }
  window.tenantChatSend={bind};
})();
