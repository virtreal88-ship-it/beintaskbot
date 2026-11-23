/* Shared due markers on configured operator sources. */
(()=>{
  const endpoint='/api/platform/ai/dialogs/shared-reminders';
  const copy={
    ru:{list:'Общие напоминания',schedule:'Создать напоминание',note:'Общая заметка',date:'Дата и время',
      save:'Запланировать',close:'Закрыть',open:'Открыть напоминание',chat:'Открыть диалог',
      due:'Общее напоминание',empty:'Нет общих напоминаний',loading:'Загрузка…',saved:'Напоминание запланировано',
      opened:'Метка снята у всех',error:'Не удалось выполнить действие',loadError:'Не удалось загрузить напоминания',retry:'Повторить',more:'Показаны первые 100 напоминаний'},
    az:{list:'Ortaq xatırlatmalar',schedule:'Xatırlatma yarat',note:'Ortaq qeyd',date:'Tarix və vaxt',
      save:'Planlaşdır',close:'Bağla',open:'Xatırlatmanı aç',chat:'Dialoqu aç',due:'Ortaq xatırlatma',
      empty:'Ortaq xatırlatma yoxdur',loading:'Yüklənir…',saved:'Xatırlatma planlaşdırıldı',
      opened:'Nişan hamı üçün silindi',error:'Əməliyyat alınmadı',loadError:'Xatırlatmaları yükləmək mümkün olmadı',retry:'Yenidən yoxla',more:'İlk 100 xatırlatma göstərilir'},
    en:{list:'Shared reminders',schedule:'Create reminder',note:'Shared note',date:'Date and time',
      save:'Schedule',close:'Close',open:'Open reminder',chat:'Open dialogue',due:'Shared reminder',
      empty:'No shared reminders',loading:'Loading…',saved:'Reminder scheduled',
      opened:'Marker cleared for everyone',error:'Could not complete the action',loadError:'Could not load reminders',retry:'Retry',more:'Showing the first 100 reminders'}
  };
  const language=()=>window.tenantI18n?.language||'az';
  const t=key=>(copy[language()]||copy.az)[key];
  const $=id=>document.getElementById(id);
  const node=(tag,content)=>{const el=document.createElement(tag);if(content!=null)el.textContent=content;return el;};
  const button=(label,action)=>{const el=node('button',label);el.type='button';el.className='outline';el.onclick=action;return el;};
  const current=()=>window.tenantOperator?.currentDialog;
  const routeKey=r=>JSON.stringify([r?.provider,String(r?.channel_id),r?.conversation_id,Number(r?.lead_id)]);
  let createControl,modal,feed=null,request=null,epoch=0,rows=[],paused=false,lastBellState='',feedStatus='loading';
  const scope=()=>{
    const p=window.tenantProfileSnapshot;
    const m=window.tenantOperator?.member;
    if(p?.capabilities?.shared_dialog_reminders!==true||m?.active!==true||!window.tenantOperator?.hasRight('dialogs'))return null;
    if(String(m.tenant_id)!==String(p.member?.tenant_id)||String(m.telegram_id)!==String(p.member?.telegram_id))return null;
    return {expected_tenant_id:String(m.tenant_id),expected_user_id:String(m.telegram_id)};
  };
  const active=()=>!paused&&!document.hidden;
  const dialogueActive=()=>active()&&!!$('view-dialogs')?.classList.contains('active');
  const canOpenList=()=>!paused&&!!scope()&&feedStatus!=='denied';
  const notificationCount=()=>canOpenList()&&feed?(feed.reminders||[]).filter(row=>row.due).length:null;
  const notificationLabel=count=>t('list')+(count?' ('+count+')':'');
  function notificationEntry(bell,beforeOpen){
    if(!canOpenList())return null;
    const count=notificationCount();
    const control=button(notificationLabel(count),()=>{
      if(!canOpenList()||!active())return;
      beforeOpen?.();renderList(box(bell,'list',t('list')));void refresh();
    });
    control.dataset.notificationFocus='shared-reminders';control.setAttribute('aria-haspopup','dialog');
    return control;
  }
  const valid=(stamp,expected)=>active()&&stamp===epoch&&JSON.stringify(scope())===JSON.stringify(expected);
  async function api(expected,data){
    const response=await fetch(endpoint+(data?'':'?'+new URLSearchParams(expected)),{
      method:data?'POST':'GET',credentials:'same-origin',cache:'no-store',
      ...(data?{headers:{'Content-Type':'application/json'},body:JSON.stringify({...data,...expected})}:{})
    });
    const p=await response.json().catch(()=>null);
    if(!response.ok||p?.success!==true){const error=Error(p?.error||t('error'));error.status=response.status;throw error;}
    return p.result;
  }
  function close(){
    const old=modal;modal=null;
    if(!old)return;
    old.dialog.remove();
    if(old.source?.isConnected&&active())old.source.focus({preventScroll:true});
  }
  function box(source,type,title){
    close();
    const dialog=node('dialog'),header=node('header'),heading=node('h2',title),status=node('p'),body=node('section');
    dialog.id='sharedDialogReminderModal';heading.id='sharedDialogReminderTitle';
    dialog.setAttribute('aria-labelledby',heading.id);status.setAttribute('role','status');
    header.append(heading,button(t('close'),close));dialog.append(header,status,body);
    dialog.addEventListener('cancel',e=>{e.preventDefault();e.stopImmediatePropagation();close();});
    dialog.addEventListener('keydown',e=>{if(e.key==='Escape')e.stopPropagation();});
    document.body.append(dialog);dialog.showModal();
    return modal={dialog,source,type,body,status,busy:false};
  }
  function markRows(next){
    rows=next;
    const available=new Set((feed?.reminders||[]).filter(r=>r.due&&r.dialog).map(r=>routeKey(r.dialog)));
    for(const row of document.querySelectorAll('#operatorRows [data-open-operator]')){
      const group=rows[Number(row.dataset.openOperator)]||[];
      let marker=row.querySelector('[data-shared-reminder]');
      if(!group.some(r=>available.has(routeKey(r)))){marker?.remove();continue;}
      if(!marker){
        marker=node('span');marker.dataset.sharedReminder='';marker.className='operator-shared-reminder-badge';
        row.querySelector('.operator-row-indicators')?.append(marker);
      }
      marker.textContent='◷';marker.title=t('due');marker.setAttribute('aria-label',t('due'));
    }
  }
  function paint(){
    const count=notificationCount(),next=JSON.stringify([canOpenList(),count,language()]);
    if(next!==lastBellState){
      lastBellState=next;document.dispatchEvent(new CustomEvent('tenant-shared-reminders-changed'));
    }
    if(createControl?.isConnected){
      createControl.hidden=!dialogueActive()||!canOpenList()||current()?.shared_reminder?.can_schedule!==true;
      createControl.textContent=t('schedule');
      const host=createControl.closest('#operatorClientSharedReminderHost');if(host)host.hidden=createControl.hidden;
    }
    markRows(rows);
    if(modal?.type==='list'&&!modal.busy)renderList(modal);
  }
  function refresh(){
    const expected=scope(),stamp=epoch;
    if(!expected||!active()||request||feedStatus==='denied')return;
    const token={};request=token;
    if(!feed&&feedStatus!=='loading'){feedStatus='loading';paint();}
    return api(expected).then(result=>{
      if(valid(stamp,expected)&&request===token){feed=result;feedStatus='ready';}
    },error=>{
      if(valid(stamp,expected)&&request===token){
        feed=null;
        if([401,403,404].includes(error.status)){feedStatus='denied';close();}
        else feedStatus='error';
      }
    }).finally(()=>{
      if(request===token){request=null;paint();}
    });
  }
  function removeMarker(id){
    request=null;
    if(feed)feed={...feed,reminders:feed.reminders.filter(row=>row.id!==id)};
    paint();void refresh();
  }
  function sayTime(el,value){
    el.dateTime=value;
    el.textContent=new Intl.DateTimeFormat(language(),{dateStyle:'medium',timeStyle:'short'}).format(new Date(value));
  }
  function renderList(view){
    view.body.replaceChildren();
    if(!feed?.reminders.length){
      if(feedStatus==='error'){
        const error=node('p',t('loadError'));error.setAttribute('role','alert');
        view.body.append(error,button(t('retry'),()=>void refresh()));
      }else view.body.append(node('p',feed?t('empty'):t('loading')));
      return;
    }
    for(const row of feed.reminders){
      const card=node('article'),title=node('strong',row.title||t('due')),time=node('time');
      sayTime(time,row.due_at);card.append(title,time);
      if(row.due)card.append(button(row.dialog?t('chat'):t('open'),()=>void openFromList(view,row)));
      view.body.append(card);
    }
    if(feed.has_more)view.body.append(node('p',t('more')));
  }
  async function openFromList(view,row){
    const expected=scope(),stamp=epoch;
    if(view!==modal||view.busy||!expected||!active())return;
    view.busy=true;view.status.textContent=t('loading');
    try{
      if(row.dialog){
        if(!dialogueActive())window.tenantAppNavigate?.('dialogs',false);
        if(!dialogueActive()||!valid(stamp,expected))throw Error(t('error'));
        const visible=await window.tenantOperator.openDialog(row.dialog);
        if(!visible||routeKey(current())!==routeKey(row.dialog))throw Error(t('error'));
      }
      if(view!==modal||!valid(stamp,expected))return;
      const result=await api(expected,{action:'open',id:row.id});
      if(view!==modal||!valid(stamp,expected))return;
      view.type='opened';view.status.textContent=t('opened');view.body.replaceChildren();
      const time=node('time');sayTime(time,result.due_at);
      view.body.append(node('strong',result.title||t('due')),time);
      if(result.note)view.body.append(node('p',result.note));
      removeMarker(row.id);
    }catch(error){
      if(view===modal&&valid(stamp,expected)){view.status.textContent=error.message;void refresh();}
    }finally{view.busy=false;}
  }
  async function dialogOpened(route){
    const expected=scope(),stamp=epoch,id=route?.shared_reminder?.id;
    if(!expected||!dialogueActive()||!canOpenList()||!id)return;
    try{
      await api(expected,{action:'open',id});
      if(valid(stamp,expected))removeMarker(id);
    }catch{if(valid(stamp,expected))void refresh();}
  }
  function schedule(source){
    const expected=scope(),stamp=epoch,route=current();
    if(!expected||!dialogueActive()||!canOpenList()||route?.shared_reminder?.can_schedule!==true)return;
    const view=box(source,'schedule',t('schedule')),form=node('form'),date=node('input'),note=node('textarea');
    view.route=routeKey(route);
    date.type='datetime-local';date.required=true;note.maxLength=280;note.rows=2;
    const dateLabel=node('label',t('date')),noteLabel=node('label',t('note')),submit=node('button',t('save'));
    dateLabel.append(date);noteLabel.append(note);submit.type='submit';submit.className='primary';
    form.append(dateLabel,noteLabel,submit);view.body.append(form);date.focus();
    let id,signature;
    form.onsubmit=async event=>{
      event.preventDefault();
      if(view!==modal||!valid(stamp,expected)||!dialogueActive()||!canOpenList()||routeKey(current())!==view.route||submit.disabled)return;
      const due=new Date(date.value);
      if(!Number.isFinite(+due)||+due<=Date.now()){view.status.textContent=t('error');return;}
      const due_at=due.toISOString(),text=note.value.trim(),next=JSON.stringify([due_at,text]);
      if(signature!==next){id=crypto.randomUUID();signature=next;}
      submit.disabled=true;view.status.textContent=t('loading');
      try{
        await api(expected,{action:'schedule',id,note:text,due_at,provider:route.provider,
          channel_id:String(route.channel_id),conversation_id:route.conversation_id,lead_id:Number(route.lead_id)});
        if(view===modal&&valid(stamp,expected)){
          view.status.textContent=t('saved');form.remove();request=null;void refresh();
        }
      }catch(error){if(view===modal&&valid(stamp,expected))view.status.textContent=error.message;}
      finally{submit.disabled=false;}
    };
  }
  function attach(){
    const host=$('operatorClientSharedReminderHost'),route=current();
    if(dialogueActive()&&canOpenList()&&host&&route?.shared_reminder?.can_schedule===true&&
        (!createControl?.isConnected||!host.contains(createControl))){
      createControl=button(t('schedule'),()=>schedule(createControl));
      createControl.id='operatorSharedReminderCreate';createControl.setAttribute('aria-haspopup','dialog');
      host.replaceChildren(createControl);host.hidden=false;
    }
    if(host&&!(createControl?.isConnected&&host.contains(createControl)))host.hidden=true;
    if(modal?.route&&modal.route!==routeKey(current()))close();
    paint();
  }
  function reset(block=false){
    epoch++;paused=block;feed=null;request=null;feedStatus='loading';close();paint();
  }
  function wake(){attach();void refresh();}
  const style=node('style');
  style.textContent=`
    #operatorClientSharedReminderHost[hidden],#operatorSharedReminderCreate[hidden]{display:none!important}
    #operatorClientSharedReminderHost{margin-top:4px}
    #operatorCompanyPanel #operatorSharedReminderCreate{width:100%;min-height:36px;text-align:start}
    .operator-shared-reminder-badge{border-radius:9px;padding-inline:5px;background:var(--paper);color:var(--app-warning-ink,#9b5f08);font-weight:700}
    #sharedDialogReminderModal{box-sizing:border-box;width:min(440px,calc(100% - 24px));max-height:calc(100dvh - 24px);padding:16px;border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink);overflow:auto}
    #sharedDialogReminderModal::backdrop{background:#071d1688}
    #sharedDialogReminderModal header{display:flex;justify-content:space-between;align-items:center;gap:8px}
    #sharedDialogReminderModal h2{font-size:1.1rem;margin:0}
    #sharedDialogReminderModal label{display:grid;gap:4px;margin:0 0 10px}
    #sharedDialogReminderModal input,#sharedDialogReminderModal textarea{box-sizing:border-box;width:100%;min-width:0;font-size:16px}
    #sharedDialogReminderModal time{display:block;margin:2px 0 7px;color:var(--muted)}
    #sharedDialogReminderModal article{padding:9px 0;border-bottom:1px solid var(--line)}
    #sharedDialogReminderModal section p{white-space:pre-wrap;overflow-wrap:anywhere}
  `;document.head.append(style);
  window.tenantSharedReminders={dialogOpened,markRows,notificationCount,notificationLabel,notificationEntry};
  document.addEventListener('tenant-profile',()=>{reset();queueMicrotask(wake);});
  for(const evt of ['tenant-auth-pending','tenant-auth-invalid'])document.addEventListener(evt,()=>reset(true));
  document.addEventListener('tenant-view',event=>{if(event.detail?.view!=='dialogs'&&modal?.type==='schedule')close();queueMicrotask(wake);});
  document.addEventListener('tenant-chat-attachments-ready',attach);
  document.addEventListener('tenant-employee-status-changed',()=>{reset(paused);wake();});
  document.addEventListener('tenant-language',()=>{close();attach();});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)wake();});
  window.setInterval(wake,30000);wake();
})();
