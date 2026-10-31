/* The existing support queue in its own workspace section; no duplicate inbox or data. */
(()=>{
  const $=id=>document?.getElementById(id),i18n=window.tenantI18n,t=key=>i18n.t(key);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const statuses=['new','waiting','in_progress','resolved'];
  const endpoint=(window.TENANT_AI_AGENTS_CONFIG?.endpoint||'/api/platform/ai/')+'support-tickets';
  const consultationEndpoint=endpoint.replace(/support-tickets$/,'internal-consultations');
  let member=null,epoch=0,listRevision=0,detailRevision=0,members=[],channels=[],selected=null,nextOffset=null,nextBefore=null,rows=[],timer,directTicketKey='';
  const identity=person=>person?String(person.tenant_id)+':'+String(person.telegram_id):'';
  const allowed=()=>window.tenantOperator?.hasRight('support')||false;
  function notice(text){if($('supportNotice'))$('supportNotice').textContent=text||'';}
  async function api(path='',method='GET',data){
    const current=member,version=epoch;
    const response=await fetch(endpoint+path,{method,credentials:'same-origin',cache:'no-store',headers:data?{'Content-Type':'application/json'}:{},...(data?{body:JSON.stringify({...data,expected_tenant_id:current?.tenant_id,expected_user_id:current?.telegram_id})}:{})});
    const payload=await response.json();
    if(!document?.body||version!==epoch||identity(current)!==identity(member))throw new Error(t('companyChanged'));
    if(!response.ok||!payload.success)throw new Error(payload.error||t('error'));
    return payload.result;
  }
  async function consultationApi(path='',method='GET',data){
    const current=member,version=epoch;
    const response=await fetch(consultationEndpoint+path,{method,credentials:'same-origin',cache:'no-store',headers:data?{'Content-Type':'application/json'}:{},...(data?{body:JSON.stringify({...data,expected_tenant_id:current?.tenant_id,expected_user_id:current?.telegram_id})}:{})});
    const payload=await response.json();
    if(!document?.body||version!==epoch||identity(current)!==identity(member))throw new Error(t('companyChanged'));
    if(!response.ok||!payload.success){const error=new Error(payload.error||t('consultCreateError'));error.status=response.status;throw error;}
    return payload.result;
  }
  async function publicShareApi(path,method='GET',data){
    const current=member,version=epoch,url='/api/platform/support-cases/'+path;
    const response=await fetch(url,{method,credentials:'same-origin',cache:'no-store',headers:data?{'Content-Type':'application/json'}:{},...(data?{body:JSON.stringify({...data,expected_tenant_id:current?.tenant_id,expected_user_id:current?.telegram_id})}:{})});
    const payload=await response.json();if(!document?.body||version!==epoch||identity(current)!==identity(member))throw new Error(t('companyChanged'));
    if(!response.ok||!payload.success)throw new Error(payload.error||t('error'));return payload.result;
  }
  function install(){
    const page=$('operatorSupport');if(!page||$('supportInbox'))return;
    const panel=document.createElement('article');panel.id='supportInbox';
    panel.innerHTML=`<header class="top"><div><h1 data-i18n="support">${t('support')}</h1><p data-i18n="supportHint">${t('supportHint')}</p></div><div class="support-actions"><button type="button" class="outline" id="supportMine" aria-pressed="false">${t('mine')}</button><button type="button" class="refresh" id="supportRefresh">${t('refresh')}</button></div></header><section class="panel"><div class="support-filters"><label class="support-search"><span>${t('searchTickets')}</span><input type="search" id="supportSearch" autocomplete="off"></label><button type="button" class="outline support-filter-toggle" id="supportFiltersToggle" aria-expanded="false" aria-controls="supportAdvanced">${t('filters')}</button><div class="support-advanced" id="supportAdvanced"><label><span>${t('status')}</span><select id="supportStatus"><option value="unresolved">${t('unresolved')}</option>${statuses.map(key=>`<option value="${key}">${t(key)}</option>`).join('')}<option value="all">${t('allTickets')}</option></select></label><label><span>${t('assignee')}</span><select id="supportAssignee"></select></label><label><span>${t('channel')}</span><select id="supportChannel"></select></label></div></div><p id="supportNotice" role="status" aria-live="polite"></p><div id="supportRows"></div><button type="button" id="supportMore" class="outline" hidden>${t('more')}</button><div id="supportDetail" hidden></div></section>`;
    page.appendChild(panel);
    const style=document.createElement('style');style.textContent=`#supportInbox h1{margin:0}#supportInbox .panel{background:var(--card);color:var(--ink)}#supportInbox .support-heading,#supportInbox .support-actions{display:flex;gap:10px;justify-content:space-between;align-items:center;flex-wrap:wrap}#supportInbox .support-filters{display:grid;grid-template-columns:minmax(190px,2fr) repeat(3,minmax(120px,1fr));gap:12px;margin:0 0 18px}#supportInbox .support-filters label{min-width:0;margin:0}#supportInbox input,#supportInbox select,#supportInbox textarea{background:var(--card);color:var(--ink);width:100%;box-sizing:border-box;border:1px solid var(--line);border-radius:10px;padding:10px}#supportInbox .support-row{display:grid;grid-template-columns:minmax(150px,1.1fr) minmax(170px,1.5fr) minmax(145px,1fr);gap:16px;padding:17px 0;border-bottom:1px solid var(--line);overflow-wrap:anywhere}#supportInbox small{display:block;color:var(--muted)}#supportDetail{border-top:2px solid var(--line);margin-top:20px;padding-top:20px}#supportDetail p,#supportHistory{white-space:pre-wrap;overflow-wrap:anywhere}#supportInbox .support-message{padding:11px;margin:8px 0;border-radius:10px;background:var(--paper);border:1px solid var(--line)}#supportInbox .support-message[data-direction=outgoing]{margin-left:10%;background:color-mix(in srgb,var(--brand) 10%,var(--card))}#supportInbox .support-edit{display:grid;gap:12px;margin:16px 0}#supportInbox .support-meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:16px 0}#supportInbox .support-ai-state{padding:12px;border-radius:10px;background:var(--app-warning-bg,var(--paper));color:var(--app-warning-ink,var(--ink));margin:14px 0}#supportInbox .support-actions button:disabled{opacity:.5;cursor:not-allowed}#supportMine[aria-pressed=true]{border-color:var(--brand);background:var(--paper)}#supportInbox .support-link{font:inherit;color:var(--brand);background:transparent;padding:0;margin-top:5px;text-align:left}@media(max-width:850px){#supportInbox .support-filters{grid-template-columns:1fr 1fr}#supportInbox .support-search{grid-column:1/-1}}@media(max-width:600px){#supportInbox .support-row{grid-template-columns:1fr}#supportInbox .support-filters{grid-template-columns:1fr}#supportInbox .support-search{grid-column:auto}#supportInbox .support-meta{grid-template-columns:1fr}#supportInbox .support-actions{justify-content:flex-start}#supportInbox .support-actions button{flex:1;white-space:normal}}`;
    style.textContent+=`#supportInbox .support-filter-toggle{display:none}#supportInbox .support-advanced{display:contents}@media(max-width:600px){#supportInbox .support-filter-toggle{display:block}#supportInbox .support-advanced{display:none}#supportInbox .support-advanced[data-open=true]{display:grid;grid-template-columns:1fr;gap:12px;grid-column:1/-1}}`;document.head.appendChild(style);
    $('supportFiltersToggle').onclick=()=>{const expanded=$('supportFiltersToggle').getAttribute('aria-expanded')!=='true';$('supportFiltersToggle').setAttribute('aria-expanded',String(expanded));$('supportAdvanced').dataset.open=String(expanded);};
    $('supportRefresh').onclick=()=>load();$('supportStatus').onchange=()=>load();$('supportAssignee').onchange=()=>load();$('supportChannel').onchange=()=>load();$('supportMore').onclick=()=>load(true);
    $('supportSearch').oninput=()=>{clearTimeout(timer);timer=setTimeout(()=>load(),250);};
    $('supportMine').onclick=()=>{$('supportAssignee').value=$('supportAssignee').value==='me'?'':'me';load();};
    filterOptions();
  }
  function assigneeOptions(value){return `<option value="">${t('unassigned')}</option>`+members.map(m=>`<option value="${esc(m.telegram_id)}" ${String(value)===String(m.telegram_id)?'selected':''}>${esc(m.display_name||m.telegram_id)}</option>`).join('');}
  function filterOptions(){
    const assignee=$('supportAssignee'),value=assignee.value;assignee.innerHTML=`<option value="">${t('allEmployees')}</option><option value="me">${t('mine')}</option><option value="unassigned">${t('unassigned')}</option>`+members.map(m=>`<option value="${esc(m.telegram_id)}">${esc(m.display_name||m.telegram_id)}</option>`).join('');assignee.value=value;
    const channel=$('supportChannel'),old=channel.value;channel.innerHTML=`<option value="">${t('allChannels')}</option>`+channels.map(row=>`<option value="${esc(row.id)}">${esc(row.name||row.id)}</option>`).join('');channel.value=old;
    $('supportMine').setAttribute('aria-pressed',String(assignee.value==='me'));
  }
  function badge(count){const node=$('supportUnresolvedCount');if(node){node.textContent=String(count);node.hidden=count===0;node.setAttribute('aria-label',t('unresolved')+': '+count);}}
  function displayName(ticket){return ticket.contact_name||ticket.phone||t('customer');}
  async function load(append=false){
    if(!member||!allowed())return;install();const revision=++listRevision,version=epoch;
    try{const result=await api('?'+new URLSearchParams({status:$('supportStatus').value,assignee:$('supportAssignee').value,search:$('supportSearch').value,channel_id:$('supportChannel').value,offset:append?nextOffset||0:0}));if(revision!==listRevision)return;
      members=result.members;channels=result.channels||[];rows=append?rows.concat(result.tickets):result.tickets;nextOffset=result.next_offset;badge(result.unresolved_count||0);filterOptions();
      $('supportRows').innerHTML=rows.map(ticket=>`<article class="support-row"><div><small title="${esc(ticket.id)}">#${esc(ticket.id.slice(0,8).toUpperCase())}</small><b data-user-content>${esc(displayName(ticket))}</b><small data-user-content>${esc(ticket.channel_name||ticket.channel_id)} · WhatsApp</small></div><div data-user-content>${esc(ticket.description)}<small>${ticket.source==='ai_handoff'?t('human'):t('new')}</small></div><div><b>${t(ticket.status)}</b><small data-user-content>${esc(ticket.assignee_name||t('unassigned'))}</small><small>${t('lastMessage')}: ${esc(i18n.date(ticket.last_message_at))}</small><button type="button" class="support-link" data-support-id="${esc(ticket.id)}">${t('open')}</button></div></article>`).join('')||`<p>${t('empty')}</p>`;
      $('supportMore').hidden=nextOffset===null;$('supportRows').querySelectorAll('[data-support-id]').forEach(button=>button.onclick=()=>open(button.dataset.supportId));notice('');
    }catch(error){if(version===epoch&&revision===listRevision)notice(error.message);}
  }
  function messages(items){return items.map(m=>`<article class="support-message" data-direction="${esc(m.direction)}"><small>${m.direction==='incoming'?t('customer'):t('employeeAI')} · ${esc(i18n.date(m.happened_at||m.created_at))}</small><div data-user-content>${esc(m.body||t('attachment'))}</div><small>${esc(m.message_type||'text')}</small></article>`).join('');}
  function renderDetail(result){
    const ticket=result.ticket;selected=ticket.id;nextBefore=result.next_before;const host=$('supportDetail');host.hidden=false;
    const state=ticket.ai_state||'unmanaged',canOpen=window.tenantOperator?.hasRight('dialogs'),canControl=canOpen&&ticket.channel_active!==false&&ticket.unambiguous!==false;
    const route={provider:'connect',channel_id:ticket.channel_id,conversation_id:ticket.conversation_id,lead_id:ticket.lead_id};
    host.innerHTML=`<div class="support-heading"><div><small title="${esc(ticket.id)}">#${esc(ticket.id.slice(0,8).toUpperCase())}</small><h2 data-user-content>${esc(displayName(ticket))}</h2></div><button type="button" class="outline" id="supportClose">${t('close')}</button></div><div class="support-meta"><div><small>${t('channel')}</small><b data-user-content>${esc(ticket.channel_name||ticket.channel_id)}</b></div><div><small>${t('status')}</small><b>${t(ticket.status)}</b></div><div><small>${t('assignee')}</small><b data-user-content>${esc(ticket.assignee_name||t('unassigned'))}</b></div><div><small>${t('created')} / ${t('updated')}</small>${esc(i18n.date(ticket.created_at))}<br>${esc(i18n.date(ticket.updated_at))}</div></div><p>${t('reason')}: <span data-user-content>${esc(t(ticket.reason))}</span></p><div class="support-ai-state" role="status"><b>${t('status')}: ${t(state==='active'&&!ticket.agent_id?'unmanaged':state)}</b><br>${state==='active'&&ticket.agent_id?t('aiStillActive'):state==='unmanaged'||!ticket.agent_id?t('noAgent'):t('aiStopped')}</div><h3>${t('summary')}</h3><p data-user-content>${esc(ticket.summary||t({pending:'summaryPending',generating:'summaryGenerating',unavailable:'summaryUnavailable'}[ticket.summary_state]||'summaryPending'))}</p>${['pending','unavailable'].includes(ticket.summary_state)?`<button type="button" class="outline" id="supportSummary">${t('generateSummary')}</button>`:''}<div class="support-edit"><label>${t('description')}<textarea id="supportDescription" maxlength="4000" rows="2">${esc(ticket.description)}</textarea></label><label>${t('suggestions')}<textarea id="supportSuggestions" maxlength="4000" rows="3">${esc(ticket.suggestions)}</textarea></label><label>${t('nextSteps')}<textarea id="supportNextSteps" maxlength="4000" rows="3">${esc(ticket.next_steps)}</textarea></label><div class="support-meta"><label>${t('status')}<select id="supportEditStatus">${statuses.map(key=>`<option value="${key}" ${key===ticket.status?'selected':''}>${t(key)}</option>`).join('')}</select></label><label>${t('assignee')}<select id="supportEditAssignee">${assigneeOptions(ticket.assigned_to)}</select></label></div></div><div class="support-actions"><button type="button" class="primary" id="supportTake" title="${t('takeHelp')}">${t('take')}</button><button type="button" class="outline" id="supportTakeover" ${canControl&&state!=='human'?'':'disabled'} title="${t('takeoverHelp')}">${t('takeover')}</button><button type="button" class="outline" id="supportOpenChat" ${canOpen?'':'disabled'}>${t('openChat')}</button><button type="button" class="outline" id="supportResolve">${t('resolve')}</button><button type="button" class="outline" id="supportSave">${t('save')}</button></div><p class="sub">${t('takeHelp')}</p>${!canControl?`<p class="sub">${t(!canOpen?'noAccess':ticket.channel_active===false?'channel_inactive':'ambiguous_channel')}</p>`:''}<h3>${t('history')}</h3><button type="button" class="outline" id="supportEarlier" ${nextBefore?'':'hidden'}>${t('earlier')}</button><div id="supportHistory">${messages(result.messages)||t('emptyHistory')}</div>`;
    const expertStates={running:'Диагностика выполняется',completed:'Диагностика завершена',failed:'Диагностика недоступна',timeout:'Время ожидания эксперта истекло',stale:'Результат устарел: состояние диалога изменилось'};
    const expertErrors={expert_model_error:'Модель недоступна; повторный платный запрос автоматически не выполняется.',expert_invalid_result:'Ответ модели не прошёл проверку формата или безопасности.',expert_context_budget:'Инструкции и контекст не помещаются в выбранный бюджет токенов.',expert_parent_inactive:'Основной диалог остановлен или изменён.',expert_timeout:'Поздний ответ не будет использован для клиентской отправки.'};
    const expertPanel=document.createElement('section');expertPanel.dataset.expertDiagnostics='';
    expertPanel.innerHTML='<h3>Внутренняя экспертная диагностика</h3>'+(result.expert_results||[]).map(item=>{const value=item.result;return `<article><b>${esc(item.expert_name)} · ${esc(item.model)}</b><p>${esc(expertStates[item.status]||item.status)}</p>${item.error_code?`<p>${esc(expertErrors[item.error_code]||'Экспертная диагностика недоступна.')}</p>`:''}${value?`<p>${esc(value.cause)}</p><p>Уверенность: ${esc(value.confidence)}</p><ul>${(value.evidence||[]).map(text=>`<li>${esc(text)}</li>`).join('')}</ul><p>${esc(value.ticket_summary)}</p><p>Безопасные рекомендации: ${esc((value.safe_actions||[]).join('; '))}</p><p>Нужны данные: ${esc(value.additional_information)}</p><p>Требуется сотрудник: ${value.requires_human?'да':'нет'}</p>`:''}</article>`}).join('');
    if(result.expert_results?.length)host.append(expertPanel);
    if(member?.role==='owner')renderPublicShareControls(ticket,result.messages||[],host);
    $('supportClose').onclick=()=>{detailRevision++;selected=null;host.hidden=true;host.replaceChildren();};
    $('supportSave').onclick=()=>mutate(ticket.id,{status:$('supportEditStatus').value,assigned_to:$('supportEditAssignee').value||null,description:$('supportDescription').value,suggestions:$('supportSuggestions').value,next_steps:$('supportNextSteps').value});
    $('supportTake').onclick=()=>mutate(ticket.id,{status:'in_progress',assigned_to:member.telegram_id});
    $('supportResolve').onclick=()=>mutate(ticket.id,{status:'resolved'});
    $('supportOpenChat').onclick=()=>document.dispatchEvent(new CustomEvent('tenant-support-open',{detail:route}));
    $('supportTakeover').onclick=async()=>{const button=$('supportTakeover'),version=epoch;button.disabled=true;try{await window.tenantOperator.changeState(route,'handoff');if(version===epoch&&selected===ticket.id)await open(ticket.id);}catch(error){if(version===epoch){notice(error.message);button.disabled=false;}}};
    if($('supportSummary'))$('supportSummary').onclick=()=>action(ticket.id,'/summary','POST',{});
    $('supportEarlier').onclick=async()=>{const revision=detailRevision,version=epoch,button=$('supportEarlier');button.disabled=true;try{const page=await api('/'+encodeURIComponent(ticket.id)+'?before='+encodeURIComponent(nextBefore));if(selected!==ticket.id||revision!==detailRevision)return;$('supportHistory').insertAdjacentHTML('afterbegin',messages(page.messages));nextBefore=page.next_before;button.hidden=!nextBefore;}catch(error){if(version===epoch)notice(error.message);}finally{if(button.isConnected)button.disabled=false;}};
    renderConsultations(ticket,result.messages||[]);
  }
  function renderPublicShareControls(ticket,caseMessages,host){
    const section=document.createElement('section');section.id='publicCaseShare';section.className='support-ai-state';
    const heading=document.createElement('h3');heading.textContent='İctimai CASE dialoqu';
    const help=document.createElement('p');help.textContent='Yalnız aşağıda seçdiyiniz mesajlar paylaşılacaq. Faylları ayrıca CRM-də açıb məzmununu yoxlayın; mətn redaktəsi faylı təmizləmir.';
    const choices=document.createElement('div');choices.className='support-edit';
    for(const message of caseMessages){const label=document.createElement('label'),check=document.createElement('input'),preview=document.createElement('span');check.type='checkbox';check.value=String(message.id);check.dataset.shareMessage='true';const type=String(message.message_type||'text').toLowerCase();preview.textContent=(message.direction==='incoming'?'Müştəri':'Texniki komanda')+' · '+type+' · '+String(message.body||'Əlavə').replace(/\s+/g,' ').slice(0,150);label.append(check,document.createTextNode(' '),preview);choices.append(label);if(['image','video','audio','voice','document'].includes(type)){const inspect=document.createElement('a');inspect.href='/?view=support&support_ticket_id='+encodeURIComponent(ticket.id)+'&support_ticket_message_id='+encodeURIComponent(message.id);inspect.target='_blank';inspect.rel='noopener noreferrer';inspect.textContent='CRM-də faylı aç';label.append(document.createTextNode(' · '),inspect);}}
    const reviewLabel=document.createElement('label'),review=document.createElement('input');review.type='checkbox';review.id='publicCaseMediaReviewed';reviewLabel.append(review,document.createTextNode(' Seçilmiş hər faylı CRM-də yoxladım və bu CASE səhifəsində paylaşılmasının təhlükəsiz olduğunu təsdiqləyirəm.'));
    const group=document.createElement('select');group.id='publicCaseGroup';const groupHint=document.createElement('label');groupHint.textContent='Bu CASE üçün developer WhatsApp qrupu';groupHint.append(group);
    const create=document.createElement('button');create.type='button';create.className='outline';create.textContent='24 saatlıq baxış linki yarat';
    const status=document.createElement('p');status.setAttribute('role','status');const active=document.createElement('div');active.className='support-edit';section.append(heading,help,groupHint,choices,reviewLabel,create,status,active);host.append(section);
    const ticketPath=encodeURIComponent(ticket.id);
    let groupRows=[];void consultationApi('/groups').then(result=>{
      groupRows=result.groups||[];group.replaceChildren(new Option('Qrupu seçin',''));
      groupRows.forEach((row,index)=>{const caption=(row.contact_name||row.conversation_id)+(row.channel_name?' · '+row.channel_name:'');group.append(new Option(caption,String(index)));});
    }).catch(error=>{status.textContent=error.message;});
    const refresh=async()=>{try{const rows=await publicShareApi(ticketPath+'/public-shares');active.replaceChildren();for(const row of rows){const item=document.createElement('div'),label=document.createElement('span'),button=document.createElement('button');label.textContent='Link · '+row.message_count+' mesaj · '+row.media_count+' fayl · '+(row.revoked?'ləğv edilib':'bitmə: '+row.expires_at);item.append(label);if(!row.revoked){button.type='button';button.className='outline danger';button.textContent='Linki ləğv et';button.onclick=async()=>{button.disabled=true;try{await publicShareApi('public-share/'+encodeURIComponent(row.id),'DELETE',{});await refresh();}catch(error){status.textContent=error.message;button.disabled=false;}};item.append(button);}active.append(item);}}catch(error){status.textContent=error.message;}};
    create.onclick=async()=>{const selected=[...choices.querySelectorAll('[data-share-message]:checked')].map(node=>node.value);if(!selected.length){status.textContent='Ən azı bir mesaj seçin.';return;}const selectedGroup=groupRows[Number(group.value)];if(group.value===''||!selectedGroup){status.textContent='Developer qrupunu seçin.';return;}const media=[...choices.querySelectorAll('[data-share-message]:checked')].filter(node=>['image','video','audio','voice','document'].includes(String(caseMessages.find(row=>String(row.id)===node.value)?.message_type||'').toLowerCase())).map(node=>node.value);if(media.length&&!review.checked){status.textContent='Seçilmiş faylları CRM-də açıb təhlükəsizliyini təsdiqləyin.';return;}create.disabled=true;try{const result=await publicShareApi(ticketPath+'/public-share','POST',{message_ids:selected,media_message_ids:media,confirm_media_review:media.length>0&&review.checked,group_channel_id:selectedGroup.channel_id,group_conversation_id:selectedGroup.conversation_id});const link=document.createElement('a');link.href=result.url;link.rel='noopener noreferrer';link.target='_blank';link.textContent='İctimai CASE linkini aç';status.replaceChildren(link,document.createTextNode(' · 24 saat etibarlıdır. WhatsApp-a yalnız istədiyiniz halda özünüz göndərin.'));choices.querySelectorAll('input').forEach(input=>input.checked=false);review.checked=false;await refresh();}catch(error){status.textContent=error.message;}finally{create.disabled=false;}};
    void refresh();
  }
  async function renderConsultations(ticket,caseMessages=[]){
    const host=$('supportDetail'),revision=detailRevision;if(!host)return;
    $('supportConsultations')?.remove();
    const section=document.createElement('section');section.id='supportConsultations';section.innerHTML=`<h3>${t('consultations')}</h3><p class="sub">${t('consultDeveloperHint')}</p><div class="support-edit"><label>${t('consultRecipient')}<select id="consultRecipient">${members.map(m=>`<option value="${esc(m.telegram_id)}">${esc(m.display_name||m.telegram_id)}</option>`).join('')}</select></label><label>${t('consultReason')}<textarea id="consultReason" maxlength="1000" rows="2"></textarea></label><button type="button" class="outline" id="consultCreate">${t('consultDeveloper')}</button></div><div id="consultRows"></div>`;host.append(section);
    const show=items=>{const rows=$('consultRows');if(!rows)return;rows.innerHTML=(items||[]).map(item=>`<article class="support-message" data-consult-id="${esc(item.id)}"><small>${esc(item.recipient_name)} · ${esc(i18n.date(item.created_at))}</small><div data-user-content>${esc(item.reason)}</div><small>${item.status==='queued_after_hours'?t('consultQueued'):t('consultWaiting')}</small>${item.response?`<hr><b>${t('consultAnswer')}</b><div data-user-content>${esc(item.response)}</div><small>${t('consultNoCustomerSend')}</small>`:`<small>${t('consultNoAnswer')}</small><label>${t('consultAnswer')}<textarea data-consult-answer="${esc(item.id)}" maxlength="4000" rows="2"></textarea></label><button type="button" class="outline" data-consult-reply="${esc(item.id)}">${t('save')}</button>`}</article>`).join('');rows.querySelectorAll('[data-consult-reply]').forEach(button=>button.onclick=async()=>{button.disabled=true;try{const field=Array.from(rows.querySelectorAll('[data-consult-answer]')).find(node=>node.dataset.consultAnswer===button.dataset.consultReply);const response=field?.value||'';await consultationApi('/'+encodeURIComponent(button.dataset.consultReply)+'/reply','POST',{response,kind:'diagnosis'});await renderConsultations(ticket,caseMessages);}catch(error){notice(error.message);button.disabled=false;}});if(member?.role==='owner'&&window.tenantOperator?.hasRight('dialogs'))for(const item of items||[]){const article=Array.from(rows.children).find(el=>el.dataset.consultId===item.id);if(article)void renderGroupFlow(article,item,caseMessages);}};
    try{const result=await consultationApi('?case_id='+encodeURIComponent(ticket.id));if(revision!==detailRevision||selected!==ticket.id)return;show(result.consultations);$('consultCreate').onclick=async()=>{const button=$('consultCreate');button.disabled=true;try{await consultationApi('','POST',{case_id:ticket.id,recipient_id:$('consultRecipient').value,reason:$('consultReason').value,evidence:{ticket_id:ticket.id,conversation_id:ticket.conversation_id},request_key:crypto.randomUUID()});await renderConsultations(ticket);}catch(error){notice(error.message);button.disabled=false;}};}catch(error){section.querySelector('#consultRows').textContent=error.message;section.querySelector('#consultCreate').disabled=true;}
  }
  async function renderGroupFlow(article,item,caseMessages){
    const path='/'+encodeURIComponent(item.id)+'/group',box=document.createElement('details'),title=document.createElement('summary'),host=document.createElement('div');
    title.textContent=t('groupConsult');box.append(title,host);article.append(box);
    const element=(tag,text)=>{const node=document.createElement(tag);if(text)node.textContent=text;return node;};
    let opened=false;
    box.ontoggle=async()=>{if(!box.open||opened)return;opened=true;
      async function display(followup=null){
        host.replaceChildren();let state;
        try{if(followup)throw {status:404};state=await consultationApi(path);}catch(error){
          if(error.status!==404){host.append(element('p',error.message));return;}
          let groups;try{groups=(await consultationApi('/groups')).groups||[];}catch(problem){host.append(element('p',problem.message));return;}
          if(followup)groups=groups.filter(group=>group.channel_id===followup.channel_id&&group.conversation_id===followup.conversation_id);
          const select=element('select');select.append(new Option(t('groupChoose'),''));
          groups.forEach((group,index)=>select.append(new Option((group.contact_name||group.conversation_id)+(group.channel_name?' · '+group.channel_name:''),String(index))));
          if(followup&&groups.length===1){select.value='0';select.disabled=true;}
          // Never prefill old remote-access codes; the owner supplies only
          // currently relevant details for this specific transmission.
          const summary=element('textarea');summary.maxLength=2600;summary.value='';summary.setAttribute('aria-label',t('groupSummary'));
          host.append(element('p',t('groupSummary')),summary,element('small',t('groupRemoteHint')),element('p',t('groupChoose')),select,element('p',t('groupAttachments')));
          const checks=[];for(const message of caseMessages.filter(row=>['image','audio','voice','video','document'].includes(row.message_type))){
            const label=element('label'),check=element('input');check.type='checkbox';check.value=message.id;
            label.append(check,document.createTextNode(' '+message.message_type+' · '+i18n.date(message.happened_at||message.created_at)));host.append(label);checks.push(check);
          }
          const prepare=element('button',t('groupPrepare'));prepare.type='button';host.append(prepare);
          prepare.onclick=async()=>{const group=groups[Number(select.value)];if(select.value===''||!group){notice(t('groupChoose'));return;}
            prepare.disabled=true;try{await consultationApi(path,'POST',{action:'prepare',request_id:crypto.randomUUID(),channel_id:group.channel_id,conversation_id:group.conversation_id,summary:summary.value,confirm_group:true,attachment_ids:checks.filter(c=>c.checked).map(c=>c.value)});await display();}catch(problem){notice(problem.message);prepare.disabled=false;}};
          return;
        }
        if(state.state==='ready'){
          host.append(element('p',state.destination+(state.channel_name?' · '+state.channel_name:'')),element('p',state.prepared_text));
          const button=element('button',t('groupSend'));button.type='button';host.append(button);
          button.onclick=async()=>{button.disabled=true;try{await consultationApi(path,'POST',{action:'send',request_id:state.request_id});await display();}catch(problem){notice(problem.message);await display();}};
        }else {
          host.append(element('p',state.state==='sent'?t('groupConfirmed'):state.state==='blocked'?t('groupBlocked'):t('groupUnknown')));
          const refresh=element('button',t('refresh'));refresh.type='button';refresh.onclick=()=>void display();host.append(refresh);
          if(state.state==='sent'){const follow=element('button',t('groupFollowup'));follow.type='button';follow.onclick=()=>void display(state);host.append(follow);}
          if(state.state==='blocked'){const retry=element('button',t('groupPrepareAgain'));retry.type='button';retry.onclick=()=>void display(state);host.append(retry);}
        }
        for(const reply of state.replies||[]){
          const section=element('section'),heading=element('b',t('groupReply')),body=element('p',reply.body),draft=element('textarea'),save=element('button',t('save')),hint=element('small',t('draftNotSent'));
          draft.value=state.draft_reply_id===reply.message_id?state.customer_draft||'':'';draft.maxLength=4000;draft.setAttribute('aria-label',t('customerDraft'));save.type='button';save.dataset.groupDraft='true';
          save.onclick=async()=>{save.disabled=true;try{await consultationApi(path,'POST',{action:'draft',message_id:reply.message_id,draft:draft.value,expected_revision:state.draft_revision});await display();}catch(problem){notice(problem.message);save.disabled=false;}};
          section.append(heading,body,element('p',t('customerDraft')),draft,save,hint);host.append(section);
        }
      }
      await display();
    };
  }
  function renderFocusedAttachment(result){
    const item=result.focus_message,ticket=result.ticket,host=$('supportDetail');if(!item||!host)return;
    const section=document.createElement('section'),label=document.createElement('p');section.dataset.focusedAttachment='true';
    label.textContent=t('attachment')+' · '+String(item.message_type||'');section.append(label);
    const status=document.createElement('small');status.setAttribute('role','status');
    if(item.media_evidence_context){const details=document.createElement('details'),summary=document.createElement('summary'),hint=document.createElement('small'),text=document.createElement('p');summary.textContent=t('savedMediaEvidence');hint.textContent=t('currentBytesNotChecked');text.textContent=item.media_evidence_context;text.setAttribute('data-user-content','');details.append(summary,hint,text);section.append(details);}
    if(window.tenantOperator?.hasRight('dialogs')&&item.external_id&&ticket.lead_id){
      const button=document.createElement('button');button.type='button';button.className='outline';button.textContent=t('downloadAttachment');
      button.onclick=()=>{if(!window.tenantDownloadAttachment){status.textContent=t('error');return;}
        const query=new URLSearchParams({lead_id:String(ticket.lead_id),message_id:String(item.external_id),channel_id:String(ticket.channel_id),conversation_id:String(ticket.conversation_id),expected_tenant_id:String(member.tenant_id),expected_user_id:String(member.telegram_id),download:'1'});
        void window.tenantDownloadAttachment(button,'/api/platform/crm/chat/attachment?'+query,status);
      };section.append(button);
    }else status.textContent=t('error');
    section.append(status);host.prepend(section);
  }
  async function open(id,focus=''){const revision=++detailRevision,version=epoch;try{const result=await api('/'+encodeURIComponent(id)+(focus?'?'+new URLSearchParams({message_id:focus}):''));if(revision!==detailRevision)return;renderDetail(result);renderFocusedAttachment(result);$('supportDetail').scrollIntoView({behavior:'smooth',block:'start'});}catch(error){if(version===epoch)notice(error.message);}}
  function openDirectTicket(){
    if(!member||!allowed())return;
    const query=new URLSearchParams(location.search),id=query.get('support_ticket_id')||'',focus=query.get('support_ticket_message_id')||'';
    if(query.get('view')!=='support'||!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id))return;
    if(focus&&!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(focus))return;
    const key=epoch+':'+id+':'+focus;if(directTicketKey===key)return;
    install();if(!$('supportDetail'))return;
    directTicketKey=key;void open(id,focus);
  }
  async function action(id,path,method,data){const revision=++detailRevision,version=epoch,host=$('supportDetail');host?.querySelectorAll('button').forEach(button=>button.disabled=true);try{const result=await api('/'+encodeURIComponent(id)+path,method,data);if(revision!==detailRevision)return;renderDetail(result);await load();}catch(error){if(version===epoch){notice(error.message);await open(id);}}}
  const mutate=(id,data)=>action(id,'','PATCH',data);
  document.addEventListener('tenant-chat-open',event=>{
    const lead=event.detail.lead_id,version=epoch;if(!member||!allowed())return;
    const host=$('modalContent');if(!host||$('supportCreateFromChat')||host.querySelector('[data-operator-dialog]'))return;
    const button=document.createElement('button');button.type='button';button.className='outline';button.id='supportCreateFromChat';button.textContent=t('createTicket');host.prepend(button);const requestId=crypto.randomUUID();
    button.onclick=async()=>{button.disabled=true;try{await api('','POST',{request_id:requestId,lead_id:lead});if(version!==epoch)return;button.textContent=t('ticketSaved');await load();}catch(error){if(version===epoch){button.textContent=error.message;button.disabled=false;}}};
  });
  function profileChanged(data){const person=data.member;if(!allowed()||identity(person)!==identity(member)||JSON.stringify(person?.permissions)!==JSON.stringify(member?.permissions)||person?.active!==member?.active){epoch++;listRevision++;detailRevision++;directTicketKey='';selected=null;rows=[];members=[];channels=[];nextOffset=null;nextBefore=null;if($('supportRows'))$('supportRows').replaceChildren();if($('supportDetail')){$('supportDetail').hidden=true;$('supportDetail').replaceChildren();}badge(0);notice('');}member=person;if(allowed()){load();openDirectTicket();}}
  document.addEventListener('tenant-profile',event=>profileChanged(event.detail));
  document.addEventListener('tenant-view',event=>{if(event.detail.view==='support'){load();openDirectTicket();}});
  document.addEventListener('tenant-support-changed',()=>load());
  document.addEventListener('tenant-dialog-state',()=>{load();if(selected)open(selected);});
  document.addEventListener('tenant-operator-error',event=>notice(event.detail.message));
  document.addEventListener('tenant-language',()=>{if(!$('supportInbox'))return;const status=$('supportStatus').value,assignee=$('supportAssignee').value,channel=$('supportChannel').value,search=$('supportSearch').value,id=selected;$('supportInbox').remove();install();$('supportStatus').value=status;$('supportAssignee').value=assignee;$('supportChannel').value=channel;$('supportSearch').value=search;load();if(id)open(id);});
  if(window.tenantProfileSnapshot)profileChanged(window.tenantProfileSnapshot);else fetch(window.TENANT_AI_AGENTS_CONFIG?.profileUrl||'/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():null).then(data=>{if(!member&&data?.member)profileChanged(data);}).catch(()=>{});
})();
