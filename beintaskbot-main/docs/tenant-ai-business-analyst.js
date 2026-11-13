/* Owner-only Business Analyst text, voice, and presentation settings. */
(()=>{'use strict';
  const host=document.getElementById('aiBusinessAnalystHost');if(!host)return;
  const defaults={
    assistant_name:'Аян',
    greeting_ru:'Здравствуйте, я Аян, ваш бизнес-аналитик. Чем помочь?',
    greeting_az:'Salam, mən Ayanam, şəxsi biznes analitikinizəm. Sizə necə kömək edə bilərəm?',
    response_language:'az',
    communication_mode:'balanced',
    communication_style:'Сначала отвечай по сути. Не повторяй вопрос; уточняй только то, без чего нельзя ответить.',
    language_style:'Для азербайджанского используй литературный язык в латинской графике и естественные бакинские формулировки; избегай турецких слов и выражений.'
  };
  const copy={
    ru:{
      title:'AI Business Analyst',intro:'Один вопрос — один анализ по разрешённым источникам. Режим только чтения: ассистент не меняет CRM, задачи или настройки. Полнота и актуальность данных могут быть ограничены.',placeholder:'Например: Какие подтверждённые риски сейчас требуют внимания?',ask:'Спросить',working:'Проверяю доступные источники и готовлю ответ…',ready:'Ответ готов. Источники и ограничения указаны ниже.',unknown:'Запрос мог дойти до AI, поэтому автоматического повтора не будет. Проверьте результат перед новым запросом.',owner:'Инструмент доступен только владельцу компании.',empty:'Введите вопрос.',sources:'Источники',noSources:'В этом ответе нет ссылок на записи источников.',open:'Открыть раздел приложения',footer:'Голосовой анализ использует отдельную сессию без истории диалога. Результаты и аудио не сохраняются.',voiceButton:'Начать голосовой анализ',voiceConnecting:'Подключаю голосовой анализ…',voiceActive:'Голосовой разговор подключён. Говорите, AI ответит после паузы.',voiceEnded:'Голосовой разговор завершён.',voiceFailed:'Не удалось подключить голосовой анализ. Проверьте доступ и микрофон.',settingsLabel:'Настройки ассистента',settingsTitle:'Настройки ассистента',name:'Имя ассистента',greetingRu:'Как представляться по-русски',greetingAz:'Как представляться по-азербайджански',responseLanguage:'Язык ответов',languageAz:'Азербайджанский — всегда',languageRu:'Русский — всегда',languageAuto:'По языку вопроса',style:'Манера общения',modeBalanced:'Сбалансированная',modeFriendly:'Доброжелательная',modeConversational:'Общительная',modeCreative:'Креативная',modeConcise:'Краткая',modeFormal:'Деловая',styleNotes:'Дополнительные пожелания',languageNotes:'Произношение и словоупотребление',protected:'Язык ответа и манера общения применяются в тексте и голосе. Доступ остаётся только у владельца; анализ — только для чтения, без отправки сообщений и изменений CRM.',loadingSettings:'Загружаю настройки…',savingSettings:'Сохраняю…',settingsSaved:'Настройки сохранены.',settingsFailed:'Не удалось сохранить настройки.',settingsLoadFailed:'Не удалось загрузить настройки.',settingsSave:'Сохранить',settingsCancel:'Закрыть',settingsEmpty:'Заполните все поля.'
    },
    az:{
      title:'AI Business Analyst',intro:'Bir sual — icazəli mənbələr üzrə bir təhlil. Yalnız oxuma rejimidir: köməkçi CRM-i, tapşırıqları və ya ayarları dəyişmir. Məlumatların tamlığı və yeniliyi məhdud ola bilər.',placeholder:'Məsələn: Hazırda hansı təsdiqlənmiş risklərə diqqət yetirmək lazımdır?',ask:'Soruş',working:'Əlçatan mənbələr yoxlanılır və cavab hazırlanır…',ready:'Cavab hazırdır. Mənbələr və məhdudiyyətlər aşağıdadır.',unknown:'Sorğu AI-yə çatmış ola bilər, ona görə avtomatik təkrar göndərilməyəcək. Yeni sorğudan əvvəl nəticəni yoxlayın.',owner:'Bu alət yalnız şirkət sahibinə açıqdır.',empty:'Sualı daxil edin.',sources:'Mənbələr',noSources:'Bu cavabda mənbə qeydlərinə istinad yoxdur.',open:'Tətbiq bölməsini aç',footer:'Səsli təhlil dialoq tarixçəsindən ayrı sessiyadan istifadə edir. Nəticələr və audio saxlanılmır.',voiceButton:'Səsli təhlilə başla',voiceConnecting:'Səsli təhlilə qoşulur…',voiceActive:'Səsli söhbət qoşuldu. Danışın, AI fasilədən sonra cavab verəcək.',voiceEnded:'Səsli söhbət bitdi.',voiceFailed:'Səsli təhlilə qoşulmaq olmadı. Girişi və mikrofonu yoxlayın.',settingsLabel:'Assistentin ayarları',settingsTitle:'Assistentin ayarları',name:'Assistentin adı',greetingRu:'Rus dilində təqdimat',greetingAz:'Azərbaycan dilində təqdimat',responseLanguage:'Cavab dili',languageAz:'Azərbaycan dili — həmişə',languageRu:'Rus dili — həmişə',languageAuto:'Sualın dilinə uyğun',style:'Ünsiyyət tərzi',modeBalanced:'Balanslı',modeFriendly:'Mehriban',modeConversational:'Ünsiyyətcil',modeCreative:'Yaradıcı',modeConcise:'Qısa',modeFormal:'Rəsmi',styleNotes:'Əlavə ünsiyyət istəkləri',languageNotes:'Tələffüz və söz seçimi',protected:'Cavab dili və ünsiyyət tərzi həm mətndə, həm də səsdə tətbiq olunur. Giriş yalnız sahib üçündür; təhlil yalnız oxunur, mesaj göndərilmir və CRM dəyişdirilmir.',loadingSettings:'Ayarlar yüklənir…',savingSettings:'Saxlanılır…',settingsSaved:'Ayarlar saxlanıldı.',settingsFailed:'Ayarları saxlamaq olmadı.',settingsLoadFailed:'Ayarları yükləmək olmadı.',settingsSave:'Saxla',settingsCancel:'Bağla',settingsEmpty:'Bütün sahələri doldurun.'
    },
    en:{
      title:'AI Business Analyst',intro:'One question per read-only analysis using permitted sources. The assistant cannot change CRM, tasks, or settings. Source coverage and freshness may be limited.',placeholder:'For example: Which confirmed risks need attention now?',ask:'Ask',working:'Checking available sources and preparing an answer…',ready:'Answer ready. Sources and limitations are listed below.',unknown:'The request may have reached AI, so it will not be retried automatically. Check before starting a new request.',owner:'This tool is available only to the company owner.',empty:'Enter a question.',sources:'Sources',noSources:'This answer has no record references.',open:'Open app section',footer:'Voice analysis uses a separate session without dialog history. Audio and results are not saved.',voiceButton:'Start voice analysis',voiceConnecting:'Connecting voice analysis…',voiceActive:'Voice connected. Speak naturally; AI will answer after a pause.',voiceEnded:'Voice conversation ended.',voiceFailed:'Could not connect voice analysis. Check access and microphone.',settingsLabel:'Assistant settings',settingsTitle:'Assistant settings',name:'Assistant name',greetingRu:'Russian introduction',greetingAz:'Azerbaijani introduction',responseLanguage:'Response language',languageAz:'Azerbaijani — always',languageRu:'Russian — always',languageAuto:'Match question language',style:'Communication style',modeBalanced:'Balanced',modeFriendly:'Friendly',modeConversational:'Conversational',modeCreative:'Creative',modeConcise:'Concise',modeFormal:'Formal',styleNotes:'Additional communication notes',languageNotes:'Pronunciation and wording',protected:'Response language and communication style apply to text and voice. Access remains owner-only; analysis is read-only, with no messages or CRM changes.',loadingSettings:'Loading settings…',savingSettings:'Saving…',settingsSaved:'Settings saved.',settingsFailed:'Could not save settings.',settingsLoadFailed:'Could not load settings.',settingsSave:'Save',settingsCancel:'Close',settingsEmpty:'Fill in every field.'
    }
  };
  let member=null,epoch=0,busy=false,controller=null,lastLang='';
  let voiceSection=null,voiceStarting=false,voiceState='idle',voiceWasStarted=false,settingsController=null;
  const lang=()=>copy[window.tenantI18n?.language]||copy.az;
  const node=(tag,text,className)=>{const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(className)item.className=className;return item;};
  const expected=()=>({tenant_id:String(member?.tenant_id||''),user_id:String(member?.telegram_id||'')});
  function clear(){epoch++;controller?.abort();settingsController?.abort();controller=null;settingsController=null;busy=false;
    if(voiceSection){void voiceSection.disposeVoice?.();voiceSection=null;}voiceStarting=false;voiceState='idle';voiceWasStarted=false;host.replaceChildren();}
  function field(form,inputs,name,labelText,multiline,maxLength){
    const label=node('label',undefined,'analyst-settings-field');label.append(node('span',labelText));
    const control=node(multiline?'textarea':'input');if(!multiline)control.type='text';
    control.name=name;control.dataset.settingsField=name;control.required=true;control.maxLength=maxLength;
    control.value=defaults[name];label.append(control);form.append(label);inputs[name]=control;
  }
  function selectField(form,inputs,name,labelText,options){
    const label=node('label',undefined,'analyst-settings-field');label.append(node('span',labelText));
    const control=node('select');control.name=name;control.dataset.settingsField=name;control.required=true;
    for(const [value,text] of options){const option=node('option',text);option.value=value;control.append(option);}
    control.value=defaults[name];label.append(control);form.append(label);inputs[name]=control;
  }
  function render(){
    host.replaceChildren();const w=lang();if(member?.role!=='owner')return;
    const card=node('article',undefined,'panel ai-business-analyst');
    const heading=node('div',undefined,'analyst-card-heading');heading.append(node('h2',w.title));
    const settingsButton=node('button','⚙','analyst-settings-button');settingsButton.type='button';
    settingsButton.title=w.settingsLabel;settingsButton.setAttribute('aria-label',w.settingsLabel);settingsButton.setAttribute('aria-haspopup','dialog');
    settingsButton.disabled=voiceStarting||voiceState==='active';heading.append(settingsButton);card.append(heading,node('p',w.intro));

    const form=node('form');const input=node('textarea');input.maxLength=1600;input.required=true;input.setAttribute('aria-label',w.placeholder);input.placeholder=w.placeholder;
    const actions=node('div',undefined,'analyst-actions');const ask=node('button',w.ask,'primary');ask.type='submit';const status=node('p','', 'sub');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const answer=node('div','', 'analyst-answer');answer.hidden=true;const sources=node('div',undefined,'analyst-sources');sources.hidden=true;
    const sourceHeading=node('strong',w.sources);sourceHeading.hidden=true;const footer=node('p',w.footer,'sub');footer.style.marginTop='12px';
    actions.append(ask);form.append(input,actions);card.append(form,status,answer,sourceHeading,sources);

    const voiceActions=node('div',undefined,'analyst-voice-actions');
    const voiceButton=node('button',w.voiceButton,'primary analyst-voice-button');voiceButton.type='button';voiceButton.disabled=voiceStarting||voiceState==='active';
    const voiceStatus=node('p',voiceStarting?w.voiceConnecting:voiceState==='active'?w.voiceActive:voiceState==='ended'?w.voiceEnded:'','sub analyst-voice-status');
    voiceStatus.setAttribute('role','status');voiceStatus.setAttribute('aria-live','polite');voiceActions.append(voiceButton,voiceStatus);card.append(voiceActions);

    const dialog=node('dialog',undefined,'analyst-settings-dialog');dialog.setAttribute('aria-labelledby','analystSettingsTitle');
    const settingsForm=node('form',undefined,'analyst-settings-form');const dialogTitle=node('h3',w.settingsTitle);dialogTitle.id='analystSettingsTitle';settingsForm.append(dialogTitle);
    const inputs={};field(settingsForm,inputs,'assistant_name',w.name,false,40);field(settingsForm,inputs,'greeting_ru',w.greetingRu,true,240);
    field(settingsForm,inputs,'greeting_az',w.greetingAz,true,240);
    selectField(settingsForm,inputs,'response_language',w.responseLanguage,[['az',w.languageAz],['ru',w.languageRu],['auto',w.languageAuto]]);
    selectField(settingsForm,inputs,'communication_mode',w.style,[['balanced',w.modeBalanced],['friendly',w.modeFriendly],['conversational',w.modeConversational],['creative',w.modeCreative],['concise',w.modeConcise],['formal',w.modeFormal]]);
    field(settingsForm,inputs,'communication_style',w.styleNotes,true,500);
    field(settingsForm,inputs,'language_style',w.languageNotes,true,500);
    const protectedNote=node('p',w.protected,'sub analyst-settings-protected');protectedNote.setAttribute('role','note');settingsForm.append(protectedNote);
    const settingsStatus=node('p','', 'sub analyst-settings-status');settingsStatus.setAttribute('role','status');settingsStatus.setAttribute('aria-live','polite');settingsForm.append(settingsStatus);
    const modalActions=node('div',undefined,'analyst-settings-actions');const save=node('button',w.settingsSave,'primary');save.type='submit';
    const cancel=node('button',w.settingsCancel,'analyst-settings-cancel');cancel.type='button';modalActions.append(cancel,save);settingsForm.append(modalActions);dialog.append(settingsForm);card.append(dialog);

    function currentOwner(ids,requestEpoch){return requestEpoch===epoch&&member?.role==='owner'&&String(member.tenant_id||'')===ids.tenant_id&&String(member.telegram_id||'')===ids.user_id;}
    function setSettings(value){for(const name of Object.keys(defaults))inputs[name].value=typeof value?.[name]==='string'?value[name]:defaults[name];}
    function readSettings(){const result={};for(const name of Object.keys(defaults))result[name]=inputs[name].value.trim();return result;}
    async function loadSettings(){
      const ids=expected();if(!ids.tenant_id||!ids.user_id)return;const requestEpoch=epoch;settingsController=new AbortController();
      setSettings(defaults);settingsStatus.textContent=w.loadingSettings;save.disabled=true;
      try{
        const query=new URLSearchParams({expected_tenant_id:ids.tenant_id,expected_user_id:ids.user_id});
        const response=await fetch('/api/platform/ai/analytics/settings?'+query.toString(),{credentials:'same-origin',cache:'no-store',signal:settingsController.signal});
        const data=await response.json().catch(()=>({success:false}));
        if(!currentOwner(ids,requestEpoch))return;
        if(!response.ok||data.success!==true||!data.result?.settings)throw Error(data.error||w.settingsLoadFailed);
        setSettings(data.result.settings);settingsStatus.textContent='';
      }catch(error){if(currentOwner(ids,requestEpoch))settingsStatus.textContent=error.name==='AbortError'?'':w.settingsLoadFailed;}
      finally{if(currentOwner(ids,requestEpoch)){settingsController=null;save.disabled=false;}}
    }
    settingsButton.addEventListener('click',()=>{
      if(voiceStarting||voiceState==='active'||member?.role!=='owner')return;
      if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','');
      void loadSettings();
    });
    cancel.addEventListener('click',()=>{if(typeof dialog.close==='function')dialog.close();else dialog.removeAttribute('open');});
    settingsForm.addEventListener('submit',async event=>{
      event.preventDefault();if(member?.role!=='owner'||save.disabled)return;
      const ids=expected(),settings=readSettings();if(Object.values(settings).some(value=>!value)){settingsStatus.textContent=w.settingsEmpty;return;}
      const requestEpoch=epoch;save.disabled=true;cancel.disabled=true;settingsStatus.textContent=w.savingSettings;
      try{
        const response=await fetch('/api/platform/ai/analytics/settings',{method:'PATCH',credentials:'same-origin',cache:'no-store',
          headers:{'Content-Type':'application/json','Accept':'application/json'},
          body:JSON.stringify({expected_tenant_id:ids.tenant_id,expected_user_id:ids.user_id,settings})});
        const data=await response.json().catch(()=>({success:false}));
        if(!currentOwner(ids,requestEpoch))return;
        if(!response.ok||data.success!==true||!data.result?.settings)throw Error(data.error||w.settingsFailed);
        setSettings(data.result.settings);settingsStatus.textContent=w.settingsSaved;
        if(typeof dialog.close==='function')dialog.close();else dialog.removeAttribute('open');
      }catch(_error){if(currentOwner(ids,requestEpoch))settingsStatus.textContent=w.settingsFailed;}
      finally{if(currentOwner(ids,requestEpoch)){save.disabled=false;cancel.disabled=false;}}
    });

    voiceButton.addEventListener('click',async()=>{
      if(voiceStarting||member?.role!=='owner'||!host.isConnected)return;
      const ids=expected();if(!ids.tenant_id||!ids.user_id)return;
      const isCurrent=()=>member?.role==='owner'&&String(member.tenant_id||'')===ids.tenant_id&&String(member.telegram_id||'')===ids.user_id&&host.isConnected;
      voiceStarting=true;voiceState='connecting';voiceButton.disabled=true;settingsButton.disabled=true;voiceStatus.textContent=w.voiceConnecting;
      try{
        if(voiceSection){await voiceSection.disposeVoice?.();voiceSection=null;}
        const language=window.tenantI18n?.language||'ru';const query=new URLSearchParams({expected_tenant_id:ids.tenant_id,expected_user_id:ids.user_id});
        voiceSection=window.mountTenantCopilotVoice?.(document.body,{
          language,isCurrent,socketPath:'/api/platform/ai/analytics/voice/ws?'+query.toString(),
          preflight:async()=>{
            const response=await fetch('/api/platform/ai/analytics/voice/preflight',{method:'POST',credentials:'same-origin',cache:'no-store',
              headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({expected_tenant_id:ids.tenant_id,expected_user_id:ids.user_id})});
            const data=await response.json().catch(()=>({success:false}));
            if(!response.ok||data.success!==true||data.result?.available!==true)throw Error('voice_unavailable');return data.result;
          },
          onState:state=>{
            voiceState=state==='listening'||state==='responding'||state==='muted'?'active':state==='idle'&&voiceWasStarted?'ended':state;
            const currentCopy=lang(),currentStatus=host.querySelector('.analyst-voice-status');
            const currentVoiceButton=host.querySelector('.analyst-voice-button'),currentSettingsButton=host.querySelector('.analyst-settings-button');
            if(currentStatus)currentStatus.textContent=voiceState==='active'?currentCopy.voiceActive:voiceState==='ended'?currentCopy.voiceEnded:state==='connecting'?currentCopy.voiceConnecting:'';
            if(currentVoiceButton)currentVoiceButton.disabled=voiceStarting||voiceState==='active';
            if(currentSettingsButton)currentSettingsButton.disabled=voiceStarting||voiceState==='active';
          },
          onIdle:outcome=>{voiceWasStarted=true;voiceState=outcome?.confirmed===true?'ended':'idle';
            const currentCopy=lang(),currentStatus=host.querySelector('.analyst-voice-status');
            const currentVoiceButton=host.querySelector('.analyst-voice-button'),currentSettingsButton=host.querySelector('.analyst-settings-button');
            if(currentStatus)currentStatus.textContent=outcome?.confirmed===true?currentCopy.voiceEnded:(outcome?.reason||currentCopy.voiceFailed);
            if(currentVoiceButton)currentVoiceButton.disabled=false;if(currentSettingsButton)currentSettingsButton.disabled=false;}
        });
        if(!voiceSection||await voiceSection.startVoice?.()!==true)throw Error('voice_unavailable');voiceWasStarted=true;
      }catch(_error){voiceState='idle';const currentStatus=host.querySelector('.analyst-voice-status');if(currentStatus)currentStatus.textContent=lang().voiceFailed;if(voiceSection){void voiceSection.disposeVoice?.();voiceSection=null;}}
      finally{voiceStarting=false;const currentVoiceButton=host.querySelector('.analyst-voice-button'),currentSettingsButton=host.querySelector('.analyst-settings-button');
        if(currentVoiceButton)currentVoiceButton.disabled=voiceState==='active';if(currentSettingsButton)currentSettingsButton.disabled=voiceState==='active';}
    });

    form.addEventListener('submit',async event=>{
      event.preventDefault();if(busy||member?.role!=='owner')return;
      const prompt=input.value.trim();if(!prompt){status.textContent=w.empty;input.focus();return;}
      const ids=expected();if(!ids.tenant_id||!ids.user_id)return;
      let requestId;try{requestId=crypto.randomUUID();}catch{status.textContent=w.unknown;return;}
      const requestEpoch=epoch;busy=true;ask.disabled=true;input.disabled=true;answer.hidden=true;answer.textContent='';sources.replaceChildren();sources.hidden=true;sourceHeading.hidden=true;status.textContent=w.working;controller=new AbortController();
      try{
        const response=await fetch('/api/platform/ai/analytics/ask',{method:'POST',credentials:'same-origin',cache:'no-store',signal:controller.signal,
          headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({expected_tenant_id:ids.tenant_id,expected_user_id:ids.user_id,request_id:requestId,prompt})});
        const data=await response.json().catch(()=>({success:false}));
        if(!currentOwner(ids,requestEpoch))return;
        if(!response.ok||data.success!==true||!data.result||typeof data.result.text!=='string')throw Error(response.status===409?w.unknown:(data.error||w.unknown));
        answer.textContent=data.result.text;answer.hidden=false;
        const refs=Array.isArray(data.result.source_refs)?data.result.source_refs.slice(0,30):[];
        if(refs.length){sourceHeading.hidden=false;sources.hidden=false;for(const ref of refs){
          const label=[ref.kind,ref.provider,ref.source,ref.message_id||ref.candidate_id||ref.task_id||ref.chunk_no].filter(value=>value!==undefined&&value!==null&&String(value).length).join(' · ')||w.sources;
          const row=node('div');if(typeof ref.inbox_url==='string'&&ref.inbox_url.startsWith('/app')){const link=node('a',w.open);link.href=ref.inbox_url;link.rel='noopener';link.target='_blank';row.append(node('span',label+' · '),link);}else row.textContent=label;sources.append(row);
        }}else{sourceHeading.hidden=false;sources.hidden=false;sources.append(node('span',w.noSources));}
        const usage=data.result.usage||{},tokens=(Number(usage.input_tokens)||0)+(Number(usage.output_tokens)||0);
        status.textContent=w.ready+(data.result.model?' · '+data.result.model:'')+(tokens?' · '+tokens+' tokens':'');
      }catch(error){if(currentOwner(ids,requestEpoch))status.textContent=error.name==='AbortError'?w.unknown:(error.message||w.unknown);}
      finally{if(requestEpoch===epoch){busy=false;controller=null;ask.disabled=false;input.disabled=false;}}
    });
    card.append(footer);host.append(card);
  }
  function receive(data){const next=data?.member||null;const changed=String(next?.tenant_id||'')!==String(member?.tenant_id||'')||String(next?.telegram_id||'')!==String(member?.telegram_id||'')||next?.role!==member?.role;member=next;if(changed)clear();render();}
  document.addEventListener('tenant-profile',event=>receive(event.detail));
  document.addEventListener('tenant-auth-invalid',()=>receive(null));document.addEventListener('tenant-auth-pending',()=>receive(null));
  document.addEventListener('tenant-language',()=>{if(lastLang!==window.tenantI18n?.language){lastLang=window.tenantI18n?.language;render();}});
  if(window.tenantProfileSnapshot)receive(window.tenantProfileSnapshot);else fetch('/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(response=>response.json()).then(receive).catch(()=>receive(null));
})();
