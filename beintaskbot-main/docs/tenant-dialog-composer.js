/* Compact controls delegate to existing AI/send/voice handlers; no new sends. */
(()=>{
  const labels={ru:{ai:'Действия AI',voice:'Записать голосовое',unavailable:'Голосовая отправка недоступна',send:'Отправить',check:'Проверить отправку'},az:{ai:'AI əməliyyatları',voice:'Səsli mesaj yaz',unavailable:'Səsli göndərmə əlçatan deyil',send:'Göndər',check:'Göndərməni yoxla'},en:{ai:'AI actions',voice:'Record voice message',unavailable:'Voice sending unavailable',send:'Send',check:'Check send'}};
  const copy=key=>(labels[window.tenantI18n?.language]||labels.az)[key];
  let active=null;
  // The existing send control owns recovery. Preserve its visible check action;
  // never inspect, mutate, or reimplement its durable receipt state.
  const hasPending=send=>send.textContent.trim()===(window.tenantI18n?.t?.('checkSend')||'Vəziyyəti yoxla');
  const close=focus=>{if(!active)return;active.menu.hidden=true;active.ai.setAttribute('aria-expanded','false');if(focus)active.ai.focus();};
  function clear(){close(false);active?.observer.disconnect();active=null;}
  function attach(){
    const actions=document.querySelector('.operator-composer-actions'),input=document.getElementById('chatText'),send=document.getElementById('sendChat');
    if(!actions||!input||!send){clear();return;}
    if(active?.actions===actions){active.refresh();return;}
    clear();
    const ai=document.createElement('button');ai.type='button';ai.id='operatorAIActionMenu';ai.className='outline operator-composer-ai';ai.textContent='AI';ai.title=copy('ai');ai.setAttribute('aria-label',copy('ai'));ai.setAttribute('aria-expanded','false');ai.setAttribute('aria-controls','operatorAIActionChoices');
    const menu=document.createElement('div');menu.id='operatorAIActionChoices';menu.className='operator-composer-ai-menu';menu.hidden=true;menu.setAttribute('role','group');menu.setAttribute('aria-label',copy('ai'));
    for(const id of ['operatorAIReply','operatorCopilotToggle','operatorCopilotVoiceToggle']){
      const item=document.getElementById(id);if(!item)continue;
      const label=document.createElement('span');label.textContent=item.getAttribute('aria-label')||item.title;label.className='operator-ai-action-caption';item.append(label);menu.append(item);
      item.addEventListener('click',()=>close(false));
    }
    actions.prepend(ai);actions.append(menu);
    const primary=document.createElement('button');primary.type='button';primary.id='operatorComposerPrimary';primary.className='primary';actions.append(primary);
    send.dataset.composerDelegate='true';
    const refresh=()=>{
      if(!active||active.actions!==actions||!actions.isConnected)return;
      const mic=actions.querySelector('.operator-voice-mic');if(mic&&mic.dataset.composerDelegate!=='true')mic.dataset.composerDelegate='true';
      const media=window.tenantCustomerMedia?.action();
      if(media){primary.dataset.action='media';primary.textContent='↑';primary.title=media.label;primary.setAttribute('aria-label',media.label);primary.disabled=media.disabled;return;}
      const pending=hasPending(send);
      const useSend=!!input.value.trim()||pending;
      primary.dataset.action=useSend?'send':'voice';
      const label=useSend?(pending?copy('check'):copy('send')):(mic?copy('voice'):copy('unavailable'));
      primary.setAttribute('aria-label',label);primary.title=label;
      primary.textContent=useSend?'↑':'🎙';
      primary.disabled=useSend?send.disabled:(!mic||mic.hidden||mic.disabled||input.disabled);
      primary.classList.toggle('loading',send.classList.contains('loading'));
      ai.disabled=!Array.from(menu.querySelectorAll('button')).some(button=>!button.disabled);
      if(ai.disabled)close(false);
    };
    const panel=document.getElementById('operatorCopilot');let panelWasOpen=panel&&!panel.hidden;
    const observer=new MutationObserver(records=>{
      if(panel){const open=!panel.hidden;if(panelWasOpen&&!open)ai.focus({preventScroll:true});panelWasOpen=open;}
      if(records.some(r=>r.target===send||r.target===input||menu.contains(r.target)&&r.attributeName==='disabled'||r.target.classList?.contains('operator-voice-mic')||r.type==='childList'&&r.target===actions))refresh();
    });
    active={actions,input,send,primary,menu,ai,observer,refresh};
    observer.observe(actions,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','hidden','class']});
    observer.observe(input,{attributes:true,attributeFilter:['disabled']});
    if(panel)observer.observe(panel,{attributes:true,attributeFilter:['hidden']});
    input.addEventListener('input',refresh);
    primary.onclick=()=>{refresh();if(primary.disabled)return;if(primary.dataset.action==='media'){window.tenantCustomerMedia?.action()?.run();return;}const target=primary.dataset.action==='send'?send:actions.querySelector('.operator-voice-mic');target?.click();refresh();};
    ai.onclick=()=>{if(ai.disabled)return;const opening=menu.hidden;menu.hidden=!opening;ai.setAttribute('aria-expanded',String(opening));if(opening)menu.querySelector('button:not(:disabled)')?.focus();};
    refresh();
  }
  const style=document.createElement('style');style.textContent=`.operator-composer{position:relative}.operator-composer-actions [data-composer-delegate=true]{display:none!important}.operator-composer-actions #operatorComposerPrimary{display:grid;place-items:center;margin-left:auto;flex:none;width:44px;height:44px;min-width:44px;min-height:44px;padding:0;border-radius:50%;background:var(--brand);color:var(--on-brand,#fff);font-size:22px}.operator-composer-actions #operatorAIActionMenu{min-width:44px;min-height:44px;font-weight:700}.operator-composer-ai-menu{position:absolute;z-index:25;left:8px;bottom:calc(100% + 6px);max-width:calc(100% - 16px);padding:6px;border:1px solid var(--line);border-radius:12px;background:var(--card);box-shadow:0 8px 24px #0003;display:grid;gap:3px}.operator-composer-ai-menu[hidden]{display:none!important}.operator-composer-actions .operator-composer-ai-menu button.operator-icon-action{display:flex;width:auto;max-width:100%;min-height:44px;height:auto;text-align:left;gap:9px;padding:9px 12px;white-space:normal}.operator-composer-ai-menu svg,.operator-composer-ai-menu .operator-voice-wave{flex:none}.operator-ai-action-caption{font-size:14px}.operator-composer-actions #operatorComposerPrimary:disabled{opacity:.5}`;document.head.append(style);
  document.addEventListener('tenant-chat-attachments-ready',attach);
  document.addEventListener('tenant-composer-media',()=>active?.refresh());
  document.addEventListener('tenant-chat-close',clear);
  document.addEventListener('tenant-auth-invalid',clear);
  document.addEventListener('pointerdown',event=>{if(active&&!active.menu.contains(event.target)&&!active.ai.contains(event.target))close(false);});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&active&&!active.menu.hidden){event.preventDefault();event.stopImmediatePropagation();close(true);}},true);
  attach();
})();
