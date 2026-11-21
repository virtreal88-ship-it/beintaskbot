/* In-app personal reminder controls. No browser notification or messaging transport. */
(()=>{
  const endpoint='/api/platform/ai/dialogs/personal-reminders';
  const labels={
    ru:['Напомнить себе','Мои напоминания','Заметка','Дата и время','Сохранить','Закрыть','Готово','Открыть диалог','Нет незавершённых напоминаний.','Пора','Запланировано','Не удалось выполнить действие.','Часовой пояс устройства','Показаны первые 100 напоминаний.'],
    az:['Özümə xatırlat','Xatırlatmalarım','Qeyd','Tarix və vaxt','Saxla','Bağla','Tamamla','Dialoqu aç','Tamamlanmamış xatırlatma yoxdur.','Vaxtı çatıb','Planlaşdırılıb','Əməliyyat alınmadı.','Cihazın saat qurşağı','İlk 100 xatırlatma göstərilir.'],
    en:['Remind myself','My reminders','Note','Date and time','Save','Close','Done','Open dialogue','No pending reminders.','Due','Scheduled','Could not complete the action.','Device time zone','Showing the first 100 reminders.']
  };
  const t=i=>(labels[window.tenantI18n?.language]||labels.az)[i];
  const newLabel=()=>({ru:'Новое напоминание',az:'Yeni xatırlatma',en:'New reminder'}[window.tenantI18n?.language]||'Yeni xatırlatma');
  const $=id=>document.getElementById(id);
  const element=(tag,txt)=>{const e=document.createElement(tag);if(txt!=null)e.textContent=txt;return e;};
  const button=(txt,fn)=>{const b=element('button',txt);b.type='button';b.className='outline';b.onclick=fn;return b;};
  const identity=()=>{
    const snapshot=window.tenantProfileSnapshot;
    if(snapshot?.capabilities?.personal_dialog_reminders!==true)return null;
    const m=window.tenantOperator?.member;
    if(!m?.tenant_id||m.telegram_id==null||m.active===false||!window.tenantOperator?.hasRight('dialogs'))return null;
    if(String(m.tenant_id)!==String(snapshot.member?.tenant_id)||String(m.telegram_id)!==String(snapshot.member?.telegram_id))return null;
    return {tenant:m.tenant_id,user:m.telegram_id,key:String(m.tenant_id)+':'+String(m.telegram_id)};
  };
  const active=()=>!!$('view-dialogs')?.classList.contains('active');
  const route=()=>window.tenantOperator?.currentDialog;
  const supported=r=>r?.provider==='connect';
  const routeKey=r=>JSON.stringify([r?.provider,r?.channel_id,r?.conversation_id,r?.lead_id]);
  let listButton=null,createButton=null,modal=null,listContent=null,cache=null,epoch=0,serial=0,paused=false,unsupported=false;
  const same=(who,generation)=>!paused&&active()&&identity()?.key===who.key&&generation===epoch;
  const pending=()=>document.hidden||paused||!active()||!identity();
  async function api(who,data){
    const expected={expected_tenant_id:String(who.tenant),expected_user_id:String(who.user)};
    const response=await fetch(endpoint+(data?'':'?'+new URLSearchParams(expected)),{
      cache:'no-store',credentials:'same-origin',method:data?'POST':'GET',
      ...(data?{headers:{'Content-Type':'application/json'},body:JSON.stringify({...data,...expected})}:{})
    });
    const payload=await response.json().catch(()=>null);
    if(!response.ok){const err=new Error(t(11));err.status=response.status;err.code=payload?.error_code;throw err;}
    if(payload?.success!==true)throw new Error(t(11));
    return payload.result;
  }
  function close(){
    const old=modal;modal=null;listContent=null;
    if(!old)return;
    old.node.remove();
    if(old.source?.isConnected&&active())old.source.focus({preventScroll:true});
  }
  function dialog(source,title){
    close();
    const node=element('dialog'),heading=element('h2',title),status=element('p');
    node.id='personalDialogReminderModal';heading.id='personalDialogReminderTitle';
    node.setAttribute('aria-labelledby',heading.id);
    status.setAttribute('role','status');
    const closeButton=button(t(5),close);
    const header=element('header');header.append(heading,closeButton);
    node.append(header,status);document.body.append(node);
    node.addEventListener('cancel',e=>{e.preventDefault();e.stopImmediatePropagation();close();});
    node.addEventListener('keydown',e=>{if(e.key==='Escape')e.stopPropagation();});
    node.addEventListener('click',e=>{if(e.target===node)close();});
    node.showModal();
    return (modal={node,source,status});
  }
  const showError=box=>{if(box===modal)box.status.textContent=t(11);};
  function stale(error,who,generation){
    if(same(who,generation)&&error.code==='reminder_session_unavailable'){reset(true);return true;}
    return false;
  }
  function update(){
    if(listButton?.isConnected){
      listButton.hidden=!cache;
      listButton.textContent=t(1)+(cache?.due_count?' ('+t(9)+': '+cache.due_count+')':'');
      listButton.dataset.due=String(!!cache?.due_count);
    }
    if(createButton?.isConnected){createButton.hidden=!cache||!supported(route());createButton.textContent=t(0);}
    if(listContent&&modal&&!modal.busy)renderList();
  }
  async function refresh(){
    if(pending()||unsupported)return;
    const who=identity(),generation=epoch,request=++serial;
    try{
      const result=await api(who);
      if(!same(who,generation)||request!==serial)return;
      if(cache&&JSON.stringify(result)===JSON.stringify(cache))return;
      cache=result;
    }catch(error){
      if(!same(who,generation)||request!==serial)return;
      if(stale(error,who,generation))return;
      cache=null;
      unsupported=error.status===404;
      if(modal)showError(modal);
    }
    update();
  }
  function create(source,linked){
    const who=identity(),current=linked?route():null,generation=epoch;
    if(!cache||!who||(linked&&!supported(current))||!active())return;
    const key=linked?routeKey(current):null;
    const date=element('input');date.type='datetime-local';date.required=true;
    const note=element('textarea');note.maxLength=280;note.rows=3;note.required=true;
    const localZone=Intl.DateTimeFormat().resolvedOptions().timeZone;
    const noteLabel=element('label',t(2)),dateLabel=element('label',t(3)+(localZone?' · '+t(12)+': '+localZone:''));
    noteLabel.append(note);dateLabel.append(date);
    const box=dialog(source,linked?t(0):newLabel()),form=element('form');
    box.routeKey=key;
    const save=element('button',t(4));save.type='submit';save.className='primary';
    form.append(noteLabel,dateLabel,save);box.node.append(form);note.focus();
    let signature='',id='';
    form.addEventListener('submit',async e=>{
      e.preventDefault();
      if(box!==modal||save.disabled||!same(who,generation)||(linked&&key!==routeKey(route())))return;
      const due=new Date(date.value),trimmed=note.value.trim();
      if(!trimmed||!Number.isFinite(+due)||+due<=Date.now()){showError(box);return;}
      const due_at=due.toISOString(),next=JSON.stringify([trimmed,due_at]);
      if(signature!==next){id=crypto.randomUUID();signature=next;}
      save.disabled=true;date.disabled=true;note.disabled=true;
      try{
        const target=linked?{provider:current.provider,
          channel_id:String(current.channel_id||''),conversation_id:String(current.conversation_id||''),
          lead_id:Number(current.lead_id)}:{};
        await api(who,{action:'create',id,note:trimmed,due_at,...target});
        if(!same(who,generation))return;
        if(box===modal)close();
        await refresh();
      }catch(error){if(!stale(error,who,generation))showError(box);}
      finally{save.disabled=false;date.disabled=false;note.disabled=false;}
    });
  }
  function renderList(){
    const box=modal,container=listContent;
    if(!box||!container)return;
    container.replaceChildren();
    const rows=cache?.reminders||[];
    if(!rows.length){container.append(element('p',cache?t(8):t(11)));return;}
    const who=identity(),generation=epoch;
    if(!who)return;
    const act=async(action,row,control)=>{
      if(box!==modal||box.busy||!same(who,generation))return;
      const current=window.tenantOperator.dialogRevision;
      box.status.textContent='';box.busy=true;control.disabled=true;
      try{
        const result=await api(who,{action,id:row.id});
        if(box!==modal||!same(who,generation))return;
        if(action==='open'){
          if(current!==window.tenantOperator.dialogRevision)return;
          close();void window.tenantOperator.openDialog(result.dialog);
        }else await refresh();
      }catch(error){if(!stale(error,who,generation))showError(box);}
      finally{box.busy=false;control.disabled=false;if(box===modal&&action==='complete'&&!box.status.textContent)renderList();}
    };
    for(const row of rows){
      const item=element('article'),note=element('p',row.note),time=element('time');
      const at=new Date(row.due_at);
      time.dateTime=row.due_at;time.textContent=new Intl.DateTimeFormat(window.tenantI18n?.language||'az',
        {dateStyle:'medium',timeStyle:'short'}).format(at)+' · '+t(row.due?9:10);
      if(row.due)item.dataset.due='true';
      const controls=element('div');
      const open=button(t(7),()=>void act('open',row,open)),done=button(t(6),()=>void act('complete',row,done));
      if(row.can_open===true)controls.append(open);
      controls.append(done);item.append(note,time,controls);container.append(item);
    }
    if(cache.total>rows.length)container.append(element('p',t(13)));
  }
  function attach(){
    if(pending())return;
    const pane=document.querySelector('#operatorWorkspace .operator-list-pane');
    if(pane&&!listButton?.isConnected){
      listButton=button(t(1),()=>{
        if(!cache)return;
        const box=dialog(listButton,t(1));
        const add=button(newLabel(),()=>create(listButton,false));
        add.id='operatorPersonalReminderStandalone';
        box.node.append(add);
        listContent=element('section');box.node.append(listContent);
        renderList();void refresh();
      });
      listButton.id='operatorPersonalRemindersOpen';listButton.hidden=true;listButton.setAttribute('aria-live','polite');
      pane.querySelector('.operator-filters')?.after(listButton);
    }
    const menu=$('operatorMoreActions')?.querySelector('.operator-action-menu');
    if(menu&&(!createButton?.isConnected||!menu.contains(createButton))){
      createButton=button(t(0),()=>{$('operatorMoreActions').open=false;create(createButton,true);});
      createButton.id='operatorPersonalReminderCreate';createButton.hidden=true;
      createButton.setAttribute('role','menuitem');menu.append(createButton);
    }
    if(modal?.routeKey&&modal.routeKey!==routeKey(route()))close();
    update();
  }
  function reset(block=false){paused=block;epoch++;cache=null;unsupported=false;close();update();}
  const style=element('style');
  style.textContent=`
    #operatorPersonalRemindersOpen{align-self:flex-start;max-width:100%;margin:0 0 6px;min-height:36px}
    #operatorPersonalRemindersOpen[data-due=true],#personalDialogReminderModal [data-due=true] time{color:var(--app-warning-ink,#9b5f08);font-weight:650}
    #personalDialogReminderModal{box-sizing:border-box;width:min(480px,calc(100% - 24px));max-height:calc(100dvh - 24px);padding:16px;border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink);overflow:auto}
    #personalDialogReminderModal::backdrop{background:#071d1688}
    #personalDialogReminderModal header,#personalDialogReminderModal article>div{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
    #personalDialogReminderModal h2{font-size:1.1rem;margin:0}
    #personalDialogReminderModal label{display:grid;gap:4px;margin:0 0 12px}
    #personalDialogReminderModal textarea,#personalDialogReminderModal input{box-sizing:border-box;width:100%;min-width:0;font-size:16px;background:var(--paper);color:var(--ink)}
    #personalDialogReminderModal button{min-height:44px}
    #personalDialogReminderModal article{padding:10px 0;border-bottom:1px solid var(--line)}
    #personalDialogReminderModal article p{white-space:pre-wrap;overflow-wrap:anywhere;margin:0 0 3px}
    #personalDialogReminderModal article time{display:block;margin:0 0 6px;color:var(--muted)}
    #personalDialogReminderModal article>div{justify-content:flex-start}
    #operatorPersonalRemindersOpen[hidden],#operatorPersonalReminderCreate[hidden]{display:none!important}
  `;document.head.append(style);
  function wake(){attach();void refresh();}
  document.addEventListener('tenant-profile',()=>{reset();queueMicrotask(wake);});
  for(const event of ['tenant-auth-pending','tenant-auth-invalid'])document.addEventListener(event,()=>reset(true));
  document.addEventListener('tenant-view',event=>{if(event.detail?.view!=='dialogs')reset(paused);else queueMicrotask(wake);});
  document.addEventListener('tenant-chat-attachments-ready',attach);
  for(const event of ['tenant-chat-open','tenant-chat-close'])document.addEventListener(event,()=>{if(modal?.routeKey)close();});
  document.addEventListener('tenant-language',()=>{close();attach();});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)wake();});
  window.setInterval(wake,30000);wake();
})();
