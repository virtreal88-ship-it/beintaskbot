/* Scoped, read-only related content. Actions reuse the tenant task workflow. */
(() => {
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels=()=>({ru:{tasks:'Задачи',notes:'Заметки',create:'Создать задачу',emptyTasks:'В загруженной части списка задач этой сделки нет.',emptyNotes:'Заметок нет.',loading:'Загружаем…',error:'Не удалось загрузить. Повторите открытие карточки.',limited:'Показана только загруженная часть списка.',chat:'Переписка'},az:{tasks:'Tapşırıqlar',notes:'Qeydlər',create:'Tapşırıq yarat',emptyTasks:'Yüklənmiş siyahıda bu sövdələşmənin tapşırığı yoxdur.',emptyNotes:'Qeyd yoxdur.',loading:'Yüklənir…',error:'Yükləmək mümkün olmadı. Kartı yenidən açın.',limited:'Siyahının yalnız yüklənmiş hissəsi göstərilir.',chat:'Yazışma'},en:{tasks:'Tasks',notes:'Notes',create:'Create task',emptyTasks:'No tasks for this deal in the loaded list.',emptyNotes:'No notes.',loading:'Loading…',error:'Could not load. Reopen the card to retry.',limited:'Only the loaded part of the list is shown.',chat:'Conversation'}}[window.tenantI18n?.language]||{tasks:'Tapşırıqlar',notes:'Qeydlər',create:'Tapşırıq yarat',emptyTasks:'Tapşırıq yoxdur.',emptyNotes:'Qeyd yoxdur.',loading:'Yüklənir…',error:'Yükləmə xətası',limited:'Yüklənmiş hissə',chat:'Yazışma'});
  const cardCopy={
    ru:{general:'Общее',data:'Данные',chat:'Чат',dialog:'Открыть диалог',noChat:'К сделке не привязан доступный диалог.',company:'Компания',pipeline:'Воронка',responsible:'Ответственный',description:'Описание',customer:'Клиент',deal:'Сделка',stage:'Стадия сделки',close:'Закрыть',duplicate:'Найти дубликат',contact:'Контакт',history:'История',amount:'Сумма',fields:'Сделка',send:'Отправить',compose:'Сообщение клиенту',customerMessage:'Клиент',operator:'Оператор',file:'Открыть файл',media:'Вложение'},
    az:{general:'Ümumi',data:'Məlumat',chat:'Söhbət',dialog:'Dialoqu aç',noChat:'Sövdələşməyə əlçatan dialoq bağlanmayıb.',company:'Şirkət',pipeline:'Satış xətti',responsible:'Məsul əməkdaş',description:'Təsvir',customer:'Müştəri',deal:'Sövdələşmə',stage:'Sövdələşmə mərhələsi',close:'Bağla',duplicate:'Dublikatları tap',contact:'Əlaqə',history:'Tarixçə',amount:'Məbləğ',fields:'Sövdələşmə',send:'Göndər',compose:'Mesaj yazın',customerMessage:'Müştəri',operator:'Operator',file:'Faylı aç',media:'Qoşma'},
    en:{general:'Overview',data:'Details',chat:'Chat',dialog:'Open conversation',noChat:'There is no accessible conversation linked to this deal.',company:'Company',pipeline:'Pipeline',responsible:'Responsible employee',description:'Description',customer:'Customer',deal:'Deal',stage:'Deal stage',close:'Close',duplicate:'Find duplicate',contact:'Contact',history:'History',amount:'Amount',fields:'Deal details',send:'Send',compose:'Message to customer',customerMessage:'Customer',operator:'Operator',file:'Open attachment',media:'Attachment'}
  };
  const copy=()=>cardCopy[window.tenantI18n?.language]||cardCopy.az;
  const detailsCopy=()=>({
    ru:{feed:'Лента',name:'Имя',phone:'Телефон',companyUnavailable:'Название компании недоступно',unassigned:'Не назначен',assigneeUnavailable:'Имя недоступно',emptyFeed:'Событий нет.',incoming:'Входящий звонок',outgoing:'Исходящий звонок',stageChange:'Смена этапа'},
    az:{feed:'Lent',name:'Ad',phone:'Telefon',companyUnavailable:'Şirkətin adı əlçatan deyil',unassigned:'Təyin edilməyib',assigneeUnavailable:'Ad əlçatan deyil',emptyFeed:'Hadisə yoxdur.',incoming:'Gələn zəng',outgoing:'Gedən zəng',stageChange:'Mərhələ dəyişikliyi'},
    en:{feed:'Activity',name:'Name',phone:'Phone',companyUnavailable:'Company name unavailable',unassigned:'Unassigned',assigneeUnavailable:'Name unavailable',emptyFeed:'No activity.',incoming:'Incoming call',outgoing:'Outgoing call',stageChange:'Stage changed'},
  }[window.tenantI18n?.language]||{feed:'Lent',name:'Ad',phone:'Telefon',companyUnavailable:'Şirkətin adı əlçatan deyil',unassigned:'Təyin edilməyib',assigneeUnavailable:'Ad əlçatan deyil',emptyFeed:'Hadisə yoxdur.',incoming:'Gələn zəng',outgoing:'Gedən zəng',stageChange:'Mərhələ dəyişikliyi'});
  const css=document.createElement('style');
  css.textContent=`
    :is(#dealModal,#nativeEntityCard[data-type=deal]){padding:0;overflow:hidden}
    :is(#dealModal,#nativeEntityCard[data-type=deal])>.modal-card{position:relative;box-sizing:border-box;width:100%;height:var(--deal-viewport-height,100dvh);max-width:none!important;max-height:100dvh!important;margin:0;padding:0;border:0;border-radius:0!important;box-shadow:none;display:flex;flex-direction:column;overflow:hidden}
    #dealModal.show>.modal-card{animation:deal-slide-in 180ms ease-out}
    @keyframes deal-slide-in{from{transform:translateX(100%)}to{transform:none}}
    @media(prefers-reduced-motion:reduce){#dealModal.show>.modal-card{animation:none}}
    :is(#dealModal,#nativeEntityCard[data-type=deal])>.modal-card>.modal-head{position:static;flex:none;display:flex;align-items:center;gap:10px;padding:9px 18px;margin:0;border-bottom:1px solid var(--line)}
    :is(#dealModal,#nativeEntityCard[data-type=deal]) :is(#modalContent,#nativeEntityBody){flex:1;min-height:0;min-width:0;overflow:auto}
    .deal-conversation .deal-chat-identity{flex:1;min-width:0}
    .deal-conversation .deal-chat-identity h2{font-size:17px;margin:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .deal-conversation .modal-head .close{flex:none;min-width:44px;min-height:44px}
    .deal-conversation .deal-stage-bar{position:relative;flex:none;min-width:0;display:grid;gap:3px;margin:0 0 16px}
    .deal-conversation .deal-stage-trigger{display:flex;align-items:center;gap:9px;min-width:0;width:100%;color:var(--ink);font:inherit;background:var(--app-accent-soft,var(--paper));border:1px solid var(--line);border-radius:8px;padding:0 11px;min-height:44px}
    .deal-conversation .deal-stage-trigger:is(select){display:block}
    .deal-stage-trigger:is(button)::after{content:"";flex:none;width:6px;height:6px;border-right:1.5px solid;border-bottom:1.5px solid;transform:rotate(45deg);margin-bottom:3px}
    .deal-stage-trigger-label{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .deal-stage-bar>[role=status]{font-size:12px;color:var(--muted);overflow-wrap:anywhere}
    .deal-stage-bar>[role=status]:empty{display:none}
    .deal-stage-picker{position:absolute;z-index:60;top:100%;left:0;width:min(360px,calc(100vw - 24px));display:flex;align-items:start;padding:4px 0;box-sizing:border-box}
    .deal-stage-sheet{width:100%;max-width:520px;max-height:min(360px,55dvh);overflow:auto;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:14px;box-shadow:0 12px 40px #0003}
    .deal-stage-sheet header{display:flex;align-items:center;gap:10px;padding:14px;border-bottom:1px solid var(--line)}
    .deal-stage-sheet select{flex:1;min-width:0;max-width:100%;font:inherit;color:var(--ink);background:var(--card);border:0;min-height:44px}
    .deal-stage-sheet button{min-height:44px}.deal-stage-choice{display:flex;width:100%;align-items:center;gap:14px;text-align:left;background:none;color:var(--ink);border:0;border-bottom:1px solid var(--line);border-radius:0;font:inherit}
    .deal-stage-dot{height:16px;width:16px;border-radius:50%;flex:none;background:var(--stage-color,var(--brand))}.deal-stage-choice-name{flex:1;min-width:0;overflow-wrap:anywhere}
    #dealModal #modalContent.deal-card-layout,#nativeEntityCard[data-type=deal] #nativeEntityBody.deal-card-layout{display:flex;flex-direction:column;overflow:hidden}
    .deal-card-layout [hidden],.deal-conversation .deal-stage-picker[hidden]{display:none!important}
    .deal-card-tabs{display:flex;align-items:stretch;gap:4px;padding:0 18px;border-bottom:1px solid var(--line);min-width:0;flex:none}
    .deal-card-tabs button{border:0;background:none;color:var(--muted);font:inherit;border-radius:0;min-height:42px;max-width:100%;padding:4px 12px;white-space:normal}
    .deal-card-tabs button[aria-selected=true]{color:var(--brand);box-shadow:inset 0 -2px var(--brand);font-weight:600}
    .deal-card-tabs [data-deal-pane=data]{display:none}
    .deal-card-columns{flex:1;min-height:0;display:grid;grid-template-columns:minmax(285px,36%) minmax(0,1fr);padding:12px;gap:10px;background:var(--app-surface,var(--paper))}
    .deal-card-main{display:flex;flex-direction:column;min-height:0;min-width:0;overflow:hidden}
    .deal-card-layout :is(.deal-info-panel,.deal-card-main){margin:0;min-height:0;min-width:0;border:1px solid var(--line);border-radius:12px;background:var(--card);overscroll-behavior:contain}
    .deal-card-layout .deal-info-panel{overflow:auto;padding:16px}
    .deal-info-panel>h3{margin:0 0 12px;font-size:16px}
    .deal-info-panel,.deal-feed-panel,.deal-chat-history{scrollbar-width:thin;touch-action:pan-y;overscroll-behavior:contain}
    :is(.deal-info-panel,.deal-feed-panel,.deal-chat-history)::-webkit-scrollbar{width:6px}
    :is(.deal-info-panel,.deal-feed-panel,.deal-chat-history)::-webkit-scrollbar-thumb{background:var(--muted);border-radius:8px;min-height:40px}
    .deal-operator-company-entry:has(> :not(h3)){margin-bottom:18px}
    .deal-operator-company-entry:not(:has(> :not(h3))){display:none}
    .deal-operator-company-entry>h3{margin-bottom:9px}
    :is(#dealInfoPanel,#nativeDealInfoPanel) .deal-operator-company-entry>button{box-sizing:border-box;width:100%;min-height:44px;text-align:start;white-space:normal;overflow-wrap:anywhere}
    .deal-card-layout .chat-placeholder{display:flex;flex:1;min-height:0;min-width:0;flex-direction:column;overflow:hidden;padding:0;margin:0;border:0;border-radius:0}
    .deal-feed-panel{flex:1;min-height:0;overflow:auto;overscroll-behavior:contain;padding:12px 16px}
    .deal-feed-panel :is([data-related-tasks],[data-related-notes]):empty,.deal-feed-actions>section:empty{display:none}
    .deal-feed-panel:has([aria-busy=true]) [data-deal-timeline]>.deal-card-empty{display:none}
    .deal-feed-actions{display:flex;justify-content:flex-end;align-items:flex-start;flex-wrap:wrap;gap:8px}
    .deal-feed-actions>section{flex:0 1 auto;min-width:0}.deal-feed-actions header:empty{display:none}
    .deal-feed-entry{display:block;margin:8px 0}
    .deal-chat-header{display:none;align-items:center;justify-content:flex-end;flex-wrap:wrap;gap:10px;padding:11px 16px;min-width:0;border-bottom:1px solid var(--line);flex:none}
    .deal-chat-header:has(button:not([hidden])){display:flex}
    .deal-chat-header h3,.deal-data-block h3,.kommo-deal-section h3{margin:0;font-size:15px;font-weight:600;color:var(--ink)}
    .deal-chat-header button{white-space:normal}
    .deal-card-layout .deal-chat-history{flex:1;min-height:0;overflow:auto;overscroll-behavior:contain;padding:10px 16px}
    .deal-chat-history #chatRows{max-height:none;overflow:visible;margin:0}
    .deal-linked-dialog{display:flex;flex:1;min-height:0;min-width:0;flex-direction:column;overflow:hidden}
    .deal-linked-dialog-tools{display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:8px 12px}
    .deal-linked-dialog-tools:empty{display:none}
    .deal-linked-dialog-body{display:flex;flex:1;min-height:0;min-width:0;overflow:hidden}
    .deal-linked-dialog-body .deal-linked-transcript{flex:1;padding:12px;overflow:auto}
    .deal-linked-dialog-body #operatorConversationPane{display:flex;flex:1;min-height:0;min-width:0;border:0;border-radius:0}
    .deal-linked-dialog-body [data-operator-back]{display:none!important}
    .deal-linked-dialog-body .operator-chat-history{scrollbar-width:thin;touch-action:pan-y}
    .deal-linked-dialog-body .operator-chat-history::-webkit-scrollbar{width:6px}
    .deal-linked-dialog-body .operator-chat-history::-webkit-scrollbar-thumb{min-height:40px;border-radius:8px;background:var(--muted)}
    @supports selector(::-webkit-scrollbar-thumb){
      :is(.deal-info-panel,.deal-feed-panel,.deal-chat-history,.deal-linked-dialog-body .operator-chat-history){scrollbar-width:auto}
    }
    .deal-card-layout .deal-chat-compose{flex:0 1 auto;min-height:0;max-height:60%;overflow:auto;overscroll-behavior:contain;padding:8px 12px max(8px,env(safe-area-inset-bottom));border-top:1px solid var(--line)}
    .deal-card-layout .deal-chat-compose .operator-composer{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:end;margin:0}
    .deal-card-layout .deal-chat-compose .operator-composer-actions{display:flex;align-items:center;gap:6px}
    .deal-card-layout .deal-chat-compose textarea{width:100%;margin:0;padding:9px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);font:inherit;resize:none;max-height:min(160px,20dvh);overflow-y:auto!important;min-height:44px;min-width:0;box-sizing:border-box}
    .deal-card-layout #tcAiResult{min-width:0;overflow-wrap:anywhere}
    .deal-card-layout :is(.deal-card-empty,.deal-card-status){margin:0;padding:10px 0;color:var(--muted)}
    .deal-data-block+.deal-data-block{margin-top:18px}
    .deal-data-block dl{background:var(--app-raised,var(--paper));border:1px solid var(--line);border-radius:10px;margin:9px 0 0;padding:4px 12px}
    .deal-data-block dl div{display:grid;grid-template-columns:minmax(92px,.7fr) minmax(0,1fr);align-items:start;gap:10px;padding:9px 0;overflow-wrap:anywhere}
    .deal-data-block dl div+div{border-top:1px solid var(--line)}
    .deal-data-block dt{color:var(--muted);font-size:13px}.deal-data-block dd{margin:0;white-space:pre-wrap}
    .deal-card-data-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:15px}
    .deal-card-data-actions:empty{display:none}
    .deal-info-panel>details{margin-top:12px;overflow-wrap:anywhere}
    .deal-info-panel>details>summary{padding:8px 0;color:var(--brand);cursor:pointer}
    #nativeEntityCard.deal-conversation[data-type=deal] .crm-modal-actions{flex:none;margin:0;padding:8px 12px;border-top:1px solid var(--line)}
    #nativeEntityCard.deal-conversation[data-type=deal] .crm-modal-actions :is([hidden],[data-native-card-close]){display:none!important}
    #nativeEntityCard.deal-conversation[data-type=deal] .crm-modal-actions:not(:has(button:not([hidden],[data-native-card-close]))){display:none}
    #nativeEntityCard.deal-conversation[data-type=deal] [data-native-card-status][hidden]{display:none!important}
    .kommo-deal-section{min-width:0}.kommo-deal-section header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}
    .kommo-related-card{display:block;box-sizing:border-box;width:100%;text-align:left;padding:12px;margin-top:9px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);font:inherit;overflow-wrap:anywhere;white-space:pre-wrap}
    .kommo-related-card small{display:block;color:var(--muted);margin-bottom:5px;font-size:12px}
    .deal-card-layout .deal-message{display:flex;margin:9px 0}.deal-card-layout .deal-message[data-direction=outgoing]{justify-content:flex-end}
    .deal-card-layout .deal-message>div{box-sizing:border-box;min-width:0;max-width:min(90%,580px);background:var(--app-raised,var(--paper));border:1px solid var(--line);border-radius:11px;padding:9px 12px;overflow-wrap:anywhere}
    .deal-card-layout .deal-message[data-direction=outgoing]>div{background:var(--app-accent-soft,color-mix(in srgb,var(--brand) 9%,var(--card)))!important}
    .deal-message-meta{display:flex;flex-wrap:wrap;align-items:baseline;gap:3px 10px;font-size:12px;color:var(--muted);margin-bottom:4px}.deal-message-meta strong{color:var(--ink)}
    .deal-message [data-chat-body]{white-space:pre-wrap}.deal-message [data-chat-attachment] button{margin-top:6px}
    .deal-conversation .deal-support-caption{display:none}
    @media(min-width:900px){.deal-conversation .deal-support-caption{display:inline}}
    @media(max-width:899px){
      :is(#dealModal,#nativeEntityCard[data-type=deal]).deal-conversation>.modal-card{align-self:start;top:var(--deal-viewport-top,0px)}
      .deal-card-tabs{padding:0 8px}.deal-card-tabs button{flex:1;min-width:0;min-height:38px;font-size:13px;padding-inline:7px;overflow-wrap:anywhere}
      .deal-card-tabs [data-deal-pane=data]{display:block}
      .deal-card-columns{display:flex;flex-direction:column;padding:0;gap:0}
      .deal-card-layout :is(.deal-info-panel,.deal-card-main){flex:1;min-height:0;border:0;border-radius:0}
      .deal-info-panel>h3{display:none}
      .deal-card-layout .deal-info-panel{padding:12px}.deal-chat-header,.deal-card-layout .deal-chat-history{padding-inline:12px}
      #nativeEntityCard.deal-conversation[data-type=deal]:not(:has([data-deal-pane=data][aria-selected=true])) .crm-modal-actions{display:none}
      .deal-card-layout .deal-chat-compose textarea{font-size:16px}
      :is(#dealModal,#nativeEntityCard[data-type=deal])>.modal-card>.modal-head{gap:6px;padding:6px 8px}
      .deal-conversation .deal-stage-bar{max-width:40%;margin:0}
      .deal-stage-picker{position:fixed;left:12px;right:12px;width:auto}.deal-conversation .deal-stage-trigger{font-size:12px;padding-inline:7px}
      .deal-conversation .deal-chat-identity h2{font-size:15px;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;white-space:normal;overflow-wrap:anywhere}
    }

  `;
  document.head.append(css);
  const date=value=>{const d=new Date(typeof value==='number'?value*1000:value);return value&&!Number.isNaN(d.getTime())?d.toLocaleString():'';};
  const epoch=value=>Number.isFinite(value)?value*1000:Date.parse(value);
  function activity(label,text,when){
    const node=document.createElement('article');node.className='kommo-related-card';
    const meta=document.createElement('small');meta.textContent=[label,date(when)].filter(Boolean).join(' · ');
    node.append(meta);
    if(text)node.append(document.createTextNode(text));
    return node;
  }
  function overview(deal,c=copy()){
    const l=detailsCopy();
    const block=(title,values,attribute='')=>{
      const fields=values.filter(([,value])=>value!==undefined&&value!==null&&value!=='');
      return fields.length?`<section class="deal-data-block" ${attribute}><h3>${esc(title)}</h3><dl>${fields.map(([label,value,track])=>`<div><dt>${esc(label)}</dt><dd ${track||''}>${esc(value)}</dd></div>`).join('')}</dl></section>`:'';
    };
    const company=deal.company_name||(deal.company_linked||deal.company_id?l.companyUnavailable:'');
    const assignee=deal.responsible_name||(deal.responsible_id?l.assigneeUnavailable:l.unassigned);
    const extra=(deal.detail_fields||[]).filter(({label})=>!['ttad_id','ttad_name','utm_campaign'].includes(String(label||'').trim().toLowerCase())).map(({label,value})=>[label,value]);
    return block(c.company,[[c.company,company]],'data-deal-company-block')+block(c.contact,[[l.name,deal.contact_name],[l.phone,deal.contact_phone??deal.phone],['Email',deal.contact_email]])+
      block(c.fields,[[c.pipeline,deal.pipeline_name||'—','data-deal-pipeline'],
      [c.responsible,assignee,'data-deal-assignee'],[c.amount,deal.price??deal.amount],
      [c.description,deal.description],...extra])+
      '<div class="deal-card-data-actions"></div>';
  }
  function layout({host,deal,sections,chat,stage,isCurrent,actions=[]}){
    const modal=host.closest('.modal'),head=modal.querySelector('.modal-head'),title=head.querySelector('h2'),closeButton=head.querySelector('.close'),c=copy();
    head.prepend(title);
    head.querySelectorAll('[data-deal-header-extra]').forEach(node=>node.remove());
    modal.classList.add('deal-conversation');
    title.textContent=deal.name||deal.title||c.deal;
    modal.querySelector('.modal-card').setAttribute('role','dialog');
    modal.querySelector('.modal-card').setAttribute('aria-modal','true');
    modal.querySelector('.modal-card').setAttribute('aria-labelledby',title.id);
    const identity=document.createElement('div');identity.className='deal-chat-identity';identity.dataset.dealHeaderExtra='';
    title.before(identity);identity.append(title);
    closeButton.setAttribute('aria-label',c.close);
    if(stage)stage.dataset.dealHeaderExtra='';
    const locale=detailsCopy(),tabs=document.createElement('div');tabs.className='deal-card-tabs';tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label',c.deal);
    const info=document.createElement('section');info.id=host.id==='modalContent'?'dealInfoPanel':'nativeDealInfoPanel';info.className='deal-info-panel';
    const infoTitle=document.createElement('h3');infoTitle.textContent=c.data;info.append(infoTitle);
    const companyHost=document.createElement('section');companyHost.className='deal-operator-company-entry deal-data-block';
    const companyTitle=document.createElement('h3');companyTitle.textContent=c.company;companyHost.append(companyTitle);
    const companyBlock=sections[0].node.querySelector('[data-deal-company-block]');
    if(companyBlock){companyBlock.querySelector('h3')?.remove();companyHost.append(companyBlock);}
    info.append(companyHost);
    const columns=document.createElement('div');columns.className='deal-card-columns';
    const main=document.createElement('div');main.className='deal-card-main';
    if(!chat)chat=document.createElement('div');
    chat.id=host.id+'Conversation';
    chat.classList.add('chat-placeholder');
    const chatHeader=document.createElement('header');chatHeader.className='deal-chat-header';
    const history=document.createElement('div');history.className='deal-chat-history';
    const composer=document.createElement('div');composer.className='deal-chat-compose';composer.hidden=true;
    const rows=chat.querySelector('#chatRows');
    if(rows){history.append(rows);composer.hidden=!chat.children.length;composer.append(...chat.children);}
    else{const status=document.createElement('p');status.className='deal-card-empty';status.textContent=c.noChat;history.append(status);}
    chat.replaceChildren(chatHeader,history,composer);
    const feed=document.createElement('section');feed.id=host.id+'Activity';feed.className='deal-feed-panel';
    const feedActions=document.createElement('div');feedActions.className='deal-feed-actions';
    const timeline=document.createElement('div');timeline.dataset.dealTimeline='';
    const empty=document.createElement('p');empty.className='deal-card-empty';empty.textContent=locale.emptyFeed;timeline.append(empty);
    feed.append(feedActions,timeline);
    for(const section of sections){
      section.node.dataset.dealPanel=section.key;
      if(section.key==='general'){info.append(section.node);continue;}
      if(section.data){
        const menu=document.createElement('details'),summary=document.createElement('summary');
        summary.textContent=section.label;summary.dataset.dealTab=section.key;
        summary.onclick=()=>{if(!menu.open&&isCurrent())section.load?.();};
        menu.append(summary,section.node);info.append(menu);continue;
      }
      feedActions.append(section.node);
    }
    columns.append(info,main);
    const dialog=document.createElement('section');
    dialog.id=host.id+'LinkedDialog';dialog.className='deal-linked-dialog';
    const dialogueName=({ru:'Диалог',az:'Dialoq',en:'Dialogue'})[window.tenantI18n?.language]||'Dialoq';
    main.append(chat,feed,dialog);
    host.classList.add('deal-card-layout');host.replaceChildren(columns);
    chatHeader.append(...actions);
    const panels={chat,data:info,feed,dialog},names={chat:c.chat,data:c.data,feed:locale.feed,dialog:dialogueName};
    const mobileOrder=['chat','data','feed','dialog'],desktopOrder=['chat','feed','dialog'];
    const buttons=Object.fromEntries(mobileOrder.map(key=>{
      const button=document.createElement('button');button.type='button';button.textContent=names[key];button.dataset.dealPane=key;
      button.dataset.dealTab=key;button.id=host.id+'Tab'+key;button.setAttribute('role','tab');button.setAttribute('aria-controls',panels[key].id);
      button.onclick=()=>select(key);tabs.append(button);return [key,button];
    }));
    const media=window.matchMedia?.('(min-width:900px)');
    let selected='chat',feedStarted=false;
    function display(){
      const desktop=media?.matches===true;
      if(desktop&&selected==='data')selected='chat';
      info.hidden=!desktop&&selected!=='data';chat.hidden=selected!=='chat';feed.hidden=selected!=='feed';dialog.hidden=selected!=='dialog';
      for(const key of mobileOrder){
        buttons[key].setAttribute('aria-selected',String(key===selected));buttons[key].tabIndex=key===selected?0:-1;
        panels[key].setAttribute('role',key==='data'&&desktop?'region':'tabpanel');
        panels[key].setAttribute('aria-labelledby',key==='data'&&desktop?infoTitle.id:buttons[key].id);
      }
      const tabHost=desktop?main:host;
      if(tabs.parentNode!==tabHost)tabHost.prepend(tabs);
      const stageHost=desktop?info:head;
      if(stage&&stage.parentNode!==stageHost)stageHost.insertBefore(stage,desktop?companyHost:closeButton);
    }
    function select(key){
      if(!isCurrent())return;
      if(key===selected)return;
      const previous=selected;
      selected=key;display();
      dialog.dispatchEvent(new CustomEvent('deal-pane-changed',{detail:{selected:key,previous}}));
      if(key==='feed'&&!feedStarted){
        feedStarted=true;
        for(const section of sections)if(!section.data)section.load?.();
      }
    }
    tabs.onkeydown=event=>{
      const order=media?.matches?desktopOrder:mobileOrder,index=order.indexOf(selected);
      const next={ArrowLeft:(index+order.length-1)%order.length,ArrowRight:(index+1)%order.length,Home:0,End:order.length-1}[event.key];
      if(next===undefined)return;event.preventDefault();select(order[next]);buttons[order[next]].focus();
    };
    infoTitle.id=info.id+'Title';
    const viewport=window.visualViewport,modalCard=modal.querySelector('.modal-card');
    const size=()=>{
      if(!media?.matches&&viewport?.scale===1){
        modalCard.style.setProperty('--deal-viewport-height',viewport.height+'px');
        modalCard.style.setProperty('--deal-viewport-top',viewport.offsetTop+'px');
      }else{
        modalCard.style.removeProperty('--deal-viewport-height');
        modalCard.style.removeProperty('--deal-viewport-top');
      }
    };
    const resize=()=>{display();size();};
    media?.addEventListener?.('change',resize);viewport?.addEventListener('resize',size);viewport?.addEventListener('scroll',size);
    const observer=new MutationObserver(()=>{
      if(host.contains(columns))return;
      observer.disconnect();media?.removeEventListener?.('change',resize);viewport?.removeEventListener('resize',size);viewport?.removeEventListener('scroll',size);
      if(!host.querySelector('.deal-card-columns')){
        modalCard.style.removeProperty('--deal-viewport-height');modalCard.style.removeProperty('--deal-viewport-top');
      }
      if(!host.querySelector('.deal-card-tabs'))host.classList.remove('deal-card-layout');
      if(modal.id==='nativeEntityCard'&&modal.dataset.type!=='deal'){
        if(identity.contains(title))head.prepend(title);
        identity.remove();stage?.remove();modal.classList.remove('deal-conversation');
      }
    });
    observer.observe(host,{childList:true});
    resize();
    function entry(node,when){
      node.classList.add('deal-feed-entry');
      node.dataset.dealWhen=String(epoch(when)||0);
      const before=Array.from(timeline.children).find(other=>Number(other.dataset.dealWhen)<Number(node.dataset.dealWhen));
      timeline.insertBefore(node,before||null);empty.hidden=true;
    }
    const calls=new Map();
    function chatCalls(messages,reset){
      if(reset){for(const node of calls.values())node.remove();calls.clear();}
      for(const message of messages){
        if(!message.external_id||!['call','call_in','call_out','call_missed','call_rejected','call_observed'].includes(message.message_type)||calls.has(message.external_id))continue;
        const label=message.direction==='outgoing'||message.message_type==='call_out'?locale.outgoing:locale.incoming;
        const node=activity(label,message.body,message.happened_at);
        calls.set(message.external_id,node);entry(node,message.happened_at);
      }
      empty.hidden=timeline.children.length>1;
    }
    return {dialog,chat,history,composer,entry,chatCalls,companyHost};
  }
  function mount({host,deal,api=window.tenantTaskFlow?.api,isCurrent}){
    const c=labels(),words=copy(),locale=detailsCopy(),general=document.createElement('section'),tasksPanel=document.createElement('section'),notesPanel=document.createElement('section');
    general.innerHTML=overview(deal,words);
    tasksPanel.className=notesPanel.className='kommo-deal-section';
    tasksPanel.innerHTML=`<header><button type="button" class="primary" data-related-create hidden>+ ${esc(c.create)}</button></header><div data-related-tasks role="status" aria-busy="true">${esc(c.loading)}</div>`;
    notesPanel.innerHTML=`<header></header><div data-related-notes role="status" aria-busy="true">${esc(c.loading)}</div>`;
    const chat=host.querySelector('.chat-placeholder'),modal=host.closest('#dealModal');
    modal.querySelectorAll('.deal-stage-picker').forEach(node=>node.remove());
    const duplicate=host.querySelector('[data-duplicate-search]');
    if(duplicate){duplicate.classList.add('deal-duplicate-action');duplicate.textContent=words.duplicate;duplicate.style.marginTop='0';
      const media=window.matchMedia?.('(min-width:900px)');
      const syncDuplicate=()=>{if(media?.matches)duplicate.dataset.duplicateAuto='true';else delete duplicate.dataset.duplicateAuto;};
      media?.addEventListener?.('change',syncDuplicate);syncDuplicate();
      const observer=new MutationObserver(()=>{if(!host.contains(duplicate)){media?.removeEventListener?.('change',syncDuplicate);observer.disconnect();}});observer.observe(host,{subtree:true,childList:true});
    }
    const current=()=>general.isConnected&&isCurrent()&&modal.classList.contains('show');
    const flow=window.tenantTaskFlow,scope=flow?.capture();
    const sections=[{key:'general',label:words.general,node:general}];
    if(flow?.sourceActions?.('kommo')?.view!==false)sections.push({key:'tasks',label:c.tasks,node:tasksPanel,load:loadTasks});
    sections.push({key:'notes',label:c.notes,node:notesPanel,load:loadNotes});
    // Reuse the board's policy catalog and immutable, receipt-verified move command.

    const stageBar=document.createElement('div');stageBar.className='deal-stage-bar';
    const stageLabel=({ru:'Этап сделки',az:'Sövdələşmə mərhələsi',en:'Deal stage'}[window.tenantI18n?.language]||'Deal stage');
    const trigger=document.createElement('button'),feedback=document.createElement('span');
    trigger.type='button';trigger.className='deal-stage-trigger';trigger.setAttribute('aria-label',stageLabel);trigger.setAttribute('aria-haspopup','listbox');trigger.setAttribute('aria-expanded','false');trigger.disabled=true;
    const triggerLabel=document.createElement('span');triggerLabel.className='deal-stage-trigger-label';triggerLabel.textContent=deal.stage_name||stageLabel;trigger.title=triggerLabel.textContent;trigger.replaceChildren(triggerLabel);
    feedback.setAttribute('role','status');stageBar.append(trigger,feedback);
    const view=layout({host,deal,sections,chat,stage:stageBar,isCurrent,actions:duplicate?[duplicate]:[]});
    if(deal.responsible_id&&flow?.api&&scope)void flow.api('/api/platform/crm/deal?details=1&lead_id='+encodeURIComponent(deal.kommo_lead_id),{scope}).then(result=>{
      if(!current()||!flow.current(scope)||!result.details?.responsible_name)return;
      general.querySelector('[data-deal-assignee]').textContent=result.details.responsible_name;
    }).catch(()=>{});
    const picker=document.createElement('div');picker.className='deal-stage-picker';picker.hidden=true;
    const sheet=document.createElement('section');sheet.className='deal-stage-sheet';sheet.setAttribute('role','listbox');sheet.setAttribute('aria-label',stageLabel);
    const pickerHead=document.createElement('header'),pipelineSelect=document.createElement('select'),dismiss=document.createElement('button'),choices=document.createElement('div');
    pipelineSelect.setAttribute('aria-label',({ru:'Сменить воронку',az:'Satış xəttini dəyiş',en:'Change pipeline'}[window.tenantI18n?.language]||'Change pipeline'));pipelineSelect.dataset.dealPipelinePicker='';
    dismiss.type='button';dismiss.textContent='×';dismiss.setAttribute('aria-label',({ru:'Закрыть',az:'Bağla',en:'Close'}[window.tenantI18n?.language]||'Close'));
    pickerHead.append(pipelineSelect,dismiss);sheet.append(pickerHead,choices);picker.append(sheet);stageBar.append(picker);
    const closePicker=()=>{picker.hidden=true;trigger.setAttribute('aria-expanded','false');if(current())trigger.focus();};
    dismiss.onclick=closePicker;picker.onclick=e=>{if(e.target===picker)closePicker();};
    picker.onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();closePicker();}else if(e.key==='Tab'){const nodes=[...sheet.querySelectorAll('button,select')].filter(n=>!n.disabled);if(!nodes.length)return;const first=nodes[0],last=nodes[nodes.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}};
    let stageRow={...deal},busy=false,stageCatalog=[];
    void Promise.resolve().then(()=>api('/api/platform/crm/deal-board-preferences')).then(policy=>{
      if(!current())return;
      const sources=policy.move_stage_sources||[],baseTargets=policy.move_stage_targets||[];
      stageCatalog=policy.catalog||[];
      const catalogFor=id=>policy.catalog?.find(p=>String(p.pipeline_id)===String(id));
      const permitted=()=>sources.some(t=>String(t.pipeline_id)===String(stageRow.pipeline_id)&&String(t.stage_id)===String(stageRow.status_id));
      const targetsFor=id=>{
        // Completing in another pipeline would bypass the approval workflow.
        // First choose a regular stage there, then use its successful stage.
        const same=String(id)===String(stageRow.pipeline_id);
        const targets=baseTargets.filter(t=>String(t.pipeline_id)===String(id)&&(same||Number(t.stage_id)!==142));
        if(same&&policy.move_stage_completion_requires_approval)for(const t of sources){if(String(t.pipeline_id)===String(id)&&Number(t.stage_id)===142&&!targets.some(a=>Number(a.stage_id)===142))targets.push(t);}
        return targets;
      };
      const render=()=>{
        const catalog=catalogFor(stageRow.pipeline_id);
        const field=general.querySelector('[data-deal-pipeline]');
        if(field)field.textContent=catalog?.name||stageRow.pipeline_name||'—';
        triggerLabel.textContent=stageRow.stage_name||stageLabel;trigger.title=triggerLabel.textContent;
        const stageColor=catalog?.stages?.find(item=>String(item.stage_id)===String(stageRow.status_id))?.color;
        trigger.style.backgroundColor=/^#[a-fA-F0-9]{6}$/.test(stageColor||'')?stageColor:'';
        trigger.style.color=trigger.style.backgroundColor?
          ((parseInt(stageColor.slice(1,3),16)*299+parseInt(stageColor.slice(3,5),16)*587+parseInt(stageColor.slice(5,7),16)*114)<140000?'#fff':'#111'):'';
        trigger.disabled=busy||!permitted()||!policy.move_context||!window.tenantKommoStageMove?.canIdentify(stageRow)||!(policy.catalog||[]).some(p=>targetsFor(p.pipeline_id).length);
        pipelineSelect.replaceChildren();
        for(const p of policy.catalog||[]){if(String(p.pipeline_id)===String(stageRow.pipeline_id)||targetsFor(p.pipeline_id).length)pipelineSelect.add(new Option(p.name||String(p.pipeline_id),String(p.pipeline_id)));}
        pipelineSelect.value=String(stageRow.pipeline_id);pipelineSelect.disabled=busy||pipelineSelect.options.length<2;
        renderChoices();
      };
      const renderChoices=()=>{
        choices.replaceChildren();const targetPipeline=pipelineSelect.value,catalog=catalogFor(targetPipeline),targets=targetsFor(targetPipeline);
        for(const item of catalog?.stages||[]){
          const selected=String(targetPipeline)===String(stageRow.pipeline_id)&&String(item.stage_id)===String(stageRow.status_id);
          if(!selected&&!targets.some(t=>String(t.stage_id)===String(item.stage_id)))continue;
          const button=document.createElement('button');button.type='button';button.className='deal-stage-choice';button.dataset.stageId=String(item.stage_id);button.dataset.pipelineId=targetPipeline;button.setAttribute('aria-pressed',String(selected));
          const dot=document.createElement('span'),name=document.createElement('span'),check=document.createElement('span');dot.className='deal-stage-dot';if(/^#[a-fA-F0-9]{6}$/.test(item.color||''))dot.style.setProperty('--stage-color',item.color);name.className='deal-stage-choice-name';name.textContent=item.name||String(item.stage_id);check.textContent=selected?'✓':'';button.append(dot,name,check);button.disabled=busy||!permitted()||selected;
          button.onclick=()=>void choose(targetPipeline,String(item.stage_id));choices.append(button);
        }
      };
      pipelineSelect.onchange=renderChoices;
      trigger.onclick=()=>{if(trigger.disabled||!current())return;if(!picker.hidden){closePicker();return;}render();if(window.matchMedia?.('(max-width:899px)').matches)picker.style.top=trigger.getBoundingClientRect().bottom+'px';else picker.style.top='';picker.hidden=false;trigger.setAttribute('aria-expanded','true');(pipelineSelect.disabled?dismiss:pipelineSelect).focus();};
      const choose=async(targetPipeline,target)=>{
        if(busy||!current()||!permitted()||!targetsFor(targetPipeline).some(t=>String(t.stage_id)===target))return;
        if(target===String(stageRow.status_id)&&targetPipeline===String(stageRow.pipeline_id))return;
        busy=true;trigger.disabled=true;feedback.textContent='';closePicker();
        const catalog=catalogFor(targetPipeline);
        try{
          if(target==='142'){
            if(targetPipeline!==String(stageRow.pipeline_id)||!window.tenantDealCompletion)throw Error('Completion unavailable');
            const result=await window.tenantDealCompletion.request(stageRow.kommo_lead_id,policy.move_context);
            if(!current()||result.cancelled)return;
            if(result.approval_pending){feedback.textContent='Təsdiq gözləyir';return;}
            if(result.completed){stageRow={...stageRow,source_revision:null,status_id:142,stage_name:catalog?.stages?.find(t=>Number(t.stage_id)===142)?.name||'Uğurla tamamlandı'};Object.assign(deal,stageRow);document.dispatchEvent(new CustomEvent('tenant-kommo-stage-confirmed',{detail:{context:policy.move_context,row:stageRow}}));return;}
            throw Error('Completion unavailable');
          }
          const updated=await window.tenantKommoStageMove.move(stageRow,target,policy.move_context,targetPipeline);
          if(!current())return;
          stageRow={...updated,pipeline_name:catalog?.name||stageRow.pipeline_name,stage_name:catalog?.stages?.find(t=>String(t.stage_id)===target)?.name||stageRow.stage_name};
          Object.assign(deal,stageRow);

        }catch(error){if(current())feedback.textContent=error.message||'Stage update failed';}
        finally{busy=false;if(current()){render();if(!trigger.disabled&&(picker.contains(document.activeElement)||document.activeElement===document.body))trigger.focus();}}
      };
      render();
    }).catch(()=>{if(current())trigger.disabled=true;});

    let tasksStarted=false,notesStarted=false;
    function loadTasks(){
    if(tasksStarted||!current())return;tasksStarted=true;
    const tasks=tasksPanel.querySelector('[data-related-tasks]');
    if(!flow){tasks.textContent=c.error;tasks.removeAttribute('aria-busy');}else void flow.read('kommo',{deal_filter:String(deal.kommo_lead_id)},scope).then(result=>{
      if(!current()||!flow.current(scope))return;
      if(result.source!=='kommo'||!Array.isArray(result.tasks)||result.sources?.find(item=>item.source==='kommo')?.view!==true)throw Error('invalid_tasks');
      const rows=result.tasks.filter(item=>String(item.kommo_lead_id)===String(deal.kommo_lead_id));
      tasks.replaceChildren();
      if(result.has_more)tasks.insertAdjacentHTML('beforeend',`<p class="sub">${esc(c.limited)}</p>`);
      for(const item of rows){
        const when=item.created_at||item.due_at;
        const button=document.createElement('button');button.type='button';button.className='kommo-related-card';button.dataset.relatedTask=item.id;
        const text=String(item.description||item.text||item.display_title||'').replace(/\n?\[CRM:[a-f0-9]{32}\]/g,'');
        button.append(...activity(item.task_type_name||c.tasks,text,when).childNodes);
        button.onclick=()=>{if(!current())return;document.getElementById('dealModal').classList.remove('show');void flow.openFromLink('kommo',item.id);};
        view.entry(button,when);
      }
      const create=tasksPanel.querySelector('[data-related-create]');create.hidden=result.sources.find(item=>item.source==='kommo')?.create!==true;
      create.onclick=async()=>{if(!current())return;create.disabled=true;try{const opened=await flow.createForKommoDeal(deal.kommo_lead_id,current,()=>{if(tasksPanel.isConnected&&isCurrent()){document.getElementById('dealModal').classList.add('show');create.focus();}});if(opened&&current())document.getElementById('dealModal').classList.remove('show');}catch{if(current())tasks.textContent=c.error;}finally{create.disabled=false;}};
    }).catch(()=>{if(current())tasks.textContent=c.error;}).finally(()=>tasks.removeAttribute('aria-busy'));
    }
    function loadNotes(){
      if(notesStarted||!current())return;notesStarted=true;
      const notes=notesPanel.querySelector('[data-related-notes]');
      const noteIds=new Set();
      const addNote=item=>{
        if(noteIds.has(item.id))return;
        noteIds.add(item.id);
        const row=activity(c.notes,item.text,item.created_at);row.classList.add('kommo-note-card');row.dataset.noteId=item.id;
        view.entry(row,item.created_at);
      };
      void (flow?flow.api('/api/platform/crm/deal/notes?include_events=1&lead_id='+encodeURIComponent(deal.kommo_lead_id),{scope}):Promise.reject(Error('scope_unavailable'))).then(result=>{
        if(!current()||!flow.current(scope))return;
        if(!Array.isArray(result.notes))throw Error('invalid_notes');
        window.tenantKommoDealNoteCreate?.mount({container:notesPanel,leadId:deal.kommo_lead_id,scope,current,canCreate:result.can_create===true,flow,onSaved:addNote});
        notes.replaceChildren();
        result.notes.forEach(addNote);
        for(const call of result.calls||[])view.entry(activity(call.type==='call_in'?locale.incoming:locale.outgoing,'',call.created_at),call.created_at);
        for(const event of result.stages||[]){
          const pipeline=stageCatalog.find(p=>String(p.pipeline_id)===event.pipeline_id);
          const stage=pipeline?.stages?.find(s=>String(s.stage_id)===event.stage_id);
          view.entry(activity(locale.stageChange,stage?.name,event.created_at),event.created_at);
        }
        if(result.has_more||result.stages_has_more)notes.textContent=c.limited;
        if(result.stages_unavailable)notes.append(document.createTextNode(' '+locale.stageChange+' · '+c.error));
      }).catch(()=>{if(current())notes.textContent=c.error;}).finally(()=>notes.removeAttribute('aria-busy'));
    }
    return view;
  }
  window.tenantKommoDealCard={mount,layout,overview,copy,activity};
})();
