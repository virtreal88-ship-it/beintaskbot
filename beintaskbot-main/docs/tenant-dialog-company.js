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
    ru:{label:'Ссылка для клиента',copy:'Скопировать ссылку',copied:'Ссылка скопирована',error:'Не удалось получить ссылку. Повторите попытку.',copyError:'Браузер не скопировал ссылку. Повторите попытку.',unsupported:'Копирование недоступно в этом браузере.',notFound:'Ссылка пока не создана.'},
    az:{label:'Müştəri linki',copy:'Linki kopyala',copied:'Link kopyalandı',error:'Linki almaq mümkün olmadı. Yenidən cəhd edin.',copyError:'Brauzer linki kopyalamadı. Yenidən cəhd edin.',unsupported:'Bu brauzerdə kopyalama əlçatan deyil.',notFound:'Link hələ yaradılmayıb.'},
    en:{label:'Customer link',copy:'Copy link',copied:'Link copied',error:'Could not retrieve the link. Try again.',copyError:'The browser could not copy the link. Try again.',unsupported:'Copying is unavailable in this browser.',notFound:'The link has not been created yet.'}
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
  // Keep the route-owned scheduling control available if Company data is absent.
  function releaseReminder(s){
    const reminder=s.reminder;
    if(reminder?.home?.isConnected&&s.main.contains(reminder.host))reminder.home.append(reminder.host);
    s.reminder=null;
  }
  function placeReminder(s,host){
    if(s.reminder?.host===host)return;
    releaseReminder(s);
    const home=host?.closest('[data-company-reminder-fallback]');
    if(home){s.reminder={host,home};s.main.append(host);}
  }
  function clear(){
    const old=state;state=null;
    if(!old)return;
    releaseReminder(old);paintHeader(old);old.tabs.remove();clearPublicLink(old);clearProfile(old);old.read?.abort();old.cancelSearch?.();old.section.remove();
  }
  async function request(path,member,fields,post=false,signal){
    const data={...fields,expected_tenant_id:String(member.tenant_id),expected_user_id:String(member.telegram_id)};
    const result=await fetch(path+(post?'':'?'+new URLSearchParams(data)),{
      cache:'no-store',credentials:'same-origin',signal,method:post?'POST':'GET',
      ...(post?{headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{})
    });
    const payload=await result.json();
    if(!result.ok||payload?.success!==true){
      const safeMessage=typeof payload?.error==='string'
        ?payload.error.replace(/[\x00-\x1f\x7f]+/g,' ').replace(/\s+/g,' ').trim().slice(0,240):'';
      const error=new Error(safeMessage||t('error'));error.status=result.status;throw error;
    }
    return payload;
  }
  const linkCurrent=(s,company)=>current(s)&&s.section.closest('dialog')?.open===true&&
    !s.writing&&s.binding?.company_id===company;
  function clearPublicLink(s){
    s.linkRequest?.abort();s.linkRequest=null;s.linkLookupRequest?.abort();s.linkLookupRequest=null;
    s.linkCompany=null;s.linkLoadedCompany=null;s.linkUrl='';
    s.linkBox.hidden=true;s.linkCopy.disabled=false;s.linkStatus.textContent='';
    if(s.linkAnchor){s.linkAnchor.hidden=true;s.linkAnchor.removeAttribute('href');s.linkAnchor.textContent='';}
  }
  function syncPublicLink(s,force=false){
    const company=s.binding?.company_id;
    if(force||s.linkCompany!==company||!company||!linkCurrent(s,company))clearPublicLink(s);
    if(!company||!linkCurrent(s,company))return;
    s.linkCompany=company;s.linkBox.hidden=false;
    if(s.linkLoadedCompany!==company&&!s.linkLookupRequest)void loadExistingPublicLink(s,company);
  }
  async function loadExistingPublicLink(s,company){
    const read=new AbortController();s.linkLookupRequest=read;
    const active=()=>s.linkLookupRequest===read&&linkCurrent(s,company);
    try{
      const {public_link:link}=await request(publicLinkEndpoint,s.member,
        {...s.route,expected_company_id:company},true,read.signal);
      if(!active())return;
      if(link?.state==='available'&&typeof link.url==='string'&&/^https:\/\/1000\.az\/[A-Za-z0-9_-]{6}$/.test(link.url)){
        s.linkUrl=link.url;s.linkAnchor.href=link.url;s.linkAnchor.textContent=link.url;s.linkAnchor.hidden=false;s.linkStatus.textContent='';
      }else if(link?.state==='not_found'){
        s.linkStatus.textContent=linkText('notFound');
      }else if(link?.state&&linkProblem(link.state)){
        s.linkStatus.textContent=linkProblem(link.state);
      }else{
        s.linkStatus.textContent=linkText('error');
      }
    }catch(error){if(active())s.linkStatus.textContent=[401,403,424].includes(error.status)?linkProblem(error.status===424?'rejected':'denied'):linkText('error');}
    finally{if(s.linkLookupRequest===read){s.linkLookupRequest=null;s.linkLoadedCompany=company;}}
  }
  const sectionNames={
    ru:['Главная','Задачи','Модули','Платежи'],
    az:['Əsas','Tapşırıqlar','Modullar','Ödənişlər'],
    en:['Main','Tasks','Modules','Payments']
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
      let body=null,toolbar=null;
      if(action){
        panel.tabIndex=0;
        if(id==='tasks'||id==='history'){toolbar=node('div');toolbar.dataset.companyActions='';panel.append(toolbar);}
        body=node('div');body.dataset.companySectionBody='';panel.append(body);
      }
      s.tabs.append(tab);s.section.append(panel);
      return {id,action,panel,body,toolbar,tab,read:null};
    });
    s.main=s.panels[0].panel;
    s.labelTabs=()=>{
      const lang=window.tenantI18n?.language||'az',names=sectionNames[lang]||sectionNames.az;
      s.tabs.setAttribute('aria-label',t('label'));
      s.panels.forEach((item,i)=>{
        item.tab.textContent=names[i];
        if(s.activeTab===item.id)item.refreshLocale?.();
      });
      for(const trigger of s.section.querySelectorAll('[data-company-action]')){
        trigger.textContent=(actionLabels[lang]||actionLabels.az)[trigger.dataset.companyAction==='task'?0:1];
      }
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
    if(changed&&s.closeAction)s.closeAction(false);
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
  const pageLabels={
    ru:['Страницы платежей','Предыдущая страница','Следующая страница','Страница'],
    az:['Ödəniş səhifələri','Əvvəlki səhifə','Növbəti səhifə','Səhifə'],
    en:['Payment history pages','Previous page','Next page','Page']
  };
  const PAYMENT_PAGE_SIZE=20;
  const paymentLabels={
    ru:{title:'История операций',hint:'Операции по балансу компании',type:'Тип',amount:'Сумма',credit:'Кредит',expense:'Ежедневный расход'},
    az:{title:'Əməliyyat tarixçəsi',hint:'Şirkət balansı üzrə əməliyyatlar',type:'Növ',amount:'Məbləğ',credit:'Kredit',expense:'günlük xərc'},
    en:{title:'Transaction history',hint:'Company balance transactions',type:'Type',amount:'Amount',credit:'Credit',expense:'Daily expense'}
  };
  const localeFor=lang=>({ru:'ru-RU',az:'az-AZ',en:'en-US'}[lang]||'az-AZ');
  const paymentKinds=new Map([['credit','credit'],['kredit','credit'],['кредит','credit'],
    ['daily charge','expense'],['daily expense','expense'],['günlük xərc','expense'],['gündəlik xərc','expense'],['ежедневный расход','expense']]);
  const paymentKind=value=>paymentKinds.get(value.trim().toLowerCase().replace(/[\s_-]+/g,' '))||'';
  const supportedCurrencies=typeof Intl.supportedValuesOf==='function'
    ?new Set(Intl.supportedValuesOf('currency')):null;
  function paymentAmount(raw,currency,locale){
    const match=/^([+-]?)(\d{1,15})(?:\.(\d{1,8}))?(?:\s+([A-Z]{3}))?$/.exec(raw.trim());
    // Do not add a displayed unit to an opaque or conflicting amount.
    if(!match)return {text:raw,positive:false};
    const [,sign,integer,fraction='',attached]=match;
    if((currency&&attached&&currency!==attached)||(integer.replace(/^0+/,'').length+fraction.length>15))
      return {text:raw,positive:false};
    const code=currency||attached;
    if(code&&(!/^[A-Z]{3}$/.test(code)||(supportedCurrencies&&!supportedCurrencies.has(code))))
      return {text:raw,positive:false};
    const number=Number(sign+integer+(fraction?'.'+fraction:''));
    const options={minimumFractionDigits:fraction.length,maximumFractionDigits:fraction.length,
      ...(sign==='+'?{signDisplay:'always'}:{}),...(code?{style:'currency',currency:code,currencyDisplay:'code'}:{})};
    try{return {text:new Intl.NumberFormat(locale,options).format(number),positive:number>0};}
    catch{return {text:raw,positive:false};}
  }
  function paymentDate(raw,locale){
    const shape=/^(\d{4}-\d{2}-\d{2})(?:[T ](?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d{1,9})?)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)?)?$/.exec(raw);
    const date=shape&&new Date(shape[1]+'T00:00:00Z');
    if(!date||Number.isNaN(date.getTime())||date.toISOString().slice(0,10)!==shape[1])return node('small',raw);
    // Use the provider's calendar date; never guess or shift its time zone.
    const time=node('time',new Intl.DateTimeFormat(locale,{dateStyle:'medium',timeZone:'UTC'}).format(date));
    time.dateTime=shape[1];time.title=raw;
    return time;
  }
  function paymentIcon(kind){
    const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');
    icon.setAttribute('viewBox','0 0 16 16');icon.setAttribute('aria-hidden','true');
    const path=document.createElementNS(icon.namespaceURI,'path');
    path.setAttribute('d',kind==='credit'?'M8 13V3m-4 4 4-4 4 4':'M8 3v10m-4-4 4 4 4-4');
    path.setAttribute('fill','none');path.setAttribute('stroke','currentColor');path.setAttribute('stroke-width','1.5');
    path.setAttribute('stroke-linecap','round');path.setAttribute('stroke-linejoin','round');
    icon.append(path);return icon;
  }
  function renderPaymentHistory(item,rows,initialPage=0){
    const lang=window.tenantI18n?.language,copy=paymentLabels[lang]||paymentLabels.az,labels=pageLabels[lang]||pageLabels.az;
    const locale=localeFor(lang),history=node('section'),header=node('div'),title=node('h3',copy.title),hint=node('p',copy.hint);
    history.className='operator-company-payment-history';header.className='operator-company-payment-intro';header.append(title,hint);
    const table=node('table'),thead=node('thead'),row=node('tr'),tbody=node('tbody');
    table.setAttribute('aria-label',copy.title);
    for(const name of [copy.type,copy.amount]){const field=node('th',name);field.scope='col';row.append(field);}
    thead.append(row);table.append(thead,tbody);history.append(header,table);item.body.replaceChildren(history);
    const pages=Math.ceil(rows.length/PAYMENT_PAGE_SIZE);
    let page=Math.min(initialPage,pages-1);
    item.refreshLocale=()=>{if(window.tenantI18n?.language!==lang)renderPaymentHistory(item,rows,page);};
    function drawRows(){
      tbody.replaceChildren();
      for(const [date,raw,type,currency] of rows.slice(page*PAYMENT_PAGE_SIZE,(page+1)*PAYMENT_PAGE_SIZE)){
        const row=node('tr'),details=node('td'),kind=paymentKind(type),label=node('span');
        label.className='operator-company-payment-kind';label.dataset.kind=kind||'unknown';
        if(kind)label.append(paymentIcon(kind));
        label.append(node('span',kind?copy[kind]:type));
        details.append(label,paymentDate(date,locale));
        const value=paymentAmount(raw,currency,locale),amount=node('td',value.text);
        amount.className='operator-company-section-amount';
        if(kind==='credit'&&value.positive)amount.dataset.positiveCredit='';
        row.append(details,amount);tbody.append(row);
      }
    }
    if(pages<=1){drawRows();return;}
    // Operator gives a verified snapshot, not a cursor. Pages only slice those rows.
    const nav=node('nav'),indicator=node('span');
    nav.className='operator-company-pagination';nav.setAttribute('aria-label',labels[0]);indicator.setAttribute('role','status');
    const prev=button('‹',()=>draw(page-1,prev)),next=button('›',()=>draw(page+1,next));
    prev.setAttribute('aria-label',labels[1]);next.setAttribute('aria-label',labels[2]);
    function draw(index,trigger){
      if(index<0||index>=pages||!nav.isConnected)return;
      page=index;drawRows();
      indicator.textContent=labels[3]+' '+(index+1)+' / '+pages;
      prev.disabled=index===0;next.disabled=index===pages-1;
      if(trigger?.disabled)(trigger===prev?next:prev).focus({preventScroll:true});
    }
    nav.append(prev,indicator,next);history.append(nav);draw(page);
  }
  function renderSection(item,rows){
    const fields=item.id==='modules'?1:3;
    if(rows.some(row=>!Array.isArray(row)||(row.length!==fields&&(item.id!=='history'||row.length!==4))||
         row.some(field=>typeof field!=='string')))throw Error();
    if(item.id==='history'){renderPaymentHistory(item,rows);return;}
    const list=node('ul');list.className='operator-company-section-list';
    for(const row of rows){
      const card=node('li');card.className='operator-company-section-'+item.id;
      if(item.id==='tasks'){
        const info=node('div');info.append(node('strong',row[1]),node('small',row[0]));
        const badge=node('span',row[2]);badge.className='operator-company-badge';badge.dataset.tone=statusTone(row[2]);
        card.append(info,badge);
      }else{
        const badge=node('span',row[0]);badge.className='operator-company-badge';badge.dataset.tone='neutral';card.append(badge);
      }
      list.append(card);
    }
    item.body.replaceChildren(list);
  }
  async function loadTab(s,item){
    item.read?.abort();item.read=null;item.refreshLocale=null;
    const company=s.binding?.company_id,body=item.body;
    if(!company||!linkCurrent(s,company)){body.textContent=t(company?'unavailable':'unknown');return;}
    const read=new AbortController();item.read=read;
    const active=()=>item.read===read&&s.activeTab===item.id&&linkCurrent(s,company);
    body.textContent=t('loading');body.setAttribute('aria-busy','true');
    try{
      const {company_section:section}=await request(endpoint,s.member,
        {...s.route,action:item.action,expected_company_id:company},true,read.signal);
      if(!active())return;
      if(section?.state!=='available'){
        body.textContent=['not_connected','not_linked'].includes(section?.state)?linkProblem(section.state):profileText('error');return;
      }
      if(!Array.isArray(section.rows))throw Error();
      if(section.rows.length)renderSection(item,section.rows);else body.textContent=profileText('empty');
    }catch(error){if(active())body.textContent=profileText([401,403,424].includes(error.status)?'denied':'error');}
    finally{if(item.read===read){item.read=null;body.removeAttribute('aria-busy');}}
  }
  function clearProfile(s){
    s.closeAction?.(false);
    s.actionPermissions=null;s.actionBinding=null;
    for(const item of s.panels.slice(1)){
      item.read?.abort();item.read=null;item.refreshLocale=null;item.body.replaceChildren();
      item.body.removeAttribute('aria-busy');item.toolbar?.replaceChildren();
    }
    selectTab(s,'main');
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
        mountActions(s,company,profile.actions,profile.task_options,profile.action_binding);
        s.registrationPhone.textContent=s.balance.textContent=profileText('empty');
        if(typeof profile.registration_phone==='string')s.registrationPhone.textContent=profile.registration_phone;
        if(Number.isFinite(profile.balance))s.balance.textContent=new Intl.NumberFormat(
          window.tenantI18n?.language||'az',{style:'currency',currency:'AZN'}).format(profile.balance);
      },error=>{if(active())s.profileStatus.textContent=profileText([401,403].includes(error.status)?'denied':'error');})
      .finally(()=>{if(s.profileRequest===read)s.profileRequest=null;});
  }
  const actionLabels={
    ru:['Создать задачу','Начислить кредит','Название','Описание','Проект','Приоритет','Высокий','Обычный','Низкий','Сумма (AZN)','Причина','Подтвердить','Готово','Operator отклонил операцию','Результат не подтверждён. Проверьте историю в Operator перед повтором.','Компания или подключение изменились. Откройте панель заново.'],
    az:['Tapşırıq yarat','Kredit əlavə et','Başlıq','Təsvir','Layihə','Prioritet','Yüksək','Normal','Aşağı','Məbləğ (AZN)','Səbəb','Təsdiqlə','Tamamlandı','Operator əməliyyatı rədd etdi','Nəticə təsdiqlənməyib. Təkrarlamadan əvvəl Operator tarixçəsinə baxın.','Şirkət və ya bağlantı dəyişib. Paneli yenidən açın.'],
    en:['Create task','Add credit','Title','Description','Project','Priority','High','Normal','Low','Amount (AZN)','Reason','Confirm','Completed','Operator rejected the operation','Outcome unconfirmed. Check Operator history before retrying.','The company or connection changed. Reopen the panel.']
  };
  const modalLabels={
    ru:{newTask:'Новая задача',account:'Аккаунт',cancel:'Отмена',back:'Назад',save:'Сохранить'},
    az:{newTask:'Yeni Tapşırıq',account:'Hesab',cancel:'Ləğv et',back:'Geri',save:'Yadda saxla'},
    en:{newTask:'New task',account:'Account',cancel:'Cancel',back:'Back',save:'Save'}
  };
  const unsettledActions=new Map();
  const actionContract=options=>options&&typeof options.account==='string'&&options.account.trim()&&
    Array.isArray(options.projects)&&options.projects.length&&options.projects.every(x=>typeof x==='string'&&x)&&
    Array.isArray(options.priorities)&&options.priorities.length&&options.priorities.every(x=>[2,3,4].includes(x));
  function mountActions(s,company,permissions,taskOptions,binding){
    s.actionPermissions=permissions;s.actionBinding=binding;
    s.taskOptions=taskOptions;
    for(const [kind,id,index] of [['task','tasks',0],['credit','history',1]]){
      const item=s.panels.find(panel=>panel.id===id),host=item.toolbar;
      host.replaceChildren();
      if(permissions?.[kind]!==true||typeof binding!=='string'||!(/^[0-9a-f]{64}$/).test(binding)||
        (kind==='task'&&!actionContract(taskOptions)))continue;
      const trigger=button((actionLabels[window.tenantI18n?.language]||actionLabels.az)[index],
        ()=>openAction(s,company,kind,item,trigger));
      trigger.dataset.companyAction=kind;
      trigger.className='primary';trigger.setAttribute('aria-haspopup','dialog');host.append(trigger);
    }
  }
  function openAction(s,company,kind,item,trigger){
    if(s.closeAction||s.activeTab!==item.id||!linkCurrent(s,company)||s.actionPermissions?.[kind]!==true)return;
    const lang=window.tenantI18n?.language||'az',labels=actionLabels[lang]||actionLabels.az,
      copy=modalLabels[lang]||modalLabels.az,task=kind==='task',options=s.taskOptions,binding=s.actionBinding;
    if(task&&!actionContract(options))return;
    const actionKey=JSON.stringify([s.key,company,kind,binding]);
    const dialog=node('dialog'),header=node('header'),title=node('h2',task?copy.newTask:labels[1]),form=node('form'),
      controls={},status=node('p'),footer=node('footer');
    dialog.dataset.companyActionModal=kind;dialog.className='operator-company-action-modal';
    dialog.setAttribute('aria-labelledby','operatorCompanyActionTitle');dialog.setAttribute('aria-modal','true');
    title.id='operatorCompanyActionTitle';status.setAttribute('role','status');
    const back=button('‹',()=>close());back.className='outline operator-company-action-back';back.setAttribute('aria-label',copy.back);
    header.append(back,title);dialog.append(header,form);
    const field=(parent,key,label,tag,choices)=>{
      const row=node('label',label),input=node(tag);
      if(key)input.required=true;
      if(choices)for(const [value,caption] of choices){const o=node('option',caption);o.value=value;input.append(o);}
      row.append(input);parent.append(row);
      if(key)controls[key]=input;
      return input;
    };
    if(task){
      field(form,'title',labels[2],'input').maxLength=240;
      const row=node('div');row.className='operator-company-action-grid';form.append(row);
      const account=field(row,null,copy.account,'input');account.readOnly=true;account.value=options.account;
      field(row,'priority',labels[5],'select',options.priorities.map(x=>[x,labels[x+4]])).value=String(options.priorities.includes(3)?3:options.priorities[0]);
      field(row,'project',labels[4],'select',[['','—'],...options.projects.map(x=>[x,x])]);
      field(form,'description',labels[3],'textarea').maxLength=8000;
    }else{
      const companyLabel=node('p',companyName(s.binding));companyLabel.className='operator-company-action-context';form.append(companyLabel);
      const amount=field(form,'amount',labels[9],'input');
      amount.type='number';amount.min='0.01';amount.step='0.01';amount.max='999999999.99';
      field(form,'info',labels[10],'input').maxLength=500;
    }
    const cancel=button(copy.cancel,()=>close());cancel.className='outline';
    const submit=node('button',task?copy.save:labels[11]);submit.type='submit';submit.className='primary';
    footer.append(cancel,submit);form.append(status,footer);
    const live=()=>s.closeAction===close&&dialog.isConnected&&s.activeTab===item.id&&linkCurrent(s,company)&&s.actionBinding===binding&&s.actionPermissions?.[kind]===true;
    const disable=value=>{for(const el of [...Object.values(controls),submit])el.disabled=value;};
    function close(restore=true){
      if(s.closeAction!==close)return;
      s.closeAction=null;s.settleAction=null;
      if(typeof dialog.close==='function'&&dialog.open)dialog.close();
      dialog.remove();
      if(restore&&trigger.isConnected&&!item.panel.hidden&&linkCurrent(s,company))trigger.focus({preventScroll:true});
    }
    // A transfer/source change or a permission rebind discards the modal;
    // a pending or unconfirmed write can never be restarted by reopening it.
    const existing=unsettledActions.get(actionKey);
    if(existing){disable(true);status.textContent=existing==='unconfirmed'?labels[14]:t('loading');}
    dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
    dialog.addEventListener('click',event=>{if(event.target===dialog)close();});
    dialog.addEventListener('keydown',event=>{
      if(event.key==='Escape'){event.preventDefault();close();return;}
      if(event.key!=='Tab')return;
      const controls=[...dialog.querySelectorAll('button,input,select,textarea')].filter(el=>!el.disabled&&!el.readOnly);
      if(event.shiftKey&&document.activeElement===controls[0]){event.preventDefault();controls.at(-1)?.focus();}
      else if(!event.shiftKey&&document.activeElement===controls.at(-1)){event.preventDefault();controls[0]?.focus();}
    });
    form.onsubmit=async event=>{
      event.preventDefault();
      if(submit.disabled||unsettledActions.has(actionKey)||!live()||!form.reportValidity())return;
      const fields=Object.fromEntries(Object.entries(controls).map(([k,v])=>[k,v.value.trim()]));
      if(task)fields.priority=Number(fields.priority);
      if(!task&&!window.confirm(labels[1]+' · '+companyName(s.binding)+' · '+fields.amount+' AZN?'))return;
      if(!live())return;
      const requestId=crypto.randomUUID();
      unsettledActions.set(actionKey,'pending');disable(true);status.textContent=t('loading');
      let result;
      try{
        result=(await request(endpoint+'/action',s.member,{...s.route,expected_company_id:company,
          expected_binding:binding,kind,fields,request_id:requestId},true)).state;
      }catch(error){result=error.status===409?'stale':[400,403,404].includes(error.status)?'denied':'unconfirmed';}
      if(['done','denied','stale'].includes(result))unsettledActions.delete(actionKey);
      else unsettledActions.set(actionKey,'unconfirmed');
      // A newly opened modal for this same exact route/company/kind may be
      // waiting on our write; never update the old disconnected surface.
      state?.settleAction?.(actionKey,result);
    };
    s.settleAction=(key,result)=>{
      if(key!==actionKey||!live())return;
      const confirmed=result==='done'||result==='denied';
      status.textContent=labels[result==='done'?12:result==='denied'?13:result==='stale'?15:14];
      if(!confirmed)return;
      disable(false);
      if(result==='done'){
        for(const el of Object.values(controls))if(el.tagName!=='SELECT')el.value='';
        void loadTab(s,item);
        if(!task)void request(endpoint,s.member,{...s.route,expected_company_id:company,action:'company_profile'},true)
          .then(({company_profile:p})=>{if(linkCurrent(s,company)&&Number.isFinite(p.balance))
            s.balance.textContent=new Intl.NumberFormat(lang,{style:'currency',currency:'AZN'}).format(p.balance);}).catch(()=>{});
      }
    };
    s.closeAction=close;document.body.append(dialog);
    if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','');
    const first=(task?controls.title:controls.amount);(first.disabled?back:first).focus({preventScroll:true});
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
    s.profileBox.append(s.profileStatus);s.main.append(s.profileBox);
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
        s.linkUrl=link.url;s.linkAnchor.href=link.url;s.linkAnchor.textContent=link.url;s.linkAnchor.hidden=false;
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
    s.linkAnchor=node('a');s.linkAnchor.className='operator-company-public-url';s.linkAnchor.target='_blank';s.linkAnchor.rel='noopener noreferrer';s.linkAnchor.hidden=true;
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
    s.linkBox.append(s.linkLabel,s.linkAnchor,s.linkCopy,s.linkStatus);s.main.append(s.linkBox);
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
      const selection={...s.route,...fields,expected_company_id};
      let response=await request(endpoint,s.member,selection,true);
      if(response.confirm_existing_company&&current(s)){
        const candidate=response.existing_company;
        const pair=(candidate?.name||companyName(s.binding))+' ↔ @'+fields.operator_account;
        const prompt={az:'CRM şirkətini Operator hesabına bağlamaq istəyirsiniz?',ru:'Связать компанию CRM с аккаунтом Operator?',en:'Link this CRM company to this Operator account?'};
        if(!window.confirm((prompt[window.tenantI18n?.language]||prompt.az)+'\n'+pair)){
          s.reset();s.status.textContent='';return;
        }
        response=await request(endpoint,s.member,{...selection,confirm_existing:true,
          ...(candidate?{confirmed_company_id:candidate.id}:{})},true);
      }
      const {binding}=response;
      if(!current(s))return;
      if(!binding||(fields.company_id===null?binding.company_id:!binding.company_id))throw Error(t('error'));
      s.reset();s.status.textContent='';paint(s,binding);
      if(current(s))void s.onChanged?.(binding);
    }catch(error){
      if(current(s)){
        recheck=[401,403,404,409].includes(error.status);
        if(recheck&&expected_company_id)s.reset();
        s.status.textContent=error.message&&error.message!==t('error')?error.message:(fields.operator_account?copy('bindError'):t('error'));
      }
    }finally{
      s.writing=false;
      if(current(s)){s.sync();if(recheck)refresh(s);else{syncPublicLink(s);syncProfile(s);}}
    }
  }
  function mountEditor(s){
    const panel=node('div'),label=node('label',t('label')),field=node('span'),input=node('input'),results=node('div');
    panel.id='operatorDialogueCompanyPicker';
    panel.setAttribute('role','search');panel.setAttribute('aria-labelledby','operatorDialogueCompanySearchLabel');
    panel.setAttribute('data-form-type','other');label.id='operatorDialogueCompanySearchLabel';
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
    input.addEventListener('change',restoreTypedValue);
    input.addEventListener('animationstart',event=>{if(event.animationName==='operatorCompanyAutofill')restoreTypedValue();});
    const unlink=button('×',()=>{
      if(!active()||s.writing||s.binding?.can_manage!==true)return;
      input.focus({preventScroll:true});primeInput();editingValue=input.value='';schedule();
    });
    unlink.className='operator-company-unlink';unlink.title=t('remove');unlink.setAttribute('aria-label',t('remove'));
    unlink.onpointerdown=event=>event.preventDefault();
    field.append(input,unlink);panel.append(label,field);
    results.className='operator-dialog-company-results';
    const operatorRows=node('div'),operatorStatus=node('p');
    operatorRows.dataset.operatorAccountResults='';
    operatorStatus.setAttribute('role','status');
    results.append(operatorRows,operatorStatus);
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
      s.cancelSearch();operatorRows.replaceChildren();
      operatorStatus.textContent='';
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
        result.append(node('strong',label));
        rows.append(result);
      }
    }
    async function find(term,seq){
      if(!active()||seq!==serial||s.writing)return;
      const query=term.slice(0,120);
      search=new AbortController();const {signal}=search;
      if(s.binding?.can_search_operator!==true){operatorStatus.textContent=t('restricted');return;}
      operatorStatus.textContent=t('loading');
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
      s.editing=true;
      suggestions.hidden=[...query].length<2||composing||!s.editing;
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
  function mount({host,nameHost,reminderHost,route,member,isCurrent,onChanged}){
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
      placeReminder(s,reminderHost);attachTabs(s);labelPublicLink(s);s.labelProfile();s.labelTabs();
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
    host.append(section);state=s;mountOperatorTabs(s);s.main.append(value,status);mountEditor(s);mountProfile(s);mountPublicLink(s);placeReminder(s,reminderHost);paintHeader(s);
    if(snapshot!==undefined)paint(s,snapshot);else refresh(s);
  }
  const style=node('style');
  style.textContent=`
    [data-operator-dialog-company]{min-width:0;color:var(--ink)}
    #operatorCompanyView-main:not([hidden]){display:grid;gap:7px;min-width:0}
    #operatorCompanyPanel [data-company-reminder-fallback]:empty{display:none}
    #operatorCompanyPanel [role=tabpanel][hidden]{display:none!important}
    dialog#operatorCompanyPanel > .operator-client-panel-header{min-width:0;gap:6px;padding-block:4px}
    #operatorCompanyPanel .operator-company-tabs{display:flex;align-items:center;flex:1 1 0;gap:2px;min-width:0;overflow-x:auto;scrollbar-width:none;overscroll-behavior-inline:contain}
    #operatorCompanyPanel .operator-company-tabs::-webkit-scrollbar{display:none}
    #operatorCompanyPanel .operator-company-tabs button{flex:none;border:0;border-radius:0;min-height:32px;padding:0 5px;background:transparent;font-size:13px;white-space:nowrap;overflow-wrap:normal}
    #operatorCompanyPanel .operator-company-tabs button[aria-selected=true]{color:var(--brand);box-shadow:inset 0 -2px var(--brand)}
    dialog#operatorCompanyPanel>.operator-client-panel-header{box-sizing:border-box;height:60px;min-height:60px;padding:0 8px}
    #operatorCompanyPanel .operator-company-tabs{height:100%;align-items:stretch}
    #operatorCompanyPanel .operator-company-tabs button{height:100%;min-height:0}
    @media(max-width:900px){dialog#operatorCompanyPanel>.operator-client-panel-header{height:56px;min-height:56px;padding:0 4px}}
    #operatorCompanyPanel .operator-company-section-list{display:grid;gap:6px;list-style:none;padding:0;margin:0}
    #operatorCompanyPanel .operator-company-section-list li{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,auto);align-items:start;gap:8px;padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--card);font-size:14px;line-height:1.35;overflow-wrap:anywhere}
    #operatorCompanyPanel .operator-company-section-list li>div{min-width:0;display:grid;gap:3px}
    #operatorCompanyPanel .operator-company-section-list small{color:var(--muted);font-size:12px;line-height:1.35}
    #operatorCompanyPanel .operator-company-badge{justify-self:start;border-radius:6px;padding:3px 6px;font-size:12px;line-height:1.35;background:var(--paper);color:var(--ink);border:1px solid var(--line)}
    #operatorCompanyPanel .operator-company-badge[data-tone=success]{color:#114d30;background:#dcf5e6;border-color:#a5d7ba}
    #operatorCompanyPanel .operator-company-badge[data-tone=progress]{color:#234680;background:#e5edff;border-color:#b6c9f0}
    #operatorCompanyPanel .operator-company-badge[data-tone=danger]{color:#7b282e;background:#fce5e5;border-color:#e5b4b8}
    #operatorCompanyPanel .operator-company-badge[data-tone=pending]{color:#644713;background:#fff0d0;border-color:#e8cca0}
    #operatorCompanyPanel [data-company-actions]{display:flex;justify-content:flex-start;margin-block-end:8px}
    #operatorCompanyPanel .operator-company-pagination{display:flex;align-items:center;justify-content:center;gap:8px;padding-block-start:10px}
    #operatorCompanyPanel .operator-company-pagination button{min-width:40px}
    .operator-company-action-modal{position:fixed;inset:0;margin:auto;height:fit-content;box-sizing:border-box;max-width:calc(100vw - 24px);max-height:calc(100dvh - 24px);width:min(700px,100%);padding:0;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:14px;box-shadow:0 18px 56px #0005;overflow:auto;overscroll-behavior:contain}
    .operator-company-action-modal[data-company-action-modal=credit]{width:min(400px,100%)}
    .operator-company-action-modal::backdrop{background:#071d1688}
    .operator-company-action-modal header,.operator-company-action-modal footer{display:flex;align-items:center;gap:10px}
    .operator-company-action-modal header{padding:12px 16px;border-block-end:1px solid var(--line)}
    .operator-company-action-modal h2{margin:0;font-size:1.08rem}
    .operator-company-action-modal form{display:grid;gap:12px;padding:16px}
    .operator-company-action-modal footer{justify-content:flex-end}
    .operator-company-action-modal button{min-height:40px}
    .operator-company-action-modal .operator-company-action-back{min-width:40px}
    .operator-company-action-modal label{display:grid;gap:4px;font-size:.86rem;min-width:0}
    .operator-company-action-modal :is(input,select,textarea){box-sizing:border-box;width:100%;min-width:0;min-height:40px;margin:0;padding:10px 12px;border:1px solid var(--line);border-color:color-mix(in srgb,var(--line) 55%,var(--muted) 45%);border-radius:9px;background:var(--card);color:var(--ink);font:inherit;line-height:1.4}
    .operator-company-action-modal :is(input,select,textarea):focus{border-color:var(--brand);outline:1px solid transparent;outline-offset:2px;box-shadow:none}
    @media(forced-colors:active){.operator-company-action-modal :is(input,select,textarea):focus{outline-color:Highlight}}
    .operator-company-action-modal textarea{min-height:150px;resize:vertical}
    .operator-company-action-modal input[readonly]{background:var(--paper);color:var(--muted)}
    .operator-company-action-modal .operator-company-action-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,.7fr) minmax(0,1fr);gap:10px}
    .operator-company-action-modal p{margin:0;overflow-wrap:anywhere}
    .operator-company-action-modal [role=status]:empty{display:none}
    /* Match the native CRM Tasks card while keeping the credit dialog unchanged. */
    dialog.operator-company-action-modal[data-company-action-modal=task]{width:min(600px,calc(100vw - 24px));border-radius:18px;box-shadow:0 24px 80px #00170d55}
    .operator-company-action-modal[data-company-action-modal=task] h2{font-size:20px}
    .operator-company-action-modal[data-company-action-modal=task] form{gap:16px;padding:18px 22px 22px}
    .operator-company-action-modal[data-company-action-modal=task] label{gap:7px;font-size:14px}
    .operator-company-action-modal[data-company-action-modal=task] :is(input,select,textarea){min-height:44px;padding:11px 12px}
    .operator-company-action-modal[data-company-action-modal=task] input[readonly]{background:var(--paper);color:var(--muted)}
    .operator-company-action-modal[data-company-action-modal=task] textarea{min-height:150px}
    .operator-company-action-modal[data-company-action-modal=task] footer{gap:9px;margin-top:2px;padding-top:14px;border-top:1px solid var(--line)}
    .operator-company-action-modal[data-company-action-modal=task] footer button{min-height:44px}
    @media(max-width:639px){
      .operator-company-action-modal[data-company-action-modal=task] form{padding:16px}
      .operator-company-action-modal[data-company-action-modal=task] :is(input,select,textarea){font-size:16px}
      .operator-company-action-modal[data-company-action-modal=task] footer{display:grid;grid-template-columns:minmax(0,1fr);justify-content:stretch}
      .operator-company-action-modal[data-company-action-modal=task] footer button{width:100%}
    }
    @media(max-width:600px){.operator-company-action-modal .operator-company-action-grid{grid-template-columns:minmax(0,1fr)}.operator-company-action-modal input,.operator-company-action-modal select,.operator-company-action-modal textarea{font-size:16px}}
    #operatorCompanyPanel .operator-company-payment-history{min-width:0;overflow:hidden;border:1px solid var(--line);border-radius:9px;background:var(--card);font-size:13px;--payment-credit:#14543b;--payment-credit-bg:#e3f6ec;--payment-expense:#7c2932;--payment-expense-bg:#fcebed}
    html[data-app-theme=dark] #operatorCompanyPanel .operator-company-payment-history{--payment-credit:#87d9af;--payment-credit-bg:#203d31;--payment-expense:#ffadb5;--payment-expense-bg:#4b282f}
    #operatorCompanyPanel .operator-company-payment-intro{padding:9px 10px}
    #operatorCompanyPanel .operator-company-payment-intro h3{margin:0;font-size:14px;line-height:1.3}
    #operatorCompanyPanel .operator-company-payment-intro p{margin:3px 0 0;color:var(--muted);font-size:12px}
    #operatorCompanyPanel .operator-company-payment-history table{box-sizing:border-box;width:100%;table-layout:fixed;border-collapse:collapse;margin:0}
    #operatorCompanyPanel .operator-company-payment-history :is(th,td){padding:7px 8px;text-align:start;border-block-start:1px solid var(--line);background:transparent;overflow-wrap:anywhere;vertical-align:middle;line-height:1.35}
    #operatorCompanyPanel .operator-company-payment-history th{color:var(--muted);font-weight:600;background:var(--paper)}
    #operatorCompanyPanel .operator-company-payment-history :is(th,td):last-child{text-align:end;width:43%}
    #operatorCompanyPanel .operator-company-payment-kind{box-sizing:border-box;display:inline-flex;align-items:center;gap:3px;max-width:100%;padding:2px 5px;border-radius:999px;background:var(--paper);color:var(--ink);font-size:12px;font-weight:600}
    #operatorCompanyPanel .operator-company-payment-kind>span{min-width:0;overflow-wrap:anywhere}
    #operatorCompanyPanel .operator-company-payment-kind svg{width:12px;height:12px;flex:none}
    #operatorCompanyPanel .operator-company-payment-kind[data-kind=credit]{color:var(--payment-credit);background:var(--payment-credit-bg)}
    #operatorCompanyPanel .operator-company-payment-kind[data-kind=expense]{color:var(--payment-expense);background:var(--payment-expense-bg)}
    #operatorCompanyPanel .operator-company-payment-history :is(time,small){display:block;margin-block-start:3px;color:var(--muted);font-size:12px}
    #operatorCompanyPanel .operator-company-section-amount{text-align:end;font-weight:600;font-variant-numeric:tabular-nums}
    #operatorCompanyPanel .operator-company-section-amount[data-positive-credit]{color:var(--payment-credit)}
    #operatorCompanyPanel .operator-company-payment-history .operator-company-pagination{padding:8px;border-block-start:1px solid var(--line)}
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
    [data-company-profile] small,[data-company-profile]>p{color:var(--muted);font-size:12px;line-height:1.35}
    [data-company-profile]>p{margin:0}
    [data-company-profile][hidden],[data-company-profile]>p:empty,[data-company-actions]:empty{display:none!important}
    [data-company-public-link]{display:grid;grid-template-columns:minmax(0,1fr) 36px;align-items:center;gap:4px 8px;border-block-start:1px solid var(--line);padding-block-start:8px}
    [data-company-public-link][hidden],[data-company-public-link] button[hidden],
    [data-company-public-link] p:empty{display:none!important}
    [data-company-public-link] p{grid-column:1/-1;margin:0;color:var(--muted);font-size:12px;line-height:1.35}
    #operatorCompanyPanel .operator-company-public-url{grid-column:1;min-width:0;overflow:hidden;color:var(--brand);font-size:13px;line-height:1.35;text-overflow:ellipsis;white-space:nowrap}
    #operatorCompanyPanel .operator-company-public-url[hidden]{display:none!important}
    #operatorCompanyPanel .operator-company-copy-icon{grid-column:2;display:grid;place-items:center;width:36px;min-width:36px;height:36px;min-height:36px;padding:0}
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
