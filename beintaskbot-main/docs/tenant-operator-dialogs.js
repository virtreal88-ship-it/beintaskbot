/* Operator conversations: a view over existing data. State/send mutations use existing APIs. */
(()=>{
  const $=id=>document?.getElementById(id),i18n=window.tenantI18n,t=key=>i18n.t(key);
  const searchValue=input=>window.tenantSearchInputs?.read(input)??input?.value??'';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const helperStyle=document.createElement('style');helperStyle.textContent=`
    #operatorCopilot{box-sizing:border-box;display:flex;flex-direction:column;min-width:0;padding:0;overflow:hidden;background:var(--card,#fff);color:var(--ink,#18332c)}#operatorCopilot[hidden]{display:none!important}
    #dialogHelperControls{flex:none;position:relative;padding:12px;border-bottom:1px solid var(--line,#d9e3de);background:var(--card,#fff)}#dialogHelperControls .operator-copilot-header{display:flex;align-items:center;justify-content:space-between;gap:8px;flex:none;position:static;margin:0 0 8px;padding:0;border:0;min-height:44px}#operatorCopilot .operator-copilot-header>div{display:grid;gap:3px;min-width:0}#operatorCopilot .operator-copilot-header strong{font-size:18px}#operatorCopilot .operator-copilot-header small{font-size:12px;color:var(--muted,#64776f);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    #operatorCopilot button,#operatorCopilot select,#operatorCopilot summary{box-sizing:border-box;min-height:44px;font:inherit}#operatorCopilot button{border:1px solid var(--line,#ccdcd3);border-radius:9px;padding:10px 12px;background:var(--card,#fff);color:var(--ink,#18332c);cursor:pointer;white-space:normal;overflow-wrap:normal;line-height:1.25}#operatorCopilot button.primary,#operatorCopilot button[aria-pressed=true]{background:var(--brand-deep,#14523c);border-color:var(--brand-deep,#14523c);color:#fff}#operatorCopilot button:disabled{opacity:.55;cursor:default}#operatorCopilot button:focus-visible,#operatorCopilot select:focus-visible,#operatorCopilot textarea:focus-visible,#operatorCopilot summary:focus-visible{outline:2px solid var(--brand,#22a575);outline-offset:2px}#operatorCopilotClose{width:44px;min-width:44px;padding:0!important;font-size:26px!important}
    #dialogHelperContent{box-sizing:border-box;flex:1;min-height:0;min-width:0;overflow-y:auto;overscroll-behavior:contain;padding:12px;scroll-padding:12px;display:flex;flex-direction:column;gap:12px}#dialogHelperContent[hidden]{display:none!important}#operatorCopilot .operator-copilot-actions{display:flex;flex-wrap:wrap;gap:8px;margin:0;flex:none}#operatorCopilot .operator-copilot-modes button{flex:1 1 90px;padding:8px}#operatorCopilot .operator-copilot-prompt-label{display:block;font-weight:600;font-size:14px;line-height:1.4}#operatorCopilotPrompt{box-sizing:border-box;display:block;flex:none;width:100%;min-width:0;min-height:96px;max-height:30dvh;margin:0;padding:12px;border:1px solid var(--line,#ccdcd3);border-radius:10px;font-size:16px;line-height:1.4;background:var(--paper,#f8faf9);color:inherit;resize:vertical}#operatorCopilotAsk,#operatorCopilotClear{flex:1 1 150px;min-width:0}#operatorCopilotStatus:empty{display:none}#operatorCopilotStatus{margin:0;overflow-wrap:anywhere}#operatorCopilot .operator-copilot-result{flex:none;max-height:none;margin:0;padding:12px;border:1px solid var(--line,#d9e3de);border-radius:10px;white-space:pre-wrap;overflow-wrap:anywhere}#operatorCopilotTemplates{flex:none;border-top:1px solid var(--line,#d9e3de);padding-top:8px}#operatorCopilotTemplates summary{cursor:pointer;padding:10px 0;font-weight:600}#operatorCopilotTemplates input,#operatorCopilotTemplates textarea{box-sizing:border-box;width:100%;max-width:100%;font-size:16px}#operatorCopilotTemplates label{display:grid;gap:6px;margin:10px 0}#dialogConsultationStart{margin:12px}
    @media(max-width:900px){#operatorCopilot{position:fixed;z-index:1201;left:8px;right:8px;top:calc(var(--operator-visual-top,0px) + 8px);bottom:auto;width:auto;height:calc(var(--operator-visual-height,100dvh) - 16px);max-height:calc(var(--operator-visual-height,100dvh) - 16px);margin:0;border:1px solid var(--line,#d9e3de);border-radius:16px;box-shadow:0 16px 48px #0004}#operatorDialogBody[data-copilot-open=true] #operatorCopilotBackdrop{position:fixed;inset:0;z-index:1200;background:#071d1688}#dialogHelperContent{padding-bottom:max(12px,env(safe-area-inset-bottom))}}
  `;document.head.append(helperStyle);
  const toolbarStyle=document.createElement('style');toolbarStyle.textContent=`
    #operatorSearch{box-sizing:border-box;height:44px;min-height:44px;margin:0}#operatorToolsMenu{position:relative;align-self:start;width:44px;height:44px;min-width:44px;flex:none}#operatorToolsMenu>summary{box-sizing:border-box;display:grid;place-items:center;width:44px;height:44px;min-height:44px;padding:0}#operatorToolsMenu>summary svg,#operatorFiltersToggle svg{width:20px;height:20px;flex:none}
    #operatorToolsPopover{display:none;position:fixed;z-index:80;width:min(280px,calc(100vw - 16px));max-width:calc(100vw - 16px);overflow-y:auto;overscroll-behavior:contain;padding:6px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);box-shadow:0 8px 24px #0002;visibility:hidden}#operatorToolsMenu[open]>#operatorToolsPopover{display:grid;gap:2px}#operatorToolsMenu[open]>#operatorToolsPopover[data-anchored=true]{visibility:visible}
    #operatorToolsPopover>button{position:static;box-sizing:border-box;width:100%;min-height:44px;margin:0;padding:9px 10px;border:0;border-radius:7px;background:transparent;color:var(--ink);box-shadow:none;text-align:left;white-space:normal;overflow-wrap:anywhere}#operatorToolsPopover>button:hover,#operatorToolsPopover>button:focus-visible{background:var(--paper)}#operatorToolsPopover>button[hidden]{display:none!important}#operatorToolsPopover>[role=status]{min-width:0;max-width:100%;margin:0;padding:4px 10px;font-size:12px;color:var(--muted);overflow-wrap:anywhere}#operatorToolsPopover>[role=status]:empty{display:none}
    @media(max-width:900px){#operatorDialogs .operator-filters{grid-template-columns:minmax(0,1fr) 44px 44px;align-items:start}#operatorDialogs .operator-search{grid-column:1;grid-row:1;min-width:0}#operatorSearch{box-sizing:border-box;height:44px;min-height:44px;margin:0}#operatorToolsMenu{grid-column:2;grid-row:1}#operatorFiltersToggle{box-sizing:border-box;grid-column:3;grid-row:1;align-self:start;display:grid;place-items:center;width:44px;min-width:44px;height:44px;min-height:44px;margin:0;padding:0}}
  `;document.head.append(toolbarStyle);
  const filterStyle=document.createElement('style');filterStyle.textContent=`
    #operatorDialogs .operator-filters{grid-template-columns:minmax(0,1fr) 44px 44px;align-items:start}#operatorDialogs .operator-search{grid-column:1;grid-row:1;min-width:0}#operatorDialogs #operatorToolsMenu{grid-column:2;grid-row:1}#operatorDialogs #operatorFiltersToggle{display:grid;place-items:center;grid-column:3;grid-row:1;width:44px;height:44px;min-height:44px;padding:0;margin:0}
    #operatorDialogs #operatorAdvanced{position:fixed;z-index:80;display:none;grid-template-columns:minmax(0,1fr);gap:12px;width:min(320px,calc(100vw - 16px));max-width:calc(100vw - 16px);max-height:calc(100dvh - 16px);overflow:auto;overscroll-behavior:contain;box-sizing:border-box;padding:14px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);box-shadow:0 8px 24px #0002}#operatorDialogs #operatorAdvanced[data-open=true]{display:grid}#operatorAdvanced label{display:grid;gap:5px;min-width:0}#operatorAdvanced select{width:100%;min-width:0;min-height:44px;margin:0}#operatorFilterSaveStatus{margin:0;font-size:12px;color:var(--muted)}#operatorFilterSaveStatus:empty{display:none}#operatorFiltersToggle[data-active=true]{color:var(--brand);border-color:var(--brand)}
  `;document.head.append(filterStyle);
  let filterPopoverCleanup=()=>{},filterPreferencesLoaded=false,filterPreferencesFlight=null,filterSaveQueue=Promise.resolve();
  let companyPanelPreference=null;
  const isMobileCompanyPanelViewport=()=>window.matchMedia?.('(max-width: 900px)').matches===true;
  const shouldRestoreCompanyPanel=()=>!isMobileCompanyPanelViewport();
  function panelPreference(){
    const key=identity(member);
    if(companyPanelPreference?.key!==key)companyPanelPreference={key,open:false,changed:false,load:null,save:Promise.resolve()};
    return companyPanelPreference;
  }
  async function panelPreferenceRequest(profile,open){
    const post=typeof open==='boolean';
    const response=await fetch(endpoint+'dialogs/company-panel/preferences',{
      cache:'no-store',credentials:'same-origin',
      ...(post?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({open,expected_tenant_id:profile.tenant_id,expected_user_id:profile.telegram_id})}:
        {headers:{'X-Tenant-Id':String(profile.tenant_id),'X-User-Id':String(profile.telegram_id)}})
    });
    const payload=await response.json();
    if(!response.ok||payload?.success!==true||typeof payload.result?.open!=='boolean')throw Error();
    return payload.result.open;
  }
  function loadPanelPreference(){
    const pref=panelPreference(),profile=member;
    if(!pref.load)pref.load=panelPreferenceRequest(profile).then(open=>{
      if(pref===companyPanelPreference&&pref.key===identity(member)&&!pref.changed)pref.open=open;
    }).catch(()=>{pref.load=null;});
    return pref.load;
  }
  const panelSaveFailed=()=>({ru:'Не удалось сохранить состояние панели компании.',az:'Şirkət panelinin vəziyyəti saxlanmadı.',en:'Could not save the company panel setting.'})[i18n.language];
  function savePanelPreference(open){
    const pref=panelPreference(),profile=member;
    pref.open=open;pref.changed=true;
    pref.save=pref.save.then(async()=>{
      if(pref!==companyPanelPreference||pref.key!==identity(member))return;
      try{await panelPreferenceRequest(profile,open);}
      catch{const status=$('operatorCompanyPanelSaveStatus');if(status&&pref===companyPanelPreference&&pref.key===identity(member))status.textContent=panelSaveFailed();}
    });
  }
  const filterIds=['operatorChannel','operatorAgent','operatorState','operatorLifecycle'],filterKeys=['channel','agent','state','lifecycle'];
  const filterCopy=key=>({scope:{ru:'Диалоги',az:'Dialoqlar',en:'Conversations'},loadFailed:{ru:'Не удалось загрузить сохранённые фильтры.',az:'Saxlanmış filtrlər yüklənmədi.',en:'Saved filters could not be loaded.'},saving:{ru:'Сохраняем…',az:'Saxlanılır…',en:'Saving…'},saved:{ru:'Сохранено',az:'Saxlanıldı',en:'Saved'},saveFailed:{ru:'Не удалось сохранить фильтры. Выбор действует только сейчас.',az:'Filtrlər saxlanmadı. Seçim yalnız indi tətbiq olunur.',en:'Filters could not be saved. This selection applies only now.'}}[key]?.[i18n.language]||'');
  const filterSnapshot=()=>Object.fromEntries(filterIds.map((id,i)=>[filterKeys[i],$(id)?.value||(i===3?'all':'')]));
  function updateFilterIndicator(){const values=filterSnapshot();$('operatorFiltersToggle')?.setAttribute('data-active',String(!!(values.channel||values.agent||values.state||values.lifecycle!=='all')));}
  async function loadFilterPreferences(){
    if(filterPreferencesLoaded)return;if(filterPreferencesFlight)return filterPreferencesFlight;
    const version=epoch,controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),8000);filterIds.forEach(id=>{if($(id))$(id).disabled=true;});
    const flight=(async()=>{try{const saved=(await rawApi(endpoint+'dialogs/preferences',{signal:controller.signal})).result;if(version!==epoch)return;
      filterIds.forEach((id,i)=>{const node=$(id),value=saved?.[filterKeys[i]]||(i===3?'all':'');if(!node)return;if(value&&![...node.options].some(option=>option.value===value)&&i<2)node.add(new Option(value,value));node.value=value;});
      filterPreferencesLoaded=true;rememberOwnerOverviewMode();updateFilterIndicator();
    }catch(error){if(version===epoch){filterPreferencesLoaded=true;if($('operatorFilterSaveStatus'))$('operatorFilterSaveStatus').textContent=filterCopy('loadFailed');}}
    finally{clearTimeout(timeout);if(version===epoch){filterPreferencesFlight=null;filterIds.forEach(id=>{if($(id))$(id).disabled=false;});}}})();filterPreferencesFlight=flight;return flight;
  }
  function saveFilterPreferences(){
    const version=epoch,values=filterSnapshot();updateFilterIndicator();$('operatorFilterSaveStatus').textContent=filterCopy('saving');
    filterSaveQueue=filterSaveQueue.catch(()=>{}).then(async()=>{if(version!==epoch)return;try{await api('dialogs/preferences','POST',values);if(version===epoch&&JSON.stringify(values)===JSON.stringify(filterSnapshot()))$('operatorFilterSaveStatus').textContent=filterCopy('saved');}catch(error){if(version===epoch)$('operatorFilterSaveStatus').textContent=filterCopy('saveFailed');}});
  }
  function bindFilterPopover(){
    filterPopoverCleanup();const trigger=$('operatorFiltersToggle'),panel=$('operatorAdvanced'),listeners=[];
    const listen=(node,event,fn,options)=>{node?.addEventListener(event,fn,options);listeners.push(()=>node?.removeEventListener(event,fn,options));};
    const close=(restore=false)=>{panel.dataset.open='false';trigger.setAttribute('aria-expanded','false');if(restore)trigger.focus({preventScroll:true});};
    const position=()=>{if(panel.dataset.open!=='true')return;const v=window.visualViewport,left=v?.offsetLeft||0,top=v?.offsetTop||0,w=v?.width||innerWidth,h=v?.height||innerHeight,rect=trigger.getBoundingClientRect();panel.style.width=Math.max(1,Math.min(320,w-16))+'px';panel.style.maxHeight=Math.max(1,h-16)+'px';const box=panel.getBoundingClientRect();panel.style.left=Math.max(left+8,Math.min(rect.right-box.width,left+w-box.width-8))+'px';panel.style.top=Math.max(top+8,Math.min(rect.bottom+6,top+h-box.height-8))+'px';};
    listen(trigger,'click',()=>{if(panel.dataset.open==='true')return close();$('operatorToolsMenu').open=false;panel.dataset.open='true';trigger.setAttribute('aria-expanded','true');position();});
    listen(trigger,'keydown',event=>{if(event.key==='ArrowDown'){event.preventDefault();panel.dataset.open='true';trigger.setAttribute('aria-expanded','true');position();$('operatorChannel').focus();}});
    listen(document,'keydown',event=>{if(panel.dataset.open==='true'&&event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();close(true);}},true);
    listen(document,'pointerdown',event=>{if(!panel.contains(event.target)&&!trigger.contains(event.target))close();},true);
    listen(document,'focusin',event=>{if(!panel.contains(event.target)&&!trigger.contains(event.target))close();});
    for(const event of ['tenant-view','tenant-profile','tenant-auth-invalid','tenant-auth-pending'])listen(document,event,()=>close());
    listen(document,'tenant-language',()=>{trigger.title=t('filters');trigger.setAttribute('aria-label',t('filters'));$('operatorFilterScopeLabel').textContent=filterCopy('scope');position();});
    for(const event of ['resize','scroll']){listen(window,event,position);listen(window.visualViewport,event,position);}filterPopoverCleanup=()=>listeners.forEach(remove=>remove());
  }
  let toolsPopoverCleanup=()=>{};
  function bindToolsPopover(menu){
    toolsPopoverCleanup();
    const trigger=menu.querySelector('summary'),popover=menu.querySelector('#operatorToolsPopover'),pane=menu.closest('.operator-list-pane'),listeners=[];
    const listen=(target,type,fn,options)=>{target?.addEventListener(type,fn,options);listeners.push(()=>target?.removeEventListener(type,fn,options));};
    const close=(restore=false)=>{menu.open=false;trigger.setAttribute('aria-expanded','false');delete popover.dataset.anchored;if(restore&&trigger.isConnected)trigger.focus({preventScroll:true});};
    const items=()=>[...popover.querySelectorAll('button')].filter(node=>!node.disabled&&node.getClientRects().length);
    const position=()=>{
      if(!menu.open||!menu.isConnected)return;
      trigger.setAttribute('aria-expanded','true');
      const v=window.visualViewport,top=v?.offsetTop||0,left=v?.offsetLeft||0,width=v?.width||innerWidth,height=v?.height||innerHeight,rect=trigger.getBoundingClientRect(),bottom=top+height;
      // Viewport-only clamping can place the dropdown behind the desktop
      // navigation. Anchor it inside the visible portion of its own list.
      const list=pane?.getBoundingClientRect(),minX=Math.max(left,list?.left??left)+8,maxX=Math.min(left+width,list?.right??left+width)-8;
      popover.style.width=Math.max(1,Math.min(280,maxX-minX))+'px';popover.style.maxHeight=Math.max(1,height-16)+'px';
      const measured=popover.getBoundingClientRect(),below=Math.max(1,bottom-Math.max(rect.bottom+6,top+8)-8),above=Math.max(1,rect.top-top-14),down=below>=Math.min(measured.height,132)||below>=above,available=down?below:above;
      popover.style.maxHeight=available+'px';
      const h=Math.min(measured.height,available),x=Math.max(minX,Math.min(rect.right-measured.width,maxX-measured.width)),y=Math.max(top+8,Math.min(down?rect.bottom+6:rect.top-h-6,bottom-8-h));
      popover.style.left=x+'px';popover.style.top=y+'px';popover.dataset.anchored='true';
    };
    const focusItem=(last=false)=>{position();const enabled=items();(last?enabled.at(-1):enabled[0])?.focus({preventScroll:true});};
    listen(menu,'toggle',()=>{trigger.setAttribute('aria-expanded',String(menu.open));if(menu.open)position();else delete popover.dataset.anchored;});
    listen(trigger,'click',event=>{event.preventDefault();if(menu.open)close();else{menu.open=true;trigger.setAttribute('aria-expanded','true');position();}});
    listen(trigger,'keydown',event=>{if(!['ArrowDown','ArrowUp'].includes(event.key))return;event.preventDefault();menu.open=true;focusItem(event.key==='ArrowUp');});
    listen(menu,'keydown',event=>{if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();close(true);return;}if(event.target===trigger||!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;const enabled=items(),index=enabled.indexOf(document.activeElement);if(index<0)return;event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?enabled.length-1:(index+(event.key==='ArrowDown'?1:-1)+enabled.length)%enabled.length;enabled[next]?.focus({preventScroll:true});enabled[next]?.scrollIntoView({block:'nearest'});},true);
    listen(document,'pointerdown',event=>{if(menu.open&&!menu.contains(event.target))close();},true);
    listen(document,'focusin',event=>{if(menu.open&&!menu.contains(event.target))close();});
    for(const type of ['tenant-view','tenant-profile','tenant-auth-invalid'])listen(document,type,()=>close());
    listen(document,'tenant-language',()=>{trigger.setAttribute('aria-label',toolsLabel());trigger.title=toolsLabel();popover.setAttribute('aria-label',toolsLabel());if(menu.open)requestAnimationFrame(position);});
    if(window.ResizeObserver){const observer=new ResizeObserver(()=>{if(menu.open)position();});observer.observe(popover);if(pane)observer.observe(pane);listeners.push(()=>observer.disconnect());}
    listen(window,'resize',position);listen(window,'scroll',position);listen(window.visualViewport,'resize',position);listen(window.visualViewport,'scroll',position);
    toolsPopoverCleanup=()=>listeners.forEach(remove=>remove());
  }
  const endpoint=window.TENANT_AI_AGENTS_CONFIG?.endpoint||'/api/platform/ai/';
  const toolsLabel=()=>({ru:'Дополнительные действия',az:'Əlavə əməliyyatlar',en:'More actions'}[i18n.language]||'More actions');
  const pullRefreshLabel=()=>({ru:'Обновляем список…',az:'Siyahı yenilənir…',en:'Refreshing conversations…'}[i18n.language]||'Refreshing conversations…');
  const composerStyle=document.createElement('style');composerStyle.textContent='.operator-reply-action{display:inline-flex!important;align-items:center;justify-content:center;flex:none;min-width:36px;min-height:36px;border:0;border-radius:8px;background:var(--card);color:var(--ink);font-size:20px;line-height:1;cursor:pointer}.operator-reply-action:focus-visible{outline:2px solid var(--brand);outline-offset:2px}#operatorTrainingMenuAction{display:none!important}@media(max-width:900px){.operator-reply-action{min-width:44px;min-height:44px}#operatorTraining{display:none!important}#operatorTrainingMenuAction{display:grid!important}}.operator-quote,#operatorReplyQuote{display:flex;align-items:center;gap:8px;padding:6px 9px;border-left:3px solid var(--brand);background:var(--paper);font-size:12px;overflow-wrap:anywhere}.operator-quote{width:100%;text-align:left;cursor:pointer}.operator-quote[hidden],#operatorReplyQuote[hidden]{display:none}.operator-composer:focus-within{border-color:var(--brand)}.operator-composer textarea{min-height:38px!important;background:transparent!important;border:0!important;box-shadow:none!important;padding:5px 7px!important}.operator-native-audio{display:block;width:min(320px,100%);max-width:100%}.operator-media-card[data-native-audio=true]{display:grid;grid-template-columns:minmax(0,1fr);gap:5px}.operator-row-avatar{position:relative;overflow:hidden}.operator-row-avatar-image{position:absolute;inset:0;width:100%;height:100%;border-radius:inherit;object-fit:cover;background:var(--card)}.operator-row-avatar-image:not([src]){display:none}.operator-header-avatar{position:relative;display:grid;place-items:center;flex:none;width:34px;height:34px;margin-right:7px;vertical-align:middle;font-weight:600;line-height:1}';document.head.append(composerStyle);
  composerStyle.textContent+=`
    #operatorReplyQuote>span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    #operatorReplyQuote .operator-cancel-reply{flex:none;display:grid;place-items:center;width:32px;height:32px;min-width:32px;min-height:32px;padding:0;border:0;border-radius:7px;background:transparent;color:var(--ink);font-size:22px;line-height:1;cursor:pointer}
    #operatorReplyQuote .operator-cancel-reply:is(:hover,:focus-visible){background:var(--card)}
    @media(max-width:900px){#operatorReplyQuote .operator-cancel-reply{width:44px;height:44px;min-width:44px;min-height:44px}}
  `;
  composerStyle.textContent+=`.operator-active-lifecycle-actions{display:inline-flex;gap:4px;align-items:center;flex:none;white-space:nowrap}.operator-active-lifecycle-actions:empty{display:none}.operator-active-lifecycle-actions button[aria-pressed=true]:disabled{opacity:1;color:var(--ink);background:var(--paper)}.operator-composer-actions .operator-icon-action{display:grid;place-items:center;flex:none;width:40px;min-width:40px;height:40px;min-height:40px;padding:8px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);line-height:1}.operator-composer-actions .operator-icon-action svg{width:21px;height:21px;fill:currentColor}.operator-composer-actions .operator-icon-action:hover{border-color:var(--brand);color:var(--brand)}.operator-composer-actions .operator-icon-action:focus-visible{outline:2px solid var(--brand);outline-offset:2px}@media(max-width:900px){.operator-composer-actions .operator-icon-action{width:44px;min-width:44px;height:44px;min-height:44px}}.operator-voice-wave{display:flex;align-items:center;justify-content:center;gap:2px;width:24px;height:24px}.operator-voice-wave i{display:block;width:3px;height:6px;border-radius:3px;background:currentColor;transform:scaleY(.6)}#operatorCopilotVoiceToggle[data-voice-state=listening]{color:#16885f;border-color:#16885f}#operatorCopilotVoiceToggle[data-voice-state=listening] .operator-voice-wave i{animation:operatorVoiceListen .8s ease-in-out infinite alternate}#operatorCopilotVoiceToggle[data-voice-state=responding]{color:#7357d5;border-color:#7357d5}#operatorCopilotVoiceToggle[data-voice-state=responding] .operator-voice-wave i{animation:operatorVoiceSpeak .52s ease-in-out infinite alternate}#operatorCopilotVoiceToggle[data-voice-state=connecting],#operatorCopilotVoiceToggle[data-voice-state=stopping]{color:var(--brand);border-color:var(--brand)}#operatorCopilotVoiceToggle[data-voice-state=connecting] .operator-voice-wave i,#operatorCopilotVoiceToggle[data-voice-state=stopping] .operator-voice-wave i{animation:operatorVoiceListen .45s ease-in-out infinite alternate}#operatorCopilotVoiceToggle[data-voice-state=muted]{opacity:.62}@keyframes operatorVoiceSpeak{from{transform:scaleY(.55)}to{transform:scaleY(2.5)}}@keyframes operatorVoiceListen{from{transform:scaleY(.65)}to{transform:scaleY(1.45)}}#operatorCopilotVoiceToggle .operator-voice-wave i:nth-child(2),#operatorCopilotVoiceToggle .operator-voice-wave i:nth-child(4){animation-delay:.12s}#operatorCopilotVoiceToggle .operator-voice-wave i:nth-child(3){animation-delay:.24s}@media(prefers-reduced-motion:reduce){#operatorCopilotVoiceToggle .operator-voice-wave i{animation:none!important;transform:none!important}}`;
  composerStyle.textContent+=`#operatorMergedReadRoutes[hidden]{display:none}#operatorMergedReadRoutes{flex:none;min-width:0;padding:4px 10px;border-bottom:1px solid var(--line);font-size:12px}#operatorMergedReadRoutes label{display:flex;align-items:center;flex-wrap:wrap;gap:6px;min-width:0}#operatorMergedReadRoutes select{width:auto;max-width:100%;min-height:36px;font:inherit;background:var(--card);color:var(--ink)}#operatorComposerSource[hidden]{display:none}#operatorComposerSource{grid-column:1/-1;min-width:0;padding:2px 7px;font-size:12px}#operatorComposerSource label{display:flex;align-items:center;gap:5px;min-width:0}#operatorComposerSource select{min-width:0;max-width:100%;width:auto;border:0;background:transparent;color:var(--brand);font:inherit;min-height:28px;padding:2px 4px}.operator-group-sources{display:flex;flex-wrap:wrap;gap:4px;font-size:11px}`;
  composerStyle.textContent+=`.operator-case-draft-review{box-sizing:border-box;flex:none;max-height:35dvh;overflow:auto;padding:12px;background:var(--paper);border:1px solid var(--line);border-radius:10px}.operator-case-draft-review p{white-space:pre-wrap;overflow-wrap:anywhere}.operator-case-draft-review button{min-height:44px}`;
  composerStyle.textContent+=`.operator-message{position:relative}.operator-reply-action{position:absolute;top:10px;right:auto;left:calc(100% + 4px);transform:none;opacity:1;pointer-events:auto}@media (min-width:901px){.operator-message[data-direction=outgoing] .operator-reply-action{left:-44px}.operator-reply-action{opacity:0;pointer-events:auto;transition:opacity .14s ease}.operator-message:hover .operator-reply-action,.operator-message:focus-within .operator-reply-action,.operator-reply-action:hover,.operator-reply-action:focus-visible{opacity:1;pointer-events:auto}}@media(max-width:900px){.operator-message{touch-action:pan-y}.operator-message:has(.operator-media-card){touch-action:auto}.operator-message-meta{padding-right:0}.operator-message .operator-reply-action{position:absolute;top:auto;right:8px;bottom:6px;left:auto;width:1px;height:1px;min-width:1px;min-height:1px;padding:0;transform:none;opacity:0;clip-path:inset(50%);overflow:hidden;pointer-events:none}.operator-message .operator-reply-action:focus-visible{width:44px;height:44px;min-width:44px;min-height:44px;opacity:1;clip-path:none;pointer-events:auto;outline:2px solid var(--brand);outline-offset:2px}}.operator-message[data-swipe-reply-feedback=true]{border-color:var(--brand);transition:border-color .16s ease}@media(prefers-reduced-motion:reduce){.operator-reply-action,.operator-message[data-swipe-reply-feedback=true]{transition:none}}.operator-reply-context-menu{position:fixed;z-index:10050;display:grid;min-width:160px;max-width:min(240px,calc(100vw - 16px));padding:5px;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:10px;box-shadow:0 8px 28px #0004}.operator-reply-context-menu button{display:block;width:100%;min-height:44px;padding:8px 12px;border:0;border-radius:7px;background:transparent;color:inherit;text-align:left;font:inherit;cursor:pointer}.operator-reply-context-menu button:focus-visible{outline:2px solid var(--brand);outline-offset:-2px}.operator-reply-context-menu [role=status]{padding:4px 10px;font-size:12px}.operator-diagnostic-audio{margin-top:14px;padding-top:10px;border-top:1px solid var(--line)}.operator-diagnostic-audio h3{margin:0 0 8px;font-size:15px}.operator-diagnostic-audio [data-audio-diagnostic]{padding:10px 0;border-bottom:1px solid var(--line)}.operator-diagnostic-audio [data-audio-diagnostic]>summary{cursor:pointer;min-height:44px;padding:10px 0}.operator-diagnostic-audio [data-audio-diagnostic]>div{margin:4px 0;overflow-wrap:anywhere}.operator-quote-receipt-diagnostic{margin-top:14px;padding-top:10px;border-top:1px solid var(--line)}.operator-quote-receipt-diagnostic>summary{cursor:pointer;min-height:44px;padding:10px 0}`;
  composerStyle.textContent+=`.operator-audio-transcription-menu{position:relative;justify-self:start}.operator-audio-transcription-menu>summary{display:grid;place-items:center;width:34px;height:34px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);font-size:20px;line-height:1;cursor:pointer;list-style:none}.operator-audio-transcription-menu>summary::-webkit-details-marker{display:none}.operator-audio-transcription-menu>summary:focus-visible{outline:2px solid var(--brand);outline-offset:2px}.operator-audio-transcription-popover{position:absolute;z-index:12;left:0;top:calc(100% + 4px);display:none;width:min(300px,calc(100vw - 32px));max-height:min(50vh,360px);overflow:auto;padding:8px;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:10px;box-shadow:0 8px 24px #0003}.operator-audio-transcription-menu[open] .operator-audio-transcription-popover{display:grid;gap:6px}.operator-audio-transcription-popover [role=status]{font-size:12px;color:var(--muted);overflow-wrap:anywhere}.operator-audio-transcription-popover button{min-height:40px;text-align:left;white-space:normal}.operator-audio-transcription-popover button:disabled{opacity:.6}.operator-audio-transcription-popover .operator-media-menu-actions{display:grid;gap:4px;padding-bottom:6px;border-bottom:1px solid var(--line)}.operator-audio-transcription-popover .operator-media-menu-actions button{width:100%;padding:8px 10px;border:0;border-radius:6px;background:transparent;color:inherit}.operator-audio-transcription-popover .operator-media-menu-actions button:hover,.operator-audio-transcription-popover .operator-media-menu-actions button:focus-visible{background:var(--paper)}.operator-audio-transcription-history{font-size:12px}.operator-audio-transcription-history summary{min-height:36px;padding:7px 0;cursor:pointer;color:var(--brand)}.operator-audio-transcription-history p{white-space:pre-wrap;overflow-wrap:anywhere;margin:5px 0;padding:6px;background:var(--paper);border-radius:6px}.operator-media-card[data-native-audio=true]{grid-template-columns:minmax(0,1fr) auto;align-items:center}.operator-media-card[data-native-audio=true] .operator-native-audio{grid-column:1/-1}.operator-media-card[data-native-audio=true] .operator-media-analysis,.operator-media-card[data-native-audio=true] .operator-media-file-meta{grid-column:1/-1}@media(max-width:900px){.operator-audio-transcription-menu>summary{width:44px;height:44px}.operator-audio-transcription-popover{position:fixed;left:12px;right:12px;top:auto;bottom:calc(12px + env(safe-area-inset-bottom,0px));width:auto;max-height:min(45vh,360px)}}`;
  composerStyle.textContent+=`.operator-media-menu{position:relative;display:inline-flex;flex:none}.operator-media-menu>summary{display:grid;place-items:center;width:36px;height:36px;min-width:36px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);font-size:20px;line-height:1;cursor:pointer;list-style:none}.operator-media-menu>summary::-webkit-details-marker{display:none}.operator-media-menu>summary:focus-visible{outline:2px solid var(--brand);outline-offset:2px}.operator-media-menu[open]{z-index:20}.operator-media-menu>div{display:none;position:absolute;z-index:21;right:0;top:calc(100% + 4px);min-width:190px;max-width:calc(100vw - 32px);padding:5px;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:9px;box-shadow:0 8px 24px #0003}.operator-media-menu[open]>div{display:grid;gap:4px}.operator-media-menu button{display:block;width:100%;min-height:44px;padding:8px 10px;border:0;border-radius:6px;background:transparent;color:inherit;text-align:left;white-space:normal}.operator-media-menu button:hover,.operator-media-menu button:focus-visible{background:var(--paper)}.operator-media-menu button:disabled{opacity:.6}`;
  const voiceCardStyle=document.createElement('style');voiceCardStyle.textContent=`
    .operator-message:has([data-native-audio=true]){box-sizing:border-box;width:min(360px,100%);min-width:0}
    .operator-message .operator-media-card[data-native-audio=true]{box-sizing:border-box;display:grid;width:100%;min-width:0;grid-template-columns:minmax(0,1fr);grid-template-rows:auto 54px 18px auto;gap:6px;padding:8px;align-items:center}
    [data-native-audio=true] .operator-audio-heading{grid-row:1;display:flex;flex-wrap:wrap;align-items:center;gap:4px;min-width:0;font-size:12px;line-height:1.35;overflow-wrap:anywhere}[data-native-audio=true] .operator-audio-duration{white-space:nowrap;color:var(--muted);font-weight:400}
    [data-native-audio=true]>.operator-audio-controls{grid-row:2;box-sizing:border-box;width:100%;min-width:0;height:54px}[data-native-audio=true] .operator-native-audio{display:block;box-sizing:border-box;width:100%;height:54px;max-width:100%}[data-native-audio=true]>.operator-audio-controls[aria-disabled=true]{opacity:.55;cursor:default}[data-native-audio=true]>.operator-audio-controls[aria-disabled=true] audio{pointer-events:none}
    [data-native-audio=true]>button[data-media-open]{display:none!important;grid-row:3;box-sizing:border-box;justify-self:start;min-height:44px;max-width:100%;margin:0;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);font:inherit;font-size:13px;white-space:normal;overflow-wrap:normal;word-break:normal;cursor:pointer}[data-native-audio=true]>button[data-media-open]:disabled{opacity:.65;cursor:default}[data-native-audio=true]>button[data-media-open]:focus-visible{outline:2px solid var(--brand);outline-offset:2px}
    [data-native-audio=true]>[data-media-load-status]{grid-row:3;min-width:0;min-height:18px;font-size:12px;line-height:1.4;overflow-wrap:anywhere}
    [data-native-audio=true][data-audio-state=loaded]>[data-media-load-status]{grid-row:3;display:flex;align-items:center}[data-native-audio=true]>.operator-audio-analysis{grid-row:4;min-width:0;border-top:1px solid var(--line);padding-top:2px}[data-native-audio=true]>.operator-audio-analysis>summary{display:flex;align-items:center;min-height:36px;cursor:pointer;font-size:12px;color:var(--muted)}[data-native-audio=true] .operator-media-analysis{display:block;white-space:normal;overflow-wrap:anywhere;font-size:12px;line-height:1.4}[data-native-audio=true] [data-media-meta]{max-width:100%;min-width:0;overflow-wrap:anywhere;font-size:11px;color:var(--muted)}[data-native-audio=true] [data-media-meta]:empty{display:none}
    .operator-photo-analysis{border-top:1px solid var(--line);font-size:12px;color:var(--muted)}.operator-photo-analysis>summary{min-height:44px;display:flex;align-items:center;cursor:pointer}
    .operator-message .operator-media-card>button[data-photo-preview]{position:relative;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:5px;box-sizing:border-box;width:min(260px,100%);min-width:0;min-height:150px;overflow:hidden;padding:12px;background:var(--paper);color:var(--muted);white-space:normal}.operator-media-card [data-photo-preview] [data-media-thumbnail]{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;filter:blur(7px);opacity:.55;transform:scale(1.1);pointer-events:none}.operator-media-card [data-photo-preview]>span,.operator-media-card [data-photo-preview]>small{position:relative;z-index:1;background:var(--card);padding:3px 5px;border-radius:4px;max-width:100%;overflow-wrap:anywhere}.operator-media-card [data-photo-preview] [data-photo-placeholder]{width:48px;height:48px}.operator-media-card[data-media-kind=image]>[data-media-preview=image]{display:block;max-width:100%;height:auto;max-height:320px;object-fit:contain}
    .operator-copy-control{display:inline-flex;align-items:center;gap:3px;max-width:100%;vertical-align:middle}.operator-copy-message{display:inline-grid;place-items:center;min-width:32px;min-height:32px;margin:0 0 0 3px;padding:0;border:0;background:transparent;color:var(--muted);cursor:pointer;vertical-align:middle}.operator-copy-message:focus-visible{outline:2px solid var(--brand);outline-offset:1px}.operator-copy-control [data-copy-status]{font-size:11px;color:var(--muted);overflow-wrap:anywhere}.operator-copy-control [data-copy-status]:empty{display:none}@media(max-width:900px){.operator-copy-message{min-width:44px;min-height:44px}}
    .operator-message .operator-message-meta{display:flex;flex-wrap:wrap;align-items:flex-end;gap:4px;margin-top:4px;min-width:0}.operator-message-origin{display:flex;flex:1 1 130px;flex-wrap:wrap;align-items:center;gap:4px;min-width:0;overflow-wrap:anywhere}.operator-message-actions{display:inline-flex;align-items:center;justify-content:flex-end;gap:2px;flex:0 1 auto;max-width:100%;margin-left:auto}.operator-copy-control{min-width:0}.operator-copy-control [data-copy-status]{max-width:150px}.operator-delivery-ticks{display:inline-flex;flex:none;align-items:center;color:#64746f}.operator-delivery-ticks[data-delivery-state=read]{color:#096db3}.operator-delivery-note{font-size:11px;max-width:170px;overflow-wrap:anywhere;color:var(--muted)}.operator-delivery-note[data-delivery-state=failed]{color:var(--danger)}html[data-app-theme=dark] .operator-delivery-ticks{color:#aab8b2}html[data-app-theme=dark] .operator-delivery-ticks[data-delivery-state=read]{color:#73c7ff}.operator-reply-context-menu [role=status]:empty{display:none}@media(max-width:900px){.operator-message:has(.operator-reply-action:focus-visible) .operator-message-actions{margin-right:48px}}
    .operator-reaction-row{display:flex;flex-wrap:wrap;gap:4px;margin-top:4px}.operator-reaction-row[hidden]{display:none}
    [data-whatsapp-reaction]:not([hidden]),.operator-peer-reaction{display:inline-flex;align-items:center;gap:4px;padding:2px 7px;border:1px solid var(--line);border-radius:14px;background:var(--card)}
    .operator-reaction-picker{display:grid;grid-template-columns:repeat(3,1fr);gap:2px;border-top:1px solid var(--line)}
    .operator-reaction-picker>span{grid-column:1/-1;padding:5px 10px;font-size:12px;color:var(--muted)}
    .operator-reaction-picker button{text-align:center;font-size:18px}.operator-reaction-picker button[aria-checked=true]{background:var(--line)}
    [data-media-actions][hidden]{display:none!important}.operator-reply-context-menu{box-sizing:border-box;max-width:min(300px,calc(100vw - 16px));max-height:calc(100dvh - 16px);overflow-y:auto;overscroll-behavior:contain}.operator-reply-context-menu [data-context-file]{min-width:0;border-top:1px solid var(--line);padding-top:4px}.operator-reply-context-menu [data-context-file]>strong{display:block;padding:6px 10px;font-size:12px;overflow-wrap:anywhere}.operator-reply-context-menu [data-audio-transcription-menu]{display:grid;gap:4px}.operator-reply-context-menu [data-audio-transcription-actions]{display:grid;gap:4px}.operator-reply-context-menu p{white-space:pre-wrap;overflow-wrap:anywhere}.operator-reply-context-menu button:disabled{opacity:.6;cursor:default}
  `;document.head.append(voiceCardStyle);
  function bindNativeAudioErrors(){document.querySelectorAll('[data-native-audio] audio').forEach(audio=>{if(audio.dataset.errorBound)return;audio.dataset.errorBound='true';audio.addEventListener('error',()=>{if(!audio.isConnected||!audio.getAttribute('src')||!audio.error)return;const status=audio.closest('[data-native-audio]')?.querySelector('[data-media-load-status]');if(status)status.textContent=({ru:'Аудио недоступно. Повторите позже.',az:'Audio əlçatan deyil. Sonra yenidən yoxlayın.',en:'Audio unavailable. Try again later.'}[i18n.language]||'Audio unavailable. Try again later.');});});}
  document.addEventListener('tenant-chat-attachments-ready',bindNativeAudioErrors);
  const operatorListScroller=()=>window.matchMedia?.('(max-width:900px)')?.matches?($('operatorListScroll')||$('operatorRows')):$('operatorRows');
  let member=null,capabilities={},epoch=0,listRevision=0,openRevision=0,diagnosticRevision=0,groupSettingsRevision=0,rows=[],nextOffset=null,currentDialog=null,selectedDialog=null,selectedDialogOwner='',viewReturnDialog=null,viewReturnDialogOwner='',currentPage=null,diagnosticScroll=null,diagnosticFocus=null,timer,bulkPreviewData=null,listScrollTop=0,autoRefreshTimer=null,autoRefreshBusy=false,pullRefreshBusy=false,pullStartY=null,hasInitialDialogSnapshot=false,lastDialogSnapshotAt=0;
  const dialogPageCache=new Map(),DIALOG_PAGE_CACHE_LIMIT=6;
  // Keep mounted metadata separate: history reads can mutate displayedPage.dialog before rendering.
  let copilotDeferredPage=null,displayedPage=null,displayedLanguage=null,displayedRouteScope=null,dialogReadSerial=0,dialogRenderSerial=0,historyUpdateSerial=0;
  const pendingCopilotRequests=new Map();
  const COPILOT_PENDING_PER_DIALOG=20,COPILOT_PENDING_TOTAL=256;
  const latestMessageIds=new Map(),AUTO_REFRESH_MS=30000;
  let listFlight=null,lastListKey='',lastListAt=0,permissionScope='',pendingDialogRoute=null,caseLinkRevision=0,caseLinkPending=false,markAllReadBusy=false;
  const readMarkers=new Map(),readInFlight=new Set(),readSyncKey=()=>`tenant.operator.read.${identity(member)}`;
  let soundEnabled=true,soundUnlocked=false,soundContext=null;
  const soundPrefKey=()=>`tenant.operator.message-sound.${member?.tenant_id||''}.${member?.telegram_id||''}`;
  const soundCopy=()=>({ru:{on:'Звук новых сообщений: вкл.',off:'Звук новых сообщений: выкл.',unlock:'Нажмите, чтобы разрешить звук'},az:{on:'Yeni mesaj səsi: aktiv',off:'Yeni mesaj səsi: sönülü',unlock:'Səsi aktiv etmək üçün klikləyin'},en:{on:'New message sound: on',off:'New message sound: off',unlock:'Click to enable sound'}}[i18n.language]||{on:'New message sound: on',off:'New message sound: off',unlock:'Click to enable sound'});
  function loadSoundPreference(){try{soundEnabled=localStorage.getItem(soundPrefKey())!=='off';}catch{soundEnabled=true;}updateSoundButton();}
  function updateSoundButton(){const button=$('operatorSoundToggle');if(!button)return;const copy=soundCopy();button.textContent=soundEnabled?(soundUnlocked?copy.on:copy.unlock):copy.off;button.setAttribute('aria-pressed',String(soundEnabled));button.title=button.textContent;}
  const browserNoticeCopy=()=>({ru:{enable:'Подключить браузерные уведомления',enabled:'Push подключён на этом устройстве',denied:'Уведомления запрещены в настройках браузера',unsupported:'Браузер не поддерживает Push',connecting:'Подключаем Push…',checking:'Проверяем подключение Push…',verify:'Не удалось проверить Push. Нажмите, чтобы проверить снова.',connected:'Устройство подключено. Для входящих сообщений уведомление должно быть разрешено в настройках сотрудника.',profile:'Настройка Push недоступна. Откройте профиль → Push-уведомления.'},az:{enable:'Brauzer bildirişlərini qoş',enabled:'Push bu cihazda qoşulub',denied:'Brauzer ayarlarında bildirişlər bloklanıb',unsupported:'Brauzer Push dəstəkləmir',connecting:'Push qoşulur…',checking:'Push bağlantısı yoxlanılır…',verify:'Push yoxlanmadı. Yenidən yoxlamaq üçün klikləyin.',connected:'Cihaz qoşuldu. Daxil olan mesaj bildirişləri əməkdaş ayarlarında aktiv olmalıdır.',profile:'Push ayarı əlçatan deyil. Profil → Push bildirişlərini açın.'},en:{enable:'Connect browser notifications',enabled:'Push connected on this device',denied:'Notifications are blocked in browser settings',unsupported:'This browser does not support Push',connecting:'Connecting Push…',checking:'Checking Push connection…',verify:'Could not verify Push. Click to check again.',connected:'Device connected. Incoming-message notifications must be enabled in employee settings.',profile:'Push setup is unavailable. Open Profile → Push notifications.'}}[i18n.language]||{enable:'Connect browser notifications',enabled:'Push connected on this device',denied:'Notifications are blocked in browser settings',unsupported:'This browser does not support Push',connecting:'Connecting Push…',checking:'Checking Push connection…',verify:'Could not verify Push. Click to check again.',connected:'Device connected. Incoming-message notifications must be enabled in employee settings.',profile:'Push setup is unavailable. Open Profile → Push notifications.'});
  let pushDeviceConnected=window.tenantPushConnected===true;
  const notificationReceipts=new Set();
  const notificationReceiptKey=()=>`tenant.operator.browser-notice-receipts.${identity(member)}`;
  function updateBrowserNoticeButton(){const button=$('operatorNotificationToggle'),status=$('operatorNotificationStatus');if(!button)return;if(typeof window.tenantPushConnected==='boolean')pushDeviceConnected=window.tenantPushConnected;const copy=browserNoticeCopy(),supported=!!window.Notification,permission=supported?Notification.permission:'unsupported',state=window.tenantPushConnectionState;button.textContent=state==='loading'?copy.checking:state==='error'?copy.verify:permission==='granted'&&pushDeviceConnected?copy.enabled:permission==='denied'?copy.denied:permission==='unsupported'?copy.unsupported:copy.enable;button.disabled=permission==='denied'||permission==='unsupported'||state==='loading';button.setAttribute('aria-pressed',String(permission==='granted'&&pushDeviceConnected&&state!=='loading'&&state!=='error'));button.title=button.textContent;if(status&&permission==='default')status.textContent='';}
  window.tenantOperatorUpdateBrowserNotice=updateBrowserNoticeButton;
  async function requestBrowserNoticePermission(){const button=$('operatorNotificationToggle'),status=$('operatorNotificationStatus'),copy=browserNoticeCopy();if(!window.Notification){if(status)status.textContent=copy.unsupported;updateBrowserNoticeButton();return;}if(Notification.permission==='denied'){if(status)status.textContent=copy.denied;updateBrowserNoticeButton();return;}if(button)button.disabled=true;if(status)status.textContent=copy.connecting;try{if(typeof window.tenantPushEnable!=='function'){if(status)status.textContent=copy.profile;return;}const result=await window.tenantPushEnable();pushDeviceConnected=typeof result==='boolean'?result:result?.connected===true;window.tenantPushConnected=pushDeviceConnected;if(status)status.textContent=pushDeviceConnected?copy.connected:(typeof result==='object'&&result?.message)||copy.profile;}catch{if(status)status.textContent=copy.profile;}finally{updateBrowserNoticeButton();if(button&&Notification.permission!=='denied'&&Notification.permission!=='unsupported')button.disabled=false;}}
  function notifyIncomingOnce(row,messageId){const activelyViewing=sameDialog(row,currentDialog)&&!document.hidden&&$('view-dialogs')?.classList.contains('active');if(pushDeviceConnected||window.tenantPushConnected===true||!window.Notification||Notification.permission!=='granted'||!messageId||activelyViewing)return;const owner=identity(member),receipt=String(messageId),key=notificationReceiptKey();if(notificationReceipts.has(receipt))return;notificationReceipts.add(receipt);try{const recent=JSON.parse(localStorage.getItem(key)||'[]');if(recent.includes(receipt))return;localStorage.setItem(key,JSON.stringify([...recent.slice(-99),receipt]));}catch{}if(owner!==identity(member)||!hasRight('dialogs')||activelyViewing)return;const body=String(row.last_message_text||row.last_message_preview||'New customer message').replace(/\s+/g,' ').trim().slice(0,180);try{const notification=new Notification(dialogTitle(row),{body:body||'New customer message',tag:'chat-'+String(row.lead_id||''),renotify:false,silent:true,icon:'/icon-192.png'});notification.onclick=()=>{notification.close?.();try{window.focus();}catch{}window.tenantAppNavigate?.('dialogs',false);if(!sameDialog(row,currentDialog))setTimeout(()=>{if(owner===identity(member)&&hasRight('dialogs'))void openDialog(row,true);},0);};}catch{}}
  async function unlockMessageSound(){if(!soundEnabled)return;try{const Audio=window.AudioContext||window.webkitAudioContext;if(!Audio)return;soundContext=soundContext||new Audio();await soundContext.resume();soundUnlocked=soundContext.state==='running';updateSoundButton();}catch{soundUnlocked=false;updateSoundButton();}}
  let messageSoundUntil=0;
  function playMessageSound(){
    if(!soundEnabled)return;
    if(!soundUnlocked||!soundContext||soundContext.state!=='running'){
      const prompt=$('operatorSoundStatus');if(prompt)prompt.textContent=soundCopy().unlock;
      return;
    }
    const now=soundContext.currentTime;
    if(now<messageSoundUntil)return;
    messageSoundUntil=now+.32; // A burst of incoming messages plays one clear chime.
    try{
      for(const [pitch,delay,duration] of [[950,0,.12],[1270,.105,.19]]){
        const oscillator=soundContext.createOscillator(),gain=soundContext.createGain(),at=now+delay;
        oscillator.type='triangle';oscillator.frequency.value=pitch;
        gain.gain.setValueAtTime(.0001,at);
        gain.gain.exponentialRampToValueAtTime(.145,at+.016);
        gain.gain.exponentialRampToValueAtTime(.0001,at+duration);
        oscillator.connect(gain);gain.connect(soundContext.destination);
        oscillator.onended=()=>{oscillator.disconnect();gain.disconnect();};
        oscillator.start(at);oscillator.stop(at+duration+.01);
      }
    }catch{}
  }
  function soundOnce(row,messageId){
    const owner=identity(member),receipt=dialogKey(row)+':'+messageId,key='tenant.operator.sound-receipts.'+owner;
    const play=()=>{if(owner!==identity(member)||!soundEnabled||!soundUnlocked)return;try{const recent=JSON.parse(localStorage.getItem(key)||'[]');if(recent.includes(receipt))return;localStorage.setItem(key,JSON.stringify([...recent.slice(-99),receipt]));}catch{}playMessageSound();};
    if(navigator.locks?.request)void navigator.locks.request('tenant.operator.sound.'+owner,play).catch(()=>{});else play();
  }
  const identity=m=>m?String(m.tenant_id)+':'+String(m.telegram_id):'';
  const dialogKey=row=>[row?.provider||'connect',row?.channel_id||'',row?.conversation_id||'',row?.lead_id||''].join(':');
  const sameDialog=(a,b)=>!!a&&!!b&&dialogKey(a)===dialogKey(b);
  function stopAutoRefresh(){clearTimeout(autoRefreshTimer);autoRefreshTimer=null;}
  function scheduleAutoRefresh(delay=AUTO_REFRESH_MS){stopAutoRefresh();const backgroundNoticePolling=document.hidden&&window.Notification?.permission==='granted'&&!pushDeviceConnected;if((document.hidden&&!backgroundNoticePolling)||!hasRight('dialogs'))return;autoRefreshTimer=setTimeout(async()=>{if(autoRefreshBusy){scheduleAutoRefresh();return;}autoRefreshBusy=true;void refreshClientSupportCase();try{await refresh();}finally{autoRefreshBusy=false;scheduleAutoRefresh();}},delay);}
  const helperDrafts=new Map();let helperHistoryClosing=false;
  const contextKey=(kind,route=currentDialog)=>`tenant.operator.${kind}.${member?.tenant_id||''}.${member?.telegram_id||''}.${kind==='route'?'':(route?.provider||'connect')+'.'+(route?.channel_id||'')+'.'+(route?.conversation_id||'')}`;
  const atBottom=node=>!node||node.scrollHeight-node.scrollTop-node.clientHeight<28;
  const afterLayout=callback=>typeof window.requestAnimationFrame==='function'?window.requestAnimationFrame(callback):Promise.resolve().then(callback);
  const canCreateSupport=()=>capabilities.support?.create??hasRight('support');
  // Page admission includes scoped dialog access; a coarse module grant does not.
  // Per-dialog capabilities (including can_send) remain authoritative for actions.
  const hasRight=right=>!!member&&member.active!==false&&(
    right==='dialogs'&&typeof capabilities.pages?.dialogs==='boolean'
      ?capabilities.pages.dialogs
      :(capabilities.modules?.[right]??(member.role==='owner'||(member.permissions||[]).includes(right))));
  function closeCopilotPanel(fromHistory=false){
    const panel=$('operatorCopilot');if(!panel)return;
    panel.querySelector('[data-copilot-voice]')?.endVoice?.();
    panel.hidden=true;panel.removeAttribute('aria-modal');
    if($('operatorCopilotBackdrop'))$('operatorCopilotBackdrop').hidden=true;
    if($('operatorDialogBody'))$('operatorDialogBody').dataset.copilotOpen='false';
    $('operatorCopilotToggle')?.setAttribute('aria-expanded','false');
    if(!fromHistory&&!helperHistoryClosing&&history.state?.operatorHelper===contextKey('helper')){helperHistoryClosing=true;history.back();}
    if($('view-dialogs')?.classList.contains('active'))$('operatorCopilotToggle')?.focus({preventScroll:true});
  }
  function syncMobileViewport(){
    const height=window.visualViewport?.height||window.innerHeight;
    if(height>0)document.documentElement.style.setProperty('--operator-visual-height',`${Math.round(height)}px`);
    document.documentElement.style.setProperty('--operator-visual-top',`${Math.round(window.visualViewport?.offsetTop||0)}px`);
    const body=$('operatorDialogBody'),panel=$('operatorCopilot'),backdrop=$('operatorCopilotBackdrop');
    if(body&&panel&&!panel.hidden){const mobile=!!window.matchMedia?.('(max-width: 900px)').matches;body.dataset.copilotOpen=String(mobile);if(backdrop)backdrop.hidden=!mobile;}
  }
  window.visualViewport?.addEventListener('resize',syncMobileViewport,{passive:true});
  window.visualViewport?.addEventListener('scroll',syncMobileViewport,{passive:true});
  window.addEventListener('resize',syncMobileViewport,{passive:true});
  syncMobileViewport();
  const copilotText=key=>({question:{ru:'Вопрос',az:'Sual',en:'Question'},title:{ru:'AI-помощник',az:'AI köməkçi',en:'AI assistant'},voice:{ru:'Голосовой разговор с AI',az:'AI ilə səsli söhbət',en:'Voice conversation with AI'},solution:{ru:'Найти решение',az:'Həll yolu tap',en:'Find solution'},instruction:{ru:'Своя инструкция',az:'AI-yə tapşırıq ver',en:'Custom instruction'},prompt:{ru:'Инструкция для AI (не отправляется клиенту)',az:'AI üçün tapşırıq (müştəriyə göndərilmir)',en:'Instruction for AI (not sent to customer)'},ask:{ru:'Спросить AI',az:'AI-dən soruş',en:'Ask AI'},clear:{ru:'Очистить контекст',az:'Konteksti təmizlə',en:'Clear context'},knowledge:{ru:'Проверенные источники',az:'Təsdiqlənmiş mənbələr',en:'Verified sources'},working:{ru:'AI готовит ответ…',az:'AI cavab hazırlayır…',en:'AI is preparing…'},failed:{ru:'Не удалось получить ответ. Проверьте лимит и настройки Chat AI.',az:'Cavab alınmadı. AI limitini və ayarlarını yoxlayın.',en:'Could not get a response. Check Chat AI settings and limits.'},replace:{ru:'Заменить текущий текст сообщения ответом AI?',az:'Mövcud mesaj mətni AI cavabı ilə əvəz edilsin?',en:'Replace the current message with the AI reply?'},mediaConsent:{ru:'Для этого ответа Copilot может передать OpenAI до 2 последних фото и извлечь текст максимум из 2 последних PDF, TXT или DOCX из текущего диалога (не более 4 файлов). Продолжить?',az:'Bu cavab üçün Copilot cari dialoqdan son 2-yə qədər şəkli OpenAI-a göndərə və son 2 PDF, TXT və ya DOCX faylından mətn çıxara bilər (cəmi ən çox 4 fayl). Davam edilsin?',en:'For this reply, Copilot may send up to 2 recent photos to OpenAI and extract text from up to 2 recent PDF, TXT, or DOCX files in this conversation (4 files maximum). Continue?'},context:{ru:'Учтено',az:'Nəzərə alındı',en:'Used'},messages:{ru:'сообщений',az:'mesaj',en:'messages'},voices:{ru:'транскрипций аудио',az:'audio transkripsiyası',en:'audio transcripts'},images:{ru:'фото',az:'şəkil',en:'photos'},documents:{ru:'документов',az:'sənəd',en:'documents'},skipped:{ru:'Не удалось учесть',az:'Nəzərə alınmadı',en:'Could not include'},contextDetails:{ru:'Подробности',az:'Ətraflı',en:'Details'},contextReason:{ru:'тип — причина',az:'növ — səbəb',en:'type — reason'},skip_confirmation_required:{ru:'не включено без подтверждения',az:'təsdiq olmadan daxil edilmədi',en:'not included without confirmation'},skip_transcription_unavailable:{ru:'нет сохранённой транскрипции',az:'saxlanmış transkripsiya yoxdur',en:'no saved transcription'},skip_video_not_analyzed:{ru:'видео не анализировалось',az:'video təhlil edilməyib',en:'video was not analyzed'},skip_media_unavailable:{ru:'вложение недоступно или неподдерживаемого формата',az:'əlavə əlçatan deyil və ya format dəstəklənmir',en:'attachment unavailable or unsupported'},skip_rate_limited:{ru:'Connect ограничил запросы',az:'Connect sorğuları məhdudlaşdırdı',en:'Connect rate limit'},skip_model_no_vision:{ru:'модель не поддерживает изображения',az:'model şəkilləri dəstəkləmir',en:'model does not support images'},skip_context_limit:{ru:'сообщение вне выбранного контекста',az:'mesaj kontekstdən kənardadır',en:'message outside selected context'},skip_media_count_limit:{ru:'превышен лимит количества файлов',az:'fayl sayı limiti aşılıb',en:'file count limit reached'},skip_image_count_limit:{ru:'превышен лимит изображений',az:'şəkil limiti aşılıb',en:'image limit reached'},skip_document_count_limit:{ru:'превышен лимит документов',az:'sənəd limiti aşılıb',en:'document limit reached'},skip_media_byte_limit:{ru:'превышен лимит размера файлов',az:'fayl ölçüsü limiti aşılıb',en:'file size limit reached'},cleared:{ru:'Внутренний контекст очищен.',az:'Daxili kontekst təmizləndi.',en:'Internal context cleared.'}}[key]?.[i18n.language]||'AI assistant');
  const mediaAdmissionReasons={"ai_spend_media_price_unknown": {"ru": "Не настроен тариф ASR для выбранной модели.", "az": "Seçilmiş model üçün ASR tarifi qurulmayıb.", "en": "The selected model has no configured ASR tariff."}, "ai_spend_budget_exhausted": {"ru": "Исчерпан денежный лимит AI.", "az": "AI pul limiti tükənib.", "en": "The AI spending limit is exhausted."}, "ai_call_limit_exhausted": {"ru": "Исчерпан дневной лимит вызовов AI.", "az": "Gündəlik AI sorğu limiti tükənib.", "en": "The daily AI call limit is exhausted."}, "media_authority_changed": {"ru": "Сообщение, назначение агента или настройки изменились.", "az": "Mesaj, agent təyinatı və ya ayarlar dəyişib.", "en": "The message, agent assignment or settings changed."}, "source_access_denied": {"ru": "Нет доступа к источнику аудио.", "az": "Səs mənbəyinə giriş yoxdur.", "en": "Access to the audio source was denied."}, "media_admission_failed": {"ru": "Вызов ASR не допущен. Проверьте настройки AI.", "az": "ASR sorğusuna icazə verilmədi. AI ayarlarını yoxlayın.", "en": "ASR admission failed. Check AI settings."}};
  const mediaAdmissionReason=code=>mediaAdmissionReasons[code]?.[i18n.language]||'';
  const mediaSkipReason=reason=>mediaAdmissionReason(reason)||copilotText('skip_'+String(reason||'media_unavailable'));
  const copilotTemplateText=key=>({title:{ru:'Шаблоны вопросов',az:'Sual şablonları',en:'Question templates'},create:{ru:'Создать шаблон',az:'Şablon yarat',en:'Create template'},name:{ru:'Название',az:'Ad',en:'Name'},question:{ru:'Текст вопроса',az:'Sualın mətni',en:'Question text'},save:{ru:'Сохранить шаблон',az:'Şablonu saxla',en:'Save template'},cancel:{ru:'Отмена',az:'Ləğv et',en:'Cancel'},edit:{ru:'Изменить',az:'Dəyiş',en:'Edit'},delete:{ru:'Удалить',az:'Sil',en:'Delete'},deleteConfirm:{ru:'Удалить этот шаблон вопроса?',az:'Bu sual şablonu silinsin?',en:'Delete this question template?'},limit:{ru:'Можно сохранить не более 20 шаблонов.',az:'Ən çox 20 şablon saxlamaq olar.',en:'You can save up to 20 templates.'},empty:{ru:'Сохранённых шаблонов пока нет.',az:'Hələ saxlanmış şablon yoxdur.',en:'No saved templates yet.'},saveFailed:{ru:'Не удалось сохранить шаблон в этом браузере.',az:'Şablonu bu brauzerdə saxlamaq mümkün olmadı.',en:'Could not save the template in this browser.'},insertResult:{ru:'Вставить в сообщение',az:'Mesaja əlavə et',en:'Use in message'},staleResult:{ru:'Этот ответ относится к прежнему назначению диалога. Получите новый ответ.',az:'Bu cavab dialoqun əvvəlki təyinatına aiddir. Yeni cavab alın.',en:'This answer belongs to an earlier dialogue assignment. Get a new answer.'},inserted:{ru:'Черновик сообщения обновлён. Ничего не отправлено.',az:'Mesaj qaralaması yeniləndi. Heç nə göndərilmədi.',en:'Message draft updated. Nothing was sent.'}}[key]?.[i18n.language]||'Template');
  const copilotContextSignature=route=>JSON.stringify([dialogKey(route),route?.agent_id||'',route?.agent_mode||'',route?.assignment_revision||'',route?.agent_version||'']);
  async function copilotRequestIdentity(route,logical,current){
    const scope=identity(member)+'|'+dialogKey(route),owner=identity(member);
    const input=JSON.stringify([owner,route.provider||'connect',String(route.lead_id),route.channel_id||'',route.conversation_id||'',logical]);
    const digest=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(input)))].map(byte=>byte.toString(16).padStart(2,'0')).join('');
    if(owner!==identity(member)||!current())throw new Error(t('companyChanged'));
    let bucket=pendingCopilotRequests.get(scope);
    if(bucket?.has(digest))return {scope,digest,id:bucket.get(digest)};
    const total=[...pendingCopilotRequests.values()].reduce((n,values)=>n+values.size,0);
    if((bucket?.size||0)>=COPILOT_PENDING_PER_DIALOG||total>=COPILOT_PENDING_TOTAL)throw new Error(({ru:'Есть незавершённые запросы AI. Проверьте их состояние перед новой операцией.',az:'Tamamlanmamış AI sorğuları var. Yeni əməliyyatdan əvvəl statusu yoxlayın.',en:'There are unresolved AI requests. Check their status before starting another operation.'}[i18n.language]));
    if(!bucket){bucket=new Map();pendingCopilotRequests.set(scope,bucket);}
    const id=crypto.randomUUID();bucket.set(digest,id);return {scope,digest,id};
  }
  function finishCopilotRequest(handle){
    if(!handle)return;
    const bucket=pendingCopilotRequests.get(handle.scope);
    if(bucket?.get(handle.digest)!==handle.id)return;
    bucket.delete(handle.digest);if(!bucket.size)pendingCopilotRequests.delete(handle.scope);
  }

  const copilotTemplateStyle=document.createElement('style');copilotTemplateStyle.textContent='.operator-copilot-templates{display:grid;gap:8px;margin:8px 0}.operator-copilot-template-list{display:grid;gap:6px}.operator-copilot-template-row{display:flex;align-items:center;gap:6px}.operator-copilot-template-row [data-template-use]{flex:1;min-width:0;text-align:left;overflow-wrap:anywhere}.operator-copilot-template-row button{min-height:36px}.operator-copilot-templates label{display:grid;gap:4px}.operator-copilot-templates input,.operator-copilot-templates textarea{width:100%;box-sizing:border-box;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px}.operator-copilot-templates textarea{min-height:90px;resize:vertical}@media(max-width:900px){.operator-copilot-template-row button{min-height:44px}.operator-copilot-templates .operator-copilot-actions button{flex:1;min-height:44px}}';document.head.append(copilotTemplateStyle);
  function contextCount(key,value,language){const count=Math.max(0,Number(value)||0);if(language==='ru'){const rest=count%100,last=rest%10;let form;if(key==='messages')form=rest>=11&&rest<=14?'сообщений':last===1?'сообщение':last>=2&&last<=4?'сообщения':'сообщений';else if(key==='voices')form=rest>=11&&rest<=14?'транскрипций аудио':last===1?'транскрипция аудио':last>=2&&last<=4?'транскрипции аудио':'транскрипций аудио';else if(key==='documents')form=rest>=11&&rest<=14?'документов':last===1?'документ':last>=2&&last<=4?'документа':'документов';else form='фото';return `${count} ${form}`;}const forms={az:{messages:'mesaj',voices:'audio transkripsiyası',images:'şəkil',documents:'sənəd'},en:{messages:count===1?'message':'messages',voices:count===1?'audio transcript':'audio transcripts',images:count===1?'photo':'photos',documents:count===1?'document':'documents'}}[language]||{messages:'messages',voices:'audio transcripts',images:'photos',documents:'documents'};return `${count} ${forms[key]}`;}
  function showCopilotContext(usage){const box=$('operatorAIContextUsage');if(!box)return;box.replaceChildren();if(!usage||!Number.isFinite(Number(usage.messages))){box.hidden=true;return;}box.hidden=false;const language=i18n.language||'en';const number=(value)=>Math.max(0,Number(value)||0);const counts=[['messages',usage?.messages],['voices',usage?.voice_transcriptions],['images',usage?.images],['documents',usage?.documents]].filter(([,value])=>number(value)>0);const summary=document.createElement('span');summary.textContent=copilotText('context')+': '+(counts.length?counts.map(([key,value])=>contextCount(key,value,language)).join(' · '):contextCount('messages',0,language));box.append(summary);if(number(usage.saved_visual_observations)){const saved=document.createElement('span');saved.textContent=' · '+number(usage.saved_visual_observations)+' '+({ru:'сохранённых наблюдений фото',az:'saxlanmış şəkil müşahidəsi',en:'saved photo observations'}[language]||'saved photo observations');box.append(saved);}const skipped=Array.isArray(usage?.skipped)?usage.skipped:[];if(skipped.length){const intro=document.createElement('span');intro.textContent=` · ${copilotText('skipped')}: ${skipped.length}`;box.append(intro);const details=document.createElement('details');const summaryNode=document.createElement('summary');summaryNode.textContent=copilotText('contextDetails');const list=document.createElement('ul');for(const item of skipped){const li=document.createElement('li');const kind=({voice:{ru:'голосовое',az:'səs',en:'voice'},image:{ru:'фото',az:'şəkil',en:'photo'},document:{ru:'документ',az:'sənəd',en:'document'},video:{ru:'видео',az:'video',en:'video'}}[item?.kind]?.[language])||item?.kind||'attachment';li.textContent=kind+' — '+mediaSkipReason(item?.reason);list.append(li);}details.append(summaryNode,list);box.append(details);}}
  const replyText=key=>({action:{ru:'Ответить',az:'Cavabla',en:'Reply'},unavailable:{ru:'Ответ недоступен: идентификатор сообщения не сохранён.',az:'Cavab əlçatan deyil: mesajın identifikatoru saxlanılmayıb.',en:'Reply unavailable: the message identifier was not saved.'},copy:{ru:'Копировать текст',az:'Mətni kopyala',en:'Copy text'},copied:{ru:'Текст скопирован',az:'Mətn kopyalandı',en:'Text copied'},copyFailed:{ru:'Не удалось скопировать текст',az:'Mətni kopyalamaq olmadı',en:'Could not copy text'},closeMenu:{ru:'Закрыть меню',az:'Menyunu bağla',en:'Close menu'},preview:{ru:'Ответ на сообщение',az:'Mesaja cavab',en:'Reply to message'},pending:{ru:'Предыдущий ответ ожидает проверки отправки',az:'Əvvəlki cavabın göndərilməsi yoxlanmalıdır',en:'The previous reply needs a send status check'},remove:{ru:'Убрать цитату',az:'Sitatı sil',en:'Remove quote'}}[key]?.[i18n.language]||'Reply');
  const messageCopyText=message=>String(message?.has_transcription?(message.audio_transcript||message.body||''):(isAttachmentMessage(message)?attachmentCaption(message):message?.body||'')).replace(/\[Attachment pending_processing: contents not read\]/gi,'').replace(/\[CRM:[^\]]*\]/gi,'').trim();
  const copyControlMarkup=message=>messageCopyText(message)?`<span class="operator-copy-control"><span data-copy-status role="status" aria-live="polite"></span><button type="button" class="operator-copy-message" data-copy-message="${esc(message.id||message.external_id||'')}" aria-label="${esc(replyText('copy'))}" title="${esc(replyText('copy'))}"><svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/></svg></button></span>`:'';
  const deliveryCopy=state=>({ru:{sent:'Отправлено',delivered:'Доставлено',read:'Прочитано',pending:'Ожидает отправки',retrying:'Отправка готовится',failed:'Ошибка отправки',unknown:'Статус неизвестен'},az:{sent:'Göndərilib',delivered:'Çatdırılıb',read:'Oxunub',pending:'Göndərilmə gözlənilir',retrying:'Göndərmə hazırlanır',failed:'Göndərmə xətası',unknown:'Status məlum deyil'},en:{sent:'Sent',delivered:'Delivered',read:'Read',pending:'Waiting to send',retrying:'Preparing send',failed:'Send failed',unknown:'Status unknown'}}[i18n.language]||{})[state]||state;
  function retainDeliveryEvidence(previous,page){
    if(!previous||!sameDialog(previous.dialog,page.dialog))return;
    const key=m=>m.direction==='outgoing'&&m.source==='connect'&&m.external_id&&m.source_channel_id&&m.source_conversation_id?JSON.stringify([m.id,m.external_id,m.source_channel_id,m.source_conversation_id,m.source_lead_id]):null;
    const known=new Map((previous.messages||[]).map(m=>[key(m),m]));
    const rank={sent:1,delivered:2,read:3};
    for(const message of page.messages||[]){
      const id=key(message),old=id&&known.get(id);if(!old)continue;
      if(old.provider_message_id&&message.provider_message_id&&old.provider_message_id!==message.provider_message_id)continue;
      const before=old.delivery_state,after=message.delivery_state;
      if((['delivered','read'].includes(before)&&(rank[after]||0)<rank[before])||(before==='sent'&&['pending','retrying','unknown',undefined].includes(after))||(before==='failed'&&['pending','retrying','sent',undefined].includes(after)))message.delivery_state=before;
    }
  }
  function deliveryMarkup(message){
    if(message.direction!=='outgoing')return '';
    const state=message.delivery_state;if(!['sent','delivered','read','pending','retrying','failed','unknown'].includes(state))return '';
    const label=deliveryCopy(state);
    if(!['sent','delivered','read'].includes(state))return `<span class="operator-delivery-note" data-delivery-state="${esc(state)}" role="status" title="${esc(label)}">${esc(label)}</span>`;
    const count={sent:1,delivered:2,read:2}[state],width=14+5*(count-1);
    return `<span class="operator-delivery-ticks" data-delivery-state="${state}" role="img" aria-label="${esc(label)}" title="${esc(label)}"><svg aria-hidden="true" width="${width}" height="14" viewBox="0 0 ${20+7*(count-1)} 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${Array.from({length:count},(_,index)=>'<path d="m2 10 4 4 10-10" transform="translate('+(index*7)+' 0)"/>').join('')}</svg></span>`;
  }
  const completionText=key=>({complete:{ru:'Завершить обращение',az:'Müraciəti tamamla',en:'Complete request'},heading:{ru:'Результат обращения',az:'Müraciətin nəticəsi',en:'Request outcome'},resolved:{ru:'Решено и подтверждено',az:'Həll edildi və təsdiqləndi',en:'Resolved and confirmed'},unknown:{ru:'Рекомендации даны, результат неизвестен',az:'Tövsiyə verildi, nəticə məlum deyil',en:'Advice given, result unknown'},unresolved:{ru:'Не решено',az:'Həll edilməyib',en:'Unresolved'},info:{ru:'Информационная консультация',az:'Məlumat məsləhəti',en:'Informational'},note:{ru:'Комментарий сотрудника (необязательно)',az:'Əməkdaşın qeydi (məcburi deyil)',en:'Employee note (optional)'},propose:{ru:'Предложить добавить опыт в базу знаний',az:'Təcrübəni bilik bazasına əlavə etməyi təklif et',en:'Propose adding this experience to Knowledge'},save:{ru:'Завершить обращение',az:'Müraciəti tamamla',en:'Complete request'},case:{ru:'Создать кейс с AI',az:'AI ilə hal yarat',en:'Create case with AI'},done:{ru:'Обращение завершено. Публикация знания не выполнялась.',az:'Müraciət tamamlandı. Bilik dərc edilmədi.',en:'Request completed. No knowledge was published.'},creating:{ru:'Подготовка кандидата кейса…',az:'Hal namizədi hazırlanır…',en:'Preparing a case candidate…'},caseReady:{ru:'Кандидат открыт в редакторе кейсов для проверки.',az:'Namizəd yoxlama üçün hallar redaktorunda açıldı.',en:'Candidate opened in the case editor for review.'},close:{ru:'Закрыть',az:'Bağla',en:'Close'}}[key]?.[i18n.language]||'');
  function notice(text){if($('operatorNotice'))$('operatorNotice').textContent=text||'';}
  const manualCaseCopy=key=>({ru:{intro:'Создать обращение по этому диалогу? Разработчика можно спросить отдельно.',pending:'Предыдущий запрос ещё не подтверждён. Можно проверить тот же запрос.',check:'Проверить сохранённый запрос',mismatch:'Сервер не подтвердил точное обращение. Создание приостановлено; проверьте обращение в CRM.',storage:'Не удалось сохранить запрос в этом браузере. Обращение не создавалось.',cancel:'Закрыть'},az:{intro:'Bu dialoq üzrə müraciət yaradılsın? Tərtibatçı ilə ayrıca məsləhətləşmək olar.',pending:'Əvvəlki sorğu hələ təsdiqlənməyib. Eyni sorğunu yoxlamaq olar.',check:'Saxlanmış sorğunu yoxla',mismatch:'Server dəqiq müraciəti təsdiqləmədi. Yaratma dayandırılıb; CRM-də müraciəti yoxlayın.',storage:'Sorğunu bu brauzerdə saxlamaq olmadı. Müraciət yaradılmadı.',cancel:'Bağla'},en:{intro:'Create a case for this conversation? You can consult a developer separately.',pending:'The previous request is unconfirmed. Check the same request.',check:'Check saved request',mismatch:'The server did not confirm the exact case. Creation is on hold; check the case in CRM.',storage:'Could not save this request in the browser. No case was created.',cancel:'Close'}}[i18n.language]||{})[key]||key;
  const manualCaseFlights=new Set();
  const canCreateManualCase=route=>!route?.lifecycle_enabled||(route.can_send===true&&route.lifecycle?.state==='accepted'&&String(route.lifecycle.operator_id)===String(member?.telegram_id)&&typeof route.lifecycle.id==='string'&&!!route.lifecycle.id&&Number.isSafeInteger(route.lifecycle.revision)&&route.lifecycle.revision>=0);
  async function openManualCaseCreate(source,button,isCurrent){
    if(button.disabled||$('operatorCreateCaseModal')||!canCreateSupport()||!hasRight('dialogs')||!canCreateManualCase(source))return;
    button.disabled=true;
    const owner=identity(member),key='tenant.operator.manual-case.'+owner+'.'+String(source.lead_id)+(source.lifecycle_enabled?'.'+JSON.stringify([source.channel_id,source.conversation_id,source.lifecycle.id]):'');
    const same=()=>owner===identity(member)&&canCreateSupport()&&hasRight('dialogs')&&canCreateManualCase(source)&&isCurrent();
    try{
      let saved;try{saved=JSON.parse(sessionStorage.getItem(key)||'null');}catch{throw Error(manualCaseCopy('mismatch'));}
      let route={...source};
      if(!route.channel_id||!route.conversation_id){const history=await api('dialogs/history?'+new URLSearchParams({provider:'connect',lead_id:source.lead_id}));route=history.dialog||{};}
      if(!same())return;
      if(route.provider&&route.provider!=='connect'||!route.channel_id||!route.conversation_id||String(route.lead_id)!==String(source.lead_id))throw Error(manualCaseCopy('mismatch'));
      if(saved&&(!saved.payload||String(saved.payload.expected_tenant_id)!==String(member.tenant_id)||String(saved.payload.expected_user_id)!==String(member.telegram_id)||String(saved.payload.lead_id)!==String(route.lead_id)||saved.payload.channel_id!==route.channel_id||saved.payload.conversation_id!==route.conversation_id||(route.lifecycle_enabled&&saved.payload.episode_id!==route.lifecycle.id)))throw Error(manualCaseCopy('mismatch'));
      const modal=document.createElement('div');modal.id='operatorCreateCaseModal';modal.className='operator-complete-modal';
      const card=document.createElement('section');card.className='operator-complete-card';card.setAttribute('role','dialog');card.setAttribute('aria-modal','true');card.setAttribute('aria-labelledby','operatorCreateCaseTitle');
      const title=document.createElement('h2');title.id='operatorCreateCaseTitle';title.textContent=t('createTicket');
      const form=document.createElement('form'),intro=document.createElement('p');intro.textContent=manualCaseCopy('intro');
      const status=document.createElement('p');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
      const actions=document.createElement('div'),submit=document.createElement('button'),cancel=document.createElement('button');actions.className='operator-complete-actions';submit.type='submit';submit.className='primary';submit.textContent=t('createTicket');cancel.type='button';cancel.className='outline';cancel.textContent=manualCaseCopy('cancel');actions.append(submit,cancel);form.append(intro,status,actions);card.append(title,form);modal.append(card);document.body.append(modal);
      const paintSaved=()=>{if(!saved)return;submit.textContent=manualCaseCopy('check');submit.disabled=!!saved.hold||manualCaseFlights.has(key);status.textContent=manualCaseCopy(saved.hold?'mismatch':'pending');};paintSaved();
      let posting=false;
      const dispose=()=>{modal.remove();document.removeEventListener('keydown',onKey,true);for(const type of ['tenant-profile','tenant-auth-invalid','tenant-chat-close','tenant-view'])document.removeEventListener(type,onScope);};const onScope=()=>{if(!same())dispose();};
      const close=()=>{if(posting)return;dispose();if(same()){button.disabled=false;button.focus();}};cancel.onclick=close;modal.onclick=event=>{if(event.target===modal)close();};
      const onKey=event=>{if(!modal.isConnected)return;if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close();}else if(event.key==='Tab'){const controls=[...card.querySelectorAll('button,input,select')].filter(node=>!node.disabled&&!node.hidden);if(!controls.length)return;const first=controls[0],last=controls.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}else if(!card.contains(document.activeElement)){event.preventDefault();first.focus();}}};document.addEventListener('keydown',onKey,true);for(const type of ['tenant-profile','tenant-auth-invalid','tenant-chat-close','tenant-view'])document.addEventListener(type,onScope);
      form.onsubmit=async event=>{
        event.preventDefault();if(posting||manualCaseFlights.has(key)||saved?.hold||!same())return;
        if(!saved){
          saved={payload:{request_id:crypto.randomUUID(),lead_id:route.lead_id,channel_id:route.channel_id,conversation_id:route.conversation_id,expected_tenant_id:member.tenant_id,expected_user_id:member.telegram_id,...(route.lifecycle_enabled?{episode_id:route.lifecycle.id,expected_revision:route.lifecycle.revision}:{})}};
          try{sessionStorage.setItem(key,JSON.stringify(saved));}catch{saved=null;status.textContent=manualCaseCopy('storage');return;}
        }
        paintSaved();posting=true;manualCaseFlights.add(key);submit.disabled=true;cancel.disabled=true;
        try{
          const created=await api('support-tickets','POST',saved.payload),ticket=created.ticket,payload=saved.payload;
          const writeOnly=created.readable===false&&ticket?.id&&ticket.create_request_id===payload.request_id&&created.create_receipt?.request_id===payload.request_id&&String(created.create_receipt.tenant_id)===String(payload.expected_tenant_id)&&String(created.create_receipt.actor_id)===String(payload.expected_user_id);
          if(!writeOnly&&(!ticket?.id||String(ticket.tenant_id)!==String(payload.expected_tenant_id)||ticket.create_request_id!==payload.request_id||String(ticket.lead_id)!==String(payload.lead_id)||ticket.channel_id!==payload.channel_id||ticket.conversation_id!==payload.conversation_id||(payload.developer_confirmed===true&&(ticket.assigned_developer_channel_id!==payload.assigned_developer_channel_id||ticket.assigned_developer_conversation_id!==payload.assigned_developer_conversation_id)))){saved.hold=true;sessionStorage.setItem(key,JSON.stringify(saved));throw Error(manualCaseCopy('mismatch'));}
          sessionStorage.removeItem(key);dispose();if(same()){button.textContent=t('ticketSaved');button.setAttribute('aria-label',t('ticketSaved'));button.title=t('ticketSaved');document.dispatchEvent(new CustomEvent('tenant-support-changed'));}
        }catch(error){if(route.lifecycle_enabled&&error.status===409&&same()){dispose();await openDialog(route,true);if(owner===identity(member)&&currentDialog?.lead_id===route.lead_id&&currentDialog?.channel_id===route.channel_id&&currentDialog?.conversation_id===route.conversation_id){const errorBox=$('operatorChatError');if(errorBox)errorBox.textContent=error.message;}return;}if(modal.isConnected){status.textContent=error.message+' '+manualCaseCopy(saved.hold?'mismatch':'pending');}}
        finally{posting=false;manualCaseFlights.delete(key);if(modal.isConnected){submit.disabled=!!saved?.hold;cancel.disabled=false;if(!card.contains(document.activeElement))cancel.focus();}}
      };
      (saved?cancel:submit).focus();
    }catch(error){if(same()){const status=$('operatorChatError')||$('supportNotice');if(status)status.textContent=error.message;button.disabled=false;}}
    finally{if(!same()){$('operatorCreateCaseModal')?.remove();button.disabled=false;}}
  }

  const lifecycleText=key=>({dismiss:{ru:'Риторический вопрос',az:'Ritorik sual',en:'Rhetorical question'},accept:{ru:'Принять диалог',az:'Dialoqu qəbul et',en:'Accept dialogue'},start:{ru:'Начать диалог',az:'Dialoqa başla',en:'Start dialogue'},close:{ru:'Завершить',az:'Tamamla',en:'Complete'},transfer:{ru:'Передать',az:'Ötür',en:'Transfer'},recipient:{ru:'Сотрудник',az:'Əməkdaş',en:'Employee'},episode:{ru:'Завершённое обращение',az:'Tamamlanmış müraciət',en:'Completed request'},cancel:{ru:'Отмена',az:'Ləğv et',en:'Cancel'},case:{ru:'Отправить в кейсы',az:'Hallara göndər',en:'Send to cases'},casePublished:{ru:'Кейс опубликован для назначенного агента.',az:'Hal təyin edilmiş agent üçün dərc edildi.',en:'Case published for the assigned agent.'},caseDone:{ru:'Черновик кейса создан. Публикации не было.',az:'Halın qaralaması yaradıldı. Dərc edilmədi.',en:'Case draft created. Nothing was published.'},caseBudget:{ru:'AI проверит выбранное обращение. Подтверждённое решение публикуется только для назначенного агента при наличии ваших прав; иначе сохраняется черновик с причиной. Используется действующий AI-бюджет, без дополнительного платного анализа вложений.',az:'AI seçilmiş müraciəti yoxlayacaq. Təsdiqlənmiş həll yalnız hüquqlarınız olduqda təyin edilmiş agent üçün dərc edilir, əks halda səbəbi ilə qaralama saxlanılır. Mövcud AI büdcəsi istifadə olunur; əlavələrə əlavə ödəniş yoxdur.',en:'AI checks only the selected completed request. Proven solutions publish to the assigned agent only with your existing permissions; otherwise a draft explains why. Uses the existing AI budget without additional paid attachment analysis.'},casePending:{ru:'Анализ ещё не завершён. Повторное нажатие проверит тот же запрос, без нового платного запуска.',az:'Təhlil hələ tamamlanmayıb. Təkrar klik eyni sorğunu yoxlayır, yeni ödənişli iş başlatmır.',en:'Analysis is not complete. Clicking again checks the same request without a new paid run.'},caseUnsuitable:{ru:'AI не нашёл материала, пригодного для обучения. Кейс не создан.',az:'AI təlim üçün uyğun material tapmadı. Hal yaradılmadı.',en:'AI found no suitable learning material. No case was created.'},caseFailed:{ru:'Черновик не создан. Новый платный запуск не выполнялся. Проверьте результат анализа в диагностике.',az:'Qaralama yaradılmadı. Yeni ödənişli iş başladılmadı. Təhlilin nəticəsini diaqnostikada yoxlayın.',en:'No draft was created. No new paid run was started. Review the analysis diagnostic.'},unavailable:{ru:'Диалог больше недоступен в вашей очереди.',az:'Dialoq artıq növbənizdə əlçatan deyil.',en:'Dialogue is no longer available in your queue.'},retry:{ru:'Повторить проверку',az:'Yenidən yoxla',en:'Check again'}}[key]?.[i18n.language]||key);
  const lifecycleRequests=new Map(),lifecycleListStates=new Map(),lifecycleInFlight=new Set();
  function rememberLifecycleListState(route,state){
    if(!route?.lifecycle_enabled||!state||!Number.isInteger(state.revision))return;
    const key=contextKey('lifecycle-list',route),previous=lifecycleListStates.get(key);
    if(!previous||state.revision>=previous.revision)lifecycleListStates.set(key,{state:state.state,revision:state.revision,id:state.id,operator_id:state.operator_id,no_reply_needed:state.no_reply_needed===true});
  }
  const queueMode=()=>$('operatorLifecycle')?.value||'all';
  const ownerOverviewMode=()=>member?.role==='owner'&&queueMode()==='all';
  let ownerOverviewWasActive=false;
  const rememberOwnerOverviewMode=()=>{ownerOverviewWasActive=ownerOverviewMode();};
  function setQueueModes(){const select=$('operatorLifecycle');if(!select)return;const value=select.value||'all';select.innerHTML=['all','active','completed'].map(key=>`<option value="${key}">${({all:{ru:'Все диалоги',az:'Bütün dialoqlar',en:'All dialogues'},active:{ru:'В работе',az:'İşdə',en:'Active'},completed:{ru:'Завершённые',az:'Tamamlanmış',en:'Completed'}})[key][i18n.language]||key}</option>`).join('');select.value=value;select.setAttribute('aria-label',({ru:'Состояние очереди',az:'Növbənin vəziyyəti',en:'Queue state'})[i18n.language]||'Queue state');rememberOwnerOverviewMode();}
  function reconcileLifecycleRows(){
    const reconcile=route=>{
      rememberLifecycleListState(route,route.lifecycle);
      const state=lifecycleListStates.get(contextKey('lifecycle-list',route));
      if(!route.lifecycle_enabled||!state||state.revision<=Number(route.lifecycle?.revision??-1))return route;
      // A successful acceptance/closure receipt can outrun an older list response.
      return {...route,lifecycle:{...route.lifecycle,...state},
        ...(['accepted','closed'].includes(state.state)?{missed_call_pending:false}:{})};
    };
    rows=rows.map(row=>row.group_sources?.length?{...reconcile(row),group_sources:row.group_sources.map(reconcile)}:reconcile(row));
    const mode=queueMode(),matches=route=>mode==='all'||(mode==='completed'?['closed','dismissed'].includes(route.lifecycle?.state):!['closed','dismissed'].includes(route.lifecycle?.state)&&!(route.lifecycle_enabled&&route.lifecycle?.state==='waiting'&&route.lifecycle.has_inbound===false));
    rows=rows.flatMap(row=>{
      if(!row.group_sources?.length)return matches(row)?[row]:[];
      const sources=row.group_sources.filter(matches);if(!sources.length)return [];if(sources.length===row.group_sources.length)return [row];
      // A matching sibling must not keep a completed/outgoing-only route as the card representative.
      const representative=sources.find(source=>sameDialog(source,row))||sources.reduce((latest,source)=>(Date.parse(source.last_message_at)||0)>(Date.parse(latest.last_message_at)||0)?source:latest);
      return [{...row,...representative,group_sources:sources,unread_count:Math.max(...sources.map(source=>Number(source.unread_count)||0))}];
    });
  }
  function updateLifecycleList(route,state){
    rememberLifecycleListState(route,state);reconcileLifecycleRows();renderRows();
  }
  const completeAllCopy=key=>({
    confirmCompany:{ru:'Попытаться завершить до {count} принятых диалогов с указанной компанией? Перед завершением сервер проверит привязку и адрес каждого диалога. Не прошедшие проверку и новые диалоги останутся в очереди.',az:'Şirkət əlaqəsi olan {count} qəbul edilmiş dialoqu tamamlamağa çalışaq? Server hər dialoqun şirkətini və ünvanını yoxlayacaq. Yoxlamadan keçməyən və yeni dialoqlar növbədə qalacaq.',en:'Try to complete up to {count} accepted dialogues with a company link? The server will verify each link and dialogue address before completing it. New dialogues and any that fail verification stay open.'},
    title:{ru:'Отметить все диалоги завершёнными',az:'Bütün dialoqları tamamlanmış işarələ',en:'Mark all dialogues completed'},
    loading:{ru:'Проверяю доступные диалоги…',az:'Əlçatan dialoqlar yoxlanılır…',en:'Checking accessible dialogues…'},
    checking:{ru:'Проверено источников: {count}. Пока ничего не изменено.',az:'Yoxlanılmış mənbələr: {count}. Hələ heç nə dəyişməyib.',en:'Sources checked: {count}. Nothing changed yet.'},
    applying:{ru:'Завершено: {done}. Осталось проверить: {remaining}.',az:'Tamamlandı: {done}. Qalan: {remaining}.',en:'Completed: {done}. Remaining: {remaining}.'},
    confirm:{ru:'Завершить {count} источников диалогов во всей доступной очереди (фильтры не учитываются)? Будут завершены только принятые вами диалоги. Новые диалоги не изменятся. Чужие принятые диалоги не изменятся. Непрочитанные сообщения останутся непрочитанными.',az:'Bütün əlçatan növbədə {count} dialoq mənbəyi tamamlansın (filtrlər nəzərə alınmır)? Yalnız qəbul etdiyiniz dialoqlar tamamlanacaq. Yeni dialoqlar dəyişməyəcək. Başqalarının qəbul etdiyi dialoqlar və oxunmamış sayğaclar dəyişməyəcək.',en:'Complete {count} dialogue sources across your accessible queue (filters ignored)? Only dialogue sources accepted by you will be completed. New items will not change. Items accepted by others and unread counters will not change.'},
    done:{ru:'Завершено: {done}. Пропущено из-за изменений или доступа: {skipped}. Не подтверждено: {unknown}. Не обработано: {remaining}.',az:'Tamamlandı: {done}. Dəyişiklik və ya giriş səbəbindən buraxıldı: {skipped}. Təsdiqlənmədi: {unknown}. İşlənmədi: {remaining}.',en:'Completed: {done}. Skipped after changes or access restrictions: {skipped}. Unconfirmed: {unknown}. Not processed: {remaining}.'},
    interrupted:{ru:'Операция прервана. Сохранено: {done}. Пропущено: {skipped}. Результат не подтверждён: {unknown}. Не обработано: {remaining}. Обновите список перед новой попыткой.',az:'Əməliyyat dayandırıldı. Saxlanıldı: {done}. Buraxıldı: {skipped}. Təsdiqlənmədi: {unknown}. İşlənmədi: {remaining}. Yenidən cəhd etməzdən əvvəl siyahını yeniləyin.',en:'Operation interrupted. Saved: {done}. Skipped: {skipped}. Unconfirmed: {unknown}. Not processed: {remaining}. Refresh the list before trying again.'},
    excluded:{ru:'Не включено в операцию: {excluded} (нет входящего сообщения: {noInbound}; принято другим сотрудником: {otherOwner}; действие недоступно: {unavailable}). Эти диалоги не изменены.',az:'Əməliyyata daxil edilmədi: {excluded} (gələn mesaj yoxdur: {noInbound}; başqa əməkdaş qəbul edib: {otherOwner}; əməliyyat əlçatan deyil: {unavailable}). Bu dialoqlar dəyişdirilmədi.',en:'Not included in this operation: {excluded} (no incoming message: {noInbound}; accepted by another employee: {otherOwner}; action unavailable: {unavailable}). These dialogues were not changed.'},
    empty:{ru:'Нет доступных диалогов для завершения.',az:'Tamamlamaq üçün əlçatan dialoq yoxdur.',en:'No eligible dialogues to complete.'},
    limit:{ru:'Не удалось получить полную очередь. Ничего не изменено.',az:'Tam növbəni əldə etmək mümkün olmadı. Heç nə dəyişmədi.',en:'Could not obtain the complete queue. Nothing changed.'}
  }[key]?.[i18n.language]||key);
  function completeAllNotice(text){const node=$('operatorBulkCompletionStatus');if(node){node.textContent=text||'';node.hidden=!text;}}
  let completeAllBusy=false;
  async function completeAllDialogs(){
    const button=$('operatorCompleteAll');
    if(completeAllBusy||!hasRight('dialogs'))return;
    const owner=identity(member),version=epoch,current=()=>owner===identity(member)&&version===epoch&&hasRight('dialogs');
    completeAllBusy=true;button.disabled=true;$('operatorToolsMenu').open=false;
    let done=0,skipped=0,unknown=0,remaining=0,applying=false,companyGate=false;
    const excluded={noInbound:0,otherOwner:0,unavailable:0,company:0,noReply:0};
    let completedView=null;
    const exclusionSummary=()=>completeAllCopy('excluded').replace('{excluded}',String(Object.values(excluded).reduce((a,b)=>a+b,0))).replace('{noInbound}',String(excluded.noInbound)).replace('{otherOwner}',String(excluded.otherOwner)).replace('{unavailable}',String(excluded.unavailable));
    const withExclusions=text=>text+' '+exclusionSummary()+
      (excluded.company?' '+companyCloseText('bulkChoice').replace('{count}',String(excluded.company)):'')+
      (excluded.noReply?' '+companyCloseText('bulkWaiting').replace('{count}',String(excluded.noReply)):'');
    try{
      completeAllNotice(completeAllCopy('loading'));
      // Snapshot every page before asking for confirmation. Never fetch more
      // candidates after approval: new arrivals must not join this operation.
      const routes=new Map(),offsets=new Set();let offset=0;
      while(offset!==null){
        if(!current())return;
        if(offsets.has(offset)||offsets.size>=1000)throw Error(completeAllCopy('limit'));
        offsets.add(offset);
        const page=await api('dialogs?'+new URLSearchParams({offset,completion_preview:1,lifecycle:'active'}));
        if(!current())return;
        if(page.completion_preview!==true)throw Error(completeAllCopy('limit'));
        for(const row of page.dialogs||[])for(const route of row.group_sources?.length?row.group_sources:[row]){
          if(route.provider==='connect'&&route.lifecycle_enabled&&['waiting','accepted'].includes(route.lifecycle?.state))routes.set(dialogKey(route),route);
        }
        completeAllNotice(completeAllCopy('checking').replace('{count}',String(routes.size)));
        offset=page.next_offset??null;
      }
      const candidates=[];
      for(const route of routes.values()){
        if(!current())return;
        // Preparation uses the server's read-only, authorized batch snapshot.
        // Existing mutations still recheck ownership, source grants and tokens.
        const fresh=route,state=fresh?.lifecycle;
        if(!fresh.lifecycle_enabled||!Number.isInteger(state?.revision)){excluded.unavailable++;continue;}
        if(state.bulk_company_gate)companyGate=true;
        if(state.bulk_company_gate==='choose'){excluded.company++;continue;}
        if(state.bulk_company_gate==='no_reply_separate'){excluded.noReply++;continue;}
        if(state.state==='waiting'&&(state.bulk_waiting===true||state.no_reply_needed===true||(!('bulk_waiting' in state)&&state.can_dismiss===true))){excluded.noReply++;continue;}
        const action=state.state==='accepted'&&state.can_close&&String(state.operator_id)===String(member.telegram_id)?'close':null;
        if(!action){
          if(state.state==='waiting'&&state.has_inbound===false)excluded.noInbound++;
          else if(state.state==='accepted'&&String(state.operator_id)!==String(member.telegram_id))excluded.otherOwner++;
          else excluded.unavailable++;
          continue;
        }
        candidates.push({route:fresh,payload:{provider:fresh.provider,channel_id:fresh.channel_id,conversation_id:fresh.conversation_id,lead_id:fresh.lead_id,action,expected_revision:state.revision,request_id:crypto.randomUUID()}});
      }
      if(!current())return;
      if(!candidates.length){completeAllNotice(withExclusions(completeAllCopy('empty')));return;}
      if(!window.confirm(withExclusions(completeAllCopy(companyGate?'confirmCompany':'confirm').replace('{count}',String(candidates.length))))){completeAllNotice('');return;}
      applying=true;remaining=candidates.length;
      for(const {route,payload} of candidates){
        if(!current())return;
        remaining--;
        completeAllNotice(completeAllCopy('applying').replace('{done}',String(done)).replace('{remaining}',String(remaining+1)));
        try{
          // The existing mutation locks and rechecks permissions, revision and
          // inbound ID and writes its own idempotent receipt and audit record.
          const result=await api('dialogs_lifecycle','POST',payload);
          if(!current())return;
          if(!result.lifecycle||result.lifecycle.state!=='closed'||!Number.isInteger(result.lifecycle.revision)||result.lifecycle.revision<=payload.expected_revision){unknown++;break;}
          done++;updateLifecycleList(route,result.lifecycle);
          if(sameDialog(route,currentDialog)&&Number(currentDialog.lifecycle?.revision??-1)<=result.lifecycle.revision){
            if(currentPage){
              currentDialog={...currentDialog,can_send:false,lifecycle:{...currentDialog.lifecycle,...result.lifecycle,can_close:false,can_transfer:false}};
              currentPage.dialog=currentDialog;renderDialog(currentPage,{suppressRead:true});
              completedView={route:currentDialog,revision:openRevision,serial:dialogReadSerial};
            }
          }
        }catch(error){
          if(!current())return;
          if(error.status===422&&route.lifecycle?.bulk_company_gate==='prelinked_unverified')excluded.company++;
          else if([403,404,409].includes(error.status))skipped++;
          else {unknown++;break;} // Network ambiguity: do not blindly retry.
        }
      }
      if(current())completeAllNotice(withExclusions(completeAllCopy(unknown?'interrupted':'done').replace('{done}',String(done)).replace('{skipped}',String(skipped)).replace('{unknown}',String(unknown)).replace('{remaining}',String(remaining))));
    }catch(error){if(current())completeAllNotice(applying?withExclusions(completeAllCopy('interrupted').replace('{done}',String(done)).replace('{skipped}',String(skipped)).replace('{unknown}',String(unknown+1)).replace('{remaining}',String(remaining))):error.message);}
    finally{
      completeAllBusy=false;if(button.isConnected)button.disabled=false;
      if(completedView)void refreshCompletedViewActions(completedView,current);
    }
  }
  async function refreshCompletedViewActions(view,current){
    // The receipt proves the transition, not fresh permissions. Retrieve only
    // the selected route's capability and leave history/scroll/reminders alone.
    const revision=view.route.lifecycle.revision;
    const same=()=>current()&&$('view-dialogs')?.classList.contains('active')&&
      sameDialog(currentDialog,view.route)&&currentPage&&
      view.revision===openRevision&&view.serial===dialogReadSerial&&
      currentDialog.lifecycle?.revision===revision;
    if(!same())return;
    try{
      const fresh=(await api('dialogs/history?'+dialogueHistoryQuery(view.route)))?.dialog;
      if(!same()||!sameDialog(fresh,view.route)||fresh.lifecycle?.revision!==revision||
        fresh.lifecycle?.state!==view.route.lifecycle.state)return;
      const startHost=$('operatorAcceptAction'),primaryHost=$('operatorPrimaryAction');
      if(!startHost||!primaryHost)return;
      const missing=new Set();
      if(fresh.lifecycle.can_start===true&&!startHost.querySelector('[data-operator-lifecycle="start"]'))missing.add('start');
      const transferNew=canSidebarTransfer(fresh)&&!$('operatorSidebarTransfer');
      if(!missing.size&&!transferNew)return;
      Object.assign(currentDialog.lifecycle,{can_start:fresh.lifecycle.can_start,can_transfer:fresh.lifecycle.can_transfer});
      currentPage.dialog=currentDialog;
      if(transferNew)renderDialog(currentPage,{suppressRead:true});
      else renderLifecycleActions(currentDialog,primaryHost,missing);
    }catch{} // Keep actions disabled until the server confirms them.
  }
  let closeLifecycleModal=()=>{};
  function removeLifecycleDialog(route){
    dialogPageCache.delete(pageCacheKey(route));
    rows=rows.filter(row=>!sameDialog(row,route));
    if(sameDialog(selectedDialog,route)){selectedDialog=null;selectedDialogOwner='';}
    if(sameDialog(viewReturnDialog,route)){viewReturnDialog=null;viewReturnDialogOwner='';}
    if(sameDialog(currentDialog,route)){
      closeDialog(true,true);
      const state={...history.state};for(const key of ['operatorDialog','operatorDialogOwner','operatorDialogHistoryEntry','operatorHelper'])delete state[key];history.replaceState(state,'');
    }
    helperDrafts.delete(contextKey('helper',route));
    for(const kind of ['draft','incoming-case-draft'])try{sessionStorage.removeItem(contextKey(kind,route));}catch{}
    closeLifecycleModal();
    if($('operatorRows'))renderRows();
  }
  function lifecycleUnavailable(error,route){
    if(route?.lifecycle_enabled&&[403,404,409].includes(error.status)){removeLifecycleDialog(route);notice(lifecycleText('unavailable'));return true;}return false;
  }
  function showLifecycleTimelineEvent(event,kind){
    if(!currentPage||!sameDialog(currentPage.dialog,currentDialog)||event?.kind!==kind||!validOperatorEvent(event))return;
    retainOperatorEvents(currentPage,{operator_events:[event]});
    const chat=$('chatRows');
    if(!chat)return;
    const follow=atBottom(chat),top=chat.scrollTop,anchor=follow?null:timelineViewportAnchor(chat);
    placeOperatorEvents(chat,currentPage);
    setTimelineScroll(chat,follow?chat.scrollHeight:timelineScrollTop(top,anchor),true);
  }
  function applyLifecycleReceipt(route,action,result){
    const state=result.lifecycle,controls=result.controls;
    if(!currentPage||!sameDialog(currentPage.dialog,route)||!state||
       state.revision!==Number(route.lifecycle?.revision)+1||
       String(state.operator_id)!==String(member.telegram_id)||
       state.state!==(action==='accept'?'accepted':'closed'))return;
    showLifecycleTimelineEvent(result.operator_event,action);
    const previous=currentDialog.lifecycle;
    if(Number(previous?.revision)>=state.revision)return;
    const {company_completion,...prior}=previous||{};
    const lifecycle={...prior,can_accept:false,can_dismiss:false,can_takeover:false,
      can_close:action==='accept',can_transfer:false,can_start:false,can_submit_case:false,
      no_reply_needed:false,...controls?.lifecycle,...state};
    if(controls?.closed_episode){
      const episode=controls.closed_episode;
      lifecycle.completed_episodes=[episode,...(prior.completed_episodes||[]).filter(old=>old.id!==episode.id)].slice(0,20);
    }
    const canSend=action==='accept'&&controls?.can_send===true&&
      !currentDialog.archive_read_only&&!currentDialog.owner_overview_read_only&&!currentDialog.search_overview_read_only;
    // A pre-transition read of this view must not replace the receipt or
    // remove a dialogue the server has just confirmed is ours.
    dialogReadSerial++;
    pendingDialogRoute=null;
    $('operatorConversationContent')?.setAttribute('aria-busy','false');
    currentDialog={...currentDialog,lifecycle,can_send:canSend,send_block_reason:canSend?'':'dialog_accept_required',missed_call_pending:false};
    selectedDialog=currentDialog;
    currentPage={...currentPage,dialog:currentDialog,scroll_to_latest:false};
    renderDialog(currentPage,{suppressRead:true});
    if(action==='accept')window.tenantDialogueCompany?.refresh?.();
  }
  async function lifecycleAction(route,action,button,target,episodeId){
    if(button.disabled||!sameDialog(route,currentDialog))return;
    const version=epoch,view=openRevision,key=contextKey('lifecycle',route)+'.'+version+'.'+route.lifecycle?.revision+'.'+action+'.'+(target||'')+'.'+(episodeId||'');
    if(lifecycleInFlight.has(key))return;
    let payload=lifecycleRequests.get(key);
    if(!payload){payload={provider:route.provider,channel_id:route.channel_id,conversation_id:route.conversation_id,lead_id:route.lead_id,action,expected_revision:route.lifecycle?.revision,request_id:crypto.randomUUID(),...(target?{target_employee_id:Number(target)}:{}),...(action==='dismiss'?{message_id:route.lifecycle?.last_inbound_id,individual_no_reply:true}:{}),...(episodeId?{episode_id:episodeId}:{}),...(action==='close'?{company_choice:'unchanged'}:{})};lifecycleRequests.set(key,payload);}
    lifecycleInFlight.add(key);button.disabled=true;
    try{
      const result=await api('dialogs_lifecycle','POST',payload);
      lifecycleRequests.delete(key);
      if(version!==epoch)return;
      if(['accept','takeover','start','close','dismiss'].includes(action))updateLifecycleList(route,result.lifecycle||result.dialog?.lifecycle);
      if(view!==openRevision||!sameDialog(route,currentDialog))return;
      closeLifecycleModal();dialogPageCache.delete(pageCacheKey(route));
      // A successful receipt is authoritative even while history is refreshing.
      if(['accept','takeover','start','close'].includes(action))button.remove();
      if(action==='accept'||action==='close'){
        applyLifecycleReceipt(route,action,result);
        return;
      }
      if(action==='start')window.tenantDialogueCompany?.refresh?.();
      if(action==='transfer'){removeLifecycleDialog(route);void refresh();return;}
      if(action==='dismiss'){
        if(currentPage&&result.lifecycle?.state==='accepted'&&result.lifecycle?.no_reply_needed===true){
          showLifecycleTimelineEvent(result.operator_event,'no_reply');
          if(Number(currentDialog.lifecycle?.revision??-1)<=result.lifecycle.revision){
            currentDialog={...currentDialog,lifecycle:{...currentDialog.lifecycle,...result.lifecycle,can_dismiss:false}};
            currentPage.dialog=currentDialog;renderDialog(currentPage,{suppressRead:true});
          }
        }
        void refresh(false,false,{revalidateSelected:true,suppressRead:true});return;
      }
      if(action==='case'){await openDialog(route,true);if(version===epoch&&sameDialog(route,currentDialog)&&$('operatorChatError')){const key=result.publication?.state==='published'?'casePublished':result.case_state==='completed'&&result.case_id?'caseDone':result.case_state==='not_suitable'?'caseUnsuitable':['running','unknown'].includes(result.case_state)?'casePending':'caseFailed';$('operatorChatError').textContent=lifecycleText(key)+(result.suitability?.reason?' '+result.suitability.reason:'')+(result.publication?.state==='draft'&&result.publication.reason?' '+result.publication.reason:'');}return;}
      await openDialog({...route,...(result.dialog||{}),lifecycle:result.lifecycle||result.dialog?.lifecycle||route.lifecycle},true);
    }catch(error){
      if(version===epoch&&view===openRevision&&sameDialog(route,currentDialog)){
        if(action==='dismiss'&&error.status===409){
          await openDialog(route,true,'',{suppressRead:true});
          const status=$('operatorChatError');if(status&&sameDialog(route,currentDialog))status.textContent=error.message;
          return;
        }
        const unavailable=lifecycleUnavailable(error,route);
        if(unavailable){if(action==='start')void refresh();}
        else{const status=$('operatorLifecycleStatus')||$('operatorChatError');if(status)status.textContent=error.message;
          button.disabled=false;button.textContent=lifecycleText('retry');}
      }
    }finally{lifecycleInFlight.delete(key);}
  }
  const transferSidebarText=key=>({
    ru:{empty:'Нет доступных операторов в сети.',loading:'Проверяем, кто в сети…',failed:'Не удалось передать диалог. Проверьте список и попробуйте снова.',stale:'Диалог или статус оператора изменился. Обновите список.',sending:'Передаём диалог…',retry:'Обновить список'},
    az:{empty:'Əlçatan onlayn operator yoxdur.',loading:'Onlayn operatorlar yoxlanılır…',failed:'Dialoqu ötürmək olmadı. Siyahını yoxlayıb yenidən cəhd edin.',stale:'Dialoq və ya operatorun statusu dəyişib. Siyahını yeniləyin.',sending:'Dialoq ötürülür…',retry:'Siyahını yenilə'},
    en:{empty:'No eligible operators online.',loading:'Checking who is online…',failed:'Could not transfer the conversation. Check the list and try again.',stale:'The conversation or operator status changed. Refresh the list.',sending:'Transferring conversation…',retry:'Refresh list'}
  }[i18n.language]||{})[key];
  const canSidebarTransfer=route=>route?.provider==='connect'&&!route.archive_read_only&&!route.owner_overview_read_only&&route.lifecycle?.can_transfer===true;
  const sidebarTransferFlights=new Set();
  function mountSidebarTransfer(host,route,current){
    const menu=host?.querySelector('details');if(!menu||!canSidebarTransfer(route))return ()=>{};
    const results=menu.querySelector('[data-transfer-targets]'),status=menu.querySelector('[role=status]'),retry=menu.querySelector('[data-transfer-retry]');
    const profile=member, owner=identity(profile), revision=route.lifecycle.revision;
    const exact={provider:'connect',channel_id:route.channel_id,conversation_id:route.conversation_id,lead_id:route.lead_id,expected_revision:revision};
    const flightKey=contextKey('sidebar-transfer',route)+'.'+revision;
    const routeCurrent=()=>current()&&host.isConnected&&menu.closest('dialog')?.open&&
      canSidebarTransfer(currentDialog)&&currentDialog.lifecycle.revision===revision;
    let choiceRead=null;
    const cancel=()=>{choiceRead?.abort();choiceRead=null;results.replaceChildren();};
    const eligibleTarget=employee=>employee&&Number.isSafeInteger(employee.employee_id)&&
      employee.employee_id!==Number(profile.telegram_id)&&typeof employee.name==='string'&&employee.name.trim();
    async function load(){
      cancel();retry.hidden=true;
      if(!menu.open||!routeCurrent())return;
      if(sidebarTransferFlights.has(flightKey)){status.textContent=transferSidebarText('sending');return;}
      const read=new AbortController();choiceRead=read;
      const live=()=>choiceRead===read&&menu.open&&routeCurrent();
      status.textContent=transferSidebarText('loading');
      try{
        const query=new URLSearchParams({...exact,expected_tenant_id:String(profile.tenant_id),expected_user_id:String(profile.telegram_id)});
        const req=await fetch(endpoint+'dialogs/transfer-candidates?'+query,{cache:'no-store',credentials:'same-origin',signal:read.signal});
        const payload=await req.json();
        if(!live())return;
        if(!req.ok||payload?.success!==true||!Array.isArray(payload.result?.targets))throw Object.assign(Error(),{status:req.status});
        const seen=new Set();
        for(const employee of payload.result.targets){
          if(!eligibleTarget(employee)||seen.has(employee.employee_id))continue;
          seen.add(employee.employee_id);
          const b=document.createElement('button');b.type='button';b.textContent=employee.name;
          b.onclick=()=>void transfer(employee.employee_id);
          results.append(b);
        }
        status.textContent=seen.size?'':transferSidebarText('empty');
      }catch(error){if(live()){status.textContent=transferSidebarText([403,404,409].includes(error.status)?'stale':'failed');retry.hidden=false;}}
      finally{if(choiceRead===read)choiceRead=null;}
    }
    async function transfer(target){
      if(!menu.open||!routeCurrent()||sidebarTransferFlights.has(flightKey))return;
      const requestKey=flightKey+'.'+target;
      let request=lifecycleRequests.get(requestKey);
      if(!request){
        request={...exact,action:'transfer',target_employee_id:target,online_only:true,request_id:crypto.randomUUID()};
        lifecycleRequests.set(requestKey,request);
      }
      sidebarTransferFlights.add(flightKey);cancel();retry.hidden=true;status.textContent=transferSidebarText('sending');
      try{
        const res=await fetch(endpoint+'dialogs_lifecycle',{method:'POST',cache:'no-store',credentials:'same-origin',
          headers:{'Content-Type':'application/json'},body:JSON.stringify({...request,expected_tenant_id:profile.tenant_id,expected_user_id:profile.telegram_id})});
        const payload=await res.json();
        if(!res.ok||payload?.success!==true)throw Object.assign(Error(),{status:res.status});
        lifecycleRequests.delete(requestKey);
        if(owner!==identity(member)||!hasRight('dialogs'))return;
        if(sameDialog(route,currentDialog)&&currentDialog.lifecycle?.revision===revision)removeLifecycleDialog(route);
        void refresh();
      }catch(error){
        if([400,403,404,409,422].includes(error.status))lifecycleRequests.delete(requestKey);
        if(menu.open&&routeCurrent()){status.textContent=transferSidebarText([403,404,409].includes(error.status)?'stale':'failed');retry.hidden=false;}
      }finally{sidebarTransferFlights.delete(flightKey);}
    }
    retry.onclick=()=>void load();
    menu.ontoggle=()=>{if(menu.open)void load();else cancel();};
    return ()=>{menu.open=false;cancel();};
  }
  function openLifecycleEpisodeCase(route){
    closeLifecycleModal();
    const modal=document.createElement('div');modal.id='operatorLifecycleModal';modal.className='operator-complete-modal';
    modal.innerHTML=`<section class="operator-complete-card" role="dialog" aria-modal="true" aria-labelledby="operatorLifecycleTitle"><h2 id="operatorLifecycleTitle">${esc(lifecycleText('case'))}</h2><form><label>${esc(lifecycleText('episode'))}<select required data-episode-select><option value=""></option></select></label><p class="sub">${esc(lifecycleText('caseBudget'))}</p><p id="operatorLifecycleStatus" role="status"></p><div class="operator-complete-actions"><button type="submit" class="primary" disabled>${esc(lifecycleText('case'))}</button><button type="button" class="outline" data-cancel>${esc(lifecycleText('cancel'))}</button></div></form></section>`;
    const select=modal.querySelector('select'),submit=modal.querySelector('[type=submit]');
    for(const episode of route.lifecycle.completed_episodes||[]){
      const option=document.createElement('option');option.value=String(episode.id);option.disabled=!!episode.case_id;
      option.textContent=[episode.accepted_at,episode.closed_at].map(value=>i18n.date(value)).join(' → ')+(episode.case_id?' · '+lifecycleText('caseDone'):'');select.append(option);
    }
    const owner=identity(member),events=['tenant-chat-close','tenant-auth-invalid','tenant-profile','tenant-view'];
    const close=()=>{modal.remove();document.removeEventListener('keydown',key,true);for(const name of events)document.removeEventListener(name,close);closeLifecycleModal=()=>{};};
    const key=event=>{if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();close();}};
    document.addEventListener('keydown',key,true);for(const name of events)document.addEventListener(name,close);closeLifecycleModal=close;
    modal.querySelector('[data-cancel]').onclick=closeLifecycleModal;modal.onclick=event=>{if(event.target===modal)closeLifecycleModal();};
    select.onchange=()=>{submit.disabled=!select.value;};
    modal.querySelector('form').onsubmit=event=>{event.preventDefault();if(owner!==identity(member)||!sameDialog(route,currentDialog)){closeLifecycleModal();return;}const chosen=(route.lifecycle.completed_episodes||[]).find(episode=>String(episode.id)===select.value&&!episode.case_id);if(chosen)void lifecycleAction(route,'case',submit,null,chosen.id);};document.body.append(modal);
  }
  const companyCloseText=key=>({
    bulkChoice:{ru:'Оставлено в работе, нужна проверка компании: {count}.',az:'Şirkət yoxlanmalıdır, işdə qaldı: {count}.',en:'Left open for company verification: {count}.'},
    bulkWaiting:{ru:'Не приняты в работу: {count}.',az:'Hələ qəbul edilməyib: {count}.',en:'Not yet accepted: {count}.'}
  }[key]?.[i18n.language]||key);
  function renderLifecycleActions(route,host,only=null){
    const state=route.lifecycle||{};
    for(const [action,allowed] of [['takeover',state.can_takeover===true],['accept',state.state==='waiting'&&state.can_accept&&(state.last_inbound_id||member?.role==='owner'||(route.provider==='connect'&&route.last_message_type==='call_missed'&&route.last_message_direction==='system'))],['start',['closed','dismissed'].includes(state.state)&&state.can_start],['dismiss',state.state==='accepted'&&(state.can_dismiss||(state.can_close&&state.no_reply_needed))&&queueMode()!=='completed'],['close',state.can_close===true],['case',state.can_submit_case||(state.completed_episodes||[]).some(episode=>!episode.case_id)]]){
      if(!allowed||(only&&!only.has(action)))continue;const button=document.createElement('button');button.type='button';button.className=['accept','start','takeover'].includes(action)?'primary':'outline';button.textContent=action==='takeover'?({ru:'Забрать себе',az:'Üzərimə götür',en:'Take over'}[i18n.language]||'Take over'):lifecycleText(action);button.dataset.operatorLifecycle=action;
      if(action==='dismiss'){const marked=state.no_reply_needed===true;button.setAttribute('aria-pressed',String(marked));button.disabled=marked;}
      if(action==='case')button.title=lifecycleText('caseBudget');
      button.onclick=()=>action==='case'&&state.completed_episodes?.length?openLifecycleEpisodeCase(route):lifecycleAction(route,action,button);if(['accept','start','takeover'].includes(action))$('operatorAcceptAction').append(button);else if(action==='close'||action==='dismiss')$('operatorActiveLifecycleActions').append(button);else host.append(button);
    }
  }
  function openCompletion(route){
    const old=$('operatorCompleteModal');old?.remove();let requestId='',analysisId='';
    const modal=document.createElement('div');modal.className='operator-complete-modal';modal.id='operatorCompleteModal';modal.setAttribute('role','presentation');
    const outcomes=[['resolved_confirmed',completionText('resolved')],['recommendations_unknown',completionText('unknown')],['unresolved',completionText('unresolved')],['informational',completionText('info')]];
    modal.innerHTML=`<section class="operator-complete-card" role="dialog" aria-modal="true" aria-labelledby="operatorCompleteTitle"><h2 id="operatorCompleteTitle">${completionText('heading')}</h2><form><label>${completionText('heading')}<select required>${outcomes.map(([value,label])=>`<option value="${value}">${label}</option>`).join('')}</select></label><label>${completionText('note')}<textarea maxlength="1200"></textarea></label><label><input type="checkbox" data-propose> ${completionText('propose')}</label><p role="status"></p><div class="operator-complete-actions"><button class="primary" type="submit">${completionText('save')}</button><button class="outline" type="button" data-close>${completionText('close')}</button><button class="outline" type="button" data-case hidden>${completionText('case')}</button></div></form></section>`;
    document.body.append(modal);const form=modal.querySelector('form'),status=form.querySelector('[role=status]'),save=form.querySelector('[type=submit]'),caseButton=form.querySelector('[data-case]');
    form.querySelector('[data-propose]').disabled=!route.agent_id;form.querySelector('[data-propose]').title=route.agent_id?'':trainingText('noAgent');
    const close=()=>modal.remove();modal.querySelector('[data-close]').onclick=close;modal.onclick=e=>{if(e.target===modal)close();};
    form.onsubmit=async event=>{event.preventDefault();if(save.disabled)return;save.disabled=true;requestId=requestId||crypto.randomUUID();const propose=form.querySelector('[data-propose]').checked;
      try{const result=await api('support-tickets/complete','POST',{request_id:requestId,lead_id:route.lead_id,channel_id:route.channel_id,conversation_id:route.conversation_id,outcome:form.querySelector('select').value,note:form.querySelector('textarea').value,propose_knowledge:propose});
        const ticket=result.ticket;if(!ticket?.id)throw new Error('Обращение не подтверждено сервером.');document.dispatchEvent(new CustomEvent('tenant-support-changed'));status.textContent=completionText('done');form.querySelector('select').disabled=true;form.querySelector('textarea').disabled=true;form.querySelector('[data-propose]').disabled=true;save.hidden=true;
        if(propose){caseButton.hidden=false;caseButton.dataset.ticketId=ticket.id;}else modal.querySelector('[data-close]').focus();
      }catch(error){status.textContent=error.message;save.disabled=false;}
    };
    caseButton.onclick=async()=>{if(caseButton.disabled)return;if(!route.agent_id){status.textContent=trainingText('noAgent');return;}caseButton.disabled=true;analysisId=analysisId||crypto.randomUUID();status.textContent=completionText('creating');
      try{const response=await rawApi(endpoint+'knowledge-candidates',{method:'POST',body:JSON.stringify({expected_tenant_id:member?.tenant_id,expected_user_id:member?.telegram_id,request_id:analysisId,provider:'connect',channel_id:route.channel_id,conversation_id:route.conversation_id,history_mode:'channel',ticket_id:caseButton.dataset.ticketId,language:['ru','az','en'].includes(i18n.language)?i18n.language:'az'})});
        const candidates=response.result?.candidates||[];if(!candidates.length)throw new Error('AI не сформировал кандидата. Повторный запрос с новым ID не выполнялся.');
        const candidate=candidates[0];status.textContent=completionText('caseReady');const nav=document.querySelector('.nav [data-view="support_cases"]');if(!nav||nav.hidden)throw new Error('Откройте раздел «Кейсы поддержки», чтобы проверить кандидата.');nav.click();setTimeout(()=>window.tenantKnowledgeCases?.openCard(candidate.id),600);setTimeout(close,900);
      }catch(error){status.textContent=error.message;caseButton.disabled=false;}
    };
  }
  const trainingText=key=>({button:{ru:'На обучение',az:'Təlimə göndər',en:'Send for learning'},noAgent:{ru:'Сначала назначьте агента диалогу',az:'Əvvəlcə dialoqa agent təyin edin',en:'Assign an agent to this conversation first'},noAccess:{ru:'Нет доступа к кейсам',az:'Hallara giriş yoxdur',en:'No access to cases'},title:{ru:'Выберите границы обращения',az:'Müraciətin sərhədlərini seçin',en:'Select the request boundaries'},start:{ru:'Первое сообщение',az:'İlk mesaj',en:'First message'},end:{ru:'Последнее сообщение',az:'Son mesaj',en:'Last message'},submit:{ru:'Отправить на разбор',az:'Baxılmağa göndər',en:'Send for review'},loading:{ru:'Подготовка кейса…',az:'Hal hazırlanır…',en:'Preparing case…'},sent:{ru:'Принято на разбор. Вложения могут требовать отдельного анализа.',az:'Baxılmağa qəbul edildi. Əlavələr ayrıca təhlil tələb edə bilər.',en:'Accepted for review. Attachments may require separate analysis.'},duplicate:{ru:'Это обращение уже отправлено.',az:'Bu müraciət artıq göndərilib.',en:'This request was already submitted.'},retry:{ru:'Повторить после сбоя подготовки',az:'Hazırlıq xətasından sonra yenidən cəhd et',en:'Retry after preparation failure'},retryReady:{ru:'Сбой произошёл до отправки модели. Можно повторить вручную.',az:'Xəta modelə göndərilməzdən əvvəl baş verdi. Əl ilə yenidən cəhd edə bilərsiniz.',en:'Preparation failed before model dispatch. You can retry manually.'},failed:{ru:'Не удалось подготовить кейс.',az:'Hal hazırlana bilmədi.',en:'Could not prepare the case.'},earlier:{ru:'Показаны до 30 последних сообщений. Выберите одно обращение. Длинные тексты анализируются по сокращённым выдержкам, не целиком.',az:'Ən çox 30 son mesaj göstərilir. Bir müraciət seçin. Uzun mətnlər tam deyil, qısaldılmış hissələrlə təhlil olunur.',en:'Up to 30 recent messages are shown. Select one request. Long texts are analyzed as shortened excerpts, not in full.'}}[key]?.[i18n.language]||'');
  const trainingMessageKind=kind=>{const value=String(kind||'').toLowerCase(),labels={ru:{text:'Текст',attachment:'Вложение'},az:{text:'Mətn',attachment:'Qoşma'},en:{text:'Text',attachment:'Attachment'}}[i18n.language]||{text:'Text',attachment:'Attachment'};if(value==='text')return labels.text;const key=value==='image'?'photo':['audio','voice','voice_message','ptt'].includes(value)?'voiceMessage':value==='video'?'video':['document','file'].includes(value)?'file':'';return key?t(key):labels.attachment;};
  const trainingRoleLabel=role=>({customer:t('customer'),operator:t('operator'),employee:t('employee'),participant:t('groupParticipant'),ai:t('aiAgent'),system:t('system')})[String(role||'').toLowerCase()]||t('system');
  const trainingMessageLabel=(item,index)=>{const preview=String(item.text||'').replace(/\[Attachment pending_processing: contents not read\]/g,' ').replace(/\s+/g,' ').trim(),kind=trainingMessageKind(item.message_type||item.media_kind),time=item.happened_at?i18n.date(item.happened_at):({ru:'Время неизвестно',az:'Vaxt məlum deyil',en:'Time unavailable'}[i18n.language]||'Time unavailable'),name=String(item.sender_name||'').trim(),sender=name?[name,trainingRoleLabel(item.role)].join(' · '):trainingRoleLabel(item.role);return [String(index+1)+'. '+sender,time,kind,preview].filter(Boolean).join(' · ');};
  async function openTraining(route,same){
    const old=$('operatorTrainingModal');old?.remove();const modal=document.createElement('div');modal.className='operator-complete-modal';modal.id='operatorTrainingModal';
    modal.innerHTML=`<section class="operator-complete-card" role="dialog" aria-modal="true"><h2>${esc(trainingText('title'))}</h2><p data-agent></p><p>${esc(trainingText('earlier'))}</p><label>${esc(trainingText('start'))}<select data-start></select></label><label>${esc(trainingText('end'))}<select data-end></select></label><p role="status"></p><div class="operator-complete-actions"><button type="button" class="primary" data-submit disabled>${esc(trainingText('submit'))}</button><button type="button" class="outline" data-retry hidden>${esc(trainingText('retry'))}</button><button type="button" class="outline" data-close>${esc(completionText('close'))}</button></div></section>`;
    document.body.append(modal);const status=modal.querySelector('[role=status]'),send=modal.querySelector('[data-submit]');modal.querySelector('[data-close]').onclick=()=>modal.remove();
    const expected={expected_tenant_id:member?.tenant_id,expected_user_id:member?.telegram_id},call=(path,data)=>rawApi(endpoint+'knowledge-candidates/'+path,{method:'POST',body:JSON.stringify({...expected,...data})});
    try{const result=(await call('operator-preview',{channel_id:route.channel_id,conversation_id:route.conversation_id})).result;if(!same()||!modal.isConnected){modal.remove();return;}
      modal.querySelector('[data-agent]').textContent=result.agent.agent_name;const items=result.messages||[],start=modal.querySelector('[data-start]'),end=modal.querySelector('[data-end]');
      for(const select of [start,end]){const option=document.createElement('option');option.value='';option.textContent='—';select.append(option);}items.forEach((item,index)=>{const label=trainingMessageLabel(item,index);for(const select of [start,end]){const option=document.createElement('option');option.value=String(index);option.textContent=label;select.append(option);}});let busy=false,retryId=null;const retry=modal.querySelector('[data-retry]');const checkBounds=()=>{retryId=null;retry.hidden=true;send.disabled=busy||start.value===''||end.value===''||Number(start.value)>Number(end.value)||Number(end.value)-Number(start.value)+1>(result.max_messages||30);};start.onchange=checkBounds;end.onchange=checkBounds;checkBounds();
      const submitAttempt=async(parentId=null)=>{
        if(busy)return;if(!same()||!modal.isConnected){modal.remove();return;}
        const first=Number(start.value),last=Number(end.value);
        if(start.value===''||end.value===''||first>last||last-first+1>(result.max_messages||30)){status.textContent=trainingText('title');return;}
        busy=true;send.disabled=true;retry.disabled=true;start.disabled=true;end.disabled=true;status.textContent=trainingText('loading');
        try{
          const response=(await call('operator-submit',{channel_id:route.channel_id,conversation_id:route.conversation_id,message_ids:items.slice(first,last+1).map(item=>item.id),agent_id:result.agent.agent_id,agent_version:result.agent.agent_version,assignment_revision:result.agent.assignment_revision,...(parentId?{retry_request_id:parentId}:{})})).result;
          if(!same()||!modal.isConnected){modal.remove();return;}
          retryId=response.preflight_retry_available===true&&response.request?.id?response.request.id:null;retry.hidden=!retryId;
          const state=response.request?.execution_status;
          if(retryId)status.textContent=trainingText('retryReady');
          else if(response.already_submitted&&(state==='running'||(state==='completed'&&(response.candidates||[]).length)))status.textContent=trainingText('duplicate');
          else if(state==='completed'&&(response.candidates||[]).length)status.textContent=trainingText('sent');
          else status.textContent=trainingText('failed');
        }catch(error){if(modal.isConnected)status.textContent=error.message||trainingText('failed');}
        finally{busy=false;if(modal.isConnected){send.disabled=false;retry.disabled=false;start.disabled=false;end.disabled=false;}}
      };
      send.onclick=()=>submitAttempt();retry.onclick=()=>retryId?submitAttempt(retryId):undefined;
    }catch(error){if(modal.isConnected)status.textContent=error.message||trainingText('failed');}
  }
  async function rawApi(url,options={}, {optionalScopedRead=false}={}){
    const version=epoch,who=identity(member);
    const response=await fetch(url,{...options,credentials:'same-origin',cache:'no-store',headers:{...(options.body?{'Content-Type':'application/json'}:{}),...((options.method||'GET')==='GET'?{'X-Tenant-Id':String(member?.tenant_id||''),'X-User-Id':String(member?.telegram_id||'')}:{ }),...options.headers}});
    const payload=await response.json();
    if(!document?.body||version!==epoch||who!==identity(member))throw new Error(t('companyChanged'));
    if(!optionalScopedRead&&(response.status===401||response.status===403||((options.method||'GET')==='GET'&&response.status===409&&payload.scope_verified!==true))){profileChanged({});if(response.status===409)void window.tenantRequestCoordinator?.revalidateProfile().catch(()=>{});}
    if((options.method||'GET')!=='GET'){listFlight=null;lastListAt=0;}
    if(!response.ok||!payload.success){const error=new Error(payload.error||t('error'));error.status=response.status;error.keepRequest=payload.keep_request!==false;throw error;}
    return payload;
  }
  const api=async(path,method='GET',data)=>(await rawApi(endpoint+path,{method,...(data?{body:JSON.stringify({...data,expected_tenant_id:member?.tenant_id,expected_user_id:member?.telegram_id})}:{})})).result;
  const duplicateCopy=(key)=>({
    ru:{title:'Поиск дублей',phone:'По номеру',name:'По имени',missing:'Диалоги без номера',search:'Найти',query:'Имя или последние 7 цифр номера',close:'Закрыть',open:'Открыть',prev:'Предыдущая',next:'Следующая',count:'{index} из {total}',more:'Ещё',empty:'Диалоги не найдены.',unavailable:'Объединение пока недоступно',noPhone:'Нет подтверждённого номера',noName:'Без имени',failed:'Не удалось выполнить действие. Обновите поиск и попробуйте снова.',busy:'Загрузка…',manual:'Указать номер для отображения',save:'Сохранить'},
    az:{title:'Təkrar dialoqları tap',phone:'Nömrəyə görə',name:'Ada görə',missing:'Nömrəsiz dialoqlar',search:'Axtar',query:'Ad və ya nömrənin son 7 rəqəmi',close:'Bağla',open:'Aç',prev:'Əvvəlki',next:'Növbəti',count:'{index} / {total}',more:'Daha çox',empty:'Dialoq tapılmadı.',unavailable:'Birləşdirmə hələ mümkün deyil',noPhone:'Təsdiqlənmiş nömrə yoxdur',noName:'Adsız',failed:'Əməliyyat baş tutmadı. Axtarışı yeniləyib təkrar yoxlayın.',busy:'Yüklənir…',manual:'Göstəriləcək nömrə',save:'Yadda saxla'},
    en:{title:'Find duplicates',phone:'By phone',name:'By name',missing:'Dialogs without a phone',search:'Search',query:'Name or last 7 digits',close:'Close',open:'Open',prev:'Previous',next:'Next',count:'{index} of {total}',more:'More',empty:'No dialogs found.',unavailable:'Merging is not available yet',noPhone:'No verified phone',noName:'No name',failed:'Could not complete the action. Refresh the search and try again.',busy:'Loading…',manual:'Set display phone',save:'Save'}
  }[i18n.language]||{})[key]||({title:'Find duplicates',close:'Close'}[key]||key);
  const canUseDuplicateSearch=()=>member?.role==='owner'&&hasRight('dialogs');
  const mergeCopy=key=>({
    ru:{start:'Объединить',status:'Статус объединения',running:'Выполняется объединение',done:'Диалоги объединены',steps:'Проценты показывают этапы объединения. Сообщения загрузятся по мере просмотра.',ready:'Сообщения загружаются по мере просмотра.',queue:'Ожидание обработки',cancel:'Отменить объединение',cancelled:'Объединение отменено',blocked:'Эти диалоги нельзя безопасно объединить.',failed:'Не удалось проверить объединение. Откройте статус ещё раз.',empty:'Для этой пары пока нет задания.',processed:'Проверено: {n}',left:'Первый диалог',right:'Второй диалог'},
    az:{start:'Birləşdir',status:'Birləşdirmə vəziyyəti',running:'Birləşdirmə davam edir',done:'Dialoqlar birləşdirildi',steps:'Faiz birləşdirmə mərhələlərini göstərir. Mesajlar baxdıqca yüklənir.',ready:'Mesajlar baxdıqca yüklənir.',queue:'Emal gözlənilir',cancel:'Birləşdirməni ləğv et',cancelled:'Birləşdirmə ləğv edildi',blocked:'Bu dialoqları təhlükəsiz birləşdirmək olmur.',failed:'Birləşdirməni yoxlamaq olmadı. Vəziyyəti yenidən açın.',empty:'Bu cütlük üçün hələ tapşırıq yoxdur.',processed:'Yoxlanılıb: {n}',left:'Birinci dialoq',right:'İkinci dialoq'},
    en:{start:'Merge',status:'Merge status',running:'Merging dialogs',done:'Dialogs merged',steps:'Progress tracks merge steps. Messages load as you view them.',ready:'Messages load as you view them.',queue:'Waiting to be processed',cancel:'Cancel merge',cancelled:'Merge cancelled',blocked:'These dialogs cannot be safely merged.',failed:'Could not check the merge. Open the status again.',empty:'No job exists for this pair yet.',processed:'Checked: {n}',left:'First dialog',right:'Second dialog'}
  }[i18n.language]||{})[key]||key;
  function mergeBlocked(code){
    const messages={
      ru:{display_changed:'Имя или номер изменились. Сравните диалоги заново.',capacity_reached:'Достигнут лимит активных объединений для этого источника.',lifecycle_in_use:'У одного из диалогов есть история работы. Такие диалоги пока не объединяются.',identity_unproven:'Не удалось подтвердить, что это один контакт WhatsApp.',scope_changed:'Доступ или источник изменился. Проверьте диалоги снова.',stale_proof:'Данные подключения изменились. Проверьте диалоги снова.'},
      az:{display_changed:'Ad və ya nömrə dəyişib. Dialoqları yenidən müqayisə edin.',capacity_reached:'Bu mənbə üzrə aktiv birləşdirmə həddinə çatılıb.',lifecycle_in_use:'Dialoqlardan birinin iş tarixçəsi var. Belə dialoqlar hələ birləşdirilmir.',identity_unproven:'Bunun eyni WhatsApp kontaktı olduğu təsdiqlənmədi.',scope_changed:'Giriş və ya mənbə dəyişdi. Dialoqları yenidən yoxlayın.',stale_proof:'Bağlantı məlumatları dəyişdi. Dialoqları yenidən yoxlayın.'},
      en:{display_changed:'The display name or number has changed. Compare the dialogs again.',capacity_reached:'This source has reached the active merge limit.',lifecycle_in_use:'One dialog has workflow history and cannot currently be merged.',identity_unproven:'The two dialogs could not be verified as the same WhatsApp contact.',scope_changed:'Access or the source changed. Check the dialogs again.',stale_proof:'The connection details changed. Check the dialogs again.'}
    };
    return messages[i18n.language]?.[code]||mergeCopy('blocked');
  }
  const canUseHistoryMerge=source=>canUseDuplicateSearch()&&capabilities.dialog_history_merge===true&&
    typeof capabilities.dialog_history_merge_source_id==='string'&&!!capabilities.dialog_history_merge_source_id&&
    (source===undefined||source===capabilities.dialog_history_merge_source_id);
  let mergeDialog=null;
  const mergeRequests=new Map();
  const mergeInProgress=job=>['queued','running','processing'].includes(job?.state);
  const mergeScopeCurrent=state=>state.epoch===epoch&&state.owner===identity(member)&&canUseHistoryMerge(state.source);
  const mergeCurrent=state=>state===mergeDialog&&mergeScopeCurrent(state);
  function closeMergeDialog(){
    const state=mergeDialog;if(!state)return;
    mergeDialog=null;
    clearTimeout(state.timer);
    if(state.node.open&&typeof state.node.close==='function')state.node.close();
    state.node.remove();
    if(duplicateDialog&&canUseHistoryMerge())duplicateDialog.node.querySelector('[data-dupe-merge-status]')?.focus();
  }
  function renderMergeDialog(state){
    if(!mergeCurrent(state))return;
    const {node,job}=state;
    const progress=node.querySelector('progress'),text=node.querySelector('[data-dupe-merge-progress]');
    const done=job?.state==='completed',parsed=job?.processed,total=job?.total;
    const count=Number.isSafeInteger(parsed)&&parsed>=0?parsed:null;
    const known=Number.isSafeInteger(total)&&total>0&&count!==null&&count<=total;
    const percent=done?100:known&&count<total?Math.floor(count*100/total):null;
    node.querySelector('h2').textContent=mergeCopy(done?'done':mergeInProgress(job)||state.loading?'running':'status');
    progress.hidden=!done&&!mergeInProgress(job)&&state.loading!==true;
    const note=node.querySelector('[data-dupe-merge-note]');
    note.hidden=!done&&!mergeInProgress(job);
    note.textContent=done?mergeCopy('ready'):mergeCopy('steps');
    if(percent===null)progress.removeAttribute('value');else progress.value=percent;
    text.textContent=state.error?mergeCopy('failed'):
      !job?(state.loading?duplicateCopy('busy'):mergeCopy('empty')):job.state==='blocked'?mergeBlocked(job.code):
      job.state==='failed'?mergeCopy('failed'):['cancelled','canceled'].includes(job.state)?mergeCopy('cancelled'):
      percent!==null?percent+'%':count!==null&&count>0?mergeCopy('processed').replace('{n}',String(count)):
      job.state==='queued'?mergeCopy('queue'):duplicateCopy('busy');
    const cancel=node.querySelector('[data-dupe-merge-cancel]');
    cancel.hidden=!mergeInProgress(job)||!job.id;
    cancel.disabled=state.loading;
  }
  function scheduleMergePoll(state){
    if(!mergeCurrent(state)||!mergeInProgress(state.job)||!state.job.id)return;
    clearTimeout(state.timer);
    state.timer=setTimeout(()=>{
      if(!mergeCurrent(state))return;
      if(document.hidden){scheduleMergePoll(state);return;}
      void mergeStatusRequest(state,'status',{job_id:state.job.id});
    },2500);
  }
  async function mergeStatusRequest(state,operation,fields){
    if(!mergeCurrent(state)||state.loading)return;
    clearTimeout(state.timer);
    state.loading=true;renderMergeDialog(state);
    try{
      const result=await api('dialogs/history-merges','POST',{operation,...fields});
      if(!mergeCurrent(state))return;
      const job=result&&Object.hasOwn(result,'job')?result.job:result;
      if(job==null&&operation!=='latest')throw Error('Missing job');
      if(job!=null&&(!job.id||!['queued','running','processing','completed','cancelled','canceled','blocked','failed'].includes(job.state)))throw Error('Unknown job state');
      if(state.job&&job&&state.job.id!==job.id)throw Error('Job changed');
      if(job?.state==='completed'&&state.job?.state!=='completed')void refresh(false,false);
      state.job=job;
    }catch{
      if(mergeCurrent(state))state.error=true;
    }finally{
      if(mergeCurrent(state)){
        state.loading=false;renderMergeDialog(state);
        if(!state.error)scheduleMergePoll(state);
      }
    }
  }
  const mergeDisplayCopy=()=>({
    ru:{name:'Имя в общей карточке',phone:'Телефон в общей карточке',source:'Диалог',choose:'Выберите вариант',none:'Нет доступного варианта',load:'Загружаем варианты имени и телефона…',failed:'Не удалось проверить варианты.',retry:'Повторить'},
    az:{name:'Ümumi kartdakı ad',phone:'Ümumi kartdakı telefon',source:'Dialoq',choose:'Variant seçin',none:'Variant mövcud deyil',load:'Ad və telefon variantları yüklənir…',failed:'Variantları yoxlamaq mümkün olmadı.',retry:'Yenidən yoxla'},
    en:{name:'Display name on the combined card',phone:'Phone on the combined card',source:'Dialogue',choose:'Choose an option',none:'No option available',load:'Loading name and phone options…',failed:'Could not verify display options.',retry:'Retry'},
  })[i18n.language]||{name:'Display name',phone:'Display phone',source:'Dialogue',choose:'Choose an option',none:'No option available',load:'Loading display options…',failed:'Could not verify display options.',retry:'Retry'};
  const mergeComparePair=(group,panel)=>{
    const picks=[...panel.querySelectorAll('[data-dupe-merge-pick]')];
    const [left,right]=(group.length===2?[0,1]:picks.map(input=>Number(input.value))).map(i=>group[i]);
    if(!left||!right||left.channel_id!==right.channel_id||left.lead_id===right.lead_id||left.conversation_id===right.conversation_id||
       ![left,right].every(row=>row.provider==='connect'&&Number.isSafeInteger(row.lead_id)&&row.lead_id>0&&typeof row.conversation_id==='string'&&!!row.conversation_id)||
       !canUseHistoryMerge(left.channel_id))return null;
    return {left,right,key:JSON.stringify([dialogKey(left),dialogKey(right)].sort())};
  };
  function mergeDisplayOptions(result,pair){
    const field=(rows,key)=>{
      if(!Array.isArray(rows)||rows.length>2)throw Error('Invalid display options');
      const found=new Map();
      for(const item of rows){
        if(![pair.left.lead_id,pair.right.lead_id].includes(item?.lead_id)||typeof item[key]!=='string')throw Error('Out-of-pair display option');
        const label=item[key].trim();
        if(!label||label.length>180||/[\x00-\x1f\x7f]/.test(label)||(key==='display_phone'&&!verifiedDialogPhone({phone:label})))throw Error('Invalid display option');
        if(found.has(item.lead_id))throw Error('Ambiguous display option');
        found.set(item.lead_id,label);
      }
      return [...found].map(([lead_id,label])=>({lead_id,label}));
    };
    if(typeof result?.display_revision!=='string'||!/^[a-f0-9]{64}$/.test(result.display_revision))throw Error('Invalid display revision');
    return {revision:result.display_revision,name:field(result.name_choices,'display_name'),phone:field(result.phone_choices,'display_phone')};
  }
  function wireMergeDisplayChoices(search,group,panel,start,statusButton,busy){
    const slot=panel.querySelector('[data-dupe-display]'),picks=[...panel.querySelectorAll('[data-dupe-merge-pick]')];
    const copy=mergeDisplayCopy();
    let loadGeneration=0;
    const current=(pair,generation)=>duplicateCurrent(search)&&generation===loadGeneration&&slot.isConnected&&mergeComparePair(group,panel)?.key===pair.key;
    const disable=()=>{start.disabled=true;start.onclick=null;slot.replaceChildren();};
    const load=()=>{
      const generation=++loadGeneration;disable();
      const pair=mergeComparePair(group,panel);
      statusButton.disabled=busy||!pair;
      if(busy||!pair)return;
      const previews=search.mergePreviews;
      let preview=previews.get(pair.key);
      if(!preview){
        if(previews.size>=8)previews.delete(previews.keys().next().value);
        preview=api('dialogs/duplicates/preview','POST',{left:duplicateRoute(pair.left),right:duplicateRoute(pair.right)})
          .then(result=>mergeDisplayOptions(result,pair));
        previews.set(pair.key,preview);
      }
      const note=document.createElement('p');note.setAttribute('role','status');note.textContent=copy.load;slot.append(note);
      preview.then(choices=>{
        if(!current(pair,generation))return;
        slot.replaceChildren();
        const fields={};
        for(const key of ['name','phone']){
          const label=document.createElement('label'),select=document.createElement('select');
          const options=choices[key];
          label.textContent=copy[key];select.dataset.dupeDisplayField=key;fields[key]=select;
          select.add(new Option(options.length?copy.choose:copy.none,''));
          for(const [index,option] of options.entries()){
            const source=option.lead_id===pair.left.lead_id?1:2;
            select.add(new Option(option.label+' · '+copy.source+' '+source,String(index)));
          }
          select.disabled=!options.length;label.append(select);slot.append(label);
        }
        function display(){
          const picked={revision:choices.revision};
          for(const key of ['name','phone']){
            const options=choices[key],value=fields[key].value;
            if(!options.length)picked[key+'_lead_id']=null;
            else if(!/^\d+$/.test(value)||!options[Number(value)])return null;
            else picked[key+'_lead_id']=options[Number(value)].lead_id;
          }
          return picked;
        }
        for(const select of Object.values(fields))select.onchange=()=>{start.disabled=!current(pair,generation)||!display();};
        start.onclick=()=>{const selected=display();if(current(pair,generation)&&selected)openMergeDialog(search,group,'create',selected);};
        start.disabled=!display();
      },()=>{
        if(!current(pair,generation))return;
        note.textContent=copy.failed;
        const retry=document.createElement('button');retry.type='button';retry.className='outline';retry.textContent=copy.retry;
        retry.onclick=()=>{if(!current(pair,generation))return;previews.delete(pair.key);load();};
        slot.append(retry);
      });
    };
    for(const select of picks)select.onchange=load;
    load();
  }
  function openMergeDialog(search,group,operation,display=null){
    if(!duplicateCurrent(search)||!canUseHistoryMerge()||mergeDialog||group.length<2)return;
    const panel=search.node.querySelector('[data-dupe-results]');
    const pair=mergeComparePair(group,panel);
    if(!pair||(operation==='create'&&!display))return;
    const {left,right}=pair;
    const [a,b]=[left,right].sort((a,b)=>dialogKey(a).localeCompare(dialogKey(b)));
    const routes={left:duplicateRoute(a),right:duplicateRoute(b)};
    const node=document.createElement('dialog');
    node.id='operatorHistoryMerge';node.setAttribute('aria-labelledby','operatorHistoryMergeTitle');
    node.innerHTML=`<header><h2 id="operatorHistoryMergeTitle"></h2><button type="button" class="outline" data-dupe-merge-close aria-label="${esc(duplicateCopy('close'))}">×</button></header><p role="status" data-dupe-merge-progress></p><p class="sub" data-dupe-merge-note hidden></p><progress max="100" aria-label="${esc(mergeCopy('running'))}"></progress><button type="button" class="outline" data-dupe-merge-cancel hidden>${esc(mergeCopy('cancel'))}</button>`;
    const state={node,owner:search.owner,epoch:search.epoch,source:left.channel_id,job:null,loading:false,error:false};
    mergeDialog=state;document.body.append(node);
    const close=node.querySelector('[data-dupe-merge-close]');
    close.onclick=closeMergeDialog;
    node.addEventListener('cancel',event=>{event.preventDefault();closeMergeDialog();});
    node.querySelector('[data-dupe-merge-cancel]').onclick=()=>{if(state.job?.id)void mergeStatusRequest(state,'cancel',{job_id:state.job.id});};
    if(typeof node.showModal==='function')node.showModal();else node.setAttribute('open','');
    close.focus();
    if(operation==='latest'){void mergeStatusRequest(state,'latest',routes);return;}
    const key=JSON.stringify([state.owner,pair.key,display]);
    // Do not create a second job while an earlier click for this pair is still
    // unacknowledged, even if the owner closed and reopened the progress modal.
    const requestId=mergeRequests.get(key)||crypto.randomUUID();
    if(!mergeRequests.has(key)&&mergeRequests.size>=20)mergeRequests.delete(mergeRequests.keys().next().value);
    mergeRequests.set(key,requestId);
    void mergeStatusRequest(state,'create',{...routes,display,request_id:requestId}).finally(()=>{
      if(state.job&&mergeRequests.get(key)===requestId)mergeRequests.delete(key);
    });
  }
  let duplicateDialog=null;
  const duplicateRoute=row=>({provider:'connect',channel_id:row.channel_id,conversation_id:row.conversation_id,lead_id:row.lead_id});
  const duplicateCardName=row=>{const name=dialogTitle(row);return name===t('whatsappContact')?duplicateCopy('noName'):name;};
  function syncDuplicateButton(){
    const button=$('operatorFindDuplicates');
    if(button){button.hidden=!canUseDuplicateSearch();button.textContent=duplicateCopy('title');}
  }
  function closeDuplicateSearch(force=false){
    const state=duplicateDialog;if(!state||state.stage==='saving'&&!force)return;
    closeMergeDialog();
    duplicateDialog=null;
    if(state.node.open&&typeof state.node.close==='function')state.node.close();
    state.node.remove();
    if(!force&&canUseDuplicateSearch())$('operatorToolsMenu')?.querySelector('summary')?.focus();
  }
  const duplicateCurrent=state=>state===duplicateDialog&&state.epoch===epoch&&state.owner===identity(member)&&canUseDuplicateSearch();
  function duplicateGroups(state){
    const groups=new Map();
    for(const row of state.results){
      if(!Number.isSafeInteger(row.candidate_group))continue;
      // Defense-in-depth: a server group cannot authorize a cross-source pair.
      const key=JSON.stringify([row.candidate_group,row.channel_id]);
      if(!groups.has(key))groups.set(key,[]);
      groups.get(key).push(row);
    }
    return [...groups.values()].filter(group=>group.length>=2);
  }
  const completedDuplicateRoute=row=>Boolean(row.archive_read_only||['closed','dismissed'].includes(row.lifecycle?.state));
  const duplicateDisplayPhoneEditable=row=>row.display_phone_editable===true&&!completedDuplicateRoute(row)
    &&!row.contact_identity_conflict&&!row.identity_conflict&&!verifiedDialogPhone(row)&&!contactDetails(row)?.phone;
  const duplicateCardDetails=row=>[verifiedDialogPhone(row)||verifiedDialogPhone({phone:row.manual_display_phone}),row.channel_name].filter(Boolean).join(' · ');
  function renderDuplicateResults(state){
    if(!duplicateCurrent(state))return;
    const panel=state.node.querySelector('[data-dupe-results]');
    const groups=duplicateGroups(state),group=groups[state.groupIndex]||[],busy=['searching','saving'].includes(state.stage);
    const status=state.node.querySelector('[data-dupe-status]');
    status.textContent=state.error||((busy)?duplicateCopy('busy'):'');
    state.node.querySelectorAll('[data-dupe-form] input,[data-dupe-form] button').forEach(el=>el.disabled=state.stage==='saving');
    state.node.querySelector('[data-dupe-close]').disabled=state.stage==='saving';
    const visible=state.mode==='missing'?state.results:group;
    const cards=visible.map(row=>{
      const index=state.results.indexOf(row);
      // No internal route identifiers ever enter displayed HTML or attributes.
      return `<div class="operator-dupe-row"><button type="button" class="outline operator-dupe-open" data-dupe-open="${index}"><strong data-user-content>${esc(duplicateCardName(row))}</strong><span data-user-content>${esc(duplicateCardDetails(row))}</span><small>${esc(duplicateCopy('open'))}</small></button>
      ${state.mode==='missing'&&duplicateDisplayPhoneEditable(row)?`<form data-dupe-phone-form="${index}"><input type="tel" maxlength="32" aria-label="${esc(duplicateCopy('manual'))}" placeholder="${esc(duplicateCopy('manual'))}" required ${busy?'disabled':''}><button type="submit" class="outline" ${busy?'disabled':''}>${esc(duplicateCopy('save'))}</button></form>`:''}</div>`;
    }).join('');
    panel.innerHTML=cards?(state.mode==='missing'?cards:`<div class="operator-dupe-pair">${cards}</div>`):((state.searched&&!busy&&!state.error)?`<p>${esc(duplicateCopy('empty'))}</p>`:'');
    if(groups.length&&state.mode!=='missing'){
      const count=duplicateCopy('count').replace('{index}',String(state.groupIndex+1)).replace('{total}',String(groups.length));
      panel.insertAdjacentHTML('afterbegin',`<nav class="operator-dupe-actions"><button type="button" class="outline" data-dupe-prev ${busy||!state.groupIndex?'disabled':''}>← <span>${esc(duplicateCopy('prev'))}</span></button><span aria-live="polite" data-dupe-count>${esc(count)}</span><button type="button" class="outline" data-dupe-next ${busy||state.groupIndex>=groups.length-1?'disabled':''}><span>${esc(duplicateCopy('next'))}</span> →</button></nav>`);
      panel.querySelector('[data-dupe-prev]').onclick=()=>move(-1);
      panel.querySelector('[data-dupe-next]').onclick=()=>move(1);
      function move(increment){
        const next=state.groupIndex+increment;
        if(!duplicateCurrent(state)||busy||next<0||next>=groups.length)return;
        state.error='';state.groupIndex=next;renderDuplicateResults(state);
        panel.querySelector(increment>0?'[data-dupe-next]:not(:disabled)':'[data-dupe-prev]:not(:disabled)')?.focus();
      }
    }
    if(state.mode!=='missing'&&group.length){
      if(!canUseHistoryMerge(group[0]?.channel_id)){
        panel.insertAdjacentHTML('beforeend',`<p data-dupe-merge-unavailable>${esc(duplicateCopy('unavailable'))}</p>`);
      }else{
        const options=group.map((row,index)=>`<option value="${index}">${esc(duplicateCardName(row))}</option>`).join('');
        panel.insertAdjacentHTML('beforeend',`${group.length>2?`<div class="operator-dupe-actions"><label>${esc(mergeCopy('left'))}<select data-dupe-merge-pick>${options}</select></label><label>${esc(mergeCopy('right'))}<select data-dupe-merge-pick>${options}</select></label></div>`:''}<div data-dupe-display></div><div class="operator-dupe-actions"><button type="button" class="primary" data-dupe-merge-start ${busy?'disabled':''}>${esc(mergeCopy('start'))}</button><button type="button" class="outline" data-dupe-merge-status ${busy?'disabled':''}>${esc(mergeCopy('status'))}</button></div>`);
        const selectors=[...panel.querySelectorAll('[data-dupe-merge-pick]')];
        if(selectors.length)selectors[1].value='1';
        const start=panel.querySelector('[data-dupe-merge-start]'),statusButton=panel.querySelector('[data-dupe-merge-status]');
        wireMergeDisplayChoices(state,group,panel,start,statusButton,busy);
        statusButton.onclick=()=>openMergeDialog(state,group,'latest');
      }
    }
    if(state.offset!=null)panel.insertAdjacentHTML('beforeend',`<button type="button" class="outline" data-dupe-more ${busy?'disabled':''}>${esc(duplicateCopy('more'))}</button>`);
    panel.querySelectorAll('[data-dupe-open]').forEach(button=>button.onclick=()=>{
      const row=state.results[Number(button.dataset.dupeOpen)];
      if(!duplicateCurrent(state)||!row||state.stage==='saving')return;
      closeDuplicateSearch();void openDialog(row,false,'',{userOpened:true});
    });
    panel.querySelectorAll('[data-dupe-phone-form]').forEach(form=>form.onsubmit=event=>{
      event.preventDefault();
      const input=form.querySelector('input');
      if(!verifiedDialogPhone({phone:input.value})){
        input.setCustomValidity(duplicateCopy('noPhone'));input.reportValidity();input.oninput=()=>input.setCustomValidity('');return;
      }
      void saveDuplicateDisplayPhone(state,Number(form.dataset.dupePhoneForm),input.value);
    });
    const more=panel.querySelector('[data-dupe-more]');
    if(more)more.onclick=()=>void searchDuplicateDialogs(state,state.mode,true);
  }
  async function searchDuplicateDialogs(state,mode,append=false){
    if(!duplicateCurrent(state)||state.stage==='saving')return;
    const rev=++state.rev;
    state.error='';state.stage='searching';state.searched=true;
    if(!append){state.results=[];state.offset=null;state.groupIndex=0;state.mergePreviews.clear();}
    state.mode=mode;
    renderDuplicateResults(state);
    try{
      const result=await api('dialogs/duplicates','POST',{mode,offset:append?state.offset:0});
      if(!duplicateCurrent(state)||rev!==state.rev)return;
      const incoming=Array.isArray(result?.dialogs)?result.dialogs.filter(row=>row?.provider==='connect'&&row.channel_id&&row.conversation_id&&row.lead_id):[];
      const rowsByKey=new Map(state.results.map(row=>[dialogKey(row),row]));
      for(const row of incoming)rowsByKey.set(dialogKey(row),row);
      state.results=[...rowsByKey.values()];
      state.offset=Number.isSafeInteger(result?.next_offset)?result.next_offset:null;
    }catch{if(duplicateCurrent(state)&&rev===state.rev)state.error=duplicateCopy('failed');}
    if(duplicateCurrent(state)&&rev===state.rev){state.stage='results';renderDuplicateResults(state);}
  }
  async function saveDuplicateDisplayPhone(state,index,value){
    const row=state.results[index],phone=String(value).trim();
    if(!duplicateCurrent(state)||!row||state.stage!=='results'||!verifiedDialogPhone({phone})||!duplicateDisplayPhoneEditable(row))return;
    state.stage='saving';state.error='';renderDuplicateResults(state);
    try{
      const result=await api('dialogs/contact-display-phone','POST',{...duplicateRoute(row),phone});
      if(!duplicateCurrent(state))return;
      const saved=verifiedDialogPhone({phone:result?.display_phone});
      if(!saved)throw new Error('no display phone');
      state.results=state.results.filter(item=>!sameDialog(item,row));
      void refresh(false,false);
    }catch{if(duplicateCurrent(state))state.error=duplicateCopy('failed');}
    if(duplicateCurrent(state)){state.stage='results';renderDuplicateResults(state);}
  }
  function openDuplicateSearch(){
    if(!canUseDuplicateSearch()||duplicateDialog)return;
    $('operatorToolsMenu').open=false;
    const node=document.createElement('dialog');
    node.id='operatorDuplicateSearch';node.setAttribute('aria-labelledby','operatorDuplicateTitle');
    node.innerHTML=`<header><h2 id="operatorDuplicateTitle">${esc(duplicateCopy('title'))}</h2><button type="button" class="outline" data-dupe-close aria-label="${esc(duplicateCopy('close'))}">×</button></header>
      <form data-dupe-form>
        <label><input type="checkbox" data-dupe-mode="phone" checked> ${esc(duplicateCopy('phone'))}</label>
        <label><input type="checkbox" data-dupe-mode="name"> ${esc(duplicateCopy('name'))}</label>
        <button type="submit" class="primary">${esc(duplicateCopy('search'))}</button>
      </form>
      <p role="status" data-dupe-status></p><section data-dupe-results></section>`;
    const state={node,owner:identity(member),epoch,rev:0,mode:'phone',offset:null,results:[],groupIndex:0,mergePreviews:new Map(),stage:'results'};
    duplicateDialog=state;document.body.append(node);
    const form=node.querySelector('form'),phone=form.querySelector('[data-dupe-mode="phone"]'),name=form.querySelector('[data-dupe-mode="name"]');
    form.onsubmit=event=>{event.preventDefault();void searchDuplicateDialogs(state,phone.checked?(name.checked?'both':'phone'):(name.checked?'name':'missing'));};
    node.querySelector('[data-dupe-close]').onclick=()=>closeDuplicateSearch();
    node.addEventListener('cancel',event=>{event.preventDefault();closeDuplicateSearch();});
    if(typeof node.showModal==='function')node.showModal();else node.setAttribute('open','');
    phone.focus();
  }
  function install(){
    const host=$('operatorDialogs');if(!host||$('operatorRows'))return;
    host.innerHTML=`<div class="operator-workspace" id="operatorWorkspace"><section class="operator-list-pane" aria-label="${esc(t('dialogs'))}"><div class="operator-filters"><label class="operator-search"><span class="operator-search-label" data-i18n="search">${t('search')}</span><input type="search" id="operatorSearch" autocomplete="off" data-lpignore="true" data-1p-ignore="true" data-i18n-placeholder="searchShort" placeholder="${esc(t('searchShort'))}" aria-label="${esc(t('search'))}"></label><details class="operator-tools-menu" id="operatorToolsMenu"><summary aria-label="${esc(toolsLabel())}" title="${esc(toolsLabel())}" aria-haspopup="menu" aria-controls="operatorToolsPopover" aria-expanded="false"><svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg></summary><div id="operatorToolsPopover" role="menu" aria-label="${esc(toolsLabel())}"><button type="button" class="outline" role="menuitem" id="operatorSoundToggle" aria-pressed="true"></button><span id="operatorSoundStatus" role="status" aria-live="polite"></span><button type="button" class="outline" role="menuitem" id="operatorNotificationToggle" aria-pressed="false"></button><span id="operatorNotificationStatus" role="status" aria-live="polite"></span><button type="button" class="outline" role="menuitem" id="operatorFindDuplicates" hidden></button><button type="button" class="outline" role="menuitem" id="operatorBulkAssignment" hidden>${bulkText('preview')}</button><button type="button" class="outline" role="menuitem" id="operatorCompleteAll">${esc(completeAllCopy('title'))}</button><button type="button" class="outline" role="menuitem" id="operatorMarkAllRead">${esc(t('markAllRead'))}</button></div></details><button type="button" class="outline operator-filter-toggle" id="operatorFiltersToggle" aria-expanded="false" aria-controls="operatorAdvanced" aria-label="${esc(t('filters'))}" title="${esc(t('filters'))}"><svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16l-6.5 7.5V19l-3 1v-7.5z"/></svg></button><div class="operator-advanced" id="operatorAdvanced" role="group" aria-label="${esc(t('filters'))}" data-open="false"><label><span data-i18n="channel">${t('channel')}</span><select id="operatorChannel"></select></label><label><span data-i18n="agent">${t('agent')}</span><select id="operatorAgent"></select></label><label><span data-i18n="status">${t('status')}</span><select id="operatorState"></select></label><label><span id="operatorFilterScopeLabel">${esc(filterCopy('scope'))}</span><select id="operatorLifecycle"></select></label><p id="operatorFilterSaveStatus" role="status" aria-live="polite"></p></div></div><section id="operatorBulkPreview" class="operator-bulk-preview" hidden></section><p id="operatorResultsCount" class="operator-results-count" aria-live="polite"></p><p id="operatorNotice" role="status" aria-live="polite"></p><p id="operatorBulkCompletionStatus" role="status" aria-live="polite" hidden></p><p id="operatorPullStatus" class="operator-pull-status" role="status" aria-live="polite" hidden></p><div id="operatorListScroll"><div id="operatorRows" role="listbox" aria-label="${esc(t('dialogs'))}"></div><button type="button" class="outline" id="operatorMore" hidden>${t('more')}</button></div></section><section class="operator-conversation-pane" id="operatorConversationPane" data-selected="false"><header id="operatorConversationHeader" class="operator-conversation-header"><div class="operator-conversation-empty">${t('chooseDialog')}</div></header><div id="operatorConversationLayout"><div id="operatorConversationContent" class="operator-conversation-content"><div class="operator-conversation-placeholder">${t('chooseDialog')}</div></div></div></section></div>`;
    const style=document.createElement('style');style.textContent=`.operator-panel{background:var(--card);color:var(--ink)}.operator-copilot-header{display:none}.operator-filters{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:7px;margin:0 0 7px}.operator-filters label{min-width:0;margin:0}.operator-advanced{grid-column:1/-1;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}.operator-tools-menu{position:relative;align-self:center}.operator-tools-menu summary{display:grid;place-items:center;width:36px;height:36px;border:1px solid var(--line);border-radius:8px;color:var(--ink);background:var(--card);font-size:22px;line-height:1;cursor:pointer;list-style:none}.operator-tools-menu summary::-webkit-details-marker{display:none}.operator-tools-menu[open] summary{border-color:var(--brand)}.operator-tools-menu>button{position:absolute;z-index:5;right:0;top:calc(100% + 4px);width:max-content;max-width:min(260px,80vw);background:var(--card);color:var(--ink);box-shadow:0 8px 24px #0002}.operator-tools-menu>button[hidden]{display:none!important}.operator-tools-menu[open]{z-index:8}.operator-tools-menu[open]>button:not([hidden]){position:relative;inset:auto;display:block;width:100%;max-width:min(260px,80vw);text-align:left}.operator-tools-menu[open] #operatorSoundStatus:not(:empty){position:relative;max-width:min(260px,80vw);padding:4px 8px;background:var(--card);border:1px solid var(--line);border-radius:6px;box-shadow:0 8px 24px #0002}.operator-filters input,.operator-filters select{min-width:0;width:100%;color:var(--ink);background:var(--card)}.operator-row{display:grid;grid-template-columns:minmax(160px,1.4fr) minmax(120px,1fr) minmax(160px,1fr) auto;align-items:center;gap:16px;padding:17px 0;border-bottom:1px solid var(--line)}.operator-row small{display:block;color:var(--muted);overflow-wrap:anywhere}.operator-state{display:inline-flex;border-radius:20px;background:var(--paper);padding:4px 9px;font-size:12px;font-weight:650}.operator-state[data-state=automatic]{color:var(--app-success-ink,#11865b);background:color-mix(in srgb,var(--app-success-ink,#11865b) 10%,var(--card))}.operator-state[data-state=manual],.operator-state.operator-row-company{color:var(--brand);background:color-mix(in srgb,var(--brand) 11%,var(--card))}.operator-state[data-state=needs_assignment],.operator-state[data-state=attention],.operator-state[data-state=human],.operator-state[data-state=paused]{color:var(--app-warning-ink,#8a5a00);background:var(--app-warning-bg,#fff2d9)}.operator-state[data-state=no_agent]{color:var(--muted);background:var(--paper)}.operator-state[data-state=blocked],.operator-state[data-state=error]{color:var(--danger,#b42318);background:color-mix(in srgb,var(--danger,#b42318) 9%,var(--card))}.operator-chat-meta{display:flex;flex-wrap:wrap;gap:14px;padding:14px 0}.operator-chat-meta>div{min-width:100px;overflow-wrap:anywhere}.operator-chat-meta small{display:block;color:var(--muted)}.operator-chat-actions{display:flex;gap:8px;flex-wrap:wrap}.operator-chat-notice{color:var(--app-warning-ink,#8a5a00);background:var(--app-warning-bg,#fff2d9);border-radius:10px;padding:11px;margin:12px 0}.operator-chat-history{max-height:45vh;overflow:auto;overscroll-behavior:contain;padding:8px 0}.operator-message{max-width:88%;margin:10px 0;padding:11px 13px;border:1px solid var(--line);border-radius:12px;background:var(--paper);white-space:pre-wrap;overflow-wrap:anywhere}.operator-message[data-direction=outgoing]{margin-left:auto;background:color-mix(in srgb,var(--brand) 10%,var(--card))}.operator-message[data-direction=system]{max-width:100%;text-align:center;background:transparent;border-style:dashed}.operator-message small{display:block;color:var(--muted);font-size:11px;margin-top:5px}.operator-composer{display:flex;flex-direction:column;gap:8px;margin:12px 0;padding:9px;border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink);min-width:0}.operator-composer textarea{display:block;width:100%;box-sizing:border-box;min-width:0;resize:none;overflow-y:hidden;min-height:54px;max-height:180px;background:transparent;color:var(--ink);padding:7px;border:0;outline:none}.operator-composer-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.operator-composer-actions button{min-height:36px;padding:7px 11px}.operator-composer-actions #sendChat{margin-left:auto}.operator-chat-actions button:disabled{opacity:.55;cursor:not-allowed}.operator-error{color:var(--danger,#b42318);overflow-wrap:anywhere}.operator-results-count{margin:2px 2px 6px;color:var(--muted);font-size:11px}.operator-pull-status{margin:2px;color:var(--muted);font-size:12px}.operator-pull-status[hidden]{display:none!important}.operator-results-count:empty{display:none}#dialogsAttentionCount,#supportUnresolvedCount{margin-left:auto;font-size:11px;flex:none}@media(max-width:850px){.operator-filters{grid-template-columns:1fr 1fr}.operator-search{grid-column:1/-1}.operator-row{grid-template-columns:1fr auto}.operator-row>div:nth-child(2){grid-column:1}.operator-row>button{grid-column:2;grid-row:1/4}.operator-chat-history{max-height:42dvh}}@media(max-width:440px){.operator-filters{grid-template-columns:1fr}.operator-search{grid-column:auto}.operator-composer{flex-direction:column;align-items:stretch}.operator-chat-actions button{flex:1;white-space:normal}.operator-row{gap:10px}.operator-message{max-width:96%}}`;
    style.textContent+=`.operator-filter-toggle{display:none}.operator-display-name{display:flex;flex-wrap:wrap;gap:8px;align-items:end;margin:12px 0}.operator-display-name label{flex:1;min-width:190px}.operator-display-name input{width:100%;box-sizing:border-box;color:var(--ink);background:var(--card)}@media(max-width:600px){.operator-filter-toggle{display:block}.operator-advanced{display:none}.operator-advanced[data-open=true]{display:grid;grid-template-columns:1fr;gap:8px;grid-column:1/-1}}`;
    style.textContent+=`@media(prefers-reduced-motion:no-preference){.operator-message-enter{animation:operator-message-appear 150ms ease-out both}}@keyframes operator-message-appear{from{opacity:.55;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}@media(prefers-reduced-motion:reduce){.operator-message-enter{animation:none!important}}`;
    document.head.appendChild(style);
    style.textContent+=`.operator-display-name{display:none}.operator-dialog-shell{display:flex;flex:1;min-height:0;flex-direction:column;padding:0 2px}.operator-dialog-head{display:flex;align-items:center;justify-content:space-between;gap:10px;min-width:0}.operator-dialog-head-main{display:flex;align-items:center;gap:8px;min-width:0;flex:1}.operator-dialog-title{display:flex;align-items:center;gap:4px;min-width:0;font-size:18px;font-weight:700}.operator-dialog-title-text{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.operator-channel-label{color:var(--muted);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.operator-icon-button{border:0;background:transparent;color:var(--muted);padding:4px 6px;line-height:1;font-size:16px;cursor:pointer}.operator-icon-button:hover,.operator-icon-button:focus-visible{color:var(--ink);background:var(--paper);border-radius:7px}.operator-title-edit,.operator-inline-name-edit{display:flex;align-items:center;gap:4px;min-width:0}.operator-title-edit input,.operator-inline-name-edit input{min-width:0;width:min(290px,55vw);color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:7px;padding:5px 7px}.operator-dialog-summary{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:8px 0;border-bottom:1px solid var(--line)}.operator-dialog-summary .operator-summary-item{font-size:12px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:180px}.operator-primary-action{display:flex;gap:8px;flex-wrap:wrap;margin-left:auto;max-width:100%}.operator-more-actions{position:relative}.operator-more-actions summary{list-style:none}.operator-more-actions summary::-webkit-details-marker{display:none}.operator-more-actions[open] .operator-action-menu{display:grid}.operator-action-menu{display:none;position:absolute;right:0;top:calc(100% + 5px);z-index:4;min-width:210px;padding:6px;background:var(--card);border:1px solid var(--line);border-radius:10px;box-shadow:0 9px 24px #0002}.operator-action-menu button{justify-content:flex-start;text-align:left;border:0;background:transparent;color:var(--ink);padding:8px;border-radius:6px}.operator-action-menu button:hover,.operator-action-menu button:focus-visible{background:var(--paper)}.operator-status-explanation{margin:8px 0;font-size:12px;color:var(--app-warning-ink,#8a5a00)}.operator-dialog-details{margin:7px 0;font-size:12px;color:var(--muted)}.operator-dialog-details summary{cursor:pointer}.operator-dialog-details dl{display:grid;grid-template-columns:auto minmax(0,1fr);gap:5px 10px;margin:8px 0}.operator-dialog-details dd{margin:0;overflow-wrap:anywhere}.operator-chat-history{flex:1;min-height:220px;max-height:none}.operator-message{padding:10px 12px}.operator-message-meta{position:relative;display:flex;align-items:center;gap:4px;color:var(--muted);font-size:11px;margin-top:6px;white-space:normal}.operator-message-meta time{white-space:nowrap}.operator-participant-name[data-participant-menu]{cursor:pointer}.operator-participant-name[data-participant-menu]:focus-visible{outline:1px solid var(--brand);outline-offset:2px;border-radius:3px}.operator-participant-menu{position:absolute;z-index:3;left:0;bottom:calc(100% + 5px);padding:4px;background:var(--card);border:1px solid var(--line);border-radius:8px;box-shadow:0 7px 18px #0002}.operator-participant-menu button{border:0;background:transparent;color:var(--ink);padding:6px 8px;white-space:nowrap}.operator-media-card{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:7px;padding:7px 8px;background:var(--card);border:1px solid var(--line);border-radius:9px;white-space:normal}.operator-media-card span:first-child{font-size:12px;color:var(--muted)}.operator-message-caption{margin-top:7px}.operator-composer{margin:10px 0 0}.operator-composer textarea{min-height:54px}.operator-audio-duration{font-size:11px;color:var(--muted)}@media(max-width:600px){.operator-dialog-title{font-size:16px}.operator-channel-label{max-width:105px}.operator-dialog-summary{gap:6px}.operator-dialog-summary .operator-summary-item{max-width:128px}.operator-primary-action{margin-left:0}.operator-action-menu{position:fixed;right:14px;max-width:calc(100vw - 28px)}}@media(max-width:440px){.operator-composer-actions{flex-wrap:wrap}.operator-composer-actions button{width:auto;max-width:100%}.operator-composer-actions #sendChat{margin-left:auto}.operator-title-edit input{width:48vw}.operator-dialog-summary .operator-summary-item{max-width:100px}}`;
    style.textContent+=`.operator-assignment-form{padding:12px;border:1px solid var(--line);border-radius:10px}.operator-assignment-form select{display:block;width:100%;max-width:100%;margin-top:6px}.operator-assignment-form p{font-size:12px;overflow-wrap:anywhere}.operator-assignment-form button{margin:4px 6px 0 0}@media(max-width:440px){.operator-assignment-form button{width:100%;margin-right:0}}`;
    style.textContent+=`.operator-media-card{align-items:center}.operator-media-card[data-media-kind=image]{display:grid;justify-items:start}.operator-media-card .operator-media-preview{display:block;max-width:min(190px,70vw);max-height:150px;object-fit:cover;border-radius:7px;cursor:zoom-in}.operator-media-card .operator-media-preview.is-expanded{max-width:100%;max-height:65vh;cursor:zoom-out}.operator-media-card .operator-media-audio{display:flex;align-items:center;gap:7px;width:min(290px,100%)}.operator-media-card .operator-media-audio button{flex:none;min-width:34px;height:34px;border-radius:50%;padding:0}.operator-media-card .operator-media-audio input{min-width:0;flex:1;accent-color:var(--brand)}.operator-media-card .operator-media-video{display:block;max-width:min(310px,100%);max-height:220px;border-radius:7px;background:#000}.operator-media-file-meta{font-size:11px;color:var(--muted)}@media(max-width:440px){.operator-media-card .operator-media-preview{max-width:100%;max-height:132px}.operator-media-card .operator-media-audio{width:100%}}`;
    style.textContent+=`#operatorDiagnosticsModal{position:fixed;inset:0;z-index:18000;display:grid;place-items:center;padding:clamp(12px,3vw,28px)}#operatorDiagnosticsModal[hidden]{display:none!important}.operator-diagnostics-backdrop{position:absolute;inset:0;background:#071d1688;backdrop-filter:blur(3px)}.operator-diagnostics-card{position:relative;z-index:1;width:min(520px,100%);max-height:min(90dvh,720px);display:flex;flex-direction:column;overflow:hidden;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:18px;box-shadow:0 24px 80px #0005}.operator-diagnostics-card>header{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;padding:18px 22px;border-bottom:1px solid var(--line)}.operator-diagnostics-card h2{margin:0;font-size:20px}.operator-diagnostics-card header p{margin:5px 0 0;color:var(--muted);overflow-wrap:anywhere}.operator-diagnostics-body{overflow:auto;padding:16px 22px}#operatorReplyDiagnostics:empty{display:none}.operator-diagnostic-row{display:grid;grid-template-columns:24px minmax(0,1fr);gap:10px;padding:11px 0}.operator-diagnostic-row strong{display:block}.operator-diagnostic-row p{margin:4px 0 0;color:var(--muted);overflow-wrap:anywhere}.operator-diagnostic-icon{display:grid;place-items:center;width:20px;height:20px;border:1px solid var(--line);border-radius:50%;font-weight:800;font-size:12px}.operator-diagnostic-row[data-state=ok] .operator-diagnostic-icon{color:#11733b;border-color:#19a65a}.operator-diagnostic-row[data-state=blocked] .operator-diagnostic-icon{color:#a4231a;border-color:#df5b48}.operator-diagnostic-actions{margin:14px 0 0;padding:16px 0;border-top:1px solid var(--line)}.operator-diagnostic-actions h3{margin:0 0 8px;font-size:15px}.operator-diagnostic-actions p{margin:6px 0}.operator-diagnostic-actions p[data-primary=true]{font-weight:700}.operator-diagnostic-technical{margin-top:14px;padding-top:10px;border-top:1px solid var(--line)}.operator-diagnostic-technical summary{cursor:pointer;min-height:44px;padding:10px 0}.operator-diagnostic-technical-row{padding:10px 0;border-top:1px solid var(--line)}.operator-diagnostic-technical-row h4{margin:0 0 6px;font-size:13px}.operator-diagnostic-technical-row p{margin:0;color:var(--muted);overflow-wrap:anywhere}.operator-diagnostic-technical-row dl{display:grid;grid-template-columns:minmax(100px,auto) 1fr;gap:5px 10px;margin:0;font-size:12px}.operator-diagnostic-technical-row dd{margin:0;overflow-wrap:anywhere}@media(max-width:600px){.operator-diagnostics-card{max-height:94dvh}.operator-diagnostics-card>header,.operator-diagnostics-body{padding:15px 16px}.operator-diagnostics-card h2{font-size:18px}}@media(max-width:390px){#operatorDiagnosticsModal{padding:0}.operator-diagnostics-card{width:100%;height:100dvh;max-height:none;border-radius:0}.operator-diagnostic-technical-row dl{grid-template-columns:1fr}}`;
    style.textContent+=`#operatorGroupSettingsModal{position:fixed;inset:0;z-index:18000;display:grid;place-items:center;padding:clamp(12px,3vw,28px)}#operatorGroupSettingsModal[hidden]{display:none!important}.operator-group-settings-backdrop{position:absolute;inset:0;background:#071d1688;backdrop-filter:blur(3px)}.operator-group-settings-card{position:relative;z-index:1;width:min(760px,100%);max-height:min(90dvh,920px);display:flex;flex-direction:column;overflow:hidden;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:18px;box-shadow:0 24px 80px #0005}.operator-group-settings-card>header{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;padding:16px 18px;border-bottom:1px solid var(--line)}.operator-group-settings-card h2{margin:0;font-size:18px}.operator-group-settings-card header p{margin:5px 0 0;color:var(--muted);overflow-wrap:anywhere}.operator-group-settings-body{overflow:auto;padding:14px 18px}.operator-group-agent{padding:12px 0;border-bottom:1px solid var(--line)}.operator-group-agent:last-child{border-bottom:0}.operator-group-agent h3{margin:0;font-size:16px}.operator-group-agent p{margin:5px 0 0;color:var(--muted);overflow-wrap:anywhere}.operator-group-settings-form{margin-top:12px;padding-top:12px;border-top:1px solid var(--line)}.operator-group-settings-form label{display:block;margin:8px 0}.operator-group-settings-form select,.operator-group-settings-form input{display:block;width:100%;box-sizing:border-box;margin-top:5px;color:var(--ink);background:var(--card)}.operator-group-settings-form input{padding:8px;border:1px solid var(--line);border-radius:8px}.operator-group-settings-form select{min-height:40px}.operator-group-settings-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.operator-results-count{margin:2px 2px 6px;color:var(--muted);font-size:11px}.operator-pull-status{margin:2px;color:var(--muted);font-size:12px}.operator-pull-status[hidden]{display:none!important}.operator-results-count:empty{display:none}@media(max-width:600px){.operator-group-settings-card{max-height:94dvh}.operator-group-settings-card>header,.operator-group-settings-body{padding:15px 16px}.operator-group-settings-card h2{font-size:18px}}@media(max-width:390px){#operatorGroupSettingsModal{padding:0}.operator-group-settings-card{width:100%;height:100dvh;max-height:none;border-radius:0}}`;
    style.textContent+=`.operator-copilot{border:1px solid var(--line);border-radius:10px;padding:10px;margin-top:8px;background:var(--paper);color:var(--ink)}.operator-copilot[hidden]{display:none}.operator-copilot-actions{display:flex;flex-wrap:wrap;gap:6px}.operator-copilot textarea{box-sizing:border-box;width:100%;min-height:56px;resize:vertical;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px}.operator-copilot-result{white-space:pre-wrap;overflow-wrap:anywhere;max-height:220px;overflow:auto;padding:8px;background:var(--card);border-radius:8px}.operator-copilot-result[hidden]{display:none}.operator-complete-modal{position:fixed;inset:0;z-index:22000;display:grid;place-items:center;padding:16px;background:#071d1688}.operator-complete-card{width:min(520px,100%);max-height:90dvh;overflow:auto;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 24px 80px #0005}.operator-complete-card h2{margin:0 0 12px;font-size:19px}.operator-complete-card label{display:block;margin:10px 0;color:var(--ink)}.operator-complete-card select,.operator-complete-card textarea{box-sizing:border-box;width:100%;margin-top:5px;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:8px;padding:9px}.operator-complete-card textarea{min-height:76px}.operator-complete-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}`;
    style.textContent+=`.operator-dialog-head{position:relative;display:flex;flex-direction:column;align-items:stretch;justify-content:center;gap:4px;min-height:0;padding:7px 14px!important;color:var(--ink);background:var(--card);border-bottom:1px solid var(--line)}.operator-dialog-head-row,.operator-dialog-head-secondary{display:flex;align-items:center;gap:8px;min-width:0;min-height:30px}.operator-dialog-head-main{display:flex;align-items:center;gap:8px;min-width:0;flex:1}.operator-dialog-title{font-size:16px;min-width:0}.operator-dialog-title-text,.operator-channel-label,.operator-agent-label,.operator-assignee-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.operator-channel-label{max-width:220px}.operator-agent-label,.operator-assignee-label{max-width:180px}.operator-primary-action{display:flex;align-items:center;gap:6px;flex-wrap:nowrap;margin-left:auto;min-width:0}.operator-primary-action button{min-height:30px;padding:5px 9px;font-size:12px;white-space:nowrap}.operator-more-actions{flex:none}.operator-status-explanation{position:absolute;z-index:6;top:calc(100% - 2px);right:44px;max-width:min(420px,calc(100vw - 56px));margin:0;padding:9px 12px;background:var(--app-warning-bg,var(--card));color:var(--app-warning-ink,#8a5a00);border:1px solid var(--line);border-radius:8px;box-shadow:0 8px 24px #0002;overflow-wrap:anywhere}.operator-status-explanation[hidden]{display:none}.operator-chat-history{flex:1;min-height:0;max-height:none;overflow:auto;overscroll-behavior:contain;padding:8px 10px}.operator-composer{flex:none;margin:8px 8px 10px;padding:8px 10px;gap:4px;background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:0 2px 8px #0000000a}.operator-composer textarea{min-height:50px;max-height:160px;padding:7px 8px}.operator-composer-actions{display:flex;align-items:center;gap:4px;flex-wrap:nowrap}.operator-composer-actions button{min-height:34px;padding:6px 9px;border:0!important;background:transparent;color:var(--muted);box-shadow:none!important;font-size:12px}.operator-composer-actions button:hover,.operator-composer-actions button:focus-visible{background:var(--paper);color:var(--ink)}.operator-composer-actions #sendChat{display:inline-flex;align-items:center;justify-content:center;margin-left:auto;width:36px;height:36px;min-width:36px;padding:0;border-radius:50%;background:var(--brand);color:var(--on-brand,#fff);font-size:20px;font-weight:700;line-height:1}.operator-composer-actions #sendChat:disabled{opacity:.5}.operator-error:empty,#operatorAIReplyStatus:empty,#operatorSendReason:empty{display:none}#operatorEarlier,#operatorLatestIncoming{flex:none;align-self:flex-start;margin:3px 10px;padding:3px 6px;border:0;background:transparent;color:var(--brand);font-size:12px}@media(max-width:600px){.operator-dialog-head{padding:6px 10px!important}.operator-dialog-head-row,.operator-dialog-head-secondary{gap:5px}.operator-dialog-head-secondary{flex-wrap:wrap}.operator-agent-label,.operator-assignee-label{max-width:110px}.operator-primary-action{margin-left:auto;flex-wrap:wrap}.operator-composer-actions{flex-wrap:wrap}}@media(max-width:440px){.operator-dialog-title{max-width:48vw}.operator-channel-label{max-width:70px}.operator-primary-action button{font-size:11px;padding:4px 6px}.operator-composer{margin:6px;padding:6px 8px}.operator-composer-actions button{font-size:11px;padding:5px 6px}}`;document.head.appendChild(style);
    const workspaceStyle=document.createElement('style');workspaceStyle.textContent=`#operatorListScroll{display:contents}#view-dialogs{height:calc(100dvh - 104px);min-height:440px;overflow:hidden}#operatorDialogs{height:100%;min-height:0;display:flex;flex-direction:column}.operator-page-top{flex:none;align-items:center;margin:0 0 10px;min-height:42px}.operator-page-top h1{font-size:21px}.operator-page-top p{font-size:12px}.operator-page-top>div:last-child{display:flex;align-items:center;gap:8px}.operator-page-top button{padding:7px 10px;font-size:12px}.operator-workspace{display:grid;grid-template-columns:minmax(270px,25fr) minmax(0,75fr);gap:8px;flex:1;min-height:0;overflow:hidden}.operator-list-pane,.operator-conversation-pane{min-width:0;min-height:0;overflow:hidden;border:1px solid var(--line);border-radius:13px;background:var(--card);color:var(--ink)}.operator-list-pane{display:flex;flex-direction:column;padding:10px}.operator-filters{grid-template-columns:minmax(0,1fr) auto;gap:7px;margin:0 0 7px}.operator-search{grid-column:1}.operator-filters input,.operator-filters select{height:38px;padding:7px 10px;border:1px solid var(--line);border-radius:8px}.operator-filter-toggle{height:36px;padding:6px 10px}.operator-advanced{grid-column:1/-1;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}.operator-advanced label{display:grid;gap:3px;color:var(--muted);font-size:11px}.operator-advanced select{font-size:12px}.operator-bulk-preview{max-height:160px;overflow:auto;flex:none}.operator-bulk-preview[hidden]{display:none!important}.operator-list-pane>#operatorNotice{flex:none;margin:0;color:var(--danger);font-size:12px}.operator-list-pane>#operatorNotice:empty{display:none}.operator-list-pane #operatorRows{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;scrollbar-gutter:auto}.operator-row{position:relative;display:grid;grid-template-columns:34px minmax(0,1fr);align-items:center;gap:9px;width:100%;padding:9px 7px;margin:0;border:0;border-bottom:1px solid var(--line);border-radius:0;background:transparent;color:var(--ink);text-align:left;cursor:pointer}.operator-row:hover{background:var(--paper)}.operator-row[aria-selected=true]{background:color-mix(in srgb,var(--brand) 10%,var(--card));box-shadow:inset 3px 0 var(--brand)}.operator-row-avatar{display:grid;place-items:center;width:32px;height:32px;border-radius:50%;background:color-mix(in srgb,var(--brand) 12%,var(--card));color:var(--brand);font-weight:700}.operator-row-body{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 9px;min-width:0}.operator-row-copy{display:grid;align-content:start;gap:2px;min-width:0}.operator-row-body>.operator-row-preview{grid-column:1/-1}.operator-row-title,.operator-row-sub,.operator-row-preview{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.operator-row-title{font-size:13px;font-weight:650}.operator-row-sub,.operator-row-preview,.operator-row-time{font-size:11px;color:var(--muted)}.operator-row-meta{display:flex;flex-direction:column;align-items:flex-end;align-self:start;gap:4px;min-width:0;max-width:100%}.operator-row-indicators{display:flex;align-items:center;justify-content:flex-end;gap:5px;flex-wrap:wrap}.operator-row-time{align-self:end;white-space:normal;text-align:right;overflow-wrap:anywhere;max-width:110px}.operator-row-status{grid-column:1/-1;justify-self:start;max-width:100%;font-size:10px;padding:2px 7px}.operator-list-pane #operatorMore{flex:none;align-self:stretch;margin-top:7px}.operator-conversation-pane{display:flex;flex-direction:column;position:relative}.operator-conversation-header{display:flex;align-items:center;justify-content:space-between;gap:8px;min-height:60px;flex:none;padding:7px 12px;border-bottom:1px solid var(--line);background:var(--card);color:var(--ink)}.operator-conversation-header .operator-dialog-head-row{flex:1;min-width:0}.operator-conversation-header .operator-dialog-head-secondary{flex:none}.operator-conversation-header .operator-dialog-title{max-width:min(34vw,390px)}.operator-conversation-header .operator-dialog-title-text{max-width:100%}.operator-conversation-header .operator-channel-label{max-width:160px}.operator-conversation-header .operator-state{font-size:10px;padding:3px 7px;white-space:nowrap}.operator-conversation-header .operator-primary-action{margin-left:0}.operator-conversation-header .operator-action-menu{max-height:min(65vh,600px);overflow:auto}.operator-conversation-header .operator-action-menu #operatorPrimaryAction{display:grid;gap:2px;padding-bottom:4px;margin-bottom:4px;border-bottom:1px solid var(--line)}.operator-conversation-header .operator-primary-action button,.operator-conversation-header .operator-action-menu button{width:100%;min-height:34px;padding:7px 9px;color:var(--ink);background:transparent;text-align:left;justify-content:flex-start;border:0}.operator-conversation-header .operator-action-menu .operator-assignment-form{display:block}.operator-conversation-header .operator-status-explanation{position:absolute;right:38px;top:calc(100% + 3px);max-width:min(390px,calc(100% - 24px))}.operator-back-to-list{display:none;flex:none;padding:5px 7px;background:transparent;color:var(--brand);font-size:18px}.operator-conversation-content{display:flex;flex:1;min-height:0;overflow:hidden}.operator-conversation-placeholder{display:grid;place-items:center;width:100%;color:var(--muted);padding:24px;text-align:center}.operator-dialog-shell{display:flex;flex:1;min-height:0;min-width:0;padding:0}.operator-dialog-body{display:flex;flex:1;min-height:0;min-width:0;overflow:hidden}.operator-accept-action{flex:none;display:flex;justify-content:center;padding:6px 10px;border-bottom:1px solid var(--line);background:var(--card)}.operator-accept-action:empty{display:none}.operator-accept-action button{width:100%;min-height:36px;padding:7px 12px}.operator-chat-main{display:flex;flex:1;min-width:0;min-height:0;flex-direction:column}.operator-chat-scrollarea{display:flex;flex:1;min-height:0;flex-direction:column;overflow:hidden}.operator-chat-history{width:100%;min-width:0}.operator-chat-main>#operatorAIReplyStatus,.operator-chat-main>#operatorSendReason{flex:none;padding:0 10px;margin:0}.operator-ai-context{flex:none;margin:0;padding:0 10px 5px;color:var(--muted);font-size:11px;overflow-wrap:anywhere}.operator-ai-context[hidden]{display:none}.operator-ai-context details{display:inline-block;margin-left:7px}.operator-ai-context summary{cursor:pointer;color:var(--brand)}.operator-ai-context ul{margin:5px 0;padding-left:20px}.operator-copilot{width:min(310px,40%);flex:none;min-width:0;height:100%;overflow:auto;margin:0;border:0;border-left:1px solid var(--line);border-radius:0;padding:10px;background:var(--paper)}.operator-copilot[hidden]{display:none!important}.operator-copilot-result{max-height:35vh}.operator-message{max-width:min(88%,720px)}.operator-conversation-pane:has(.operator-dialog-shell) .operator-conversation-empty{display:none}@media(max-width:1050px){.operator-workspace{grid-template-columns:minmax(270px,25fr) minmax(0,75fr);gap:8px}.operator-list-pane{padding:8px}.operator-conversation-header{padding:6px 8px}.operator-conversation-header .operator-agent-label,.operator-conversation-header .operator-assignee-label{max-width:110px}.operator-copilot{width:270px}}@media(max-width:900px){#view-dialogs.active{height:calc(100dvh - 168px);min-height:0}.operator-page-top{margin-bottom:6px}.operator-page-top p{display:none}.operator-workspace{grid-template-columns:minmax(0,1fr);gap:0}.operator-list-pane,.operator-conversation-pane{grid-column:1;grid-row:1;border-radius:11px}.operator-workspace[data-selected=true] .operator-list-pane{display:none}.operator-workspace:not([data-selected=true]) .operator-conversation-pane{display:none}.operator-conversation-pane{height:100%}.operator-back-to-list{display:inline-flex}.operator-conversation-header{min-height:54px}.operator-copilot{width:min(280px,40%)}.operator-row{padding:10px 8px}}@media(max-width:520px){.operator-advanced{grid-template-columns:1fr}.operator-filters{grid-template-columns:minmax(0,1fr) auto}.operator-conversation-header{min-height:58px;padding:5px}.operator-conversation-header .operator-dialog-head-row{flex-wrap:wrap}.operator-conversation-header .operator-dialog-head-main{gap:4px}.operator-conversation-header .operator-dialog-title{max-width:46vw;font-size:14px}.operator-conversation-header .operator-channel-label{max-width:70px;font-size:10px}.operator-conversation-header .operator-dialog-head-secondary{min-width:0}.operator-conversation-header .operator-agent-label,.operator-conversation-header .operator-assignee-label{max-width:76px;font-size:10px}.operator-conversation-header .operator-action-menu{position:fixed;right:10px;top:62px;max-width:calc(100vw - 20px)}.operator-dialog-body{position:relative}.operator-dialog-body:has(.operator-copilot:not([hidden])){flex-direction:column}.operator-dialog-body:has(.operator-copilot:not([hidden])) .operator-copilot{width:100%;height:auto;max-height:38%;border-left:0;border-top:1px solid var(--line)}.operator-composer{margin:6px;padding:6px}.operator-composer-actions{gap:2px}.operator-composer-actions button{padding:5px 6px;font-size:11px}.operator-message{max-width:96%}}`;
    workspaceStyle.textContent+=`@media(max-width:520px){.operator-conversation-head-copy{flex-direction:column;align-items:stretch;gap:2px}.operator-conversation-header .operator-dialog-head-main{flex:1 1 150px;min-width:150px;flex-wrap:wrap}.operator-conversation-header .operator-dialog-title{flex:0 1 auto;max-width:46vw}.operator-conversation-header .operator-client-phone{font-size:10px}}`;workspaceStyle.textContent+=`@media(max-width:600px){.operator-advanced{display:none}.operator-advanced[data-open=true]{display:grid;grid-template-columns:1fr}}.operator-list-pane [hidden]{display:none!important}.operator-conversation-content>[data-operator-dialog]{width:100%}.operator-row:focus-visible{outline:2px solid var(--brand);outline-offset:-2px}.operator-row-preview:empty{display:none}.operator-search-label{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}.operator-conversation-head-copy{display:flex;flex:1;min-width:0;align-items:center;gap:10px}.operator-conversation-header .operator-dialog-head{display:flex;flex:1;min-width:0;flex-direction:column;gap:2px}.operator-conversation-header .operator-dialog-head-row{width:100%;min-height:25px;gap:7px}.operator-conversation-header .operator-dialog-head-main{gap:6px}.operator-conversation-header .operator-client-phone{flex:none;color:var(--muted);font-size:12px;white-space:nowrap}.operator-conversation-header .operator-dialog-head-secondary{min-width:0;gap:7px}.operator-conversation-header .operator-more-actions{flex:none}.operator-conversation-header .operator-more-actions summary{display:grid;place-items:center;width:30px;height:30px;padding:0;border-radius:7px;font-size:20px}.operator-conversation-header .operator-action-menu{display:none;position:absolute;right:0;top:calc(100% + 4px);z-index:10;min-width:220px;padding:7px;border:1px solid var(--line);border-radius:10px;background:var(--card);box-shadow:0 8px 24px #0003}.operator-conversation-header .operator-more-actions[open] .operator-action-menu{display:grid}.operator-conversation-header .operator-action-menu #operatorMenuExtra{display:grid}.operator-conversation-header .operator-primary-action{display:grid;min-width:0}.operator-conversation-header .operator-primary-action button{white-space:normal}.operator-conversation-header .operator-assignment-form{padding:8px}.operator-dialog-body #operatorChatError:not(:empty){flex:none;padding:5px 10px}.operator-chat-main{overflow:hidden}.operator-chat-scrollarea>#operatorEarlier{flex:none}.operator-chat-history{overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable}`;
    workspaceStyle.textContent+=`.operator-lifecycle-badge{align-self:center;flex:none;display:inline-flex;align-items:center;white-space:nowrap;border-radius:999px;padding:3px 8px;font-size:11px;font-weight:600;line-height:1.25;background:var(--panel);color:var(--text);border:1px solid var(--border)}.operator-lifecycle-badge[data-lifecycle-state="waiting"]{background:#be123c;color:#fff;border-color:transparent}.operator-lifecycle-badge[data-lifecycle-state="accepted"]{background:#1d4ed8;color:#fff;border-color:transparent}.operator-lifecycle-badge[data-lifecycle-state="closed"]{color:var(--muted)}.operator-unread-count{align-self:center;flex:none;min-width:22px;height:22px;padding:0 6px;display:inline-flex;align-items:center;justify-content:center;border-radius:999px;background:var(--brand);color:var(--brand-contrast,#fff);font-size:11px;font-weight:700;line-height:1;font-variant-numeric:tabular-nums}.operator-tools-menu #operatorSoundStatus{display:block;max-width:240px;padding:4px 8px;color:var(--muted);font-size:12px}.operator-tools-menu[open] #operatorSoundStatus:empty{display:none}`;
    document.head.appendChild(workspaceStyle);
    const rowStatusStyle=document.createElement('style');rowStatusStyle.textContent='#operatorRows .operator-row :is(.operator-row-status,.operator-row-company){font-weight:400}.operator-row-company,.operator-row-source{display:block;min-width:0;width:fit-content;max-width:100%;box-sizing:border-box;padding:2px 7px;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.operator-row-context{grid-column:1/-1;display:flex;align-items:center;gap:8px;min-width:0}.operator-row-source{margin-inline-start:auto;padding-inline-end:0;text-align:end;color:var(--brand);font-weight:400}.operator-row-sub:empty,.operator-row-indicators:empty{display:none}.operator-row-context-with-preview .operator-row-preview{flex:1 1 0;min-width:0}.operator-row-context-with-preview .operator-row-source{max-width:55%}';document.head.append(rowStatusStyle);
    const mobileStyle=document.createElement('style');mobileStyle.textContent=`@media(max-width:900px){#view-dialogs.active{height:calc(var(--operator-visual-height,100dvh) - 104px);min-height:0;padding-bottom:env(safe-area-inset-bottom)}#operatorDialogs[data-conversation-open=true]>.operator-page-top{display:none}.operator-list-pane,.operator-conversation-pane,.operator-conversation-content,.operator-dialog-body,.operator-chat-main{min-height:0}.operator-list-pane #operatorRows,.operator-chat-history{-webkit-overflow-scrolling:touch;overscroll-behavior:contain}.operator-chat-scrollarea{flex:1 1 0;min-height:0}.operator-chat-history{flex:1 1 0;min-height:0;max-height:none;overflow-y:auto}.operator-chat-scrollarea>#operatorEarlier,.operator-chat-scrollarea>#operatorNewMessages{flex:none}.operator-row{min-height:66px}.operator-filters input,.operator-filters select,.operator-composer textarea,.operator-copilot textarea{font-size:16px}.operator-composer{margin-bottom:max(6px,env(safe-area-inset-bottom));}.operator-dialog-body{position:relative}.operator-copilot-backdrop{display:none}.operator-dialog-body[data-copilot-open=true] .operator-copilot-backdrop{display:block;position:absolute;inset:0;z-index:20;border:0;padding:0;background:#071d1688}.operator-copilot-backdrop[hidden]{display:none!important}.operator-dialog-body .operator-copilot{position:absolute;left:8px;right:8px;bottom:calc(var(--operator-composer-height,76px) + env(safe-area-inset-bottom) + 10px);z-index:21;width:auto;height:auto;max-height:min(68%,520px);overflow:auto;margin:0;border:1px solid var(--line);border-radius:16px;background:var(--card);box-shadow:0 16px 42px #0004;padding:12px}.operator-copilot-header{display:flex;align-items:center;justify-content:space-between;gap:8px;min-height:36px;padding-bottom:8px;border-bottom:1px solid var(--line);margin-bottom:8px}.operator-copilot-header strong{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.operator-copilot-header button{flex:none;min-width:36px;min-height:36px;font-size:20px}.operator-copilot textarea{width:100%;max-width:100%;min-height:76px;max-height:20dvh;box-sizing:border-box}.operator-copilot-result{max-height:22dvh;overflow:auto}.operator-composer textarea{scroll-margin-bottom:8px}#operatorCopilotBackdrop{touch-action:manipulation}}@media(max-width:440px){.operator-dialog-body .operator-copilot{left:6px;right:6px;padding:10px}.operator-composer-actions button{min-height:40px;touch-action:manipulation}.operator-row{padding:11px 8px}}@media(orientation:landscape) and (max-width:900px){#view-dialogs.active{height:calc(var(--operator-visual-height,100dvh) - 104px)}.operator-dialog-body .operator-copilot{max-height:min(62%,340px)}.operator-composer{margin:3px 4px 4px;padding:4px 6px}#chatText{min-height:34px!important;max-height:64px}.operator-composer-actions button{min-height:32px;padding:4px 7px}#chatSendReview{max-height:48px;overflow:auto;flex:none;margin:4px 4px 0!important;padding:4px 6px!important}#chatSendReview>button{min-height:32px;padding:4px 8px;font-size:11px}}`;
    mobileStyle.textContent+=` .operator-menu-agent{display:none}.operator-composer #chatText,.operator-composer #chatText:focus,.operator-composer #chatText:focus-visible{border:0!important;box-shadow:none!important;outline:none!important}.operator-composer:focus-within{border-color:var(--line)!important;box-shadow:none!important}.operator-composer:has(#chatText:focus-visible){outline:none!important;outline-offset:0!important}.operator-composer #chatText{scrollbar-width:none}.operator-composer #chatText::-webkit-scrollbar{width:0;height:0}.operator-composer #chatText:focus{scrollbar-width:thin}.operator-composer #chatText:focus::-webkit-scrollbar{width:6px;height:6px}@media(max-width:900px){body[data-app-ui=kwid]:has(#view-dialogs.active) main{padding-top:64px!important}#view-dialogs.active{height:calc(var(--operator-visual-height,100dvh) - 64px)}#view-dialogs.active .operator-list-pane{border-radius:0;padding:4px 6px}.operator-filters input,.operator-filter-toggle,.operator-tools-menu summary{min-height:44px}.operator-filter-toggle,.operator-tools-menu summary{min-width:44px}.operator-conversation-header{position:relative;padding-right:50px}.operator-conversation-header .operator-dialog-head-secondary{position:absolute;right:4px;top:4px}.operator-conversation-header .operator-dialog-head-secondary>.operator-agent-label{display:none}.operator-conversation-header .operator-dialog-head-secondary>.operator-assignee-label{display:none}.operator-conversation-header .operator-more-actions summary{width:44px;height:44px}.operator-conversation-header .operator-action-menu{position:absolute;top:calc(100% + 4px);right:0;max-width:calc(100vw - 16px)}.operator-menu-agent{display:grid;gap:3px;padding:8px 9px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.operator-menu-agent small{color:var(--muted)}}`;
    mobileStyle.textContent+=`.operator-status-compact{display:none}@media(max-width:900px){.operator-conversation-header .operator-dialog-head-row{display:block}.operator-conversation-header .operator-dialog-head-main{display:grid;grid-template-columns:auto minmax(0,1fr);gap:0 5px;min-width:0}.operator-conversation-header .operator-dialog-title{grid-column:1/-1;max-width:calc(100% - 100px);min-height:34px}.operator-conversation-header .operator-client-phone{min-width:0;overflow:hidden;text-overflow:ellipsis}.operator-conversation-header .operator-channel-label{min-width:0}.operator-conversation-header .operator-agent-label{max-width:90px;font-size:10px}.operator-conversation-header #operatorCreateTicketHost button{min-height:30px;padding:4px 7px;font-size:11px}.operator-conversation-header .operator-dialog-head-secondary{min-height:0}}`;
    mobileStyle.textContent+=`@media(max-width:900px){body:has(#view-dialogs.active #operatorDialogs[data-conversation-open=true]) .app-topbar{display:none}#view-dialogs.active:has(#operatorDialogs[data-conversation-open=true]){position:fixed;z-index:36;top:var(--operator-visual-top,0px);left:0;width:100%;height:var(--operator-visual-height,100dvh);min-height:0;max-height:none;box-sizing:border-box;margin:0;padding:env(safe-area-inset-top) 0 0;overflow:hidden;background:var(--card)}#view-dialogs.active:has(#operatorDialogs[data-conversation-open=true]) .operator-workspace{width:100%;max-width:none;grid-template-columns:minmax(0,1fr);gap:0}#view-dialogs.active:has(#operatorDialogs[data-conversation-open=true]) .operator-conversation-pane{width:100%;max-width:none;min-width:0;border:0;border-radius:0;box-sizing:border-box}#view-dialogs.active:has(#operatorDialogs[data-conversation-open=true]) .operator-workspace[data-selected=true] .operator-conversation-pane{animation:operator-chat-enter 170ms ease-out both}#view-dialogs.active:has(#operatorDialogs[data-conversation-open=true]) .operator-composer{margin-bottom:max(6px,env(safe-area-inset-bottom))}}@keyframes operator-chat-enter{from{opacity:0;transform:translateX(12px)}to{opacity:1;transform:translateX(0)}}@media(prefers-reduced-motion:reduce){#view-dialogs.active .operator-workspace[data-selected=true] .operator-conversation-pane{animation:none!important}}@media(max-width:900px){body[data-app-ui=kwid] .operator-composer #chatText{background:transparent!important;border:0!important;box-shadow:none!important;outline:none!important;min-height:38px;padding:5px 7px!important}body[data-app-ui=kwid] .operator-composer-actions button{min-height:44px!important}body[data-app-ui=kwid] .operator-composer-actions #sendChat{width:44px;height:44px;min-width:44px}}@media(max-width:600px){.operator-filters{grid-template-columns:minmax(0,1fr) auto auto;align-items:center}.operator-search{grid-column:1}.operator-filter-toggle{display:grid;place-items:center;width:36px;min-width:36px;height:36px;padding:0}.operator-advanced{grid-column:1/-1}}.operator-dialog-loading{display:flex;flex-direction:column;justify-content:flex-end;gap:12px;padding:18px;box-sizing:border-box}.operator-dialog-skeleton-line{display:block;width:min(78%,420px);height:42px;border-radius:12px;background:color-mix(in srgb,var(--muted) 12%,var(--card))}.operator-dialog-skeleton-line:nth-child(even){align-self:flex-end;width:min(64%,350px)}.operator-dialog-skeleton-label{padding:6px;color:var(--muted);font-size:12px}`;
    mobileStyle.textContent+=`.operator-create-ticket-compact{display:inline-flex;align-items:center;justify-content:center;gap:4px;min-height:32px;padding:4px 8px;white-space:nowrap}.operator-create-ticket-compact>span:first-child{font-size:18px;font-weight:700;line-height:1}`;
    mobileStyle.textContent+=`@media(max-width:900px){body:has(#view-dialogs.active #operatorDialogs:not([data-conversation-open=true])) main{padding-left:0!important;padding-right:0!important}body:has(#view-dialogs.active #operatorDialogs:not([data-conversation-open=true])) .operator-list-pane{padding-left:max(6px,env(safe-area-inset-left));padding-right:max(6px,env(safe-area-inset-right))}}`;
    // Keep the mobile conversation header to one toolbar row; details stay in the existing menu/card.
    mobileStyle.textContent+=`@media(max-width:900px){#operatorConversationHeader{padding:4px;gap:2px;min-height:56px}#operatorConversationHeader .operator-back-to-list{width:32px;min-width:32px;min-height:44px;justify-content:center;padding:0}#operatorConversationHeader .operator-conversation-head-copy{flex-direction:row;align-items:center;gap:4px}#operatorConversationHeader .operator-dialog-head-row{width:auto;flex:1;min-width:0}#operatorConversationHeader .operator-dialog-head-main{min-width:0;gap:0 4px}#operatorConversationHeader .operator-dialog-title{max-width:100%;min-width:0;min-height:0;font-size:14px;line-height:20px}#operatorConversationHeader .operator-dialog-title-text{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}#operatorConversationHeader #operatorClientCardOpen{width:44px;min-width:44px;height:44px;min-height:44px;margin:0 4px 0 0}#operatorConversationHeader #operatorClientCardOpen .operator-header-avatar{width:30px;height:30px}#operatorConversationHeader .operator-client-phone,#operatorConversationHeader .operator-channel-label{font-size:10px;line-height:12px;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}#operatorConversationHeader .operator-dialog-head-secondary{position:static;display:flex;flex:none;align-items:center;gap:2px;min-height:44px}#operatorConversationHeader #operatorCreateTicketHost button{width:auto;min-width:44px;height:44px;min-height:44px;padding:0 5px}#operatorConversationHeader .operator-more-actions{position:relative}#operatorConversationHeader .operator-action-menu{max-width:calc(100vw - 16px)}}`;
    document.head.appendChild(mobileStyle);
    const compactCompanyCloseStyle=document.createElement('style');compactCompanyCloseStyle.textContent=`
    `;document.head.append(compactCompanyCloseStyle);
    const missedCallPreviewStyle=document.createElement('style');missedCallPreviewStyle.textContent='.operator-row-preview-missed-pending{color:var(--danger,#b42318)!important;font-weight:600}';document.head.append(missedCallPreviewStyle);
    bindFilterPopover();
    try{listScrollTop=Number(sessionStorage.getItem(`tenant.operator.list-scroll.${member?.tenant_id||''}.${member?.telegram_id||''}`))||0;}catch{listScrollTop=0;}
    [$('operatorRows'),$('operatorListScroll')].forEach(node=>node.addEventListener('scroll',()=>{listScrollTop=operatorListScroller().scrollTop;try{sessionStorage.setItem(`tenant.operator.list-scroll.${member?.tenant_id||''}.${member?.telegram_id||''}`,String(listScrollTop));}catch{}}));
    $('operatorConversationPane').dataset.selected='false';
    $('operatorConversationHeader').addEventListener('click',event=>{if(event.target.closest('[data-operator-back]'))closeDialog();});
    $('operatorMore').onclick=()=>refresh(true);
    const rowsNode=$('operatorRows');rowsNode.addEventListener('touchstart',event=>{pullStartY=operatorListScroller().scrollTop===0?(event.touches[0]?.clientY??null):null;},{passive:true});rowsNode.addEventListener('touchend',async event=>{const startY=pullStartY,endY=event.changedTouches[0]?.clientY??0;pullStartY=null;if(startY===null||endY-startY<72||operatorListScroller().scrollTop!==0||pullRefreshBusy||autoRefreshBusy)return;pullRefreshBusy=true;rowsNode.dataset.refreshing='true';rowsNode.setAttribute('aria-busy','true');const status=$('operatorPullStatus');if(status){status.textContent=pullRefreshLabel();status.hidden=false;}try{await refresh();}finally{pullRefreshBusy=false;delete rowsNode.dataset.refreshing;rowsNode.removeAttribute('aria-busy');if(status)status.hidden=true;}},{passive:true});
    bindToolsPopover($('operatorToolsMenu'));
    $('operatorFindDuplicates').onclick=openDuplicateSearch;
    syncDuplicateButton();
    const dupeStyle=document.createElement('style');
    dupeStyle.textContent=`#operatorDuplicateSearch [data-dupe-display]{display:grid;gap:6px;margin:8px 0}#operatorDuplicateSearch [data-dupe-display]>label{display:grid;gap:4px}#operatorDuplicateSearch [data-dupe-display] select{min-width:0;max-width:100%;margin:0}#operatorDuplicateSearch{width:min(470px,calc(100% - 24px));max-height:85dvh;overflow:auto;box-sizing:border-box;border:1px solid var(--line);border-radius:12px;padding:14px;background:var(--card);color:var(--ink)}#operatorDuplicateSearch::backdrop{background:#071d1677}#operatorDuplicateSearch header,#operatorDuplicateSearch form,.operator-dupe-actions{display:flex;align-items:center;gap:6px;flex-wrap:wrap}#operatorDuplicateSearch header{justify-content:space-between}#operatorDuplicateSearch h2{font-size:18px;margin:0}#operatorDuplicateSearch form{margin:12px 0 8px}#operatorDuplicateSearch [data-dupe-phone-form] input{flex:1;min-width:135px}#operatorDuplicateSearch .operator-dupe-pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}#operatorDuplicateSearch .operator-dupe-pair>*{min-width:0;overflow-wrap:anywhere}#operatorDuplicateSearch .operator-dupe-row input[type=checkbox]{width:auto}#operatorDuplicateSearch [data-dupe-results]{display:grid;gap:7px}#operatorDuplicateSearch .operator-dupe-row{display:grid;gap:4px;padding:6px;border:1px solid var(--line);border-radius:8px}#operatorDuplicateSearch .operator-dupe-open{display:grid;gap:2px;text-align:start;overflow-wrap:anywhere}#operatorDuplicateSearch .operator-dupe-open span{color:var(--muted);font-size:12px}#operatorDuplicateSearch label select{display:block;max-width:100%}#operatorDuplicateSearch [hidden],#operatorFindDuplicates[hidden]{display:none!important}#operatorHistoryMerge{width:min(360px,calc(100% - 24px));box-sizing:border-box;border:1px solid var(--line);border-radius:12px;padding:14px;background:var(--card);color:var(--ink)}#operatorHistoryMerge::backdrop{background:#071d1677}#operatorHistoryMerge header{display:flex;align-items:center;justify-content:space-between;gap:8px}#operatorHistoryMerge h2{font-size:17px;margin:0}#operatorHistoryMerge p{margin:10px 0 6px;font-variant-numeric:tabular-nums}#operatorHistoryMerge progress{display:block;width:100%;height:5px;margin-bottom:10px;accent-color:var(--brand)}#operatorHistoryMerge [hidden]{display:none!important}`;
    document.head.append(dupeStyle);
    $('operatorBulkAssignment').onclick=()=>openBulkAssignmentPreview();
    $('operatorCompleteAll').onclick=completeAllDialogs;
    $('operatorMarkAllRead').onclick=async()=>{const button=$('operatorMarkAllRead');if(markAllReadBusy||!hasRight('dialogs'))return;if(!window.confirm(t('markAllReadConfirm')))return;markAllReadBusy=true;button.disabled=true;$('operatorToolsMenu').open=false;try{const result=await api('dialogs/read-all','POST',{});listFlight=null;lastListAt=0;const refreshed=await refresh(false,false,{suppressRead:true});const remaining=Number(refreshed?.unread_count);notice(Number.isFinite(remaining)?t('markAllReadDone').replace('{count}',String(result.updated_dialogs||0)).replace('{remaining}',String(remaining)):t('markAllReadProcessed').replace('{count}',String(result.updated_dialogs||0)));}catch(error){notice(error.message||t('error'));}finally{markAllReadBusy=false;if(button.isConnected)button.disabled=false;};};
    document.addEventListener('tenant-employee-status-changed',()=>{if($('view-dialogs')?.classList.contains('active'))void refresh(false,false,{suppressRead:true});});
    // This is an explicit browser permission only; Web Push enrollment remains in profile settings.
    $('operatorNotificationToggle').onclick=requestBrowserNoticePermission;updateBrowserNoticeButton();
    $('operatorSoundToggle').onclick=async()=>{if(!soundEnabled){soundEnabled=true;await unlockMessageSound();}else if(!soundUnlocked){await unlockMessageSound();}else soundEnabled=false;try{localStorage.setItem(soundPrefKey(),soundEnabled?'on':'off');}catch{}updateSoundButton();};
    const unlockFromPage=event=>{if(event.target?.closest?.('#operatorSoundToggle'))return;void unlockMessageSound();};window.addEventListener('pointerdown',unlockFromPage,{once:true,passive:true});window.addEventListener('keydown',unlockFromPage,{once:true});loadSoundPreference();
    filterIds.forEach(id=>$(id).onchange=()=>{
      if(id==='operatorLifecycle'){
        const wasOverview=ownerOverviewWasActive;
        rememberOwnerOverviewMode();
        if(wasOverview&&!ownerOverviewWasActive&&(currentDialog?.owner_overview_read_only||pendingDialogRoute))closeDialog();
      }
      saveFilterPreferences();refresh();
    });
    // Search autofill hints and idle protection are shared across the app.
    window.tenantSearchInputs?.protect($('operatorSearch'));
    $('operatorSearch').oninput=()=>{clearTimeout(timer);timer=setTimeout(()=>refresh(),250);};
    setOptions('operatorChannel',[],'allChannels');setOptions('operatorAgent',[],'allAgents');setStates();setQueueModes();
  }
  function setStates(){const select=$('operatorState'),value=select.value;select.innerHTML='<option value="">'+t('allStates')+'</option>'+['automatic','manual','needs_assignment','attention','no_agent'].map(key=>`<option value="${key}">${t(key==='automatic'?'filterAiAutomatic':key==='manual'?'filterAiManual':key==='needs_assignment'?'filterNeedsAssignment':key==='attention'?'filterNeedsReview':'filterNoAgent')}</option>`).join('');select.value=[...select.options].some(option=>option.value===value)?value:'';}
  const bulkText=key=>({preview:{ru:'Предпросмотр массового назначения',az:'Kütləvi təyinatın önizləməsi',en:'Preview bulk assignment'},title:{ru:'Проверка назначений',az:'Təyinatların yoxlanması',en:'Assignment review'},confirm:{ru:'Назначить подходящих агентов',az:'Uyğun agentləri təyin et',en:'Assign eligible agents'},summary:{ru:'Диалогов: {total}; уже назначено: {assigned}; один кандидат: {one}; несколько: {multiple}; нет кандидатов: {none}; группы: {groups}; подходят: {groupEligible}; handoff: {handoff}.',az:'Dialoqlar: {total}; artıq təyin edilib: {assigned}; bir namizəd: {one}; bir neçə: {multiple}; namizəd yoxdur: {none}; qruplar: {groups}; uyğun qruplar: {groupEligible}; handoff: {handoff}.',en:'Dialogues: {total}; already assigned: {assigned}; one candidate: {one}; multiple: {multiple}; none: {none}; groups: {groups}; eligible groups: {groupEligible}; handoffs: {handoff}.'},confirmPrompt:{ru:'Назначить единственного подходящего агента во всех показанных подходящих диалогах и группах? Автоответы останутся выключенными; существующие handoff и состояния сохраняются.',az:'Göstərilən uyğun dialoq və qruplarda yeganə uyğun agent təyin edilsin? Avtomatik cavablar söndürülmüş qalacaq; mövcud handoff və vəziyyətlər saxlanılır.',en:'Assign the only eligible agent to every shown eligible dialogue and group? Automatic replies remain off; existing handoffs and states are preserved.'},done:{ru:'Назначение завершено: {assigned}; пропущено из-за изменений или ошибок: {skipped}.',az:'Təyinat tamamlandı: {assigned}; dəyişiklik və ya xətalara görə buraxıldı: {skipped}.',en:'Assignment complete: {assigned}; skipped due to changes or errors: {skipped}.'}}[key]?.[i18n.language]||'');
  function bulkCopy(key,params={}){let value=bulkText(key);for(const [name,text]of Object.entries(params))value=value.replaceAll('{'+name+'}',String(text));return value;}
  const bulkField=(key)=>({ru:{channel:'Номер WhatsApp / канал',chooseChannel:'Выберите канал',agent:'AI-агент',chooseAgent:'Выберите агента',review:'Проверить подходящие диалоги',missing:'Выберите канал и агента.',noChannels:'Нет активных привязанных WhatsApp-каналов.',warning:'Назначение не включает автоматические ответы. Существующие назначения и handoff не меняются.'},az:{channel:'WhatsApp nömrəsi / kanal',chooseChannel:'Kanal seçin',agent:'AI agent',chooseAgent:'Agent seçin',review:'Uyğun dialoqları yoxla',missing:'Kanal və agent seçin.',noChannels:'Aktiv qoşulmuş WhatsApp kanalı yoxdur.',warning:'Təyinat avtomatik cavabları aktivləşdirmir. Mövcud təyinatlar və handoff dəyişmir.'},en:{channel:'WhatsApp number / channel',chooseChannel:'Choose a channel',agent:'AI agent',chooseAgent:'Choose an agent',review:'Review eligible dialogues',missing:'Select a channel and agent.',noChannels:'No active linked WhatsApp channels.',warning:'Assignment does not enable automatic replies. Existing assignments and handoffs remain unchanged.'}}[i18n.language]||{})[key]||key;
  const bulkResultLine=(channel,agent,total,eligible,assigned,multiple,groups,groupEligible)=>({ru:`Канал: ${channel} · Агент: ${agent} · Диалогов: ${total} · Подходят: ${eligible} · Уже назначены: ${assigned} · Несколько кандидатов: ${multiple} · Группы: ${groups} · Подходят: ${groupEligible}`,az:`Kanal: ${channel} · Agent: ${agent} · Dialoqlar: ${total} · Uyğundur: ${eligible} · Artıq təyin olunub: ${assigned} · Bir neçə namizəd: ${multiple} · Qruplar: ${groups} · Uyğun qruplar: ${groupEligible}`,en:`Channel: ${channel} · Agent: ${agent} · Dialogues: ${total} · Eligible: ${eligible} · Already assigned: ${assigned} · Multiple candidates: ${multiple} · Groups: ${groups} · Eligible groups: ${groupEligible}`}[i18n.language]||'');
  const bulkInventoryLine=s=>({ru:`Инвентаризация: один кандидат ${s.single_candidate_unassigned||0}; несколько ${s.multiple_candidates||0}; нет кандидата ${s.no_candidate||0}; группы без основного агента ${s.group_without_primary_agent||0}; legacy-неоднозначность ${s.legacy_ambiguity||0}; handoff/остановленные ${s.handoff_and_exceptions||0}.`,az:`İnventar: bir namizəd ${s.single_candidate_unassigned||0}; bir neçə ${s.multiple_candidates||0}; namizəd yoxdur ${s.no_candidate||0}; əsas agentsiz qruplar ${s.group_without_primary_agent||0}; legacy qeyri-müəyyənliyi ${s.legacy_ambiguity||0}; handoff/dayandırılmış ${s.handoff_and_exceptions||0}.`,en:`Inventory: one candidate ${s.single_candidate_unassigned||0}; multiple ${s.multiple_candidates||0}; none ${s.no_candidate||0}; groups without a primary agent ${s.group_without_primary_agent||0}; legacy ambiguity ${s.legacy_ambiguity||0}; handoff/stopped ${s.handoff_and_exceptions||0}.`}[i18n.language]||'');
  const bulkReasonLabels={already_assigned:['уже назначен','artıq təyin olunub','already assigned'],multiple_candidates:['несколько подходящих агентов','bir neçə uyğun agent','multiple eligible agents'],no_candidate:['нет подходящего агента','uyğun agent yoxdur','no eligible agent'],legacy_channel_ambiguous:['неоднозначная привязка канала','kanal bağlantısı qeyri-müəyyəndir','channel binding is ambiguous'],handoff:['сохранён handoff','handoff saxlanılıb','handoff preserved'],group_active_state_preserved:['группа уже активна, состояние сохранено','qrup aktivdir, vəziyyət saxlanılıb','group is active; state preserved'],group_messages_not_allowed:['агенту не разрешены группы','agentə qruplar üçün icazə verilməyib','agent is not allowed in groups'],no_connect_scope:['агент не настроен для канала','agent kanal üçün təyin edilməyib','agent is not scoped to this channel'],agent_disabled:['агент выключен','agent deaktivdir','agent is disabled'],connect_admission_blocked:['обязательные проверки Connect не пройдены','Connect yoxlamaları keçilməyib','Connect admission checks did not pass'],selected_agent_not_candidate:['выбранный агент не подходит этому диалогу','seçilmiş agent bu dialoqa uyğun deyil','selected agent does not match this dialogue']};
  const bulkSkippedLine=items=>({ru:'Пропущено: ',az:'Buraxılıb: ',en:'Skipped: '}[i18n.language]||'Skipped: ')+items.map(([reason,count])=>`${bulkReasonLabels[reason]?.[['ru','az','en'].indexOf(i18n.language)]||reason}: ${count}`).join(' · ');
  async function openBulkAssignmentPreview(){
    const button=$('operatorBulkAssignment'),box=$('operatorBulkPreview');if(!button||!box||member?.role!=='owner')return;
    button.disabled=true;box.hidden=false;box.style.cssText='margin:12px 0;padding:12px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink)';box.textContent=bulkCopy('title')+'…';
    try{
      const [connect,agents]=await Promise.all([
        fetch('/api/platform/integrations/connect',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'channels',api_key:''})}).then(async r=>{const v=await r.json();if(!r.ok||!v.success)throw Error(v.error||'WhatsApp kanalları yüklənmədi.');return v.channels||[];}),
        api('agents').then(value=>Array.isArray(value)?value:value.agents||[])
      ]);
      const channels=connect.filter(c=>c.id&&c.linked&&c.active&&!c.phoneConflict),eligibleAgents=agents.filter(a=>a.enabled!==false&&Array.isArray(a.scopes)&&a.scopes.some(s=>s.provider==='connect'));
      box.replaceChildren();const channelLabel=document.createElement('label');channelLabel.textContent=bulkField('channel');const channel=document.createElement('select');channel.setAttribute('aria-label',bulkField('channel'));channel.append(new Option(bulkField('chooseChannel'),''));channels.forEach(c=>{const id=String(c.id||c.channel_id),phone=String(c.phone||c.phone_number||''),name=String(c.name||'WhatsApp');channel.add(new Option([phone,name,id].filter(Boolean).join(' · '),id));});if(!channels.length)channel.add(new Option(bulkField('noChannels'),''));channelLabel.append(channel);
      const agentLabel=document.createElement('label');agentLabel.textContent=bulkField('agent');const agent=document.createElement('select');agent.setAttribute('aria-label',bulkField('agent'));agent.append(new Option(bulkField('chooseAgent'),''));eligibleAgents.forEach(a=>agent.add(new Option(a.name,String(a.id))));agentLabel.append(agent);box.append(channelLabel,agentLabel);
      channel.onchange=()=>{if(channel.value){$('operatorChannel').value='connect:'+channel.value;refresh();}};
      const resultBox=document.createElement('div');resultBox.setAttribute('role','status');box.append(resultBox);
      const showPreview=document.createElement('button');showPreview.type='button';showPreview.className='outline';showPreview.textContent=bulkField('review');box.append(showPreview);
      showPreview.onclick=async()=>{if(!channel.value||!agent.value){resultBox.textContent=bulkField('missing');return;}showPreview.disabled=true;resultBox.textContent='…';try{
        const params=new URLSearchParams({channel_id:channel.value,agent_id:agent.value}),preview=await api('dialogs/agent-assignment-bulk?'+params),s=preview.summary||{};
        resultBox.replaceChildren();const heading=document.createElement('p');heading.textContent=bulkResultLine(channel.selectedOptions[0]?.textContent||channel.value,agent.selectedOptions[0]?.textContent||'',s.total||0,(preview.eligible||[]).length,s.assigned||0,s.multiple_candidates||0,s.group||0,s.group_eligible||0);const inventory=document.createElement('p');inventory.textContent=bulkInventoryLine(s);resultBox.append(heading,inventory);
        const reasons=Object.entries(preview.skipped_reasons||{});if(reasons.length){const p=document.createElement('p');p.textContent=bulkSkippedLine(reasons);resultBox.append(p);}
        const caution=document.createElement('p');caution.textContent=bulkField('warning');resultBox.append(caution);
        if(preview.eligible?.length){const confirm=document.createElement('button');confirm.type='button';confirm.className='primary';confirm.textContent=bulkCopy('confirm');confirm.onclick=async()=>{if(!window.confirm(bulkCopy('confirmPrompt')))return;confirm.disabled=true;try{const result=await api('dialogs/agent-assignment-bulk','POST',{confirmed:true,channel_id:channel.value,agent_id:agent.value,preview_token:preview.preview_token,batch_id:crypto.randomUUID()});resultBox.textContent=bulkCopy('done',{assigned:result.assigned||0,skipped:result.skipped||0});await refresh();}catch(error){resultBox.textContent=error.message;confirm.disabled=false;}};resultBox.append(confirm);}
      }catch(error){resultBox.textContent=error.message;}finally{showPreview.disabled=false;}};
    }catch(error){box.textContent=error.message;}finally{button.disabled=false;}
  }
  function setOptions(id,items,placeholder){const select=$(id),value=select.value,selected=select.selectedOptions[0]?.textContent;select.innerHTML=`<option value="">${t(placeholder)}</option>`+(id==='operatorAgent'?`<option value="unassigned">${t('noAgent')}</option>`:'')+items.map(item=>`<option value="${esc(item.id)}">${esc(item.name||item.id)}</option>`).join('');if(value&&![...select.options].some(option=>option.value===value))select.add(new Option(selected||value,value));select.value=value;}
  function badge(count){const node=$('dialogsAttentionCount'),value=Number(count)||0;if(node){node.textContent=value>99?'99+':String(value);node.hidden=value===0;node.setAttribute('aria-label',({ru:'Непрочитанные сообщения',az:'Oxunmamış mesajlar',en:'Unread messages'})[i18n.language]||'Unread messages')+': '+value;}}
  function observeLatestMessages(nextRows){
    const snapshotTime=Date.now();let selectedChanged=false;
    for(const row of nextRows||[]){const key=dialogKey(row),latest=String(row.last_message_id||''),previous=latestMessageIds.get(key),at=Date.parse(row.last_message_at||'');
      const isNew=hasInitialDialogSnapshot&&(previous?!!latest&&latest!==previous:Number.isFinite(at)&&at>lastDialogSnapshotAt);
      if(isNew&&row.last_message_direction==='incoming'){soundOnce(row,latest);notifyIncomingOnce(row,latest);if(sameDialog(row,currentDialog))selectedChanged=true;}
      if(latest)latestMessageIds.set(key,latest);
    }
    const selected=(nextRows||[]).flatMap(row=>row.group_sources||[row]).find(row=>sameDialog(row,currentDialog));
    if(selected?.last_message_id&&String(selected.last_message_id)!==String(currentPage?.dialog?.last_message_id||''))selectedChanged=true;
    if(!hasInitialDialogSnapshot){hasInitialDialogSnapshot=true;lastDialogSnapshotAt=snapshotTime;}return selectedChanged;
  }
  function refresh(append=false,reuse=false,options={}){
    if(!hasRight('dialogs'))return Promise.resolve();install();
    if(!filterPreferencesLoaded){const version=epoch;return loadFilterPreferences().then(()=>{if(version===epoch&&hasRight('dialogs'))return refresh(append,reuse,options);});}
    const search=searchValue($('operatorSearch')).trim(),overview=ownerOverviewMode()||Boolean(search);
    const key='dialogs?'+new URLSearchParams({search,channel:$('operatorChannel').value,agent:$('operatorAgent').value,state:$('operatorState').value,lifecycle:queueMode(),offset:append?nextOffset||0:0,paired_history:'1',...(queueMode()==='all'?{mixed_history_card:'1'}:{}),...(ownerOverviewMode()?{owner_overview:'1'}:{}),...(search?{search_overview:'1'}:{}),...(overview?{expected_tenant_id:member.tenant_id,expected_user_id:member.telegram_id}:{})});
    if(listFlight?.key===key)return listFlight.promise;
    if(reuse&&!append&&key===lastListKey&&Date.now()-lastListAt<5000){renderRows();return Promise.resolve();}
    const revision=++listRevision,version=epoch;
    $('operatorRows').setAttribute('aria-busy','true');if(!rows.length)notice(t('loading'));
    const promise=(async()=>{try{
      const result=await api(key);if(version!==epoch||revision!==listRevision)return;
      const selectedChanged=observeLatestMessages(result.dialogs);rows=append?[...new Map(rows.concat(result.dialogs).map(row=>[row.group_sources?.length?'connect-group:'+row.conversation_id:dialogKey(row),row])).values()]:result.dialogs;nextOffset=result.next_offset;
      reconcileLifecycleRows();lastListKey=key;lastListAt=Date.now();
      $('operatorResultsCount').textContent=t('dialogResultCount').replace('{count}',String(result.total_count||0));badge(result.unread_count);setOptions('operatorChannel',result.channels,'allChannels');setOptions('operatorAgent',result.agents,'allAgents');renderRows();notice('');
      const nameHint=(result.dialogs||[]).find(row=>isDirectConnectDialog(row)&&sameDialog(row,currentDialog));
      const nameOnly=Boolean(!selectedChanged&&!options.revalidateSelected&&nameHint&&currentPage?.dialog&&
        sameDialog(nameHint,currentPage.dialog)&&nameHint.customer_name!==currentPage.dialog.customer_name);
      if(currentDialog&&!pendingDialogRoute&&(currentDialog.lifecycle_enabled||currentDialog.wecall||historyMergeId(currentPage)||options.revalidateSelected)){
        await openDialog(currentDialog,true,'',{suppressRead:options.suppressRead===true||nameOnly,revalidateAccess:options.revalidateSelected===true});
      }else if(nameOnly&&!pendingDialogRoute)await appendNewMessages(currentDialog,true,true);
      else if(selectedChanged&&currentDialog)await appendNewMessages(currentDialog,options.suppressRead===true);
      else if(currentDialog&&!options.suppressRead)void markDialogReadIfVisible(currentPage);
      return result;
    }catch(error){if(version===epoch&&revision===listRevision)notice(error.message);}
    finally{if(version===epoch&&revision===listRevision){listFlight=null;$('operatorRows').setAttribute('aria-busy','false');}}})();
    listFlight={key,promise};return promise;
  }
  const audioEvidenceFields=['has_transcription','audio_transcript','analysis_state','analysis_diagnostic','analysis_receipt_diagnostic'];
  function updateVisualEvidence(latest,route,sourceIds=null){
    const chat=$('chatRows');if(!chat||!currentPage||!sameDialog(route,currentDialog)||!sameDialog(latest.dialog,route))return;
    for(const fresh of latest.messages||[]){
      if(fresh.source!=='connect'||!['image','video','document'].includes(fresh.message_type)||(sourceIds&&!sourceIds.has(String(fresh.external_id))))continue;
      const old=(currentPage.messages||[]).find(item=>String(item.id||item.external_id)===String(fresh.id||fresh.external_id)&&String(item.external_id)===String(fresh.external_id)&&String(item.source_channel_id||route.channel_id)===String(fresh.source_channel_id||route.channel_id));
      if(!old)continue;
      // The visible preview belongs to its own bytes; a replacement's receipt
      // cannot certify that preview. Full history reload restores new media.
      old.analysis_state=old.media_version===fresh.media_version?fresh.analysis_state:'not_requested';
      const article=[...chat.querySelectorAll('[data-message-id]')].find(node=>node.dataset.messageId===String(old.id||old.external_id));
      const label=article?.querySelector('.operator-media-analysis');if(label)label.textContent=mediaAnalysisLabel(old);
    }
    rememberDialogPage(route,currentPage);
  }
  function updateAudioEvidence(latest,route,sourceIds=null){
    const chat=$('chatRows');if(!chat||!currentPage||!sameDialog(route,currentDialog)||!sameDialog(latest.dialog,route))return;
    const top=chat.scrollTop;
    for(const fresh of latest.messages||[]){
      if(fresh.source!=='connect'||!['audio','voice','voice_message','ptt'].includes(String(fresh.message_type||'').toLowerCase())||(sourceIds&&!sourceIds.has(String(fresh.external_id))))continue;
      const old=(currentPage.messages||[]).find(item=>String(item.id||item.external_id)===String(fresh.id||fresh.external_id)&&String(item.external_id)===String(fresh.external_id)&&String(item.source_channel_id||route.channel_id)===String(fresh.source_channel_id||route.channel_id));
      if(!old||audioEvidenceFields.every(key=>JSON.stringify(old[key])===JSON.stringify(fresh[key])))continue;
      const article=[...chat.querySelectorAll('[data-message-id]')].find(node=>node.dataset.messageId===String(old.id||old.external_id));
      for(const key of audioEvidenceFields){if(Object.prototype.hasOwnProperty.call(fresh,key))old[key]=fresh[key];else delete old[key];}
      if(!article)continue;
      const template=document.createElement('template');template.innerHTML=messageMarkup([old],route);
      const label=article.querySelector('.operator-media-analysis'),nextLabel=template.content.querySelector('.operator-media-analysis');if(label&&nextLabel)label.textContent=nextLabel.textContent;
      // Keep the native player, quote controls and employee editor in place.
      for(const selector of ['[data-audio-transcript]','[data-audio-diagnostic]']){
        const prior=article.querySelector(selector),next=template.content.querySelector(selector),focused=prior?.contains(document.activeElement),opened=prior?.open;
        if(next){if(opened)next.open=true;if(prior)prior.replaceWith(next);else article.insertBefore(next,article.querySelector('footer'));if(focused)next.querySelector('summary')?.focus({preventScroll:true});}else prior?.remove();
      }
      const footer=article.querySelector('[data-message-footer-actions]');if(footer)footer.innerHTML=deliveryMarkup(old)+copyControlMarkup(old);
    }
    chat.scrollTop=top;rememberDialogPage(route,currentPage);
  }
  function updateDeliveryEvidence(latest,route){
    const chat=$('chatRows');if(!chat||!currentPage||!sameDialog(route,currentDialog)||!sameDialog(latest.dialog,route))return;
    retainDeliveryEvidence(currentPage,latest);
    const top=chat.scrollTop;
    for(const fresh of latest.messages||[]){
      if(fresh.source!=='connect'||fresh.direction!=='outgoing')continue;
      const old=(currentPage.messages||[]).find(item=>item.source===fresh.source&&item.direction==='outgoing'&&String(item.id)===String(fresh.id)&&String(item.external_id)===String(fresh.external_id)&&String(item.source_channel_id||route.channel_id)===String(fresh.source_channel_id||route.channel_id)&&String(item.source_conversation_id||route.conversation_id)===String(fresh.source_conversation_id||route.conversation_id));
      if(!old||old.delivery_state===fresh.delivery_state)continue;
      if(Object.prototype.hasOwnProperty.call(fresh,'delivery_state'))old.delivery_state=fresh.delivery_state;else delete old.delivery_state;
      const article=[...chat.querySelectorAll('article[data-message-id]')].find(node=>node.dataset.messageId===String(old.id));
      const footer=article?.querySelector('[data-message-footer-actions]');if(!footer)continue;
      const prior=footer.querySelector('[data-delivery-state]'),markup=deliveryMarkup(old);
      if(prior){if(markup)prior.outerHTML=markup;else prior.remove();}else if(markup)footer.insertAdjacentHTML('afterbegin',markup);
    }
    chat.scrollTop=top;
  }
  async function refreshAudioEvidence(route,sourceIds){
    const page=currentPage,chat=$('chatRows'),version=epoch,serial=++historyUpdateSerial;
    try{const latest=await api('dialogs/history?'+(historyMergeId(page)?timelineHistoryQuery(route):dialogueHistoryQuery(route)));if(serial!==historyUpdateSerial||version!==epoch||page!==currentPage||!chat?.isConnected||!sameDialog(route,currentDialog)||!sameDialog(latest.dialog,route))return;if(applyHistoryMergeChange(route,latest))return;updateDeliveryEvidence(latest,route);updateAudioEvidence(latest,route,sourceIds);updateVisualEvidence(latest,route,sourceIds);}
    catch(error){if(serial===historyUpdateSerial&&version===epoch&&page===currentPage&&sameDialog(route,currentDialog)){const status=$('operatorChatError');if(status)status.textContent=error.message;}}
  }
  async function appendNewMessages(route,suppressRead=false,namesOnly=false){
    const chat=$('chatRows'),page=currentPage,version=epoch,revision=openRevision,readSerial=dialogReadSerial,serial=++historyUpdateSerial;if(!chat||!page||!sameDialog(route,currentDialog))return;
    try{
      const latest=await api('dialogs/history?'+timelineHistoryQuery(route,page));
      if(serial!==historyUpdateSerial||version!==epoch||revision!==openRevision||readSerial!==dialogReadSerial||page!==currentPage||!sameDialog(route,currentDialog)||!sameDialog(latest.dialog,route)||!chat.isConnected)return;
      if(applyHistoryMergeChange(route,latest))return;
      if(namesOnly){
        const follow=atBottom(chat),top=chat.scrollTop,anchor=follow?null:timelineViewportAnchor(chat);
        patchDirectDialogNames(latest,page,chat);
        let placed=false;
        if(Array.isArray(latest.shared_reminder_events)){
          retainSharedReminderEvents(page,latest);
          placed=placeOperatorEvents(chat,page);
        }
        setTimelineScroll(chat,follow?(placed?chat.scrollHeight:top):timelineScrollTop(top,anchor),true);
        rememberDialogPage(route,page);
        return;
      }
      patchDirectDialogNames(latest,page,chat);
      updateDeliveryEvidence(latest,route);
      updateAudioEvidence(latest,route);
      updateVisualEvidence(latest,route);
      const messageKeys=m=>[m.id,m.external_id].filter(Boolean).map(String),seen=new Set((currentPage.messages||[]).flatMap(messageKeys));
      const added=(latest.messages||[]).filter(m=>{const keys=messageKeys(m);if(!keys.length||keys.some(key=>seen.has(key)))return false;keys.forEach(key=>seen.add(key));return true;});
      retainOperatorEvents(currentPage,latest);
      if(!added.length){const follow=atBottom(chat),top=chat.scrollTop;if(Array.isArray(latest.operator_events)||Array.isArray(latest.shared_reminder_events)){const anchor=follow?null:timelineViewportAnchor(chat),placed=placeOperatorEvents(chat,currentPage);setTimelineScroll(chat,follow?chat.scrollHeight:timelineScrollTop(top,anchor),placed);}if(Object.prototype.hasOwnProperty.call(latest,'read_through_message_id'))currentPage.read_through_message_id=latest.read_through_message_id;else delete currentPage.read_through_message_id;currentPage.dialog=latest.dialog||currentPage.dialog;rememberDialogPage(route,currentPage);if(!suppressRead&&atBottom(chat))void markDialogReadIfVisible(currentPage);return;}
      const follow=atBottom(chat),oldTop=chat.scrollTop,anchor=follow?null:timelineViewportAnchor(chat);
      const empty=chat.textContent.trim()===t('emptyHistory');if(empty)chat.replaceChildren();
      chat.insertAdjacentHTML('beforeend',messageMarkup(added,route));
      currentPage.messages=(currentPage.messages||[]).concat(added);const timelinePlaced=placeOperatorEvents(chat,currentPage);currentPage.dialog=latest.dialog||currentPage.dialog;if(Object.prototype.hasOwnProperty.call(latest,'read_through_message_id'))currentPage.read_through_message_id=latest.read_through_message_id;else delete currentPage.read_through_message_id;rememberDialogPage(route,currentPage);
      document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));
      const newArticles=[...chat.querySelectorAll('[data-message-id]')].filter(node=>added.some(message=>String(message.id)===node.dataset.messageId));
      newArticles.forEach(node=>{node.classList.add('operator-message-enter');setTimeout(()=>node.classList.remove('operator-message-enter'),180);});
      if(follow){setTimelineScroll(chat,chat.scrollHeight,timelinePlaced);if(!suppressRead)void markDialogReadIfVisible(currentPage);}
      else {setTimelineScroll(chat,timelineScrollTop(oldTop,anchor),timelinePlaced);const button=$('operatorNewMessages');if(button)button.hidden=false;}
      const error=$('operatorChatError');if(error)error.textContent='';
    }catch(error){
      if(serial!==historyUpdateSerial||version!==epoch||revision!==openRevision||readSerial!==dialogReadSerial||page!==currentPage||!sameDialog(route,currentDialog))return;
      if(lifecycleUnavailable(error,route))return;
      const status=$('operatorChatError');if(status)status.textContent=error.message;
    }
  }
  const usableDialogName=(value,route)=>{
    const name=typeof value==='string'?value.trim():'';
    const lid=route?.provider==='connect'&&String(route.conversation_id).match(/^([1-9][0-9]{5,21})@lid$/);
    if(lid&&/^[+0-9(). -]+$/.test(name)&&name.replace(/\D/g,'')===lid[1])return '';
    return name&&name.length<=256&&!/@(?:s\.whatsapp\.net|c\.us|g\.us|lid)$/i.test(name)?name:'';
  };
  const safeName=row=>usableDialogName(row.customer_name,row);
  const verifiedDialogPhone=row=>{const phone=String(row?.phone||'').trim(),digits=phone.replace(/\D/g,'');return /^\+?[0-9 ()-]+$/.test(phone)&&digits.length>=7&&digits.length<=15?phone:'';};
  // Device contact details are presentation only and never change the route.
  const contactPhoneLookups=new Map();
  const contactPhoneKey=route=>`${epoch}:${contextKey('contact-phone',route)}:${route.lead_id}:${route.contact_source_revision||''}`;
  const contactDetails=route=>{if(route.archive_read_only||route.owner_overview_read_only||route.case_attachment||route.contact_identity_conflict)return null;const cached=contactPhoneLookups.get(contactPhoneKey(route));return cached&&(!cached.done||Date.now()<cached.retryAt)?cached:null;};
  const deviceDisplayPhone=route=>!route.contact_identity_conflict&&verifiedDialogPhone({phone:route.device_display_phone});
  const displayDialogPhone=route=>verifiedDialogPhone(route)||deviceDisplayPhone(route)||contactDetails(route)?.phone||(!route.contact_identity_conflict&&!route.identity_conflict?verifiedDialogPhone({phone:route.manual_display_phone}):'');
  const phoneHiddenLabel=()=>({ru:'Скрыт',az:'Gizlidir',en:'Hidden'}[i18n.language]||'Hidden');
  const dialogPhoneLabel=route=>displayDialogPhone(route)||(contactDetails(route)?.phoneHidden?phoneHiddenLabel():'');
  const phoneUnavailable=()=>dialogPhoneLabel(currentDialog);
  const isDialogGroup=route=>route.customer_kind==='group'||route.conversation_kind==='group';
  const hasContactName=route=>{
    const name=safeName(route);
    return Boolean(usableDialogName(route.manual_display_name,route)||(name&&!['whatsapp contact','контакт whatsapp','whatsapp kontaktı'].includes(name.toLowerCase())&&!/^[+0-9(). -]+$/.test(name)));
  };
  const canLookupContact=route=>route?.provider==='connect'&&!isDialogGroup(route)
    &&!route.archive_read_only&&!route.owner_overview_read_only&&!route.case_attachment&&!route.contact_identity_conflict
    &&!((verifiedDialogPhone(route)||deviceDisplayPhone(route))&&hasContactName(route))
    &&/^[1-9][0-9]{5,21}@lid$/.test(route.conversation_id||'');
  const canLookupRowContact=route=>canLookupContact(route)&&/^[a-f0-9]{64}$/.test(route.contact_source_revision||'');
  const inDialogsView=()=>hasRight('dialogs')&&$('view-dialogs')?.classList.contains('active');
  function lookupContactDetails(route,fromList=false){
    let entry=contactDetails(route);
    if(entry){if(!fromList)entry.fromList=false;return entry.flight;}
    const key=contactPhoneKey(route),owner=identity(member);
    entry={retryAt:0,done:false,phone:'',name:'',fromList,controller:new AbortController()};
    contactPhoneLookups.delete(key);
    if(contactPhoneLookups.size>=64){
      for(const [old,cached] of contactPhoneLookups)if(cached.done){contactPhoneLookups.delete(old);break;}
    }
    contactPhoneLookups.set(key,entry);
    const query=historyQuery(route);
    if(route.contact_source_revision)query.set('contact_source_revision',route.contact_source_revision);
    entry.flight=rawApi(endpoint+'dialogs/contact-phone?'+query,{signal:entry.controller.signal}, {optionalScopedRead:true}).then(({result})=>{
      if(entry.controller.signal.aborted||key!==contactPhoneKey(route)||owner!==identity(member)||!hasRight('dialogs')||result?.display_only!==true||!['available','hidden'].includes(result.status)||result.provider!=='connect'||!sameDialog(result,route))return;
      if(route.contact_source_revision&&result.contact_source_revision!==route.contact_source_revision)return;
      if(/^\+[1-9][0-9]{6,14}$/.test(result.phone||''))entry.phone=result.phone;
      entry.phoneHidden=result.status==='hidden'&&!entry.phone;
      entry.name=usableDialogName(result.display_name,route);
    }).catch(()=>{}).finally(()=>{
      entry.retryAt=Date.now()+(fromList&&!entry.phone&&!entry.name?120000:30000);
      entry.done=true;
    });
    return entry.flight;
  }
  function abortContactLookup(key,entry){
    if(entry?.done||!entry)return;
    if(contactPhoneLookups.get(key)===entry)contactPhoneLookups.delete(key);
    entry.controller.abort();
  }
  function clearContactLookups(){
    stopRowContacts();
    for(const [key,entry] of contactPhoneLookups)abortContactLookup(key,entry);
    contactPhoneLookups.clear();
  }
  function updateRowContact(route){
    if(!inDialogsView())return;
    const index=rows.findIndex(row=>sameDialog(row,route)&&canLookupContact(row)&&contactPhoneKey(row)===contactPhoneKey(route));
    if(index<0)return;
    const card=$('operatorRows')?.querySelector(`[data-open-operator="${index}"]`);
    const title=card?.querySelector('.operator-row-title'),subtitle=card?.querySelector('.operator-row-sub');
    if(title)title.textContent=visualDialogTitle(rows[index]);
    if(subtitle)subtitle.textContent=dialogRowSubtitle(rows[index]);
  }
  let contactRowObserver;
  const contactRowQueue=new Map(),contactRowActive=new Map(),contactRowStarts=[];
  const rowContactView=()=>!document.hidden&&inDialogsView()
    &&!(window.matchMedia?.('(max-width:900px)')?.matches&&$('operatorDialogs')?.dataset.conversationOpen==='true');
  const rowContactVisible=button=>{
    if(!button.isConnected||!rowContactView()||!button.getClientRects().length)return false;
    const rect=button.getBoundingClientRect();
    if(!(rect.bottom>0&&rect.right>0&&rect.top<window.innerHeight&&rect.left<window.innerWidth))return false;
    // The desktop list scrolls internally; the mobile wrapper can use display:contents.
    // Fall back to the rows container when the wrapper has no own box.
    const scroller=operatorListScroller(),clip=(scroller?.getClientRects().length?scroller:$('operatorRows'))?.getBoundingClientRect();
    return !clip||rect.bottom>clip.top&&rect.right>clip.left&&rect.top<clip.bottom&&rect.left<clip.right;
  };
  function cancelRowContact(button){
    const active=contactRowActive.get(button);
    if(active?.entry?.fromList)abortContactLookup(active.key,active.entry);
  }
  function stopRowContacts(){
    contactRowObserver?.disconnect();
    contactRowObserver=null;
    contactRowQueue.clear();
    for(const button of contactRowActive.keys())cancelRowContact(button);
  }
  function pumpRowContacts(){
    const now=Date.now();
    while(contactRowStarts.length&&now-contactRowStarts[0]>=15000)contactRowStarts.shift();
    while(contactRowActive.size<2&&contactRowQueue.size&&contactRowStarts.length<8){
      const [button,route]=contactRowQueue.entries().next().value;
      contactRowQueue.delete(button);
      if(!rowContactVisible(button)||!sameDialog(rows[Number(button.dataset.openOperator)],route)||contactPhoneKey(rows[Number(button.dataset.openOperator)])!==contactPhoneKey(route)||contactDetails(route))continue;
      const key=contactPhoneKey(route);
      contactRowStarts.push(now);
      const flight=lookupContactDetails(route,true),entry=contactDetails(route);
      contactRowActive.set(button,{key,entry});
      flight.then(()=>{
        if(!entry.controller.signal.aborted&&key===contactPhoneKey(route)&&rowContactVisible(button))updateRowContact(route);
      }).finally(()=>{
        contactRowActive.delete(button);
        pumpRowContacts();
      });
    }
  }
  function hydrateRowContacts(){
    // Preserve an already-started exact-route lookup when opening that conversation.
    const selected=currentDialog&&contactDetails(currentDialog);
    if(selected&&!selected.done)selected.fromList=false;
    stopRowContacts();
    if(!rowContactView()||!('IntersectionObserver' in window))return;
    const observer=new IntersectionObserver(entries=>{
      if(contactRowObserver!==observer)return;
      for(const entry of entries){
        const button=entry.target,row=rows[Number(button.dataset.openOperator)];
        if(entry.isIntersecting&&rowContactVisible(button)&&canLookupRowContact(row)&&!contactDetails(row))contactRowQueue.set(button,row);
        else if(!entry.isIntersecting){contactRowQueue.delete(button);cancelRowContact(button);}
      }
      pumpRowContacts();
    });
    contactRowObserver=observer;
    for(const button of $('operatorRows').querySelectorAll('[data-open-operator]')){
      const row=rows[Number(button.dataset.openOperator)];
      if(canLookupRowContact(row)&&!contactDetails(row))observer.observe(button);
    }
  }
  function hydrateContactDetails(route,head){
    if(!canLookupContact(route))return;
    const key=contactPhoneKey(route),revision=openRevision;
    lookupContactDetails(route).then(()=>{
      if(key!==contactPhoneKey(route)||revision!==openRevision||!inDialogsView()||!sameDialog(route,currentDialog)||!head.isConnected||head!==$('operatorConversationHeader'))return;
      const phone=visualDialogPhone(currentDialog);
      for(const label of [head.querySelector('[data-header-phone]'),$('operatorClientCard')?.querySelector('[data-panel-phone]')])if(label)label.textContent=phone;
      const title=$('operatorDialogTitleText');if(title)title.textContent=visualDialogTitle(currentDialog);
      const manualForm=$('operatorManualDisplayPhone');if(manualForm)manualForm.hidden=!canEditManualPhone(currentDialog);
      const panelName=$('operatorClientCard')?.querySelector('[data-panel-name]');if(panelName)panelName.textContent=visualDialogTitle(currentDialog);
      updateRowContact(route);
    });
  }
  const dialogTitle=row=>{
    const known=safeName(row);
    if(isDialogGroup(row))return known||t('whatsappGroup');
    if(row.provider!=='connect')return known||row.phone||t('whatsappContact');
    return usableDialogName(row.manual_display_name,row)||(hasContactName(row)?known:'')||contactDetails(row)?.name||displayDialogPhone(row)||t('whatsappContact');
  };
  // A combined-card label is presentation only; physical identities and their editors stay exact.
  const hasMergeDisplay=row=>member?.role==='owner'&&row?.provider==='connect'&&!!row.history_merge_id&&!isDialogGroup(row)&&!row.case_attachment;
  const mergeDisplayName=row=>hasMergeDisplay(row)?usableDialogName(row.history_merge_display_name,row):'';
  const mergeDisplayPhone=row=>hasMergeDisplay(row)?verifiedDialogPhone({phone:row.history_merge_display_phone}):'';
  const visualDialogTitle=row=>mergeDisplayName(row)||dialogTitle(row);
  const visualDialogPhone=row=>mergeDisplayPhone(row)||dialogPhoneLabel(row);
  const dialogIdentity=row=>isDialogGroup(row)?(row.group_participant_count?`${row.group_participant_count} · ${t('participants')}`:''):dialogPhoneLabel(row);
  const dialogRowSubtitle=row=>{const identity=mergeDisplayPhone(row)||dialogIdentity(row);return !isDialogGroup(row)&&identity===visualDialogTitle(row)?'':identity;};
  const avatarUrl=row=>'/api/platform/ai/dialogs/avatar?'+new URLSearchParams({provider:row.provider||'connect',lead_id:String(row.lead_id||''),channel_id:String(row.channel_id||''),conversation_id:String(row.conversation_id||'')});
  const avatarAttempts=new Set(),avatarReady=new Set(),avatarQueue=[];let avatarActive=0,avatarObserver;
  const avatarMarkup=(row,header=false)=>{
    if(row.archive_read_only||row.provider!=='connect'||!['','available'].includes(row.avatar_status||''))return '';
    const url=avatarUrl(row),key=identity(member)+':'+url;
    return `<img class="operator-row-avatar-image" ${row.avatar_status==='available'||avatarReady.has(key)?`src="${esc(url)}"`:`data-avatar-src="${esc(url)}"`} alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">`;
  };
  function hydrateAvatars(){
    const pump=()=>{while(avatarActive<2&&avatarQueue.length){const {image,key,url}=avatarQueue.shift();if(!image.isConnected){avatarAttempts.delete(key);continue;}avatarActive++;avatarAttempts.add(key);const probe=new Image(),done=()=>{avatarActive--;pump();};probe.onload=()=>{avatarReady.add(key);if(key===identity(member)+':'+url)document.querySelectorAll('[data-avatar-src]').forEach(node=>{if(node.dataset.avatarSrc===url){node.src=url;delete node.dataset.avatarSrc;}});image.src=url;done();};probe.onerror=()=>{image.remove();done();};probe.src=url;}};
    const enqueue=image=>{const url=image.dataset.avatarSrc,key=identity(member)+':'+url;if(!url||avatarAttempts.has(key))return;avatarAttempts.add(key);avatarQueue.push({image,key,url});pump();};
    const images=[...document.querySelectorAll('[data-avatar-src]')];avatarObserver?.disconnect();
    if(!('IntersectionObserver' in window)){images.slice(0,8).forEach(enqueue);return;}
    avatarObserver=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){avatarObserver.unobserve(entry.target);const image=entry.target.querySelector('[data-avatar-src]');if(image)enqueue(image);}}, {rootMargin:'40px'});
    images.forEach(image=>avatarObserver.observe(image.parentElement));
  }

  const dialogPreview=row=>{const kind=String(row.last_message_type||'').toLowerCase();if(kind&&kind!=='text'){if(kind==='call_missed')return missedCallLabel(false);if(kind==='call_rejected')return rejectedCallLabel(false);if(kind==='call_invalidated')return invalidatedCallLabel(false);if(kind==='call_observed')return observedCallLabel(false);if(kind==='image')return t('photo');if(['audio','voice','ptt','voice_message'].includes(kind))return t('voiceMessage');if(kind==='video')return t('video');return t('file');}return String(row.last_message_preview||row.last_message_text||row.last_message||'').replace(/\s+/g,' ').trim().slice(0,160);};
  function lifecycleBadge(route){
    if(!route?.lifecycle_enabled||(route.lifecycle?.state==='waiting'&&route.lifecycle.has_inbound===false))return '';
    const state=route.lifecycle?.state,labels={waiting:{ru:'Новый',az:'Yeni',en:'New'},accepted:{ru:'Принят',az:'Qəbul edilib',en:'Accepted'},closed:{ru:'Завершён',az:'Tamamlanıb',en:'Completed'},dismissed:{ru:'Не требует ответа',az:'Cavab tələb etmir',en:'No reply needed'}};
    const label=labels[state]?.[i18n.language]||labels[state]?.en;
    return label?`<span class="operator-lifecycle-badge" data-lifecycle-state="${esc(state)}">${esc(label)}</span>`:'';
  }
  // Presentation only: retain all authorized history routes, but never offer a
  // disabled channel as a reply source. Lifecycle actions stay route-scoped.
  const activeGroupSources=row=>(row?.group_sources||[]).filter(source=>source.channel_active!==false);
  function cardLifecycleBadge(row){
    if(!row.group_sources?.length)return lifecycleBadge(row);
    const sources=activeGroupSources(row);
    const route=['waiting','accepted','closed','dismissed'].map(state=>sources.find(source=>source.lifecycle_enabled&&source.lifecycle?.state===state&&!(state==='waiting'&&source.lifecycle.has_inbound===false))).find(Boolean);
    return lifecycleBadge(route);
  }
  function formatDialogListTime(value){
    if(!value)return '';
    const date=new Date(value);
    if(!Number.isFinite(date.getTime()))return '';
    const two=n=>String(n).padStart(2,'0');
    const day=[two(date.getDate()),two(date.getMonth()+1),String(date.getFullYear()).padStart(4,'0')].join('.');
    const time=[date.getHours(),date.getMinutes(),date.getSeconds()].map(two).join(':');
    return day+' '+time;
  }
  function isSelectedMergeCard(row){
    if(!row?.history_merge_id||!currentDialog?.history_merge_id)return false;
    const readRoutes=historyMergeReadRoutes(currentDialog);
    if(readRoutes.length)return ((!!row.history_merge_certificate&&row.history_merge_certificate===currentDialog.history_merge_certificate)||
      row.history_merge_id===historyMergeId(currentPage))&&readRoutes.some(source=>sameDialog(source,row));
    return row.history_merge_id===historyMergeId(currentPage)&&historyMergeActionRoutes(currentDialog).some(source=>sameDialog(source,row));
  }
  function renderRows(){
    $('operatorRows').innerHTML=rows.map((row,index)=>{
      const companyName=(member?.role==='owner'||capabilities.dialog_company_binding===true)?row.dialog_company_name:'';
      const name=visualDialogTitle(row),preview=dialogPreview(row);
      const selected=isSelectedMergeCard(row)||(row.group_sources||[row]).some(source=>sameDialog(currentDialog,source))
        ||(selectedDialogOwner===identity(member)&&sameDialog(selectedDialog,row));
      const unread=Math.max(0,Number(row.unread_count)||0);
      const identityText=dialogRowSubtitle(row),timeText=formatDialogListTime(row.last_message_at);
      const pendingMissedCall=String(row.last_message_type||'').toLowerCase()==='call_missed'&&(
        row.provider==='connect'&&row.lifecycle_enabled
          ?row.lifecycle?.state!=='accepted'&&row.missed_call_pending===true
          :unread>0);
      const lifecycle=cardLifecycleBadge(row),previewMarkup=preview?`<span class="operator-row-preview${pendingMissedCall?' operator-row-preview-missed-pending':''}" data-user-content>${esc(preview)}</span>`:'';
      return `<button type="button" role="option" class="operator-row" aria-selected="${selected?'true':'false'}" data-open-operator="${index}">
        <span class="operator-row-avatar" aria-hidden="true">${row.customer_kind==='group'?'◉':esc(name.slice(0,1).toLocaleUpperCase())}${avatarMarkup(row)}</span>
        <span class="operator-row-body">
          <span class="operator-row-copy"><span class="operator-row-title" data-user-content>${esc(name)}</span><span class="operator-row-sub" data-user-content>${esc(identityText)}</span></span>
          <span class="operator-row-meta"><span class="operator-row-indicators">${lifecycle}${unread?`<span class="operator-unread-count" aria-label="${unread} ${esc(({ru:'непрочитанных',az:'oxunmamış',en:'unread'})[i18n.language]||'unread')}" title="${unread}">${unread>99?'99+':unread}</span>`:''}</span>${timeText?`<time class="operator-row-time">${esc(timeText)}</time>`:''}</span>
          ${companyName?`<span class="operator-row-context"><span class="operator-state operator-row-company" title="${esc(companyName)}" data-user-content>${esc(companyName)}</span></span>`:''}
          ${previewMarkup}
          ${capabilities.dialog_company_binding===true?'':`<span class="operator-state operator-row-status" data-state="${esc(row.agent_status||row.display_state)}">${esc(agentModeLabel(row))}</span>`}
        </span>
      </button>`;
    }).join('')||`<p class="operator-conversation-placeholder">${t('empty')}</p>`;
    hydrateAvatars();
    hydrateRowContacts();
    window.tenantSharedReminders?.markRows(rows.map(row=>row.group_sources?.length?activeGroupSources(row):[row]));
    renderComposerSourcePicker();$('operatorMore').hidden=nextOffset===null;$('operatorMore').textContent=t('more');operatorListScroller().scrollTop=listScrollTop;
    $('operatorRows').querySelectorAll('[data-open-operator]').forEach(button=>button.onclick=()=>{
      const row=rows[Number(button.dataset.openOperator)],sources=activeGroupSources(row);
      if(isSelectedMergeCard(row))return void openDialog(currentDialog,true,'',{suppressRead:true,revalidateAccess:true});
      const preferCard=ownerOverviewMode()||row.search_overview_read_only||sources.some(source=>source.archive_read_only);
      openDialog(sources.find(source=>sameDialog(source,currentDialog))
        ||(preferCard&&sources.find(source=>sameDialog(source,row)))||sources[0]||row,false,'',{userOpened:true});
    });
  }
  const historyQuery=route=>new URLSearchParams({provider:route.provider||'connect',lead_id:route.lead_id,channel_id:route.channel_id||'',conversation_id:route.conversation_id||'',...(route.source_message_id?{anchor:route.source_message_id}:{})});
  function dialogueHistoryQuery(route){
    const query=historyQuery(route);
    if(route.search_overview_read_only===true&&!route.case_attachment&&route.provider==='connect'&&member?.tenant_id&&member?.telegram_id){
      query.set('search_overview','1');query.set('search',String(route.search_overview_query||searchValue($('operatorSearch'))).trim());
      query.set('expected_tenant_id',member.tenant_id);query.set('expected_user_id',member.telegram_id);
    }
    if(ownerOverviewMode()&&!route.case_attachment&&route.provider==='connect'&&route.channel_id&&route.conversation_id&&member?.tenant_id&&member?.telegram_id){
      query.set('owner_overview','1');query.set('expected_tenant_id',member.tenant_id);query.set('expected_user_id',member.telegram_id);
    }
    return query;
  }
  function timelineHistoryQuery(route,knownPage=null){
    const query=dialogueHistoryQuery(route);
    if(route.provider==='connect'&&route.channel_id&&route.conversation_id&&!route.source_message_id&&member?.tenant_id&&member?.telegram_id){
      if(!route.case_attachment){
        query.set('paired_history','1');query.set('paired_history_redirect','1');
      }
      if(!route.case_attachment)query.set('shared_reminder_events','1');
      if(!(knownPage?.operatorEventsRetryAt>Date.now())){
        query.set('operator_events','1');
        if(Number.isSafeInteger(knownPage?.operator_events_revision))query.set('operator_events_revision',knownPage.operator_events_revision);
      }
      if(query.has('operator_events')||query.has('shared_reminder_events')){
        query.set('expected_tenant_id',member.tenant_id);query.set('expected_user_id',member.telegram_id);
      }
    }
    return query;
  }
  const timelineStamp=value=>Date.parse(value||'');
  const validOperatorEvent=event=>typeof event?.id==='string'&&!!event.id&&['accept','start','transfer','no_reply','close'].includes(event.kind)&&typeof event.at==='string'&&Number.isFinite(timelineStamp(event.at));
  const operatorEventOrder=(a,b)=>timelineStamp(a.at)-timelineStamp(b.at)||a.at.localeCompare(b.at)||a.id.localeCompare(b.id);
  const OPERATOR_TIMELINE_LIMIT=180;
  function noteOperatorEventOmission(page,at){
    if(typeof at==='string'&&Number.isFinite(timelineStamp(at))&&
       (!page.operator_events_omitted_at||timelineStamp(at)>timelineStamp(page.operator_events_omitted_at)))page.operator_events_omitted_at=at;
  }
  function boundedOperatorEvents(page,items){
    const ordered=[...new Map(items.filter(validOperatorEvent).map(event=>[event.id,event])).values()].sort(operatorEventOrder);
    if(ordered.length>OPERATOR_TIMELINE_LIMIT)noteOperatorEventOmission(page,ordered[ordered.length-OPERATOR_TIMELINE_LIMIT].at);
    return ordered.slice(-OPERATOR_TIMELINE_LIMIT);
  }
  const SHARED_REMINDER_TIMELINE_LIMIT=100;
  function retainSharedReminderEvents(page,fresh){
    if(!Array.isArray(fresh.shared_reminder_events))return;
    const events=fresh.shared_reminder_events.filter(event=>typeof event?.id==='string'&&!!event.id&&
      typeof event.at==='string'&&Number.isFinite(timelineStamp(event.at))&&typeof event.note==='string'&&!!event.note.trim())
      .map(({id,at,note})=>({id:'shared-reminder:'+id,at,note,kind:'shared_reminder'}));
    const ordered=[...new Map([...(page.sharedReminderTimeline||[]),...events].map(event=>[event.id,event])).values()].sort(operatorEventOrder);
    for(const at of [fresh.shared_reminder_events_omitted_at,ordered.length>SHARED_REMINDER_TIMELINE_LIMIT?ordered[ordered.length-SHARED_REMINDER_TIMELINE_LIMIT].at:null]){
      if(Number.isFinite(timelineStamp(at))&&(!page.sharedReminderOmission||timelineStamp(at)>timelineStamp(page.sharedReminderOmission)))page.sharedReminderOmission=at;
    }
    page.sharedReminderTimeline=ordered.slice(-SHARED_REMINDER_TIMELINE_LIMIT);
  }
  const operatorEventRetry=delay=>Number.isSafeInteger(delay)&&delay>=250&&delay<=30000;
  function retainOperatorEventRetry(page,fresh){
    const delay=fresh.operator_events_retry_after_ms;
    if(operatorEventRetry(delay))page.operatorEventsRetryAt=Math.max(page.operatorEventsRetryAt||0,Date.now()+delay);
  }
  function retainOperatorEvents(page,fresh,{older=false}={}){
    retainSharedReminderEvents(page,fresh);
    retainOperatorEventRetry(page,fresh);
    if(Number.isSafeInteger(fresh.operatorEventsRetryAt))page.operatorEventsRetryAt=Math.max(page.operatorEventsRetryAt||0,fresh.operatorEventsRetryAt);
    if(!Array.isArray(fresh.operator_events))return;
    noteOperatorEventOmission(page,fresh.operator_events_omitted_at);
    const updates=fresh.operator_events.filter(validOperatorEvent).map(event=>({id:event.id,kind:event.kind,at:event.at,
      employee_name:typeof event.employee_name==='string'?event.employee_name:''}));
    page.operator_events=boundedOperatorEvents(page,[...(page.operator_events||[]),...updates]);
    // An older response must not suppress a new lifecycle revision in polling.
    if(!older&&Number.isSafeInteger(fresh.operator_events_revision))page.operator_events_revision=fresh.operator_events_revision;
  }
  function restoreOperatorEventsWindow(page,known){
    retainOperatorEventRetry(page,page);
    if(!known||Array.isArray(page.operator_events)||!page.dialog?.lifecycle_enabled||page.anchor_message_id)return;
    const oldest=page.next_before?timelineStamp(page.messages?.[0]?.happened_at||page.messages?.[0]?.created_at):-Infinity;
    // Preserve only events that belong to the newly rendered window on a
    // full redraw. The incremental path retains already paged history.
    retainOperatorEvents(page,{operator_events:(known.operator_events||[]).filter(event=>timelineStamp(event.at)>=oldest),
      operator_events_omitted_at:timelineStamp(known.operator_events_omitted_at)>=oldest?known.operator_events_omitted_at:null,
      operator_events_revision:known.operator_events_revision});
    page.operatorEventsRetryAt=Math.max(page.operatorEventsRetryAt||0,known.operatorEventsRetryAt||0);
  }
  function timelineViewportAnchor(chat){
    const top=chat.getBoundingClientRect().top;
    const node=[...chat.children].find(item=>item.dataset.messageId&&item.getBoundingClientRect().bottom>top+1);
    return node?{node,top:node.getBoundingClientRect().top}:null;
  }
  const timelineScrollTop=(saved,anchor)=>saved+(anchor?.node.isConnected?anchor.node.getBoundingClientRect().top-anchor.top:0);
  const timelineScrollMarks=new WeakMap();
  function setTimelineScroll(chat,top,suppressRead){
    chat.scrollTop=top;
    if(!suppressRead)return;
    const mark={top:chat.scrollTop};timelineScrollMarks.set(chat,mark);
    afterLayout(()=>{if(timelineScrollMarks.get(chat)===mark)timelineScrollMarks.delete(chat);});
  }
  function timelineOnlyScroll(chat){
    const mark=timelineScrollMarks.get(chat);
    if(!mark)return false;
    // A single DOM update can queue several scroll events at the same
    // position. Keep suppressing them until layout advances, but allow a
    // user move to another position to clear the guard immediately.
    if(Math.abs(chat.scrollTop-mark.top)<1)return true;
    timelineScrollMarks.delete(chat);
    return false;
  }

  function placeOperatorEvents(chat,page){
    page.operator_events=boundedOperatorEvents(page,page.operator_events||[]);
    if(Array.isArray(page.shared_reminder_events)){
      retainSharedReminderEvents(page,page);
      delete page.shared_reminder_events;
      delete page.shared_reminder_events_omitted_at;
    }
    const events=[...page.operator_events,...(page.sharedReminderTimeline||[])].sort(operatorEventOrder);
    const omissions=[page.operator_events_omitted_at,page.sharedReminderOmission].map(timelineStamp).filter(Number.isFinite);
    const omission=omissions.length?Math.max(...omissions):NaN;
    if(!events.length&&!Number.isFinite(omission))return false;
    const messageTimes=new Map((page.messages||[]).map(m=>[String(m.id),timelineStamp(m.happened_at||m.created_at)]));
    const text={
      ru:{accept:'Диалог принят',start:'Начал(а) диалог',transfer:'Диалог передан',acceptUnknown:'Диалог принят сотрудником',startUnknown:'Диалог начат сотрудником',transferUnknown:'Диалог передан сотруднику'},
      az:{accept:'Dialoq qəbul edildi',start:'Dialoqa başladı',transfer:'Dialoq ötürüldü',acceptUnknown:'Dialoq əməkdaş tərəfindən qəbul edildi',startUnknown:'Dialoq əməkdaş tərəfindən başladıldı',transferUnknown:'Dialoq əməkdaşa ötürüldü'},
      en:{accept:'Dialog accepted',start:'Started dialog',transfer:'Dialog transferred',acceptUnknown:'Dialog accepted by an employee',startUnknown:'Dialog started by an employee',transferUnknown:'Dialog transferred to an employee'}
    }[i18n.language]||{accept:'Dialog accepted',start:'Started dialog',transfer:'Dialog transferred',acceptUnknown:'Dialog accepted by an employee',startUnknown:'Dialog started by an employee',transferUnknown:'Dialog transferred to an employee'};
    const existing=new Map([...chat.children].filter(node=>node.dataset.operatorTimelineEvent).map(node=>[node.dataset.operatorTimelineEvent,node]));
    const kept=new Set(events.map(event=>event.id));for(const [id,node] of existing)if(!kept.has(id))node.remove();
    let omitted=chat.querySelector('[data-operator-timeline-omissions]');
    if(!Number.isFinite(omission))omitted?.remove();
    const messages=[...chat.children].filter(node=>node.dataset.messageId);
    if(!messages.length)for(const child of [...chat.childNodes])if(child.nodeType===3&&child.textContent.trim()===t('emptyHistory'))child.remove();
    for(const event of events){
      const at=timelineStamp(event.at);
      const node=existing.get(event.id)||document.createElement('div');
      if(!node.isConnected){node.className='operator-timeline-event';node.dataset.operatorTimelineEvent=event.id;node.setAttribute('role','note');node.append(document.createElement('span'),document.createElement('time'));}
      const name=typeof event.employee_name==='string'?event.employee_name.trim():'';
      const reminderLabel=({ru:'Напоминание',az:'Xatırlatma',en:'Reminder'})[i18n.language]||'Reminder';
      const statusLabel={
        no_reply:{ru:'Ответ риторический',az:'Cavab ritorikdir',en:'Rhetorical reply'},
        close:{ru:'Диалог завершён',az:'Dialoq tamamlandı',en:'Dialog completed'}
      }[event.kind];
      node.firstElementChild.textContent=event.kind==='shared_reminder'?reminderLabel+': '+event.note:
        statusLabel?(statusLabel[i18n.language]||statusLabel.en):name?text[event.kind]+': '+name:text[event.kind+'Unknown'];
      node.lastElementChild.dateTime=event.at;node.lastElementChild.textContent=i18n.date(event.at);existing.set(event.id,node);
      const next=messages.find(message=>messageTimes.get(message.dataset.messageId)>at);
      if(next)next.before(node);else chat.append(node);
    }
    if(Number.isFinite(omission)){
      if(!omitted){omitted=document.createElement('div');omitted.className='operator-timeline-event';omitted.dataset.operatorTimelineOmissions='true';omitted.setAttribute('role','note');}
      omitted.textContent=({ru:'Часть системных событий не показана',az:'Sistem hadisələrinin bir hissəsi göstərilmir',en:'Some system events are not shown'})[i18n.language]||'Some system events are not shown';
      const nextEvent=events.find(event=>timelineStamp(event.at)>=omission);
      const nextMessage=messages.find(message=>messageTimes.get(message.dataset.messageId)>=omission);
      const next=nextEvent&&(!nextMessage||timelineStamp(nextEvent.at)<messageTimes.get(nextMessage.dataset.messageId))?existing.get(nextEvent.id):nextMessage;
      if(next)next.before(omitted);else chat.append(omitted);
    }
    return true;
  }

  const pageCacheKey=route=>identity(member)+'|'+dialogKey(route);
  const historyMergeId=page=>page?.dialog?.history_merge_id||null;
  const historyMergeChanged=(prior,page)=>!!prior&&sameDialog(prior.dialog,page?.dialog)&&historyMergeId(prior)!==historyMergeId(page);
  function discardHistoryMergeWindow(route,{wipe=false}={}){
    dialogPageCache.delete(pageCacheKey(route));
    helperDrafts.delete(contextKey('helper',route));
    copilotDeferredPage=null;
    document.dispatchEvent(new CustomEvent('tenant-chat-history-reset',{detail:{dropCache:true}}));
    historyUpdateSerial++;
    const quote=$('operatorReplyQuote');
    if(quote){quote.hidden=true;quote.replaceChildren();delete quote.dataset.quoteMessageId;delete quote.dataset.quoteChannelId;delete quote.dataset.quoteConversationId;delete quote.dataset.replyPending;}
    if(wipe){displayedPage=null;currentPage=null;dialogRenderSerial++;renderDialogSkeleton(route);}
  }
  function applyHistoryMergeChange(route,page){
    if(!historyMergeChanged(currentPage,page))return false;
    discardHistoryMergeWindow(route);
    currentDialog=page.dialog;selectedDialog=page.dialog;currentPage=page;
    renderDialog(page,{suppressRead:true,replaceMergedHistory:true});
    return true;
  }
  function cloneDialogPage(page){try{const {operator_events,operator_events_revision,operator_events_omitted_at,operator_events_retry_after_ms,operatorEventsRetryAt,shared_reminder_events,shared_reminder_events_omitted_at,sharedReminderTimeline,sharedReminderOmission,...cacheable}=page;return JSON.parse(JSON.stringify(cacheable));}catch{return null;}}
  function rememberDialogPage(route,page){if(historyMergeId(page)){dialogPageCache.delete(pageCacheKey(route));return;}const copy=cloneDialogPage(page);if(!copy)return;delete copy.scroll_to_latest;if(copy.dialog)delete copy.dialog.compose_draft;const key=pageCacheKey(route);dialogPageCache.delete(key);dialogPageCache.set(key,copy);while(dialogPageCache.size>DIALOG_PAGE_CACHE_LIMIT)dialogPageCache.delete(dialogPageCache.keys().next().value);}
  function cachedDialogPage(route){const key=pageCacheKey(route),page=dialogPageCache.get(key);if(!page||page.dialog?.lifecycle_enabled||!sameDialog(page.dialog,route)){dialogPageCache.delete(key);return null;}dialogPageCache.delete(key);dialogPageCache.set(key,page);return cloneDialogPage(page);}
  function historyMergeRoutes(route,read=false){
    const offered=route?.[read?'history_merge_read_routes':'history_merge_action_routes'];
    if(member?.active!==true||!hasRight('dialogs')||!route?.history_merge_id||route.provider!=='connect'||route.case_attachment||
      !Array.isArray(offered)||offered.length!==2)return [];
    if(read){
      if(!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(route.history_merge_certificate||'')||
        route.search_overview_read_only||offered.some(item=>!['waiting','accepted','closed','dismissed'].includes(item?.state)||typeof item.read_only!=='boolean'))return [];
    }else if((member.role!=='owner'&&route.lifecycle?.state!=='accepted')||route.owner_overview_read_only||route.search_overview_read_only||route.archive_read_only)return [];
    const sources=offered.map(item=>({
      provider:item?.provider,channel_id:item?.channel_id,conversation_id:item?.conversation_id,lead_id:item?.lead_id,
      ...(read?{state:item.state,read_only:item.read_only}:{}),
    }));
    if(sources.some(item=>item.provider!=='connect'||!route.channel_id||item.channel_id!==route.channel_id||
      typeof item.conversation_id!=='string'||!item.conversation_id||!Number.isSafeInteger(item.lead_id)||item.lead_id<1)||
      sources[0].lead_id===sources[1].lead_id||sources[0].conversation_id===sources[1].conversation_id||
      !sources.some(item=>sameDialog(item,route)))return [];
    return sources;
  }
  const historyMergeActionRoutes=route=>historyMergeRoutes(route);
  const historyMergeReadRoutes=route=>historyMergeRoutes(route,true);
  const mergeRouteCopy=()=>{const copy={
    ru:{label:'Диалог для ответа и действий',read:'Открыть диалог',route:'Диалог',waiting:'Ожидает',accepted:'В работе',closed:'Архив',dismissed:'Архив',readOnly:'Только просмотр',unavailable:'Выбранный диалог недоступен.'},
    az:{label:'Cavab və əməliyyat üçün dialoq',read:'Dialoqu aç',route:'Dialoq',waiting:'Gözləyir',accepted:'İşlənir',closed:'Arxiv',dismissed:'Arxiv',readOnly:'Yalnız baxış',unavailable:'Seçilmiş dialoq əlçatan deyil.'},
    en:{label:'Dialogue for replies and actions',read:'Open dialogue',route:'Dialogue',waiting:'Waiting',accepted:'In progress',closed:'Archive',dismissed:'Archive',readOnly:'Read only',unavailable:'The selected dialogue is unavailable.'},
  };return copy[i18n.language]||copy.en;};
  const verifiedMergePages=new WeakSet();
  function renderHistoryMergePicker(slot,route,sources,{read=false}={}){
    const scope=read?'mergeReadScope':'mergeActionScope',field=read?'mergeReadRoute':'mergeActionRoute';
    const projection=read?item=>item?.history_merge_certificate:item=>item?.history_merge_id;
    const authorizedRoutes=read?historyMergeReadRoutes:historyMergeActionRoutes;
    const signature=JSON.stringify([historyMergeId(currentPage),projection(route),dialogKey(route),sources,i18n.language]);
    if(slot.dataset[scope]===signature&&slot.querySelector('select'))return;
    slot.dataset[scope]=signature;
    const copy=mergeRouteCopy(),label=document.createElement('label'),select=document.createElement('select'),status=document.createElement('small');
    label.textContent=read?copy.read:copy.label;
    select.dataset[field]='true';
    status.setAttribute('role','status');
    const selected=sources.findIndex(item=>sameDialog(item,route));
    sources.forEach((item,index)=>{
      const context=read?' · '+copy[item.state]+(item.read_only?' · '+copy.readOnly:''):'';
      const option=new Option(copy.route+' '+(index+1)+context,String(index));option.selected=index===selected;select.add(option);
    });
    select.onchange=async()=>{
      const choice=sources[Number(select.value)],version=epoch,revision=openRevision,readSerial=dialogReadSerial,identityAtStart=identity(member),certificate=projection(route);
      const stillCurrent=()=>version===epoch&&revision===openRevision&&readSerial===dialogReadSerial&&identityAtStart===identity(member)&&
        !pendingDialogRoute&&select.isConnected&&sameDialog(route,currentDialog)&&projection(currentDialog)===certificate&&
        authorizedRoutes(currentDialog).some(item=>sameDialog(item,choice));
      if(!choice||sameDialog(route,choice)||!stillCurrent()){select.value=String(selected);return;}
      select.disabled=true;status.textContent='';
      try{
        const page=await api('dialogs/history?'+timelineHistoryQuery(choice));
        if(!stillCurrent())return;
        const authorized=authorizedRoutes(page?.dialog);
        const floor=lifecycleListStates.get(contextKey('lifecycle-list',choice));
        const freshRevision=page?.dialog?.lifecycle?.revision;
        if(page.combined_dialog||!sameDialog(page.dialog,choice)||projection(page.dialog)!==certificate||
          !authorized.some(item=>sameDialog(item,route))||!authorized.some(item=>sameDialog(item,choice))||
          (floor&&Number.isSafeInteger(freshRevision)&&freshRevision<floor.revision)){
          status.textContent=copy.unavailable;
          void openDialog(route,true,'',{suppressRead:true,revalidateAccess:true,keepExactMergeRoute:read});
          return;
        }
        verifiedMergePages.add(page);
        const switched=await openDialog(choice,false,'',{suppressRead:true,verifiedMergePage:page,keepExactMergeRoute:read});
        if(switched&&read&&sameDialog(choice,currentDialog)&&projection(currentDialog)===certificate)
          $('operatorMergedReadRoutes')?.querySelector('select')?.focus({preventScroll:true});
      }catch(error){if(stillCurrent()){
        status.textContent=copy.unavailable;
        if([401,403,404,409].includes(error.status))void openDialog(route,true,'',{suppressRead:true,revalidateAccess:true,keepExactMergeRoute:read});
      }}
      finally{if(select.isConnected){select.disabled=false;select.value=String(selected);}}
    };
    label.append(select);slot.replaceChildren(label,status);slot.hidden=false;
  }
  function renderHistoryMergeReadPicker(){
    const slot=$('operatorMergedReadRoutes');if(!slot)return;
    const routes=historyMergeReadRoutes(currentDialog);
    if(routes.length)renderHistoryMergePicker(slot,currentDialog,routes,{read:true});
    else if(!slot.hidden){slot.replaceChildren();slot.hidden=true;delete slot.dataset.mergeReadScope;}
  }
  function renderComposerSourcePicker(){
    const slot=$('operatorComposerSource');if(!slot)return;
    const mergeRoutes=historyMergeReadRoutes(currentDialog).length?[]:historyMergeActionRoutes(currentDialog);
    if(mergeRoutes.length){renderHistoryMergePicker(slot,currentDialog,mergeRoutes);return;}
    delete slot.dataset.mergeActionScope;
    const group=rows.find(row=>row.group_sources?.some(source=>sameDialog(source,currentDialog)));
    const sources=activeGroupSources(group),currentAvailable=sources.some(source=>sameDialog(source,currentDialog));
    slot.replaceChildren();slot.hidden=!sources.length||(sources.length<2&&currentAvailable);
    if(slot.hidden)return;
    const label=document.createElement('label');
    label.textContent=({ru:'Ответить через',az:'Cavab mənbəyi',en:'Reply via'})[i18n.language]||'Reply via';
    const select=document.createElement('select');select.id='operatorComposerSourceSelect';
    if(!currentAvailable){const option=document.createElement('option');option.value='';option.textContent=({ru:'Выберите источник',az:'Mənbə seçin',en:'Choose source'})[i18n.language]||'Choose source';option.disabled=true;option.selected=true;select.append(option);}
    sources.forEach((source,index)=>{const option=document.createElement('option');option.value=String(index);option.textContent=source.channel_name;option.selected=sameDialog(source,currentDialog);select.append(option);});
    const original=currentDialog;
    select.onchange=()=>{
      if(!sameDialog(original,currentDialog))return;
      const candidate=sources[Number(select.value)];
      const fresh=rows.flatMap(row=>row.group_sources||[row]).find(source=>candidate&&sameDialog(source,candidate));
      if(!fresh||fresh.channel_active===false){renderComposerSourcePicker();return;}
      if(sameDialog(fresh,currentDialog))return;
      select.disabled=true;
      void openDialog(fresh,false,'',{userOpened:true});
    };
    label.append(select);slot.append(label);
  }
  function renderDialogSkeleton(route){
    const header=$('operatorConversationHeader'),content=$('operatorConversationContent');
    const company=capabilities.dialog_company_binding===true
      ? (route.company_binding?.company_name||route.dialog_company_name||''):'';
    if(header)header.innerHTML=`<button type="button" class="operator-back-to-list" data-operator-back aria-label="${esc(t('backToList'))}" title="${esc(t('backToList'))}">‹</button><div class="operator-conversation-head-copy"><div class="operator-dialog-head"><div class="operator-dialog-title" role="heading" aria-level="2"><span class="operator-dialog-title-text" data-user-content>${esc(dialogTitle(route))}</span></div>${company?`<span id="operatorDialogCompanyName" title="${esc(company)}" data-user-content>${esc(company)}</span>`:''}<div class="operator-dialog-head-row"><span class="operator-agent-label" data-user-content>${esc(assignmentLabel(route))}</span></div></div></div><span class="sub" role="status">${esc(t('loading'))}</span>`;
    if(content)content.innerHTML=`<section class="operator-dialog-shell operator-dialog-loading" role="status" aria-live="polite"><span class="operator-dialog-skeleton-line"></span><span class="operator-dialog-skeleton-line"></span><span class="operator-dialog-skeleton-line"></span><span class="operator-dialog-skeleton-line"></span><span class="operator-dialog-skeleton-line"></span><span class="operator-dialog-skeleton-label">${esc(t('loading'))}</span></section>`;
  }
  async function markDialogReadIfVisible(page=currentPage){
    const route=page?.dialog,chat=$('chatRows'),messages=page?.messages||[],last=messages[messages.length-1],readId=Object.prototype.hasOwnProperty.call(page||{},'read_through_message_id')?page.read_through_message_id:last?.id;
    if(!route||route.archive_read_only||!last?.id||!readId||displayedPage!==page||!chat?.isConnected||document.hidden||!$('view-dialogs')?.classList.contains('active')||!sameDialog(route,currentDialog))return;
    if(!atBottom(chat))return;
    if(page.anchor_message_id&&String(page.anchor_message_id)!==String(last.id))return;
    const owner=identity(member),key=owner+':'+epoch+':'+dialogKey(route);if(readMarkers.get(key)===readId||readInFlight.has(key))return;readInFlight.add(key);
    try{await api('dialogs/read','POST',{provider:route.provider||'connect',lead_id:route.lead_id,channel_id:route.channel_id||'',conversation_id:route.conversation_id||'',message_id:readId,...(route.search_overview_read_only===true?{search_overview:true,search:route.search_overview_query}:{})});readMarkers.set(key,readId);if(owner===identity(member)){try{localStorage.setItem(readSyncKey(),String(Date.now())+':'+crypto.randomUUID());}catch{}listRevision++;listFlight=null;lastListAt=0;if($('view-dialogs')?.classList.contains('active'))void refresh();}}
    catch(error){const status=$('operatorChatError');if(status&&owner===identity(member)&&sameDialog(route,currentDialog))status.textContent=error.message;}
    finally{readInFlight.delete(key);if(readMarkers.get(key)===readId&&owner===identity(member)&&sameDialog(route,currentDialog)){const next=Object.prototype.hasOwnProperty.call(currentPage||{},'read_through_message_id')?currentPage.read_through_message_id:currentPage?.messages?.at(-1)?.id;if(next&&next!==readId)void markDialogReadIfVisible(currentPage);}}
  }
  function messageAuthor(m){if(m.author_type==='ai')return t('aiAgent');if(m.author_type==='operator')return m.participant_jid&&m.author_name&&!['Operator','WhatsApp operator'].includes(m.author_name)?m.author_name:t('operator');if(m.author_type==='employee')return `${m.author_name||t('groupParticipant')} · ${t('employee')}`;if(m.author_type==='participant')return m.author_name||t('groupParticipant');if(m.author_type==='customer')return m.author_name||t('customer');return t('system');}
  function mediaLabel(m){return m.message_type==='image'?t('photo'):['audio','voice'].includes(m.message_type)?t('voiceMessage'):m.message_type==='video'?t('video'):m.message_type==='document'?t('file'):t('attachment');}
  const attachmentName=m=>String(m.media_filename||m.file_name||'').trim();
  const attachmentStem=name=>name.replace(/\.[a-z0-9]{1,12}$/i,'');
  function technicalAttachmentName(name,id){
    return /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(attachmentStem(name))||
      !!id&&(name===id||attachmentStem(name)===id);
  }
  const isAttachmentMessage=m=>m?.source==='connect'&&m.external_id&&!['text','call_recording','call_missed','call_rejected','call_invalidated','call_observed'].includes(m.message_type);
  function attachmentCaption(m){
    const body=String(m.body||''),text=body.trim();
    return text===attachmentName(m)||technicalAttachmentName(text,m.external_id)?'':body;
  }
  function attachmentLabel(m,servedName='',mime=''){
    const stored=attachmentName(m),name=stored||servedName;
    const declared=attachmentStem(servedName).toLowerCase()==='attachment'?'':servedName;
    const original=[stored,declared].find(value=>value&&!technicalAttachmentName(value,m.external_id));
    if(original)return original;
    const type=String(mime||m.media_mime_type||m.media_content_type||'').toLowerCase();
    const extension=(name||attachmentCaption(m)||String(m.body||'')).match(/\.([a-z0-9]+)$/i)?.[1]?.toLowerCase();
    const key=type==='application/pdf'||extension==='pdf'?'pdf':
      m.message_type==='image'||type.startsWith('image/')?'image':
      m.message_type==='video'||type.startsWith('video/')?'video':
      ['audio','voice','voice_message','ptt'].includes(m.message_type)||type.startsWith('audio/')?'audio':'attachment';
    return ({
      ru:{pdf:'Документ PDF',image:'Изображение',video:'Видео',audio:'Голосовое',attachment:'Вложение'},
      az:{pdf:'PDF sənədi',image:'Şəkil',video:'Video',audio:'Səsli mesaj',attachment:'Qoşma'},
      en:{pdf:'PDF document',image:'Image',video:'Video',audio:'Voice message',attachment:'Attachment'}
    }[i18n.language]||{})[key]||mediaLabel(m);
  }
  const quotedMessagePreview=m=>isAttachmentMessage(m)?attachmentCaption(m)||attachmentLabel(m):m.body||mediaLabel(m);
  function mediaAnalysisLabel(message){
    const audio=['audio','voice','voice_message','ptt'].includes(String(message.message_type||'').toLowerCase());
    const state=message.has_transcription?'completed':audio&&(!message.analysis_state||['completed','not_requested'].includes(message.analysis_state))?'no_transcript':message.analysis_state||'not_requested';
    const labels={no_transcript:{ru:'Нет сохранённой транскрипции',az:'Saxlanmış transkripsiya yoxdur',en:'No saved transcript'},file_unavailable:{ru:'Файл не загружен',az:'Fayl yüklənməyib',en:'File could not be loaded'},unsupported:{ru:'Формат не поддерживается',az:'Format dəstəklənmir',en:'Unsupported format'},transcription_unavailable:{ru:'Транскрипция недоступна',az:'Transkripsiya əlçatan deyil',en:'Transcription unavailable'},completed:{ru:'Транскрипция сохранена',az:'Transkripsiya saxlanılıb',en:'Transcript saved'},pending:{ru:'Ожидает AI-анализа',az:'AI təhlilini gözləyir',en:'Awaiting AI analysis'},processing:{ru:'AI-анализ выполняется',az:'AI təhlili aparılır',en:'AI analysis in progress'},failed:{ru:'Ошибка AI-анализа',az:'AI təhlili xətası',en:'AI analysis failed'},disabled:{ru:'AI-анализ отключён',az:'AI təhlili söndürülüb',en:'AI analysis disabled'},not_required:{ru:'AI-анализ не требуется',az:'AI təhlili tələb olunmur',en:'AI analysis not required'},not_requested:{ru:'AI-анализ не запускался',az:'AI təhlili başladılmayıb',en:'AI analysis has not been requested'}};
    labels.visual_completed={ru:'Анализ вложения сохранён',az:'Əlavənin təhlili saxlanılıb',en:'Attachment analysis saved'};
    labels.visual_partial={ru:'Сохранён частичный анализ вложения',az:'Əlavənin qismən təhlili saxlanılıb',en:'Partial attachment analysis saved'};
    return (labels[state]||labels.not_requested)[i18n.language];
  }
  function mediaAction(m){return m.message_type==='image'?t('openPhoto'):['audio','voice'].includes(m.message_type)?t('openAudio'):m.message_type==='video'?t('openVideo'):t('viewAttachment');}
  function duration(value){const seconds=Number(value);if(!Number.isFinite(seconds)||seconds<0)return '';const whole=Math.round(seconds);return String(Math.floor(whole/60)).padStart(1,'0')+':'+String(whole%60).padStart(2,'0');}
  function mediaSize(value){const bytes=Number(value);if(!Number.isSafeInteger(bytes)||bytes<=0)return t('mediaSizeUnknown');if(bytes<1024)return `${bytes} B`;if(bytes<1024*1024)return `${Math.round(bytes/1024)} KB`;return `${(bytes/(1024*1024)).toFixed(1)} MB`;}
  function audioDiagnostics(message){
    if(message.source!=='connect'||!['audio','voice','voice_message','ptt'].includes(String(message.message_type||'').toLowerCase()))return '';
    const copy={ru:{title:'Диагностика аудио',empty:'Нет сохранённой причины анализа',status:'Статус',code:'Код',stage:'Этап',at:'Запись',receipt:'Сохранённый анализ',missingReceipt:'Исходная запись анализа не сохранена',proof:'Сбой до AI в этой записи подтверждён',yes:'Да',no:'Не подтверждён'},az:{title:'Səs diaqnostikası',empty:'Təhlilin səbəbi saxlanmayıb',status:'Status',code:'Kod',stage:'Mərhələ',at:'Qeyd',receipt:'Saxlanmış təhlil',missingReceipt:'İlkin təhlil qeydi saxlanmayıb',proof:'Bu qeyddə AI-dən əvvəl xəta təsdiqlənib',yes:'Bəli',no:'Təsdiqlənməyib'},en:{title:'Audio diagnostics',empty:'No saved analysis reason',status:'Status',code:'Code',stage:'Stage',at:'Record',receipt:'Saved analysis receipt',missingReceipt:'No original analysis receipt saved',proof:'Pre-AI failure of this receipt is proven',yes:'Yes',no:'Not proven'}}[i18n.language]||{title:'Audio diagnostics',empty:'No saved analysis reason',status:'Status',code:'Code',stage:'Stage',at:'Record',receipt:'Saved analysis receipt',missingReceipt:'No original analysis receipt saved',proof:'Pre-AI failure of this receipt is proven',yes:'Yes',no:'Not proven'};
    const diagnostic=message.analysis_diagnostic,receipt=message.analysis_receipt_diagnostic;
    const saved=receipt?`<hr><strong>${esc(copy.receipt)}</strong><div>${esc(copy.status)}: ${esc(receipt.status)}</div><div>${esc(copy.code)}: ${esc(receipt.code)}</div><div>${esc(copy.stage)}: ${esc(receipt.stage)}</div><div>${esc(copy.at)}: ${esc(i18n.date(receipt.at))}</div><div>${esc(copy.proof)}: ${esc(receipt.pre_dispatch_proven===true?copy.yes:copy.no)}</div>`:`<div>${esc(copy.missingReceipt)}</div>`;
    return `<details data-audio-diagnostic data-audio-message-id="${esc(message.id||message.external_id||'')}"><summary>${esc(copy.title)} · ${esc(i18n.date(message.happened_at||message.created_at))}</summary><div>ID: <code>${esc(message.external_id||'')}</code></div>${diagnostic?`<div>${esc(copy.status)}: ${esc(diagnostic.status||'unknown')}</div><div>${esc(copy.code)}: ${esc(diagnostic.code||'unknown')}</div>${mediaAdmissionReason(diagnostic.code)?`<div>${esc(mediaAdmissionReason(diagnostic.code))}</div>`:''}<div>${esc(copy.stage)}: ${esc(diagnostic.stage||'unknown')}</div>${diagnostic.at?`<div>${esc(copy.at)}: ${esc(i18n.date(diagnostic.at))}</div>`:''}`:`<div>${esc(copy.empty)}</div>`}${saved}</details>`;
  }
  function attachmentUrl(item,route,download=false){const leadId=item.source_lead_id||route?.lead_id;if(!leadId||!item.external_id)return '';const guard=route?.case_attachment;const query=new URLSearchParams({lead_id:String(leadId),message_id:String(item.external_id),channel_id:String(item.source_channel_id||route?.channel_id||''),conversation_id:String(item.source_conversation_id||route?.conversation_id||''),expected_tenant_id:String(member?.tenant_id||''),expected_user_id:String(member?.telegram_id||''),...(route?.owner_overview_read_only===true?{owner_overview:'1'}:{}),...(route?.search_overview_read_only===true?{search_overview:'1',search:String(route.search_overview_query||searchValue($('operatorSearch'))).trim()}:{}),...(download?{download:'1'}:{}),...(download&&guard&&String(item.id)===guard.message_id?{case_id:guard.case_id,case_message_id:guard.message_id,case_version:String(guard.version)}:{})});return '/api/platform/crm/chat/attachment?'+query;}
  const photoLoadLabel=()=>({ru:'Нажмите, чтобы загрузить фото',az:'Şəkli yükləmək üçün toxunun',en:'Tap to load photo'}[i18n.language]||'Tap to load photo');
  function photoPreviewMarkup(message){const thumbnail=String(message.media_thumbnail||''),safe=thumbnail.length<=24023&&/^data:image\/(?:jpeg|png|webp);base64,[A-Za-z0-9+/]+=*$/.test(thumbnail);return `${safe?`<img data-media-thumbnail src="${esc(thumbnail)}" alt="">`:`<svg data-photo-placeholder aria-hidden="true" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="2"/><path d="m3 17 6-6 4 4 3-3 5 5"/></svg>`}<span>${esc(t('photo'))}</span><small>${esc(photoLoadLabel())}</small>`;}
  function missedCallLabel(video){return ({ru:video?'Пропущенный видеозвонок':'Пропущенный звонок',az:video?'Buraxılmış videozəng':'Buraxılmış zəng',en:video?'Missed video call':'Missed call'}[i18n.language]||'Missed call');}
  function rejectedCallLabel(video){return ({ru:video?'Отклонённый входящий видеозвонок':'Отклонённый входящий звонок',az:video?'Rədd edilmiş daxil olan videozəng':'Rədd edilmiş daxil olan zəng',en:video?'Rejected incoming video call':'Rejected incoming call'}[i18n.language]||'Rejected incoming call');}
  function observedCallLabel(video){return ({ru:video?'Входящий видеозвонок · результат неизвестен':'Входящий звонок · результат неизвестен',az:video?'Daxil olan videozəng · nəticə məlum deyil':'Daxil olan zəng · nəticə məlum deyil',en:video?'Incoming video call · outcome unknown':'Incoming call · outcome unknown'}[i18n.language]||'Incoming call · outcome unknown');}
  function invalidatedCallLabel(video){return ({ru:video?'Статус видеозвонка уточнён':'Статус звонка уточнён',az:video?'Videozəngin statusu dəqiqləşdirildi':'Zəngin statusu dəqiqləşdirildi',en:video?'Video call status corrected':'Call status corrected'}[i18n.language]||'Call status corrected');}
  function callRecordingCopy(){const labels={
    ru:{inbound:'Входящий звонок WhatsApp',outbound:'Исходящий звонок WhatsApp',unknown:'Звонок WhatsApp',gaps:'Возможны пропуски аудио',expired:'Срок хранения записи истёк',archived:'Привязано владельцем к архивному диалогу',source:'Источник'},
    az:{inbound:'Daxil olan WhatsApp zəngi',outbound:'Gedən WhatsApp zəngi',unknown:'WhatsApp zəngi',gaps:'Səsdə boşluqlar ola bilər',expired:'Yazının saxlanma müddəti bitib',archived:'Sahib tərəfindən arxiv dialoquna bağlanıb',source:'Mənbə'},
    en:{inbound:'Incoming WhatsApp call',outbound:'Outgoing WhatsApp call',unknown:'WhatsApp call',gaps:'Audio may contain gaps',expired:'Recording has expired',archived:'Linked to an archived conversation by the owner',source:'Source'}
  };return labels[i18n.language]||labels.en;}
  const isDirectConnectDialog=route=>route?.provider==='connect'&&route.customer_kind==='contact'&&route.conversation_kind!=='group'&&!/@g[.]us$/i.test(route.conversation_id||'');
  const reactionEmoji=['👍','❤️','😂','😮','😢','🙏'];
  const reactionEndpoint='/api/platform/crm/chat/reaction';
  const reactionCopy=key=>({
    ru:{action:'Реакция',peers:'Реакции собеседников',remove:'Снять реакцию',check:'Проверить реакцию',unknown:'Результат отправки реакции пока неизвестен.',failed:'Реакция недоступна для этого сообщения.'},
    az:{action:'Reaksiya',peers:'Digər iştirakçıların reaksiyaları',remove:'Reaksiyanı sil',check:'Reaksiyanı yoxla',unknown:'Reaksiyanın göndərilmə nəticəsi hələ məlum deyil.',failed:'Bu mesaj üçün reaksiya əlçatan deyil.'},
    en:{action:'Reaction',peers:'Reactions from others',remove:'Remove reaction',check:'Check reaction',unknown:'The reaction is not confirmed yet.',failed:'Reactions are unavailable for this message.'}
  }[i18n.language]||{})[key]||key;
  function reactionMarkup(message){
    const own=message.wa_reaction;
    const peers=message.wa_peer_reactions||[];
    if(!message.wa_reaction_available&&!own&&!peers.length)return '';
    return `<div class="operator-reaction-row" data-whatsapp-reactions ${own||peers.length?'':'hidden'}><span data-whatsapp-reaction aria-label="${esc(reactionCopy('action'))}" ${own?'':'hidden'}>${esc(own||'')}</span>${peers.map(({emoji,count})=>`<span class="operator-peer-reaction" title="${esc(reactionCopy('peers'))}">${esc(emoji)}${count>1?` <small>${esc(count)}</small>`:''}</span>`).join('')}</div>`;
  }
  function paintReactions(article,message){
    if(!article)return;
    const row=article.querySelector('[data-whatsapp-reactions]');
    const markup=reactionMarkup(message);
    if(row){row.insertAdjacentHTML('beforebegin',markup);row.remove();}
    else if(markup)article.querySelector('footer')?.insertAdjacentHTML('beforebegin',markup);
  }
  function messageMarkup(items,route=currentDialog,{readOnly=false}={}){readOnly=readOnly||route?.owner_overview_read_only===true||route?.search_overview_read_only===true;return items.map((m,index)=>{
    if(m.message_type==='call_recording'&&m.call_recording){
      const recording=m.call_recording,query=new URLSearchParams({expected_tenant_id:String(member?.tenant_id||''),expected_user_id:String(member?.telegram_id||'')}),expired=Date.parse(recording.expires_at)<=Date.now();
      const copy=callRecordingCopy(),title=['inbound','outbound'].includes(recording.direction)?copy[recording.direction]:copy.unknown;
      const calledAt=m.happened_at||m.created_at,callTime=Number.isFinite(Date.parse(calledAt))?`<footer class="operator-message-meta"><time datetime="${esc(new Date(calledAt).toISOString())}">${esc(i18n.date(calledAt))}</time></footer>`:'';
      return `<article class="operator-message operator-message-system" data-message-id="${esc(m.id)}"><strong>${esc(title)}</strong>${expired?`<p>${esc(copy.expired)}</p>`:`<audio data-test-call-audio style="display:block;max-width:100%;margin-top:8px" controls preload="none" src="/api/platform/test-call-recordings/${encodeURIComponent(recording.id)}?${esc(query)}"></audio>${recording.complete?'':`<p>${esc(copy.gaps)}</p>`}`}${recording.archived_owner_binding?`<small>${esc(copy.archived)}${recording.account_number?` · ${esc(copy.source)} +${esc(recording.account_number)}`:''}</small>`:''}${callTime}</article>`;
    }
    const rejected=m.message_type==='call_rejected',invalidated=m.message_type==='call_invalidated',observed=m.message_type==='call_observed';
    const missed=m.message_type==='call_missed'||rejected||invalidated||observed;
    const media=m.source==='connect'&&!missed&&m.message_type!=='text'&&m.external_id;
    const transcript=m.has_transcription&&(m.audio_transcript||m.body)?`<details data-audio-transcript><summary>${t('showTranscript')}</summary><div data-user-content>${esc(m.audio_transcript||m.body)}</div></details>`:'';
    const participantMenu=!readOnly&&m.author_type==='participant'&&m.participant_jid;
    const author=participantMenu?`<span class="operator-participant-name" data-participant-menu="${esc(m.id)}" data-participant-jid="${esc(m.participant_jid)}" role="button" tabindex="0" aria-label="${esc(t('editParticipantName'))}" data-user-content>${esc(messageAuthor(m))}</span>`:`<span data-user-content${isDirectConnectDialog(route)&&m.source==='connect'&&m.direction==='incoming'&&m.author_type==='customer'?' data-direct-message-author':''}>${esc(messageAuthor(m))}</span>`;const reply=!missed&&!readOnly&&route?.provider==='connect'&&!route.wecall&&m.id&&['incoming','outgoing'].includes(m.direction)?`<button type="button" class="operator-reply-action" data-reply-message="${esc(m.id)}" aria-label="${esc(replyText('action'))}" title="${esc(m.provider_message_id?replyText('action'):replyText('unavailable'))}"><span aria-hidden="true">${m.direction==='outgoing'?'→':'←'}</span></button>`:'';
    const directAudio=media&&['audio','voice','voice_message','ptt'].includes(String(m.message_type||'').toLowerCase());
    const fileActuators=media?`${readOnly?'':`<button type="button" data-media-menu-load>${esc(t('loadAttachment'))}</button>`}<button type="button" data-media-download="${esc(attachmentUrl(m,route,true))}">${esc(t('downloadAttachment'))}</button>`:'';
    const audioActions=directAudio?`<div data-media-actions ${readOnly?'':'hidden'}>${fileActuators}${m.id&&!route?.owner_overview_read_only?`<div data-audio-transcription-menu data-message-row-id="${esc(m.id)}" data-message-id="${esc(m.external_id)}" hidden><span data-audio-transcription-status role="status" aria-live="polite"></span><div data-audio-transcription-actions></div></div>`:''}</div>`:'';
    const mediaActions=media&&!directAudio?`<div data-media-actions ${readOnly?'':'hidden'}>${fileActuators}</div>`:'';
    const knownSize=m.media_size_bytes!==undefined&&m.media_size_bytes!==null&&m.media_size_bytes!==''&&Number.isSafeInteger(Number(m.media_size_bytes))&&Number(m.media_size_bytes)>0;
    const mediaMeta=!knownSize?'':mediaSize(m.media_size_bytes);
    const caption=media?attachmentCaption(m):m.body;
    const label=media?attachmentLabel(m):'';
    const original=m.quoted_provider_message_id?(readOnly?items:currentPage?.messages||items).find(item=>item.provider_message_id===m.quoted_provider_message_id&&String(item.source_channel_id||route.channel_id)===String(m.source_channel_id||route.channel_id)):null;const quote=m.quoted_provider_message_id?`<button type="button" class="operator-quote" data-quote-jump="${esc(m.quoted_provider_message_id)}">${esc(original?(messageAuthor(original)+' · '+String(quotedMessagePreview(original)).slice(0,160)):({ru:'Исходное сообщение не загружено',az:'Orijinal mesaj yüklənməyib',en:'Original message is not loaded'}[i18n.language]||'Original message is not loaded'))}</button>`:'';
    const content=missed?`<div ${observed?'data-observed-call':invalidated?'data-invalidated-call':rejected?'data-rejected-call':'data-missed-call'}>${esc(observed?observedCallLabel(m.call_is_video===true):invalidated?invalidatedCallLabel(m.call_is_video===true):rejected?rejectedCallLabel(m.call_is_video===true):missedCallLabel(m.call_is_video===true))}</div>`:media?`${caption&&!m.has_transcription?`<div class="operator-message-caption" data-user-content>${esc(caption)}</div>`:''}<div class="operator-media-card" ${directAudio?'data-native-audio="true" data-chat-attachment="'+esc(m.external_id)+'" data-connect-media="true" data-attachment-lead-id="'+esc(m.source_lead_id||'')+'" data-channel-id="'+esc(m.source_channel_id||route.channel_id)+'" data-conversation-id="'+esc(m.source_conversation_id||route.conversation_id)+'" data-media-kind="'+esc(m.message_type)+'"':'data-chat-attachment="'+esc(m.external_id)+'" data-connect-media="true" data-attachment-lead-id="'+esc(m.source_lead_id||'')+'" data-channel-id="'+esc(m.source_channel_id||route.channel_id)+'" data-conversation-id="'+esc(m.source_conversation_id||route.conversation_id)+'" data-media-kind="'+esc(m.message_type)+'"'} data-message-row-id="${esc(m.id||'')}" data-media-filename="${esc(m.media_filename||m.file_name||'')}" data-media-version="${esc(m.media_version||'')}" data-media-duration="${esc(m.duration_seconds||'')}">${directAudio?`<div class="operator-audio-heading"><span data-media-title>${esc(label)}${Number.isFinite(Number(m.duration_seconds))&&Number(m.duration_seconds)>0?` <span class="operator-audio-duration">· ${esc(duration(m.duration_seconds))}</span>`:``}</span><span class="operator-media-file-meta" data-media-meta>${esc(mediaMeta)}</span></div><div class="operator-audio-controls" aria-disabled="true" inert><audio class="operator-native-audio" controls preload="none" tabindex="-1" aria-label="${esc(label)}"></audio></div><button type="button" data-media-open hidden>${esc(t('loadAttachment'))}</button>${audioActions}`:`<span data-media-title>${esc(label)}</span><button type="button" class="outline" data-media-open ${m.message_type==='image'?`data-photo-preview aria-label="${esc(photoLoadLabel())}"`:``}>${m.message_type==='image'?photoPreviewMarkup(m):esc(mediaAction(m))}</button>${mediaActions}`}<span data-media-load-status role="status" aria-live="polite"></span>${directAudio||m.message_type==='image'?`<details class="${directAudio?'operator-audio-analysis':'operator-photo-analysis'}"><summary>${esc(({ru:'AI-анализ',az:'AI təhlili',en:'AI analysis'})[i18n.language]||'AI analysis')}</summary><span class="operator-media-analysis sub">${esc(mediaAnalysisLabel(m))}</span></details>`:`<span class="operator-media-analysis sub">${esc(mediaAnalysisLabel(m))}</span>`}${directAudio?'':`<span class="operator-media-file-meta" data-media-meta>${esc(mediaMeta)}</span>`}</div>`:`<div data-user-content>${esc(m.body||t('attachment'))}</div>`;
    return `<article class="operator-message" ${readOnly?'':'tabindex="0" aria-haspopup="menu"'} data-direction="${esc(m.direction)}" data-message-index="${index}" data-message-id="${esc(m.id||m.external_id||'')}" data-provider-message-id="${esc(m.provider_message_id||'')}" ${m.customer_voice_status===true?`data-customer-voice-status="true" data-customer-voice-lead="${esc(m.source_lead_id||route?.lead_id||'')}"`:''}>${quote}${content}${transcript}${reactionMarkup(m)}<footer class="operator-message-meta"><span class="operator-message-origin">${author}<span>·</span><time>${esc(i18n.date(m.happened_at||m.created_at))}</time>${m.sources?.length?`<span data-user-content>${esc([...new Set(m.sources.map(s=>s.channel_name+' · '+t(s.direction==='outgoing'?'sentVia':'receivedVia')))].join(' · '))}</span>`:''}</span><span class="operator-message-actions" data-message-footer-actions>${deliveryMarkup(m)}${copyControlMarkup(m)}</span></footer>${reply}</article>`;
  }).join('');}

  // The case history uses the same presentation without activating a composer or dialogue side effects.
  function bindHistoryMessages(host,messages,isCurrent){
    host.onclick=async event=>{
      if(!isCurrent())return;
      const jump=event.target.closest('[data-quote-jump]');if(jump){const target=[...host.querySelectorAll('[data-provider-message-id]')].find(node=>node.dataset.providerMessageId===jump.dataset.quoteJump);target?.scrollIntoView?.({block:'center'});return;}
      const button=event.target.closest('[data-copy-message]');if(!button)return;
      const message=messages.find(row=>String(row.id||row.external_id)===button.dataset.copyMessage),text=messageCopyText(message),status=button.parentElement.querySelector('[data-copy-status]');if(!text)return;
      let copied=false;try{if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(text);copied=true;}else if(document.execCommand){const input=document.createElement('textarea');input.value=text;input.style.cssText='position:fixed;opacity:0;pointer-events:none';document.body.append(input);input.select();copied=document.execCommand('copy');input.remove();}}catch{}
      if(isCurrent()&&button.isConnected)status.textContent=replyText(copied?'copied':'copyFailed');
    };
  }
  function rememberDialogHistory(route){
    if(!$('view-dialogs')?.classList.contains('active'))return;
    const mobile=!!window.matchMedia?.('(max-width: 900px)').matches;
    const dialog={provider:route.provider||'connect',lead_id:route.lead_id,channel_id:route.channel_id,conversation_id:route.conversation_id};
    const state={...history.state,view:'dialogs',operatorDialog:dialog,operatorDialogOwner:identity(member),operatorDialogHistoryEntry:history.state?.operatorDialog?history.state.operatorDialogHistoryEntry!==false:mobile};if(state.operatorHelper!==contextKey('helper',route))delete state.operatorHelper;
    if(!mobile||history.state?.operatorDialog)history.replaceState(state,'');else history.pushState(state,'');
  }
  async function openDialog(route,fromHistory=false,caseLocationKey='',options={}){
    if(!hasRight('dialogs')){notice(t('noAccess'));return false;}
    if(typeof route.compose_draft==='string'&&route.compose_draft.trim()){try{sessionStorage.setItem(contextKey('incoming-case-draft',route),route.compose_draft);}catch{notice(manualCaseCopy('storage'));return false;}}
    route={...route};delete route.compose_draft;
    if(route.search_overview_read_only===true&&!route.search_overview_query)route.search_overview_query=String(searchValue($('operatorSearch'))).trim();
    if(currentDialog&&!sameDialog(currentDialog,route))closeDialog(false);
    const retained=!!displayedPage&&sameDialog(currentDialog,route)&&!!$('chatRows'),revision=retained?openRevision:++openRevision,readSerial=++dialogReadSerial,version=epoch;
    const knownTimeline=retained&&!route.source_message_id&&sameDialog(displayedPage?.dialog,route)?displayedPage:null;
    const verifyingDisplayed=options.revalidateAccess&&retained&&!pendingDialogRoute;
    if(!verifyingDisplayed){
      cancelCaseLink();
      if(!retained)closeDialogDiagnostics(false);
      pendingDialogRoute=route;
    }
    const workspace=$('operatorWorkspace'),operatorHost=$('operatorDialogs'),content=$('operatorConversationContent');
    currentDialog={...route};selectedDialog=currentDialog;selectedDialogOwner=identity(member);
    if(!fromHistory)rememberDialogHistory(route);
    try{sessionStorage.setItem(contextKey('route',route),JSON.stringify({provider:route.provider||'connect',lead_id:route.lead_id,channel_id:route.channel_id,conversation_id:route.conversation_id}));}catch{}
    if(workspace)workspace.dataset.selected='true';if(operatorHost)operatorHost.dataset.conversationOpen='true';
    const cached=retained||route.lifecycle_enabled?null:cachedDialogPage(route);
    if(cached){cached.dialog={...cached.dialog,dialog_company_name:route.dialog_company_name,...(route.case_attachment?{case_attachment:route.case_attachment}:{})};delete cached.dialog.company_binding;currentDialog=cached.dialog;selectedDialog=cached.dialog;currentPage=cached;renderRows();renderDialog(cached);}
    else if(!retained){currentPage=null;renderRows();renderDialogSkeleton(route);}
    content?.setAttribute('aria-busy','true');notice('');
    try{const page=verifiedMergePages.delete(options.verifiedMergePage)?options.verifiedMergePage:await api('dialogs/history?'+timelineHistoryQuery(route,knownTimeline));if(version!==epoch||revision!==openRevision||readSerial!==dialogReadSerial)return false;if(page.combined_dialog){
      const combined=page.combined_dialog;
      if(options.keepExactMergeRoute||historyMergeReadRoutes(route).length||options.followedCombinedDialog||route.case_attachment||route.source_message_id||combined.provider!=='connect'||combined.channel_id!==route.channel_id||sameDialog(combined,route))throw new Error(t('error'));
      return openDialog(combined,false,'',{...options,followedCombinedDialog:true});
    }if(!sameDialog(page.dialog,route))throw new Error(t('error'));pendingDialogRoute=null;const replaceMergedHistory=historyMergeChanged(displayedPage,page);if(replaceMergedHistory)discardHistoryMergeWindow(route);const lifecycleFloor=lifecycleListStates.get(contextKey('lifecycle-list',route)),incomingRevision=page.dialog.lifecycle?.revision,validRevision=Number.isSafeInteger(incomingRevision)&&incomingRevision>=0;if(lifecycleFloor&&validRevision&&incomingRevision<lifecycleFloor.revision){if(replaceMergedHistory)discardHistoryMergeWindow(route,{wipe:true});return false;}if(page.dialog.lifecycle_enabled&&!validRevision){const state=page.dialog.lifecycle?.state;page.dialog={...page.dialog,can_send:false,lifecycle:{state}};}if(!Object.prototype.hasOwnProperty.call(page.dialog,'phone')&&sameDialog(currentDialog,page.dialog)&&page.dialog.customer_kind!=='group'&&page.dialog.conversation_kind!=='group'&&verifiedDialogPhone(currentDialog))page.dialog.phone=currentDialog.phone;if(route.search_overview_read_only===true)page.dialog.search_overview_query=route.search_overview_query;currentDialog={...page.dialog,...(route.case_attachment?{case_attachment:route.case_attachment}:{})};page.dialog=currentDialog;updateLifecycleList(currentDialog,currentDialog.lifecycle);if(caseLocationKey)consumeCaseLocation(caseLocationKey);selectedDialog=page.dialog;selectedDialogOwner=identity(member);if(!replaceMergedHistory)restoreOperatorEventsWindow(page,knownTimeline);else currentPage=null;retainDeliveryEvidence(currentPage,page);currentPage=page;page.scroll_to_latest=Number(route.unread_count)>0&&!page.anchor_message_id;rememberDialogPage(route,page);renderRows();renderDialog(page,{suppressRead:options.suppressRead===true,replaceMergedHistory});notice('');if(options.userOpened&&!currentDialog.archive_read_only)window.tenantSharedReminders?.dialogOpened(currentDialog);const markRead=()=>{if(!options.suppressRead&&version===epoch&&revision===openRevision&&readSerial===dialogReadSerial&&currentPage===displayedPage&&sameDialog(route,currentDialog))void markDialogReadIfVisible(currentPage);};if(window.matchMedia?.('(max-width: 900px)').matches)afterLayout(markRead);else markRead();return true;}
    catch(error){if(version===epoch&&revision===openRevision&&readSerial===dialogReadSerial){if(lifecycleUnavailable(error,route))return false;
      if(historyMergeId(displayedPage)&&sameDialog(displayedPage.dialog,route))discardHistoryMergeWindow(route,{wipe:true});
      if(options.revalidateAccess&&error.status===404){removeLifecycleDialog(route);notice(error.message);return false;}
      if(error.message!=='Request scope changed')pendingDialogRoute=null;notice(error.message);const status=$('operatorChatError');if(status)status.textContent=error.message;else if(content)content.innerHTML=`<section class="operator-dialog-shell" role="status"><p class="operator-error">${esc(error.message)}</p></section>`;document.dispatchEvent(new CustomEvent('tenant-operator-error',{detail:{message:error.message}}));}return false;}
    finally{if(version===epoch&&revision===openRevision&&readSerial===dialogReadSerial)$('operatorConversationContent')?.setAttribute('aria-busy','false');}
  }
  const clientCardText=key=>({
    ru:{companyPanel:'Данные компании',sourceDisabled:'Источник отключён',phone:'Номер клиента',company:'Компания',title:'Данные клиента',group:'Группа',dialog:'Диалог',acceptedBy:'Диалог принял',source:'Источник',transfer:'Передача',delegated:'Передано сотруднику',staff:'Сотрудник',saved:'Имя сохранено',close:'Закрыть',resize:'Изменить ширину панели'},
    az:{companyPanel:'Şirkət məlumatları',sourceDisabled:'Mənbə deaktivdir',phone:'Müştərinin nömrəsi',company:'Şirkət',title:'Müştəri məlumatları',group:'Qrup',dialog:'Dialoq',acceptedBy:'Dialoqu qəbul edən',source:'Mənbə',transfer:'Ötürülmə',delegated:'Əməkdaşa ötürülüb',staff:'Əməkdaş',saved:'Ad yadda saxlanıldı',close:'Bağla',resize:'Panelin enini dəyiş'},
    en:{companyPanel:'Company details',sourceDisabled:'Source disabled',phone:'Customer phone',company:'Company',title:'Customer details',group:'Group',dialog:'Conversation',acceptedBy:'Accepted by',source:'Source',transfer:'Transfer',delegated:'Handed off to staff',staff:'Employee',saved:'Name saved',close:'Close',resize:'Resize panel'}
  }[i18n.language]||{})[key]||'';
  let clientCardDispose=()=>{},refreshClientSupportCase=()=>{};
  const pendingClientNames=new Set();
  const clientCardStyle=document.createElement('style');clientCardStyle.textContent=`
    #operatorConversationLayout{position:relative;display:flex;flex:1;min-width:0;min-height:0;overflow:hidden}
    #operatorConversationLayout>.operator-conversation-content{min-width:0}
    #operatorConversationHeader:has(#operatorMoreActions[open]){position:relative;z-index:11}
    @media(min-width:901px){.operator-conversation-pane:has(#operatorConversationLayout > #operatorCompanyPanel[open]){display:grid;grid-template-columns:minmax(0,1fr) 8px var(--operator-company-panel-width,clamp(260px,30%,460px));grid-template-rows:auto minmax(0,1fr)}.operator-conversation-pane:has(#operatorConversationLayout > #operatorCompanyPanel[open])>#operatorConversationHeader{grid-column:1;grid-row:1}.operator-conversation-pane:has(#operatorConversationLayout > #operatorCompanyPanel[open])>#operatorConversationLayout{display:contents}.operator-conversation-pane:has(#operatorConversationLayout > #operatorCompanyPanel[open]) #operatorConversationContent{grid-column:1;grid-row:2;min-width:0;min-height:0}.operator-conversation-pane:has(#operatorConversationLayout > #operatorCompanyPanel[open]) #operatorCompanyPanelResize{grid-column:2;grid-row:1/3}.operator-conversation-pane:has(#operatorConversationLayout > #operatorCompanyPanel[open]) #operatorCompanyPanel{grid-column:3;grid-row:1/3;width:100%;height:100%;max-height:none;align-self:stretch}}
    #operatorCompanyPanelResize{display:none;box-sizing:border-box;position:relative;flex:0 0 8px;width:8px;min-width:8px;min-height:0;padding:0;border:0;border-radius:0;background:transparent;touch-action:none;cursor:col-resize;z-index:2}
    #operatorCompanyPanelResize::before{content:'';position:absolute;inset:0 auto 0 3px;width:2px;background:var(--line);transition:background-color .12s ease}
    #operatorCompanyPanelResize:hover::before,#operatorCompanyPanelResize:focus-visible::before,#operatorCompanyPanelResize[data-dragging="true"]::before{background:var(--brand)}
    #operatorCompanyPanelResize:focus-visible{outline:0}
    #operatorConversationLayout:has(> #operatorCompanyPanel[open])>#operatorCompanyPanelResize{display:block}
    #operatorConversationHeader .operator-dialog-head-main{display:flex;align-items:center;flex-wrap:nowrap;min-width:0;gap:8px}
    #operatorConversationHeader .operator-dialog-head-identity{display:flex;flex-direction:column;justify-content:center;gap:2px;flex:1;min-width:0}
    #operatorConversationHeader #operatorDialogTitleSlot{min-width:0;max-width:none;min-height:22px}
    #operatorConversationHeader .operator-header-meta{display:flex;align-items:center;gap:4px;min-width:0;min-height:14px;color:var(--muted);font-size:12px;line-height:14px}
    #operatorConversationHeader .operator-header-meta:empty{display:none}
    #operatorConversationHeader .operator-client-phone,#operatorConversationHeader .operator-client-source{display:block;min-width:0;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    #operatorConversationHeader .operator-client-source{flex:0 1 auto}
    #operatorDialogCompanyName{display:block;align-self:center;flex:0 1 auto;width:max-content;min-width:0;max-width:min(180px,38%);box-sizing:border-box;padding-inline:5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;border-radius:5px;font-size:10px;font-weight:600;line-height:11px;color:#1d4ed8;background:#e8f1ff}
    #operatorDialogCompanyName[hidden]{display:none!important}
    [data-theme="dark"] #operatorDialogCompanyName{color:#bfdbfe;background:#1e3a5f}
    #operatorClientCardOpen,#operatorDialogTitleText{box-sizing:border-box;border:0;background:transparent;color:inherit;cursor:pointer;padding:0}
    #operatorClientCardOpen{display:grid;place-items:center;flex:none;width:44px;min-width:44px;height:44px;min-height:44px;margin:0;border-radius:8px}
    #operatorClientCardOpen .operator-header-avatar{margin:0;width:40px;height:40px;font-size:17px}
    #operatorClientCardOpen:focus-visible,#operatorDialogTitleText:focus-visible{outline:2px solid var(--brand);outline-offset:-2px}
    #operatorDialogTitleText{font:inherit;text-align:start;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;border-radius:5px;margin:0}
    .operator-history-tools{display:flex;align-items:center;gap:4px;flex:none;padding-inline-end:12px}
    .operator-history-tools button[hidden]{display:none}
    .operator-history-tools #operatorCompanyPanelOpen{display:grid;flex:none;place-items:center;margin:4px 0 4px auto;width:34px;height:34px;min-height:34px;padding:0;color:var(--muted)}
    .operator-history-tools #operatorCompanyPanelOpen[hidden]{display:none!important}
    .operator-history-tools #operatorCompanyPanelOpen:hover,.operator-history-tools #operatorCompanyPanelOpen[aria-expanded="true"]{color:var(--brand);background:var(--paper)}@media(min-width:901px){.operator-history-tools #operatorCompanyPanelOpen{display:none!important}}
    :is(#operatorClientCard,#operatorCompanyPanel){box-sizing:border-box;min-height:0;padding:0;background:var(--card);color:var(--ink);overflow:hidden;text-align:start}
    :is(#operatorClientCard,#operatorCompanyPanel):not([open]){display:none!important}
    :is(#operatorClientCard,#operatorCompanyPanel)[open]{display:flex;flex-direction:column}
    #operatorClientCard{position:fixed;inset:auto;inset-inline-start:50%;top:var(--operator-client-center,50%);transform:translate(-50%,-50%);width:min(420px,calc(100% - 24px));max-height:min(640px,calc(var(--operator-client-viewport-height,100dvh) - 24px));margin:0;border:1px solid var(--line);border-radius:15px;box-shadow:0 14px 52px #0003}
    #operatorClientCard::backdrop{background:#13223455}
    #operatorCompanyPanel{position:static;inset:auto;margin:0;width:var(--operator-company-panel-width,clamp(260px,30%,460px));max-width:none;max-height:var(--operator-client-available-height,100dvh);flex:0 0 var(--operator-company-panel-width,clamp(260px,30%,460px));align-self:stretch;border:0;border-bottom-left-radius:0;border-bottom-right-radius:0}
    /* The desktop pane clips its children to its own radius. Keep the other three corners unchanged. */
    .operator-conversation-pane:has(#operatorConversationLayout > #operatorCompanyPanel[open]){border-bottom-right-radius:0}
    :is(#operatorClientCard,#operatorCompanyPanel) .operator-client-panel-header{flex:none;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 14px;border-bottom:1px solid var(--line);background:var(--card)}
    :is(#operatorClientCard,#operatorCompanyPanel) h2{margin:0;font-size:1.05rem;line-height:1.3}
    #operatorCompanyPanel h2{font-size:.94rem}
    #operatorCompanyPanel #operatorCompanyPanelClose{display:none}
    #operatorCompanyPanel .operator-client-panel-content:has(#operatorCompanyView-main[hidden]) #operatorSidebarTransfer,
    #operatorClientCompanyHost:empty,#operatorSidebarTransfer [data-transfer-retry][hidden],#operatorSidebarTransfer [role=status]:empty{display:none!important}
    #operatorSidebarTransfer{display:grid;gap:6px;border-block-start:1px solid var(--line);padding-block-start:8px;font-size:.8rem}
    #operatorSidebarTransfer summary{min-height:36px;cursor:pointer;padding:8px;color:var(--brand);border:1px solid var(--line);border-radius:8px}
    #operatorSidebarTransfer [data-transfer-targets]{display:grid;gap:4px;max-height:190px;overflow:auto}
    #operatorSidebarTransfer [data-transfer-targets] button{margin-top:4px;width:100%;min-height:40px;text-align:start}
    #operatorSidebarTransfer p{margin:5px 0;color:var(--muted)}
    #operatorCompanyPanelSaveStatus:empty{display:none}
    #operatorCompanyPanelSaveStatus{margin:0;color:var(--muted);font-size:.75rem}
    #operatorCancelConversationName{border:0;background:transparent;font-size:23px;min-width:40px;min-height:40px;padding:0}
    :is(#operatorClientCard,#operatorCompanyPanel) .operator-client-panel-content{min-height:0;flex:1 1 auto;display:grid;grid-auto-rows:max-content;align-content:start;gap:10px;padding:12px;overflow:auto;overscroll-behavior:contain;scrollbar-gutter:stable}
    #operatorClientCard .operator-client-panel-block{min-width:0;border:1px solid var(--line);border-radius:10px;overflow:hidden}
    #operatorClientCard .operator-client-panel-block>summary{display:flex;align-items:center;gap:8px;padding:9px 11px;font-weight:650;cursor:pointer;background:var(--paper);list-style:none}
    #operatorClientCard .operator-client-panel-block>summary::-webkit-details-marker{display:none}
    #operatorClientCard .operator-client-panel-block>summary::before{content:'';width:7px;height:7px;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:rotate(-45deg);margin-inline-end:2px}
    #operatorClientCard .operator-client-panel-block[open]>summary::before{transform:rotate(45deg)}
    :is(#operatorClientCard,#operatorCompanyPanel) .operator-client-panel-row{display:grid;gap:3px;min-width:0;padding:6px 11px;border-top:1px solid var(--line);overflow-wrap:anywhere;font-size:13px;line-height:1.35}
    :is(#operatorClientCard,#operatorCompanyPanel) .operator-client-panel-row small{color:var(--muted);font-size:11px}
    :is(#operatorClientCard,#operatorCompanyPanel) .operator-client-panel-row:empty{display:none}
    :is(#operatorClientCard,#operatorCompanyPanel) .operator-client-panel-row[hidden]{display:none!important}
    #operatorClientCard form{display:grid;gap:8px;margin:0}
    :is(#operatorClientCard,#operatorCompanyPanel) label{display:grid;gap:5px;min-width:0}
    :is(#operatorClientCard,#operatorCompanyPanel) input,:is(#operatorClientCard,#operatorCompanyPanel) select{box-sizing:border-box;width:100%;min-width:0;height:34px;min-height:34px;margin:0;padding:4px 9px;border:1px solid var(--line);border-radius:8px;background:var(--paper);color:var(--ink);font:inherit;font-size:16px}
    #operatorClientCard .operator-display-name-field{position:relative;display:block;min-width:0}
    #operatorClientCard #operatorConversationNameInput{padding-inline-end:27px}
    #operatorClientCard [data-client-name-saved]{position:absolute;inset-inline-end:8px;top:50%;transform:translateY(-50%);color:var(--brand,#168456);font-weight:700;pointer-events:none}
    :is(#operatorClientCard,#operatorCompanyPanel) button{box-sizing:border-box;min-height:36px;padding:6px 9px;font:inherit;white-space:normal;overflow-wrap:anywhere;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);cursor:pointer}
    #operatorClientCard button.primary{background:var(--brand-deep,#14523c);color:white}
    :is(#operatorClientCard,#operatorCompanyPanel) button:disabled,:is(#operatorClientCard,#operatorCompanyPanel) input:disabled{opacity:.6;cursor:default}
    :is(#operatorClientCard,#operatorCompanyPanel) button:focus-visible,:is(#operatorClientCard,#operatorCompanyPanel) input:focus-visible{outline:2px solid var(--brand);outline-offset:-2px}
    #operatorClientCard .operator-dialog-agent-status{justify-self:start;width:auto}
    #operatorClientCard .operator-agent-label{display:block!important;max-width:none!important;white-space:normal;overflow-wrap:anywhere}
    #operatorClientDeveloperHost{display:grid;gap:6px}
    #operatorClientDeveloperHost:not(:empty){margin-top:4px}
    #operatorClientDeveloperHost:empty,#operatorClientDeveloperStatus:empty,#operatorClientCard [data-client-card-error]:empty{display:none}
    #operatorClientDeveloperStatus{font-size:12px;color:var(--muted)}
    #operatorClientCard [data-client-card-error]{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;color:var(--danger,#ad2831)}
    @media(max-width:900px){
      #operatorCompanyPanelResize{display:none!important}
      .operator-history-tools #operatorCompanyPanelOpen{width:44px;min-width:44px;height:44px;min-height:44px;margin-block:0}
      #operatorCompanyPanel{position:fixed;z-index:30;inset:var(--operator-company-mobile-top,0px) auto auto var(--operator-company-mobile-left,0px);width:var(--operator-company-mobile-width,100%);height:var(--operator-company-mobile-height,100dvh);max-height:none;border:0;border-radius:0;padding:env(safe-area-inset-top) 0 env(safe-area-inset-bottom)}
      #operatorCompanyPanel #operatorCompanyPanelClose{display:grid;place-items:center;flex:none;order:1;margin-inline-start:auto;width:44px;height:44px;min-height:44px;padding:0;font-size:22px}
      #operatorCompanyPanel .operator-client-panel-content{scrollbar-gutter:auto}
      #operatorConversationHeader .operator-conversation-head-copy{flex-direction:row;align-items:center;min-width:0}
      #operatorConversationHeader .operator-dialog-head-row{flex:1;min-width:0}
      #operatorConversationHeader .operator-dialog-head-main{flex:1;min-width:0}
      #operatorConversationHeader .operator-dialog-head-secondary{position:static;flex:none;gap:3px}
    }
    #operatorClientCard .operator-roster-heading{display:flex;align-items:center;gap:8px;justify-content:space-between}#operatorClientCard .operator-roster-heading h3{margin:0;font-size:14px;font-weight:600;line-height:1.35}#operatorClientCard .operator-roster-search-button{display:grid;place-items:center;width:32px;height:32px;min-height:32px;padding:0;flex:none;border:0;background:transparent;color:var(--muted)}#operatorClientCard .operator-roster-search-button svg{width:18px;height:18px}#operatorClientCard .operator-roster-count{margin:1px 0 3px;font-size:12px;color:var(--muted)}#operatorClientCard .operator-roster-list{list-style:none;margin:0;padding:0}#operatorClientCard .operator-roster-row{display:flex;align-items:center;gap:8px;min-height:34px;padding:3px 0;box-sizing:border-box}#operatorClientCard .operator-roster-avatar{display:grid;place-items:center;width:28px;height:28px;flex:none;border-radius:50%;background:var(--paper);color:var(--muted)}#operatorClientCard .operator-roster-avatar svg{width:17px;height:17px}#operatorClientCard .operator-roster-phone{font-size:13px;line-height:1.35;overflow-wrap:anywhere;min-width:0}#operatorClientCard .operator-roster-meta{margin-top:4px;font-size:12px;line-height:1.35;color:var(--muted)}#operatorClientCard .operator-roster-meta summary{cursor:pointer;min-height:28px;display:flex;align-items:center}#operatorClientCard .operator-roster-meta p{margin:4px 0}#operatorClientCard .operator-roster-unknown{margin:3px 0;font-size:12px;color:var(--muted)}#operatorClientCard .operator-roster-search{margin:0 0 3px}#operatorClientCard .operator-roster-search[hidden],#operatorClientCard .operator-roster-row[hidden]{display:none!important}
  `;document.head.append(clientCardStyle);
  function groupParticipantsText(key){return ({ru:{title:'Участники группы',count:'Участников',search:'Поиск участников',noMatch:'Участники не найдены',loading:'Загрузка…',unavailable:'Коннектор пока не передал состав группы.',snapshot:'Снимок состава',note:'Состав мог измениться. Показаны только номера, переданные WhatsApp.',unknown:'Участников без доступного номера',empty:'В снимке нет доступных номеров.'},az:{title:'Qrup iştirakçıları',count:'İştirakçılar',search:'İştirakçı axtarışı',noMatch:'İştirakçı tapılmadı',loading:'Yüklənir…',unavailable:'Konnektor qrupun tərkibini hələ ötürməyib.',snapshot:'Tərkibin qeydə alındığı vaxt',note:'Tərkib dəyişmiş ola bilər. Yalnız WhatsApp tərəfindən verilən nömrələr göstərilir.',unknown:'Nömrəsi əlçatan olmayan iştirakçılar',empty:'Qeyddə əlçatan nömrə yoxdur.'},en:{title:'Group participants',count:'Participants',search:'Search participants',noMatch:'No participants found',loading:'Loading…',unavailable:'The connector has not provided the group roster yet.',snapshot:'Roster snapshot',note:'Membership may have changed. Only numbers provided by WhatsApp are shown.',unknown:'Participants without an available number',empty:'No available numbers in this snapshot.'}})[i18n.language]?.[key]||key;}
  async function renderGroupParticipants(route,card,current){
    const host=card.querySelector('[data-group-participants]');if(!host||host.dataset.loading==='true')return;
    host.dataset.loading='true';host.replaceChildren();
    const heading=document.createElement('h3'),status=document.createElement('p');heading.textContent=groupParticipantsText('title');status.textContent=groupParticipantsText('loading');host.append(heading,status);
    try{
      const result=await api('dialogs/group-participants?'+new URLSearchParams({provider:'connect',lead_id:route.lead_id,channel_id:route.channel_id,conversation_id:route.conversation_id}));
      if(!current()||!card.open)return;
      if(result.status!=='available'){status.textContent=groupParticipantsText('unavailable');return;}
      const phones=(result.phones||[]).filter(phone=>typeof phone==='string'&&/^\+[1-9]\d{6,14}$/.test(phone));
      const toolbar=document.createElement('div');toolbar.className='operator-roster-heading';host.prepend(toolbar);toolbar.append(heading);
      const searchButton=document.createElement('button');searchButton.type='button';searchButton.className='operator-roster-search-button';searchButton.setAttribute('aria-label',groupParticipantsText('search'));searchButton.title=groupParticipantsText('search');searchButton.setAttribute('aria-expanded','false');searchButton.setAttribute('aria-controls','operatorRosterSearch');searchButton.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg>';toolbar.append(searchButton);
      status.className='operator-roster-count';status.textContent=groupParticipantsText('count')+': '+(Number.isInteger(result.total)&&result.total>=phones.length?result.total:phones.length);
      const search=document.createElement('input');search.type='search';search.id='operatorRosterSearch';search.className='operator-roster-search';search.hidden=true;search.placeholder=groupParticipantsText('search');search.setAttribute('aria-label',groupParticipantsText('search'));host.append(search);
      const list=document.createElement('ul');list.className='operator-roster-list';list.setAttribute('aria-label',groupParticipantsText('title'));
      for(const phone of phones){const item=document.createElement('li');item.className='operator-roster-row';const avatar=document.createElement('span');avatar.className='operator-roster-avatar';avatar.setAttribute('aria-hidden','true');avatar.innerHTML='<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2Z"/></svg>';const label=document.createElement('span');label.className='operator-roster-phone';label.textContent=phone;label.dir='ltr';item.append(avatar,label);list.append(item);}host.append(list);
      const empty=document.createElement('p');empty.textContent=groupParticipantsText('empty');empty.hidden=phones.length>0;host.append(empty);
      const filter=()=>{const query=search.value.replace(/\s/g,'');let visible=0;for(const item of list.children){item.hidden=!item.textContent.includes(query);if(!item.hidden)visible++;}empty.hidden=visible>0;empty.textContent=groupParticipantsText(query?'noMatch':'empty');};
      search.addEventListener('input',filter);search.addEventListener('keydown',event=>{if(event.key==='Enter')event.preventDefault();});searchButton.addEventListener('click',()=>{search.hidden=!search.hidden;searchButton.setAttribute('aria-expanded',String(!search.hidden));if(search.hidden){search.value='';filter();}else search.focus();});
      if(result.unresolved){const unknown=document.createElement('p');unknown.className='operator-roster-unknown';unknown.textContent=groupParticipantsText('unknown')+': '+result.unresolved;host.append(unknown);}
      const details=document.createElement('details');details.className='operator-roster-meta';const summary=document.createElement('summary');summary.textContent=groupParticipantsText('snapshot');const timestamp=document.createElement('p');timestamp.textContent=new Date(result.observed_at).toLocaleString();const note=document.createElement('p');note.textContent=groupParticipantsText('note');details.append(summary,timestamp,note);host.append(details);
    }catch{if(current()&&card.open)status.textContent=groupParticipantsText('unavailable');}finally{host.dataset.loading='false';}
  }
  function mountDialogCompany(route,useSnapshot=true){
    if((route.archive_read_only||route.owner_overview_read_only||route.search_overview_read_only)&&member?.role!=='owner'){window.tenantDialogueCompany?.clear();return;}
    if(capabilities.dialog_company_binding!==true){window.tenantDialogueCompany?.clear();return;}
    const host=$('operatorClientCompanyHost'),owner=identity(member);
    const current=()=>capabilities.dialog_company_binding===true&&hasRight('dialogs')&&$('view-dialogs')?.classList.contains('active')&&
      owner===identity(member)&&host?.isConnected&&sameDialog(route,currentDialog);
    window.tenantDialogueCompany?.mount({host,nameHost:$('operatorDialogCompanyName'),reminderHost:$('operatorClientSharedReminderHost'),route:useSnapshot?route:{...route,company_binding:undefined},member,isCurrent:current,
      onChanged:async binding=>{
        if(current()){currentDialog.company_binding=binding;if(displayedPage?.dialog)displayedPage.dialog.company_binding=binding;}
        if(listFlight)await listFlight.promise;
        if(current())await refresh(false,false,{revalidateSelected:true,suppressRead:true});
      }});
  }
  function restoreDialogCompany(){
    if(currentDialog&&sameDialog(displayedPage?.dialog,currentDialog)&&$('view-dialogs')?.classList.contains('active')){
      mountDialogCompany(currentDialog,false);
    }
  }
  function supportCaseBlock(route,block){
    const item=route.support_case;
    const labels={
      ru:{title:'Обращение',status:'Статус',responsible:'Ответственный',new:'Новое',waiting:'Ожидает',in_progress:'В работе',resolved:'Решено'},
      az:{title:'Müraciət',status:'Status',responsible:'Məsul şəxs',new:'Yeni',waiting:'Gözləyir',in_progress:'İcradadır',resolved:'Həll edilib'},
      en:{title:'Support case',status:'Status',responsible:'Responsible',new:'New',waiting:'Waiting',in_progress:'In progress',resolved:'Resolved'},
    };
    if(route.provider!=='connect'||!item||!['new','waiting','in_progress','resolved'].includes(item.status))return '';
    const copy=labels[i18n.language]||labels.en;
    const row=(label,value,field)=>`<div class="operator-client-panel-row" data-client-case-field="${field}"><small>${esc(label)}</small><span data-user-content>${esc(value)}</span></div>`;
    const name=typeof item.responsible_name==='string'&&item.responsible_name.trim()?item.responsible_name:'';
    return block(copy.title,row(copy.status,copy[item.status],'status')+(name?row(copy.responsible,name,'responsible'):''));
  }
  async function showClientTransfer(route,card,current){
    const host=card.querySelector('[data-client-transfer]');
    if(host.dataset.requested||!route.lifecycle?.id||Number(route.lifecycle.revision)<2)return;
    host.dataset.requested='true';
    try{
      const {transfer}=await api('dialogs/transfer-details?'+historyQuery(route));
      if(!current()||!transfer)return;
      const text=document.createElement('span');
      text.textContent=[transfer.from_name||clientCardText('staff'),transfer.to_name||clientCardText('staff')].join(' → ');
      if(transfer.at)text.textContent+=' · '+i18n.date(transfer.at);
      host.querySelector('[data-client-transfer-value]').replaceChildren(text);
      host.hidden=false;
    }catch{delete host.dataset.requested;}
  }
  function patchDirectDialogNames(latest,page,chat){
    const route=latest.dialog,previous=page.dialog;
    if(page!==displayedPage||!chat.isConnected||pendingDialogRoute||
       !$('view-dialogs')?.classList.contains('active')||!isDirectConnectDialog(route)||!isDirectConnectDialog(previous)||
       !sameDialog(route,previous)||!sameDialog(route,currentDialog))return;
    const name=visualDialogTitle(route);
    const title=$('operatorDialogTitleText');
    if(title&&title.textContent!==name){
      title.textContent=name;
      const initial=$('operatorClientCardOpen')?.querySelector('.operator-header-avatar')?.firstChild;
      if(initial?.nodeType===Node.TEXT_NODE)initial.nodeValue=name.slice(0,1).toLocaleUpperCase();
    }
    // Only patch rendered direct incoming authors backed by this history page.
    // Do not infer names for older messages outside it or reset their media.
    const existing=new Map((page.messages||[]).map(message=>[String(message.id),message]));
    const articles=new Map([...chat.querySelectorAll('article[data-message-id]')].map(node=>[node.dataset.messageId,node]));
    const direct=message=>message?.source==='connect'&&message.author_type==='customer'&&message.direction==='incoming';
    for(const fresh of latest.messages||[]){
      const prior=existing.get(String(fresh.id));
      if(!fresh.id||!fresh.external_id||!direct(prior)||!direct(fresh)||
         prior.external_id!==fresh.external_id||
         String(prior.source_channel_id||route.channel_id)!==String(fresh.source_channel_id||route.channel_id)||
         String(prior.source_conversation_id||route.conversation_id)!==String(fresh.source_conversation_id||route.conversation_id))continue;
      prior.author_name=fresh.author_name;
      const label=articles.get(String(fresh.id))?.querySelector('[data-direct-message-author]');
      if(label){const author=messageAuthor(prior);if(label.textContent!==author)label.textContent=author;}
    }
    previous.customer_name=route.customer_name;
    currentDialog.customer_name=route.customer_name;
  }
  const pendingManualPhones=new Set();
  const canEditManualPhone=route=>canUseDuplicateSearch()&&route?.provider==='connect'&&!isDialogGroup(route)
    &&!completedDuplicateRoute(route)&&!route.owner_overview_read_only&&!route.search_overview_read_only&&!route.case_attachment
    &&(!route.lifecycle_enabled||route.lifecycle?.state==='waiting'||route.lifecycle?.state==='accepted'&&String(route.lifecycle.operator_id)===String(member?.telegram_id))
    &&!route.contact_identity_conflict&&!route.identity_conflict&&!verifiedDialogPhone(route)&&!contactDetails(route)?.phone;
  function configureDialogHead(route,saveDisplayName){
    const head=$('operatorConversationHeader'),layout=$('operatorConversationLayout');
    if(!head||!layout)return;
    const hasCompany=()=>capabilities.dialog_company_binding===true&&(member?.role==='owner'||(!route.archive_read_only&&!route.owner_overview_read_only));
    const hasReminder=dialog=>(capabilities.shared_dialog_reminder_creation===true||capabilities.shared_dialog_reminders===true)&&dialog?.shared_reminder?.can_schedule===true;
    const hasTransfer=canSidebarTransfer(route),canScheduleReminder=hasReminder(route);
    const panelTitle=hasCompany()||(!hasTransfer&&canScheduleReminder)?clientCardText('companyPanel'):clientCardText('dialog');
    const editable=route.provider==='connect'&&!route.archive_read_only,group=route.customer_kind==='group'||route.conversation_kind==='group';
    const name=visualDialogTitle(route),phone=visualDialogPhone(route),channel=route.channel_name||'',scope=contextKey('client-card',route),owner=identity(member),oldCard=$('operatorClientCard');
    const menuWasOpen=oldCard?.dataset.scope===scope&&$('operatorMoreActions')?.open===true;
    const oldCompany=$('operatorCompanyPanel');
    const sameCard=oldCard?.dataset.scope===scope;
    const companySnapshot=sameCard&&oldCompany?.open?{
      focus:oldCompany.contains(document.activeElement)?document.activeElement.id:'',
      scroll:oldCompany.querySelector('.operator-client-panel-content').scrollTop
    }:null;
    const snapshot=sameCard&&oldCard.open?{value:$('operatorConversationNameInput')?.value,start:$('operatorConversationNameInput')?.selectionStart,end:$('operatorConversationNameInput')?.selectionEnd,focus:oldCard.contains(document.activeElement)?document.activeElement.id:'',scroll:oldCard.querySelector('.operator-client-panel-content').scrollTop,error:oldCard.querySelector('[data-client-card-error]')?.textContent,saved:oldCard.querySelector('[data-client-name-saved]')?.hidden===false,blocks:[...oldCard.querySelectorAll('.operator-client-panel-block')].map(block=>block.open)}:null;
    const developerNodes=sameCard?[...($('operatorClientDeveloperHost')?.childNodes||[])]:[],developerStatus=sameCard?[...($('operatorClientDeveloperStatus')?.childNodes||[])]:[];
    clientCardDispose(Boolean(companySnapshot));
    oldCard?.remove();oldCompany?.remove();
    head.className='operator-conversation-header';head.innerHTML=`<button type="button" class="operator-back-to-list" data-operator-back aria-label="${esc(t('backToList'))}" title="${esc(t('backToList'))}">‹</button><div class="operator-conversation-head-copy"><div class="operator-dialog-head-row"><div class="operator-dialog-head-main"><button type="button" id="operatorClientCardOpen" title="${esc(clientCardText('title'))}" aria-label="${esc(clientCardText('title'))}" aria-controls="operatorClientCard" aria-expanded="false"></button><div class="operator-dialog-head-identity"><div id="operatorDialogTitleSlot" class="operator-dialog-title" role="heading" aria-level="2"><button type="button" id="operatorDialogTitleText" class="operator-dialog-title-text" aria-controls="operatorClientCard" aria-expanded="false" data-user-content>${esc(name)}</button></div><div class="operator-header-meta" data-header-meta>${group?'':`<span class="operator-client-phone" data-header-phone data-user-content>${esc(phone||phoneUnavailable())}</span>`}${!group&&channel?'<span aria-hidden="true">·</span>':''}${channel?`<span class="operator-client-source" title="${esc(channel)}" data-user-content>${esc(channel)}</span>`:''}<span id="operatorDialogCompanyName" data-user-content hidden></span></div></div></div></div><div class="operator-dialog-head-secondary">${!editable?`<span class="operator-agent-label" data-user-content>${esc(assignmentLabel(route))}</span>`:''}<span id="operatorCreateTicketHost"></span><span id="operatorBrowserCallHost"></span><details id="operatorMoreActions" class="operator-more-actions"><summary class="operator-icon-button" aria-label="${esc(t('moreActions'))}" title="${esc(t('moreActions'))}">⋯</summary><div class="operator-action-menu" role="menu">${!editable?`<div class="operator-menu-agent"><small>${esc(t('agent'))}</small><span data-user-content>${esc(assignmentLabel(route))}</span></div>`:''}<div id="operatorPrimaryAction" class="operator-primary-action"></div><div id="operatorMenuExtra"></div></div></details></div></div><p id="operatorActionReason" class="operator-status-explanation" role="status" hidden></p>`;
    if(menuWasOpen)$('operatorMoreActions').open=true;
    head.parentElement.querySelector('[data-identity-warning]')?.remove();if(route.identity_conflict){const warning=document.createElement('p');warning.dataset.identityWarning='true';warning.className='operator-ai-context';warning.setAttribute('role','status');warning.textContent=({ru:'История WhatsApp разделена между JID/LID. AI остановлен до проверки связи диалогов.',az:'WhatsApp tarixçəsi JID/LID arasında bölünüb. Əlaqə yoxlanana qədər AI dayandırılıb.',en:'WhatsApp history is split between JID/LID. AI is blocked pending identity review.'})[i18n.language];head.after(warning);}
    const trigger=$('operatorClientCardOpen'),title=$('operatorDialogTitleText');
    const avatar=document.createElement('span');
    avatar.className='operator-row-avatar operator-header-avatar';
    avatar.setAttribute('aria-hidden','true');
    avatar.textContent=group?'◉':name.slice(0,1).toLocaleUpperCase();
    avatar.insertAdjacentHTML('beforeend',avatarMarkup(route,true));trigger.append(avatar);
    hydrateAvatars();
    const card=document.createElement('dialog');
    card.id='operatorClientCard';card.dataset.scope=scope;card.setAttribute('aria-labelledby','operatorClientCardTitle');
    const block=(label,content,expanded=true)=>`<details${expanded?' open':''} class="operator-client-panel-block"><summary>${esc(label)}</summary>${content}</details>`;
    const roster=group&&!route.archive_read_only?block(clientCardText('group'),'<section class="operator-client-panel-row" data-group-participants aria-live="polite"></section>',false):'';
    const display=editable&&!mergeDisplayName(route)?'':`<div class="operator-client-panel-row"><small>${esc(mergeDisplayName(route)?mergeDisplayCopy().name:t('displayName'))}</small><span data-panel-name data-user-content>${esc(name)}</span></div>`;
    const phoneRow=group?'':`<div class="operator-client-panel-row"><small>${esc(clientCardText('phone'))}</small><span data-panel-phone data-user-content>${esc(phone||phoneUnavailable())}</span></div>
      ${canEditManualPhone(route)?`<form id="operatorManualDisplayPhone" class="operator-client-panel-row"><label for="operatorManualDisplayPhoneInput">${esc(duplicateCopy('manual'))}</label><input id="operatorManualDisplayPhoneInput" type="tel" maxlength="32" autocomplete="off" value="${esc(verifiedDialogPhone({phone:route.manual_display_phone}))}" required><button type="submit" class="outline">${esc(duplicateCopy('save'))}</button><p data-manual-phone-status role="status"></p></form>`:''}`;
    const nameForm=editable?`<form id="operatorConversationName" class="operator-client-panel-row"><label for="operatorConversationNameInput">${esc(t('displayName'))}<span class="operator-display-name-field"><input id="operatorConversationNameInput" maxlength="180" autocomplete="off"><span data-client-name-saved role="status" aria-label="${esc(clientCardText('saved'))}" title="${esc(clientCardText('saved'))}" hidden>✓</span></span></label><p data-client-card-error role="alert"></p></form>`:'';
    const handoff=route.handoff_active?[route.handoff_source==='ai_handoff'?'AI':'',route.handoff_assignee_name||''].filter(Boolean).join(' → '):'';
    const reminder=canScheduleReminder?
      '<div data-company-reminder-fallback><div class="operator-client-panel-row" id="operatorClientSharedReminderHost" hidden></div></div>':'';
    const conversation=block(clientCardText('dialog'),`<div class="operator-client-panel-row"${channel?'':' hidden'}><small>${esc(clientCardText('source'))}</small><span data-user-content>${esc(channel)}</span></div>
      <div class="operator-client-panel-row" data-client-accepted-by hidden><small>${esc(clientCardText('acceptedBy'))}</small><span data-user-content></span></div>
      <div class="operator-client-panel-row" data-client-transfer ${route.handoff_active?'':'hidden'}><small>${esc(clientCardText('transfer'))}</small><span data-client-transfer-value>${esc(handoff||clientCardText('delegated'))}</span></div>
      <div class="operator-client-panel-row"><small>${esc(t('agent'))}</small><span class="operator-agent-label" data-user-content>${esc(assignmentLabel(route))}</span><span class="operator-state operator-dialog-agent-status" data-panel-agent-status data-state="${esc(route.agent_status||route.display_state)}">${esc(agentModeLabel(route))}</span><div id="operatorClientDeveloperStatus"></div><div id="operatorClientDeveloperHost"></div></div>`);
    card.innerHTML=`<header class="operator-client-panel-header"><h2 id="operatorClientCardTitle">${esc(clientCardText('title'))}</h2><button type="button" id="operatorCancelConversationName" aria-label="${esc(clientCardText('close'))}" autofocus>×</button></header><div class="operator-client-panel-content"><div class="operator-client-panel-block">${display}${nameForm}${phoneRow}</div>${conversation}<div data-client-support-case hidden></div>${roster}</div>`;
    layout.append(card);
    const historyToolbar=$('operatorConversationContent')?.querySelector('.operator-history-tools');
    if(hasCompany()||hasTransfer||canScheduleReminder)historyToolbar?.insertAdjacentHTML('beforeend',`<button type="button" class="operator-icon-button" id="operatorCompanyPanelOpen" aria-controls="operatorCompanyPanel" aria-expanded="false" aria-label="${esc(panelTitle)}" title="${esc(panelTitle)}"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M14.5 4.5v15m2.5-10.5h1m-1 3h1"/></svg></button>`);
    const companyTrigger=$('operatorCompanyPanelOpen');
    const companyPanel=companyTrigger?document.createElement('dialog'):null;
    const companyPanelResize=companyPanel?document.createElement('button'):null;
    if(companyPanel){
      companyPanel.id='operatorCompanyPanel';companyPanel.setAttribute('aria-label',panelTitle);
      companyPanel.innerHTML=`<header class="operator-client-panel-header">${hasCompany()?'':`<h2>${esc(panelTitle)}</h2>`}<button type="button" id="operatorCompanyPanelClose" aria-label="${esc(clientCardText('close'))}">×</button></header><div class="operator-client-panel-content"><div id="operatorClientCompanyHost"></div>${hasTransfer?`<section id="operatorSidebarTransfer"><strong>${esc(clientCardText('dialog'))}</strong><details><summary data-operator-lifecycle="transfer">${esc(lifecycleText('transfer'))}</summary><div data-transfer-targets></div><p role="status"></p><button type="button" data-transfer-retry hidden>${esc(transferSidebarText('retry'))}</button></details></section>`:'<section id="operatorSidebarDialogInfo" hidden></section>'}${reminder}</div>`;
      companyPanelResize.id='operatorCompanyPanelResize';companyPanelResize.type='button';companyPanelResize.setAttribute('role','separator');companyPanelResize.setAttribute('aria-orientation','vertical');companyPanelResize.setAttribute('aria-label',clientCardText('resize'));companyPanelResize.setAttribute('aria-controls',companyPanel.id);companyPanelResize.setAttribute('tabindex','0');
      layout.append(companyPanelResize,companyPanel);
      const status=document.createElement('span');status.id='operatorCompanyPanelSaveStatus';status.className='sr-only';
      status.setAttribute('role','status');head.append(status);
    }
    updateDialogueAcceptor(route,card);
    hydrateContactDetails(route,head);
    const reason=$('operatorActionReason'),reasonText=compactExplanation(route,dialogReason(route));if(reasonText)reason.textContent=reasonText;
    mountDialogCompany(route);
    const form=$('operatorConversationName'),input=$('operatorConversationNameInput'),saved=card.querySelector('[data-client-name-saved]'),error=card.querySelector('[data-client-card-error]'),listeners=[];
    let savedName=(route.manual_display_name||dialogTitle(route)).trim();
    $('operatorClientDeveloperHost').append(...developerNodes);$('operatorClientDeveloperStatus').append(...developerStatus);
    if(input){
      input.value=snapshot?.value??savedName;
      input.readOnly=pendingClientNames.has(scope);
      saved.hidden=!snapshot?.saved;
      if(snapshot?.error)error.textContent=snapshot.error;
    }
    const groupBlock=card.querySelector('[data-group-participants]')?.closest('details');
    const dialogueBlock=card.querySelector('#operatorClientDeveloperHost').closest('details');
    let authorized=true,opener=trigger;
    const current=()=>authorized&&hasRight('dialogs')&&$('view-dialogs')?.classList.contains('active')&&owner===identity(member)&&card.isConnected&&$('operatorClientCard')===card&&sameDialog(route,currentDialog);
    const loadRoster=()=>{if(card.open&&groupBlock?.open)void renderGroupParticipants(route,card,current);};
    groupBlock?.addEventListener('toggle',loadRoster);
    const caseSlot=card.querySelector('[data-client-support-case]');
    let caseRead=0,caseInFlight=false,caseExpanded=true;
    const clearSupportCase=()=>{
      const details=caseSlot.querySelector('details');
      if(details)caseExpanded=details.open;
      caseRead++;caseInFlight=false;caseSlot.hidden=true;caseSlot.replaceChildren();
    };
    const refreshCase=async(restart=false)=>{
      if(!current()||!card.open||route.provider!=='connect'||route.archive_read_only){clearSupportCase();return;}
      if(caseInFlight&&!restart)return;
      // No previous case name or status may remain visible during revalidation.
      clearSupportCase();caseInFlight=true;
      const check=caseRead,params=historyQuery(route);
      params.delete('anchor');params.set('support_case_only','1');
      try{
        const {dialog}=await api('dialogs/history?'+params);
        if(check!==caseRead||!current()||!card.open||!sameDialog(dialog,route))return;
        const markup=supportCaseBlock(dialog,block);
        if(!markup)return;
        caseSlot.innerHTML=markup;caseSlot.querySelector('details').open=caseExpanded;caseSlot.hidden=false;
      }catch{/* Optional case info remains absent if authorization or transport is unavailable. */}
      finally{if(check===caseRead)caseInFlight=false;}
    };
    refreshClientSupportCase=refreshCase;
    const companyPanelViewport=window.matchMedia?.('(max-width: 900px)');
    let companyPanelWasMobile=companyPanelViewport?.matches===true;
    const mobileCompany=()=>companyPanel?.open===true&&companyPanelViewport?.matches===true;
    const inertBefore=new Map();
    const syncChatInteraction=()=>{
      const chat=$('operatorDialogs'),behind=new Set();
      // Isolate the chat, never the panel itself. Its in-panel close control
      // stays reachable while the full mobile viewport is covered.
      if(mobileCompany()&&chat?.contains(companyPanel)){
        for(let node=companyPanel;node!==chat;node=node.parentElement)
          for(const sibling of node.parentElement.children)if(sibling!==node)behind.add(sibling);
      }
      for(const [node,value] of inertBefore)if(!behind.has(node)){
        node.inert=value;inertBefore.delete(node);
      }
      for(const node of behind)if(!inertBefore.has(node)){
        inertBefore.set(node,node.inert);node.inert=true;
      }
    };
    const close=(restore=true)=>{
      if(restore&&document.activeElement===input)void persistName();
      clearSupportCase();
      if(card.open){if(typeof card.close==='function')card.close();else card.removeAttribute('open');}
      for(const button of [trigger,title])button.setAttribute('aria-expanded','false');
      if(restore){if(groupBlock)groupBlock.open=false;if(opener.isConnected)opener.focus({preventScroll:true});}
    };
    const closeTransferMenu=mountSidebarTransfer($('operatorSidebarTransfer'),route,current);
    const closeCompany=(restore=true,preservePublicLink=false)=>{
      if(!companyPanel?.open)return;
      closeTransferMenu();
      if(!preservePublicLink)window.tenantDialogueCompany?.hidePublicLink?.();
      if(restore)window.tenantDialogueCompany?.closePicker?.();
      companyPanel.removeAttribute('open');companyTrigger?.setAttribute('aria-expanded','false');
      if(companyPanel.parentElement!==layout)layout.append(companyPanelResize,companyPanel);
      syncChatInteraction();
      if(restore&&companyTrigger?.isConnected)companyTrigger.focus({preventScroll:true});
    };
    const companyPanelWidthKey=`operator-company-panel-width:${owner}`,conversationPane=$('operatorConversationPane');
    const panelWidthBounds=()=>{
      const available=conversationPane.getBoundingClientRect().width;
      const max=Math.max(200,Math.min(520,available-368,Math.floor((available-8)*.45)));
      return {min:Math.min(260,max),max};
    };
    const storedPanelWidth=()=>{try{const value=Number(localStorage.getItem(companyPanelWidthKey));return Number.isFinite(value)&&value>0?value:null;}catch{return null;}};
    const applyCompanyPanelWidth=(value=storedPanelWidth(),save=false)=>{
      if(!companyPanel||mobileCompany())return;
      const {min,max}=panelWidthBounds(),fallback=Math.min(max,Math.max(min,conversationPane.clientWidth*.3));
      const width=Math.round(Math.max(min,Math.min(max,Number(value)||fallback)));
      conversationPane.style.setProperty('--operator-company-panel-width',`${width}px`);
      companyPanelResize?.setAttribute('aria-valuemin',String(min));companyPanelResize?.setAttribute('aria-valuemax',String(max));companyPanelResize?.setAttribute('aria-valuenow',String(width));
      if(save)try{localStorage.setItem(companyPanelWidthKey,String(width));}catch{}
      return width;
    };
    if(companyPanelResize){
      let draggingPointer=null;
      companyPanelResize.addEventListener('pointerdown',event=>{
        if(!companyPanel.open||mobileCompany()||event.button!==0)return;
        event.preventDefault();draggingPointer=event.pointerId;companyPanelResize.dataset.dragging='true';companyPanelResize.setPointerCapture?.(event.pointerId);
        const move=moveEvent=>{if(moveEvent.pointerId!==draggingPointer)return;const rect=conversationPane.getBoundingClientRect();applyCompanyPanelWidth(rect.right-moveEvent.clientX-4);};
        const finish=finishEvent=>{if(finishEvent.pointerId!==draggingPointer)return;const value=Number.parseFloat(conversationPane.style.getPropertyValue('--operator-company-panel-width')),width=applyCompanyPanelWidth(value,true);draggingPointer=null;delete companyPanelResize.dataset.dragging;companyPanelResize.removeEventListener('pointermove',move);companyPanelResize.removeEventListener('pointerup',finish);companyPanelResize.removeEventListener('pointercancel',finish);};
        companyPanelResize.addEventListener('pointermove',move);companyPanelResize.addEventListener('pointerup',finish);companyPanelResize.addEventListener('pointercancel',finish);
      });
      companyPanelResize.addEventListener('keydown',event=>{
        if(!companyPanel.open||mobileCompany())return;
        const {min,max}=panelWidthBounds(),currentWidth=applyCompanyPanelWidth(Number.parseFloat(conversationPane.style.getPropertyValue('--operator-company-panel-width')));let next;
        if(event.key==='ArrowLeft')next=(currentWidth||min)+16;
        else if(event.key==='ArrowRight')next=(currentWidth||min)-16;
        else if(event.key==='Home')next=min;
        else if(event.key==='End')next=max;
        else return;
        event.preventDefault();applyCompanyPanelWidth(next,true);
      });
    }
    const resizePanel=()=>{
      if(!card.open&&!companyPanel?.open)return;
      const viewport=window.visualViewport;
      const top=viewport?.offsetTop||0,height=viewport?.height||window.innerHeight;
      if(card.open){
        card.style.setProperty('--operator-client-center',top+height/2+'px');
        card.style.setProperty('--operator-client-viewport-height',height+'px');
      }
      if(companyPanel?.open){
        const layoutTop=layout.getBoundingClientRect().top;
        if(mobileCompany()){
          // The animated conversation pane establishes a fixed-position containing
          // block and clips its children. Keep the mobile sheet above that pane.
          const root=$('operatorDialogs');if(root&&companyPanel.parentElement!==root)root.append(companyPanel);
          companyPanel.style.setProperty('--operator-company-mobile-top',top+'px');
          companyPanel.style.setProperty('--operator-company-mobile-left',(viewport?.offsetLeft||0)+'px');
          companyPanel.style.setProperty('--operator-company-mobile-width',(viewport?.width||window.innerWidth)+'px');
          companyPanel.style.setProperty('--operator-company-mobile-height',height+'px');
        }else{
          if(companyPanel.parentElement!==layout||companyPanel.previousElementSibling!==companyPanelResize)layout.append(companyPanelResize,companyPanel);
          applyCompanyPanelWidth();
          companyPanel.style.setProperty('--operator-client-available-height',Math.max(1,top+height-Math.max(top,layoutTop))+'px');
        }
        syncChatInteraction();
      }
    };
    const open=(button=trigger,restore=false)=>{
      if(!current()||card.open)return;
      opener=button;if(!restore)dialogueBlock.open=true;if(typeof card.showModal==='function')card.showModal();else card.setAttribute('open','');for(const control of [trigger,title])control.setAttribute('aria-expanded','true');
      resizePanel();
      void refreshCase(true);
      if(!route.archive_read_only){loadRoster();void showClientTransfer(route,card,current);}
      if(!snapshot)$('operatorCancelConversationName').focus({preventScroll:true});
    };
    trigger.onclick=()=>open(trigger);title.onclick=()=>open(title);
    $('operatorCancelConversationName').onclick=()=>close();
    card.addEventListener('cancel',event=>{event.preventDefault();close();});
    const companyAllowed=()=>current()&&(hasCompany()||canSidebarTransfer(currentDialog)||hasReminder(currentDialog));
    const openCompany=(restore=false)=>{
      if(!companyAllowed()||!companyPanel||companyPanel.open)return;
      companyPanel.setAttribute('open','');companyTrigger?.setAttribute('aria-expanded','true');resizePanel();
      window.tenantDialogueCompany?.refreshPublicLink?.();
      if(!restore)(mobileCompany()?$('operatorCompanyPanelClose'):($('operatorCompanyTab-main')||$('operatorSidebarTransfer')?.querySelector('summary')||$('operatorSharedReminderCreate')))?.focus({preventScroll:true});
    };
    if(companyPanelViewport?.addEventListener){
      const onCompanyPanelViewportChange=event=>{
        const mobile=event.matches===true;
        if(mobile&&!companyPanelWasMobile)closeCompany(false);
        else if(!mobile&&companyPanelWasMobile&&companyPanel&&shouldRestoreCompanyPanel())openCompany(true);
        companyPanelWasMobile=mobile;
        resizePanel();
      };
      companyPanelViewport.addEventListener('change',onCompanyPanelViewportChange);
      listeners.push(()=>companyPanelViewport.removeEventListener('change',onCompanyPanelViewportChange));
    }
    const dismissCompany=()=>{if(companyAllowed()&&isMobileCompanyPanelViewport()){savePanelPreference(false);closeCompany();}};
    if(companyPanel){
      companyTrigger.onclick=()=>{
        if(!companyAllowed())return;
        if(companyPanel.open){dismissCompany();return;}
        savePanelPreference(true);openCompany();
      };
      $('operatorCompanyPanelClose').onclick=dismissCompany;
      if(shouldRestoreCompanyPanel())openCompany(true);
    }
    for(const target of [window,window.visualViewport])if(target?.addEventListener){
      target.addEventListener('resize',resizePanel);
      listeners.push(()=>target.removeEventListener?.('resize',resizePanel));
    }
    window.visualViewport?.addEventListener?.('scroll',resizePanel);
    listeners.push(()=>window.visualViewport?.removeEventListener?.('scroll',resizePanel));
    const listen=(type,fn,capture=false)=>{document.addEventListener(type,fn,capture);listeners.push(()=>document.removeEventListener(type,fn,capture));};
    listen('keydown',event=>{
      if(event.key!=='Escape'||(!card.open&&!companyPanel?.open)||event.defaultPrevented||
         document.querySelector('#operatorLifecycleModal,#operatorCreateCaseModal,dialog[open]:not(#operatorClientCard,#operatorCompanyPanel)'))return;
      event.preventDefault();event.stopImmediatePropagation();
      if(card.open)close();else dismissCompany();
    },true);
    const destroy=()=>{clientCardDispose();card.remove();};
    listen('tenant-chat-close',destroy);
    listen('tenant-auth-invalid',destroy);
    const closeAll=()=>{close(false);closeCompany(false);};
    listen('tenant-auth-pending',()=>{authorized=false;closeAll();});
    listen('tenant-view',event=>{if(event.detail?.view!=='dialogs')closeAll();else if(companyPanel&&shouldRestoreCompanyPanel())openCompany(true);});
    listen('tenant-profile',()=>{
      authorized=owner===identity(member)&&hasRight('dialogs');
      if(!current()){closeAll();return;}
      if(card.open)void refreshCase(true);
      if(!companyPanel)return;
      companyTrigger.hidden=!companyAllowed();
      if(!companyAllowed())closeCompany(false);
      else if(companyPanel.open)window.tenantDialogueCompany?.refreshPublicLink?.(true);
      else if(shouldRestoreCompanyPanel())openCompany(true);
    });
    listen('tenant-dialog-reauthorized',()=>{if(card.open)void refreshCase(true);});
    clientCardDispose=(preservePublicLink=false)=>{if(refreshClientSupportCase===refreshCase)refreshClientSupportCase=()=>{};listeners.forEach(remove=>remove());close(false);closeCompany(false,preservePublicLink);companyPanel?.remove();};
    const activeCard=()=>{
      const active=$('operatorClientCard');
      return active?.dataset.scope===scope&&owner===identity(member)?active:null;
    };
    async function persistName(){
      if(!input||!current()||!card.open||pendingClientNames.has(scope))return;
      const desired=input.value.trim();
      if(desired===savedName)return;
      pendingClientNames.add(scope);input.readOnly=true;error.textContent='';saved.hidden=true;
      try{
        if(await saveDisplayName('conversation',desired)){
          savedName=(currentDialog.manual_display_name||dialogTitle(currentDialog)).trim();
          const active=activeCard(),field=active?.querySelector('#operatorConversationNameInput');
          const indicator=active?.querySelector('[data-client-name-saved]');
          if(field)field.value=savedName;
          if(indicator)indicator.hidden=false;
        }
      }catch(failure){
        const feedback=activeCard()?.querySelector('[data-client-card-error]');
        if(feedback)feedback.textContent=failure.message;
      }finally{
        pendingClientNames.delete(scope);
        const field=activeCard()?.querySelector('#operatorConversationNameInput');
        if(field)field.readOnly=false;
      }
    }
    const phoneForm=card.querySelector('#operatorManualDisplayPhone');
    if(phoneForm){
      const phoneInput=phoneForm.querySelector('input'),phoneButton=phoneForm.querySelector('button');
      phoneInput.disabled=phoneButton.disabled=pendingManualPhones.has(scope);
      phoneForm.onsubmit=async event=>{
        event.preventDefault();
        if(!current()||!canEditManualPhone(currentDialog)||pendingManualPhones.has(scope))return;
        const phone=verifiedDialogPhone({phone:phoneInput.value});
        const status=node=>node?.querySelector('[data-manual-phone-status]');
        if(!phone){status(card).textContent=duplicateCopy('noPhone');return;}
        const version=epoch;
        pendingManualPhones.add(scope);phoneInput.disabled=phoneButton.disabled=true;status(card).textContent='';
        try{
          const result=await api('dialogs/contact-display-phone','POST',{...duplicateRoute(route),phone});
          const stored=verifiedDialogPhone({phone:result?.display_phone});
          if(!stored)throw new Error('no display phone');
          if(version!==epoch||owner!==identity(member)||!sameDialog(currentDialog,route)||!canUseDuplicateSearch())return;
          currentDialog.manual_display_phone=stored;
          if(sameDialog(currentPage?.dialog,route))currentPage.dialog.manual_display_phone=stored;
          for(const row of rows)if(sameDialog(row,route))row.manual_display_phone=stored;
          const active=activeCard(),field=active?.querySelector('#operatorManualDisplayPhoneInput');
          if(field)field.value=stored;
          if(status(active))status(active).textContent=clientCardText('saved');
          for(const label of [active?.querySelector('[data-panel-phone]'),head.querySelector('[data-header-phone]')])if(label)label.textContent=visualDialogPhone(currentDialog);
          renderRows();
        }catch{if(version===epoch&&status(activeCard()))status(activeCard()).textContent=duplicateCopy('failed');}
        finally{
          pendingManualPhones.delete(scope);
          activeCard()?.querySelectorAll('#operatorManualDisplayPhone input,#operatorManualDisplayPhone button').forEach(field=>field.disabled=false);
        }
      };
    }
    if(form){
      form.onsubmit=event=>{event.preventDefault();void persistName();};
      input.onblur=()=>void persistName();
      input.oninput=()=>{saved.hidden=true;error.textContent='';};
    }
    if(companySnapshot&&companyPanel&&shouldRestoreCompanyPanel()){
      openCompany(true);
      if(companySnapshot.focus&&companyPanel.contains($(companySnapshot.focus)))$(companySnapshot.focus).focus({preventScroll:true});
      companyPanel.querySelector('.operator-client-panel-content').scrollTop=companySnapshot.scroll;
    }
    if(snapshot){
      [...card.querySelectorAll('.operator-client-panel-block')].forEach((block,index)=>{if(index<snapshot.blocks.length)block.open=snapshot.blocks[index];});
      open(trigger,true);if(snapshot.focus&&card.contains($(snapshot.focus)))$(snapshot.focus).focus({preventScroll:true});
      if(input&&snapshot.start!==undefined)try{input.setSelectionRange(snapshot.start,snapshot.end);}catch{}
      card.querySelector('.operator-client-panel-content').scrollTop=snapshot.scroll||0;
    }
  }
  function agentModeLabel(route){
    if(route.agent_status==='restricted')return assignmentLabel(route);
    if(route.group_coordination?.primary_agent_id&&route.group_coordination.state==='human')return t('groupAgentHuman');
    if(route.group_coordination?.primary_agent_id&&route.group_coordination.state==='paused')return t('groupAgentPaused');
    if(route.group_coordination?.primary_agent_id&&route.group_coordination.state==='active'){
      if(route.agent_status==='error')return t('aiStatusError');
      if(route.agent_status==='automatic')return t('aiModeAutomatic');
      if(route.agent_status==='attention')return t('aiNeedsAttention');
      if(route.agent_mode==='manual')return t('groupAgentManual');
      return t('groupAgentAssigned');
    }
    if(route.agent_status==='error')return t('aiStatusError');
    if(route.agent_status==='automatic')return t('aiModeAutomatic');
    if(route.agent_status==='manual')return t('aiModeManual');
    if(route.agent_status==='attention')return t('aiNeedsAttention');
    if(route.agent_status==='needs_assignment')return t('assignmentRequired');
    if(route.group_coordination&&!route.group_coordination.primary_agent_id)return (route.group_coordination.assignments||[]).length?t('groupNoCurrentAgent'):t('groupAssignmentRequired');
    if(!route.agent_id)return (route.agent_assignment?.agents?.length||0)?t('assignmentRequired'):t('noAvailableAgent');
    return route.agent_available?(route.agent_mode==='manual'?t('aiModeManual'):t('aiModeAutomatic')):t('aiNeedsAttention');
  }
  function assignmentLabel(route){
    if(route.assignment_display?.assigned)return route.assignment_display.name;
    if(route.agent_status==='restricted'&&!route.assignment_display)return ({ru:'Сведения об агенте недоступны',az:'Agent məlumatları əlçatan deyil',en:'Agent details unavailable'})[i18n.language]||'Agent details unavailable';
    return route.agent_name||t('noAgent');
  }
  function compactAgentModeLabel(route){
    const lang=window.tenantI18n?.language||'ru';
    const labels={ru:{manual:'AI · Ручной',automatic:'AI · Авто',attention:'AI · Проверка'},az:{manual:'AI · Əl',automatic:'AI · Avto',attention:'AI · Yoxla'},en:{manual:'AI · Manual',automatic:'AI · Auto',attention:'AI · Check'}};
    const status=route.agent_status||((route.agent_id&&route.agent_available)?(route.agent_mode==='manual'?'manual':'automatic'):'');
    return labels[lang]?.[status]||agentModeLabel(route);
  }
  function dialogCondition(route){
    if(route.handoff_active)return t('handoffActive');
    if(route.agent_mode==='manual'&&route.agent_id)return t('manualCopilotAvailable');
    if(route.state==='paused')return t('automaticRepliesPaused');
    if(route.state==='human')return route.reason==='operator_agent_assigned'?t('assignedNotResumed'):t('automaticRepliesStopped');
    return '';
  }
  function dialogReason(route){if(route.agent_status_reason)return i18n.reason?i18n.reason(route.agent_status_reason):t('reasonUnknown');if(route.agent_assignment&&!route.agent_assignment.allowed&&route.agent_assignment.reason)return assignmentReason(route.agent_assignment.reason);const code=route.display_reason||route.reason;return !code&&route.display_state==='active'?'—':i18n.reason?i18n.reason(code):t('reasonUnknown');}
  function compactExplanation(route,reasonText){if(!route.controls_allowed)return i18n.reason?i18n.reason(route.control_block_reason):t('noAccess');if(['paused','human'].includes(route.state)&&!route.resume_allowed)return i18n.reason?i18n.reason(route.resume_block_reason):t('noAccess');if(['human','paused','blocked','error','unmanaged'].includes(route.display_state)||['attention','needs_assignment','error'].includes(route.agent_status))return reasonText;return '';}
  const diagnosisCopy={ru:{title:'Почему AI не отвечает?',checking:'Проверяю условия диалога…',close:'Закрыть',what:'Что делать?',details:'Технические подробности',agent:'Агент включён',agentOff:'Агент выключен',agentUnknown:'Не удалось проверить агента',channel:'WhatsApp-канал подключён',channelOff:'WhatsApp-канал недоступен',channelUnknown:'Не удалось проверить WhatsApp-канал',groups:'Работа в группах разрешена',groupsOff:'Работа в группах не разрешена',groupsPrivate:'Для личного диалога разрешение групп не требуется',mode:'Режим агента',schedule:'Расписание активно',scheduleOff:'Сейчас вне расписания агента',scheduleAny:'У агента нет ограничения по расписанию',dialog:'Состояние диалога',human:'Диалог передан сотруднику',paused:'AI приостановлен в диалоге',active:'AI не приостановлен в этом диалоге',limits:'Ограничения компании и сервера',limitOk:'Подтверждённых общих ограничений не найдено',limitUnknown:'Не удалось проверить общие ограничения',last:'Последний результат обработки',lastUnknown:'Нет подтверждённого результата для этого диалога',resume:'Доступность Resume',resumeAvailable:'Сервер разрешает возобновить AI',resumeUnavailable:'Resume сейчас недоступен',resumeNotNeeded:'Resume не требуется: AI не остановлен',ok:'Выполнено',blocked:'Есть ограничение',unknown:'Не удалось проверить',enableAgent:'Включите агента в настройках.',checkChannel:'Проверьте подключение и статус WhatsApp-канала.',allowGroups:'Разрешите обработку групповых сообщений, если это необходимо.',checkSchedule:'Проверьте рабочее расписание агента.',resumeAction:'Возобновите AI в диалоге, если сервер разрешает Resume.',administrator:'Не удалось определить точную причину. Требуется проверка администратора.',noAction:'Подтверждённых блокировок не найдено. Проверьте следующее новое сообщение клиента.',technicalWorker:'Worker heartbeat',technicalDeployment:'Deployment',technicalFlags:'Ограничения сервера',technicalQueue:'Очередь AI',technicalCodes:'Коды событий',noData:'Нет данных',lastSent:'Ответ отправлен',lastDraft:'Ответ подготовлен как черновик',lastWaiting:'Сообщение ожидает обработки',lastError:'Сообщение не обработано',lastSkipped:'Сообщение пропущено условиями агента'},az:{title:'AI niyə cavab vermir?',checking:'Dialoq şərtləri yoxlanılır…',close:'Bağla',what:'Nə etmək lazımdır?',details:'Texniki məlumat',agent:'Agent aktivdir',agentOff:'Agent söndürülüb',agentUnknown:'Agenti yoxlamaq mümkün olmadı',channel:'WhatsApp kanalı qoşulub',channelOff:'WhatsApp kanalı əlçatan deyil',channelUnknown:'WhatsApp kanalını yoxlamaq mümkün olmadı',groups:'Qrup mesajlarına icazə verilib',groupsOff:'Qrup mesajlarına icazə verilməyib',groupsPrivate:'Şəxsi dialoq üçün qrup icazəsi tələb olunmur',mode:'Agent rejimi',schedule:'İş qrafiki aktivdir',scheduleOff:'Hazırda agentin iş qrafikindən kənardadır',scheduleAny:'Agent üçün iş qrafiki məhdudiyyəti yoxdur',dialog:'Dialoqun vəziyyəti',human:'Dialoq əməkdaşa ötürülüb',paused:'Dialoqda AI dayandırılıb',active:'Bu dialoqda AI dayandırılmayıb',limits:'Şirkət və server məhdudiyyətləri',limitOk:'Təsdiqlənmiş ümumi məhdudiyyət tapılmadı',limitUnknown:'Ümumi məhdudiyyətləri yoxlamaq mümkün olmadı',last:'Son emal nəticəsi',lastUnknown:'Bu dialoq üçün təsdiqlənmiş nəticə yoxdur',resume:'Resume əlçatanlığı',resumeAvailable:'Server AI-ni davam etdirməyə icazə verir',resumeUnavailable:'Resume hazırda əlçatan deyil',resumeNotNeeded:'Resume lazım deyil: AI dayandırılmayıb',ok:'Yerinə yetirilib',blocked:'Məhdudiyyət var',unknown:'Yoxlamaq mümkün olmadı',enableAgent:'Agentin ayarlarda aktiv edin.',checkChannel:'WhatsApp kanalının qoşulmasını və statusunu yoxlayın.',allowGroups:'Lazımdırsa, qrup mesajlarının emalına icazə verin.',checkSchedule:'Agentin iş qrafikini yoxlayın.',resumeAction:'Server Resume-a icazə verirsə, dialoqda AI-ni davam etdirin.',administrator:'Dəqiq səbəbi müəyyən etmək mümkün olmadı. Administrator yoxlaması tələb olunur.',noAction:'Təsdiqlənmiş bloklama yoxdur. Müştərinin növbəti yeni mesajını yoxlayın.',technicalWorker:'Worker heartbeat',technicalDeployment:'Deployment',technicalFlags:'Server məhdudiyyətləri',technicalQueue:'AI növbəsi',technicalCodes:'Hadisə kodları',noData:'Məlumat yoxdur',lastSent:'Cavab göndərilib',lastDraft:'Cavab qaralama kimi hazırlanıb',lastWaiting:'Mesaj emal gözləyir',lastError:'Mesaj emal edilmədi',lastSkipped:'Mesaj agentin şərtlərinə görə buraxıldı'},en:{title:'Why is AI not replying?',checking:'Checking dialogue conditions…',close:'Close',what:'What should I do?',details:'Technical details',agent:'Agent is enabled',agentOff:'Agent is disabled',agentUnknown:'Could not verify the agent',channel:'WhatsApp channel is connected',channelOff:'WhatsApp channel is unavailable',channelUnknown:'Could not verify the WhatsApp channel',groups:'Group messages are allowed',groupsOff:'Group messages are not allowed',groupsPrivate:'A private dialogue does not need group permission',mode:'Agent mode',schedule:'Schedule is active',scheduleOff:'The agent is outside its schedule',scheduleAny:'The agent has no schedule restriction',dialog:'Dialogue state',human:'Dialogue is handed to an employee',paused:'AI is paused in this dialogue',active:'AI is not paused in this dialogue',limits:'Company and server restrictions',limitOk:'No confirmed general restriction was found',limitUnknown:'Could not verify general restrictions',last:'Last processing result',lastUnknown:'No confirmed result exists for this dialogue',resume:'Resume availability',resumeAvailable:'The server allows AI to resume',resumeUnavailable:'Resume is currently unavailable',resumeNotNeeded:'Resume is not needed: AI is not stopped',ok:'Passed',blocked:'Restriction found',unknown:'Could not verify',enableAgent:'Enable the agent in settings.',checkChannel:'Check the WhatsApp channel connection and status.',allowGroups:'Allow group-message processing if it is needed.',checkSchedule:'Check the agent schedule.',resumeAction:'Resume AI in the dialogue only if the server allows it.',administrator:'The exact cause could not be determined. Administrator review is required.',noAction:'No confirmed block was found. Check the customer’s next new message.',technicalWorker:'Worker heartbeat',technicalDeployment:'Deployment',technicalFlags:'Server restrictions',technicalQueue:'AI queue',technicalCodes:'Event codes',noData:'No data',lastSent:'Reply sent',lastDraft:'Reply prepared as a draft',lastWaiting:'Message is awaiting processing',lastError:'Message was not processed',lastSkipped:'Message was skipped by agent conditions'}};
  Object.assign(diagnosisCopy.ru,{technicalTenant:'Tenant ID',technicalScope:'Tenant/channel scope'});Object.assign(diagnosisCopy.az,{technicalTenant:'Tenant ID',technicalScope:'Tenant/channel scope'});Object.assign(diagnosisCopy.en,{technicalTenant:'Tenant ID',technicalScope:'Tenant/channel scope'});
  Object.assign(diagnosisCopy.ru,{aiUnavailable:'Общая диагностика AI недоступна для вашей роли.'});
  Object.assign(diagnosisCopy.az,{aiUnavailable:'Ümumi AI diaqnostikası rolunuz üçün əlçatan deyil.'});
  Object.assign(diagnosisCopy.en,{aiUnavailable:'Company-wide AI diagnostics are unavailable for your role.'});
  const dx=key=>(diagnosisCopy[window.tenantI18n?.language||'ru']||diagnosisCopy.ru)[key]||diagnosisCopy.ru[key]||key;
  const dxNode=(tag,text,className)=>{const node=document.createElement(tag);if(text!=null)node.textContent=String(text);if(className)node.className=className;return node;};
  const dxReason=code=>code&&i18n.reason?i18n.reason(code):dx('administrator');
  const dxRouteMatches=(event,route)=>event?.agent_id&&String(event.agent_id)===String(route.agent_id||'')&&event.channel?.provider===route.provider&&String(event.channel?.id||'').endsWith(':'+String(route.channel_id||''))&&Number(event.dialog?.lead_id)===Number(route.lead_id);
  function closeDialogDiagnostics(restoreFocus=true){diagnosticRevision++;const modal=$('operatorDiagnosticsModal');if(modal){modal.hidden=true;modal.replaceChildren();}const chat=$('chatRows');if(chat&&diagnosticScroll!==null)chat.scrollTop=diagnosticScroll;diagnosticScroll=null;const target=diagnosticFocus;diagnosticFocus=null;if(restoreFocus)(target?.isConnected?target:$('operatorMoreActions')?.querySelector('summary'))?.focus();}
  function diagnosisRow(host,state,title,description){const row=dxNode('section',null,'operator-diagnostic-row');row.dataset.state=state;const icon=dxNode('span',state==='ok'?'✓':state==='blocked'?'!':'?','operator-diagnostic-icon');icon.setAttribute('aria-label',dx(state));const copy=dxNode('div');copy.append(dxNode('strong',title),dxNode('p',description));row.append(icon,copy);host.append(row);}
  function diagnosisTechnical(host,technical,route){const fields=[['technicalTenant','tenant_id'],['technicalWorker','worker_last_seen_at'],['technicalDeployment','deployment_id'],['technicalFlags','server_flags'],['technicalQueue','queue'],['technicalCodes','reason_codes']];for(const [label,key] of fields){const value=technical?.[key],section=dxNode('section',null,'operator-diagnostic-technical-row');section.append(dxNode('h4',dx(label)));if(value==null||(Array.isArray(value)&&!value.length)){section.append(dxNode('p',dx('noData')));}else if(typeof value==='object'&&!Array.isArray(value)){const list=dxNode('dl');for(const [name,item] of Object.entries(value)){list.append(dxNode('dt',name),dxNode('dd',typeof item==='boolean'?(item?dx('ok'):dx('blocked')):typeof item==='number'?item:dx('noData')));}section.append(list);}else section.append(dxNode('p',Array.isArray(value)?value.join(', '):value));host.append(section);}const scope=dxNode('section',null,'operator-diagnostic-technical-row');scope.append(dxNode('h4',dx('technicalScope')),dxNode('p',[route.provider,route.channel_id,route.conversation_id].filter(Boolean).join(' · ')||dx('noData')));host.append(scope);}
  const legacyCopy={
    ru:{title:'Подтверждение канала старого диалога',check:'Проверить каналы',choose:'Выберите сохранённый WhatsApp-канал',restore:'Подтвердить канал и восстановить',confirm:'Подтвердить связь, сверенную по сохранённым маршрутам, сообщениям и событиям? Назначение и история сохранятся; AI останется остановлен.',saved:'Канал подтверждён. Обновите диалог; включение AI — отдельное действие.',unique_verified_route:'Канал однозначно подтверждён сохранёнными данными; конфликтов и ожидающих ответов нет.',multiple_channels:'Этот диалог найден в нескольких каналах. Выбор сотрудника сам по себе не подтверждает источник.',conflicting_state:'Для канала уже есть отдельное состояние. Автоматическое объединение запрещено.',legacy_agent_scope_mismatch:'Старый агент отсутствует или не имеет права работать на этом канале.',insufficient_evidence:'Сохранённых доказательств недостаточно; привязка остаётся заблокированной.',conflicting_evidence:'Задачи или события противоречат выбранному каналу либо агенту.',outstanding_work:'Есть ожидающие задачи, черновики или отправки с неопределённым результатом.',group_requires_review:'Для группы автоматическое восстановление не подтверждено; нужна отдельная проверка.',no_legacy_state:'Старое непривязанное состояние отсутствует.',evidence:'Сохранённые данные: {routes} маршрутов, {messages} сообщений, {jobs} задач, {drafts} черновиков, {events} каналов событий.',channelEvidence:'{messages} сообщений · {jobs} задач · {events} событий'},
    az:{title:'Köhnə dialoq üçün kanalın təsdiqi',check:'Kanalları yoxla',choose:'Saxlanmış WhatsApp kanalını seçin',restore:'Kanalı təsdiqlə və bərpa et',confirm:'Saxlanmış marşrut, mesaj və hadisələrə görə yoxlanmış əlaqə təsdiqlənsin? Təyinat və tarixçə saxlanacaq; AI dayandırılmış qalacaq.',saved:'Kanal təsdiqləndi. Dialoqu yeniləyin; AI ayrıca aktivləşdirilir.',unique_verified_route:'Kanal saxlanmış məlumatla birmənalı təsdiqləndi; ziddiyyət və gözləyən cavab yoxdur.',multiple_channels:'Bu dialoq bir neçə kanalda tapıldı. Əməkdaşın seçimi mənbəni təkbaşına sübut etmir.',conflicting_state:'Kanal üçün ayrıca vəziyyət mövcuddur. Avtomatik birləşdirmə qadağandır.',legacy_agent_scope_mismatch:'Köhnə agent yoxdur və ya bu kanala icazəsi çatmır.',insufficient_evidence:'Saxlanmış sübut kifayət etmir; əlaqə bloklanmış qalır.',conflicting_evidence:'Tapşırıqlar və ya hadisələr seçilmiş kanal/agentlə ziddiyyət təşkil edir.',outstanding_work:'Gözləyən tapşırıq, qaralama və ya nəticəsi qeyri-müəyyən göndəriş var.',group_requires_review:'Qrup üçün avtomatik bərpa təsdiqlənməyib; ayrıca yoxlama lazımdır.',no_legacy_state:'Əlaqəsiz köhnə vəziyyət yoxdur.',evidence:'Saxlanmış məlumat: {routes} marşrut, {messages} mesaj, {jobs} tapşırıq, {drafts} qaralama, {events} hadisə kanalı.',channelEvidence:'{messages} mesaj · {jobs} tapşırıq · {events} hadisə'},
    en:{title:'Confirm the legacy dialogue channel',check:'Review channels',choose:'Choose a saved WhatsApp channel',restore:'Confirm channel and recover',confirm:'Confirm the route verified against saved routes, messages and events? Assignment and history stay intact; AI remains stopped.',saved:'Channel confirmed. Refresh the dialogue; enabling AI remains separate.',unique_verified_route:'Persisted data uniquely confirms this channel; no conflicts or outstanding replies.',multiple_channels:'This dialogue exists on multiple channels. An operator choice alone does not prove its source.',conflicting_state:'A channel-local state already exists. Automatic merging is prohibited.',legacy_agent_scope_mismatch:'The legacy agent is missing or not scoped to this channel.',insufficient_evidence:'Persisted evidence is insufficient; the binding remains blocked.',conflicting_evidence:'Jobs or events conflict with the selected channel or agent.',outstanding_work:'Outstanding jobs, drafts or uncertain sends require review.',group_requires_review:'Automatic recovery for this group is not proven; separate review is required.',no_legacy_state:'No unbound legacy state exists.',evidence:'Saved evidence: {routes} routes, {messages} messages, {jobs} jobs, {drafts} drafts, {events} event channels.',channelEvidence:'{messages} messages · {jobs} jobs · {events} events'}
  };
  function legacyRecoveryControl(host,route,isCurrent){
    if(route.provider!=='connect'||![route.reason,route.resume_block_reason,route.control_block_reason,route.display_reason].includes('legacy_channel_ambiguous'))return;
    const copy=legacyCopy[window.tenantI18n?.language||'ru']||legacyCopy.ru,box=dxNode('section'),note=dxNode('p'),button=dxNode('button',copy.check),label=dxNode('label',copy.choose),select=dxNode('select');button.type='button';select.hidden=true;label.hidden=true;label.append(select);box.append(dxNode('h3',copy.title),note,label,button);host.append(box);let checked=null,selectedRoute=route;
    const read=async selected=>api('dialogs/legacy-recovery?'+historyQuery(selected)+'&'+new URLSearchParams({expected_tenant_id:member?.tenant_id,expected_user_id:member?.telegram_id}));
    const inspect=async selected=>{button.disabled=true;note.textContent='';try{checked=await read(selected);selectedRoute=selected;if(!isCurrent())return;const evidence=checked.evidence||{};note.textContent=[copy[checked.reason]||dx('administrator'),copy.evidence.replace('{routes}',evidence.route_count??0).replace('{messages}',evidence.message_count??0).replace('{jobs}',evidence.job_count??0).replace('{drafts}',evidence.draft_count??0).replace('{events}',evidence.event_channel_count??0)].join(' ');button.textContent=copy.restore;button.disabled=!checked.allowed;}catch(error){if(isCurrent()){note.textContent=error.message;button.disabled=false;}}};
    button.onclick=async()=>{if(checked?.allowed){if(!isCurrent()||!window.confirm(copy.confirm))return;button.disabled=true;try{await api('dialogs/legacy-recovery','POST',{provider:selectedRoute.provider,channel_id:selectedRoute.channel_id,conversation_id:selectedRoute.conversation_id,lead_id:selectedRoute.lead_id,expected_token:checked.expected_token,confirmed:true});if(isCurrent()){note.textContent=copy.saved;button.remove();label.remove();}}catch(error){if(isCurrent()){note.textContent=error.message;checked=null;button.disabled=false;}}return;}button.disabled=true;try{const initial=await read(route);if(!isCurrent())return;select.replaceChildren();const placeholder=dxNode('option',copy.choose);placeholder.value='';select.append(placeholder);for(const item of initial.channels||[]){const title=item.name||((window.tenantI18n?.language||'ru')==='az'?'WhatsApp kanalı':(window.tenantI18n?.language||'ru')==='en'?'WhatsApp channel':'WhatsApp-канал');const detail=[item.name||title,item.active?null:((window.tenantI18n?.language||'ru')==='en'?'inactive':(window.tenantI18n?.language||'ru')==='az'?'qeyri-aktiv':'неактивен'),copy.channelEvidence.replace('{messages}',item.message_count||0).replace('{jobs}',item.job_count||0).replace('{events}',item.event_count||0)].filter(Boolean).join(' · ');const option=dxNode('option',detail);option.value=JSON.stringify({provider:'connect',channel_id:item.channel_id,conversation_id:route.conversation_id,lead_id:item.lead_id});select.append(option);}label.hidden=false;select.hidden=false;button.disabled=true;if(!initial.channels?.length){note.textContent=copy[initial.reason]||dx('administrator');return;}select.onchange=()=>{checked=null;const value=select.value;if(!value){button.disabled=true;return;}inspect(JSON.parse(value));};if(initial.channels.length===1){select.value=select.options[1].value;await inspect(JSON.parse(select.value));}else {note.textContent=copy.multiple_channels;}}catch(error){if(isCurrent()){note.textContent=error.message;button.disabled=false;}}};
  }
  const continuationCopy={
    ru:{title:'Продолжить на существующем канальном состоянии',channel:'Выберите WhatsApp-канал',agent:'Выберите основного агента',save:'Сохранить разрешение для канала',confirm:'Разрешить продолжение только на выбранном канале? Старое состояние и история сохранятся. AI останется остановлен; Включить AI — отдельное действие.',saved:'Разрешение сохранено. Обновите выбранный диалог. AI остаётся остановлен; включение выполняется отдельно.',missing_state:'Нет одновременно старого и отдельного канального состояния.',must_be_stopped:'Сначала требуется остановить AI в выбранном канале.',outstanding_work:'Есть незавершённые задачи, черновики или отправки с неизвестным результатом.',no_eligible_agent:'Нет включённого основного агента с правами на этот канал.'},
    az:{title:'Mövcud kanal vəziyyəti ilə davam et',channel:'WhatsApp kanalını seçin',agent:'Əsas agenti seçin',save:'Kanal üçün icazəni saxla',confirm:'Yalnız seçilmiş kanalda davam etməyə icazə verilsin? Köhnə vəziyyət və tarixçə saxlanacaq. AI dayandırılmış qalacaq; aktivləşdirmə ayrıca edilir.',saved:'İcazə saxlanıldı. Seçilmiş dialoqu yeniləyin. AI dayandırılmış qalır; aktivləşdirmə ayrıca edilir.',missing_state:'Köhnə və ayrıca kanal vəziyyəti birlikdə mövcud deyil.',must_be_stopped:'Əvvəlcə seçilmiş kanalda AI dayandırılmalıdır.',outstanding_work:'Tamamlanmamış tapşırıq, qaralama və ya nəticəsi naməlum göndəriş var.',no_eligible_agent:'Bu kanala icazəsi olan aktiv əsas agent yoxdur.'},
    en:{title:'Continue on the existing channel state',channel:'Choose a WhatsApp channel',agent:'Choose a primary agent',save:'Save channel permission',confirm:'Allow continuation on the selected channel only? Legacy state and history will be preserved. AI will remain stopped; Resume is separate.',saved:'Permission saved. Refresh the selected dialogue. AI remains stopped; Resume is separate.',missing_state:'Both a legacy state and a separate channel state are required.',must_be_stopped:'Stop AI on the selected channel first.',outstanding_work:'Outstanding jobs, drafts or unknown send results require review.',no_eligible_agent:'No enabled primary agent is eligible for this channel.'}
  };
  function channelContinuationControl(host,route,isCurrent){
    if(route.provider!=='connect'||route.customer_kind==='group'||String(route.conversation_id).endsWith('@g.us')||![route.resume_block_reason,route.display_reason,route.assignment_block_reason].includes('legacy_channel_ambiguous'))return;
    const copy=continuationCopy[window.tenantI18n?.language||'ru']||continuationCopy.ru,box=dxNode('section'),start=dxNode('button',copy.title),note=dxNode('p');start.type='button';box.style.display='grid';box.style.gap='8px';box.append(start,note);host.append(box);
    const read=selected=>api('dialogs/legacy-continuation?'+historyQuery(selected)+'&'+new URLSearchParams({expected_tenant_id:member?.tenant_id,expected_user_id:member?.telegram_id}));
    start.onclick=async()=>{start.disabled=true;try{
      const initial=await read(route);if(!isCurrent())return;
      const channel=dxNode('select'),agent=dxNode('select'),save=dxNode('button',copy.save),channelLabel=dxNode('label',copy.channel),agentLabel=dxNode('label',copy.agent);
      channel.dataset.continuationChannel='';agent.dataset.continuationAgent='';save.dataset.continuationSave='';save.type='button';save.disabled=true;agent.disabled=true;
      const option=(select,value,label)=>{const o=dxNode('option',label);o.value=value;select.append(o);};
      option(channel,'',copy.channel);for(const item of initial.channels||[])option(channel,item.channel_id,item.name||copy.channel);option(agent,'',copy.agent);
      for(const label of [channelLabel,agentLabel]){label.style.display='grid';label.style.gap='4px';}for(const select of [channel,agent]){select.style.width='100%';select.style.minWidth='0';}channelLabel.append(channel);agentLabel.append(agent);box.append(channelLabel,agentLabel,save);start.remove();let selection=null,preview=null,epoch=0;
      channel.onchange=async()=>{const current=++epoch;save.disabled=true;agent.disabled=true;agent.replaceChildren();option(agent,'',copy.agent);preview=null;selection=null;note.textContent='';
        const item=(initial.channels||[]).find(c=>c.channel_id===channel.value);if(!item)return;
        const selected={...route,channel_id:item.channel_id,lead_id:item.lead_id};
        try{const checked=await read(selected);if(!isCurrent()||current!==epoch)return;preview=checked;selection=selected;note.textContent=copy[checked.reason]||'';
          if(!checked.allowed&&!checked.test_close_allowed)return;
          if(checked.test_close_allowed)note.textContent=({ru:'Будут закрыты без повторной обработки старые неизвестные запуски: ',az:'Təkrar icra edilmədən bağlanacaq köhnə naməlum icralar: ',en:'Old unknown runs to close without retry: '}[window.tenantI18n?.language||'ru'])+checked.test_close_count;
          for(const a of checked.agents||[])if(!checked.test_close_allowed||a.mode==='manual')option(agent,a.id,a.name);agent.disabled=false;
        }catch(error){if(isCurrent()&&current===epoch)note.textContent=error.message;}
      };
      agent.onchange=()=>{save.disabled=!(preview?.allowed||preview?.test_close_allowed)||!agent.value;};
      save.onclick=async()=>{if(!isCurrent()||!selection||!(preview?.allowed||preview?.test_close_allowed)||!agent.value||!window.confirm(copy.confirm))return;if(preview.test_close_allowed&&!window.confirm(({ru:'Подтверждаю: это мой тестовый диалог. Закрыть старые неизвестные запуски без повторной обработки и разрешить продолжение только на выбранном канале. Human Mode и Manual сохранятся.',az:'Təsdiq edirəm: bu mənim test dialoqumdur. Köhnə naməlum icraları təkrarlamadan bağla və yalnız seçilmiş kanalda davam etməyə icazə ver. Human Mode və Manual saxlanılır.',en:'I confirm this is my own test dialogue. Close old unknown runs without retry and permit only the selected channel. Human Mode and Manual remain unchanged.'})[window.tenantI18n?.language||'ru']))return;
        save.disabled=true;channel.disabled=true;agent.disabled=true;
        try{await api('dialogs/legacy-continuation','POST',{provider:'connect',channel_id:selection.channel_id,conversation_id:selection.conversation_id,lead_id:selection.lead_id,agent_id:agent.value,expected_token:preview.expected_token,confirmed:true,test_owner_confirmed:preview.test_close_allowed===true});if(isCurrent()){note.textContent=copy.saved;save.remove();}}
        catch(error){if(isCurrent()){note.textContent=error.message;channel.disabled=false;preview=null;}}
      };
    }catch(error){if(isCurrent()){note.textContent=error.message;start.disabled=false;}}};
  }
  const readAiDiagnostics=async path=>(await rawApi(endpoint+path,{}, {optionalScopedRead:true})).result;
  function openDialogDiagnostics(route,trigger=$('operatorMoreActions')?.querySelector('summary')){
    const current=++diagnosticRevision,chat=$('chatRows');diagnosticScroll=chat?chat.scrollTop:0;let modal=$('operatorDiagnosticsModal');if(!modal){modal=document.createElement('section');modal.id='operatorDiagnosticsModal';modal.hidden=true;document.body.append(modal);}if(modal.hidden)diagnosticFocus=trigger;modal.hidden=false;modal.innerHTML=`<div class="operator-diagnostics-backdrop" data-operator-diagnostics-close></div><article class="operator-diagnostics-card" role="dialog" aria-modal="true" aria-labelledby="operatorDiagnosticsTitle"><header><div><h2 id="operatorDiagnosticsTitle"></h2><p id="operatorDiagnosticsSubject"></p></div><button type="button" class="outline" id="operatorDiagnosticsClose"></button></header><div id="operatorDiagnosticsBody" class="operator-diagnostics-body" aria-live="polite"><div id="operatorAiDiagnostics"></div><div id="operatorReplyDiagnostics"></div></div></article>`;$('operatorDiagnosticsTitle').textContent=t('diagnostics');$('operatorDiagnosticsSubject').textContent=dialogTitle(route)+' · '+(assignmentLabel(route));$('operatorDiagnosticsClose').textContent=dx('close');$('operatorDiagnosticsClose').onclick=closeDialogDiagnostics;modal.querySelector('[data-operator-diagnostics-close]').onclick=closeDialogDiagnostics;const body=$('operatorAiDiagnostics');body.append(dxNode('p',dx('checking')));window.tenantChatSend?.renderDiagnostics?.($('operatorReplyDiagnostics'));$('operatorDiagnosticsClose').focus();
    const identityQuery=new URLSearchParams({expected_tenant_id:member?.tenant_id||'',expected_user_id:member?.telegram_id||'',limit:'30'}),isCurrent=()=>current===diagnosticRevision&&currentDialog?.lead_id===route.lead_id&&currentDialog?.channel_id===route.channel_id&&currentDialog?.conversation_id===route.conversation_id&&!modal.hidden;
    readAiDiagnostics('state?'+identityQuery).then(state=>{if(!isCurrent())return;body.replaceChildren();const agent=(state.agents||[]).find(item=>String(item.id)===String(route.agent_id||'')),channel=agent?.channels?.find(item=>item.provider===route.provider&&String(item.channel_id||'')===String(route.channel_id||'')),codes=channel?.reason_codes||[],recommendations=[];if(!agent){diagnosisRow(body,'unknown',dx('agentUnknown'),route.agent_name||dx('noData'));recommendations.push(dx('administrator'));}else{diagnosisRow(body,agent.enabled?'ok':'blocked',agent.enabled?dx('agent'):dx('agentOff'),agent.name);diagnosisRow(body,'ok',dx('mode'),agent.mode==='automatic'?'Automatic':'Manual');}
      const channelBlocked=route.channel_active===false||route.connected===false||codes.some(code=>['integration_disconnected','channel_connection_unconfirmed','channel_not_in_catalog'].includes(code));if(!channel)diagnosisRow(body,'unknown',dx('channelUnknown'),route.channel_name||dx('noData'));else diagnosisRow(body,channelBlocked?'blocked':'ok',channelBlocked?dx('channelOff'):dx('channel'),route.channel_name||channel.name||dx('noData'));if(channelBlocked)recommendations.push(dx('checkChannel'));
      const isGroup=route.customer_kind==='group',groupsAllowed=channel?.group_messages_allowed===true;if(isGroup){diagnosisRow(body,!channel?'unknown':groupsAllowed?'ok':'blocked',groupsAllowed?dx('groups'):dx('groupsOff'),groupsAllowed?route.channel_name||channel?.name:dxReason('group_messages_not_allowed'));if(!groupsAllowed)recommendations.push(dx('allowGroups'));}else diagnosisRow(body,'ok',dx('groupsPrivate'),route.channel_name||dx('noData'));
      if(!agent)diagnosisRow(body,'unknown',dx('schedule'),dx('noData'));else if(!agent.schedule?.start)diagnosisRow(body,'ok',dx('scheduleAny'),dx('noData'));else {diagnosisRow(body,agent.schedule_open===true?'ok':'blocked',agent.schedule_open===true?dx('schedule'):dx('scheduleOff'),`${agent.schedule.start}–${agent.schedule.end} · ${agent.schedule.timezone||dx('noData')}`);if(agent.schedule_open===false)recommendations.push(dx('checkSchedule'));}
      const stopped=['human','paused'].includes(route.state);diagnosisRow(body,'ok',dx('dialog'),route.state==='human'?dx('human'):route.state==='paused'?dx('paused'):dx('active'));
      const hardCodes=codes.filter(code=>['ai_agents_server_disabled','company_ai_disabled','emergency_stop','tenant_inactive','worker_unavailable','model_unavailable','ai_configuration_not_ready'].includes(code));if(!channel)diagnosisRow(body,'unknown',dx('limits'),dx('limitUnknown'));else diagnosisRow(body,hardCodes.length?'blocked':'ok',dx('limits'),hardCodes.length?dxReason(hardCodes[0]):dx('limitOk'));if(hardCodes.length)recommendations.push(dx('administrator'));
      const event=(state.events||[]).find(item=>dxRouteMatches(item,route));if(!event)diagnosisRow(body,'unknown',dx('last'),dx('lastUnknown'));else {const expectedStop=['manager_replied','human_handoff','conversation_human','conversation_human_or_paused','conversation_paused_or_human','conversation_paused','manager_pause','connect_human_takeover','customer_requested_manager','trigger_reply_sent_then_handoff'].includes(event.reason_code);const key=event.status==='sent'?'lastSent':event.status==='generated'?'lastDraft':event.status==='error'?'lastError':event.status==='skipped'?'lastSkipped':'lastWaiting';diagnosisRow(body,expectedStop||['sent','generated'].includes(event.status)?'ok':['error','skipped'].includes(event.status)?'blocked':'unknown',dx('last'),expectedStop?dxReason(event.reason_code):dx(key));if(!expectedStop&&['error','skipped'].includes(event.status))recommendations.push(dx('administrator'));}
      if(stopped&&route.agent_mode!=='manual'){const resumeState=route.resume_allowed===true?'ok':'blocked';diagnosisRow(body,resumeState,dx('resume'),route.resume_allowed===true?dx('resumeAvailable'):route.resume_block_reason?dxReason(route.resume_block_reason):dx('resumeUnavailable'));if(route.resume_allowed===true&&!isGroup&&!hardCodes.length)recommendations.push(dx('resumeAction'));else if(route.resume_allowed!==true)recommendations.push(dx('administrator'));}else diagnosisRow(body,'ok',dx('resume'),dx('resumeNotNeeded'));
      const actions=body.appendChild(dxNode('section',null,'operator-diagnostic-actions'));actions.append(dxNode('h3',dx('what')));const unique=[...new Set(recommendations)];for(const [index,item] of (unique.length?unique:[dx('noAction')]).entries()){const line=dxNode('p',item);if(index===0)line.dataset.primary='true';actions.append(line);}
      if(state.can_view_technical===true){legacyRecoveryControl(body,route,isCurrent);channelContinuationControl(body,route,isCurrent);}
      if(state.can_view_technical===true){const details=dxNode('details',null,'operator-diagnostic-technical'),content=dxNode('div');details.append(dxNode('summary',dx('details')),content);body.append(details);let loaded=false;details.ontoggle=async()=>{if(!details.open||loaded||!isCurrent())return;loaded=true;content.replaceChildren(dxNode('p',dx('checking')));try{const diagnostic=await readAiDiagnostics('state/technical?'+identityQuery);if(!isCurrent())return;content.replaceChildren();diagnosisTechnical(content,diagnostic.technical&&typeof diagnostic.technical==='object'?diagnostic.technical:{},route);}catch{if(isCurrent())content.replaceChildren(dxNode('p',dx('administrator')));}};}
      const audioMessages=(currentPage?.messages||[]).filter(message=>audioDiagnostics(message));if(audioMessages.length){const labels={ru:'Диагностика аудио',az:'Səs diaqnostikası',en:'Audio diagnostics'},section=dxNode('section',null,'operator-diagnostic-audio');section.setAttribute('aria-label',labels[i18n.language]||labels.en);section.append(dxNode('h3',labels[i18n.language]||labels.en));for(const message of audioMessages){const template=document.createElement('template');template.innerHTML=audioDiagnostics(message);if(template.content.firstElementChild)section.append(template.content.firstElementChild);}body.append(section);}
    }).catch(error=>{if(isCurrent())body.replaceChildren(dxNode('p',dx(error.status===403?'aiUnavailable':'administrator')));});
  }
  function closeGroupSettings(){groupSettingsRevision++;const modal=$('operatorGroupSettingsModal');if(modal){modal.hidden=true;modal.replaceChildren();}}
  async function openGroupSettings(route){
    const group=route.group_coordination,isGroup=Boolean(group);if(route.provider!=='connect')return;const current=++groupSettingsRevision;
    let modal=$('operatorGroupSettingsModal');if(!modal){modal=document.createElement('section');modal.id='operatorGroupSettingsModal';modal.hidden=true;document.body.append(modal);}
    modal.hidden=false;modal.innerHTML=`<div class="operator-group-settings-backdrop" aria-hidden="true"></div><article class="operator-group-settings-card" role="dialog" aria-modal="true" aria-labelledby="operatorGroupSettingsTitle"><header><div><h2 id="operatorGroupSettingsTitle"></h2><p id="operatorGroupSettingsSubject"></p></div><button type="button" class="outline" id="operatorGroupSettingsClose"></button></header><div id="operatorGroupSettingsBody" class="operator-group-settings-body"></div></article>`;
    $('operatorGroupSettingsTitle').textContent=t('groupSettingsTitle');$('operatorGroupSettingsSubject').textContent=dialogTitle(route)+' · '+(route.channel_name||t('channel'));$('operatorGroupSettingsClose').textContent=t('close');$('operatorGroupSettingsClose').onclick=closeGroupSettings;
    const body=$('operatorGroupSettingsBody');let choices,assignments=[],revision=0,currentAgentId='',legacyPreview=null,legacyPreviewValue='';
    try{
      if(isGroup){
        if(!group.can_configure)throw new Error(t('groupConfigureDenied'));
        assignments=Array.isArray(group.assignments)?group.assignments:[];revision=Number(group.revision)||0;
        choices=(group.agents||[]).filter(agent=>agent.role==='primary'&&(agent.scopes||[]).some(scope=>scope.provider==='connect'&&scope.allow_group_messages===true));
      }else{
        const result=await api('dialogs/agent-assignment?'+new URLSearchParams({...Object.fromEntries(historyQuery(route)),manage:'1'}));
        if(current!==groupSettingsRevision)return;choices=result.agents||[];revision=Number(result.revision)||0;currentAgentId=result.current_agent_id||route.agent_id||'';
        if(!result.can_manage)throw new Error(t('assignmentBusy'));
      }
    }catch(error){if(current!==groupSettingsRevision)return;body.append(dxNode('p',error.message||t('groupConfigureDenied')));$('operatorGroupSettingsClose')?.focus();return;}
    const single=assignments.length===1?assignments[0]:null,savedId=isGroup?(single?String(single.agent_id)+'|'+String(single.channel_id):''):String(currentAgentId||'');
    const currentOption=isGroup?choices.find(agent=>String(agent.id)===String(single?.agent_id)):choices.find(agent=>String(agent.id)===String(currentAgentId));
    body.append(dxNode('p',t('groupCurrentAgent')+': '+(currentOption?.name||(!assignments.length&&!currentAgentId?t('noAgent'):t('groupMissing')))));
    if(assignments.length>1)body.append(dxNode('p',t('groupLegacyAmbiguous')));
    const form=document.createElement('form');form.className='operator-group-settings-form';form.dataset.operatorGroupSettingsForm='true';const label=document.createElement('label'),select=document.createElement('select');select.setAttribute('aria-label',t('groupPrimary'));select.append(new Option(t('chooseAgent'),''));
    if(isGroup){const channels=group.channels||[];for(const agent of choices)for(const channel of channels){if((agent.scopes||[]).some(scope=>scope.provider==='connect'&&(!scope.channel_id||String(scope.channel_id)===String(channel.channel_id))&&scope.allow_group_messages===true))select.append(new Option(agent.name+' — '+channel.channel_name,String(agent.id)+'|'+String(channel.channel_id)));}}
    else for(const agent of choices)select.append(new Option(agent.name,String(agent.id)));
    if(savedId&&[...select.options].some(option=>option.value===savedId))select.value=savedId;
    else if(currentAgentId&&!choices.some(agent=>String(agent.id)===String(currentAgentId))){const stale=new Option(currentOption?.name||route.agent_name||t('groupMissing'),String(currentAgentId));stale.disabled=true;select.add(stale);select.value=String(currentAgentId);}
    label.append(dxNode('strong',t('groupPrimary')),select);form.append(label);
    const legacyBox=dxNode('section',null,'operator-group-legacy-preview'),legacyNote=dxNode('p',''),legacyDetails=document.createElement('details'),legacySummary=dxNode('summary',t('groupLegacyPreview')),legacyEvidence=dxNode('p',''),legacyPrior=dxNode('p',''),legacyBlockingHeading=dxNode('p',''),legacyInventory=dxNode('ul','', 'operator-group-legacy-work-items');legacyDetails.append(legacySummary,legacyEvidence,legacyPrior,legacyBlockingHeading,legacyInventory);legacyBox.hidden=true;legacyBox.append(legacyNote,legacyDetails);form.append(legacyBox);
    const actions=dxNode('div',null,'operator-group-settings-actions'),save=document.createElement('button'),cancel=document.createElement('button'),remove=document.createElement('button'),error=dxNode('p','');let requestId='';error.setAttribute('role','status');error.setAttribute('aria-live','polite');save.type='submit';save.className='primary';save.textContent=t('groupSave');cancel.type='button';cancel.className='outline';cancel.textContent=t('cancelEdit');cancel.onclick=closeGroupSettings;remove.type='button';remove.className='outline';remove.textContent=t('groupRemove');remove.disabled=!(assignments.length||currentAgentId);remove.onclick=()=>{select.value='';form.requestSubmit();};select.onchange=()=>{requestId='';};actions.append(save,cancel,remove);form.append(actions,error);form.onsubmit=async event=>{event.preventDefault();save.disabled=true;remove.disabled=true;try{
      const hasCurrent=Boolean(assignments.length||currentAgentId),nextValue=select.value;
      if(!nextValue&&!hasCurrent)throw Error(t('chooseAgent'));
      if(isGroup&&nextValue&&(!legacyPreview||legacyPreviewValue!==nextValue))throw Error(t('groupLegacyBlocked'));
      if(!nextValue&&hasCurrent&&!window.confirm(t('groupConfirmRemove')))return;
      if(isGroup){const next=nextValue?(()=>{const [agent_id,channel_id]=nextValue.split('|');return [{agent_id,channel_id}];})():[];let legacy_resolution;if(nextValue&&legacyPreview?.requires_resolution){if(legacyPreviewValue!==nextValue||!legacyPreview.allowed)throw Error(t('groupLegacyBlocked'));const channel=group.channels.find(row=>String(row.channel_id)===next[0].channel_id),agent=choices.find(row=>String(row.id)===next[0].agent_id),stopCount=Number(legacyPreview.safely_stoppable_queued_count)||0;const confirmation=[t('groupLegacyConfirmPrompt'),`${dialogTitle(route)} · ${channel?.channel_name||t('channel')} · ${agent?.name||''}`,stopCount?t('groupLegacyStopConfirm').replace('{count}',String(stopCount)):'',t('groupLegacyConfirm')].filter(Boolean).join('\n\n');if(!window.confirm(confirmation))return;legacy_resolution={confirmed:true,expected_token:legacyPreview.expected_token,agent_version:legacyPreview.agent_version,channel_id:next[0].channel_id,lead_id:legacyPreview.lead_id,agent_id:next[0].agent_id,...(stopCount?{cancel_queued_work:true}:{})};}else if(nextValue&&legacyPreviewValue&&legacyPreviewValue!==nextValue)throw Error(t('groupLegacyBlocked'));await api('dialogs/group-coordination','POST',{conversation_id:group.group_jid,expected_revision:revision,assignments:next,...(legacy_resolution?{legacy_resolution}:{})});}
      else {requestId=requestId||crypto.randomUUID();await api('dialogs/agent-assignment','POST',{provider:'connect',lead_id:route.lead_id,channel_id:route.channel_id,conversation_id:route.conversation_id,agent_id:nextValue||null,expected_revision:revision,request_id:requestId});}
      if(current===groupSettingsRevision){closeGroupSettings();await refresh();await openDialog(route);}
    }catch(e){if(current===groupSettingsRevision)error.textContent=e.message;}finally{if(save.isConnected)save.disabled=isGroup&&legacyPreview?.requires_resolution&&(!legacyPreview.allowed||legacyPreviewValue!==select.value);if(remove.isConnected)remove.disabled=!(assignments.length||currentAgentId);}};
    const inspectLegacySelection=async()=>{
      const value=select.value;legacyPreview=null;legacyPreviewValue=value;legacyBox.hidden=true;
      if(!isGroup||!value)return;
      const [agent_id,channel_id]=value.split('|'),channel=group.channels.find(row=>String(row.channel_id)===channel_id);
      if(!channel)return;
      save.disabled=true;error.textContent='';
      const params=new URLSearchParams({provider:'connect',channel_id,conversation_id:group.group_jid,lead_id:String(channel.lead_id),agent_id,expected_tenant_id:member?.tenant_id,expected_user_id:member?.telegram_id});
      try{
        const result=await api('dialogs/group-legacy-resolution?'+params);
        if(current!==groupSettingsRevision||select.value!==value)return;
        legacyPreview=result;
        if(result.requires_resolution){
          legacyBox.hidden=false;
          const reasonKey=`groupLegacyReason_${String(result.reason||'unknown')}`;
          legacyNote.textContent=result.allowed?t('groupLegacyReview'):t(reasonKey)===(reasonKey)?t('groupLegacyReason_unknown'):t(reasonKey);
          const evidence=result.evidence||{},items=Array.isArray(result.work_items)?result.work_items:[],stoppable=Number(result.safely_stoppable_queued_count)||0,blocked=Number(result.blocked_work_count)||0,recordCount=Number(result.work_record_count)||items.length;
          const selectedState=evidence.selected_channel_state;
          if(result.allowed&&selectedState&&['paused','human'].includes(String(selectedState.state))){const stateKey=`groupLegacyChannelState_${selectedState.state}`,stateLabel=t(stateKey)===(stateKey)?String(selectedState.state):t(stateKey),stateAgent=choices.find(row=>String(row.id)===String(selectedState.agent_id)),agentLabel=stateAgent?.name?` · ${stateAgent.name}`:'';legacyNote.textContent+=' '+t('groupLegacySelectedStatePreserved').replace('{state}',stateLabel).replace('{agent}',agentLabel);}
          legacyEvidence.textContent=t('groupLegacyEvidence').replace('{routes}',String(evidence.route_count||0)).replace('{messages}',String(evidence.message_count||0)).replace('{links}',String(evidence.active_source_links||0)).replace('{work}',String(recordCount)+(result.work_inventory_truncated?'+':''));
          if(stoppable)legacyNote.textContent+=' '+t(blocked?'groupLegacyStopHeld':'groupLegacyStopNotice').replace('{count}',String(stoppable));
          if(blocked)legacyNote.textContent+=' '+t('groupLegacyBlockedWork').replace('{count}',String(blocked));
          legacyBlockingHeading.textContent=blocked?t('groupLegacyBlockingRecords').replace('{count}',String(blocked)):'';
          const prior=(result.current_assignments||[]).map(item=>{const oldAgent=choices.find(row=>String(row.id)===String(item.agent_id)),oldChannel=group.channels.find(row=>String(row.channel_id)===String(item.channel_id));return `${oldAgent?.name||t('groupMissing')} — ${oldChannel?.channel_name||t('channel')}`;});
          legacyPrior.textContent=t('groupLegacyPriorAssignments').replace('{assignments}',prior.length?prior.join('; '):t('noAgent'));
          const formatTime=value=>{if(!value)return'';const date=new Date(value);return Number.isNaN(date.getTime())?'':date.toLocaleString();};
          legacyInventory.replaceChildren(...items.map(item=>{
            const id=String(item.item_type==='job'?item.job_id:(item.item_id||item.job_id||'')).slice(0,80),relatedEvent=item.job_id&&String(item.job_id)!==id?String(item.job_id).slice(0,80):'',state=item.job_state||item.run_state||item.draft_state||item.event_state||'unknown',channelName=String(item.channel_id||'')?group.channels.find(row=>String(row.channel_id)===String(item.channel_id))?.channel_name||t('channel'):t('groupLegacyUnboundChannel'),type=t('groupLegacyMessageType_'+(item.message_type||'other')),attempts=item.attempts??item.event_attempts,lease=item.lease_until||item.event_lease_until,blocking=item.relevant_to_selection===true&&item.safely_stoppable!==true,rawCode=String(item.error_code||item.event_error_code||''),safeCode=/^[a-z0-9_:-]{1,64}$/i.test(rawCode)?rawCode:'',parts=[blocking?t('groupLegacyBlockingLabel'):'',`${item.item_type||'work'}: ${id}`,relatedEvent?`event: ${relatedEvent}`:'',item.run_id?`${t('groupLegacyRelatedRun')}: ${item.run_id}`:'',item.draft_id?`${t('groupLegacyRelatedDraft')}: ${item.draft_id}`:'',state,`${t('groupLegacyEventState')}: ${item.event_state||'unknown'}`,channelName,type,item.relevant_to_selection===false?t('groupLegacyOtherChannel'):'',item.model||'',attempts!=null?`${t('groupLegacyAttempts')}: ${attempts}`:'',safeCode?`${t('groupLegacyErrorCode')}: ${safeCode}`:'',lease?`${t('groupLegacyLease')}: ${lease}`:'',formatTime(item.created_at)?`${t('groupLegacyCreated')}: ${formatTime(item.created_at)}`:'',formatTime(item.updated_at)?`${t('groupLegacyUpdated')}: ${formatTime(item.updated_at)}`:'',formatTime(item.due_at)?`${t('groupLegacyDue')}: ${formatTime(item.due_at)}`:''];
            const row=dxNode('li',parts.filter(Boolean).join(' · '));if(blocking)row.dataset.blocking='true';return row;
          }));
          if(result.work_inventory_truncated)legacyInventory.append(dxNode('li',t('groupLegacyInventoryTruncated')));
          save.disabled=!result.allowed;
        }else save.disabled=false;
      }catch(e){if(current===groupSettingsRevision&&select.value===value){legacyPreview=null;legacyBox.hidden=false;legacyNote.textContent=e.message||t('groupLegacyBlocked');save.disabled=true;}}
    };
    select.onchange=()=>{requestId='';legacyPreview=null;legacyPreviewValue=select.value;inspectLegacySelection();};if(isGroup&&select.value)inspectLegacySelection();body.append(form);$('operatorGroupSettingsClose').focus();
  }
  function captureDialogInteractionState(route,host){
    const shell=host.querySelector('[data-operator-dialog]');if(shell?.dataset.dialogKey!==dialogKey(route))return null;
    const composer=host.querySelector('#chatText'),quote=host.querySelector('#operatorReplyQuote'),prompt=host.querySelector('#operatorCopilotPrompt'),copilot=host.querySelector('#operatorCopilot'),result=host.querySelector('#operatorCopilotResult'),insert=host.querySelector('#operatorCopilotInsert'),active=document.activeElement;
    const modeButton=[...host.querySelectorAll('[data-copilot-mode]')].find(button=>button.getAttribute('aria-pressed')==='true');
    const quoteTarget=quote&&!quote.hidden&&quote.dataset.quoteMessageId?{quote_message_id:quote.dataset.quoteMessageId,quote_channel_id:quote.dataset.quoteChannelId,quote_conversation_id:quote.dataset.quoteConversationId,pending:quote.dataset.replyPending==='true'}:null;
    let focus='';if(active===composer)focus='composer';else if(active===prompt)focus='copilotPrompt';else if(active?.id&&copilot?.contains(active))focus=active.id;
    let selection=null;if(focus)try{const field=focus==='composer'?composer:focus==='copilotPrompt'?prompt:active;selection={start:field.selectionStart,end:field.selectionEnd,direction:field.selectionDirection};}catch{}
    const state={draft:composer?.value,focus,selection,quote:quoteTarget,copilotOpen:copilot&&!copilot.hidden,copilotPrompt:prompt?.value||'',copilotResult:result?.textContent||'',copilotResultHidden:result?.hidden??true,copilotInsertAvailable:insert&&!insert.hidden,copilotInsertContext:insert?.dataset.contextSignature||'',copilotMode:modeButton?.dataset.copilotMode||'chat',helperType:host.querySelector('#dialogHelperType')?.value||copilot?.dataset.helperType||'Helper',copilotScroll:host.querySelector('#dialogHelperContent')?.scrollTop||0,copilotStatus:host.querySelector('#operatorCopilotStatus')?.textContent||'',templateOpen:host.querySelector('#operatorCopilotTemplates')?.open||false,templateFormHidden:host.querySelector('#operatorCopilotTemplateForm')?.hidden??true,templateName:host.querySelector('#operatorCopilotTemplateName')?.value||'',templateQuestion:host.querySelector('#operatorCopilotTemplateQuestion')?.value||'',templateId:host.querySelector('#operatorCopilotTemplateForm')?.dataset.editingId||''};
    if(!historyMergeId(displayedPage))helperDrafts.set(contextKey('helper',route),{...state,focus:'',copilotOpen:false});return state;
  }
  function restoreCaseDraft(route,composer){
    const key=contextKey('incoming-case-draft',route),draft=sessionStorage.getItem(key);if(!draft)return;
    const clearReview=()=>{const prior=$('operatorCaseDraftReview');if(prior){composer.removeEventListener('input',prior._draftInput);prior.remove();}};
    const use=()=>{composer.value=draft;composer.dispatchEvent(new Event('input',{bubbles:true}));sessionStorage.removeItem(key);clearReview();const status=$('operatorAIReplyStatus');if(status)status.textContent=copilotText('aiReplyReady');};
    if(!composer.value.trim()){use();return;}
    if(composer.value===draft){sessionStorage.removeItem(key);clearReview();return;}
    if($('operatorCaseDraftReview')?.dataset.caseDraft===draft)return;clearReview();
    const words=({ru:{title:'Черновик по ответу разработчика — клиенту не отправлен',kept:'Ваш текущий черновик сохранён. Освободите поле сообщения, чтобы использовать этот ответ.',use:'Вставить проверенный черновик'},az:{title:'Tərtibatçı cavabına əsaslanan qaralama — müştəriyə göndərilməyib',kept:'Cari qaralamanız saxlanılıb. Bu cavabı istifadə etmək üçün mesaj sahəsini boşaldın.',use:'Yoxlanmış qaralamanı əlavə et'},en:{title:'Developer reply draft — not sent to the customer',kept:'Your current draft is preserved. Clear the message field to use this reply.',use:'Use reviewed draft'}}[i18n.language]||{});
    const panel=document.createElement('section');panel.id='operatorCaseDraftReview';panel.dataset.caseDraft=draft;panel.className='operator-case-draft-review';const title=document.createElement('strong'),body=document.createElement('p'),hint=document.createElement('p'),insert=document.createElement('button');title.textContent=words.title;body.textContent=draft;hint.textContent=words.kept;insert.type='button';insert.className='outline';insert.textContent=words.use;insert.disabled=true;insert.onclick=()=>{if(!composer.value.trim())use();};panel._draftInput=()=>{insert.disabled=!!composer.value.trim();};composer.addEventListener('input',panel._draftInput);panel.append(title,body,hint,insert);composer.closest('.operator-composer').before(panel);
  }
  // Keep the mounted editor/media subtree for a refreshed snapshot of the
  // same route and permissions. Mutate the page object captured by handlers.
  const dialogRenderScope=route=>JSON.stringify([route?.shared_reminder?.can_schedule===true,Object.entries(route||{}).filter(([key])=>!(key==='customer_name'&&isDirectConnectDialog(route))&&!['unread_count','last_message_id','last_message_direction','last_message_type','last_message_text','last_message_preview','last_message_at','company_binding','dialog_company_name','shared_reminder'].includes(key)).map(([key,value])=>{
    if(key!=='lifecycle'||!value)return [key,value];
    const {accepted_by_name,company_completion,...lifecycle}=value;
    return [key,Object.entries(lifecycle).sort(([a],[b])=>a.localeCompare(b))];
  }).sort(([a],[b])=>a.localeCompare(b))]);
  function updateDialogueAcceptor(route,card=$('operatorClientCard')){
    if(card?.dataset.scope!==contextKey('client-card',route))return;
    const row=card.querySelector('[data-client-accepted-by]');
    if(!row)return;
    const name=route.lifecycle?.accepted_by_name||'';
    row.querySelector('span').textContent=name;
    row.hidden=!name;
  }
  function updateLatestIncomingShortcut(chat,page){
    const button=$('operatorLatestIncoming');
    if(!button)return;
    const messages=page.dialog.history_pair_id?(page.messages||[]):[];
    const incoming=messages.findLast(message=>message.direction==='incoming');
    button.hidden=!incoming||incoming===messages.at(-1);
    button.onclick=button.hidden?null:()=>{
      const node=[...chat.querySelectorAll('[data-message-id]')].find(row=>row.dataset.messageId===String(incoming.id));
      if(!node)return;
      const offset=node.getBoundingClientRect().top-chat.getBoundingClientRect().top;
      setTimelineScroll(chat,chat.scrollTop+offset-(chat.clientHeight-node.offsetHeight)/2,true);
    };
  }
  function patchDialogPage(latest,{suppressRead=false}={}){
    const old=displayedPage,chat=$('chatRows');
    if(!old||!chat||displayedLanguage!==i18n.language||displayedRouteScope!==dialogRenderScope(latest.dialog)||latest.anchor_message_id&&latest.anchor_message_id!==old.anchor_message_id)return false;
    currentPage=old;old.messages ||= [];
    const follow=atBottom(chat),top=chat.scrollTop,anchor=follow?null:timelineViewportAnchor(chat);
    patchDirectDialogNames(latest,old,chat);
    // Only a complete, unanchored history proves absence. A newest-page read
    // must not remove older messages explicitly loaded by the employee.
    if(latest.next_before===null&&!latest.anchor_message_id){
      const present=new Set((latest.messages||[]).map(message=>String(message.id)));
      old.messages=old.messages.filter(message=>{if(present.has(String(message.id)))return true;[...chat.querySelectorAll('[data-message-id]')].find(node=>node.dataset.messageId===String(message.id))?.remove();return false;});
      old.next_before=null;if($('operatorEarlier'))$('operatorEarlier').hidden=true;
    }
    const byId=new Map((old.messages||[]).map(m=>[String(m.id),m]));
    updateDeliveryEvidence(latest,latest.dialog);updateAudioEvidence(latest,latest.dialog);updateVisualEvidence(latest,latest.dialog);
    for(const message of latest.messages||[]){
      const prior=byId.get(String(message.id));
      if(!prior){if(!(old.messages||[]).length&&chat.textContent.trim()===t('emptyHistory'))chat.replaceChildren();chat.insertAdjacentHTML('beforeend',messageMarkup([message],latest.dialog));old.messages.push(message);byId.set(String(message.id),message);}
      else {if(prior.media_version!==message.media_version||prior.message_type!==message.message_type||(prior.body!==message.body&&(!message.message_type||message.message_type==='text'))){const node=[...chat.querySelectorAll('[data-message-id]')].find(n=>n.dataset.messageId===String(message.id));if(node){const template=document.createElement('template');template.innerHTML=messageMarkup([message],latest.dialog);node.replaceWith(template.content);}}
        const ownChanged=prior.wa_reaction_event!==message.wa_reaction_event||
          prior.wa_reaction_available!==message.wa_reaction_available;
        if(!ownChanged)message.wa_reaction=prior.wa_reaction;
        if(ownChanged||JSON.stringify(prior.wa_peer_reactions||[])!==JSON.stringify(message.wa_peer_reactions||[])){
          const article=[...chat.querySelectorAll('[data-message-id]')].find(node=>node.dataset.messageId===String(message.id));
          paintReactions(article,message);
        }
        Object.assign(prior,message);}
    }
    retainOperatorEvents(old,latest);const timelinePlaced=placeOperatorEvents(chat,old);
    for(const [key,value] of Object.entries(latest))if(!['messages','next_before','anchor_message_id','operator_events','operator_events_revision','operator_events_omitted_at','operator_events_retry_after_ms','operatorEventsRetryAt','shared_reminder_events','shared_reminder_events_omitted_at','sharedReminderTimeline','sharedReminderOmission'].includes(key))old[key]=value;
    updateLatestIncomingShortcut(chat,old);
    currentDialog=old.dialog;displayedPage=old;renderHistoryMergeReadPicker();if(historyMergeId(old)||$('operatorComposerSource')?.dataset.mergeActionScope)renderComposerSourcePicker();updateDialogueAcceptor(currentDialog);mountDialogCompany(currentDialog);rememberDialogPage(old.dialog,old);restoreCaseDraft(old.dialog,$('chatText'));
    setTimelineScroll(chat,follow?chat.scrollHeight:timelineScrollTop(top,anchor),timelinePlaced||suppressRead);
    document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));
    return true;
  }
  function renderDialog(page,{suppressRead=false,replaceMergedHistory=false}={}){
    const route=page.dialog,host=$('operatorConversationContent');if(!host)return;const existingShell=host.querySelector('[data-operator-dialog]');
    if(!replaceMergedHistory&&existingShell?.dataset.dialogKey===dialogKey(route)&&existingShell.dataset.copilotBusy==='true'&&(!route.lifecycle_enabled||displayedRouteScope===dialogRenderScope(route))){updateDialogueAcceptor(route);copilotDeferredPage=page;return;}
    if(!replaceMergedHistory&&existingShell?.dataset.dialogKey===dialogKey(route)&&patchDialogPage(page,{suppressRead})){copilotDeferredPage=null;return;}
    const existingVoice=host.querySelector('[data-copilot-voice]');
    if(!replaceMergedHistory&&existingShell?.dataset.dialogKey===dialogKey(route)&&existingVoice?.isVoiceActive?.()&&(!route.lifecycle_enabled||displayedRouteScope===dialogRenderScope(route))){copilotDeferredPage=page;return;}
    if(existingVoice?.disposeVoice)void existingVoice.disposeVoice().catch(()=>{});
    if(copilotDeferredPage&&dialogKey(copilotDeferredPage.dialog)!==dialogKey(route))copilotDeferredPage=null;
    document.dispatchEvent(new CustomEvent('tenant-chat-render'));
    const captured=captureDialogInteractionState(route,host)||helperDrafts.get(contextKey('helper',route));
    const interactionState=replaceMergedHistory&&captured?{draft:captured.draft,focus:captured.focus==='composer'?'composer':'',selection:captured.selection}:captured;
    if(replaceMergedHistory)helperDrafts.delete(contextKey('helper',route));
    const companyToggleFocused=document.activeElement?.id==='operatorCompanyPanelOpen';
    const retainedMedia=new Map();
    if(!replaceMergedHistory&&existingShell?.dataset.dialogKey===dialogKey(route))host.querySelectorAll('[data-chat-attachment]').forEach(node=>{if(/^[a-f0-9]{64}$/.test(node.dataset.mediaVersion||''))retainedMedia.set([node.dataset.chatAttachment,node.dataset.attachmentLeadId,node.dataset.channelId,node.dataset.conversationId,node.dataset.mediaVersion,node.dataset.mediaKind].join('|'),node);});
    if(existingShell?.dataset.dialogKey!==dialogKey(route))document.dispatchEvent(new CustomEvent('tenant-chat-open',{detail:{lead_id:route.lead_id,surface:'operator'}}));
    const revision=openRevision,version=epoch,who=identity(member),renderSerial=++dialogRenderSerial;
    const same=()=>renderSerial===dialogRenderSerial&&version===epoch&&revision===openRevision&&currentDialog?.lead_id===route.lead_id&&currentDialog?.channel_id===route.channel_id&&currentDialog?.conversation_id===route.conversation_id&&host.querySelector('[data-operator-dialog]');
    const reasonText=dialogReason(route);
    host.innerHTML=`<section data-operator-dialog data-dialog-key="${esc(dialogKey(route))}" ${route.owner_overview_read_only?'data-owner-overview-read-only':''} class="operator-dialog-shell"><p id="operatorChatError" class="operator-error" role="status"></p><div class="operator-dialog-body" id="operatorDialogBody"><section class="operator-chat-main"><div id="operatorMergedReadRoutes" hidden></div><div class="operator-chat-scrollarea"><div class="operator-history-tools"><button type="button" class="outline" id="operatorEarlier" ${page.next_before?'':'hidden'}>${t('earlier')}</button><button type="button" id="operatorLatestIncoming" hidden>${esc(({ru:'К последнему входящему',az:'Son gələn mesaja',en:'Latest incoming'})[i18n.language]||'Latest incoming')}</button></div><div id="chatRows" class="operator-chat-history">${messageMarkup(page.messages)||t('emptyHistory')}</div><button type="button" class="outline" id="operatorNewMessages" hidden>${esc(({ru:'Новые сообщения ↓',az:'Yeni mesajlar ↓',en:'New messages ↓'})[i18n.language]||'New messages ↓')}</button></div><p id="operatorAIReplyStatus" class="operator-error" role="status" aria-live="polite"></p><div id="operatorAIContextUsage" class="operator-ai-context" hidden></div><p id="operatorSendReason" class="sub"></p><div id="operatorAcceptAction" class="operator-accept-action"></div><div class="operator-composer"><div id="operatorComposerSource" hidden></div><div id="operatorReplyQuote" hidden></div><textarea id="chatText" data-operator-composer rows="1" aria-label="${esc(t('message'))}" placeholder="${esc(t('message'))}"></textarea><div class="operator-composer-actions"><button type="button" class="outline operator-icon-action" id="operatorCopilotToggle" aria-label="${esc(copilotText('title'))}" title="${esc(copilotText('title'))}"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="m12 3 1.5 6.5L20 11l-6.5 1.5L12 19l-1.5-6.5L4 11l6.5-1.5L12 3Zm7 11 .8 3.2L23 18l-3.2.8L19 22l-.8-3.2L15 18l3.2-.8L19 14Z"/></svg></button><div id="operatorActiveLifecycleActions" class="operator-active-lifecycle-actions"></div><button type="button" class="outline operator-icon-action" id="operatorAIReply" aria-label="${esc(t('aiReply'))}" title="${esc(t('aiReply'))}"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 3.5 13.8 9l5.7 1.8-5.7 1.8L12 18.3l-1.8-5.7-5.7-1.8L10.2 9 12 3.5Zm7.2 11.8.9 2.6 2.4.8-2.4.8-.9 2.5-.8-2.5-2.5-.8 2.5-.8.8-2.6ZM5 15l.8 2.2 2.2.8-2.2.8L5 21l-.8-2.2-2.2-.8 2.2-.8L5 15Z"/></svg></button><button type="button" class="outline operator-icon-action operator-voice-launcher" id="operatorCopilotVoiceToggle" aria-label="${esc(copilotText('voice'))}" title="${esc(copilotText('voice'))}" data-voice-state="idle"><span class="operator-voice-wave" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></span></button><button type="button" class="outline" id="operatorTraining">${esc(trainingText('button'))}</button><button type="button" class="primary" id="sendChat" aria-label="${esc(t('send'))}" title="${esc(t('send'))}">↑</button></div></div></section><div class="operator-copilot-backdrop" id="operatorCopilotBackdrop" hidden aria-hidden="true"></div><section class="operator-copilot" id="operatorCopilot" role="dialog" aria-label="${esc(copilotText('title'))}" hidden><header class="operator-copilot-header"><div><strong>${copilotText('title')}</strong><small>${esc(dialogTitle(route))}</small></div><button type="button" class="operator-icon-button" id="operatorCopilotClose" aria-label="${esc(completionText('close'))}" title="${esc(completionText('close'))}">×</button></header><div class="operator-copilot-actions operator-copilot-modes" aria-label="${esc(copilotText('title'))}"><button type="button" class="outline" data-copilot-mode="chat">${copilotText('question')}</button><button type="button" class="outline" data-copilot-mode="solution">${copilotText('solution')}</button><button type="button" class="outline" data-copilot-mode="instruction">${copilotText('instruction')}</button></div><label class="operator-copilot-prompt-label" for="operatorCopilotPrompt">${copilotText('prompt')}</label><textarea id="operatorCopilotPrompt" maxlength="1600" aria-label="${copilotText('prompt')}" placeholder="${copilotText('prompt')}"></textarea><div class="operator-copilot-actions"><button type="button" class="primary" id="operatorCopilotAsk">${copilotText('ask')}</button><button type="button" class="outline" id="operatorCopilotClear">${copilotText('clear')}</button></div><p id="operatorCopilotStatus" class="operator-error" role="status"></p><div id="operatorCopilotResult" class="operator-copilot-result" hidden></div><button type="button" class="outline" id="operatorCopilotInsert" hidden>${copilotTemplateText('insertResult')}</button><details id="operatorCopilotTemplates" class="operator-copilot-templates" hidden aria-label="${copilotTemplateText('title')}"><summary>${copilotTemplateText('title')}</summary><div id="operatorCopilotTemplateList" class="operator-copilot-template-list"></div><button type="button" class="outline" id="operatorCopilotTemplateCreate">${copilotTemplateText('create')}</button><form id="operatorCopilotTemplateForm" hidden><label>${copilotTemplateText('name')}<input id="operatorCopilotTemplateName" maxlength="80" required></label><label>${copilotTemplateText('question')}<textarea id="operatorCopilotTemplateQuestion" maxlength="1600" required></textarea></label><div class="operator-copilot-actions"><button type="submit" class="primary">${copilotTemplateText('save')}</button><button type="button" class="outline" id="operatorCopilotTemplateCancel">${copilotTemplateText('cancel')}</button></div></form><p id="operatorCopilotTemplateStatus" class="operator-error" role="status" aria-live="polite"></p></details></section></div></section>`;
    const saveDisplayName=async(kind,displayName,participantJid='',source=route)=>{await api('dialogs/display-name','POST',{provider:'connect',lead_id:source.source_lead_id||route.lead_id,channel_id:source.source_channel_id||route.channel_id,conversation_id:route.conversation_id,kind,display_name:displayName,...(participantJid?{participant_jid:participantJid}:{})});const sourceViewCurrent=()=>who===identity(member)&&hasRight('dialogs')&&sameDialog(route,currentDialog)&&$('view-dialogs')?.classList.contains('active');if(!sourceViewCurrent())return false;const saved=t(kind==='participant'?'participantNameSaved':'nameSaved');if(kind==='participant')notice(saved);await refresh();if(!sourceViewCurrent())return false;if(!await openDialog({...currentDialog},true))throw Error(saved+' '+t('error'));return true;};
    configureDialogHead(route,saveDisplayName);
    if(companyToggleFocused)$('operatorCompanyPanelOpen')?.focus({preventScroll:true});
    const beginParticipantRename=button=>{const row=page.messages.find(m=>String(m.id)===button.dataset.participantMenu),participantJid=String(button.dataset.participantJid||'');if(!row||!participantJid)return;const meta=button.closest('.operator-message-meta');if(!meta||meta.querySelector('[data-participant-name-form]'))return;meta.querySelectorAll('[data-participant-context-menu]').forEach(menu=>menu.remove());button.hidden=true;const form=document.createElement('form');form.dataset.participantNameForm='true';form.className='operator-inline-name-edit';form.innerHTML=`<input maxlength="180" aria-label="${esc(t('displayName'))}"><button type="submit" class="operator-icon-button" aria-label="${esc(t('saveEdit'))}" title="${esc(t('saveEdit'))}">✓</button><button type="button" class="operator-icon-button" aria-label="${esc(t('cancelEdit'))}" title="${esc(t('cancelEdit'))}">×</button>`;const input=form.querySelector('input');input.value=row.author_name||'';form.querySelector('[type=button]').onclick=()=>{form.remove();button.hidden=false;};form.onsubmit=async event=>{event.preventDefault();const save=form.querySelector('[type=submit]');save.disabled=true;try{await saveDisplayName('participant',input.value,participantJid,row);}catch(error){if(same()){$('operatorChatError').textContent=error.message;save.disabled=false;}}};meta.append(form);input.focus();};
    const beginParticipantEmployeeLink=async button=>{const jid=String(button.dataset.participantJid||''),match=/^([0-9]{8,15})@s\.whatsapp\.net$/.exec(jid);if(!match){$('operatorChatError').textContent=t('employeeNumberUnavailable');return;}const meta=button.closest('.operator-message-meta');if(!meta||meta.querySelector('[data-employee-link-form]'))return;try{const response=await rawApi('/api/platform/members');const staff=(response.members||[]).filter(item=>item.active);if(!staff.length)throw new Error(t('employeeListUnavailable'));const form=document.createElement('form');form.dataset.employeeLinkForm='true';form.className='operator-inline-name-edit';const select=document.createElement('select'),save=document.createElement('button'),cancel=document.createElement('button'),status=document.createElement('span');select.setAttribute('aria-label',t('chooseEmployee'));select.append(new Option(t('chooseEmployee'),''));staff.forEach(item=>select.append(new Option(`${item.display_name||t('employee')} · ${item.role}`,String(item.telegram_id))));save.type='submit';save.className='operator-icon-button';save.textContent='✓';save.setAttribute('aria-label',t('saveEdit'));cancel.type='button';cancel.className='operator-icon-button';cancel.textContent='×';cancel.setAttribute('aria-label',t('cancelEdit'));status.setAttribute('role','status');form.append(select,save,cancel,status);cancel.onclick=()=>form.remove();form.onsubmit=async event=>{event.preventDefault();if(!select.value){status.textContent=t('chooseEmployee');return;}save.disabled=true;try{await rawApi('/api/platform/members',{method:'POST',body:JSON.stringify({action:'add_whatsapp_number',telegram_id:Number(select.value),phone:'+'+match[1],expected_tenant_id:member?.tenant_id,expected_user_id:member?.telegram_id})});status.textContent=t('employeeNumberSaved');document.dispatchEvent(new CustomEvent('tenant-members-changed'));await refresh();if(same())await openDialog(route);}catch(error){status.textContent=error.message;save.disabled=false;}};meta.append(form);select.focus();}catch(error){$('operatorChatError').textContent=error.message;}};
    const canManageEmployeeIdentities=()=>member?.role==='owner'||member?.role==='admin'||(member?.permissions||[]).includes('employees.manage');
    const openParticipantMenu=button=>{host.querySelectorAll('[data-participant-context-menu]').forEach(menu=>menu.remove());const menu=document.createElement('div'),rename=document.createElement('button');menu.className='operator-participant-menu';menu.dataset.participantContextMenu='true';menu.setAttribute('role','menu');rename.type='button';rename.dataset.participantRename='true';rename.textContent=t('editParticipantName');menu.append(rename);if(canManageEmployeeIdentities()){const link=document.createElement('button');link.type='button';link.dataset.participantEmployeeLink='true';link.dataset.participantJid=button.dataset.participantJid||'';link.textContent=t('markAsEmployee');menu.append(link);}button.closest('.operator-message-meta')?.append(menu);rename.focus();};
    host.onclick=event=>{const back=event.target.closest('[data-operator-back]');if(back&&host.contains(back)){closeDialog();return;}const link=event.target.closest('[data-participant-employee-link]');if(link&&host.contains(link)){beginParticipantEmployeeLink(link);return;}const rename=event.target.closest('[data-participant-rename]');if(rename&&host.contains(rename)){const name=rename.closest('.operator-message-meta')?.querySelector('[data-participant-menu]');if(name)beginParticipantRename(name);return;}const button=event.target.closest('[data-participant-menu]');if(button&&host.contains(button)){openParticipantMenu(button);return;}host.querySelectorAll('[data-participant-context-menu]').forEach(menu=>menu.remove());};
    host.onkeydown=event=>{const button=event.target.closest?.('[data-participant-menu]');if(button&&(event.key==='Enter'||event.key===' ')){event.preventDefault();openParticipantMenu(button);}};
    const actions=new Set(route.lifecycle_enabled?[]:route.available_actions||[]),primaryHost=$('operatorPrimaryAction'),moreHost=$('operatorMenuExtra');
    const actionButton=(label,action,primary=false)=>{const button=document.createElement('button');button.type='button';button.className=primary?'primary':'outline';button.textContent=t(label);button.dataset.operatorAction=action;button.disabled=!route.controls_allowed;button.onclick=async()=>{button.disabled=true;try{await changeState(route,action);if(same())await openDialog(route);}catch(error){if(same())$('operatorChatError').textContent=error.message;}finally{if(button.isConnected)button.disabled=!route.controls_allowed;}};return button;};
    const resumeUiAllowed=actions.has('resume')&&route.agent_mode!=='manual';
    let primaryAction='';if(actions.has('pause')){primaryAction='pause';primaryHost.append(actionButton('switchToManual','pause',true));}else if(resumeUiAllowed){primaryAction='resume';primaryHost.append(actionButton('resumeAutomatic','resume',true));}
    if(route.provider==='connect'&&!route.archive_read_only){const configure=document.createElement('button');configure.type='button';configure.className='outline';configure.textContent=t('groupSettingsTitle');configure.dataset.operatorGroupSettings='true';configure.onclick=()=>openGroupSettings(route);primaryHost.append(configure);}
    const menu=$('operatorMoreActions'),menuList=moreHost;
    const appendMenu=button=>{button.setAttribute('role','menuitem');menuList.append(button);};
    if(route.test_recording_import_allowed){
      const upload=document.createElement('button');upload.type='button';upload.textContent='Добавить тестовую запись звонка';
      upload.onclick=()=>{const file=document.createElement('input');file.type='file';file.accept='.beincall.zip,application/zip';
        file.onchange=async()=>{if(!same()||!file.files?.[0])return;const selected=file.files[0];if(selected.size>7696400){$('operatorChatError').textContent='Запись слишком большая';return;}upload.disabled=true;
          const query=new URLSearchParams({channel_id:String(route.channel_id),conversation_id:String(route.conversation_id),lead_id:String(route.lead_id),expected_tenant_id:String(member.tenant_id),expected_user_id:String(member.telegram_id)});
          try{const response=await fetch('/api/platform/test-call-recordings?'+query,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/zip'},body:selected});const result=await response.json();if(!response.ok)throw new Error(result.error||'Не удалось сохранить запись');if(same())await openDialog(route,true);}
          catch(error){if(same())$('operatorChatError').textContent=error.message;}finally{if(upload.isConnected)upload.disabled=false;}
        };file.click();};appendMenu(upload);
    }
    if(actions.has('pause')&&primaryAction!=='pause')appendMenu(actionButton('switchToManual','pause'));
    if(actions.has('handoff')&&primaryAction!=='handoff')appendMenu(actionButton('takeover','handoff'));
    if(resumeUiAllowed&&primaryAction!=='resume')appendMenu(actionButton('resumeAutomatic','resume'));
    if(route.lifecycle_enabled)renderLifecycleActions(route,primaryHost);
    if(route.provider==='connect'&&canCreateSupport()&&canCreateManualCase(route)){const create=document.createElement('button');create.type='button';create.className='outline operator-create-ticket-compact';create.setAttribute('aria-label',t('createTicket'));create.title=t('createTicket');const plus=document.createElement('span');plus.textContent='+';plus.setAttribute('aria-hidden','true');const caption=document.createElement('span');caption.textContent=({ru:'Обращение',az:'sorğu',en:'Ticket'}[i18n.language]||t('createTicketCompact'));create.append(plus,caption);create.onclick=()=>openManualCaseCreate(route,create,same);create.dataset.operatorCreateTicket='true';$('operatorCreateTicketHost')?.append(create);if(!route.lifecycle_enabled){const complete=document.createElement('button');complete.type='button';complete.className='outline';complete.textContent=completionText('complete');complete.dataset.operatorComplete='true';complete.onclick=()=>openCompletion(route);primaryHost.append(complete);}}
    if(!route.archive_read_only){const diagnostics=document.createElement('button');diagnostics.type='button';diagnostics.textContent=t('diagnostics');diagnostics.dataset.operatorDiagnostics='true';diagnostics.onclick=()=>{menu.open=false;openDialogDiagnostics(route);};appendMenu(diagnostics);}
    const trainingButton=$('operatorTraining'),caseAllowed=hasRight('dialogs')&&hasRight('support');trainingButton.hidden=!!route.lifecycle_enabled;trainingButton.disabled=!!route.lifecycle_enabled||route.provider!=='connect'||!route.agent_id||!caseAllowed;trainingButton.title=route.agent_status==='restricted'?assignmentLabel({...route,assignment_display:null}):!route.agent_id?trainingText('noAgent'):trainingButton.disabled?trainingText('noAccess'):trainingText('button');trainingButton.onclick=()=>openTraining(route,same);
    if(!route.lifecycle_enabled&&route.provider==='connect'){
      const menuTraining=document.createElement('button');menuTraining.type='button';menuTraining.id='operatorTrainingMenuAction';menuTraining.className='outline';menuTraining.textContent=trainingText('button');menuTraining.title=trainingButton.title;menuTraining.disabled=trainingButton.disabled;menuTraining.onclick=()=>{if(!same())return;menu.open=false;openTraining(route,same);};appendMenu(menuTraining);
    }
    const sendDraftKey=contextKey('draft',route),sendAllowed=route.can_send&&hasRight('dialogs');$('sendChat').disabled=!sendAllowed;$('chatText').disabled=!sendAllowed;
    let copilotMode=interactionState?.copilotMode||'chat',copilotBusy=false,copilotAnswer='',editingCopilotTemplateId=interactionState?.templateId||'';
    const copilotPanel=$('operatorCopilot'),copilotStatus=$('operatorCopilotStatus'),copilotResult=$('operatorCopilotResult'),copilotAsk=$('operatorCopilotAsk'),copilotInsert=$('operatorCopilotInsert');
    const dialogAiAssigned=route.provider==='connect'&&!!route.agent_id;
    const dialogAiAllowed=dialogAiAssigned&&route.can_use_copilot!==false;
    const dialogAiBlockReason=route.agent_status==='restricted'?assignmentLabel({...route,assignment_display:null}):!dialogAiAssigned?trainingText('noAgent'):({ru:'Нет права на использование AI-помощника',az:'AI köməkçidən istifadə icazəsi yoxdur',en:'You do not have permission to use the AI assistant'}[i18n.language]||'You do not have permission to use the AI assistant');
    for(const node of [$('operatorAIReply'),$('operatorCopilotToggle'),$('operatorCopilotVoiceToggle'),copilotAsk,...host.querySelectorAll('[data-copilot-mode]')]){node.disabled=!dialogAiAllowed;if(!dialogAiAllowed)node.title=dialogAiBlockReason;}
    if(!dialogAiAllowed)$('operatorAIReplyStatus').textContent=dialogAiAssigned?dialogAiBlockReason:'';
    const templateSection=$('operatorCopilotTemplates'),templateList=$('operatorCopilotTemplateList'),templateForm=$('operatorCopilotTemplateForm'),templateName=$('operatorCopilotTemplateName'),templateQuestion=$('operatorCopilotTemplateQuestion'),templateStatus=$('operatorCopilotTemplateStatus');
    const templateStorageKey=()=>`tenant.operator.copilot-question-templates.${identity(member)}`;
    const readCopilotTemplates=()=>{try{const items=JSON.parse(localStorage.getItem(templateStorageKey())||'[]');return Array.isArray(items)?items.filter(item=>item&&typeof item.id==='string'&&typeof item.name==='string'&&typeof item.prompt==='string').slice(0,20):[];}catch{return[];}};
    const writeCopilotTemplates=items=>{try{localStorage.setItem(templateStorageKey(),JSON.stringify(items));return true;}catch{templateStatus.textContent=copilotTemplateText('saveFailed');return false;}};
    function renderCopilotTemplates(){templateSection.hidden=copilotMode!=='instruction';templateList.replaceChildren();const items=readCopilotTemplates();if(!items.length){const empty=document.createElement('span');empty.className='sub';empty.textContent=copilotTemplateText('empty');templateList.append(empty);}for(const item of items){const row=document.createElement('div');row.className='operator-copilot-template-row';const use=document.createElement('button');use.type='button';use.className='outline';use.textContent=item.name;use.dataset.templateUse=item.id;use.title=item.prompt;use.onclick=()=>{if(!same())return;$('operatorCopilotPrompt').value=item.prompt;$('operatorCopilotPrompt').dispatchEvent(new Event('input',{bubbles:true}));templateStatus.textContent='';$('operatorCopilotPrompt').focus();};const edit=document.createElement('button');edit.type='button';edit.className='outline';edit.textContent=copilotTemplateText('edit');edit.dataset.templateEdit=item.id;edit.onclick=()=>{editingCopilotTemplateId=item.id;templateName.value=item.name;templateQuestion.value=item.prompt;templateStatus.textContent='';templateForm.dataset.editingId=editingCopilotTemplateId;templateForm.hidden=false;templateSection.open=true;templateName.focus();};const remove=document.createElement('button');remove.type='button';remove.className='outline';remove.textContent=copilotTemplateText('delete');remove.dataset.templateDelete=item.id;remove.onclick=()=>{if(!same()||!window.confirm(copilotTemplateText('deleteConfirm')))return;if(writeCopilotTemplates(readCopilotTemplates().filter(row=>row.id!==item.id)))renderCopilotTemplates();};row.append(use,edit,remove);templateList.append(row);}}
    $('operatorCopilotTemplateCreate').onclick=()=>{editingCopilotTemplateId='';templateForm.reset();templateStatus.textContent='';templateForm.dataset.editingId=editingCopilotTemplateId;templateForm.hidden=false;templateSection.open=true;templateName.focus();};$('operatorCopilotTemplateCancel').onclick=()=>{templateForm.hidden=true;editingCopilotTemplateId='';templateForm.reset();};templateForm.onsubmit=event=>{event.preventDefault();if(!same())return;const name=templateName.value.trim().slice(0,80),prompt=templateQuestion.value.trim().slice(0,1600);if(!name||!prompt)return;const items=readCopilotTemplates();if(!editingCopilotTemplateId&&items.length>=20){templateStatus.textContent=copilotTemplateText('limit');return;}const id=editingCopilotTemplateId||crypto.randomUUID?.()||`template-${Date.now()}-${Math.random()}`;const next=editingCopilotTemplateId?items.map(item=>item.id===editingCopilotTemplateId?{...item,name,prompt}:item):[...items,{id,name,prompt}];if(writeCopilotTemplates(next)){templateForm.hidden=true;templateForm.reset();editingCopilotTemplateId='';templateStatus.textContent='';renderCopilotTemplates();}};
    renderCopilotTemplates();
    const copilotBackdrop=$('operatorCopilotBackdrop'),copilotBody=$('operatorDialogBody'),copilotToggle=$('operatorCopilotToggle');
    const insertCopilotAnswer=()=>{if(!same()||!sendAllowed)return;const signature=copilotInsert.dataset.contextSignature,currentSignature=copilotContextSignature(currentPage?.dialog||currentDialog);if(!signature||signature!==currentSignature){copilotStatus.textContent=copilotTemplateText('staleResult');return;}const answer=copilotResult.textContent;if(!answer.trim())return;const composer=$('chatText');if(composer.value.trim()&&!window.confirm(copilotText('replace')))return;composer.value=answer;composer.dispatchEvent(new Event('input',{bubbles:true}));composer.focus();copilotStatus.textContent=copilotTemplateText('inserted');};copilotInsert.onclick=insertCopilotAnswer;
    const closeCopilot=()=>closeCopilotPanel();
    let voiceSection=null;
    const openCopilot=()=>{copilotPanel.hidden=false;const mobile=!!window.matchMedia?.('(max-width: 900px)').matches;copilotBackdrop.hidden=!mobile;copilotBody.dataset.copilotOpen=String(mobile);copilotPanel.setAttribute('aria-modal',String(mobile));if(mobile&&$('view-dialogs')?.classList.contains('active')){if(!history.state?.operatorDialog)rememberDialogHistory(route);if(history.state?.operatorHelper!==contextKey('helper',route))history.pushState({...history.state,operatorHelper:contextKey('helper',route)},'');}copilotToggle.setAttribute('aria-expanded','true');syncMobileViewport();void voiceSection?.activateVoice?.();const helperType=$('dialogHelperType');if(helperType){copilotPanel.scrollTop=0;helperType.focus({preventScroll:true});}else $('operatorCopilotPrompt').focus();};
    copilotToggle.setAttribute('aria-expanded','false');copilotToggle.onclick=()=>copilotPanel.hidden?openCopilot():closeCopilot();$('operatorCopilotClose').onclick=closeCopilot;
    host.querySelectorAll('[data-copilot-mode]').forEach(button=>button.onclick=()=>{copilotMode=button.dataset.copilotMode;host.querySelectorAll('[data-copilot-mode]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));renderCopilotTemplates();if(copilotMode==='solution')submitCopilot(copilotMode);else $('operatorCopilotPrompt').focus();});
    async function submitCopilot(mode=copilotMode){
      if(copilotBusy||!same())return;
      if(!dialogAiAllowed){$('operatorAIReplyStatus').textContent=dialogAiBlockReason;return;}
      const prompt=$('operatorCopilotPrompt').value.trim();if(['chat','instruction'].includes(mode)&&!prompt){copilotStatus.textContent=copilotText('prompt');$('operatorCopilotPrompt').focus();return;}
      const contextBox=$('operatorAIContextUsage');let hasIncomingMedia=false;if(mode==='reply'){contextBox.hidden=true;contextBox.replaceChildren();hasIncomingMedia=(currentPage?.messages||[]).some(message=>message.direction==='incoming'&&['image','document'].includes(String(message.message_type||'').toLowerCase()));if(hasIncomingMedia&&!window.confirm(copilotText('mediaConsent')))return;}copilotBusy=true;host.querySelector('[data-operator-dialog]')?.setAttribute('data-copilot-busy','true');copilotAsk.disabled=true;$('operatorAIReply').disabled=true;if(mode==='reply'){$('chatText').disabled=true;$('operatorAIReplyStatus').textContent=copilotText('working');}else copilotStatus.textContent=copilotText('working');copilotResult.hidden=true;copilotInsert.hidden=true;
      if(mode==='reply'&&$('chatText').value.trim()&&!window.confirm(t('replaceDraftConfirm'))){copilotBusy=false;host.querySelector('[data-operator-dialog]')?.removeAttribute('data-copilot-busy');copilotAsk.disabled=false;$('operatorAIReply').disabled=false;return;}const requestContextSignature=copilotContextSignature(currentPage?.dialog||currentDialog);let requestHandle=null;
      let audioRefreshSources=null;
      try{const latestIncoming=[...(currentPage?.messages||[])].reverse().find(message=>message.direction==='incoming'&&!['employee','operator','ai'].includes(message.author_type));
        const logical={mode,prompt:mode==='reply'?'':String(prompt).trim(),include_media:mode==='reply'&&hasIncomingMedia===true,assigned_context:requestContextSignature,latest_incoming_id:latestIncoming?.id||latestIncoming?.external_id||''};
        requestHandle=await copilotRequestIdentity(route,logical,same);if(!same())return;
        const result=await api('dialogs/copilot','POST',{provider:'connect',lead_id:route.lead_id,channel_id:route.channel_id,conversation_id:route.conversation_id,mode,prompt:logical.prompt,request_id:requestHandle.id,...(mode==='reply'?{include_media:logical.include_media}:{})});
        finishCopilotRequest(requestHandle);if(!same())return;copilotAnswer=String(result.text||'');if(mode==='reply'){if(copilotAnswer.trim()){$('chatText').value=copilotAnswer;$('chatText').dispatchEvent(new Event('input',{bubbles:true}));$('chatText').focus();$('operatorAIReplyStatus').textContent=t('aiReplyReady');showCopilotContext(result.context_usage||{});const sources=new Set((result.context_usage?.evidence||[]).filter(item=>['audio','voice','voice_message','ptt','image','video','document'].includes(String(item.kind||'').toLowerCase())).map(item=>String(item.source_id)));audioRefreshSources=sources;}else $('operatorAIReplyStatus').textContent=t('aiReplyError');}else{copilotResult.textContent=copilotAnswer;copilotResult.hidden=false;copilotInsert.dataset.contextSignature=requestContextSignature;copilotInsert.hidden=!copilotAnswer.trim()||!sendAllowed;const sourceTitles=(result.knowledge_sources||[]).map(source=>source.title).filter(Boolean);copilotStatus.replaceChildren();copilotStatus.removeAttribute('aria-label');if(sourceTitles.length){const sourceIcon=document.createElement('span');sourceIcon.textContent='▤';sourceIcon.setAttribute('aria-hidden','true');sourceIcon.title=copilotText('knowledge');copilotStatus.setAttribute('aria-label',copilotText('knowledge')+': '+sourceTitles.join(', '));copilotStatus.append(sourceIcon,document.createTextNode(' '+sourceTitles.join(', ')));}}
        if(result.history_status==='cleared'){const target=mode==='reply'?$('operatorAIReplyStatus'):copilotStatus;const message=({ru:'Контекст очищен во время ответа, поэтому ответ не сохранён в истории AI.',az:'Cavab hazırlanarkən kontekst təmizləndi, buna görə cavab AI tarixçəsində saxlanmadı.',en:'The context was cleared during this response, so it was not saved in AI history.'}[i18n.language]||'The context was cleared during this response, so it was not saved in AI history.');target.textContent=[target.textContent,message].filter(Boolean).join(' ');}
      }catch(error){if(error.keepRequest===false)finishCopilotRequest(requestHandle);if(same()){if(mode==='reply')$('operatorAIReplyStatus').textContent=error.message||t('aiReplyError');else copilotStatus.textContent=error.message||copilotText('failed');$('operatorAIContextUsage')?.replaceChildren();if($('operatorAIContextUsage'))$('operatorAIContextUsage').hidden=true;}}
      finally{copilotBusy=false;if(same()){host.querySelector('[data-operator-dialog]')?.removeAttribute('data-copilot-busy');if(copilotAsk.isConnected)copilotAsk.disabled=!dialogAiAllowed;if($('operatorAIReply')?.isConnected)$('operatorAIReply').disabled=!dialogAiAllowed;if($('chatText')?.isConnected)$('chatText').disabled=!sendAllowed;const deferred=copilotDeferredPage;copilotDeferredPage=null;if(deferred&&currentPage===deferred&&sameDialog(deferred.dialog,route)){renderDialog(deferred);const markDisplayed=()=>{if(version===epoch&&revision===openRevision&&currentPage===displayedPage&&sameDialog(deferred.dialog,currentDialog))void markDialogReadIfVisible(currentPage);};if(window.matchMedia?.('(max-width: 900px)').matches)afterLayout(markDisplayed);else markDisplayed();}if(audioRefreshSources?.size)void refreshAudioEvidence(route,audioRefreshSources);}}
    }
    const voiceRoute={provider:'connect',lead_id:route.lead_id,channel_id:route.channel_id,conversation_id:route.conversation_id};
    const voiceQuery=new URLSearchParams({...voiceRoute,expected_tenant_id:String(member?.tenant_id||''),expected_user_id:String(member?.telegram_id||'')});
    voiceSection=window.mountTenantCopilotVoice?.(host,{
      language:i18n.language,socketPath:endpoint+'dialogs/copilot-voice/ws?'+voiceQuery,
      preflight:()=>dialogAiAllowed&&(route.provider||'connect')==='connect'?api('dialogs/copilot-voice/preflight','POST',voiceRoute):Promise.resolve({available:false}),
      onState:state=>{const button=$('operatorCopilotVoiceToggle');if(button)button.dataset.voiceState=state;},
      isCurrent:()=>!!same()&&identity(member)===who,
      onIdle:()=>{const deferred=copilotDeferredPage;if(deferred&&version===epoch&&identity(member)===who&&currentPage===deferred&&sameDialog(deferred.dialog,route)&&sameDialog(currentDialog,route)){copilotDeferredPage=null;renderDialog(deferred);}},
    });
    copilotAsk.onclick=()=>submitCopilot(copilotMode==='instruction'?'instruction':'chat');
    $('operatorAIReply').onclick=()=>{if(copilotBusy)return;submitCopilot('reply');};
    $('operatorCopilotVoiceToggle').onclick=()=>{if(copilotBusy||!dialogAiAllowed)return;void voiceSection?.startVoice?.();};
    $('operatorCopilotClear').onclick=async()=>{if(copilotBusy)return;const button=$('operatorCopilotClear');button.disabled=true;try{await api('dialogs/copilot','POST',{provider:'connect',lead_id:route.lead_id,channel_id:route.channel_id,conversation_id:route.conversation_id,mode:'clear'});if(!same())return;copilotAnswer='';copilotResult.hidden=true;copilotInsert.hidden=true;copilotInsert.dataset.contextSignature='';copilotStatus.textContent=copilotText('cleared');}catch(error){if(same())copilotStatus.textContent=error.message;}finally{if(button.isConnected)button.disabled=false;}};
    let draftQuote=null,pendingReply=null;const replyBox=$('operatorReplyQuote');
    function hidePendingQuote(){
      pendingReply?.dismiss?.();
      pendingReply=null;
    }
    function drawReplyQuote(){
      const pending=!draftQuote&&Boolean(pendingReply?.data.quote_message_id);
      const target=draftQuote||(pending?pendingReply.data:null);
      replyBox.replaceChildren();
      replyBox.hidden=!target;
      if(!target){
        delete replyBox.dataset.quoteMessageId;
        delete replyBox.dataset.quoteChannelId;
        delete replyBox.dataset.quoteConversationId;
        delete replyBox.dataset.replyPending;
        return;
      }
      replyBox.dataset.quoteMessageId=target.quote_message_id;
      replyBox.dataset.quoteChannelId=target.quote_channel_id;
      replyBox.dataset.quoteConversationId=target.quote_conversation_id;
      replyBox.dataset.replyPending=String(pending);
      const source=page.messages.find(row=>String(row.id)===target.quote_message_id);
      const label=document.createElement('span');
      label.textContent=pending?replyText('pending'):source?messageAuthor(source)+': '+String(quotedMessagePreview(source)||t('attachment')).slice(0,160):replyText('preview');
      label.title=label.textContent;
      const cancel=document.createElement('button');
      const caption=replyText('remove');
      cancel.type='button';cancel.className='operator-cancel-reply';cancel.textContent='×';
      cancel.title=caption;cancel.setAttribute('aria-label',caption);
      cancel.onclick=()=>{
        if(pending)hidePendingQuote();
        else draftQuote=null;
        drawReplyQuote();
        $('chatText').focus();
      };
      replyBox.append(label,cancel);
    }
    function selectReplyMessage(messageId,{focus=true}={}){
      if(!same()||!sendAllowed)return false;
      const message=page.messages.find(row=>String(row.id)===String(messageId));
      if(!message)return false;
      if(!message.provider_message_id||String(message.source_channel_id||route.channel_id)!==String(route.channel_id)
        ||String(message.source_conversation_id||route.conversation_id)!==String(route.conversation_id)){
        $('operatorChatError').textContent=replyText('unavailable');
        return false;
      }
      $('operatorChatError').textContent='';
      hidePendingQuote();
      draftQuote={quote_message_id:String(message.id),quote_channel_id:route.channel_id,quote_conversation_id:route.conversation_id};
      drawReplyQuote();
      if(focus)$('chatText').focus();
      return true;
    }
    const chatRowsHost=$('chatRows'),swipeBlocked='button,a,input,select,textarea,audio,video,img,summary,[contenteditable=true],[data-quote-jump],[data-media-open],[data-media-download],[data-participant-menu],.operator-message-meta,.operator-media-preview,details[data-audio-transcript]';let replySwipe=null,replySwipeFeedbackTimer=null,replyLongPress=null,replyContextMenu=null;
    const audioScopeCurrent=()=>same()&&currentPage===page&&chatRowsHost.isConnected&&$('chatRows')===chatRowsHost;
    const audioMenuCopy=key=>t(key),audioResultSourceMatches=(result,message)=>result?.source&&String(result.source.channel_id)===String(route.channel_id)&&String(result.source.conversation_id)===String(route.conversation_id)&&String(result.source.message_id)===String(message.external_id)&&String(result.source.message_row_id)===String(message.id)&&(!result.message||(String(result.message.id)===String(message.id)&&String(result.message.external_id)===String(message.external_id))),audioRequestStorageKey=message=>`tenant.operator.audio-transcription-request.${identity(member)}.${route.channel_id}.${encodeURIComponent(route.conversation_id)}.${encodeURIComponent(message.id)}`;
    function rememberedAudioRequestKey(menu,message){if(menu?._audioIdempotencyKey)return menu._audioIdempotencyKey;try{const key=sessionStorage.getItem(audioRequestStorageKey(message));if(/^[0-9a-f-]{36}$/i.test(key||'')){menu._audioIdempotencyKey=key;return key;}if(key)sessionStorage.removeItem(audioRequestStorageKey(message));}catch{}return '';}
    function saveAudioRequestKey(menu,message,key){menu._audioIdempotencyKey=key;try{sessionStorage.setItem(audioRequestStorageKey(message),key);}catch{}}
    function clearAudioRequestKey(menu,message){delete menu._audioIdempotencyKey;try{sessionStorage.removeItem(audioRequestStorageKey(message));}catch{}}
    function audioMenuStatus(menu,key){const status=menu.querySelector('[data-audio-transcription-status]');if(status)status.textContent=audioMenuCopy(key);menu.querySelector('[data-audio-transcription-actions]')?.replaceChildren();}
    function renderAudioTranscriptionResult(menu,message,result,requestKey=''){
      const status=menu.querySelector('[data-audio-transcription-status]'),actions=menu.querySelector('[data-audio-transcription-actions]');if(!status||!actions)return;actions.replaceChildren();menu._audioResult=result;
      const state=result?.state,latest=result?.latest_success,history=Array.isArray(result?.history)?result.history:[];
      const requested=result?.requested_attempt,requestedState=String(requested?.state||'').toLowerCase(),requestedUnresolved=!!requestKey&&(!requested?state!=='ready':['reserved','sending','pending','unknown'].includes(requestedState));
      if(requestKey&&requested&&(requestedState==='completed'||requestedState==='failed_safe'))clearAudioRequestKey(menu,message);
      else if(requestKey&&!requested&&state==='ready')clearAudioRequestKey(menu,message);
      status.textContent=requestedUnresolved?(requestedState==='unknown'||state==='unknown'?audioMenuCopy('audioTranscriptionUnknown'):audioMenuCopy('audioTranscriptionPending')):state==='pending'?audioMenuCopy('audioTranscriptionPending'):state==='unknown'?audioMenuCopy('audioTranscriptionUnknown'):state==='blocked'?audioMenuCopy('audioTranscriptionBlocked'):'';
      const currentText=latest&&typeof latest.text==='string'?latest.text:'';
      if(currentText){const details=document.createElement('details'),summary=document.createElement('summary'),text=document.createElement('p');details.className='operator-audio-transcription-history';summary.textContent=latest.source_current===false?audioMenuCopy('audioTranscriptionPrevious'):audioMenuCopy('audioTranscriptionCurrent');text.textContent=currentText;details.append(summary,text);actions.append(details);}
      const prior=history.filter(entry=>entry&&entry.state==='completed'&&typeof entry.text==='string'&&entry.text&&String(entry.attempt_id||'')!==String(latest?.attempt_id||''));
      if(prior.length){const details=document.createElement('details'),summary=document.createElement('summary');details.className='operator-audio-transcription-history';summary.textContent=audioMenuCopy('audioTranscriptionHistory');for(const entry of prior){const item=document.createElement('p');item.textContent=[entry.created_at?i18n.date(entry.created_at):'',entry.text].filter(Boolean).join('\n');details.append(item);}details.prepend(summary);actions.append(details);}
      const allowed=Array.isArray(result?.allowed_actions)?result.allowed_actions:[],action=!requestedUnresolved&&state==='ready'&&allowed.includes('transcribe')?'transcribe':!requestedUnresolved&&state==='completed'&&allowed.includes('retranscribe')?'retranscribe':'';
      if(action){const button=document.createElement('button');button.type='button';button.className='outline';button.dataset.audioTranscriptionAction=action;button.textContent=audioMenuCopy(action==='retranscribe'?'retranscribeAudio':'transcribeAudio');actions.append(button);}
      else if(!status.textContent)status.textContent=audioMenuCopy('audioTranscriptionNoAction');
    }
    function applyAudioTranscriptionResult(message,result){
      const article=[...chatRowsHost.querySelectorAll('[data-message-id]')].find(node=>node.dataset.messageId===String(message.id));if(!article||!audioResultSourceMatches(result,message)||!audioScopeCurrent())return;const scrollTop=chatRowsHost.scrollTop;
      const dto=result.message&&typeof result.message==='object'?result.message:{},success=result.latest_success,text=success?.source_current===true&&typeof success.text==='string'?success.text:(dto.has_transcription&&typeof (dto.audio_transcript||dto.body)==='string'?(dto.audio_transcript||dto.body):'');
      for(const key of audioEvidenceFields)if(Object.prototype.hasOwnProperty.call(dto,key))message[key]=dto[key];
      if(text){message.has_transcription=true;message.audio_transcript=text;message.analysis_state='completed';}
      const analysis=article.querySelector('.operator-media-analysis');if(analysis)analysis.textContent=mediaAnalysisLabel(message);
      const prior=article.querySelector('[data-audio-transcript]');if(text){const details=document.createElement('details'),summary=document.createElement('summary'),body=document.createElement('div');details.dataset.audioTranscript='';summary.textContent=t('showTranscript');body.dataset.userContent='';body.textContent=text;details.append(summary,body);if(prior){details.open=prior.open;prior.replaceWith(details);}else article.insertBefore(details,article.querySelector('footer'));}else prior?.remove();
      const footer=article.querySelector('[data-message-footer-actions]');if(footer)footer.innerHTML=deliveryMarkup(message)+copyControlMarkup(message);
      chatRowsHost.scrollTop=scrollTop;rememberDialogPage(route,page);
    }
    async function loadAudioTranscriptionMenu(menu,force=false){
      if(!menu?.isConnected||!audioScopeCurrent())return;if(menu.dataset.posting==='true'||menu.dataset.loading==='true'||(menu.dataset.loaded==='true'&&!force))return;
      const message=page.messages.find(item=>String(item.id)===menu.dataset.messageRowId&&String(item.external_id)===menu.dataset.messageId);if(!message||message.source!=='connect'||!['audio','voice','voice_message','ptt'].includes(String(message.message_type||'').toLowerCase())){audioMenuStatus(menu,'audioTranscriptionUnavailable');menu.dataset.loaded='true';return;}
      if(!route.agent_id){audioMenuStatus(menu,'audioTranscriptionNoAgent');const button=document.createElement('button');button.type='button';button.disabled=true;button.textContent=audioMenuCopy('transcribeAudio');menu.querySelector('[data-audio-transcription-actions]')?.append(button);menu.dataset.loaded='true';return;}
      menu.dataset.loading='true';menu.dataset.loaded='false';audioMenuStatus(menu,'audioTranscriptionChecking');const query=new URLSearchParams({channel_id:String(route.channel_id),conversation_id:String(route.conversation_id),message_id:String(message.external_id),message_row_id:String(message.id)}),owner=who,rev=revision,openEpoch=version;
      const requestKey=rememberedAudioRequestKey(menu,message);if(requestKey)query.set('idempotency_key',requestKey);
      try{const response=await rawApi('/api/platform/ai/dialogs/audio-transcription?'+query);const result=response.result;if(owner!==identity(member)||openEpoch!==epoch||rev!==openRevision||!audioScopeCurrent()||!menu.isConnected)return;if(!audioResultSourceMatches(result,message)){audioMenuStatus(menu,'audioTranscriptionError');return;}menu.dataset.loaded='true';renderAudioTranscriptionResult(menu,message,result,requestKey);if(result.latest_success?.source_current===true)applyAudioTranscriptionResult(message,result);}
      catch(error){if(owner!==identity(member)||openEpoch!==epoch||rev!==openRevision||!audioScopeCurrent()||!menu.isConnected)return;menu.dataset.loaded='true';if(error.status===404)audioMenuStatus(menu,'audioTranscriptionUnavailable');else audioMenuStatus(menu,'audioTranscriptionError');}
      finally{delete menu.dataset.loading;if(!audioScopeCurrent())menu._audioReconcile?.();}
    }
    async function runAudioTranscription(menu,message,action){
      if(!audioScopeCurrent()||!menu.isConnected||menu.dataset.posting==='true'||!route.agent_id)return;
      const result=menu._audioResult;if(!audioResultSourceMatches(result,message)||!Array.isArray(result.allowed_actions)||!result.allowed_actions.includes(action)||!result.cost_token||!result.source?.source_version)return;
      const price=Number(result.price?.reserve_microusd),seconds=Number(result.price?.max_duration_seconds);if(!Number.isSafeInteger(price)||price<0||!Number.isFinite(seconds)||seconds<=0)return;
      const locale=i18n.language==='ru'?'ru-RU':i18n.language==='az'?'az-AZ':'en-GB',cost=new Intl.NumberFormat(locale,{style:'currency',currency:result.price.currency||'USD',minimumFractionDigits:2,maximumFractionDigits:6}).format(price/1e6),wholeMinutes=Math.floor(seconds/60),remainingSeconds=seconds-wholeMinutes*60,number=new Intl.NumberFormat(locale,{maximumFractionDigits:3}),durationText=wholeMinutes?(remainingSeconds?`${wholeMinutes} ${audioMenuCopy('audioMinuteUnit')} ${number.format(remainingSeconds)} ${audioMenuCopy('audioSecondUnit')}`:`${wholeMinutes} ${audioMenuCopy('audioMinuteUnit')}`):`${number.format(seconds)} ${audioMenuCopy('audioSecondUnit')}`;
      if(!window.confirm(audioMenuCopy('audioTranscriptionCost').replace('{cost}',cost).replace('{duration}',durationText)))return;
      const idempotencyKey=crypto.randomUUID();saveAudioRequestKey(menu,message,idempotencyKey);menu.dataset.posting='true';const actions=menu.querySelector('[data-audio-transcription-actions]'),status=menu.querySelector('[data-audio-transcription-status]');actions?.querySelectorAll('button').forEach(button=>button.disabled=true);if(status)status.textContent=audioMenuCopy('audioTranscriptionChecking');
      try{const body={channel_id:route.channel_id,conversation_id:route.conversation_id,message_id:message.external_id,message_row_id:message.id,source_version:result.source.source_version,action,cost_token:result.cost_token,idempotency_key:idempotencyKey,expected_tenant_id:member?.tenant_id,expected_user_id:member?.telegram_id};const response=await rawApi('/api/platform/ai/dialogs/audio-transcription',{method:'POST',body:JSON.stringify(body)});if(!audioScopeCurrent()||!menu.isConnected||!audioResultSourceMatches(response.result,message))return;menu.dataset.loaded='true';const confirmed=['completed','failed_safe'].includes(response.result.requested_attempt?.state||response.result.state);if(confirmed)clearAudioRequestKey(menu,message);renderAudioTranscriptionResult(menu,message,response.result,confirmed?'':idempotencyKey);if(response.result.latest_success?.source_current===true)applyAudioTranscriptionResult(message,response.result);}
      catch(error){if(!audioScopeCurrent()||!menu.isConnected)return;if(error.status&&error.status<500){clearAudioRequestKey(menu,message);menu.dataset.loaded='false';audioMenuStatus(menu,'audioTranscriptionError');}else{saveAudioRequestKey(menu,message,idempotencyKey);menu.dataset.loaded='false';audioMenuStatus(menu,'audioTranscriptionError');delete menu.dataset.posting;void loadAudioTranscriptionMenu(menu,true);}}
      finally{delete menu.dataset.posting;if(!audioScopeCurrent())menu._audioReconcile?.();}
    }
    chatRowsHost.addEventListener('toggle',event=>{const menu=event.target;if(!menu.matches?.('[data-audio-transcription-menu]'))return;if(menu.open)void loadAudioTranscriptionMenu(menu);else menu.dataset.loaded='false';},true);
    chatRowsHost.addEventListener('click',event=>{const action=event.target.closest('[data-audio-transcription-action]');if(action){event.preventDefault();const menu=action.closest('[data-audio-transcription-menu]'),message=page.messages.find(item=>String(item.id)===menu?.dataset.messageRowId&&String(item.external_id)===menu?.dataset.messageId);if(menu&&message)void runAudioTranscription(menu,message,action.dataset.audioTranscriptionAction);}});
    const reactionInFlight=new Set();
    const reactionStorageKey=message=>contextKey('reaction:'+message.id,route);
    const canReact=message=>!!(sendAllowed&&message.wa_reaction_available===true&&
      !route.owner_overview_read_only&&!route.search_overview_read_only&&!route.case_attachment&&
      (!historyMergeId(page)||(message.source_channel_id===route.channel_id&&
        message.source_conversation_id===route.conversation_id&&Number.isSafeInteger(message.source_lead_id)&&
        message.source_lead_id===Number(route.lead_id))));
    async function reactToMessage(message,article,selected){
      if(!same()||!canReact(message)||reactionInFlight.has(message.id))return;
      const storageKey=reactionStorageKey(message),pending=sessionStorage.getItem(storageKey);
      if(selected===undefined&&!pending)return;
      const requestId=pending||crypto.randomUUID();
      const emoji=selected===message.wa_reaction?'':selected;
      const identityFields={expected_tenant_id:member.tenant_id,expected_user_id:member.telegram_id};
      const query=new URLSearchParams({...identityFields,lead_id:route.lead_id,request_id:requestId});
      const check=()=>rawApi(reactionEndpoint+'?'+query,{}, {optionalScopedRead:true});
      const originalEvent=message.wa_reaction_event;
      reactionInFlight.add(message.id);
      sessionStorage.setItem(storageKey,requestId);
      closeReplyContextMenu(true);
      $('operatorChatError').textContent='';
      try{
        let result;
        if(pending)result=await check();
        else {
          try{
            result=await rawApi(reactionEndpoint,{method:'POST',body:JSON.stringify({
              ...identityFields,lead_id:route.lead_id,message_id:message.id,request_id:requestId,emoji
            })},{optionalScopedRead:true});
          }catch(error){if(error.status&&error.status<500)throw error;}
          if(!result||result.state==='unknown')result=await check();
        }
        if(result.state!=='accepted'){
          if(same())$('operatorChatError').textContent=reactionCopy('unknown');
          return;
        }
        if(sessionStorage.getItem(storageKey)===requestId)sessionStorage.removeItem(storageKey);
        if(same()&&article.isConnected&&message.wa_reaction_event===originalEvent){
          message.wa_reaction=result.emoji;
          paintReactions(article,message);
        }
      }catch(error){
        if((error.message==='reaction_request_unavailable'||!pending&&error.status&&error.status<500)&&sessionStorage.getItem(storageKey)===requestId)sessionStorage.removeItem(storageKey);
        if(same())$('operatorChatError').textContent=sessionStorage.getItem(storageKey)?reactionCopy('unknown'):reactionCopy('failed');
      }finally{reactionInFlight.delete(message.id);}
    }
    const messageMenuEvents=['tenant-chat-close','tenant-chat-open','tenant-chat-render','tenant-view','tenant-profile','tenant-auth-invalid','tenant-language'];
    function closeReplyContextMenu(restoreFocus=false){
      if(!replyContextMenu)return;const menu=replyContextMenu;replyContextMenu=null;
      document.removeEventListener('pointerdown',menu.outside,true);document.removeEventListener('keydown',menu.keys,true);
      for(const type of messageMenuEvents)document.removeEventListener(type,menu.closed);
      window.removeEventListener('resize',menu.position);window.visualViewport?.removeEventListener('resize',menu.position);window.visualViewport?.removeEventListener('scroll',menu.position);menu.observer?.disconnect();
      for(const {controller,parking} of menu.controllers){controller.hidden=true;controller.dataset.loaded='false';delete controller._audioReconcile;parking.append(controller);}
      menu.node.remove();if(restoreFocus&&menu.article.isConnected)menu.article.focus({preventScroll:true});
    }
    function openReplyContextMenu(article,messageId,x,y,touchedHost=null){
      if(route.owner_overview_read_only||route.search_overview_read_only)return false;
      if(!same()||currentPage!==page||!article.isConnected||!chatRowsHost.contains(article))return false;
      const message=page.messages.find(row=>String(row.id)===String(messageId));if(!message)return false;closeReplyContextMenu();
      const valid=()=>same()&&currentPage===page&&article.isConnected&&chatRowsHost.contains(article)&&article.dataset.messageId===String(messageId);
      const node=document.createElement('div');node.className='operator-reply-context-menu';node.tabIndex=-1;node.setAttribute('role','menu');node.setAttribute('aria-label',({ru:'Действия с сообщением',az:'Mesaj əməliyyatları',en:'Message actions'}[i18n.language]||'Message actions'));
      const addButton=(host,label,action)=>{const button=document.createElement('button');button.type='button';button.setAttribute('role','menuitem');button.textContent=label;button.onclick=()=>{if(valid())action(button);else closeReplyContextMenu();};host.append(button);return button;};
      const reply=addButton(node,replyText('action'),()=>{if(selectReplyMessage(messageId,{focus:false}))closeReplyContextMenu(true);});reply.dataset.replyContextAction='reply';reply.disabled=!sendAllowed||!message.provider_message_id;reply.title=reply.disabled?replyText('unavailable'):'';
      if(canReact(message)){
        if(sessionStorage.getItem(reactionStorageKey(message))){
          addButton(node,reactionCopy('check'),()=>void reactToMessage(message,article));
        }else{
          const picker=document.createElement('div');picker.className='operator-reaction-picker';
          picker.setAttribute('role','group');picker.setAttribute('aria-label',reactionCopy('action'));
          const label=document.createElement('span');label.textContent=reactionCopy('action');label.setAttribute('aria-hidden','true');picker.append(label);
          for(const emoji of reactionEmoji){
            const button=addButton(picker,emoji,()=>void reactToMessage(message,article,emoji));
            button.setAttribute('aria-label',emoji===message.wa_reaction?reactionCopy('remove'):reactionCopy('action')+' '+emoji);
            button.setAttribute('role','menuitemcheckbox');button.setAttribute('aria-checked',String(emoji===message.wa_reaction));
          }
          node.append(picker);
          if(message.wa_reaction&&!reactionEmoji.includes(message.wa_reaction)){
            addButton(node,reactionCopy('remove'),()=>void reactToMessage(message,article,''));
          }
        }
      }
      const controllers=[],preflights=[],mirrors=[],hosts=[...article.querySelectorAll('[data-chat-attachment]')],selected=touchedHost&&hosts.includes(touchedHost)?[touchedHost]:hosts;
      for(const file of selected){
        const sourceKey=()=>['chatAttachment','messageRowId','attachmentLeadId','channelId','conversationId','mediaVersion','mediaKind'].map(key=>file.dataset[key]||'').join('|'),source=sourceKey();
        const fileValid=()=>valid()&&file.isConnected&&article.contains(file)&&source===sourceKey();
        const group=document.createElement('div');group.dataset.contextFile=file.dataset.chatAttachment;group.setAttribute('role','group');
        const name=file.dataset.mediaFilename||mediaLabel(page.messages.find(row=>String(row.id)===file.dataset.messageRowId)||message),label=hosts.length>1?`${hosts.indexOf(file)+1}. ${name}`:name;group.setAttribute('aria-label',label);
        if(hosts.length>1){const heading=document.createElement('strong');heading.textContent=label;group.append(heading);}node.append(group);
        const primary=file.querySelector('[data-media-open]'),menuLoad=file.querySelector('[data-media-menu-load]'),download=file.querySelector('[data-media-download]'),fileStatus=file.querySelector('[data-media-load-status]');
        if(primary){const load=addButton(group,t('loadAttachment'),()=>{if(fileValid()&&!primary.disabled&&!(menuLoad?.hidden??primary.hidden))primary.click();});load.dataset.contextFileAction='load';mirrors.push(()=>{load.hidden=menuLoad?.hidden??primary.hidden;load.disabled=primary.disabled;load.textContent=primary.getAttribute('aria-busy')==='true'?primary.textContent:(menuLoad?.textContent||primary.textContent);load.setAttribute('aria-busy',String(primary.getAttribute('aria-busy')==='true'));});}
        if(download){const save=addButton(group,t('downloadAttachment'),()=>{if(fileValid()&&!download.disabled)download.click();});save.dataset.contextFileAction='download';mirrors.push(()=>{save.disabled=download.disabled;});}
        if(fileStatus){const feedback=document.createElement('span');feedback.setAttribute('role','status');feedback.setAttribute('aria-live','polite');group.append(feedback);mirrors.push(()=>{feedback.textContent=fileStatus.textContent;});}
        const controller=file.querySelector('[data-audio-transcription-menu]'),audioMessage=page.messages.find(row=>String(row.id)===file.dataset.messageRowId&&String(row.external_id)===file.dataset.chatAttachment);
        if(controller&&audioMessage&&String(controller.dataset.messageRowId)===String(audioMessage.id)&&String(controller.dataset.messageId)===String(audioMessage.external_id)){
          const parking=controller.parentElement;controllers.push({controller,parking});controller.hidden=false;group.append(controller);
          controller._audioReconcile=()=>{if(fileValid()&&node.isConnected)void loadAudioTranscriptionMenu(controller,true);};
          controller.onclick=event=>{const action=event.target.closest('[data-audio-transcription-action]');if(action&&controller.contains(action)){event.preventDefault();if(fileValid())void runAudioTranscription(controller,audioMessage,action.dataset.audioTranscriptionAction);else closeReplyContextMenu();}};
          // The existing controller retains posting/pending state when the message menu closes.
          if(controller.dataset.posting!=='true')preflights.push(()=>loadAudioTranscriptionMenu(controller,true));
        }
        mirrors.push(()=>{if(!fileValid())closeReplyContextMenu();});
      }
      node.addEventListener('pointerdown',()=>{suppressedMenuClick=null;},true);
      node.addEventListener('click',event=>{const press=suppressedMenuClick;if(event.isTrusted&&press&&performance.now()<press.until&&(event.pointerType==='touch'||event.sourceCapabilities?.firesTouchEvents)&&Math.abs(event.clientX-press.x)<20&&Math.abs(event.clientY-press.y)<20){event.preventDefault();event.stopImmediatePropagation();suppressedMenuClick=null;}},true);
      document.body.append(node);
      const position=()=>{if(!valid()){closeReplyContextMenu();return;}const v=window.visualViewport,left=v?.offsetLeft||0,top=v?.offsetTop||0,width=v?.width||innerWidth,height=v?.height||innerHeight;node.style.maxHeight=Math.max(1,height-16)+'px';const box=node.getBoundingClientRect();node.style.left=Math.max(left+8,Math.min(left+width-box.width-8,x-box.width/2))+'px';node.style.top=Math.max(top+8,Math.min(top+height-box.height-8,y-box.height/2))+'px';};
      const outside=event=>{if(!node.contains(event.target))closeReplyContextMenu();},keys=event=>{if(event.key==='Tab'){closeReplyContextMenu(true);return;}if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();closeReplyContextMenu(true);}else if(node.contains(document.activeElement)&&['ArrowDown','ArrowUp','Home','End'].includes(event.key)){const buttons=[...node.querySelectorAll('button:not(:disabled),summary')].filter(button=>button.getClientRects().length&&!button.hidden);if(!buttons.length)return;event.preventDefault();const at=buttons.indexOf(document.activeElement),next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(at+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;buttons[next]?.focus({preventScroll:true});buttons[next]?.scrollIntoView({block:'nearest'});}},closed=()=>closeReplyContextMenu();
      const sync=()=>{if(!node.isConnected)return;for(const mirror of mirrors)mirror();if(node.isConnected)position();};
      const observer=new MutationObserver(sync);for(const file of selected)observer.observe(file,{attributes:true,childList:true,subtree:true,characterData:true});for(const {controller} of controllers)observer.observe(controller,{attributes:true,childList:true,subtree:true,characterData:true});
      replyContextMenu={node,article,outside,keys,closed,position,observer,controllers};document.addEventListener('pointerdown',outside,true);document.addEventListener('keydown',keys,true);for(const type of messageMenuEvents)document.addEventListener(type,closed);window.addEventListener('resize',position);window.visualViewport?.addEventListener('resize',position);window.visualViewport?.addEventListener('scroll',position);sync();for(const preview of preflights)void preview();((reply.disabled?[...node.querySelectorAll('button:not(:disabled)')].find(button=>!button.hidden&&button.getClientRects().length):reply)||node).focus({preventScroll:true});return true;
    }
    const menuBlocked='button,a,input,select,textarea,audio,video,summary,.operator-audio-controls,[contenteditable=true],[data-quote-jump],[data-participant-menu],details[data-audio-transcript]';
    const menuGestureBlocked=(event,article)=>(!event.target.closest('[data-photo-preview]')&&!!event.target.closest(menuBlocked))||[...article.querySelectorAll('.operator-audio-controls')].some(node=>{const rect=node.getBoundingClientRect();return event.clientX>=rect.left&&event.clientX<=rect.right&&event.clientY>=rect.top&&event.clientY<=rect.bottom;});
    let suppressedMenuClick=null;
    const cancelLongPress=()=>{if(replyLongPress)clearTimeout(replyLongPress.timer);replyLongPress=null;};
    chatRowsHost.addEventListener('pointerdown',event=>{
      suppressedMenuClick=null;if(replyLongPress)cancelLongPress();if(event.pointerType!=='touch'||!event.isPrimary||!same())return;
      const article=event.target.closest('.operator-message');if(!article||!chatRowsHost.contains(article)||menuGestureBlocked(event,article)||window.getSelection?.()?.toString())return;
      if(event.clientX<20||event.clientX>window.innerWidth-20)return;
      const press={pointerId:event.pointerId,article,messageId:article.dataset.messageId,host:event.target.closest('[data-chat-attachment]'),startX:event.clientX,startY:event.clientY,opened:false,timer:null,target:event.target.closest('[data-photo-preview]')||event.target};replyLongPress=press;
      press.timer=setTimeout(()=>{if(replyLongPress!==press||window.getSelection?.()?.toString())return;press.opened=openReplyContextMenu(article,press.messageId,press.startX,press.startY,press.host);if(press.opened){replySwipe=null;suppressedMenuClick={article,target:press.target,x:press.startX,y:press.startY,until:performance.now()+1500};}},560);
    });
    chatRowsHost.addEventListener('pointermove',event=>{const press=replyLongPress;if(!press||event.pointerId!==press.pointerId||press.opened)return;if(Math.abs(event.clientX-press.startX)>12||Math.abs(event.clientY-press.startY)>12||window.getSelection?.()?.toString())cancelLongPress();});
    chatRowsHost.addEventListener('pointerup',event=>{const press=replyLongPress;if(!press||event.pointerId!==press.pointerId)return;if(press.opened)suppressedMenuClick={article:press.article,target:press.target,x:press.startX,y:press.startY,until:performance.now()+1500};cancelLongPress();});
    chatRowsHost.addEventListener('pointercancel',cancelLongPress);chatRowsHost.addEventListener('scroll',()=>{const held=replyLongPress?.opened;cancelLongPress();if(held)closeReplyContextMenu();});
    chatRowsHost.addEventListener('click',event=>{if(event.isTrusted&&suppressedMenuClick&&performance.now()<suppressedMenuClick.until&&(event.target===suppressedMenuClick.target||suppressedMenuClick.target?.contains(event.target))){event.preventDefault();event.stopImmediatePropagation();suppressedMenuClick=null;}},true);
    chatRowsHost.addEventListener('contextmenu',event=>{const article=event.target.closest('.operator-message');if(!article||!chatRowsHost.contains(article)||menuGestureBlocked(event,article))return;const rect=article.getBoundingClientRect();if(replyContextMenu?.article===article||openReplyContextMenu(article,article.dataset.messageId,event.clientX||rect.x+rect.width/2,event.clientY||rect.y+rect.height/2,event.target.closest('[data-chat-attachment]'))){event.preventDefault();event.stopPropagation();if(replyLongPress){replyLongPress.opened=true;clearTimeout(replyLongPress.timer);}}});
    chatRowsHost.addEventListener('keydown',event=>{if(event.key!=='ContextMenu'&&!(event.shiftKey&&event.key==='F10'))return;const article=event.target.closest('.operator-message');if(!article||event.target.closest(menuBlocked))return;const rect=article.getBoundingClientRect();if(openReplyContextMenu(article,article.dataset.messageId,rect.x+rect.width/2,rect.y+rect.height/2)){event.preventDefault();event.stopPropagation();}});
    function clearSwipeFeedback(article){if(article)delete article.dataset.swipeReplyFeedback;if(replySwipeFeedbackTimer){clearTimeout(replySwipeFeedbackTimer);replySwipeFeedbackTimer=null;}}
    chatRowsHost.addEventListener('pointerdown',event=>{if(route.wecall||event.pointerType!=='touch'||!event.isPrimary||!same()||!sendAllowed)return;const article=event.target.closest('.operator-message');if(!article||!chatRowsHost.contains(article)||(event.target.closest(swipeBlocked)||menuGestureBlocked(event,article))||window.getSelection?.()?.toString())return;if(event.clientX<24||event.clientX>window.innerWidth-24)return;replySwipe={pointerId:event.pointerId,article,messageId:article.dataset.messageId,startX:event.clientX,startY:event.clientY,startedAt:performance.now(),ready:false};});
    chatRowsHost.addEventListener('pointermove',event=>{const swipe=replySwipe;if(!swipe||event.pointerId!==swipe.pointerId)return;const dx=event.clientX-swipe.startX,dy=event.clientY-swipe.startY;if(window.getSelection?.()?.toString()||Math.abs(dy)>28||dx>0){clearSwipeFeedback(swipe.article);replySwipe=null;return;}if(dx<=-56){swipe.ready=true;swipe.article.dataset.swipeReplyFeedback='true';}});
    chatRowsHost.addEventListener('pointerup',event=>{const swipe=replySwipe;if(!swipe||event.pointerId!==swipe.pointerId)return;replySwipe=null;if(!swipe.ready||performance.now()-swipe.startedAt>650||window.getSelection?.()?.toString()){clearSwipeFeedback(swipe.article);return;}event.preventDefault();selectReplyMessage(swipe.messageId,{focus:false});clearTimeout(replySwipeFeedbackTimer);replySwipeFeedbackTimer=setTimeout(()=>clearSwipeFeedback(swipe.article),360);});
    chatRowsHost.addEventListener('pointercancel',event=>{if(!replySwipe||event.pointerId!==replySwipe.pointerId)return;clearSwipeFeedback(replySwipe.article);replySwipe=null;});
    chatRowsHost.addEventListener('click',async event=>{const button=event.target.closest('[data-copy-message]');if(!button)return;event.preventDefault();event.stopPropagation();const message=page.messages.find(row=>String(row.id||row.external_id)===button.dataset.copyMessage),text=messageCopyText(message),status=button.parentElement.querySelector('[data-copy-status]');if(!text||!same()||currentPage!==page||!chatRowsHost.contains(button))return;let copied=false;try{if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(text);copied=true;}else if(document.execCommand){const input=document.createElement('textarea');input.value=text;input.style.cssText='position:fixed;opacity:0;pointer-events:none';document.body.append(input);input.select();copied=document.execCommand('copy');input.remove();}}catch{}if(button.isConnected&&same()&&currentPage===page)status.textContent=replyText(copied?'copied':'copyFailed');});
    chatRowsHost.addEventListener('click',event=>{const jump=event.target.closest('[data-quote-jump]');if(jump){const target=[...chatRowsHost.querySelectorAll('[data-provider-message-id]')].find(node=>node.dataset.providerMessageId===jump.dataset.quoteJump);if(target){const behavior=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?'auto':'smooth';target.scrollIntoView({block:'center',behavior});}else $('operatorChatError').textContent=({ru:'Исходное сообщение не загружено.',az:'Orijinal mesaj yüklənməyib.',en:'Original message is not loaded.'}[i18n.language]||'Original message is not loaded.');return;}const button=event.target.closest('[data-reply-message]');if(button)selectReplyMessage(button.dataset.replyMessage,{focus:true});});
    function restorePendingReply(pending,dismiss,started){
      if(started)draftQuote=null;
      pendingReply=pending.quote_preview_dismissed?null:{data:pending,dismiss};
      drawReplyQuote();
    }
    function settlePendingReply(){pendingReply=null;drawReplyQuote();}
    if(interactionState?.quote&&!interactionState.quote.pending){
      const {quote_message_id,quote_channel_id,quote_conversation_id}=interactionState.quote;
      draftQuote={quote_message_id,quote_channel_id,quote_conversation_id};
      drawReplyQuote();
    }
    window.tenantChatSend?.clearDiagnostics?.();if(sendAllowed&&window.tenantChatSend)window.tenantChatSend.bind({api:async(url,options)=>{if(options.method==='GET'||url==='/api/platform/crm/chat/send-status/close-unsubmitted')return rawApi(url,options,{optionalScopedRead:true});try{return await rawApi(url,options);}catch(error){if(same())lifecycleUnavailable(error,route);throw error;}},identity:who,leadId:route.lead_id,me:member,provider:route.provider,channelId:route.channel_id,getQuote:()=>draftQuote,onPending:restorePendingReply,onDiscard:settlePendingReply,isQuoteCurrent:pending=>String(pending.quote_channel_id)===String(route.channel_id)&&String(pending.quote_conversation_id)===String(route.conversation_id),onAccepted:text=>{settlePendingReply();if(draftQuote)return true;if((sessionStorage.getItem(sendDraftKey)||'').trim()===text)sessionStorage.removeItem(sendDraftKey);},isCurrent:same,reload:async()=>{if(same()){sessionStorage.removeItem(contextKey('scroll',route));await openDialog(route);}}});else $('operatorSendReason').textContent=route.owner_overview_read_only?(({ru:'Только просмотр',az:'Yalnız baxış',en:'Read only'})[i18n.language]||'Read only'):route.send_block_reason==='dialog_accept_required'?'':t(route.send_block_reason||'sendUnavailable');
    const replyDiagnostics=$('operatorReplyDiagnostics');
    if(replyDiagnostics){replyDiagnostics.replaceChildren();window.tenantChatSend?.renderDiagnostics?.(replyDiagnostics);}
    renderHistoryMergeReadPicker();renderComposerSourcePicker();
    const chat=$('chatRows'),timelinePlaced=placeOperatorEvents(chat,page);
    updateLatestIncomingShortcut(chat,page);
    const draftKey=contextKey('draft',route),composer=$('chatText'),composerSurface=composer.closest('.operator-composer'),dialogBody=$('operatorDialogBody'),newMessages=$('operatorNewMessages');const scrollKey=contextKey('scroll',route),savedScroll=sessionStorage.getItem(scrollKey);setTimelineScroll(chat,savedScroll===null||page.scroll_to_latest?chat.scrollHeight:Number(savedScroll),timelinePlaced||suppressRead);chat.addEventListener('scroll',()=>{sessionStorage.setItem(scrollKey,String(chat.scrollTop));if(!timelineOnlyScroll(chat)&&atBottom(chat))void markDialogReadIfVisible(currentPage);});composer.value=interactionState?.draft??sessionStorage.getItem(draftKey)??'';const updateComposerHeight=()=>{const rect=composerSurface.getBoundingClientRect(),style=window.getComputedStyle(composerSurface),marginBottom=parseFloat(style.marginBottom)||0;dialogBody.style.setProperty('--operator-composer-height',`${Math.ceil(rect.height+marginBottom)}px`);};const autosize=()=>{composer.style.height='auto';const height=Math.max(40,Math.min(180,composer.scrollHeight||40));composer.style.height=height+'px';composer.style.overflowY=composer.scrollHeight>composer.clientHeight?'auto':'hidden';updateComposerHeight();};autosize();composer.addEventListener('input',()=>{sessionStorage.setItem(draftKey,composer.value);autosize();});newMessages.onclick=()=>{const behavior=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?'auto':'smooth';if(typeof chat.scrollTo==='function')chat.scrollTo({top:chat.scrollHeight,behavior});else chat.scrollTop=chat.scrollHeight;newMessages.hidden=true;if(behavior==='auto')void markDialogReadIfVisible(currentPage);};
    $('operatorEarlier').onclick=async()=>{
      const button=$('operatorEarlier'),before=page.next_before;if(button.disabled||!before)return;
      button.disabled=true;
      try{
        const query=timelineHistoryQuery(route),timelineRequested=query.get('operator_events')==='1'&&page.dialog?.lifecycle_enabled===true;
        const clientDeferred=timelineRequested&&page.operatorEventsRetryAt>Date.now();
        if(clientDeferred){
          query.delete('operator_events');
          if(!query.has('shared_reminder_events'))for(const key of ['expected_tenant_id','expected_user_id'])query.delete(key);
        }
        query.set('before',before);
        const earlier=await api('dialogs/history?'+query);
        if(!same()||currentPage!==page||!button.isConnected||!sameDialog(earlier.dialog,route))return;
        if(historyMergeChanged(page,earlier)){
          discardHistoryMergeWindow(route,{wipe:true});
          await openDialog(route,true,'',{suppressRead:true,revalidateAccess:true});
          return;
        }
        if(earlier.dialog.history_pair_id!==page.dialog.history_pair_id){
          await openDialog(route,true);
          return;
        }
        const height=chat.scrollHeight,top=chat.scrollTop,viewportTop=chat.getBoundingClientRect().top,
          anchor=[...chat.querySelectorAll('[data-message-id]')].find(node=>node.getBoundingClientRect().bottom>viewportTop+1),
          anchorTop=anchor?.getBoundingClientRect().top;
        if(timelineRequested&&(clientDeferred||operatorEventRetry(earlier.operator_events_retry_after_ms))){
          // Message paging is independent: tell the operator that this
          // historical activity window was skipped instead of hiding that fact.
          const boundary=(page.messages||[]).find(message=>String(message.id)===String(before))||earlier.messages?.at(-1);
          noteOperatorEventOmission(page,boundary?.happened_at||boundary?.created_at);
        }
        page.messages=earlier.messages.concat(page.messages);
        chat.insertAdjacentHTML('afterbegin',messageMarkup(earlier.messages,route));
        retainOperatorEvents(page,earlier,{older:true});const timelinePlaced=placeOperatorEvents(chat,page);
        page.next_before=earlier.next_before;button.hidden=!page.next_before;
        updateLatestIncomingShortcut(chat,page);
        if(anchor?.isConnected&&Number.isFinite(anchorTop)){
          const moved=anchor.getBoundingClientRect().top-anchorTop;
          if(moved||timelinePlaced)setTimelineScroll(chat,chat.scrollTop+moved,timelinePlaced);
        }else setTimelineScroll(chat,top+(chat.scrollHeight-height),timelinePlaced);
        document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));
      }catch(error){if(same())$('operatorChatError').textContent=error.message;}
      finally{if(button.isConnected)button.disabled=false;}
    };
    restoreCaseDraft(route,composer);
    if(route.provider==='connect'&&sendAllowed&&!route.archive_read_only&&!route.owner_overview_read_only&&
        !route.search_overview_read_only&&!route.case_attachment){
      // Suggestions use only the selected physical route, never a companion
      // message or owner-selected presentation label/number.
      window.tenantDialogQuickReplies?.attach(composer,route,()=>same()&&currentDialog?.can_send===true,updateComposerHeight);
    }
    copilotPanel.dataset.helperType=interactionState?.helperType||'Helper';
    if(interactionState){
      $('operatorCopilotPrompt').value=interactionState.copilotPrompt||'';
      copilotResult.textContent=interactionState.copilotResult||'';copilotResult.hidden=interactionState.copilotResultHidden??true;
      copilotStatus.textContent=interactionState.copilotStatus||'';
      copilotInsert.hidden=!interactionState.copilotInsertAvailable||!sendAllowed;copilotInsert.dataset.contextSignature=interactionState.copilotInsertContext||'';
      templateSection.open=interactionState.templateOpen===true;templateForm.hidden=interactionState.templateFormHidden??true;
      templateName.value=interactionState.templateName||'';templateQuestion.value=interactionState.templateQuestion||'';templateForm.dataset.editingId=interactionState.templateId||'';
      if(interactionState.copilotOpen){const mobile=!!window.matchMedia?.('(max-width: 900px)').matches;copilotPanel.hidden=false;copilotPanel.setAttribute('aria-modal',String(mobile));copilotBackdrop.hidden=!mobile;copilotBody.dataset.copilotOpen=String(mobile);copilotToggle.setAttribute('aria-expanded','true');}
    }
    host.querySelectorAll('[data-copilot-mode]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.copilotMode===copilotMode)));renderCopilotTemplates();
    host.querySelectorAll('[data-chat-attachment]').forEach(node=>{const old=retainedMedia.get([node.dataset.chatAttachment,node.dataset.attachmentLeadId,node.dataset.channelId,node.dataset.conversationId,node.dataset.mediaVersion,node.dataset.mediaKind].join('|'));if(old){for(const selector of ['.operator-media-analysis','[data-media-open]']){const previous=old.querySelector(selector),fresh=node.querySelector(selector);if(previous&&fresh){if(previous.matches('[data-photo-preview]')){previous.innerHTML=fresh.innerHTML;previous.setAttribute('aria-label',fresh.getAttribute('aria-label'));}else previous.textContent=fresh.textContent;}}const previousMenu=old.querySelector('[data-audio-transcription-menu]'),freshMenu=node.querySelector('[data-audio-transcription-menu]');if(previousMenu&&freshMenu){previousMenu.dataset.loaded='false';}node.replaceWith(old);}});

    displayedPage=page;displayedLanguage=i18n.language;displayedRouteScope=dialogRenderScope(page.dialog);document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));syncMobileViewport();if(interactionState){const body=$('dialogHelperContent');if(body)body.scrollTop=interactionState.copilotScroll||0;}if(interactionState?.focus==='composer'){composer.focus({preventScroll:true});if(interactionState.selection)try{composer.setSelectionRange(interactionState.selection.start,interactionState.selection.end,interactionState.selection.direction);}catch{}}else if(interactionState?.focus==='copilotPrompt'){const field=$('operatorCopilotPrompt');field?.focus({preventScroll:true});if(field&&interactionState.selection)try{field.setSelectionRange(interactionState.selection.start,interactionState.selection.end,interactionState.selection.direction);}catch{}}else if(interactionState?.focus&&copilotPanel.contains($(interactionState.focus))){const field=$(interactionState.focus);field.focus({preventScroll:true});if(interactionState.selection&&field.setSelectionRange)try{field.setSelectionRange(interactionState.selection.start,interactionState.selection.end,interactionState.selection.direction);}catch{}}else if(sendAllowed&&!window.matchMedia?.('(max-width: 900px)').matches)$('chatText').focus();
    if(page.anchor_message_id){const item=[...host.querySelectorAll('[data-message-id]')].find(node=>node.dataset.messageId===String(page.anchor_message_id));if(item)requestAnimationFrame(()=>{item.style.outline='2px solid var(--brand)';item.scrollIntoView({block:'center'});setTimeout(()=>{if(item.isConnected)item.style.outline='';},3000);});}
  }
  async function changeState(route,action){
    if(action==='resume'&&!window.confirm(t('resumeConfirm')))return;
    const result=await api('conversations/'+encodeURIComponent(route.conversation_id)+'/'+action,'POST',{provider:route.provider,channel_id:route.channel_id,reason:action==='pause'?'operator_manual_control':action==='handoff'?'operator_handoff':'operator_resume'});
    await refresh();document.dispatchEvent(new CustomEvent('tenant-dialog-state',{detail:{route,result}}));if(action==='handoff')document.dispatchEvent(new CustomEvent('tenant-support-changed'));return result;
  }
  function assignmentReason(reason){return ['no_eligible_agent','agent_already_assigned'].includes(reason)?t(reason):i18n.reason(reason);}
  function closeDialog(forget=true,fromHistory=false){if(forget===true&&!fromHistory&&window.matchMedia?.('(max-width: 900px)').matches&&(($('operatorCopilot')&&!$('operatorCopilot').hidden)||helperHistoryClosing)){if(!helperHistoryClosing)closeCopilotPanel();return;}if(currentDialog&&$('operatorConversationContent'))captureDialogInteractionState(currentDialog,$('operatorConversationContent'));if(forget===true&&!fromHistory&&history.state?.operatorDialog&&$('view-dialogs')?.classList.contains('active')){if(history.state.operatorDialogHistoryEntry!==false)history.back();else{const state={...history.state};for(const key of ['operatorDialog','operatorDialogOwner','operatorDialogHistoryEntry','operatorHelper'])delete state[key];history.replaceState(state,'');}}else if(history.state?.operatorHelper){const state={...history.state};delete state.operatorHelper;history.replaceState(state,'');}cancelCaseLink();pendingDialogRoute=null;copilotDeferredPage=null;displayedPage=null;if(forget===true){sessionStorage.removeItem(contextKey('route'));if(!fromHistory){viewReturnDialog=null;viewReturnDialogOwner='';}}closeDialogDiagnostics(false);closeGroupSettings();document.dispatchEvent(new CustomEvent('tenant-chat-close'));openRevision++;currentDialog=null;currentPage=null;const workspace=$('operatorWorkspace');if(workspace)workspace.dataset.selected='false';const host=$('operatorDialogs');if(host)host.dataset.conversationOpen='false';const header=$('operatorConversationHeader'),content=$('operatorConversationContent');if(header)header.innerHTML=`<div class="operator-conversation-empty">${t('chooseDialog')}</div>`;if(content)content.innerHTML=`<div class="operator-conversation-placeholder">${t('chooseDialog')}</div>`;if($('operatorRows'))renderRows();}
  let notificationRouteHandled=false;
  function notificationDialogRoute(){
    try{
      const query=new URLSearchParams(window.location.search);
      if(query.get('view')!=='dialogs'||query.get('open_dialog')!=='1'||query.get('provider')!=='connect')return null;
      const lead=query.get('lead_id')||'',channel=query.get('channel_id')||'',conversation=query.get('conversation_id')||'';
      if(!/^\d{1,20}$/.test(lead)||!/^[0-9a-f-]{36}$/i.test(channel)||!conversation||conversation.length>220||/[\u0000-\u001f\u007f]/.test(conversation))return null;
      const anchor=query.get('source_message_id')||'';if(anchor&&!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(anchor))return null;
      return {provider:'connect',lead_id:Number(lead),channel_id:channel,conversation_id:conversation,...(anchor?{source_message_id:anchor}:{})};
    }catch{return null;}
  }
  function clearNotificationDialogRoute(){
    try{const url=new URL(window.location.href);for(const key of ['open_dialog','provider','lead_id','channel_id','conversation_id','source_message_id'])url.searchParams.delete(key);window.history.replaceState(window.history.state,'',url.pathname+url.search+url.hash);}catch{}
  }
  function profileChanged(data){
    if($('operatorDialogs'))$('operatorDialogs').style.visibility='';
    const next=data.member||null,changed=identity(next)!==identity(member)||JSON.stringify(next?.permissions)!==JSON.stringify(member?.permissions)||next?.role!==member?.role||next?.active!==member?.active||next?.department_id!==member?.department_id||JSON.stringify(data.capabilities)!==JSON.stringify(capabilities)||JSON.stringify(data.role_permissions)!==permissionScope;
    permissionScope=JSON.stringify(data.role_permissions);
    member=next;capabilities=data.capabilities||{};loadSoundPreference();
    if(changed){mergeRequests.clear();closeDuplicateSearch(true);}syncDuplicateButton();
    if($('operatorBulkAssignment')){$('operatorBulkAssignment').hidden=member?.role!=='owner';$('operatorBulkAssignment').textContent=bulkText('preview');}
if(changed){companyPanelPreference=null;filterPreferencesLoaded=false;filterPreferencesFlight=null;filterSaveQueue=Promise.resolve();if($('operatorState'))$('operatorState').value='';if($('operatorFilterSaveStatus'))$('operatorFilterSaveStatus').textContent='';if($('operatorLifecycle'))$('operatorLifecycle').value='all';rememberOwnerOverviewMode();completeAllNotice('');viewReturnDialog=null;viewReturnDialogOwner='';pendingCopilotRequests.clear();lifecycleRequests.clear();lifecycleListStates.clear();dialogPageCache.clear();selectedDialog=null;selectedDialogOwner='';readMarkers.clear();readInFlight.clear();clearContactLookups();avatarAttempts.clear();avatarReady.clear();avatarQueue.length=0;listFlight=null;lastListKey='';lastListAt=0;stopAutoRefresh();latestMessageIds.clear();hasInitialDialogSnapshot=false;lastDialogSnapshotAt=0;epoch++;listRevision++;closeDialog(false);helperDrafts.clear();caseLinkKey='';rows=[];nextOffset=null;badge(0);if($('operatorRows'))$('operatorRows').innerHTML='';if($('operatorConversationContent'))$('operatorConversationContent').innerHTML=`<div class="operator-conversation-placeholder">${t('chooseDialog')}</div>`;if($('operatorChannel')){$('operatorChannel').value='';$('operatorAgent').value='';setOptions('operatorChannel',[],'allChannels');setOptions('operatorAgent',[],'allAgents');}}
    if(hasRight('dialogs')){if(!changed&&pendingDialogRoute)void openDialog(pendingDialogRoute);refresh(false,true);scheduleAutoRefresh();const fromNotification=!notificationRouteHandled?notificationDialogRoute():null;if(fromNotification){notificationRouteHandled=true;clearNotificationDialogRoute();const version=epoch;setTimeout(()=>{if(version===epoch&&!currentDialog&&hasRight('dialogs'))openDialog(fromNotification);},0);return;}if(openLocationCaseLink())return;const route=history.state?.view==='dialogs'&&history.state?.operatorDialogOwner===identity(member)&&new URLSearchParams(location.search).get('view')==='dialogs'?history.state.operatorDialog:null;if(route?.channel_id&&route?.conversation_id&&!currentDialog&&!pendingDialogRoute&&!caseLinkPending){const version=epoch,revision=openRevision;setTimeout(()=>{if(version===epoch&&revision===openRevision&&!currentDialog&&!pendingDialogRoute&&!caseLinkPending&&hasRight('dialogs')&&$('view-dialogs')?.classList.contains('active'))openDialog(route,true);},0);}}else stopAutoRefresh();
  }
  window.addEventListener('storage',event=>{if(event.key===soundPrefKey())loadSoundPreference();if(event.key===readSyncKey()&&!document.hidden&&$('view-dialogs')?.classList.contains('active'))void refresh();});
  document.addEventListener('tenant-auth-pending',()=>{mergeRequests.clear();stopRowContacts();closeDuplicateSearch(true);});
  document.addEventListener('tenant-profile',event=>{profileChanged(event.detail);restoreDialogCompany();});
  document.addEventListener('tenant-auth-invalid',()=>profileChanged({}));
  for(const event of ['tenant-chat-render','tenant-chat-close','tenant-auth-invalid','tenant-auth-pending','tenant-view'])document.addEventListener(event,()=>{document.querySelectorAll('audio[data-test-call-audio]').forEach(audio=>{audio.pause();audio.removeAttribute('src');audio.load();});});
  document.addEventListener('tenant-auth-pending',()=>{if(caseLinkPending){cancelCaseLink();caseLinkKey='';}listFlight=null;listRevision++;lastListAt=0;if($('operatorDialogs'))$('operatorDialogs').style.visibility='hidden';});
  document.addEventListener('tenant-view',event=>{if(event.detail.view==='dialogs'){restoreDialogCompany();refresh(false,true);scheduleAutoRefresh();}else{closeDuplicateSearch(true);closeDialogDiagnostics(false);stopRowContacts();if(currentDialog&&$('operatorWorkspace')?.dataset.selected==='true'&&selectedDialogOwner===identity(member)){viewReturnDialog={...currentDialog};viewReturnDialogOwner=identity(member);}if($('operatorCopilot')&&!$('operatorCopilot').hidden)closeCopilotPanel(true);scheduleAutoRefresh();cancelCaseLink();if(pendingDialogRoute)closeDialog(false,true);}});
  document.addEventListener('tenant-dialog-reauthorized',()=>{
    // A list issued before the fresh profile may still be in flight.
    listFlight=null;
    const pending=pendingDialogRoute;
    if(pending)void openDialog(pending,true,'',{revalidateAccess:true});
    void refresh(false,false,{revalidateSelected:!pending});
    scheduleAutoRefresh();
  });
  document.addEventListener('visibilitychange',()=>{
    if($('operatorRows'))hydrateRowContacts();
    if(document.hidden){if(window.Notification?.permission==='granted')scheduleAutoRefresh();else stopAutoRefresh();}
    else if(!window.tenantRequestCoordinator){refresh();scheduleAutoRefresh();if($('view-dialogs')?.classList.contains('active'))void markDialogReadIfVisible(currentPage);}
  });
  window.addEventListener('focus',()=>{updateBrowserNoticeButton();if(!document.hidden&&!window.tenantRequestCoordinator)void markDialogReadIfVisible(currentPage);});
  window.addEventListener('beforeunload',stopAutoRefresh);
  window.addEventListener('popstate',event=>{helperHistoryClosing=false;if(document.querySelector('.operator-photo-viewer'))return;if(!event.state?.operatorHelper&&$('operatorCopilot')&&!$('operatorCopilot').hidden)closeCopilotPanel(true);cancelCaseLink();const route=(event.state?.operatorDialogOwner===identity(member)?event.state.operatorDialog:null)||(event.state?.view==='dialogs'&&viewReturnDialogOwner===identity(member)?viewReturnDialog:null);if(event.state?.view==='dialogs'&&route&&hasRight('dialogs')){viewReturnDialog=null;viewReturnDialogOwner='';const workspace=$('operatorWorkspace');if(!sameDialog(currentDialog,route)||workspace?.dataset.selected!=='true')void openDialog(route,true);}else if(currentDialog||pendingDialogRoute)closeDialog(true,true);const query=new URLSearchParams(location.search),linkKey=[query.get('case_id')||'',query.get('message_id')||'',query.get('review_run_id')||'',query.get('case_version')||''].join(':');if(!event.state?.operatorCaseHandled||event.state.operatorCaseHandled!==linkKey)openLocationCaseLink();});
  document.addEventListener('tenant-language',()=>{syncDuplicateButton();closeDuplicateSearch(true);updateSoundButton();updateBrowserNoticeButton();if($('operatorRows')){setStates();setQueueModes();refresh();}if($('operatorBulkAssignment'))$('operatorBulkAssignment').textContent=bulkText('preview');if(currentDialog&&currentPage){const diagnosticsOpen=$('operatorDiagnosticsModal')&&!$('operatorDiagnosticsModal').hidden,settingsOpen=$('operatorGroupSettingsModal')&&!$('operatorGroupSettingsModal').hidden;renderDialog(currentPage);if(diagnosticsOpen)openDialogDiagnostics(currentDialog);if(settingsOpen)openGroupSettings(currentDialog);}});
  document.addEventListener('keydown',event=>{if(event.key==='Tab'&&$('operatorCopilot')&&!$('operatorCopilot').hidden&&window.matchMedia?.('(max-width: 900px)').matches){const nodes=[...$('operatorCopilot').querySelectorAll('button,input,select,textarea,summary,a[href]')].filter(node=>!node.disabled&&node.getClientRects().length);if(nodes.length){const first=nodes[0],last=nodes[nodes.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}}if(event.key!=='Escape')return;if($('operatorCopilot')&&!$('operatorCopilot').hidden){$('operatorCopilotClose')?.click();return;}if($('operatorGroupSettingsModal')&&!$('operatorGroupSettingsModal').hidden){closeGroupSettings();return;}if($('operatorDiagnosticsModal')&&!$('operatorDiagnosticsModal').hidden){closeDialogDiagnostics();return;}if(currentDialog||caseLinkPending)closeDialog();});
  let caseLinkKey='';
  const caseLinkText=key=>({opening:{ru:'Открываем исходный диалог кейса…',az:'Halın ilkin dialoqu açılır…',en:'Opening the case conversation…'},invalid:{ru:'Ссылка на кейс неполная или некорректна.',az:'Hal keçidi natamam və ya yanlışdır.',en:'The case link is incomplete or invalid.'},unavailable:{ru:'Исходное сообщение кейса недоступно или у вас нет доступа к нему.',az:'Halın ilkin mesajı əlçatan deyil və ya ona girişiniz yoxdur.',en:'The original case message is unavailable or you do not have access to it.'}}[key]?.[i18n.language]||'The case conversation is unavailable.');
  const uuid=value=>/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(String(value||''));
  function cancelCaseLink(){caseLinkRevision++;caseLinkPending=false;}
  async function resolveCaseLink(resolve,locationKey=''){
    closeDialog(false);
    window.tenantAppNavigate?.('dialogs',false);
    const revision=++caseLinkRevision,version=epoch;
    const current=()=>revision===caseLinkRevision&&version===epoch&&hasRight('dialogs');
    caseLinkPending=true;notice(caseLinkText('opening'));
    try{
      const route=await resolve();
      if(!current())return false;
      caseLinkPending=false;
      window.tenantAppNavigate?.('dialogs',false);
      await openDialog(route,false,locationKey,{userOpened:true});
      return true;
    }catch(error){
      if(!current())return false;
      caseLinkPending=false;
      notice(error?.message||caseLinkText('unavailable'));
      document.dispatchEvent(new CustomEvent('tenant-learning-deep-link-error',{detail:{message:error?.message||caseLinkText('unavailable')}}));
      return false;
    }finally{if(revision===caseLinkRevision)caseLinkPending=false;}
  }
  async function openCaseLink(data,locationKey=''){
    if(!hasRight('dialogs')){notice(t('noAccess'));return false;}
    const caseId=String(data?.case_id||''),messageId=String(data?.message_id||''),reviewId=data?.review_run_id?String(data.review_run_id):'',version=data?.expected_version;
    if(!uuid(caseId)||(messageId&&!uuid(messageId))||(reviewId&&!uuid(reviewId))){cancelCaseLink();notice(caseLinkText('invalid'));return false;}
    return resolveCaseLink(async()=>{
      const query=new URLSearchParams({expected_tenant_id:String(member?.tenant_id||''),expected_user_id:String(member?.telegram_id||'')});
      if(messageId)query.set('message_id',messageId);
      if(reviewId)query.set('review_run_id',reviewId);
      if(Number.isInteger(version)&&version>0)query.set('expected_version',String(version));
      const resolved=(await rawApi(endpoint+'knowledge-candidates/'+encodeURIComponent(caseId)+'/conversation?'+query)).result;
      if(!resolved?.route||String(resolved.case_id)!==caseId||!uuid(resolved.message_id)||(messageId&&String(resolved.message_id)!==messageId))throw Error(caseLinkText('unavailable'));
      return {...resolved.route,source_message_id:resolved.message_id,...(Number.isInteger(version)&&version>0?{case_attachment:{case_id:caseId,message_id:messageId,version}}:{})};
    },locationKey);
  }
  async function openLearningReviewLink(data,locationKey=''){
    if(!hasRight('dialogs')){notice(t('noAccess'));return false;}
    const reviewId=String(data?.review_run_id||''),messageId=String(data?.message_id||'');
    if(!uuid(reviewId)||!uuid(messageId)){cancelCaseLink();notice(caseLinkText('invalid'));return false;}
    return resolveCaseLink(async()=>{
      const resolved=(await rawApi(endpoint+'administrator/approvals?'+new URLSearchParams({review_run_id:reviewId}))).result;
      if(!resolved?.route||String(resolved.source_message_id)!==messageId)throw Error(caseLinkText('unavailable'));
      return {...resolved.route,source_message_id:resolved.source_message_id};
    },locationKey);
  }
  function consumeCaseLocation(key){
    const url=new URL(location.href),q=url.searchParams;
    if([q.get('case_id')||'',q.get('message_id')||'',q.get('review_run_id')||'',q.get('case_version')||''].join(':')!==key)return;
    for(const name of ['case_id','message_id','review_run_id','case_version'])q.delete(name);
    history.replaceState(history.state,'',url.pathname+url.search+url.hash);
    caseLinkKey='';
  }
  function openLocationCaseLink(){
    let query;try{query=new URLSearchParams(location.search);}catch{return;}
    const caseId=query.get('case_id'),messageId=query.get('message_id'),reviewId=query.get('review_run_id')||'',caseVersion=query.get('case_version');
    if(!caseId&&!messageId&&!reviewId){caseLinkKey='';return;}
    if(caseVersion&&(!/^[1-9]\d*$/.test(caseVersion)||!Number.isSafeInteger(Number(caseVersion))||!caseId||!messageId||reviewId)){notice(caseLinkText('invalid'));return true;}
    const key=[caseId||'',messageId||'',reviewId,caseVersion||''].join(':');
    if(key===caseLinkKey)return true;caseLinkKey=key;
    if(caseId)void openCaseLink({case_id:caseId,message_id:messageId,review_run_id:reviewId,...(caseVersion&&/^[1-9]\d*$/.test(caseVersion)?{expected_version:Number(caseVersion)}:{})},key);
    else void openLearningReviewLink({review_run_id:reviewId,message_id:messageId},key);
    history.replaceState({...history.state,operatorCaseHandled:key},'');
    return true;
  }
  function showReplyContext(){
    if(!hasRight('dialogs')||!currentDialog||!currentPage||!$('view-dialogs')?.classList.contains('active'))return false;
    const panel=$('operatorCopilot');if(!panel)return false;
    const mobile=!!window.matchMedia?.('(max-width: 900px)').matches;panel.hidden=false;
    if($('operatorCopilotBackdrop'))$('operatorCopilotBackdrop').hidden=!mobile;
    if($('operatorDialogBody'))$('operatorDialogBody').dataset.copilotOpen=String(mobile);
    $('operatorCopilotToggle')?.setAttribute('aria-expanded','true');syncMobileViewport();return true;
  }
  function updateCustomerVoiceDelivery(id,state){
    if(!['sent','delivered','read','failed','unknown'].includes(state)||!currentDialog||!currentPage)return;
    const message=(currentPage.messages||[]).find(row=>String(row.id)===String(id)&&row.customer_voice_status===true);
    if(!message)return;message.delivery_state=state;
    const article=[...document.querySelectorAll('#chatRows [data-message-id]')].find(node=>node.dataset.messageId===String(id));
    const footer=article?.querySelector('[data-message-footer-actions]');if(footer)footer.innerHTML=deliveryMarkup(message)+copyControlMarkup(message);
  }
  window.tenantOperator={messageMarkup,attachmentLabel,bindHistoryMessages,openDialog,openManualCaseCreate,showReplyContext,openCaseLink,openLearningReviewLink,openLocationCaseLink,closeDialog,changeState,refresh,hasRight,updateCustomerVoiceDelivery,get resolvingCaseLink(){return caseLinkPending},get dialogRevision(){return openRevision},get member(){return member},get currentDialog(){return currentDialog?{...currentDialog}:null}};
  if(window.tenantProfileSnapshot)profileChanged(window.tenantProfileSnapshot);else fetch(window.TENANT_AI_AGENTS_CONFIG?.profileUrl||'/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():null).then(data=>{if(!member&&data?.member)profileChanged(data);}).catch(()=>{});
})();


