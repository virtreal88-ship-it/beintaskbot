/* Local CRM company binding for the exact authorized dialogue route. */
(()=>{
  'use strict';
  const endpoint='/api/platform/operator/dialog-company';
  const labels={
    ru:{label:'Компания',unknown:'Компания не выбрана',unavailable:'Недоступна',loading:'Загрузка…',search:'Найти компанию',remove:'Снять связь',confirm:'Снять привязку компании с этого диалога?',empty:'В CRM нет совпадений.',error:'Не удалось получить или изменить компанию.'},
    az:{label:'Şirkət',unknown:'Şirkət seçilməyib',unavailable:'Əlçatan deyil',loading:'Yüklənir…',search:'Şirkət axtar',remove:'Əlaqəni sil',confirm:'Bu dialoqun şirkətlə əlaqəsini silmək istəyirsiniz?',empty:'CRM-də uyğun şirkət tapılmadı.',error:'Şirkət məlumatını almaq və ya dəyişmək mümkün olmadı.'},
    en:{label:'Company',unknown:'No company selected',unavailable:'Unavailable',loading:'Loading…',search:'Find a company',remove:'Remove company link',confirm:'Remove the company link from this dialogue?',empty:'No matching CRM companies.',error:'Could not get or change the company.'}
  };
  const operatorLabels={
    ru:{empty:'Аккаунты Operator не найдены.',error:'Не удалось найти аккаунты Operator.',bindError:'Не удалось привязать аккаунт Operator.',},
    az:{empty:'Operator hesabı tapılmadı.',error:'Operator hesablarını tapmaq mümkün olmadı.',bindError:'Operator hesabını bağlamaq mümkün olmadı.',},
    en:{empty:'No Operator accounts found.',error:'Could not find Operator accounts.',bindError:'Could not link the Operator account.',}
  };
  const t=key=>(labels[window.tenantI18n?.language]||labels.az)[key];
  const copy=key=>(operatorLabels[window.tenantI18n?.language]||operatorLabels.az)[key];
  const node=(tag,txt)=>{const e=document.createElement(tag);if(txt!=null)e.textContent=txt;return e;};
  const button=(txt,fn)=>{const b=node('button',txt);b.type='button';b.onclick=fn;return b;};
  let state=null;
  const headerHosts=new WeakMap();
  const current=s=>s===state&&s.section.isConnected&&s.isCurrent();
  const companyName=binding=>binding?.company_id&&typeof binding.company_name==='string'?binding.company_name:'';
  function paintHeader(s){
    const el=headerHosts.get(s.section);
    if(!el?.isConnected)return;
    const name=current(s)?companyName(s.binding):'';
    el.textContent=name;el.hidden=!name;
    if(name)el.title=name;else el.removeAttribute('title');
  }
  function clear(){
    const old=state;state=null;
    if(!old)return;
    paintHeader(old);old.read?.abort();old.cancelSearch?.();old.section.remove();
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
    const same=s.binding?.company_id===binding?.company_id;
    s.binding=binding;
    paintHeader(s);
    const name=companyName(binding);
    s.value.textContent=name||t(binding?'unknown':'unavailable');
    s.value.title=s.value.textContent;
    if(!same||binding?.can_manage!==true||!s.editing)s.reset();
    s.sync();
  }
  function refresh(s=state){
    if(!s||!current(s)||s.read||s.writing)return;
    const read=new AbortController();s.read=read;
    void request(endpoint,s.member,s.route,false,read.signal)
      .then(payload=>{if(s.read===read)paint(s,payload.binding);},error=>{
        if(s.read===read&&(!s.binding||[401,403,404,409].includes(error.status)))paint(s,null);
      }).finally(()=>{if(s.read===read)s.read=null;});
  }
  async function changeBinding(s,fields){
    if(!current(s)||s.writing||s.binding?.can_manage!==true||
        (fields.operator_account&&s.binding.can_search_operator!==true))return;
    const expected_company_id=s.binding.company_id||null;
    let recheck=false;
    s.writing=true;s.read?.abort();s.read=null;s.cancelSearch();s.sync();
    s.status.textContent=t('loading');
    try{
      const {binding}=await request(endpoint,s.member,{...s.route,...fields,expected_company_id},true);
      if(!current(s))return;
      if(!binding||(fields.company_id===null?binding.company_id:!binding.company_id))throw Error(t('error'));
      s.reset();s.status.textContent='';paint(s,binding);
      if(current(s))void s.onChanged?.(binding);
    }catch(error){
      if(current(s)){
        recheck=[401,403,404,409].includes(error.status);
        if(recheck&&expected_company_id)s.reset();
        s.status.textContent=fields.operator_account?copy('bindError'):t('error');
      }
    }finally{
      s.writing=false;
      if(current(s)){s.sync();if(recheck)refresh(s);}
    }
  }
  function mountEditor(s){
    const panel=node('div'),label=node('label',t('label')),field=node('span'),input=node('input'),results=node('div');
    panel.id='operatorDialogueCompanyPicker';
    label.htmlFor=input.id='operatorDialogueCompanySearch';field.className='operator-company-link-row';
    input.type='search';input.maxLength=180;input.autocomplete='off';
    input.placeholder=t('search');input.setAttribute('data-lpignore','true');input.setAttribute('data-1p-ignore','true');
    const unlink=button('×',()=>{
      if(!active()||s.writing||s.binding?.can_manage!==true)return;
      input.value='';schedule();input.focus({preventScroll:true});
    });
    unlink.className='operator-company-unlink';unlink.title=t('remove');unlink.setAttribute('aria-label',t('remove'));
    unlink.onpointerdown=event=>event.preventDefault();
    field.append(input,unlink);label.append(field);panel.append(label);
    results.className='operator-dialog-company-results';
    const operatorRows=node('div'),localRows=node('div'),operatorStatus=node('p'),localStatus=node('p');
    operatorRows.dataset.operatorAccountResults='';localRows.dataset.localCompanyResults='';
    operatorStatus.setAttribute('role','status');localStatus.setAttribute('role','status');
    results.append(operatorRows,operatorStatus,localRows,localStatus);
    const suggestions=node('div');suggestions.className='operator-company-suggestions';suggestions.hidden=true;
    suggestions.id='operatorDialogueCompanySuggestions';suggestions.append(results);panel.append(suggestions);
    panel.addEventListener('focusout',event=>{
      if(panel.contains(event.relatedTarget)||!active()||s.writing||!s.editing)return;
      if(!input.value.trim()&&s.binding?.company_id&&window.confirm(t('confirm'))){
        void changeBinding(s,{company_id:null});
      }else{s.reset();s.sync();}
    });
    s.section.append(panel);
    let serial=0,timer=null,search=null,composing=false;
    const active=()=>current(s)&&panel.isConnected;
    s.cancelSearch=()=>{serial++;clearTimeout(timer);timer=null;search?.abort();search=null;};
    const clearResults=()=>{
      s.cancelSearch();operatorRows.replaceChildren();localRows.replaceChildren();
      operatorStatus.textContent='';localStatus.textContent='';
    };
    s.reset=()=>{
      clearResults();suggestions.hidden=true;
      s.editing=false;input.value=companyName(s.binding);
    };
    s.sync=()=>{
      const allowed=s.binding?.can_manage===true;
      input.readOnly=!allowed||s.writing;
      unlink.hidden=!allowed||!s.binding.company_id||!input.value;
      unlink.disabled=s.writing;
      for(const result of results.querySelectorAll('button'))result.disabled=s.writing;
    };
    function render(rows,items){
      rows.replaceChildren();
      for(const [label,fields] of items){
        const result=button('',()=>{if(active()&&result.isConnected)void changeBinding(s,fields);});
        result.onpointerdown=event=>event.preventDefault();
        result.append(node('strong',label));rows.append(result);
      }
    }
    async function find(term,seq){
      if(!active()||seq!==serial||s.writing)return;
      const query=term.slice(0,120);
      search=new AbortController();const {signal}=search;
      const operatorSearch=s.binding?.can_search_operator===true&&[...query].length>=4;
      localStatus.textContent=t('loading');operatorStatus.textContent=operatorSearch?t('loading'):'';
      void request('/api/platform/crm/native/companies',s.member,{search:query},false,signal)
        .then(payload=>{
          if(!active()||seq!==serial)return;
          const choices=new Map();
          for(const row of payload.companies||[]){
            if(row.actions?.update===true&&row.id&&row.name)choices.set(String(row.id),String(row.name));
          }
          const items=[...choices].slice(0,5).map(([id,name])=>[name,{company_id:id}]);
          render(localRows,items);localStatus.textContent=items.length?'':t('empty');
        },()=>{if(active()&&seq===serial)localStatus.textContent=t('error');});
      if(!operatorSearch)return;
      try{
        const payload=await request(endpoint,s.member,{...s.route,operator_search:query},true,signal);
        if(!active()||seq!==serial)return;
        const accounts=[...new Set((Array.isArray(payload.accounts)?payload.accounts:[])
          .map(row=>row?.account).filter(value=>typeof value==='string'&&value.trim()&&value.length<=120))].slice(0,5);
        render(operatorRows,accounts.map(account=>[account.startsWith('@')?account:'@'+account,{operator_account:account}]));
        operatorStatus.textContent=accounts.length?'':copy('empty');
      }catch{if(active()&&seq===serial)operatorStatus.textContent=copy('error');}
    }
    function schedule(){
      if(!active()||s.writing||input.readOnly)return;
      clearResults();s.status.textContent='';
      const query=input.value.trim();
      s.editing=input.value!==companyName(s.binding);
      suggestions.hidden=!query||composing||!s.editing;
      s.sync();
      if(!suggestions.hidden){
        const seq=serial;
        timer=setTimeout(()=>void find(query,seq),300);
      }
    }
    input.oninput=schedule;
    input.oncompositionstart=()=>{composing=true;schedule();};
    input.oncompositionend=()=>{composing=false;schedule();};
    input.onkeydown=event=>{
      if(event.key==='ArrowDown'&&results.querySelector('button')){
        event.preventDefault();results.querySelector('button').focus();
      }else if(event.key==='Enter')event.preventDefault();
    };
    s.sync();
  }
  function mount({host,nameHost,route,member,isCurrent,onChanged}){
    if(!host?.isConnected||!route?.provider||!route?.lead_id
        ||typeof route.conversation_id!=='string'||typeof route.channel_id!=='string'
        ||(route.provider==='connect'&&(!route.channel_id||!route.conversation_id))
        ||!member?.tenant_id||member?.telegram_id==null||!isCurrent()){clear();return;}
    const exact={provider:String(route.provider),lead_id:String(route.lead_id),
      channel_id:route.channel_id,conversation_id:route.conversation_id};
    const key=JSON.stringify([String(member.tenant_id),String(member.telegram_id),exact]);
    const snapshot=route.company_binding;
    if(state?.key===key){
      const s=state;
      s.isCurrent=isCurrent;s.onChanged=onChanged;s.member=member;
      if(s.section.parentElement!==host)host.append(s.section);
      headerHosts.set(s.section,nameHost);
      if(snapshot!==undefined&&snapshot!==s.snapshot&&!s.writing){
        s.snapshot=snapshot;s.read?.abort();s.read=null;paint(s,snapshot);
      }else paintHeader(s);
      return;
    }
    clear();
    const section=node('div'),value=node('span',t('loading')),status=node('p');
    headerHosts.set(section,nameHost);
    section.dataset.operatorDialogCompany='';
    value.dataset.companyValue='';value.setAttribute('role','status');value.setAttribute('aria-live','polite');
    status.className='operator-company-link-status';status.setAttribute('role','status');
    const s={section,value,status,member,isCurrent,onChanged,route:exact,key,snapshot,read:null};
    section.append(value,status);host.append(section);state=s;mountEditor(s);
    if(snapshot!==undefined)paint(s,snapshot);else refresh(s);
  }
  const style=node('style');
  style.textContent=`
    [data-operator-dialog-company]{display:grid;gap:7px;min-width:0;color:var(--ink)}
    #operatorDialogueCompanyPicker [hidden]{display:none!important}
    #operatorDialogueCompanyPicker{display:grid;gap:5px;min-width:0}
    #operatorDialogueCompanyPicker .operator-company-suggestions{display:grid;gap:6px;min-width:0;padding:4px;border:1px solid var(--line);border-radius:8px;background:var(--card)}
    #operatorDialogueCompanyPicker [role=status]{margin:0;color:var(--muted);font-size:.75rem}
    #operatorDialogueCompanyPicker [role=status]:empty,#operatorDialogueCompanyPicker .operator-dialog-company-results>div:empty{display:none}
    #operatorDialogueCompanyPicker .operator-dialog-company-results{display:grid;gap:4px;max-height:clamp(112px,35dvh,205px);overflow:auto;overscroll-behavior:auto}
    #operatorClientCard #operatorDialogueCompanyPicker .operator-dialog-company-results button{display:block;width:100%;min-height:36px;text-align:start;border:0;color:inherit;background:var(--card)}
    #operatorClientCard #operatorDialogueCompanyPicker .operator-dialog-company-results button:hover{background:var(--paper)}
    .operator-company-link-row{position:relative;display:block;min-width:0}
    [data-operator-dialog-company] [data-company-value]{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
    #operatorClientCard #operatorDialogueCompanySearch{padding-inline-end:32px}
    #operatorClientCard #operatorDialogueCompanySearch::-webkit-search-cancel-button{display:none}
    #operatorClientCard .operator-company-unlink{display:inline-grid;position:absolute;inset-inline-end:3px;top:50%;transform:translateY(-50%);place-items:center;width:26px;min-height:26px;padding:0;border:0;background:transparent;color:var(--muted);font-size:18px}
    #operatorClientCard .operator-company-unlink[hidden],.operator-company-link-status:empty{display:none}
    .operator-company-link-status{margin:0;color:var(--muted);font-size:.75rem}
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
  document.addEventListener('tenant-dialog-reauthorized',()=>{if(state?.snapshot===undefined)refresh();});
  window.tenantDialogueCompany={mount,clear,refresh,closePicker(){state?.reset?.();state?.sync?.();}};
})();
