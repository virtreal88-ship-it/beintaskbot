/* Owner-only text analyst and direct read-only task search. */
(()=>{'use strict';
  const host=document.getElementById('aiBusinessAnalystHost');if(!host)return;
  const copy={
    ru:{
      title:'AI Business Analyst',intro:'Один вопрос — один анализ по разрешённым источникам. Режим только чтения: ассистент не меняет CRM, задачи или настройки. Полнота и актуальность данных могут быть ограничены.',placeholder:'Например: Какие подтверждённые риски сейчас требуют внимания?',ask:'Спросить',working:'Проверяю доступные источники и готовлю ответ…',ready:'Ответ готов. Источники и ограничения указаны ниже.',unknown:'Запрос мог дойти до AI, поэтому автоматического повтора не будет. Проверьте результат перед новым запросом.',owner:'Инструмент доступен только владельцу компании.',empty:'Введите вопрос.',sources:'Источники',noSources:'В этом ответе нет ссылок на записи источников.',open:'Открыть раздел приложения',footer:'Голосовой анализ использует отдельную сессию без истории диалога. Результаты и аудио не сохраняются.',voiceButton:'Начать голосовой анализ',voiceConnecting:'Подключаю голосовой анализ…',voiceActive:'Голосовой разговор подключён. Говорите, AI ответит после паузы.',voiceEnded:'Голосовой разговор завершён.',voiceFailed:'Не удалось подключить голосовой анализ. Проверьте доступ и микрофон.',
      taskSearchTitle:'Прямой поиск задач',taskSearchIntro:'Ищет задачи в подключённых источниках без вызова AI. Поиск только для чтения; задачи и статусы не меняются.',taskSearchLabel:'Название, ID или текст задачи',taskSearchPlaceholder:'Например: печать чека или BS-1364',taskSourceLabel:'Источник',taskSourceAll:'Все источники',taskSourceNative:'Beintaskbot',taskSourceKommo:'Kommo',taskSourceLinear:'Linear',taskSearchButton:'Найти задачи',taskSearchWorking:'Ищу задачи в выбранных источниках…',taskSearchComplete:'Поиск завершён',taskSearchEmpty:'Введите не менее 2 символов.',taskSearchNoResults:'В доступных источниках задачи по этому запросу не найдены.',taskSearchNoSources:'Ни один выбранный источник сейчас недоступен.',taskSearchFailed:'Не удалось выполнить поиск задач.',taskSearchPartial:'Результат может быть неполным: источник недоступен или есть дополнительные страницы.',taskSourceAvailable:'доступен',taskSourceUnavailable:'недоступен',taskSearchMore:'Есть дополнительные результаты.',taskSearchStatus:'Статус',taskSearchAssignee:'Ответственный',taskSearchDue:'Срок',taskSearchUpdated:'Обновлено',taskSearchOpen:'Открыть задачу',taskSearchOpenApp:'Открыть список задач',taskSearchReadOnly:'Результаты отражают текущий снимок источника. Статус задачи сам по себе не подтверждает решение клиентской проблемы.'
    },
    az:{
      title:'AI Business Analyst',intro:'Bir sual — icazəli mənbələr üzrə bir təhlil. Yalnız oxuma rejimidir: köməkçi CRM-i, tapşırıqları və ya ayarları dəyişmir. Məlumatların tamlığı və yeniliyi məhdud ola bilər.',placeholder:'Məsələn: Hazırda hansı təsdiqlənmiş risklərə diqqət yetirmək lazımdır?',ask:'Soruş',working:'Əlçatan mənbələr yoxlanılır və cavab hazırlanır…',ready:'Cavab hazırdır. Mənbələr və məhdudiyyətlər aşağıdadır.',unknown:'Sorğu AI-yə çatmış ola bilər, ona görə avtomatik təkrar göndərilməyəcək. Yeni sorğudan əvvəl nəticəni yoxlayın.',owner:'Bu alət yalnız şirkət sahibinə açıqdır.',empty:'Sualı daxil edin.',sources:'Mənbələr',noSources:'Bu cavabda mənbə qeydlərinə istinad yoxdur.',open:'Tətbiq bölməsini aç',footer:'Səsli təhlil dialoq tarixçəsindən ayrı sessiyadan istifadə edir. Nəticələr və audio saxlanılmır.',voiceButton:'Səsli təhlilə başla',voiceConnecting:'Səsli təhlilə qoşulur…',voiceActive:'Səsli söhbət qoşuldu. Danışın, AI fasilədən sonra cavab verəcək.',voiceEnded:'Səsli söhbət bitdi.',voiceFailed:'Səsli təhlilə qoşulmaq olmadı. Girişi və mikrofonu yoxlayın.',
      taskSearchTitle:'Tapşırıqlarda birbaşa axtarış',taskSearchIntro:'AI çağırışı olmadan qoşulmuş mənbələrdə tapşırıqları axtarır. Yalnız oxuma rejimidir; tapşırıq və statuslar dəyişdirilmir.',taskSearchLabel:'Tapşırığın adı, ID-si və ya mətni',taskSearchPlaceholder:'Məsələn: çekin çapı və ya BS-1364',taskSourceLabel:'Mənbə',taskSourceAll:'Bütün mənbələr',taskSourceNative:'Beintaskbot',taskSourceKommo:'Kommo',taskSourceLinear:'Linear',taskSearchButton:'Tapşırıqları axtar',taskSearchWorking:'Seçilmiş mənbələrdə axtarılır…',taskSearchComplete:'Axtarış tamamlandı',taskSearchEmpty:'Ən azı 2 simvol daxil edin.',taskSearchNoResults:'Əlçatan mənbələrdə bu sorğu ilə tapşırıq tapılmadı.',taskSearchNoSources:'Seçilmiş mənbələr hazırda əlçatan deyil.',taskSearchFailed:'Tapşırıqları axtarmaq mümkün olmadı.',taskSearchPartial:'Nəticə natamam ola bilər: mənbə əlçatan deyil və ya əlavə səhifələr var.',taskSourceAvailable:'əlçatandır',taskSourceUnavailable:'əlçatan deyil',taskSearchMore:'Əlavə nəticələr var.',taskSearchStatus:'Status',taskSearchAssignee:'Məsul şəxs',taskSearchDue:'Son tarix',taskSearchUpdated:'Yenilənib',taskSearchOpen:'Tapşırığı aç',taskSearchOpenApp:'Tapşırıq siyahısını aç',taskSearchReadOnly:'Nəticələr mənbənin cari vəziyyətini göstərir. Tapşırığın statusu müştəri probleminin həllini öz-özlüyündə təsdiqləmir.'
    },
    en:{
      title:'AI Business Analyst',intro:'One question per read-only analysis using permitted sources. The assistant cannot change CRM, tasks, or settings. Source coverage and freshness may be limited.',placeholder:'For example: Which confirmed risks need attention now?',ask:'Ask',working:'Checking available sources and preparing an answer…',ready:'Answer ready. Sources and limitations are listed below.',unknown:'The request may have reached AI, so it will not be retried automatically. Check before starting a new request.',owner:'This tool is available only to the company owner.',empty:'Enter a question.',sources:'Sources',noSources:'This answer has no record references.',open:'Open app section',footer:'Voice analysis uses a separate session without dialog history. Audio and results are not saved.',voiceButton:'Start voice analysis',voiceConnecting:'Connecting voice analysis…',voiceActive:'Voice connected. Speak naturally; AI will answer after a pause.',voiceEnded:'Voice conversation ended.',voiceFailed:'Could not connect voice analysis. Check access and microphone.',
      taskSearchTitle:'Direct task search',taskSearchIntro:'Searches connected sources without calling AI. Read-only: tasks and statuses are not changed.',taskSearchLabel:'Task title, ID, or text',taskSearchPlaceholder:'For example: receipt printing or BS-1364',taskSourceLabel:'Source',taskSourceAll:'All sources',taskSourceNative:'Beintaskbot',taskSourceKommo:'Kommo',taskSourceLinear:'Linear',taskSearchButton:'Search tasks',taskSearchWorking:'Searching selected sources…',taskSearchComplete:'Search complete',taskSearchEmpty:'Enter at least 2 characters.',taskSearchNoResults:'No tasks matched in available sources.',taskSearchNoSources:'None of the selected sources is currently available.',taskSearchFailed:'Task search failed.',taskSearchPartial:'Results may be incomplete: a source is unavailable or has more pages.',taskSourceAvailable:'available',taskSourceUnavailable:'unavailable',taskSearchMore:'More results are available.',taskSearchStatus:'Status',taskSearchAssignee:'Assignee',taskSearchDue:'Due',taskSearchUpdated:'Updated',taskSearchOpen:'Open task',taskSearchOpenApp:'Open task list',taskSearchReadOnly:'Results reflect a current source snapshot. A task status alone does not confirm that a customer issue was resolved.'
    }
  };
  let member=null,epoch=0,busy=false,controller=null,taskBusy=false,taskController=null,lastLang='';
  let voiceSection=null,voiceStarting=false,voiceState='idle',voiceWasStarted=false;
  const lang=()=>copy[window.tenantI18n?.language]||copy.az;
  function clear(){epoch++;controller?.abort();taskController?.abort();controller=null;taskController=null;busy=false;taskBusy=false;
    if(voiceSection){void voiceSection.disposeVoice?.();voiceSection=null;}voiceStarting=false;voiceState='idle';voiceWasStarted=false;host.replaceChildren();}
  function node(tag,text,className){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(className)n.className=className;return n;}
  function displayDate(value){const timestamp=Date.parse(String(value||''));return Number.isFinite(timestamp)?new Date(timestamp).toLocaleString():String(value||'');}
  function renderTask(task,container,w){
    if(!task||typeof task!=='object')return;
    const source=String(task.source||''),id=String(task.task_id||''),title=String(task.title||id||'Task');
    const item=node('article',undefined,'analyst-task-result');item.append(node('strong',title));
    const sourceName=source==='native'?w.taskSourceNative:source==='kommo'?w.taskSourceKommo:source==='linear'?w.taskSourceLinear:source;
    const meta=[sourceName,id,task.status&&`${w.taskSearchStatus}: ${task.status}`,task.assignee&&`${w.taskSearchAssignee}: ${task.assignee}`,task.due_at&&`${w.taskSearchDue}: ${displayDate(task.due_at)}`,task.updated_at&&`${w.taskSearchUpdated}: ${displayDate(task.updated_at)}`].filter(Boolean);
    if(meta.length)item.append(node('small',meta.join(' · '),'analyst-task-meta'));
    if(typeof task.description==='string'&&task.description)item.append(node('p',task.description,'analyst-task-description'));
    const ref=task.source_ref&&typeof task.source_ref==='object'?task.source_ref:{};
    let href='',external=false;
    if(source==='linear'&&typeof ref.url==='string'){
      try{const url=new URL(ref.url);if(url.protocol==='https:'&&url.hostname==='linear.app'){href=url.href;external=true;}}catch{}
    }else if((source==='native'||source==='kommo')&&ref.inbox_url==='/app')href='/app?view=tasks';
    if(href){const link=node('a',external?w.taskSearchOpen:w.taskSearchOpenApp,'analyst-task-link');link.href=href;link.rel='noopener noreferrer';if(external)link.target='_blank';item.append(link);}
    container.append(item);
  }
  function render(){
    host.replaceChildren();const w=lang();
    if(member?.role!=='owner')return;
    const card=node('article',undefined,'panel ai-business-analyst');
    card.append(node('h2',w.title),node('p',w.intro));
    const form=node('form');const input=node('textarea');input.maxLength=1600;input.required=true;input.setAttribute('aria-label',w.placeholder);input.placeholder=w.placeholder;
    const actions=node('div',undefined,'analyst-actions');const ask=node('button',w.ask,'primary');ask.type='submit';const status=node('p','', 'sub');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const answer=node('div','', 'analyst-answer');answer.hidden=true;const sources=node('div',undefined,'analyst-sources');sources.hidden=true;
    const sourceHeading=node('strong',w.sources);sourceHeading.hidden=true;
    const footer=node('p',w.footer,'sub');footer.style.marginTop='12px';
    actions.append(ask);form.append(input,actions);card.append(form,status,answer,sourceHeading,sources);

    const voiceActions=node('div',undefined,'analyst-voice-actions');
    const voiceButton=node('button',w.voiceButton,'primary analyst-voice-button');voiceButton.type='button';
    voiceButton.disabled=voiceStarting||voiceState==='active';
    const voiceStatus=node('p',voiceStarting?w.voiceConnecting:voiceState==='active'?w.voiceActive:voiceState==='ended'?w.voiceEnded:'','sub analyst-voice-status');
    voiceStatus.setAttribute('role','status');voiceStatus.setAttribute('aria-live','polite');
    voiceActions.append(voiceButton,voiceStatus);card.append(voiceActions);

    const taskPanel=node('section',undefined,'analyst-task-search');
    taskPanel.append(node('h3',w.taskSearchTitle),node('p',w.taskSearchIntro));
    const taskForm=node('form',undefined,'analyst-task-search-form');
    const queryLabel=node('label',w.taskSearchLabel);const query=node('input');query.type='search';query.required=true;query.minLength=2;query.maxLength=120;query.autocomplete='off';query.placeholder=w.taskSearchPlaceholder;query.setAttribute('aria-label',w.taskSearchLabel);queryLabel.append(query);
    const sourceLabel=node('label',w.taskSourceLabel);const sourceSelect=node('select');sourceSelect.setAttribute('aria-label',w.taskSourceLabel);
    [['all',w.taskSourceAll],['native',w.taskSourceNative],['kommo',w.taskSourceKommo],['linear',w.taskSourceLinear]].forEach(([value,label])=>{const option=node('option',label);option.value=value;sourceSelect.append(option);});sourceLabel.append(sourceSelect);
    const taskButton=node('button',w.taskSearchButton,'primary');taskButton.type='submit';taskButton.disabled=taskBusy;query.disabled=taskBusy;sourceSelect.disabled=taskBusy;
    const taskStatus=node('p','', 'sub analyst-task-status');taskStatus.setAttribute('role','status');taskStatus.setAttribute('aria-live','polite');
    const coverage=node('div',undefined,'analyst-task-coverage');coverage.setAttribute('aria-live','polite');
    const results=node('div',undefined,'analyst-task-results');results.setAttribute('aria-live','polite');
    const readOnly=node('p',w.taskSearchReadOnly,'sub analyst-task-readonly');
    taskForm.append(queryLabel,sourceLabel,taskButton);taskPanel.append(taskForm,taskStatus,coverage,results,readOnly);card.append(taskPanel,footer);host.append(card);

    voiceButton.addEventListener('click',async()=>{
      if(voiceStarting||member?.role!=='owner'||!host.isConnected)return;
      const expected={tenant_id:String(member.tenant_id||''),user_id:String(member.telegram_id||'')};
      if(!expected.tenant_id||!expected.user_id)return;
      const isCurrent=()=>member?.role==='owner'&&String(member.tenant_id||'')===expected.tenant_id&&
        String(member.telegram_id||'')===expected.user_id&&host.isConnected;
      voiceStarting=true;voiceState='connecting';voiceButton.disabled=true;voiceStatus.textContent=w.voiceConnecting;
      try{
        if(voiceSection){await voiceSection.disposeVoice?.();voiceSection=null;}
        const language=window.tenantI18n?.language||'ru';
        const query=new URLSearchParams({expected_tenant_id:expected.tenant_id,expected_user_id:expected.user_id});
        voiceSection=window.mountTenantCopilotVoice?.(document.body,{
          language,isCurrent,socketPath:'/api/platform/ai/analytics/voice/ws?'+query.toString(),
          preflight:async()=>{
            const response=await fetch('/api/platform/ai/analytics/voice/preflight',{method:'POST',credentials:'same-origin',cache:'no-store',
              headers:{'Content-Type':'application/json','Accept':'application/json'},
              body:JSON.stringify({expected_tenant_id:expected.tenant_id,expected_user_id:expected.user_id})});
            const data=await response.json().catch(()=>({success:false}));
            if(!response.ok||data.success!==true||data.result?.available!==true)throw Error('voice_unavailable');
            return data.result;
          },
          onState:state=>{
            voiceState=state==='listening'||state==='responding'||state==='muted'?'active':
              state==='idle'&&voiceWasStarted?'ended':state;
            if(voiceStatus.isConnected)voiceStatus.textContent=voiceState==='active'?w.voiceActive:
              voiceState==='ended'?w.voiceEnded:state==='connecting'?w.voiceConnecting:'';
            if(voiceButton.isConnected)voiceButton.disabled=voiceStarting||voiceState==='active';
          },
          onIdle:()=>{voiceWasStarted=true;voiceState='ended';if(voiceStatus.isConnected)voiceStatus.textContent=w.voiceEnded;
            if(voiceButton.isConnected)voiceButton.disabled=false;}
        });
        if(!voiceSection||await voiceSection.startVoice?.()!==true)throw Error('voice_unavailable');
        voiceWasStarted=true;
      }catch(_error){voiceState='idle';voiceStatus.textContent=w.voiceFailed;
        if(voiceSection){void voiceSection.disposeVoice?.();voiceSection=null;}}
      finally{voiceStarting=false;if(voiceButton.isConnected)voiceButton.disabled=voiceState==='active';}
    });

    form.addEventListener('submit',async event=>{
      event.preventDefault();if(busy||member?.role!=='owner')return;
      const prompt=input.value.trim();if(!prompt){status.textContent=w.empty;input.focus();return;}
      const expected={tenant_id:String(member.tenant_id||''),user_id:String(member.telegram_id||'')};
      if(!expected.tenant_id||!expected.user_id)return;
      let requestId;try{requestId=crypto.randomUUID();}catch{status.textContent=w.unknown;return;}
      const requestEpoch=epoch;busy=true;ask.disabled=true;input.disabled=true;answer.hidden=true;answer.textContent='';sources.replaceChildren();sources.hidden=true;sourceHeading.hidden=true;status.textContent=w.working;
      controller=new AbortController();
      try{
        const response=await fetch('/api/platform/ai/analytics/ask',{method:'POST',credentials:'same-origin',cache:'no-store',signal:controller.signal,
          headers:{'Content-Type':'application/json','Accept':'application/json'},
          body:JSON.stringify({expected_tenant_id:expected.tenant_id,expected_user_id:expected.user_id,request_id:requestId,prompt})});
        const data=await response.json().catch(()=>({success:false}));
        if(requestEpoch!==epoch||member?.role!=='owner'||String(member.tenant_id||'')!==expected.tenant_id||String(member.telegram_id||'')!==expected.user_id)return;
        if(!response.ok||data.success!==true||!data.result||typeof data.result.text!=='string')throw Error(response.status===409?w.unknown:(data.error||w.unknown));
        answer.textContent=data.result.text;answer.hidden=false;
        const refs=Array.isArray(data.result.source_refs)?data.result.source_refs.slice(0,30):[];
        if(refs.length){sourceHeading.hidden=false;sources.hidden=false;for(const ref of refs){
          const label=[ref.kind,ref.provider,ref.source,ref.message_id||ref.candidate_id||ref.task_id||ref.chunk_no].filter(v=>v!==undefined&&v!==null&&String(v).length).join(' · ')||w.sources;
          const row=node('div');if(typeof ref.inbox_url==='string'&&ref.inbox_url.startsWith('/app')){const link=node('a',w.open);link.href=ref.inbox_url;link.rel='noopener';link.target='_blank';row.append(node('span',label+' · '),link);}else row.textContent=label;
          sources.append(row);
        }}else{sourceHeading.hidden=false;sources.hidden=false;sources.append(node('span',w.noSources));}
        const usage=data.result.usage||{},tokens=(Number(usage.input_tokens)||0)+(Number(usage.output_tokens)||0);
        status.textContent=w.ready+(data.result.model?' · '+data.result.model:'')+(tokens?' · '+tokens+' tokens':'');
      }catch(error){if(requestEpoch===epoch&&member?.role==='owner'){status.textContent=error.name==='AbortError'?w.unknown:(error.message||w.unknown);}}
      finally{if(requestEpoch===epoch){busy=false;controller=null;ask.disabled=false;input.disabled=false;}}
    });

    taskForm.addEventListener('submit',async event=>{
      event.preventDefault();if(taskBusy||member?.role!=='owner')return;
      const search=query.value.trim();if(search.length<2){taskStatus.textContent=w.taskSearchEmpty;query.focus();return;}
      const expected={tenant_id:String(member.tenant_id||''),user_id:String(member.telegram_id||'')};
      if(!expected.tenant_id||!expected.user_id)return;
      const requestEpoch=epoch;taskBusy=true;taskButton.disabled=true;query.disabled=true;sourceSelect.disabled=true;
      taskStatus.textContent=w.taskSearchWorking;coverage.replaceChildren();results.replaceChildren();taskController=new AbortController();
      try{
        const response=await fetch('/api/platform/ai/analytics/tools',{method:'POST',credentials:'same-origin',cache:'no-store',signal:taskController.signal,
          headers:{'Content-Type':'application/json','Accept':'application/json'},
          body:JSON.stringify({expected_tenant_id:expected.tenant_id,expected_user_id:expected.user_id,tool:'search_tasks',arguments:{query:search,source:sourceSelect.value,limit:10}})});
        const data=await response.json().catch(()=>({success:false}));
        if(requestEpoch!==epoch||member?.role!=='owner'||String(member.tenant_id||'')!==expected.tenant_id||String(member.telegram_id||'')!==expected.user_id)return;
        if(!response.ok||data.success!==true||!data.result||!Array.isArray(data.result.tasks))throw Error(data.error||w.taskSearchFailed);
        const result=data.result,states=result.sources&&typeof result.sources==='object'?result.sources:{};
        const entries=Object.entries(states);let available=0,partial=result.partial===true;
        for(const [kind,state] of entries){
          if(!state||typeof state!=='object')continue;
          const sourceName=kind==='native'?w.taskSourceNative:kind==='kommo'?w.taskSourceKommo:kind==='linear'?w.taskSourceLinear:kind;
          const line=node('div',undefined,'analyst-task-source');const summary=node('strong',`${sourceName}: ${state.available===true?w.taskSourceAvailable:w.taskSourceUnavailable}`);line.append(summary);
          const details=[];if(typeof state.scope==='string'&&state.scope)details.push(state.scope);if(state.has_more===true){details.push(w.taskSearchMore);partial=true;}if(typeof state.freshness==='string'&&state.freshness)details.push(state.freshness);
          if(state.available===true)available++;else partial=true;
          if(details.length)line.append(node('small',details.join(' · '),'analyst-task-meta'));
          coverage.append(line);
        }
        if(result.checked_at)coverage.prepend(node('small',`${w.taskSearchComplete} · ${displayDate(result.checked_at)}`,'analyst-task-meta'));
        const tasks=result.tasks.slice(0,75);for(const task of tasks)renderTask(task,results,w);
        if(available===0){taskStatus.textContent=w.taskSearchNoSources;}
        else if(tasks.length===0){taskStatus.textContent=w.taskSearchNoResults;}
        else taskStatus.textContent=`${w.taskSearchComplete}: ${tasks.length}`+(partial?` · ${w.taskSearchPartial}`:'');
      }catch(error){if(requestEpoch===epoch&&member?.role==='owner'){taskStatus.textContent=error.name==='AbortError'?w.taskSearchFailed:(error.message||w.taskSearchFailed);}}
      finally{if(requestEpoch===epoch){taskBusy=false;taskController=null;taskButton.disabled=false;query.disabled=false;sourceSelect.disabled=false;}}
    });
  }
  function receive(data){const next=data?.member||null;const changed=String(next?.tenant_id||'')!==String(member?.tenant_id||'')||String(next?.telegram_id||'')!==String(member?.telegram_id||'')||next?.role!==member?.role;member=next;if(changed)clear();render();}
  document.addEventListener('tenant-profile',event=>receive(event.detail));
  document.addEventListener('tenant-auth-invalid',()=>receive(null));document.addEventListener('tenant-auth-pending',()=>receive(null));
  document.addEventListener('tenant-language',()=>{if(lastLang!==window.tenantI18n?.language){lastLang=window.tenantI18n?.language;render();}});
  if(window.tenantProfileSnapshot)receive(window.tenantProfileSnapshot);else fetch('/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(r=>r.json()).then(receive).catch(()=>receive(null));
})();
