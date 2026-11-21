/* Local CRM company binding for the exact authorized dialogue route. */
(()=>{
  'use strict';
  const endpoint='/api/platform/operator/dialog-company';
  const labels={
    ru:{label:'Компания',unknown:'Компания не выбрана',unavailable:'Недоступна',loading:'Загрузка…',chooseCompany:'Выбрать компанию',edit:'Изменить компанию',search:'Найти компанию',remove:'Снять связь',confirm:'Снять привязку компании с этого диалога?',empty:'Компании с правом привязки не найдены.',error:'Не удалось получить или изменить компанию.'},
    az:{label:'Şirkət',unknown:'Şirkət seçilməyib',unavailable:'Əlçatan deyil',loading:'Yüklənir…',chooseCompany:'Şirkət seç',edit:'Şirkəti dəyiş',search:'Şirkət axtar',remove:'Əlaqəni sil',confirm:'Bu dialoqun şirkətlə əlaqəsini silmək istəyirsiniz?',empty:'Əlaqələndirə biləcəyiniz şirkət tapılmadı.',error:'Şirkət məlumatını almaq və ya dəyişmək mümkün olmadı.'},
    en:{label:'Company',unknown:'No company selected',unavailable:'Unavailable',loading:'Loading…',chooseCompany:'Choose a company',edit:'Change company',search:'Find a company',remove:'Remove company link',confirm:'Remove the company link from this dialogue?',empty:'No linkable companies found.',error:'Could not get or change the company.'}
  };
  const operatorLabels={
    ru:{hint:'Для поиска в Operator введите от 4 символов.',empty:'Аккаунты Operator не найдены.',error:'Не удалось найти аккаунты Operator.',bindError:'Не удалось привязать аккаунт Operator.',conflict:'Не удалось привязать аккаунт Operator. Проверьте текущую привязку.'},
    az:{hint:'Operator üçün ən azı 4 simvol yazın.',empty:'Operator hesabı tapılmadı.',error:'Operator hesablarını tapmaq mümkün olmadı.',bindError:'Operator hesabını bağlamaq mümkün olmadı.',conflict:'Operator hesabını bağlamaq mümkün olmadı. Mövcud əlaqəni yoxlayın.'},
    en:{hint:'Type at least 4 characters to search Operator.',empty:'No Operator accounts found.',error:'Could not find Operator accounts.',bindError:'Could not link the Operator account.',conflict:'Could not link the Operator account. Check the current company link.'}
  };
  const t=key=>(labels[window.tenantI18n?.language]||labels.az)[key];
  const copy=key=>(operatorLabels[window.tenantI18n?.language]||operatorLabels.az)[key];
  const node=(tag,txt)=>{const e=document.createElement(tag);if(txt!=null)e.textContent=txt;return e;};
  const button=(txt,fn)=>{const b=node('button',txt);b.type='button';b.onclick=fn;return b;};
  let state=null;
  const current=s=>s===state&&s.section.isConnected&&s.isCurrent();
  function clear(){
    const old=state;state=null;
    old?.read?.abort();
    old?.closePicker?.(false);
    old?.section.remove();
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
    s.edit.textContent=t(binding?.company_id?'edit':'chooseCompany');
    s.edit.hidden=binding?.can_manage!==true;
    if(!binding?.can_manage||s.pickerOffersOperator!==(binding.can_search_operator===true))s.closePicker?.(false);
  }
  function refresh(s=state){
    if(!s||!current(s)||s.read||s.writing)return;
    const read=new AbortController();s.read=read;
    void request(endpoint,s.member,s.route,false,read.signal)
      .then(payload=>{if(s.read===read)paint(s,payload.binding);},error=>{
        if(s.read===read&&(!s.binding||[401,403,404,409].includes(error.status)))paint(s,null);
      }).finally(()=>{if(s.read===read)s.read=null;});
  }
  function openPicker(s){
    if(!current(s)||s.binding?.can_manage!==true||s.writing)return;
    if(s.panel){s.closePicker(true);return;}

    const showOperator=s.binding.can_search_operator===true;
    const panel=node('div'),input=node('input'),status=node('p'),results=node('div');
    panel.id='operatorDialogueCompanyPicker';panel.setAttribute('role','group');
    panel.setAttribute('aria-label',t('search'));
    input.id='operatorDialogueCompanySearch';input.type='search';input.maxLength=120;
    input.autocomplete='off';input.placeholder=t('search');input.setAttribute('aria-label',t('search'));
    input.setAttribute('data-lpignore','true');input.setAttribute('data-1p-ignore','true');
    status.setAttribute('role','status');results.className='operator-dialog-company-results';
    const operatorRows=node('div'),localRows=node('div'),operatorStatus=node('p'),localStatus=node('p');
    operatorRows.dataset.operatorAccountResults='';localRows.dataset.localCompanyResults='';
    operatorStatus.setAttribute('role','status');localStatus.setAttribute('role','status');
    results.append(operatorRows,operatorStatus,localRows,localStatus);

    const remove=button(t('remove'),()=>{
      if(active()&&s.binding?.company_id&&window.confirm(t('confirm')))void select({company_id:null});
    });
    remove.hidden=!s.binding.company_id;remove.className='outline operator-company-unlink';
    panel.append(input,status,results,remove);s.section.append(panel);
    s.panel=panel;s.pickerOffersOperator=showOperator;
    s.edit.setAttribute('aria-expanded','true');s.edit.setAttribute('aria-controls',panel.id);

    let serial=0,timer=null,search=null,composing=false;
    const active=()=>current(s)&&s.panel===panel&&panel.isConnected;
    function cancelSearch(){
      serial++;
      clearTimeout(timer);timer=null;
      search?.abort();search=null;
    }
    function close(restore){
      cancelSearch();panel.remove();
      if(s.panel===panel){
        s.panel=null;s.closePicker=null;
        s.edit.setAttribute('aria-expanded','false');s.edit.removeAttribute('aria-controls');
      }
      if(restore&&current(s)&&!s.edit.hidden)s.edit.focus({preventScroll:true});
    }
    s.closePicker=close;

    function setDisabled(value){
      input.disabled=value;remove.disabled=value;
      for(const result of results.querySelectorAll('button'))result.disabled=value;
    }
    async function select(fields){
      if(!active()||s.writing||s.binding?.can_manage!==true||
          (fields.operator_account&&s.binding?.can_search_operator!==true))return;
      s.writing=true;cancelSearch();s.read?.abort();s.read=null;
      setDisabled(true);status.textContent=t('loading');
      try{
        const payload=await request(endpoint,s.member,{...s.route,...fields},true);
        if(!current(s))return;
        if(fields.operator_account&&!payload.binding?.company_id)throw Error(t('error'));
        const restore=active();paint(s,payload.binding);close(restore);
        if(current(s))void s.onChanged?.();
      }catch(error){
        if(active()){
          status.textContent=fields.operator_account
            ? copy(error.status===409&&s.binding.company_id?'conflict':'bindError') : t('error');
        }
      }finally{
        s.writing=false;
        if(active())setDisabled(false);
        else if(s.panel&&current(s))refresh(s);
      }
    }
    function render(rows,items,source){
      rows.replaceChildren();
      if(items.length&&showOperator){
        const label=node('p',source);label.className='operator-company-source';rows.append(label);
      }
      for(const [label,fields] of items){
        const result=button('',()=>{if(active()&&result.isConnected)void select(fields);});
        result.append(node('strong',label));rows.append(result);
      }
    }
    async function find(query,seq){
      if(!active()||seq!==serial||s.writing)return;
      search=new AbortController();
      const {signal}=search;
      const operatorSearch=showOperator&&[...query].length>=4;
      localStatus.textContent=t('loading');
      operatorStatus.textContent=operatorSearch?t('loading'):showOperator?copy('hint'):'';

      void request('/api/platform/crm/native/companies',s.member,{search:query},false,signal)
        .then(payload=>{
          if(!active()||seq!==serial)return;
          const choices=new Map();
          for(const row of payload.companies||[]){
            if(row.actions?.update===true&&row.id&&row.name)choices.set(String(row.id),String(row.name));
          }
          const items=[...choices].slice(0,query?5:3).map(([id,name])=>[name,{company_id:id}]);
          render(localRows,items,'CRM');
          localStatus.textContent=items.length?'':t('empty');
        },()=>{
          if(active()&&seq===serial)localStatus.textContent=t('error');
        });

      if(!operatorSearch)return;
      try{
        const payload=await request(endpoint,s.member,{...s.route,operator_search:query},true,signal);
        if(!active()||seq!==serial)return;
        const accounts=[...new Set((Array.isArray(payload.accounts)?payload.accounts:[])
          .map(row=>row?.account).filter(value=>typeof value==='string'&&value.trim()&&value.length<=120))].slice(0,5);
        const items=accounts.map(account=>[account.startsWith('@')?account:'@'+account,{operator_account:account}]);
        render(operatorRows,items,'Operator');
        operatorStatus.textContent=items.length?'':copy('empty');
      }catch{
        if(active()&&seq===serial)operatorStatus.textContent=copy('error');
      }
    }
    function schedule(){
      if(!active()||s.writing)return;
      cancelSearch();operatorRows.replaceChildren();localRows.replaceChildren();
      status.textContent='';operatorStatus.textContent='';localStatus.textContent='';
      if(!composing){
        const query=input.value.trim(),seq=serial;
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
    input.focus({preventScroll:true});
    void find('',serial);
  }
  function mount({host,route,member,isCurrent,onChanged}){
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
      return;
    }
    clear();
    const section=node('div'),value=node('span',t('loading'));
    section.dataset.operatorDialogCompany='';
    value.dataset.companyValue='';value.setAttribute('role','status');value.setAttribute('aria-live','polite');
    const s={section,value,member,isCurrent,onChanged,route:exact,key,read:null};
    s.edit=button(t('edit'),()=>openPicker(s));s.edit.hidden=true;s.edit.className='outline';s.edit.setAttribute('aria-expanded','false');
    section.append(value,s.edit);host.append(section);state=s;
    refresh(s);
  }
  const style=node('style');
  style.textContent=`
    [data-operator-dialog-company]{display:grid;gap:7px;min-width:0;color:var(--ink)}
    [data-operator-dialog-company]>button{justify-self:start}
    [data-operator-dialog-company]>button[hidden],#operatorDialogueCompanyPicker [hidden]{display:none!important}
    #operatorDialogueCompanyPicker{display:grid;gap:7px;min-width:0;padding:8px;border:1px solid var(--line);border-radius:9px;background:var(--card)}
    #operatorDialogueCompanyPicker [role=status]{margin:0;color:var(--muted);font-size:.75rem}
    #operatorDialogueCompanyPicker [role=status]:empty,#operatorDialogueCompanyPicker .operator-dialog-company-results>div:empty{display:none}
    #operatorDialogueCompanyPicker .operator-dialog-company-results{display:grid;gap:4px;max-height:clamp(112px,35dvh,205px);overflow:auto;overscroll-behavior:auto}
    #operatorDialogueCompanyPicker .operator-company-source{margin:4px 8px;color:var(--muted);font-size:.72rem}
    #operatorClientCard #operatorDialogueCompanyPicker .operator-dialog-company-results button{display:block;width:100%;min-height:36px;text-align:start;border:0;color:inherit;background:var(--card)}
    #operatorClientCard #operatorDialogueCompanyPicker .operator-dialog-company-results button:hover{background:var(--paper)}
    #operatorDialogueCompanyPicker .operator-company-unlink{justify-self:start;font-size:.8rem}
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
  window.tenantDialogueCompany={mount,clear,closePicker(){state?.closePicker?.(false);}};
})();
