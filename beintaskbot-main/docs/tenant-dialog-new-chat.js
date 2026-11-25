(() => {
  'use strict';
  const root=document.getElementById('operatorDialogs');
  if(!root)return;
  const copy={
    ru:{newChat:'Новый чат',title:'Новое сообщение',source:'WhatsApp-источник',phone:'Номер клиента',phoneHint:'Международный формат, например +994501234567',text:'Сообщение',textHint:'Напишите сообщение клиенту',send:'Отправить',check:'Проверить результат',cancel:'Отмена',loading:'Загружаю источники…',sending:'Отправляю…',openExisting:'Чат с этим номером уже есть в выбранном источнике. Открою его и оставлю текст в поле ввода.',sent:'Сообщение отправлено.',noSource:'Нет доступных WhatsApp-источников с правом отправки.',unknown:'Статус отправки неизвестен. Не создавайте новый запрос. Повторная проверка использует тот же запрос и не отправляет сообщение повторно.',error:'Не удалось отправить сообщение.',close:'Закрыть окно'},
    az:{newChat:'Yeni çat',title:'Yeni mesaj',source:'WhatsApp mənbəyi',phone:'Müştərinin nömrəsi',phoneHint:'Beynəlxalq format, məsələn +994501234567',text:'Mesaj',textHint:'Müştəriyə mesaj yazın',send:'Göndər',check:'Nəticəni yoxla',cancel:'Ləğv et',loading:'Mənbələr yüklənir…',sending:'Göndərilir…',openExisting:'Seçilmiş mənbədə bu nömrə ilə çat artıq var. Çatı açıb mətni yazı sahəsində saxlayacağam.',sent:'Mesaj göndərildi.',noSource:'Göndərmə hüququ olan WhatsApp mənbəyi yoxdur.',unknown:'Göndərmə statusu məlum deyil. Yeni sorğu yaratmayın. Təkrar yoxlama eyni sorğudan istifadə edir və mesajı yenidən göndərmir.',error:'Mesaj göndərilmədi.',close:'Pəncərəni bağla'},
    en:{newChat:'New chat',title:'New message',source:'WhatsApp source',phone:'Customer number',phoneHint:'International format, for example +994501234567',text:'Message',textHint:'Write a message to the customer',send:'Send',check:'Check result',cancel:'Cancel',loading:'Loading sources…',sending:'Sending…',openExisting:'A chat with this number already exists on the selected source. Opening it and keeping the text in the composer.',sent:'Message sent.',noSource:'No WhatsApp source with send permission is available.',unknown:'Send status is unknown. Do not create a new request. Checking again uses the same request and does not send another message.',error:'Could not send the message.',close:'Close dialog'}
  };
  const language=()=>copy[window.tenantI18n?.language]||copy.az;
  const identity=()=>window.tenantOperator?.member;
  const keyFor=member=>`tenant-new-chat:${member?.tenant_id||''}:${member?.telegram_id||''}`;
  let sources=[],loadedAt=0,dialog=null,formState=null;
  const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const style=document.createElement('style');
  style.textContent=`#operatorNewChat{min-height:38px;white-space:nowrap}@media(max-width:850px){#operatorNewChat{grid-column:1/-1;width:100%;min-height:44px}}.operator-new-chat-dialog{width:min(520px,calc(100vw - 24px));max-height:90dvh;overflow:auto;box-sizing:border-box;border:1px solid var(--line);border-radius:14px;padding:0;background:var(--card);color:var(--ink);box-shadow:0 24px 80px #0005}.operator-new-chat-dialog::backdrop{background:#071d1688}.operator-new-chat-dialog header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 16px;border-bottom:1px solid var(--line)}.operator-new-chat-dialog h2{margin:0;font-size:18px}.operator-new-chat-dialog form{display:grid;gap:10px;padding:14px 16px}.operator-new-chat-dialog label{display:grid;gap:4px;color:var(--muted);font-size:13px}.operator-new-chat-dialog input,.operator-new-chat-dialog select,.operator-new-chat-dialog textarea{box-sizing:border-box;width:100%;min-width:0;border:1px solid var(--line);border-radius:8px;padding:9px 10px;background:var(--card);color:var(--ink);font:inherit}.operator-new-chat-dialog textarea{min-height:110px;resize:vertical}.operator-new-chat-dialog small{color:var(--muted)}.operator-new-chat-dialog [role=status]{min-height:1em;margin:0;color:var(--muted);overflow-wrap:anywhere}.operator-new-chat-dialog .operator-new-chat-actions{display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap}.operator-new-chat-dialog button{min-height:40px}@media(max-width:440px){.operator-new-chat-dialog{width:calc(100vw - 16px)}.operator-new-chat-dialog header,.operator-new-chat-dialog form{padding-inline:12px}.operator-new-chat-dialog .operator-new-chat-actions button{flex:1;min-height:44px}}`;
  document.head.append(style);

  async function api(method='GET',payload){
    const member=identity();if(!member)throw Error(language().error);
    const query=new URLSearchParams({expected_tenant_id:String(member.tenant_id),expected_user_id:String(member.telegram_id)});
    const response=await fetch('/api/platform/ai/dialogs/new?'+query,{method,credentials:'same-origin',cache:'no-store',headers:method==='POST'?{'Content-Type':'application/json'}:undefined,body:method==='POST'?JSON.stringify({...payload,expected_tenant_id:member.tenant_id,expected_user_id:member.telegram_id}):undefined});
    let result;try{result=await response.json()}catch{throw Error(language().error)}
    if(!response.ok||result.success!==true)throw Object.assign(Error(result.error||language().error),{status:response.status,keep_request:result.keep_request===true});
    return result.result;
  }

  async function loadSources(force=false){
    if(!force&&Date.now()-loadedAt<30000)return sources;
    const result=await api();sources=Array.isArray(result.sources)?result.sources:[];loadedAt=Date.now();
    const button=document.getElementById('operatorNewChat');
    if(button)button.hidden=!sources.length;
    return sources;
  }

  function ensureDialog(){
    if(dialog?.isConnected)return dialog;
    dialog=document.createElement('dialog');dialog.className='operator-new-chat-dialog';dialog.setAttribute('aria-labelledby','operatorNewChatTitle');
    dialog.innerHTML=`<header><h2 id="operatorNewChatTitle"></h2><button type="button" class="outline" data-new-chat-close></button></header><form><label><span data-label="source"></span><select required data-field="source"></select></label><label><span data-label="phone"></span><input data-field="phone" type="tel" inputmode="tel" autocomplete="tel" maxlength="16" required></label><small data-phone-hint></small><label><span data-label="text"></span><textarea data-field="text" maxlength="8000" required></textarea></label><p role="status" aria-live="polite"></p><div class="operator-new-chat-actions"><button type="button" class="outline" data-new-chat-cancel></button><button type="submit" class="primary" data-new-chat-send></button></div></form>`;
    document.body.append(dialog);
    const close=()=>{if(formState?.submitting)return;dialog.close?.();dialog.removeAttribute('open');formState=null;};
    dialog.querySelector('[data-new-chat-close]').onclick=close;
    dialog.querySelector('[data-new-chat-cancel]').onclick=close;
    dialog.addEventListener('cancel',event=>{event.preventDefault();});
    dialog.querySelector('form').onsubmit=submit;
    dialog.addEventListener('input',event=>{
      if(!formState||formState.submitted)return;
      formState.requestId=crypto.randomUUID();
      const member=identity();if(member)try{sessionStorage.setItem(keyFor(member),JSON.stringify({channel_id:dialog.querySelector('[data-field=source]').value,phone:dialog.querySelector('[data-field=phone]').value,text:dialog.querySelector('[data-field=text]').value,request_id:formState.requestId,submitted:formState.submitted===true}));}catch{}
    });
    return dialog;
  }

  function localize(node){
    const c=language();node.querySelector('#operatorNewChatTitle').textContent=c.title;
    node.querySelector('[data-new-chat-close]').textContent='×';node.querySelector('[data-new-chat-close]').setAttribute('aria-label',c.close);
    node.querySelector('[data-new-chat-cancel]').textContent=c.cancel;node.querySelector('[data-new-chat-send]').textContent=c.send;
    for(const field of ['source','phone','text'])node.querySelector(`[data-label=${field}]`).textContent=c[field];
    node.querySelector('[data-phone-hint]').textContent=c.phoneHint;
    node.querySelector('[data-field=phone]').placeholder='+994501234567';node.querySelector('[data-field=text]').placeholder=c.textHint;
  }

  async function open(){
    const c=language(),button=document.getElementById('operatorNewChat');if(button)button.disabled=true;
    try{
      const available=await loadSources(true);if(!available.length)throw Error(c.noSource);
      const node=ensureDialog();localize(node);const select=node.querySelector('[data-field=source]');
      select.replaceChildren(...available.map(source=>{const option=document.createElement('option');option.value=source.channel_id;option.textContent=source.name||'WhatsApp';return option;}));
      const member=identity();let saved=null;try{saved=JSON.parse(sessionStorage.getItem(keyFor(member))||'null')}catch{}
      const eligible=saved&&saved.request_id&&available.some(source=>source.channel_id===saved.channel_id);
      formState={requestId:eligible?saved.request_id:crypto.randomUUID(),submitted:eligible&&saved.submitted===true,submitting:false};
      node.querySelector('form').reset();
      if(eligible){select.value=saved.channel_id;node.querySelector('[data-field=phone]').value=saved.phone||'';node.querySelector('[data-field=text]').value=saved.text||'';}
      for(const field of node.querySelectorAll('[data-field]'))field.disabled=formState.submitted===true;
      node.querySelector('[role=status]').textContent=formState.submitted?c.unknown:'';node.querySelector('[data-new-chat-send]').disabled=false;node.querySelector('[data-new-chat-send]').textContent=formState.submitted?c.check:c.send;
      if(typeof node.showModal==='function')node.showModal();else node.setAttribute('open','');
      node.querySelector('[data-field=phone]').focus();
    }catch(error){window.alert(error.message||c.error);}
    finally{if(button)button.disabled=false;}
  }

  async function submit(event){
    event.preventDefault();if(!formState||formState.submitting)return;
    const node=dialog,c=language(),button=node.querySelector('[data-new-chat-send]'),status=node.querySelector('[role=status]'),member=identity();
    const payload={channel_id:node.querySelector('[data-field=source]').value,phone:node.querySelector('[data-field=phone]').value.trim(),text:node.querySelector('[data-field=text]').value,request_id:formState.requestId};
    if(!payload.channel_id||!/^\+[1-9]\d{5,14}$/.test(payload.phone)||!payload.text.trim()){status.textContent=c.error;return;}
    formState.submitting=true;formState.submitted=true;button.disabled=true;button.textContent=c.sending;status.textContent='';
    for(const field of node.querySelectorAll('[data-field]'))field.disabled=true;
    try{
      sessionStorage.setItem(keyFor(member),JSON.stringify({...payload,submitted:true}));
      const result=await api('POST',payload);
      if(result.existing){
        status.textContent=c.openExisting;
        try{sessionStorage.removeItem(keyFor(member));}catch{}
        node.close?.();node.removeAttribute('open');formState=null;
        await window.tenantOperator?.openDialog?.({...result.route,compose_draft:payload.text});
        return;
      }
      if(result.state==='accepted'){
        status.textContent=c.sent;try{sessionStorage.removeItem(keyFor(member));}catch{}
        const route=result.route;node.close?.();node.removeAttribute('open');formState=null;
        await window.tenantOperator?.refresh?.();await window.tenantOperator?.openDialog?.(route);
        return;
      }
      if(result.state==='unknown')status.textContent=c.unknown;else status.textContent=c.error;
      if(member)try{sessionStorage.setItem(keyFor(member),JSON.stringify({...payload,submitted:true}));}catch{}
    }catch(error){
      status.textContent=error.keep_request?c.unknown:(error.status===503?c.unknown:error.message||c.error);
      const uncertain=error.keep_request===true||!error.status||error.status>=500;
      if(uncertain)status.textContent=c.unknown;
      formState.submitted=uncertain;
      if(!uncertain){formState.requestId=crypto.randomUUID();for(const field of node.querySelectorAll('[data-field]'))field.disabled=false;}
      if(member)try{sessionStorage.setItem(keyFor(member),JSON.stringify({...payload,request_id:formState.requestId,submitted:uncertain}));}catch{}
    }finally{
      if(dialog?.isConnected&&formState){formState.submitting=false;button.disabled=false;button.textContent=formState.submitted?c.check:c.send;}
    }
  }

  async function install(){
    const filters=root.querySelector('.operator-filters');if(!filters||!identity()||!window.tenantOperator?.hasRight?.('dialogs'))return;
    let button=document.getElementById('operatorNewChat');
    if(!button){button=document.createElement('button');button.type='button';button.id='operatorNewChat';button.className='outline';button.hidden=true;filters.append(button);}
    if(button.textContent!==language().newChat)button.textContent=language().newChat;button.onclick=()=>void open();
    try{await loadSources();}catch{button.hidden=true;}
  }

    const observer=new MutationObserver(()=>{void install();});
  observer.observe(root,{childList:true,subtree:true});
  document.addEventListener('tenant-profile',()=>{sources=[];loadedAt=0;void install();});
  document.addEventListener('tenant-language',()=>{const button=document.getElementById('operatorNewChat');if(button)button.textContent=language().newChat;if(dialog?.isConnected)localize(dialog);});
  void install();
})();
