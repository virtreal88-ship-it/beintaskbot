/* Owner-only, single-turn text interface to the read-only Business Analyst registry. */
(()=>{'use strict';
  const host=document.getElementById('aiBusinessAnalystHost');if(!host)return;
  const copy={
    ru:{title:'AI Business Analyst',intro:'Один вопрос — один анализ по разрешённым источникам. Режим только чтения: ассистент не меняет CRM, задачи или настройки. Полнота и актуальность данных могут быть ограничены.',placeholder:'Например: Какие подтверждённые риски сейчас требуют внимания?',ask:'Спросить',working:'Проверяю доступные источники и готовлю ответ…',ready:'Ответ готов. Источники и ограничения указаны ниже.',unknown:'Запрос мог дойти до AI, поэтому автоматического повтора не будет. Проверьте результат перед новым запросом.',owner:'Инструмент доступен только владельцу компании.',empty:'Введите вопрос.',sources:'Источники',noSources:'В этом ответе нет ссылок на записи источников.',open:'Открыть раздел приложения',footer:'Голосовое подключение к аналитике пока не включено. История вопросов не сохраняется в этом интерфейсе.'},
    az:{title:'AI Business Analyst',intro:'Bir sual — icazəli mənbələr üzrə bir təhlil. Yalnız oxuma rejimidir: köməkçi CRM-i, tapşırıqları və ya ayarları dəyişmir. Məlumatların tamlığı və yeniliyi məhdud ola bilər.',placeholder:'Məsələn: Hazırda hansı təsdiqlənmiş risklərə diqqət yetirmək lazımdır?',ask:'Soruş',working:'Əlçatan mənbələr yoxlanılır və cavab hazırlanır…',ready:'Cavab hazırdır. Mənbələr və məhdudiyyətlər aşağıdadır.',unknown:'Sorğu AI-yə çatmış ola bilər, ona görə avtomatik təkrar göndərilməyəcək. Yeni sorğudan əvvəl nəticəni yoxlayın.',owner:'Bu alət yalnız şirkət sahibinə açıqdır.',empty:'Sualı daxil edin.',sources:'Mənbələr',noSources:'Bu cavabda mənbə qeydlərinə istinad yoxdur.',open:'Tətbiq bölməsini aç',footer:'Analitikaya səs bağlantısı hələ aktiv deyil. Bu interfeysdə sual tarixçəsi saxlanmır.'},
    en:{title:'AI Business Analyst',intro:'One question per read-only analysis using permitted sources. The assistant cannot change CRM, tasks, or settings. Source coverage and freshness may be limited.',placeholder:'For example: Which confirmed risks need attention now?',ask:'Ask',working:'Checking available sources and preparing an answer…',ready:'Answer ready. Sources and limitations are listed below.',unknown:'The request may have reached AI, so it will not be retried automatically. Check before starting a new request.',owner:'This tool is available only to the company owner.',empty:'Enter a question.',sources:'Sources',noSources:'This answer has no record references.',open:'Open app section',footer:'Voice access to analytics is not enabled yet. This interface does not save question history.'}
  };
  let member=null,epoch=0,busy=false,controller=null,lastLang='';
  const lang=()=>copy[window.tenantI18n?.language]||copy.az;
  function clear(){epoch++;controller?.abort();controller=null;busy=false;host.replaceChildren();}
  function node(tag,text,className){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(className)n.className=className;return n;}
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
    actions.append(ask);form.append(input,actions);card.append(form,status,answer,sourceHeading,sources,footer);host.append(card);
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
  }
  function receive(data){const next=data?.member||null;const changed=String(next?.tenant_id||'')!==String(member?.tenant_id||'')||String(next?.telegram_id||'')!==String(member?.telegram_id||'')||next?.role!==member?.role;member=next;if(changed)clear();render();}
  document.addEventListener('tenant-profile',event=>receive(event.detail));
  document.addEventListener('tenant-auth-invalid',()=>receive(null));document.addEventListener('tenant-auth-pending',()=>receive(null));
  document.addEventListener('tenant-language',()=>{if(lastLang!==window.tenantI18n?.language){lastLang=window.tenantI18n?.language;render();}});
  if(window.tenantProfileSnapshot)receive(window.tenantProfileSnapshot);else fetch('/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(r=>r.json()).then(receive).catch(()=>receive(null));
})();
