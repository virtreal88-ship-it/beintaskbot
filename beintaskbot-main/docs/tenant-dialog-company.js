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
    ru:{unconfirmed:'Выдача ссылки пока не подтверждена. Повторный клик проверит сохранение.',not_found:'Operator не вернул существующую ссылку.',not_connected:'Operator не подключён.',not_linked:'У компании нет подтверждённой привязки к аккаунту Operator.',rejected:'Operator отклонил запрос ссылки.',denied:'У вас нет доступа к ссылке этой компании.'},
    az:{unconfirmed:'Linkin verilməsi hələ təsdiqlənməyib. Yenidən klikləyərək yoxlayın.',not_found:'Operator mövcud linki qaytarmadı.',not_connected:'Operator qoşulmayıb.',not_linked:'Şirkət təsdiqlənmiş Operator hesabı ilə əlaqələndirilməyib.',rejected:'Operator link sorğusunu rədd etdi.',denied:'Bu şirkətin linkinə giriş icazəniz yoxdur.'},
    en:{unconfirmed:'Link issuance is unconfirmed. Click again to check the saved result.',not_found:'Operator did not return an existing link.',not_connected:'Operator is disconnected.',not_linked:'This company has no verified Operator account link.',rejected:'Operator rejected the link request.',denied:'You do not have access to this company’s link.'}
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
    paintHeader(old);old.tabs.remove();clearPublicLink(old);clearProfile(old);old.read?.abort();old.cancelSearch?.();old.section.remove();
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
    ru:['Главная','Задачи','Модули','История платежей'],
    az:['Əsas','Tapşırıqlar','Modullar','Ödəniş tarixçəsi'],
    en:['Main','Tasks','Modules','Payment history']
  };
  const tabs=[['main',null],['tasks','company_tasks'],['modules','company_modules'],['history','company_history']];
  function attachTabs(s){
    const header=s.section.closest('dialog')?.querySelector('.operator-client-panel-header');
    if(header&&s.tabs.parentElement!==header)header.append(s.tabs);
  }
  function mountOperatorTabs(s){
    s.tabs=node('div');s.tabs.className='operator-company-tabs';s.tabs.setAttribute('role','tablist');
    s.panels=tabs.map(([id,action])=>{
      const panel=node('div');panel.id='operatorCompanyView-'+id;panel.setAttribute('role','tabpanel');
      const tab=button('',()=>selectTab(s,id));tab.id='operatorCompanyTab-'+id;tab.setAttribute('role','tab');
      tab.setAttribute('aria-controls',panel.id);panel.setAttribute('aria-labelledby',tab.id);
      if(action){panel.tabIndex=0;panel.setAttribute('aria-live','polite');}
      s.tabs.append(tab);s.section.append(panel);
      return {id,action,panel,tab,read:null};
    });
    s.main=s.panels[0].panel;
    s.labelTabs=()=>{
      const lang=window.tenantI18n?.language||'az',names=sectionNames[lang]||sectionNames.az;
      s.tabs.setAttribute('aria-label',t('label'));
      s.panels.forEach(({tab},i)=>tab.textContent=names[i]);
    };
    s.tabs.onkeydown=event=>{
      const index=s.panels.findIndex(item=>item.tab===event.target);
      if(index<0||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
      event.preventDefault();
      const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
      selectTab(s,s.panels[next].id);s.panels[next].tab.focus();
    };
    attachTabs(s);s.labelTabs();selectTab(s,'main');
  }
  function selectTab(s,id){
    const changed=s.activeTab!==id;
    s.activeTab=id;
    for(const item of s.panels){
      const selected=item.id===id;
      item.tab.setAttribute('aria-selected',String(selected));item.tab.tabIndex=selected?0:-1;
      item.panel.hidden=!selected;
      if(!selected&&item.read){item.read.abort();item.read=null;}
    }
    if(changed&&id!=='main')void loadTab(s,s.panels.find(item=>item.id===id));
  }
  function statusTone(status){
    const key=status.trim().toLowerCase().replace(/[-_]+/g,' ');
    if(['done','completed','resolved','closed','approved','готово','решено','завершено','bağlanıb','tamamlanıb','həll olunub'].includes(key))return 'success';
    if(['in progress','in work','testing','in review','в работе','тестирование','проверка','icrada','işdə','testdə'].includes(key))return 'progress';
    if(['blocked','failed','canceled','cancelled','rejected','заблокировано','отменено','отклонено','ləğv edilib','bloklanıb','rədd edilib'].includes(key))return 'danger';
    if(['new','open','todo','to do','pending','новая','новый','открыта','ожидает','yeni','açıq','gözləyir'].includes(key))return 'pending';
    return 'neutral';
  }
  function renderSection(item,rows){
    const fields=item.id==='modules'?1:3;
    if(rows.some(row=>!Array.isArray(row)||row.length!==fields||row.some(field=>typeof field!=='string')))throw Error();
    const list=node('ul');list.className='operator-company-section-list';
    for(const row of rows){
      const card=node('li');card.className='operator-company-section-'+item.id;
      if(item.id==='tasks'){
        const info=node('div');info.append(node('strong',row[1]),node('small',row[0]));
        const badge=node('span',row[2]);badge.className='operator-company-badge';badge.dataset.tone=statusTone(row[2]);
        card.append(info,badge);
      }else if(item.id==='modules'){
        const badge=node('span',row[0]);badge.className='operator-company-badge';badge.dataset.tone='neutral';card.append(badge);
      }else{
        const info=node('div');info.append(node('strong',row[2]),node('small',row[0]));
        const amount=node('span',row[1]);amount.className='operator-company-section-amount';
        card.append(info,amount);
      }
      list.append(card);
    }
    item.panel.replaceChildren(list);
  }
  async function loadTab(s,item){
    const company=s.binding?.company_id;
    if(!company||!linkCurrent(s,company)){item.panel.textContent=t(company?'unavailable':'unknown');return;}
    const read=new AbortController();item.read=read;
    const active=()=>item.read===read&&s.activeTab===item.id&&linkCurrent(s,company);
    item.panel.textContent=t('loading');
    try{
      const {company_section:section}=await request(endpoint,s.member,
        {...s.route,action:item.action,expected_company_id:company},true,read.signal);
      if(!active())return;
      if(section?.state!=='available'){
        item.panel.textContent=['not_connected','not_linked'].includes(section?.state)?linkProblem(section.state):profileText('error');return;
      }
      if(!Array.isArray(section.rows))throw Error();
      if(section.rows.length)renderSection(item,section.rows);else item.panel.textContent=profileText('empty');
    }catch(error){if(active())item.panel.textContent=profileText([401,403,424].includes(error.status)?'denied':'error');}
    finally{if(item.read===read)item.read=null;}
  }
  function clearProfile(s){
    for(const item of s.panels.slice(1)){item.read?.abort();item.read=null;item.panel.replaceChildren();}
    selectTab(s,'main');
    s.actionHost?.replaceChildren();
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
        mountActions(s,company,profile.actions);
        s.registrationPhone.textContent=s.balance.textContent=profileText('empty');
        if(typeof profile.registration_phone==='string')s.registrationPhone.textContent=profile.registration_phone;
        if(Number.isFinite(profile.balance))s.balance.textContent=new Intl.NumberFormat(
          window.tenantI18n?.language||'az',{style:'currency',currency:'AZN'}).format(profile.balance);
      },error=>{if(active())s.profileStatus.textContent=profileText([401,403].includes(error.status)?'denied':'error');})
      .finally(()=>{if(s.profileRequest===read)s.profileRequest=null;});
  }
  const actionLabels={
    ru:['Создать задачу','Начислить кредит','Название','Описание','Проект','Приоритет','Высокий','Обычный','Низкий','Сумма (AZN)','Причина','Подтвердить','Готово','Operator отклонил операцию','Результат не подтверждён. Проверьте историю в Operator перед повтором.'],
    az:['Tapşırıq yarat','Kredit əlavə et','Başlıq','Təsvir','Layihə','Prioritet','Yüksək','Normal','Aşağı','Məbləğ (AZN)','Səbəb','Təsdiqlə','Tamamlandı','Operator əməliyyatı rədd etdi','Nəticə təsdiqlənməyib. Təkrarlamadan əvvəl Operator tarixçəsinə baxın.'],
    en:['Create task','Add credit','Title','Description','Project','Priority','High','Normal','Low','Amount (AZN)','Reason','Confirm','Completed','Operator rejected the operation','Outcome unconfirmed. Check Operator history before retrying.']
  };
  function mountActions(s,company,permissions){
    const host=s.actionHost;
    host.replaceChildren();
    const labels=actionLabels[window.tenantI18n?.language]||actionLabels.az;
    for(const kind of ['task','credit']){
      if(permissions?.[kind]!==true)continue;
      const task=kind==='task', box=node('details'), title=node('summary',labels[task?0:1]);
      const form=node('div'), controls={}, status=node('p');
      status.setAttribute('role','status');
      const field=(key,label,tag,options)=>{
        const row=node('label',label),input=node(tag);
        input.required=true;input.style.cssText='display:block;width:100%;box-sizing:border-box';
        if(options)for(const [value,caption] of options){const o=node('option',caption);o.value=value;input.append(o);}
        row.append(input);form.append(row);controls[key]=input;return input;
      };
      if(task){
        field('title',labels[2],'input').maxLength=240;
        field('description',labels[3],'textarea').maxLength=8000;
        field('project',labels[4],'select',[['','—'],...['Akul Admin','Akul POS','Akul PWA','Akul Mobile','Akul Counting','Dine Admin','Dine POS','Dine PWA','Dine QR','Umumi'].map(x=>[x,x])]);
        field('priority',labels[5],'select',[[2,labels[6]],[3,labels[7]],[4,labels[8]]]).value='3';
      }else{
        const amount=field('amount',labels[9],'input');amount.type='number';amount.min='0.01';amount.step='0.01';amount.max='999999999.99';
        field('info',labels[10],'input').maxLength=500;
      }
      let requestId=null;
      const submit=button(labels[11],async()=>{
        if(submit.disabled||!linkCurrent(s,company))return;
        for(const input of Object.values(controls))if(!input.reportValidity())return;
        const fields=Object.fromEntries(Object.entries(controls).map(([k,v])=>[k,v.value.trim()]));
        if(task)fields.priority=Number(fields.priority);
        if(!task&&!window.confirm(labels[1]+' · '+companyName(s.binding)+' · '+fields.amount+' AZN?'))return;
        requestId??=crypto.randomUUID();
        for(const el of [...Object.values(controls),submit])el.disabled=true;
        status.textContent=t('loading');
        let result;
        try{
          result=(await request(endpoint+'/action',s.member,{...s.route,expected_company_id:company,
            kind,fields,request_id:requestId},true)).state;
        }catch(error){result=[400,403,404,409].includes(error.status)?'denied':'unconfirmed';}
        if(!linkCurrent(s,company)||!host.contains(form))return;
        status.textContent=labels[result==='done'?12:result==='denied'?13:14];
        if(result==='unconfirmed')return;
        for(const el of [...Object.values(controls),submit])el.disabled=false;
        requestId=null;
        if(result==='done'){
          for(const el of Object.values(controls))if(el.tagName!=='SELECT')el.value='';
          // The Tasks/Payment History tabs fetch fresh lists whenever selected.
          if(!task){
            // Refresh balance without discarding the visible receipt.
            void request(endpoint,s.member,{...s.route,expected_company_id:company,action:'company_profile'},true)
              .then(({company_profile:p})=>{if(linkCurrent(s,company)&&host.contains(form)&&Number.isFinite(p.balance))
                s.balance.textContent=new Intl.NumberFormat(window.tenantI18n?.language||'az',{style:'currency',currency:'AZN'}).format(p.balance);}).catch(()=>{});
          }
        }
      });
      form.append(submit,status);box.append(title,form);host.append(box);
    }
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
    s.actionHost=node('div');s.actionHost.dataset.companyActions='';
    s.profileBox.append(s.profileStatus,s.actionHost);s.main.append(s.profileBox);
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
      {...s.route,action:'copy_customer_link',expected_company_id:company},true,read.signal).then(({public_link:link})=>{
        if(!active())throw Error('stale');
        if(['unconfirmed','not_found','not_connected','not_linked'].includes(link?.state))
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
    svg.append(rect,path);s.linkCopy.append(s.linkLabel,svg);labelPublicLink(s);
    s.linkBox.append(s.linkCopy,s.linkStatus);s.main.append(s.linkBox);
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
    input.type='search';input.maxLength=180;input.autocomplete='off';input.readOnly=true;
    input.setAttribute('data-search-input','');input.setAttribute('data-lpignore','true');input.setAttribute('data-1p-ignore','true');
    input.setAttribute('data-bwignore','true');input.setAttribute('data-form-type','other');
    input.setAttribute('autocapitalize','none');input.setAttribute('autocorrect','off');input.spellcheck=false;
    let editingValue='';
    const restoreTypedValue=()=>{if(input.value!==editingValue)input.value=editingValue;};
    const primeInput=()=>{
      if(!active()||!s.section.closest('dialog')?.open||s.writing||s.binding?.can_manage!==true)return;
      restoreTypedValue();input.readOnly=false;
    };
    // Like other app searches, remain read-only while idle. This control owns
    // readOnly because it must additionally enforce exact-route write access.
    input.addEventListener('pointerdown',primeInput);
    input.addEventListener('focus',primeInput);
    input.addEventListener('blur',()=>{input.readOnly=true;restoreTypedValue();});
    input.addEventListener('animationstart',event=>{if(event.animationName==='operatorCompanyAutofill')restoreTypedValue();});
    const unlink=button('×',()=>{
      if(!active()||s.writing||s.binding?.can_manage!==true)return;
      input.focus({preventScroll:true});primeInput();editingValue=input.value='';schedule();
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
    s.main.append(panel);
    let serial=0,timer=null,search=null,composing=false;
    const active=()=>current(s)&&panel.isConnected;
    s.cancelSearch=()=>{serial++;clearTimeout(timer);timer=null;search?.abort();search=null;};
    const clearResults=()=>{
      s.cancelSearch();operatorRows.replaceChildren();localRows.replaceChildren();
      operatorStatus.textContent='';localStatus.textContent='';
    };
    s.reset=()=>{
      clearResults();suggestions.hidden=true;
      s.editing=false;editingValue=input.value=companyName(s.binding);
    };
    s.sync=()=>{
      const allowed=s.binding?.can_manage===true;
      input.readOnly=!allowed||s.writing||document.activeElement!==input;
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
    input.oninput=event=>{
      // Autofill/replacement events are not company searches. Keep the last
      // manually edited value; do not send injected text to either endpoint.
      const typed=event.inputType||'';
      if(input.readOnly||document.activeElement!==input||
         !(/^(?:insert(?:Text|CompositionText|FromPaste|FromDrop)|delete[A-Za-z]+|history(?:Undo|Redo))$/.test(typed))){
        restoreTypedValue();return;
      }
      editingValue=input.value;schedule();
    };
    input.oncompositionstart=()=>{if(!input.readOnly){composing=true;schedule();}};
    input.oncompositionend=()=>{if(!input.readOnly&&composing){composing=false;editingValue=input.value;schedule();}};
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
      attachTabs(s);labelPublicLink(s);s.labelProfile();s.labelTabs();
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
    host.append(section);state=s;mountOperatorTabs(s);s.main.append(value,status);mountEditor(s);mountProfile(s);mountPublicLink(s);paintHeader(s);
    if(snapshot!==undefined)paint(s,snapshot);else refresh(s);
  }
  const style=node('style');
  style.textContent=`
    [data-operator-dialog-company]{min-width:0;color:var(--ink)}
    #operatorCompanyView-main:not([hidden]){display:grid;gap:7px;min-width:0}
    #operatorCompanyPanel [role=tabpanel][hidden]{display:none!important}
    #operatorCompanyPanel .operator-client-panel-header{min-width:0;gap:6px;padding-block:8px}
    #operatorCompanyPanelTitle{max-width:32%;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    #operatorCompanyPanel .operator-company-tabs{display:flex;align-items:center;flex:1 1 0;gap:2px;min-width:0;overflow-x:auto;scrollbar-width:none;overscroll-behavior-inline:contain}
    #operatorCompanyPanel .operator-company-tabs::-webkit-scrollbar{display:none}
    #operatorCompanyPanel .operator-company-tabs button{flex:none;border:0;border-radius:5px;min-height:40px;padding:0 5px;background:transparent;font-size:.72rem;white-space:nowrap;overflow-wrap:normal}
    #operatorCompanyPanel .operator-company-tabs button[aria-selected=true]{color:var(--brand);box-shadow:inset 0 -2px var(--brand)}
    #operatorCompanyPanel .operator-company-section-list{display:grid;gap:6px;list-style:none;padding:0;margin:0}
    #operatorCompanyPanel .operator-company-section-list li{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,auto);align-items:start;gap:8px;padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--card);font-size:.8rem;overflow-wrap:anywhere}
    #operatorCompanyPanel .operator-company-section-list li>div{min-width:0;display:grid;gap:3px}
    #operatorCompanyPanel .operator-company-section-list small{color:var(--muted);font-size:.72rem}
    #operatorCompanyPanel .operator-company-badge{justify-self:start;border-radius:6px;padding:3px 6px;font-size:.72rem;line-height:1.35;background:var(--paper);color:var(--ink);border:1px solid var(--line)}
    #operatorCompanyPanel .operator-company-badge[data-tone=success]{color:#114d30;background:#dcf5e6;border-color:#a5d7ba}
    #operatorCompanyPanel .operator-company-badge[data-tone=progress]{color:#234680;background:#e5edff;border-color:#b6c9f0}
    #operatorCompanyPanel .operator-company-badge[data-tone=danger]{color:#7b282e;background:#fce5e5;border-color:#e5b4b8}
    #operatorCompanyPanel .operator-company-badge[data-tone=pending]{color:#644713;background:#fff0d0;border-color:#e8cca0}
    #operatorCompanyPanel .operator-company-section-amount{justify-self:end;text-align:end;font-weight:600;font-variant-numeric:tabular-nums}
    #operatorCompanyPanel [role=tabpanel]>p{overflow-wrap:anywhere}
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
    @keyframes operatorCompanyAutofill{from{opacity:1}to{opacity:1}}
    #operatorDialogueCompanySearch:autofill{animation:operatorCompanyAutofill .01s}
    #operatorDialogueCompanySearch:-webkit-autofill{animation:operatorCompanyAutofill .01s}
    #operatorCompanyPanel .operator-company-unlink{display:inline-grid;position:absolute;inset-inline-end:3px;top:50%;transform:translateY(-50%);place-items:center;width:26px;min-height:26px;padding:0;border:0;background:transparent;color:var(--muted);font-size:18px}
    #operatorCompanyPanel .operator-company-unlink[hidden],.operator-company-link-status:empty{display:none}
    .operator-company-link-status{margin:0;color:var(--muted);font-size:.75rem}
    [data-company-profile]>div{display:grid;gap:3px;padding:7px 0;border-block-start:1px solid var(--line)}
    [data-company-profile] small,[data-company-profile]>p{color:var(--muted);font-size:.75rem}
    [data-company-profile]>p{margin:0}
    [data-company-profile][hidden],[data-company-profile]>p:empty,[data-company-actions]:empty{display:none!important}
    [data-company-public-link]{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:4px 8px;border-block-start:1px solid var(--line);padding-block-start:3px}
    [data-company-public-link][hidden],[data-company-public-link] button[hidden],
    [data-company-public-link] p:empty{display:none!important}
    [data-company-public-link] p{grid-column:1/-1;margin:0;color:var(--muted);font-size:.75rem}
    #operatorCompanyPanel .operator-company-copy-icon{grid-column:1/-1;display:flex;align-items:center;justify-content:space-between;width:100%;min-height:40px;padding:4px 8px}
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
  document.addEventListener('tenant-language',()=>{if(state){state.labelTabs();state.labelProfile();labelPublicLink(state);}});
  document.addEventListener('tenant-dialog-reauthorized',()=>{if(state?.snapshot===undefined)refresh();});
  window.tenantDialogueCompany={mount,clear,refresh,closePicker(){state?.reset?.();state?.sync?.();},
    refreshPublicLink(force=false){if(state){syncPublicLink(state,force);syncProfile(state);}},
    hidePublicLink(){if(state){clearPublicLink(state);clearProfile(state);}}
  };
})();
