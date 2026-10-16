/* Internal support queue embedded in the existing AI Agents page. */
(()=>{
  const $=id=>document.getElementById(id);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels={new:'Новое',waiting:'Ожидает сотрудника',in_progress:'В работе',resolved:'Решено'};
  const endpoint=(window.TENANT_AI_AGENTS_CONFIG?.endpoint||'/api/platform/ai/')+'support-tickets';
  let member=null,epoch=0,listRevision=0,detailRevision=0,members=[],selected=null,nextOffset=null,nextBefore=null,rows=[];
  const identity=person=>person?String(person.tenant_id)+':'+String(person.telegram_id):'';
  const date=value=>value?new Date(value).toLocaleString('ru-RU'):'—';
  function notice(text){const host=$('supportNotice');if(host)host.textContent=text||'';}
  async function api(path='',method='GET',data){
    const current=member,version=epoch;
    const response=await fetch(endpoint+path,{method,credentials:'same-origin',cache:'no-store',headers:data?{'Content-Type':'application/json'}:{},
      ...(data?{body:JSON.stringify({...data,expected_tenant_id:current?.tenant_id,expected_user_id:current?.telegram_id})}:{})});
    const payload=await response.json();
    if(version!==epoch||identity(current)!==identity(member))throw new Error('Компания изменилась. Обновите очередь.');
    if(!response.ok||!payload.success)throw new Error(payload.error||'Очередь недоступна.');
    return payload.result;
  }
  function install(){
    const page=$('view-ai_agents');if(!page||$('supportInbox'))return;
    const panel=document.createElement('article');panel.id='supportInbox';panel.className='panel';
    panel.innerHTML=`<div class="support-heading"><div><h2>Обращения</h2><p class="sub">Утренняя смена: нерешённые обращения, сначала переданные AI человеку.</p></div><button type="button" class="outline" id="supportRefresh">Обновить</button></div>
      <div class="support-filters"><label>Статус <select id="supportStatus"><option value="unresolved">Все нерешённые</option>${Object.entries(labels).map(([v,t])=>`<option value="${v}">${t}</option>`).join('')}<option value="all">Все обращения</option></select></label>
      <label>Ответственный <select id="supportAssignee"><option value="">Все сотрудники</option></select></label></div>
      <p id="supportNotice" role="status" aria-live="polite"></p><div id="supportRows"></div><button type="button" id="supportMore" class="outline" hidden>Ещё обращения</button><div id="supportDetail" hidden></div>`;
    const overview=$('aiAgentsOverview');if(overview)overview.after(panel);else page.prepend(panel);
    const style=document.createElement('style');style.textContent=`#supportInbox{margin:16px 0}#supportInbox h2{margin:0}#supportInbox .support-heading,#supportInbox .support-filters,#supportInbox .support-actions{display:flex;gap:12px;justify-content:space-between;flex-wrap:wrap}#supportInbox .support-filters{justify-content:flex-start;margin:15px 0}#supportInbox label{display:grid;gap:5px}#supportInbox .support-row{display:grid;grid-template-columns:minmax(150px,1.1fr) minmax(150px,1.5fr) minmax(130px,1fr);gap:12px;padding:14px 0;border-bottom:1px solid var(--line)}#supportInbox small{display:block;color:var(--muted)}#supportDetail{border-top:2px solid var(--line);margin-top:20px;padding-top:20px}#supportDetail p,#supportHistory{white-space:pre-wrap;overflow-wrap:anywhere}#supportInbox .support-message{padding:10px;margin:8px 0;border-radius:10px;background:var(--paper)}#supportInbox .support-message[data-direction=outgoing]{margin-left:10%;background:#dff7ec}#supportInbox textarea{width:100%;box-sizing:border-box}#supportInbox .support-edit{display:grid;gap:12px;margin:16px 0}@media(max-width:650px){#supportInbox .support-row{grid-template-columns:1fr}#supportInbox select{max-width:100%}}`;
    document.head.appendChild(style);
    $('supportRefresh').onclick=()=>load();$('supportStatus').onchange=()=>load();$('supportAssignee').onchange=()=>load();$('supportMore').onclick=()=>load(true);
  }
  function options(value){return '<option value="">Без ответственного</option>'+members.map(m=>`<option value="${esc(m.telegram_id)}" ${String(value)===String(m.telegram_id)?'selected':''}>${esc(m.display_name||m.telegram_id)}</option>`).join('');}
  async function load(append=false){
    if(!member)return;install();const revision=++listRevision,currentEpoch=epoch;
    try{
      const offset=append?nextOffset:0;
      const result=await api('?'+new URLSearchParams({status:$('supportStatus').value,assignee:$('supportAssignee').value,offset:offset||0}));
      if(revision!==listRevision)return;
      members=result.members;rows=append?rows.concat(result.tickets):result.tickets;nextOffset=result.next_offset;
      const filter=$('supportAssignee'),value=filter.value;
      filter.innerHTML='<option value="">Все сотрудники</option><option value="unassigned">Без ответственного</option>'+members.map(m=>`<option value="${esc(m.telegram_id)}">${esc(m.display_name||m.telegram_id)}</option>`).join('');filter.value=value;
      $('supportRows').innerHTML=rows.map(t=>`<div class="support-row"><div><b>${esc(t.contact_name||t.phone||t.conversation_id)}</b><small>${esc(t.channel_name||t.channel_id)} · WhatsApp</small><small>Создано: ${esc(date(t.created_at))}</small></div><div>${esc(t.description)}<small>${t.source==='ai_handoff'?'Передано AI человеку':'Создано сотрудником'}</small></div><div><b>${esc(labels[t.status])}</b><small>${esc(t.assignee_name||'Без ответственного')}</small><small>Последнее сообщение: ${esc(date(t.last_message_at))}</small><button type="button" class="outline" data-support-id="${esc(t.id)}">Открыть обращение</button></div></div>`).join('')||'<p class="sub">Нет обращений по выбранным фильтрам.</p>';
      $('supportMore').hidden=nextOffset===null;
      $('supportRows').querySelectorAll('[data-support-id]').forEach(button=>button.onclick=()=>open(button.dataset.supportId));notice('');
    }catch(error){if(currentEpoch===epoch&&revision===listRevision)notice(error.message);}
  }
  function messages(items){return items.map(m=>`<div class="support-message" data-direction="${esc(m.direction)}"><small>${m.direction==='incoming'?'Клиент':'Сотрудник / AI'} · ${esc(date(m.happened_at||m.created_at))}</small>${esc(m.body||'Вложение / аудио: откройте исходный диалог')}<small>${esc(m.message_type||'text')}</small></div>`).join('');}
  function renderDetail(result){
    const t=result.ticket;selected=t.id;nextBefore=result.next_before;
    const host=$('supportDetail');host.hidden=false;
    host.innerHTML=`<div class="support-heading"><h3>${esc(t.contact_name||t.phone||t.conversation_id)}</h3><button type="button" class="outline" id="supportClose">Закрыть</button></div>
      <p>${esc(t.channel_name||t.channel_id)} · ${esc(t.conversation_id)}</p><p><b>Причина передачи:</b> ${esc(t.reason)}</p>
      <h4>AI-сводка</h4><p>${esc(t.summary||({'pending':'Сводка ещё не подготовлена.','generating':'Сводка начата; обновите обращение.','unavailable':'AI-сводка недоступна. Обращение сохранено — проверьте историю.'}[t.summary_state]||'Нет сводки.'))}</p>
      ${['pending','unavailable'].includes(t.summary_state)?'<button type="button" class="outline" id="supportSummary">Подготовить AI-сводку</button>':''}
      <div class="support-edit"><label>Краткое описание<textarea id="supportDescription" maxlength="4000" rows="2">${esc(t.description)}</textarea></label><label>Что уже предложил агент<textarea id="supportSuggestions" maxlength="4000" rows="3">${esc(t.suggestions)}</textarea></label><label>Что требуется сотруднику<textarea id="supportNextSteps" maxlength="4000" rows="3">${esc(t.next_steps)}</textarea></label>
      <label>Статус<select id="supportEditStatus">${Object.entries(labels).map(([v,label])=>`<option value="${v}" ${v===t.status?'selected':''}>${label}</option>`).join('')}</select></label><label>Ответственный<select id="supportEditAssignee">${options(t.assigned_to)}</select></label></div>
      <div class="support-actions"><button type="button" class="primary" id="supportTake">Взять в работу</button><button type="button" class="outline" id="supportSave">Сохранить</button><button type="button" class="outline" id="supportResolve">Отметить решённым</button><button type="button" class="outline" id="supportOpenChat">Открыть исходный диалог</button></div>
      <h4>История диалога</h4><button type="button" class="outline" id="supportEarlier" ${nextBefore?'':'hidden'}>Более ранние сообщения</button><div id="supportHistory">${messages(result.messages)||'<p>История пока пуста.</p>'}</div>`;
    $('supportClose').onclick=()=>{detailRevision++;selected=null;host.hidden=true;host.innerHTML='';};
    $('supportSave').onclick=()=>mutate(t.id,{status:$('supportEditStatus').value,assigned_to:$('supportEditAssignee').value||null,description:$('supportDescription').value,suggestions:$('supportSuggestions').value,next_steps:$('supportNextSteps').value});
    $('supportTake').onclick=()=>mutate(t.id,{status:'in_progress',assigned_to:member.telegram_id});
    $('supportResolve').onclick=()=>mutate(t.id,{status:'resolved'});
    $('supportOpenChat').onclick=()=>document.dispatchEvent(new CustomEvent('tenant-support-open',{detail:{lead_id:t.lead_id}}));
    if($('supportSummary'))$('supportSummary').onclick=()=>action(t.id,'/summary','POST',{});
    $('supportEarlier').onclick=async()=>{const revision=detailRevision,currentEpoch=epoch;try{const page=await api('/'+encodeURIComponent(t.id)+'?before='+encodeURIComponent(nextBefore));if(selected!==t.id||revision!==detailRevision)return;$('supportHistory').insertAdjacentHTML('afterbegin',messages(page.messages));nextBefore=page.next_before;$('supportEarlier').hidden=!nextBefore;}catch(error){if(currentEpoch===epoch)notice(error.message);}};
  }
  async function open(id){const revision=++detailRevision,currentEpoch=epoch;try{const result=await api('/'+encodeURIComponent(id));if(revision!==detailRevision)return;renderDetail(result);$('supportDetail').scrollIntoView({behavior:'smooth',block:'start'});}catch(error){if(currentEpoch===epoch)notice(error.message);}}
  async function action(id,path,method,data){const revision=++detailRevision,currentEpoch=epoch;const host=$('supportDetail');host?.querySelectorAll('button').forEach(b=>b.disabled=true);try{const result=await api('/'+encodeURIComponent(id)+path,method,data);if(revision!==detailRevision)return;renderDetail(result);await load();}catch(error){if(currentEpoch===epoch)notice(error.message);}finally{if(currentEpoch===epoch)host?.querySelectorAll('button').forEach(b=>b.disabled=false);}}
  const mutate=(id,data)=>action(id,'','PATCH',data);
  document.addEventListener('tenant-chat-open',event=>{
    const lead=event.detail.lead_id,currentEpoch=epoch;
    if(!member||!['owner','admin'].includes(member.role))return;
    const meta=$('modalContent');if(!meta||$('supportCreateFromChat'))return;
    const button=document.createElement('button');button.type='button';button.className='outline';button.id='supportCreateFromChat';button.textContent='Создать обращение';meta.prepend(button);
    const requestId=crypto.randomUUID();
    button.onclick=async()=>{button.disabled=true;try{const result=await api('','POST',{request_id:requestId,lead_id:lead,reason:'Создано сотрудником из диалога'});if(currentEpoch!==epoch)return;button.textContent='Обращение сохранено';await load();if($('supportInbox'))renderDetail(result);}catch(error){if(currentEpoch===epoch){button.textContent=error.message;button.disabled=false;}}};
  });
  function profileChanged(person){
    if(identity(person)!==identity(member)){epoch++;listRevision++;detailRevision++;selected=null;rows=[];members=[];nextOffset=null;nextBefore=null;if($('supportRows'))$('supportRows').innerHTML='';if($('supportDetail')){$('supportDetail').hidden=true;$('supportDetail').innerHTML='';}if($('supportAssignee'))$('supportAssignee').innerHTML='<option value="">Все сотрудники</option>';notice('');}
    member=person;if(member&&['owner','admin'].includes(member.role))load();
  }
  document.addEventListener('tenant-profile',event=>profileChanged(event.detail.member));
  const initialEpoch=epoch;
  fetch(window.TENANT_AI_AGENTS_CONFIG?.profileUrl||'/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():null).then(data=>{if(!member&&epoch===initialEpoch&&data?.member)profileChanged(data.member);}).catch(()=>{});
})();
