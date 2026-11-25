/* Compact controls delegate to existing AI/send/voice handlers; no new sends. */
(()=>{
  const labels={ru:{ai:'Действия AI',voice:'Записать голосовое',unavailable:'Голосовая отправка недоступна',send:'Отправить',check:'Проверить отправку',emoji:'Эмодзи'},az:{ai:'AI əməliyyatları',voice:'Səsli mesaj yaz',unavailable:'Səsli göndərmə əlçatan deyil',send:'Göndər',check:'Göndərməni yoxla',emoji:'Emojilər'},en:{ai:'AI actions',voice:'Record voice message',unavailable:'Voice sending unavailable',send:'Send',check:'Check send',emoji:'Emoji'}};
  const copy=key=>(labels[window.tenantI18n?.language]||labels.az)[key];
  const icons={
    preview:'<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="m10 9 5 3-5 3Z" fill="currentColor" stroke="none"/></svg>',
    send:'<svg aria-hidden="true" viewBox="0 0 24 24" fill="currentColor"><path d="m3.6 3.8 17.6 7.3a1 1 0 0 1 0 1.8L3.6 20.2a.9.9 0 0 1-1.2-1l1.3-5.4 9-1.8-9-1.8-1.3-5.4a.9.9 0 0 1 1.2-1Z"/></svg>',
    emoji:'<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path stroke-linejoin="round" d="M9.5 3.5h5A5.5 5.5 0 0 1 20 9v3a4 4 0 0 1-1.2 2.8l-3.5 3.5a4 4 0 0 1-2.8 1.2h-3A5.5 5.5 0 0 1 4 14V9a5.5 5.5 0 0 1 5.5-5.5Z"/><circle cx="9.2" cy="9.4" r="1.1" fill="currentColor" stroke="none"/><circle cx="14.8" cy="9.4" r="1.1" fill="currentColor" stroke="none"/><path d="M8.5 13a3.7 3.7 0 0 0 3.3 2M14 19v-3.4a2 2 0 0 1 2-2h3.5"/></svg>',
    voice:'<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21m-2.5 0h5"/></svg>',
  };
  const emojiGroups=[
    ['😀','Лица','Üzlər','Smileys','😀 😃 😄 😁 😆 😅 😂 🤣 🙂 🙃 😉 😊 😇 🥰 😍 🤩 😘 😗 ☺️ 😚 😙 🥲 😋 😛 😜 🤪 😝 🤑 🤗 🤭 🫢 🫣 🤫 🤔 🫡 🤐 🤨 😐 😑 😶 😏 😒 🙄 😬 😮‍💨 🤥 😌 😔 😪 🤤 😴 😷 🤒 🤕 🤢 🤮 🤧 🥵 🥶 🥴 😵 🤯 🤠 🥳 😎 🤓 🧐 😕 🫤 😟 🙁 ☹️ 😮 😯 😲 😳 🥺 🥹 😦 😧 😨 😰 😥 😢 😭 😱 😖 😣 😞 😓 😩 😫 😤 😡 😠 🤬 💀 👻 🤖'],
    ['👋','Жесты','Jestlər','Gestures','👋 🤚 🖐️ ✋ 🖖 🫱 🫲 👌 🤌 🤏 ✌️ 🤞 🫰 🤟 🤘 🤙 👈 👉 👆 🖕 👇 ☝️ 🫵 👍 👎 ✊ 👊 🤛 🤜 👏 🙌 🫶 👐 🤲 🤝 🙏 💪'],
    ['🌿','Природа','Təbiət','Nature','🌿 🐶 🐱 🐭 🐹 🐰 🦊 🐻 🐼 🐨 🐯 🦁 🐮 🐷 🐸 🐵 🐔 🐦 🐣 🐺 🦋 🐝 🐢 🐬 🐟 🦜 🌵 🌴 🌳 🌱 🍀 🍁 🌷 🌹 🌻 🌸 💐 ⭐ 🌙 ☀️ 🌈 ☁️ ❄️ 🔥 💧'],
    ['🍕','Еда и напитки','Yemək və içkilər','Food and drink','🍎 🍐 🍊 🍋 🍌 🍉 🍇 🍓 🍒 🍑 🥭 🍍 🥑 🍅 🥕 🌽 🥐 🍞 🧀 🍳 🥞 🥗 🍔 🍟 🍕 🌮 🍝 🍜 🍱 🍣 🍦 🍰 🎂 🍫 🍿 🍪 🥤 ☕ 🍵 🧃 🍷 🍻 🥂'],
    ['⚽','Спорт и отдых','İdman və istirahət','Activities','⚽ 🏀 🏈 ⚾ 🎾 🏐 🏓 🏸 🥊 🏋️ 🏃 🚴 🧘 🏊 🏆 🥇 🎮 🎲 🎯 🎳 🎸 🎹 🎤 🎧 🎨 🎬 🎭 🎪 🎉 🎊 🎁 🎈'],
    ['🚗','Поездки и предметы','Səyahət və əşyalar','Travel and objects','🚗 🚕 🚌 🚎 🚓 🚑 🚒 🚚 🚲 🛴 🚆 🚇 🚀 ✈️ 🚁 🚢 🏖️ 🗻 🏠 🏢 🗺️ 🧳 ⏰ ⌚ 📱 💻 📷 💡 🔑 🔒 📚 📝 📎 💸 💳 🔔 📦'],
    ['❤️','Символы','Simvollar','Symbols','❤️ 🩷 🧡 💛 💚 💙 🩵 💜 🤎 🖤 🤍 💔 ❤️‍🔥 ❤️‍🩹 💖 💗 💓 💞 💕 💟 💯 ✅ ❌ ⚠️ ❗ ❓ ➕ ➖ ♻️ 🔁 🔝 🆗 🆕 ☮️ ✨ 💬 💤'],
  ];
  function createEmojiPicker(input,onOpen){
    const trigger=document.createElement('button');
    trigger.type='button';trigger.id='operatorComposerEmoji';trigger.className='outline operator-icon-action';
    trigger.innerHTML=icons.emoji;trigger.title=copy('emoji');trigger.setAttribute('aria-label',copy('emoji'));
    trigger.setAttribute('aria-expanded','false');trigger.setAttribute('aria-controls','operatorComposerEmojiPanel');
    const panel=document.createElement('div');panel.id='operatorComposerEmojiPanel';panel.hidden=true;
    panel.className='operator-emoji-panel';panel.setAttribute('role','group');panel.setAttribute('aria-label',copy('emoji'));
    const grid=document.createElement('div');grid.className='operator-emoji-grid';
    const categories=document.createElement('div');categories.className='operator-emoji-categories';
    panel.append(grid,categories);
    const close=focus=>{
      panel.hidden=true;trigger.setAttribute('aria-expanded','false');
      if(focus)trigger.focus({preventScroll:true});
    };
    const tabs=emojiGroups.map(([symbol,ru,az,en],index)=>{
      const tab=document.createElement('button');tab.type='button';tab.textContent=symbol;
      tab.title=({ru,az,en})[window.tenantI18n?.language]||az;
      tab.setAttribute('aria-label',tab.title);tab.onclick=()=>showCategory(index);
      categories.append(tab);
      return tab;
    });
    function showCategory(index){
      for(const [position,tab] of tabs.entries())tab.setAttribute('aria-pressed',String(position===index));
      grid.replaceChildren(...emojiGroups[index][4].split(' ').map(value=>{
        const button=document.createElement('button');button.type='button';button.textContent=value;
        button.onclick=()=>{
          if(input.disabled||input.readOnly||!input.isConnected)return close(false);
          const start=input.selectionStart,end=input.selectionEnd;
          if(input.maxLength>=0&&input.value.length-end+start+value.length>input.maxLength)return;
          input.setRangeText(value,start,end,'end');
          input.dispatchEvent(new Event('input',{bubbles:true}));
          close(false);input.focus({preventScroll:true});
        };
        return button;
      }));
      grid.scrollTop=0;
    }
    showCategory(0);
    trigger.onclick=event=>{
      if(!panel.hidden)return close(false);
      onOpen();panel.hidden=false;trigger.setAttribute('aria-expanded','true');
      if(event.detail===0)grid.firstElementChild?.focus({preventScroll:true});
    };
    return {trigger,panel,close};
  }
  let active=null;
  // The existing send control owns recovery. Preserve its visible check action;
  // never inspect, mutate, or reimplement its durable receipt state.
  const hasPending=send=>send.textContent.trim()===(window.tenantI18n?.t?.('checkSend')||'Vəziyyəti yoxla');
  const close=focus=>{if(!active)return;active.menu.hidden=true;active.ai.setAttribute('aria-expanded','false');if(focus)active.ai.focus();};
  function clear(){close(false);active?.emoji.close(false);active?.observer.disconnect();active=null;}
  function attach(){
    const actions=document.querySelector('.operator-composer-actions'),input=document.getElementById('chatText'),send=document.getElementById('sendChat');
    if(!actions||!input||!send||actions.closest('[data-widget-reply="true"]')){clear();return;}
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
    const emoji=createEmojiPicker(input,()=>close(false));
    primary.before(emoji.trigger);
    actions.append(emoji.panel);
    function setPrimary(action,label,disabled,icon=action==='voice'?'voice':'send'){
      if(primary.dataset.icon!==icon){primary.dataset.icon=icon;primary.innerHTML=icons[icon];}
      primary.dataset.action=action;
      primary.title=label;primary.setAttribute('aria-label',label);primary.disabled=disabled;
    }
    send.dataset.composerDelegate='true';
    const refresh=()=>{
      if(!active||active.actions!==actions||!actions.isConnected)return;
      const emojiDisabled=input.disabled||input.readOnly;
      if(emoji.trigger.disabled!==emojiDisabled)emoji.trigger.disabled=emojiDisabled;
      if(emojiDisabled&&!emoji.panel.hidden)emoji.close(false);
      const mic=actions.querySelector('.operator-voice-mic');if(mic&&mic.dataset.composerDelegate!=='true')mic.dataset.composerDelegate='true';
      const media=!hasPending(send)&&window.tenantCustomerMedia?.action();
      if(media){setPrimary('media',media.label,media.disabled,media.preview?'preview':'send');return;}
      const pending=hasPending(send);
      const useSend=!!input.value.trim()||pending;
      const label=useSend?(pending?copy('check'):copy('send')):(mic?copy('voice'):copy('unavailable'));
      setPrimary(useSend?'send':'voice',label,useSend?send.disabled:(!mic||mic.hidden||mic.disabled||input.disabled));
      primary.classList.toggle('loading',send.classList.contains('loading'));
      ai.disabled=!Array.from(menu.querySelectorAll('button')).some(button=>!button.disabled);
      if(ai.disabled)close(false);
    };
    const panel=document.getElementById('operatorCopilot');let panelWasOpen=panel&&!panel.hidden;
    const observer=new MutationObserver(records=>{
      if(panel){const open=!panel.hidden;if(panelWasOpen&&!open)ai.focus({preventScroll:true});panelWasOpen=open;}
      if(records.some(r=>r.target===send||r.target===input||menu.contains(r.target)&&r.attributeName==='disabled'||r.target.classList?.contains('operator-voice-mic')||r.type==='childList'&&r.target===actions))refresh();
    });
    active={actions,input,send,primary,menu,ai,emoji,observer,refresh};
    observer.observe(actions,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','hidden','class']});
    observer.observe(input,{attributes:true,attributeFilter:['disabled','readonly']});
    if(panel)observer.observe(panel,{attributes:true,attributeFilter:['hidden']});
    input.addEventListener('input',refresh);
    input.setAttribute('enterkeyhint','send');
    input.addEventListener('keydown',event=>{
      if(active?.primary!==primary||event.defaultPrevented||event.key!=='Enter'||event.shiftKey||event.altKey||event.ctrlKey||event.metaKey||event.isComposing||event.keyCode===229)return;
      event.preventDefault();
      if(event.repeat||input.disabled)return;
      refresh();
      if(primary.dataset.action==='send'&&!primary.disabled)primary.click();
    });
    primary.onclick=()=>{refresh();if(primary.disabled)return;if(primary.dataset.action==='media'){window.tenantCustomerMedia?.action()?.run();return;}const target=primary.dataset.action==='send'?send:actions.querySelector('.operator-voice-mic');target?.click();refresh();};
    ai.onclick=()=>{if(ai.disabled)return;emoji.close(false);const opening=menu.hidden;menu.hidden=!opening;ai.setAttribute('aria-expanded',String(opening));if(opening)menu.querySelector('button:not(:disabled)')?.focus();};
    refresh();
  }
  const style=document.createElement('style');style.textContent=`
#view-dialogs .operator-composer { position:relative; padding-bottom:4px; margin-bottom:4px }
#view-dialogs .operator-composer #chatText {
  background:transparent!important; border:0!important; outline:none!important;
  box-shadow:none!important; padding:5px 7px!important; min-height:38px;
}
@media(max-width:900px) {
  #view-dialogs #operatorDialogs[data-conversation-open=true] .operator-composer {
    margin-bottom:max(4px,env(safe-area-inset-bottom));
  }
}
.operator-composer{position:relative}
.operator-composer-actions [data-composer-delegate=true]{display:none!important}
.operator-composer-actions #operatorComposerPrimary{display:grid;place-items:center;margin-left:auto;flex:none;width:44px;height:44px;min-width:44px;min-height:44px;padding:0;border-radius:50%;background:var(--ink,#132621);color:var(--card,#fff)}
.operator-composer-actions #operatorComposerPrimary[data-action=voice]{background:transparent;color:var(--ink,#132621)}
.operator-composer-actions #operatorComposerPrimary[data-action=voice]:is(:hover,:focus-visible){background:var(--paper)}
.operator-composer-actions #operatorComposerPrimary svg{width:22px;height:22px;pointer-events:none}
.operator-composer-actions #operatorComposerPrimary:focus-visible{outline:2px solid var(--brand);outline-offset:2px}.operator-composer-actions #operatorAIActionMenu{min-width:44px;min-height:44px;font-weight:700}.operator-composer-ai-menu{position:absolute;z-index:25;left:8px;bottom:calc(100% + 6px);max-width:calc(100% - 16px);padding:6px;border:1px solid var(--line);border-radius:12px;background:var(--card);box-shadow:0 8px 24px #0003;display:grid;gap:3px}.operator-composer-ai-menu[hidden]{display:none!important}.operator-composer-actions .operator-composer-ai-menu button.operator-icon-action{display:flex;width:auto;max-width:100%;min-height:44px;height:auto;text-align:left;gap:9px;padding:9px 12px;white-space:normal}.operator-composer-ai-menu svg,.operator-composer-ai-menu .operator-voice-wave{flex:none}.operator-ai-action-caption{font-size:14px}.operator-composer-actions #operatorComposerPrimary:disabled{opacity:.5}
.operator-composer-actions #operatorComposerEmoji{margin-left:auto}
.operator-composer-actions #operatorComposerEmoji svg{fill:none}
.operator-composer-actions #operatorComposerEmoji + #operatorComposerPrimary{margin-left:0}
.operator-emoji-panel{position:absolute;right:6px;bottom:calc(100% + 6px);z-index:25;display:grid;width:min(410px,calc(100% - 12px));box-sizing:border-box;border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink);box-shadow:0 8px 24px #0003}
.operator-emoji-panel[hidden]{display:none!important}
.operator-emoji-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(30px,1fr));align-content:start;gap:2px;max-height:min(248px,34dvh);overflow-y:auto;overscroll-behavior:contain;padding:7px}
.operator-emoji-categories{display:grid;grid-template-columns:repeat(7,1fr);gap:2px;padding:4px;border-top:1px solid var(--line)}
.operator-composer-actions .operator-emoji-panel button{display:grid;place-items:center;margin:0;padding:0;min-width:0;min-height:34px;border-radius:6px;line-height:1}
.operator-emoji-grid button{font-size:21px!important}
.operator-emoji-categories button{font-size:16px!important}
.operator-emoji-categories button[aria-pressed=true]{background:var(--paper);box-shadow:inset 0 -2px var(--brand)!important}
.operator-emoji-panel button:focus-visible{outline:2px solid var(--brand);outline-offset:-2px}`;document.head.append(style);
  document.addEventListener('tenant-chat-attachments-ready',attach);
  document.addEventListener('tenant-composer-media',()=>active?.refresh());
  document.addEventListener('tenant-chat-close',clear);
  document.addEventListener('tenant-auth-invalid',clear);
  document.addEventListener('pointerdown',event=>{
    if(!active)return;
    if(!active.menu.contains(event.target)&&!active.ai.contains(event.target))close(false);
    const {emoji}=active;
    if(!emoji.panel.contains(event.target)&&!emoji.trigger.contains(event.target))emoji.close(false);
  });
  document.addEventListener('keydown',event=>{
    if(event.key!=='Escape'||!active)return;
    if(!active.emoji.panel.hidden){
      event.preventDefault();event.stopImmediatePropagation();active.emoji.close(true);
    }else if(!active.menu.hidden){
      event.preventDefault();event.stopImmediatePropagation();close(true);
    }
  },true);
  attach();
})();
