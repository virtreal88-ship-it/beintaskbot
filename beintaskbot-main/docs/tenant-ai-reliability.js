/* Read-only operator diagnostics. Stale responses cannot cross company changes. */
(()=>{
 let profile=null,epoch=0;
 const labels={received:'Получено',queued:'В очереди',processing:'Обрабатывается',generated:'Ответ создан',sent:'Отправлен',error:'Ошибка',skipped:'Пропущено'};
 const node=(tag,text)=>{const e=document.createElement(tag);e.textContent=String(text??'');return e};
 function panel(){let box=document.getElementById('aiReliability');if(!box){const parent=document.getElementById('aiAgentsOverview');if(!parent)return null;box=node('section','');box.id='aiReliability';box.className='panel';box.setAttribute('aria-label','Работоспособность AI');box.setAttribute('aria-live','polite');parent.insertAdjacentElement('afterend',box)}return box}
 async function refresh(){
  const current=profile,request=++epoch,box=panel();if(!box||!current)return;
  box.replaceChildren(node('h2','Работоспособность AI'),node('p','Проверяю…'));
  try{
   const response=await fetch('/api/platform/ai/reliability?limit=30',{credentials:'same-origin',cache:'no-store'});
   const data=await response.json();if(request!==epoch||profile!==current)return;
   if(!response.ok||!data.success)throw Error('Диагностика недоступна. Проверьте права администратора и соединение.');
   const {health,metrics,messages}=data.result;
   box.replaceChildren(node('h2','Работоспособность AI'),node('p',health.label));
   for(const text of health.blockers||[])box.append(node('p',text));
   box.append(node('p',`В очереди: ${metrics.queued} · Обрабатывается: ${metrics.processing} · Просрочено: ${metrics.overdue} · Зависшие задачи: ${metrics.expired_leases}`));
   box.append(node('p',`За 24 часа: ошибки AI — ${metrics.ai_errors_24h}; отправки без подтверждения — ${metrics.unsuccessful_sends_24h}. Неизвестный результат автоматически не повторяется.`));
   box.append(node('p',`Режим компании: ${health.operating_mode}. Автоотправка: ${health.automatic_send_enabled?'разрешена, зависит также от режима агента':'выключена'}.`));
   const reload=node('button','Обновить диагностику');reload.type='button';reload.onclick=refresh;box.append(reload);
   const list=node('ol','');
   for(const item of messages){const li=node('li','');li.append(node('strong',`${labels[item.status]||item.status} · ${item.provider} · ${item.message_id}`));li.append(node('p',item.reason||'Ошибок на этом этапе не зафиксировано'));const stages=Object.entries(item.checkpoints).filter(([,done])=>done).map(([key])=>labels[key]||key);li.append(node('small',`${item.received_at} · ${stages.join(' → ')} · Попыток: ${item.attempts}${item.automatic_retry?' · безопасный повтор разрешён':''}`));list.append(li)}
   box.append(list);if(!messages.length)box.append(node('p','Входящих AI-событий пока нет.'));
  }catch(error){if(request===epoch&&profile===current)box.replaceChildren(node('h2','Работоспособность AI'),node('p','Диагностика недоступна. Проверьте права администратора и соединение.'))}
 }
 document.addEventListener('tenant-profile',event=>{epoch++;profile=event.detail?.member||null;document.getElementById('aiReliability')?.remove()});
 const anchor=document.querySelector('[data-ai-overview-diagnostics]');
 if(anchor){const open=node('button','Статус AI');open.type='button';open.className='outline';open.setAttribute('data-ai-reliability-open','');anchor.insertAdjacentElement('afterend',open)}
 document.addEventListener('click',event=>{if(event.target.closest?.('[data-ai-reliability-open]'))refresh()});
 const initial=epoch;fetch('/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():null).then(data=>{if(initial===epoch&&!profile)profile=data?.member||null}).catch(()=>{});
})();
