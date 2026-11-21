/* Local CRM company binding for the exact authorized dialogue route. */
(()=>{
  'use strict';
  const endpoint='/api/platform/operator/dialog-company';
  const labels={
    ru:{label:'Компания',unknown:'Не указана или недоступна',unavailable:'Недоступна',loading:'Загрузка…',edit:'Изменить компанию',search:'Найти CRM-компанию',save:'Сохранить',remove:'Снять связь',confirm:'Снять привязку компании с этого диалога?',cancel:'Отмена',choose:'Выберите из результатов поиска',empty:'Компании с правом привязки не найдены.',error:'Не удалось получить или изменить компанию.'},
    az:{label:'Şirkət',unknown:'Göstərilməyib və ya əlçatan deyil',unavailable:'Əlçatan deyil',loading:'Yüklənir…',edit:'Şirkəti dəyiş',search:'CRM şirkətini tap',save:'Yadda saxla',remove:'Əlaqəni sil',confirm:'Bu dialoqun şirkətlə əlaqəsini silmək istəyirsiniz?',cancel:'Ləğv et',choose:'Axtarış nəticələrindən seçin',empty:'Əlaqələndirə biləcəyiniz şirkət tapılmadı.',error:'Şirkət məlumatını almaq və ya dəyişmək mümkün olmadı.'},
    en:{label:'Company',unknown:'Not specified or unavailable',unavailable:'Unavailable',loading:'Loading…',edit:'Change company',search:'Find a CRM company',save:'Save',remove:'Remove company link',confirm:'Remove the company link from this dialogue?',cancel:'Cancel',choose:'Choose a search result',empty:'No linkable companies found.',error:'Could not get or change the company.'}
  };
  const t=key=>(labels[window.tenantI18n?.language]||labels.az)[key];
  const node=(tag,txt)=>{const e=document.createElement(tag);if(txt!=null)e.textContent=txt;return e;};
  const button=(txt,fn)=>{const b=node('button',txt);b.type='button';b.onclick=fn;return b;};
  let state=null;
  const current=s=>s===state&&s.section.isConnected&&s.isCurrent();
  function clear(){
    const old=state;state=null;
    old?.read?.abort();old?.abortSearch?.();
    old?.panel?.remove();old?.section.remove();
  }
  async function request(path,member,fields,post=false,signal){
    const data={...fields,expected_tenant_id:String(member.tenant_id),expected_user_id:String(member.telegram_id)};
    const result=await fetch(path+(post?'':'?'+new URLSearchParams(data)),{
      cache:'no-store',credentials:'same-origin',signal,method:post?'POST':'GET',
      ...(post?{headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{})
    });
    const payload=await result.json();
    if(!result.ok||payload?.success!==true){
      const error=new Error(t('error'));error.status=result.status;throw error;
    }
    return payload;
  }
  function paint(s,binding){
    if(!current(s))return;
    s.binding=binding;
    // The API may mask an existing but inaccessible company as null.
    const name=binding?.company_id&&binding.company_name?String(binding.company_name):binding?t('unknown'):t('unavailable');
    s.value.textContent=name;s.value.title=name;
    s.edit.hidden=binding?.can_manage!==true;
    if(!binding?.can_manage&&s.panel){
      s.abortSearch?.();s.panel.remove();s.panel=null;
    }
  }
  function refresh(s=state){
    if(!s||!current(s)||s.read||s.writing)return;
    const read=new AbortController();s.read=read;
    void request(endpoint,s.member,s.route,false,read.signal)
      .then(payload=>{if(s.read===read)paint(s,payload.binding);},error=>{
        if(s.read===read&&(!s.binding||[401,403,404,409].includes(error.status)))paint(s,null);
      }).finally(()=>{if(s.read===read)s.read=null;});
  }
  function open(s){
    if(!current(s)||s.binding?.can_manage!==true||s.panel?.isConnected)return;
    const panel=node('dialog'),heading=node('h2',t('edit')),status=node('p');
    panel.id='operatorDialogueCompanyPicker';heading.id='operatorDialogueCompanyPickerTitle';
    panel.setAttribute('aria-labelledby',heading.id);status.setAttribute('role','status');
    const form=node('form'),label=node('label'),input=node('input'),search=node('button',t('search'));
    input.type='search';input.maxLength=120;input.autocomplete='off';input.placeholder=t('search');input.setAttribute('aria-label',t('search'));
    input.setAttribute('data-lpignore','true');input.setAttribute('data-1p-ignore','true');
    search.type='submit';label.append(input);form.append(label,search);
    const choices=node('select');choices.setAttribute('aria-label',t('choose'));
    const actions=node('div');actions.className='operator-dialog-company-actions';
    const save=button(t('save'),()=>void mutate(choices.value));
    save.className='primary';
    const cancel=button(t('cancel'),close),remove=button(t('remove'),()=>{
      if(current(s)&&s.binding?.company_id&&window.confirm(t('confirm')))void mutate(null);
    });
    remove.hidden=!s.binding.company_id;
    actions.append(save,remove,cancel);panel.append(heading,form,choices,status,actions);document.body.append(panel);
    s.panel=panel;let writing=false,serial=0,searchRequest;
    s.abortSearch=()=>searchRequest?.abort();
    const active=()=>current(s)&&s.panel===panel&&panel.isConnected;
    function close(){if(writing)return;searchRequest?.abort();serial++;panel.remove();if(current(s)){s.panel=null;s.edit.focus({preventScroll:true});}}
    function resetChoices(){choices.replaceChildren(new Option(t('choose'),''));choices.disabled=true;save.disabled=true;}
    async function mutate(companyId){
      if(!active()||writing||s.binding?.can_manage!==true||companyId==='')return;
      writing=true;s.writing=true;searchRequest?.abort();s.read?.abort();s.read=null;serial++;status.textContent=t('loading');
      for(const ctrl of [input,search,save,remove,cancel,choices])ctrl.disabled=true;
      try{
        const payload=await request(endpoint,s.member,{...s.route,company_id:companyId},true);
        if(!active())return;
        paint(s,payload.binding);writing=false;close();
        // Refresh lifecycle metadata using the ordinary scoped dialogue read.
        if(current(s))void s.onChanged?.();
      }catch{
        if(active()){
          status.textContent=t('error');input.disabled=false;search.disabled=false;cancel.disabled=false;remove.disabled=false;
          choices.disabled=choices.options.length<=1;save.disabled=!choices.value;
        }
      }finally{writing=false;s.writing=false;}
    }
    resetChoices();
    input.oninput=()=>{searchRequest?.abort();serial++;resetChoices();status.textContent='';};
    choices.onchange=()=>{save.disabled=writing||!choices.value;};
    form.onsubmit=async event=>{
      event.preventDefault();if(!active()||writing)return;
      searchRequest?.abort();searchRequest=new AbortController();
      const attempt=++serial;
      resetChoices();status.textContent=t('loading');
      try{
        const payload=await request('/api/platform/crm/native/companies',s.member,{search:input.value.trim()},false,searchRequest.signal);
        if(!active()||attempt!==serial)return;
        const companies=(payload.companies||[]).filter(row=>row.actions?.update===true&&row.id);
        for(const company of companies)choices.append(new Option(String(company.name||''),String(company.id)));
        choices.disabled=!companies.length;status.textContent=companies.length?'':t('empty');
      }catch{if(active()&&attempt===serial)status.textContent=t('error');}
    };
    panel.addEventListener('cancel',event=>{event.preventDefault();event.stopImmediatePropagation();close();});
    panel.addEventListener('keydown',event=>{if(event.key==='Escape'){event.stopPropagation();event.preventDefault();close();}});
    panel.addEventListener('click',event=>{if(event.target===panel)close();});
    if(panel.showModal)panel.showModal();else panel.setAttribute('open','');
    input.focus({preventScroll:true});
  }
  function mount({host,route,member,isCurrent,onChanged}){
    if(!host?.isConnected||!route?.provider||!route?.lead_id
        ||typeof route.conversation_id!=='string'||typeof route.channel_id!=='string'
        ||(route.provider==='connect'&&(!route.channel_id||!route.conversation_id))
        ||!member?.tenant_id||member?.telegram_id==null||!isCurrent()){clear();return;}
    const exact={provider:String(route.provider),lead_id:String(route.lead_id),
      channel_id:route.channel_id,conversation_id:route.conversation_id};
    const key=JSON.stringify([String(member.tenant_id),String(member.telegram_id),exact]);
    // Reuse a confirmed binding only for this exact authorized conversation.
    if(state?.key===key){
      state.isCurrent=isCurrent;state.onChanged=onChanged;state.member=member;
      if(state.section.parentElement!==host)host.append(state.section);
      return;
    }
    clear();
    const section=node('div'),label=node('span',t('label')+':'),value=node('span',t('loading'));
    section.dataset.operatorDialogCompany='';
    value.dataset.companyValue='';value.setAttribute('role','status');value.setAttribute('aria-live','polite');
    const s={section,value,member,isCurrent,onChanged,route:exact,key,read:null};
    s.edit=button(t('edit'),()=>open(s));s.edit.hidden=true;s.edit.className='outline';
    section.append(label,value,s.edit);host.append(section);state=s;
    refresh(s);
  }
  const style=node('style');
  style.textContent=`
    [data-operator-dialog-company]{display:flex;align-items:center;gap:4px 6px;flex-wrap:wrap;min-width:0;max-width:min(100%,19rem);font-size:.78rem;color:var(--muted)}
    @media(max-width:900px){[data-operator-dialog-company]{grid-column:1/-1;max-width:100%}}
    [data-company-value]{max-width:100%;overflow-wrap:anywhere;color:var(--ink)}
    [data-operator-dialog-company]>button{min-height:32px;font-size:inherit;padding:4px 8px}
    [data-operator-dialog-company]>button[hidden],#operatorDialogueCompanyPicker [hidden]{display:none!important}
    #operatorDialogueCompanyPicker{box-sizing:border-box;width:min(440px,calc(100vw - 24px));max-height:calc(100dvh - 24px);padding:16px;border:1px solid var(--line);border-radius:14px;background:var(--card);color:var(--ink);overflow:auto}
    #operatorDialogueCompanyPicker::backdrop{background:#071d1688}
    #operatorDialogueCompanyPicker h2{margin:0 0 12px;font-size:1.1rem}
    #operatorDialogueCompanyPicker form,#operatorDialogueCompanyPicker .operator-dialog-company-actions{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
    #operatorDialogueCompanyPicker label{flex:1 1 160px;min-width:0}
    #operatorDialogueCompanyPicker input,#operatorDialogueCompanyPicker select{box-sizing:border-box;min-width:0;width:100%;min-height:44px;font-size:16px;margin:0}
    #operatorDialogueCompanyPicker select{margin-top:8px}
    #operatorDialogueCompanyPicker button{min-height:44px}
  `;document.head.append(style);
  for(const event of ['tenant-auth-invalid','tenant-auth-pending','tenant-chat-open','tenant-chat-close'])
    document.addEventListener(event,clear);
  document.addEventListener('tenant-profile',event=>{
    if(!state)return;
    const next=event.detail?.member;
    if(event.detail?.capabilities?.dialog_company_binding!==true||!next?.active||
        String(next.tenant_id)!==String(state.member.tenant_id)||String(next.telegram_id)!==String(state.member.telegram_id))clear();
  });
  document.addEventListener('tenant-view',event=>{if(event.detail?.view!=='dialogs')clear();});
  document.addEventListener('tenant-dialog-reauthorized',()=>refresh());
  window.tenantDialogueCompany={mount,clear};
})();
