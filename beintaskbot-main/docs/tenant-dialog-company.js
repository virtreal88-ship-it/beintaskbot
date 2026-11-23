/* Local CRM company binding for the exact authorized dialogue route. */
(()=>{
  'use strict';
  const endpoint='/api/platform/operator/dialog-company';
  const publicLinkEndpoint=endpoint+'/public-link';
  const labels={
    ru:{label:'Компания',unknown:'Компания не выбрана',unavailable:'Недоступна',loading:'Загрузка…',search:'Найти компанию',restricted:'Нет доступа к выбору компании',remove:'Снять связь',confirm:'Снять привязку компании с этого диалога?',empty:'В CRM нет совпадений.',error:'Не удалось получить или изменить компанию.'},
    az:{label:'Şirkət',unknown:'Şirkət seçilməyib',unavailable:'Əlçatan deyil',loading:'Yüklənir…',search:'Şirkət axtar',restricted:'Şirkət seçmək üçün icazə yoxdur',remove:'Əlaqəni sil',confirm:'Bu dialoqun şirkətlə əlaqəsini silmək istəyirsiniz?',empty:'CRM-də uyğun şirkət tapılmadı.',error:'Şirkət məlumatını almaq və ya dəyişmək mümkün olmadı.'},
    en:{label:'Company',unknown:'No company selected',unavailable:'Unavailable',loading:'Loading…',search:'Find a company',restricted:'Company selection requires access',remove:'Remove company link',confirm:'Remove the company link from this dialogue?',empty:'No matching CRM companies.',error:'Could not get or change the company.'}
  };
  const operatorLabels={
    ru:{empty:'Аккаунты Operator не найдены.',error:'Не удалось найти аккаунты Operator.',bindError:'Не удалось привязать аккаунт Operator.',},
    az:{empty:'Operator hesabı tapılmadı.',error:'Operator hesablarını tapmaq mümkün olmadı.',bindError:'Operator hesabını bağlamaq mümkün olmadı.',},
    en:{empty:'No Operator accounts found.',error:'Could not find Operator accounts.',bindError:'Could not link the Operator account.',}
  };
  const linkLabels={
    ru:{label:'Ссылка для клиента',copy:'Скопировать ссылку',copied:'Ссылка скопирована',error:'Не удалось получить ссылку. Повторите попытку.',copyError:'Браузер не скопировал ссылку. Повторите попытку.',unsupported:'Копирование недоступно в этом браузере.'},
    az:{label:'Müştəri linki',copy:'Linki kopyala',copied:'Link kopyalandı',error:'Linki almaq mümkün olmadı. Yenidən cəhd edin.',copyError:'Brauzer linki kopyalamadı. Yenidən cəhd edin.',unsupported:'Bu brauzerdə kopyalama əlçatan deyil.'},
    en:{label:'Customer link',copy:'Copy link',copied:'Link copied',error:'Could not retrieve the link. Try again.',copyError:'The browser could not copy the link. Try again.',unsupported:'Copying is unavailable in this browser.'}
  };
  const profileLabels={
    ru:{phone:'Телефон',balance:'Баланс компании',unavailable:'Данные Operator недоступны',empty:'Нет данных',denied:'Нет доступа к данным компании',error:'Ошибка запроса к Operator'},
    az:{phone:'Telefon',balance:'Şirkət balansı',unavailable:'Operator məlumatları əlçatan deyil',empty:'Məlumat yoxdur',denied:'Şirkət məlumatlarına giriş icazəsi yoxdur',error:'Operator sorğusu uğursuz oldu'},
    en:{phone:'Phone',balance:'Company balance',unavailable:'Operator details are unavailable',empty:'No data',denied:'Access to company details denied',error:'Operator request failed'}
  };
  const profileText=key=>(profileLabels[window.tenantI18n?.language]||profileLabels.az)[key];
  const linkProblems={
    ru:{not_found:'Operator не вернул существующую ссылку.',not_connected:'Operator не подключён.',not_linked:'У компании нет подтверждённой привязки к аккаунту Operator.',rejected:'Operator отклонил запрос ссылки.',denied:'У вас нет доступа к ссылке этой компании.'},
    az:{not_found:'Operator mövcud linki qaytarmadı.',not_connected:'Operator qoşulmayıb.',not_linked:'Şirkət təsdiqlənmiş Operator hesabı ilə əlaqələndirilməyib.',rejected:'Operator link sorğusunu rədd etdi.',denied:'Bu şirkətin linkinə giriş icazəniz yoxdur.'},
    en:{not_found:'Operator did not return an existing link.',not_connected:'Operator is disconnected.',not_linked:'This company has no verified Operator account link.',rejected:'Operator rejected the link request.',denied:'You do not have access to this company’s link.'}
  };
  const linkProblem=key=>(linkProblems[window.tenantI18n?.language]||linkProblems.az)[key];
  const linkText=key=>(linkLabels[window.tenantI18n?.language]||linkLabels.az)[key];
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
    const name=current(s)?(s.binding===undefined?s.initialCompanyName:companyName(s.binding)):'';
    el.textContent=name;el.hidden=!name;
    if(name)el.title=name;else el.removeAttribute('title');
  }
  function clear(){
    const old=state;state=null;
    if(!old)return;
    paintHeader(old);clearPublicLink(old);clearProfile(old);old.read?.abort();old.cancelSearch?.();old.section.remove();
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
  const linkCurrent=(s,company)=>current(s)&&s.section.closest('dialog')?.open===true&&
    !s.writing&&s.binding?.company_id===company;
  function clearPublicLink(s){
    s.linkRequest?.abort();s.linkRequest=null;s.linkCompany=null;
    s.linkBox.hidden=true;s.linkCopy.disabled=false;s.linkStatus.textContent='';
  }
  function syncPublicLink(s,force=false){
    const company=s.binding?.company_id;
    if(force||s.linkCompany!==company||!company||!linkCurrent(s,company))clearPublicLink(s);
    if(company&&linkCurrent(s,company)){s.linkCompany=company;s.linkBox.hidden=false;}
  }
  const sectionNames={
    ru:['Задачи','Модули','История платежей'],az:['Tapşırıqlar','Modullar','Ödəniş tarixçəsi'],
    en:['Tasks','Modules','Payment history']
  };
  function mountOperatorLists(s){
    s.lists=['company_tasks','company_modules','company_history'].map((action,index)=>{
      const box=node('details'),title=node('summary'),body=node('div');
      title.textContent=(sectionNames[window.tenantI18n?.language]||sectionNames.az)[index];
      box.append(title,body);s.profileBox.append(box);
      const list={box,body,read:null};
      box.ontoggle=async()=>{
        list.read?.abort();list.read=null;body.replaceChildren();
        const company=s.binding?.company_id;
        if(!box.open||!company||!linkCurrent(s,company))return;
        const read=new AbortController();list.read=read;
        const active=()=>list.read===read&&box.open&&linkCurrent(s,company);
        body.textContent=t('loading');
        try{
          const {company_section:section}=await request(endpoint,s.member,
            {...s.route,action,expected_company_id:company},true,read.signal);
          if(!active())return;
          if(section?.state!=='available'){
            body.textContent=['not_connected','not_linked'].includes(section?.state)?linkProblem(section.state):profileText('error');return;
          }
          if(!Array.isArray(section.rows))throw Error();
          body.replaceChildren(...section.rows.map(row=>node('p',row.join(' · '))));
          if(!section.rows.length)body.textContent=profileText('empty');
        }catch(error){if(active())body.textContent=profileText([401,403,424].includes(error.status)?'denied':'error');}
      };
      return list;
    });
  }
  function clearProfile(s){
    for(const list of s.lists||[]){list.read?.abort();list.read=null;list.box.open=false;list.body.replaceChildren();}
    s.profileRequest?.abort();s.profileRequest=null;s.profileCompany=null;
    s.profileBox.hidden=true;s.profileStatus.textContent='';
    s.registrationPhone.textContent=s.balance.textContent='—';
  }
  function syncProfile(s){
    const company=s.binding?.company_id;
    if(!company||!linkCurrent(s,company)){clearProfile(s);return;}
    if(s.profileCompany===company)return;
    clearProfile(s);
    s.profileCompany=company;s.profileBox.hidden=false;
    const read=new AbortController();s.profileRequest=read;
    const active=()=>s.profileRequest===read&&linkCurrent(s,company);
    void request(endpoint,s.member,{...s.route,action:'company_profile',expected_company_id:company},true,read.signal)
      .then(({company_profile:profile})=>{
        if(!active())return;
        if(profile?.state!=='available'){
          s.profileStatus.textContent=['not_connected','not_linked'].includes(profile?.state)
            ?linkProblem(profile.state):profileText(profile?.state==='not_found'?'empty':'error');
          return;
        }
        s.registrationPhone.textContent=s.balance.textContent=profileText('empty');
        if(typeof profile.registration_phone==='string')s.registrationPhone.textContent=profile.registration_phone;
        if(Number.isFinite(profile.balance))s.balance.textContent=new Intl.NumberFormat(
          window.tenantI18n?.language||'az',{style:'currency',currency:'AZN'}).format(profile.balance);
      },error=>{if(active())s.profileStatus.textContent=profileText([401,403].includes(error.status)?'denied':'error');})
      .finally(()=>{if(s.profileRequest===read)s.profileRequest=null;});
  }
  function mountProfile(s){
    s.profileBox=node('div');s.profileBox.dataset.companyProfile='';s.profileBox.hidden=true;
    s.profileStatus=node('p');s.profileStatus.setAttribute('role','status');
    const field=(key)=>{
      const row=node('div'),label=node('small',profileText(key)),value=node('span','—');
      row.append(label,value);s.profileBox.append(row);return {label,value};
    };
    const phone=field('phone'),balance=field('balance');
    s.registrationPhone=phone.value;s.balance=balance.value;
    s.labelProfile=()=>{phone.label.textContent=profileText('phone');balance.label.textContent=profileText('balance');};
    s.profileBox.append(s.profileStatus);mountOperatorLists(s);s.section.append(s.profileBox);
  }
  function writePendingClipboard(pending){
    if(typeof window.ClipboardItem!=='function'||typeof navigator.clipboard?.write!=='function')return null;
    const blob=pending.then(url=>new Blob([url],{type:'text/plain'}));
    // Also handle rejection when ClipboardItem/write throws before consuming it.
    void blob.catch(()=>{});
    return navigator.clipboard.write([new window.ClipboardItem({'text/plain':blob})]);
  }
  async function copyPublicLink(s){
    const company=s.binding?.company_id;
    if(!company||!linkCurrent(s,company)||s.linkCopy.disabled)return;
    if(typeof navigator.clipboard?.writeText!=='function'&&
      (typeof window.ClipboardItem!=='function'||typeof navigator.clipboard?.write!=='function')){
      s.linkStatus.textContent=linkText('unsupported');return;
    }
    const read=new AbortController();s.linkRequest=read;s.linkCopy.disabled=true;s.linkStatus.textContent='';
    const active=()=>s.linkRequest===read&&linkCurrent(s,company);
    let received=false;
    const pending=request(publicLinkEndpoint,s.member,
      {...s.route,expected_company_id:company},true,read.signal).then(({public_link:link})=>{
        if(!active())throw Error('stale');
        if(['not_found','not_connected','not_linked'].includes(link?.state))
          throw Object.assign(Error('unavailable'),{status:404,linkState:link.state});
        if(link?.state!=='available'||typeof link.url!=='string'||!/^https:\/\/1000\.az\/[A-Za-z0-9_-]{6}$/.test(link.url))
          throw Error('unconfirmed');
        received=true;
        return link.url;
      });
    // Call write in the original gesture. Safari requires this even when the
    // item data itself cannot exist until the verified Operator reply arrives.
    let gestureWrite=null;
    try{gestureWrite=writePendingClipboard(pending);}catch{}
    const wrote=gestureWrite?.then(()=>true,()=>false);
    try{
      const url=await pending;
      if(!active())return;
      const copied=await wrote;
      if(!active())return;
      if(!copied)await navigator.clipboard.writeText(url);
      if(active())s.linkStatus.textContent=linkText('copied');
    }catch(error){
      if(!active())return;
      const problem=error.linkState||(error.status===424?'rejected':error.status===403?'denied':null);
      s.linkStatus.textContent=linkProblem(problem)||linkText(received?'copyError':'error');
    }finally{
      if(s.linkRequest===read){s.linkRequest=null;if(linkCurrent(s,company))s.linkCopy.disabled=false;}
    }
  }
  function mountPublicLink(s){
    s.linkBox=node('div');s.linkBox.dataset.companyPublicLink='';s.linkBox.hidden=true;
    s.linkLabel=node('span',linkText('label'));s.linkStatus=node('p');s.linkStatus.setAttribute('role','status');
    s.linkCopy=button('',()=>void copyPublicLink(s));s.linkCopy.className='operator-company-copy-icon';
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('width','17');svg.setAttribute('height','17');
    svg.setAttribute('fill','none');svg.setAttribute('stroke','currentColor');svg.setAttribute('stroke-width','1.7');
    svg.setAttribute('aria-hidden','true');
    const rect=document.createElementNS(svg.namespaceURI,'rect'),path=document.createElementNS(svg.namespaceURI,'path');
    for(const [key,value] of Object.entries({x:8,y:8,width:12,height:12,rx:2}))rect.setAttribute(key,value);
    path.setAttribute('d','M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2');
    svg.append(rect,path);s.linkCopy.append(svg);labelPublicLink(s);
    s.linkBox.append(s.linkLabel,s.linkCopy,s.linkStatus);s.section.append(s.linkBox);
  }
  function labelPublicLink(s){
    s.linkLabel.textContent=linkText('label');s.linkCopy.title=linkText('copy');
    s.linkCopy.setAttribute('aria-label',linkText('copy'));
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
    s.sync();syncPublicLink(s);syncProfile(s);
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
    s.writing=true;clearPublicLink(s);clearProfile(s);s.read?.abort();s.read=null;s.cancelSearch();s.sync();
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
      if(current(s)){s.sync();if(recheck)refresh(s);else{syncPublicLink(s);syncProfile(s);}}
    }
  }
  function mountEditor(s){
    const panel=node('div'),label=node('label',t('label')),field=node('span'),input=node('input'),results=node('div');
    panel.id='operatorDialogueCompanyPicker';
    label.htmlFor=input.id='operatorDialogueCompanySearch';field.className='operator-company-link-row';
    input.type='search';input.maxLength=180;input.autocomplete='off';
    input.setAttribute('data-lpignore','true');input.setAttribute('data-1p-ignore','true');
    const unlink=button('×',()=>{
      if(!active()||s.writing||s.binding?.can_manage!==true)return;
      input.value='';schedule();input.focus({preventScroll:true});
    });
    unlink.className='operator-company-unlink';unlink.title=t('remove');unlink.setAttribute('aria-label',t('remove'));
    unlink.onpointerdown=event=>event.preventDefault();
    field.append(input,unlink);panel.append(label,field);
    results.className='operator-dialog-company-results';
    const operatorRows=node('div'),localRows=node('div'),operatorStatus=node('p'),localStatus=node('p');
    operatorRows.dataset.operatorAccountResults='';localRows.dataset.localCompanyResults='';
    operatorStatus.setAttribute('role','status');localStatus.setAttribute('role','status');
    results.append(operatorRows,operatorStatus,localRows,localStatus);
    const suggestions=node('div');suggestions.className='operator-company-suggestions';suggestions.hidden=true;
    suggestions.id='operatorDialogueCompanySuggestions';suggestions.append(results);panel.append(suggestions);
    panel.addEventListener('focusout',event=>{
      if(panel.contains(event.relatedTarget)||!active()||s.writing||!s.editing)return;
      if(!input.value.trim()&&s.binding?.company_id&&s.binding.can_unlink!==false&&window.confirm(t('confirm'))){
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
      const hint=allowed?'search':s.binding===undefined?'loading':s.binding?'restricted':'unavailable';
      input.placeholder=t(hint);
      input.title=allowed?'':input.placeholder;
      unlink.hidden=!allowed||s.binding.can_unlink===false||!s.binding.company_id||!input.value;
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
      const operatorSearch=s.binding?.can_search_operator===true&&[...query].length>=2;
      localStatus.textContent=t('loading');operatorStatus.textContent=operatorSearch?t('loading'):'';
      void request(endpoint,s.member,{...s.route,company_search:query},true,signal)
        .then(payload=>{
          if(!active()||seq!==serial)return;
          const choices=new Map();
          for(const row of payload.companies||[]){
            if(row.id&&row.name)choices.set(String(row.id),String(row.name));
          }
          const items=[...choices].slice(0,5).map(([id,name])=>[name,{company_id:id}]);
          render(localRows,items);localStatus.textContent=items.length?'':t('empty');
        },()=>{if(active()&&seq===serial)localStatus.textContent=t('error');});
      if(!operatorSearch)return;
      try{
        const payload=await request(endpoint,s.member,{...s.route,operator_search:query},true,signal);
        if(!active()||seq!==serial)return;
        if(!Array.isArray(payload.accounts))throw Error('invalid account list');
        const accounts=[...new Set(payload.accounts
          .map(row=>row?.account).filter(value=>typeof value==='string'&&value.trim()&&value.length<=120))].slice(0,5);
        render(operatorRows,accounts.map(account=>[account.startsWith('@')?account:'@'+account,{operator_account:account}]));
        operatorStatus.textContent=accounts.length?'':copy('empty');
      }catch(error){if(active()&&seq===serial)operatorStatus.textContent=[401,403,424].includes(error.status)?profileText('denied'):copy('error');}
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
      labelPublicLink(s);s.labelProfile();
      if(snapshot!==undefined&&snapshot!==s.snapshot&&!s.writing){
        s.snapshot=snapshot;s.read?.abort();s.read=null;paint(s,snapshot);
      }else{paintHeader(s);s.sync();}
      return;
    }
    clear();
    const section=node('div'),value=node('span',t('loading')),status=node('p');
    headerHosts.set(section,nameHost);
    section.dataset.operatorDialogCompany='';
    value.dataset.companyValue='';value.setAttribute('role','status');value.setAttribute('aria-live','polite');
    status.className='operator-company-link-status';status.setAttribute('role','status');
    const initialCompanyName=String(route.dialog_company_name||'').trim();
    const s={section,value,status,member,isCurrent,onChanged,route:exact,key,snapshot,initialCompanyName,read:null};
    section.append(value,status);host.append(section);state=s;mountEditor(s);mountProfile(s);mountPublicLink(s);paintHeader(s);
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
    #operatorCompanyPanel #operatorDialogueCompanyPicker .operator-dialog-company-results button{display:block;width:100%;min-height:36px;text-align:start;border:0;color:inherit;background:var(--card)}
    #operatorCompanyPanel #operatorDialogueCompanyPicker .operator-dialog-company-results button:hover{background:var(--paper)}
    .operator-company-link-row{position:relative;display:block;min-width:0}
    [data-operator-dialog-company] [data-company-value]{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
    #operatorCompanyPanel #operatorDialogueCompanySearch{padding-inline-end:32px}
    #operatorCompanyPanel #operatorDialogueCompanySearch::-webkit-search-cancel-button{display:none}
    #operatorCompanyPanel .operator-company-unlink{display:inline-grid;position:absolute;inset-inline-end:3px;top:50%;transform:translateY(-50%);place-items:center;width:26px;min-height:26px;padding:0;border:0;background:transparent;color:var(--muted);font-size:18px}
    #operatorCompanyPanel .operator-company-unlink[hidden],.operator-company-link-status:empty{display:none}
    .operator-company-link-status{margin:0;color:var(--muted);font-size:.75rem}
    [data-company-profile]>div{display:grid;gap:3px;padding:7px 0;border-block-start:1px solid var(--line)}
    [data-company-profile] small,[data-company-profile]>p{color:var(--muted);font-size:.75rem}
    [data-company-profile]>p{margin:0}
    [data-company-profile][hidden],[data-company-profile]>p:empty{display:none!important}
    [data-company-public-link]{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:4px 8px;border-block-start:1px solid var(--line);padding-block-start:3px}
    [data-company-public-link][hidden],[data-company-public-link] button[hidden],
    [data-company-public-link] p:empty{display:none!important}
    [data-company-public-link] p{grid-column:1/-1;margin:0;color:var(--muted);font-size:.75rem}
    #operatorCompanyPanel .operator-company-copy-icon{display:inline-grid;place-items:center;width:40px;min-height:40px;padding:4px}
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
  window.tenantDialogueCompany={mount,clear,refresh,closePicker(){state?.reset?.();state?.sync?.();},
    refreshPublicLink(force=false){if(state){syncPublicLink(state,force);syncProfile(state);}},
    hidePublicLink(){if(state){clearPublicLink(state);clearProfile(state);}}
  };
})();
