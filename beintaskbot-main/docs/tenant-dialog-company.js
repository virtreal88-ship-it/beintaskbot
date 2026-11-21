/* Local CRM company binding for the exact authorized dialogue route. */
(()=>{
  'use strict';
  const endpoint='/api/platform/operator/dialog-company';
  const labels={
    ru:{label:'Компания',unknown:'Компания не выбрана',unavailable:'Недоступна',loading:'Загрузка…',chooseCompany:'Выбрать компанию',edit:'Изменить компанию',search:'Найти компанию',save:'Сохранить',remove:'Снять связь',confirm:'Снять привязку компании с этого диалога?',cancel:'Отмена',choose:'Выберите из результатов поиска',empty:'Компании с правом привязки не найдены.',error:'Не удалось получить или изменить компанию.'},
    az:{label:'Şirkət',unknown:'Şirkət seçilməyib',unavailable:'Əlçatan deyil',loading:'Yüklənir…',chooseCompany:'Şirkət seç',edit:'Şirkəti dəyiş',search:'Şirkət axtar',save:'Yadda saxla',remove:'Əlaqəni sil',confirm:'Bu dialoqun şirkətlə əlaqəsini silmək istəyirsiniz?',cancel:'Ləğv et',choose:'Axtarış nəticələrindən seçin',empty:'Əlaqələndirə biləcəyiniz şirkət tapılmadı.',error:'Şirkət məlumatını almaq və ya dəyişmək mümkün olmadı.'},
    en:{label:'Company',unknown:'No company selected',unavailable:'Unavailable',loading:'Loading…',chooseCompany:'Choose a company',edit:'Change company',search:'Find a company',save:'Save',remove:'Remove company link',confirm:'Remove the company link from this dialogue?',cancel:'Cancel',choose:'Choose a search result',empty:'No linkable companies found.',error:'Could not get or change the company.'}
  };
  const operatorLabels={
    ru:{title:'Найти аккаунт Operator',placeholder:'Аккаунт Operator',hint:'Введите хотя бы 4 символа.',empty:'Аккаунты не найдены.',error:'Не удалось найти аккаунты Operator.',bindError:'Не удалось привязать аккаунт. Можно повторить выбор или найти компанию в CRM.',local:'Найти в CRM'},
    az:{title:'Operator hesabını tap',placeholder:'Operator hesabı',hint:'Ən azı 4 simvol daxil edin.',empty:'Hesab tapılmadı.',error:'Operator hesablarını tapmaq mümkün olmadı.',bindError:'Hesabı bağlamaq mümkün olmadı. Yenidən seçin və ya CRM-də şirkəti tapın.',local:'CRM-də tap'},
    en:{title:'Find an Operator account',placeholder:'Operator account',hint:'Enter at least 4 characters.',empty:'No accounts found.',error:'Could not find Operator accounts.',bindError:'Could not link this account. Select it again or find a company in CRM.',local:'Find in CRM'}
  };
  const t=key=>(labels[window.tenantI18n?.language]||labels.az)[key];
  const copy=key=>(operatorLabels[window.tenantI18n?.language]||operatorLabels.az)[key];
  const node=(tag,txt)=>{const e=document.createElement(tag);if(txt!=null)e.textContent=txt;return e;};
  const button=(txt,fn)=>{const b=node('button',txt);b.type='button';b.onclick=fn;return b;};
  let state=null;
  const headerHosts=new WeakMap();
  const current=s=>s===state&&s.section.isConnected&&s.isCurrent();
  function paintHeader(s,verified){
    s.headerVerified=verified;
    const el=headerHosts.get(s.section),binding=s.binding;
    if(!el?.isConnected)return;
    const name=verified&&current(s)&&binding?.company_id&&typeof binding.company_name==='string'
      ? binding.company_name:'';
    el.textContent=name.trim()?name:'';
    el.hidden=!el.textContent;
    if(el.hidden)el.removeAttribute('title');else el.title=name;
  }
  function clear(){
    if(state)paintHeader(state,false);
    const old=state;state=null;
    old?.read?.abort();old?.abortSearch?.();
    old?.closeOperator?.(false);
    old?.closeLocal?.(false);
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
    paintHeader(s,true);
    // The API may mask an existing but inaccessible company as null.
    const name=binding?.company_id&&binding.company_name?String(binding.company_name):binding?t('unknown'):t('unavailable');
    s.value.textContent=name;s.value.title=name;
    s.edit.textContent=t(binding?.company_id?'edit':'chooseCompany');
    s.edit.hidden=binding?.can_manage!==true;
    if(!binding?.can_manage)s.closeLocal?.(false);
    if(!binding?.can_manage||binding.can_search_operator!==true||binding.company_id)s.closeOperator?.(false);
  }
  function refresh(s=state){
    if(!s||!current(s)||s.read||s.writing)return;
    paintHeader(s,false);
    const read=new AbortController();s.read=read;
    void request(endpoint,s.member,s.route,false,read.signal)
      .then(payload=>{if(s.read===read)paint(s,payload.binding);},error=>{
        if(s.read===read&&(!s.binding||[401,403,404,409].includes(error.status)))paint(s,null);
      }).finally(()=>{if(s.read===read)s.read=null;});
  }
  function openLocal(s){
    if(!current(s)||s.binding?.can_manage!==true||s.panel?.isConnected)return;
    const panel=node('div'),status=node('p');
    panel.id='operatorDialogueCompanyPicker';panel.setAttribute('role','group');
    panel.setAttribute('aria-label',t('search'));status.setAttribute('role','status');
    const form=node('form'),label=node('label'),input=node('input'),search=node('button',t('search'));
    input.type='search';input.maxLength=120;input.autocomplete='off';input.placeholder=t('search');input.setAttribute('aria-label',t('search'));
    input.setAttribute('data-lpignore','true');input.setAttribute('data-1p-ignore','true');
    search.type='submit';label.append(input);form.append(label,search);
    const choices=node('select');choices.setAttribute('aria-label',t('choose'));
    const actions=node('div');actions.className='operator-dialog-company-actions';
    const save=button(t('save'),()=>void mutate(choices.value));
    save.className='primary';
    const cancel=button(t('cancel'),()=>close(true)),remove=button(t('remove'),()=>{
      if(current(s)&&s.binding?.company_id&&window.confirm(t('confirm')))void mutate(null);
    });
    remove.hidden=!s.binding.company_id;
    actions.append(save,remove,cancel);panel.append(form,choices,status,actions);s.section.append(panel);
    s.panel=panel;s.edit.setAttribute('aria-expanded','true');s.edit.setAttribute('aria-controls',panel.id);
    let writing=false,serial=0,searchRequest;
    s.abortSearch=()=>searchRequest?.abort();
    const active=()=>current(s)&&s.panel===panel&&panel.isConnected;
    function close(restore){
      searchRequest?.abort();serial++;panel.remove();
      if(s.panel===panel){s.panel=null;s.closeLocal=null;s.edit.setAttribute('aria-expanded','false');s.edit.removeAttribute('aria-controls');}
      if(restore&&current(s)&&!s.edit.hidden)s.edit.focus({preventScroll:true});
    }
    s.closeLocal=close;
    function resetChoices(){choices.replaceChildren(new Option(t('choose'),''));choices.disabled=true;save.disabled=true;}
    async function mutate(companyId){
      if(!active()||writing||s.binding?.can_manage!==true||companyId==='')return;
      writing=true;s.writing=true;searchRequest?.abort();s.read?.abort();s.read=null;serial++;status.textContent=t('loading');
      for(const ctrl of [input,search,save,remove,cancel,choices])ctrl.disabled=true;
      try{
        const payload=await request(endpoint,s.member,{...s.route,company_id:companyId},true);
        if(!current(s))return;
        paint(s,payload.binding);writing=false;close(active());
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
    input.focus({preventScroll:true});
  }
  function openOperator(s){
    if(!current(s)||s.binding?.can_manage!==true||s.writing)return;
    if(s.panel?.isConnected){s.closeLocal?.(true);return;}
    if(s.binding.company_id||s.binding.can_search_operator!==true){openLocal(s);return;}
    if(s.operator?.isConnected){if(!s.operatorBusy)s.closeOperator?.(true);return;}
    const picker=node('div'),heading=node('h2'),icon=node('span','⌕'),input=node('input'),results=node('div'),feedback=node('p');
    picker.id='operatorDialogueAccountPicker';picker.setAttribute('role','group');
    picker.setAttribute('aria-labelledby','operatorDialogueAccountTitle');
    heading.id='operatorDialogueAccountTitle';icon.setAttribute('aria-hidden','true');
    heading.append(icon,document.createTextNode(copy('title')));
    input.type='search';input.placeholder=t('search');input.setAttribute('aria-label',t('search'));
    input.maxLength=120;input.autocomplete='off';input.setAttribute('data-lpignore','true');input.setAttribute('data-1p-ignore','true');
    results.dataset.operatorAccountResults='';feedback.setAttribute('role','status');feedback.textContent=copy('hint');
    const local=button(copy('local'),()=>{if(!busy&&active()){close(false);openLocal(s);}});
    local.dataset.localCompanyPicker='';local.className='outline';
    picker.append(heading,input,feedback,results,local);s.section.append(picker);
    s.operator=picker;s.edit.setAttribute('aria-expanded','true');s.edit.setAttribute('aria-controls',picker.id);
    let busy=false,attempt=0,pending=null,searchController=null,composing=false;
    const active=()=>current(s)&&s.operator===picker&&picker.isConnected;
    const cancelSearch=()=>{attempt++;clearTimeout(pending);pending=null;searchController?.abort();};
    function close(restore){
      cancelSearch();picker.remove();
      if(s.operator===picker){s.operator=null;s.operatorBusy=false;s.closeOperator=null;s.edit.setAttribute('aria-expanded','false');s.edit.removeAttribute('aria-controls');}
      if(restore&&current(s)&&!s.edit.hidden)s.edit.focus({preventScroll:true});
    }
    s.closeOperator=close;
    picker.addEventListener('keydown',event=>{
      if(event.key==='Enter'&&event.target===input)event.preventDefault();
    });
    const setBusy=value=>{
      busy=value;s.operatorBusy=value;input.disabled=value;local.disabled=value;
      for(const b of results.querySelectorAll('button'))b.disabled=value;
    };
    async function choose(account,seq){
      if(!active()||busy||seq!==attempt||s.binding?.can_manage!==true||s.binding.can_search_operator!==true||s.binding.company_id)return;
      setBusy(true);s.writing=true;s.read?.abort();s.read=null;feedback.textContent=t('loading');
      try{
        // Account result click is an explicit mutation; never auto-submit it.
        const payload=await request(endpoint,s.member,{...s.route,operator_account:account},true);
        if(!current(s))return;
        if(!payload.binding?.company_id)throw Error(copy('bindError'));
        const restore=active();paint(s,payload.binding);close(restore);
        if(current(s))void s.onChanged?.();
      }catch{if(active())feedback.textContent=copy('bindError');}
      finally{s.writing=false;if(active())setBusy(false);}
    }
    function schedule(){
      if(!active()||busy||s.binding?.can_search_operator!==true)return;
      cancelSearch();results.replaceChildren();
      if(composing){feedback.textContent='';return;}
      const query=input.value.trim(),seq=attempt;
      if([...query].length<4){feedback.textContent=copy('hint');return;}
      feedback.textContent=t('loading');
      pending=setTimeout(async()=>{
        pending=null;if(!active()||seq!==attempt||s.binding?.can_search_operator!==true)return;
        searchController=new AbortController();
        try{
          const payload=await request(endpoint,s.member,{...s.route,operator_search:query},true,searchController.signal);
          if(!active()||seq!==attempt)return;
          const accounts=[...new Set((Array.isArray(payload.accounts)?payload.accounts:[]).slice(0,5)
            .map(row=>row?.account).filter(value=>typeof value==='string'&&value.trim()&&value.length<=120))];
          for(const account of accounts){
            const result=button('',()=>void choose(account,seq));
            result.append(node('strong',account.startsWith('@')?account:'@'+account));results.append(result);
          }
          feedback.textContent=accounts.length?'':copy('empty');
        }catch{if(active()&&seq===attempt)feedback.textContent=copy('error');}
      },400);
    }
    input.oninput=schedule;
    input.oncompositionstart=()=>{composing=true;schedule();};
    input.oncompositionend=()=>{composing=false;schedule();};
    input.focus({preventScroll:true});
  }
  function mount({host,nameHost,route,member,isCurrent,onChanged}){
    if(!host?.isConnected||!route?.provider||!route?.lead_id
        ||typeof route.conversation_id!=='string'||typeof route.channel_id!=='string'
        ||(route.provider==='connect'&&(!route.channel_id||!route.conversation_id))
        ||!member?.tenant_id||member?.telegram_id==null||!isCurrent()){clear();return;}
    const exact={provider:String(route.provider),lead_id:String(route.lead_id),
      channel_id:route.channel_id,conversation_id:route.conversation_id};
    const key=JSON.stringify([String(member.tenant_id),String(member.telegram_id),exact]);
    // The authorized route owns these controls across same-route renders.
    if(state?.key===key){
      state.isCurrent=isCurrent;state.onChanged=onChanged;state.member=member;
      if(state.section.parentElement!==host)host.append(state.section);
      if(headerHosts.get(state.section)!==nameHost){
        const verified=state.headerVerified;
        paintHeader(state,false);
        headerHosts.set(state.section,nameHost);
        paintHeader(state,verified);
      }
      return;
    }
    clear();
    const section=node('div'),value=node('span',t('loading'));
    headerHosts.set(section,nameHost);
    section.dataset.operatorDialogCompany='';
    value.dataset.companyValue='';value.setAttribute('role','status');value.setAttribute('aria-live','polite');
    const s={section,value,member,isCurrent,onChanged,route:exact,key,read:null};
    s.edit=button(t('edit'),()=>openOperator(s));s.edit.hidden=true;s.edit.className='outline';s.edit.setAttribute('aria-expanded','false');
    section.append(value,s.edit);host.append(section);state=s;
    refresh(s);
  }
  const style=node('style');
  style.textContent=`
    [data-operator-dialog-company]{display:grid;gap:7px;min-width:0;color:var(--ink)}
    [data-operator-dialog-company]>button{justify-self:start}
    [data-operator-dialog-company]>button[hidden],#operatorDialogueCompanyPicker [hidden]{display:none!important}
    #operatorDialogueCompanyPicker,#operatorDialogueAccountPicker{display:grid;gap:7px;min-width:0;padding:9px;border:1px solid var(--line);border-radius:8px;background:var(--card)}
    #operatorDialogueCompanyPicker .operator-dialog-company-actions{display:flex;flex-wrap:wrap;gap:6px}
    #operatorDialogueAccountPicker h2{display:flex;align-items:center;gap:6px;margin:0;font-size:.82rem;font-weight:600}
    #operatorDialogueAccountPicker h2 span{color:var(--brand);font-size:18px}
    #operatorDialogueCompanyPicker [role=status],#operatorDialogueAccountPicker [role=status]{margin:0;color:var(--muted);font-size:12px}
    #operatorDialogueCompanyPicker [role=status]:empty,#operatorDialogueAccountPicker [role=status]:empty,#operatorDialogueAccountPicker [data-operator-account-results]:empty{display:none}
    #operatorDialogueAccountPicker [data-operator-account-results]{display:grid;gap:4px;max-height:176px;overflow:auto;overscroll-behavior:contain}
    #operatorDialogueAccountPicker [data-operator-account-results] button{width:100%;min-height:36px;text-align:start;color:inherit;background:var(--paper,var(--card))}
    #operatorDialogueAccountPicker [data-local-company-picker]{justify-self:start;font-size:.8rem}
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
  window.tenantDialogueCompany={mount,clear,closePicker(){state?.closeLocal?.(false);state?.closeOperator?.(false);}};
})();
