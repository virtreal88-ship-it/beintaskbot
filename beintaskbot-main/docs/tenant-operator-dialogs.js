/* Operator conversations: a view over existing data. State/send mutations use existing APIs. */
(()=>{
  const $=id=>document?.getElementById(id),i18n=window.tenantI18n,t=key=>i18n.t(key);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const endpoint=window.TENANT_AI_AGENTS_CONFIG?.endpoint||'/api/platform/ai/';
  let member=null,capabilities={},epoch=0,listRevision=0,openRevision=0,rows=[],nextOffset=null,currentDialog=null,currentPage=null,timer;
  const identity=m=>m?String(m.tenant_id)+':'+String(m.telegram_id):'';
  const hasRight=right=>!!member&&(capabilities.modules?.[right]??(member.role==='owner'||(member.permissions||[]).includes(right)));
  function notice(text){if($('operatorNotice'))$('operatorNotice').textContent=text||'';}
  async function rawApi(url,options={}){
    const version=epoch,who=identity(member);
    const response=await fetch(url,{...options,credentials:'same-origin',cache:'no-store',headers:{...(options.body?{'Content-Type':'application/json'}:{}),...options.headers}});
    const payload=await response.json();
    if(!document?.body||version!==epoch||who!==identity(member))throw new Error(t('companyChanged'));
    if(!response.ok||!payload.success){const error=new Error(payload.error||t('error'));error.status=response.status;error.keepRequest=payload.keep_request!==false;throw error;}
    return payload;
  }
  const api=async(path,method='GET',data)=>(await rawApi(endpoint+path,{method,...(data?{body:JSON.stringify({...data,expected_tenant_id:member?.tenant_id,expected_user_id:member?.telegram_id})}:{})})).result;
  function install(){
    const host=$('operatorDialogs');if(!host||$('operatorRows'))return;
    host.innerHTML=`<header class="top"><div><h1 data-i18n="dialogs">${t('dialogs')}</h1><p data-i18n="dialogsHint">${t('dialogsHint')}</p></div><button type="button" class="refresh" id="operatorRefresh" data-i18n="refresh">${t('refresh')}</button></header><section class="panel operator-panel"><div class="operator-filters"><label class="operator-search"><span data-i18n="search">${t('search')}</span><input type="search" id="operatorSearch" autocomplete="off"></label><button type="button" class="outline operator-filter-toggle" id="operatorFiltersToggle" aria-expanded="false" aria-controls="operatorAdvanced">${t('filters')}</button><div class="operator-advanced" id="operatorAdvanced"><label><span data-i18n="channel">${t('channel')}</span><select id="operatorChannel"></select></label><label><span data-i18n="agent">${t('agent')}</span><select id="operatorAgent"></select></label><label><span data-i18n="status">${t('status')}</span><select id="operatorState"></select></label></div></div><p id="operatorNotice" role="status" aria-live="polite"></p><div id="operatorRows"></div><button type="button" class="outline" id="operatorMore" hidden>${t('more')}</button></section>`;
    const style=document.createElement('style');style.textContent=`.operator-panel{background:var(--card);color:var(--ink)}.operator-filters{display:grid;grid-template-columns:minmax(180px,2fr) repeat(3,minmax(120px,1fr));gap:12px;margin-bottom:16px}.operator-filters label{min-width:0;margin:0}.operator-filters input,.operator-filters select{min-width:0;width:100%;color:var(--ink);background:var(--card)}.operator-row{display:grid;grid-template-columns:minmax(160px,1.4fr) minmax(120px,1fr) minmax(160px,1fr) auto;align-items:center;gap:16px;padding:17px 0;border-bottom:1px solid var(--line)}.operator-row small{display:block;color:var(--muted);overflow-wrap:anywhere}.operator-state{display:inline-flex;border-radius:20px;background:var(--paper);padding:4px 9px;font-size:12px;font-weight:650}.operator-state[data-state=active]{color:#11865b;background:#11865b18}.operator-state[data-state=human],.operator-state[data-state=paused]{color:var(--app-warning-ink,#8a5a00);background:var(--app-warning-bg,#fff2d9)}.operator-state[data-state=blocked],.operator-state[data-state=error]{color:var(--danger,#b42318);background:#b4231814}.operator-chat-meta{display:flex;flex-wrap:wrap;gap:14px;padding:14px 0}.operator-chat-meta>div{min-width:100px;overflow-wrap:anywhere}.operator-chat-meta small{display:block;color:var(--muted)}.operator-chat-actions{display:flex;gap:8px;flex-wrap:wrap}.operator-chat-notice{color:var(--app-warning-ink,#8a5a00);background:var(--app-warning-bg,#fff2d9);border-radius:10px;padding:11px;margin:12px 0}.operator-chat-history{max-height:45vh;overflow:auto;overscroll-behavior:contain;padding:8px 0}.operator-message{max-width:88%;margin:10px 0;padding:11px 13px;border:1px solid var(--line);border-radius:12px;background:var(--paper);white-space:pre-wrap;overflow-wrap:anywhere}.operator-message[data-direction=outgoing]{margin-left:auto;background:color-mix(in srgb,var(--brand) 10%,var(--card))}.operator-message[data-direction=system]{max-width:100%;text-align:center;background:transparent;border-style:dashed}.operator-message small{display:block;color:var(--muted);font-size:11px;margin-top:5px}.operator-composer{display:flex;gap:10px;align-items:flex-end;margin:12px 0}.operator-composer textarea{flex:1;min-width:0;resize:vertical;min-height:70px;background:var(--card);color:var(--ink);padding:11px;border:1px solid var(--line);border-radius:10px}.operator-chat-actions button:disabled{opacity:.55;cursor:not-allowed}.operator-error{color:var(--danger,#b42318);overflow-wrap:anywhere}#dialogsAttentionCount,#supportUnresolvedCount{margin-left:auto;font-size:11px;flex:none}#dealModal .modal-card:has([data-operator-dialog]){max-height:92dvh;overflow-y:auto;width:min(850px,100%)}@media(max-width:850px){.operator-filters{grid-template-columns:1fr 1fr}.operator-search{grid-column:1/-1}.operator-row{grid-template-columns:1fr auto}.operator-row>div:nth-child(2){grid-column:1}.operator-row>button{grid-column:2;grid-row:1/4}.operator-chat-history{max-height:42dvh}}@media(max-width:440px){.operator-filters{grid-template-columns:1fr}.operator-search{grid-column:auto}.operator-composer{flex-direction:column;align-items:stretch}.operator-chat-actions button{flex:1;white-space:normal}.operator-row{gap:10px}.operator-message{max-width:96%}}`;
    style.textContent+=`.operator-filter-toggle{display:none}.operator-advanced{display:contents}.operator-display-name{display:flex;flex-wrap:wrap;gap:8px;align-items:end;margin:12px 0}.operator-display-name label{flex:1;min-width:190px}.operator-display-name input{width:100%;box-sizing:border-box;color:var(--ink);background:var(--card)}.operator-participant-edit{margin:5px 0 0;padding:2px 6px;font-size:11px}@media(max-width:600px){.operator-filter-toggle{display:block}.operator-advanced{display:none}.operator-advanced[data-open=true]{display:grid;grid-template-columns:1fr;gap:12px;grid-column:1/-1}}`;document.head.appendChild(style);
    $('operatorFiltersToggle').onclick=()=>{const expanded=$('operatorFiltersToggle').getAttribute('aria-expanded')!=='true';$('operatorFiltersToggle').setAttribute('aria-expanded',String(expanded));$('operatorAdvanced').dataset.open=String(expanded);};
    $('operatorRefresh').onclick=()=>refresh();$('operatorMore').onclick=()=>refresh(true);
    ['operatorChannel','operatorAgent','operatorState'].forEach(id=>$(id).onchange=()=>refresh());
    $('operatorSearch').oninput=()=>{clearTimeout(timer);timer=setTimeout(()=>refresh(),250);};
    setOptions('operatorChannel',[],'allChannels');setOptions('operatorAgent',[],'allAgents');setStates();
  }
  function setStates(){const select=$('operatorState'),value=select.value;select.innerHTML='<option value="">'+t('allStates')+'</option>'+['attention','active','paused','human','blocked','error','unmanaged'].map(key=>`<option value="${key}">${t(key)}</option>`).join('');select.value=value;}
  function setOptions(id,items,placeholder){const select=$(id),value=select.value;select.innerHTML=`<option value="">${t(placeholder)}</option>`+(id==='operatorAgent'?`<option value="unassigned">${t('noAgent')}</option>`:'')+items.map(item=>`<option value="${esc(item.id)}">${esc(item.name||item.id)}</option>`).join('');select.value=value;}
  function badge(count){const node=$('dialogsAttentionCount');if(node){node.textContent=String(count);node.hidden=count===0;node.setAttribute('aria-label',t('attention')+': '+count);}}
  async function refresh(append=false){
    if(!hasRight('dialogs'))return;install();const revision=++listRevision,version=epoch;
    try{const result=await api('dialogs?'+new URLSearchParams({search:$('operatorSearch').value,channel:$('operatorChannel').value,agent:$('operatorAgent').value,state:$('operatorState').value,offset:append?nextOffset||0:0}));if(revision!==listRevision)return;rows=append?rows.concat(result.dialogs):result.dialogs;nextOffset=result.next_offset;badge(result.attention_count);setOptions('operatorChannel',result.channels,'allChannels');setOptions('operatorAgent',result.agents,'allAgents');renderRows();notice('');}
    catch(error){if(version===epoch&&revision===listRevision)notice(error.message);}
  }
  const dialogTitle=row=>row.customer_name||(row.customer_kind==='group'?t('whatsappGroup'):row.phone||t('whatsappContact'));
  const dialogIdentity=row=>row.customer_kind==='group'?`${t('technicalId')}: ${row.conversation_id||'—'}`:row.phone||`${t('technicalId')}: ${row.conversation_id||'—'}`;
  function renderRows(){
    $('operatorRows').innerHTML=rows.map((row,index)=>`<article class="operator-row"><div><b data-user-content>${esc(dialogTitle(row))}</b><small data-user-content>${esc(dialogIdentity(row))}</small></div><div><b data-user-content>${esc(row.channel_name||row.channel_id)}</b><small>${t('agent')}: <span data-user-content>${esc(row.agent_name||t('noAgent'))}</span></small></div><div><span class="operator-state" data-state="${esc(row.display_state)}">${t(row.display_state)}</span><small>${t('reason')}: ${esc(i18n.reason?i18n.reason(row.display_reason||row.reason):t('reasonUnknown'))}</small><small>${t('lastMessage')}: ${esc(i18n.date(row.last_message_at))}</small><small>${t('assignee')}: ${esc(row.assignee_name||t('unassigned'))}</small></div><button type="button" class="outline" data-open-operator="${index}">${t('openChat')}</button></article>`).join('')||`<p>${t('empty')}</p>`;
    $('operatorMore').hidden=nextOffset===null;$('operatorMore').textContent=t('more');$('operatorRows').querySelectorAll('[data-open-operator]').forEach(button=>button.onclick=()=>openDialog(rows[Number(button.dataset.openOperator)]));
  }
  const historyQuery=route=>new URLSearchParams({provider:route.provider||'connect',lead_id:route.lead_id,channel_id:route.channel_id||'',conversation_id:route.conversation_id||''});
  function messageAuthor(m){if(m.author_type==='ai')return t('aiAgent');if(m.author_type==='operator')return t('operator');if(m.author_type==='participant')return m.author_name||t('groupParticipant');if(m.author_type==='customer')return m.author_name||t('customer');return t('system');}
  function messageMarkup(items){return items.map((m,index)=>{
    const media=m.source==='connect'&&['image','audio'].includes(m.message_type)&&m.external_id;
    const transcript=m.has_transcription?`<details><summary>${t('savedTranscript')}</summary><div data-user-content>${esc(m.body)}</div></details>`:'';
    const participantEdit=m.author_type==='participant'&&m.participant_jid?`<button type="button" class="outline operator-participant-edit" data-edit-participant="${index}" data-participant-jid="${esc(m.participant_jid)}">${t('editParticipantName')}</button>`:'';
    return `<article class="operator-message" data-direction="${esc(m.direction)}" data-message-index="${index}"><div data-user-content>${esc(m.has_transcription?t('attachment'):(m.body||t('attachment')))}</div>${media?`<div data-chat-attachment="${esc(m.external_id)}" data-connect-media="true"><button type="button" class="outline">${t(m.message_type==='image'?'openPhoto':'openAudio')}</button><span role="status"></span></div>`:''}${transcript}${m.message_type&&m.message_type!=='text'?`<small>${esc(m.message_type)}</small>`:''}<small>${esc(messageAuthor(m))} · ${esc(i18n.date(m.happened_at||m.created_at))}</small>${participantEdit}</article>`;
  }).join('');}

  async function openDialog(route){
    if(!hasRight('dialogs')){notice(t('noAccess'));return;}
    const revision=++openRevision,version=epoch;
    try{const page=await api('dialogs/history?'+historyQuery(route));if(revision!==openRevision)return;currentDialog=page.dialog;currentPage=page;renderDialog(page);}
    catch(error){if(version===epoch){notice(error.message);document.dispatchEvent(new CustomEvent('tenant-operator-error',{detail:{message:error.message}}));}}
  }
  function renderDialog(page){
    const route=page.dialog,host=$('modalContent'),modal=$('dealModal');if(!host||!modal)return;
    document.dispatchEvent(new CustomEvent('tenant-chat-open',{detail:{lead_id:route.lead_id}}));
    const revision=openRevision,version=epoch,who=identity(member);
    $('modalTitle').textContent=dialogTitle(route);
    const same=()=>version===epoch&&revision===openRevision&&currentDialog?.lead_id===route.lead_id&&host.querySelector('[data-operator-dialog]');
    const reasonCode=String(route.display_reason||route.reason||'');
    const reasonText=i18n.reason?i18n.reason(reasonCode):t('reasonUnknown');
    const nameEditor=route.provider==='connect'?`<form class="operator-display-name" id="operatorConversationName"><label><small>${t('displayName')}</small><input id="operatorConversationNameInput" maxlength="180" value="${esc(route.manual_display_name||'')}" placeholder="${esc(dialogTitle(route))}"><small>${t('displayNameHint')}</small></label><button type="submit" class="outline">${t('saveName')}</button>${route.manual_display_name?`<button type="button" class="outline" id="operatorClearConversationName">${t('clearName')}</button>`:''}</form>`:'';
    host.innerHTML=`<section data-operator-dialog><div class="operator-chat-meta"><div><small>${t('channel')}</small><b data-user-content>${esc(route.channel_name||route.channel_id)}</b></div><div><small>${t('agent')}</small><b data-user-content>${esc(route.agent_name||t('noAgent'))}</b></div><div><small>${t('status')}</small><span class="operator-state" data-state="${esc(route.display_state)}">${t(route.display_state)}</span><small>${t('availability')}: ${esc(t(route.ai_availability==='available'?'aiAvailable':route.ai_availability==='stopped'?'aiStopped':route.ai_availability==='error'?'error':route.ai_availability==='unmanaged'?'noAgent':'blocked'))}</small></div><div><small>${t('lastStateChange')}</small><b>${esc(i18n.date(route.state_changed_at))}</b></div><div><small>${t('assignee')}</small><b data-user-content>${esc(route.assignee_name||t('unassigned'))}</b></div></div><p>${t('reason')}: ${esc(reasonText)}</p>${reasonCode?`<details><summary>${t('diagnostics')}</summary><small>${t('technicalId')}: ${esc(reasonCode)} · ${esc(dialogIdentity(route))}</small></details>`:''}${nameEditor}<p class="sub">${t('readOnly')}</p><div class="operator-chat-actions" id="operatorChatActions"></div><p id="operatorActionReason" class="sub"></p><div class="operator-chat-notice">${route.ai_availability==='available'?t('aiStillActive'):t('aiStopped')}</div><p id="operatorChatError" class="operator-error" role="status"></p><h3>${t('history')}</h3><button type="button" class="outline" id="operatorEarlier" ${page.next_before?'':'hidden'}>${t('earlier')}</button><div id="chatRows" class="operator-chat-history">${messageMarkup(page.messages)||t('emptyHistory')}</div><div class="operator-composer"><textarea id="chatText" data-operator-composer rows="2" aria-label="${t('message')}" placeholder="${t('message')}"></textarea><button type="button" class="primary" id="sendChat">${t('send')}</button></div><p id="operatorSendReason" class="sub"></p></section>`;
    modal.setAttribute('role','dialog');modal.setAttribute('aria-modal','true');modal.setAttribute('aria-labelledby','modalTitle');modal.classList.add('show');
    $('closeModal').setAttribute('aria-label',t('close'));$('closeModal').addEventListener('click',closeDialog,{once:true});
    const saveDisplayName=async(kind,displayName,participantJid='')=>{
      await api('dialogs/display-name','POST',{provider:'connect',lead_id:route.lead_id,channel_id:route.channel_id,conversation_id:route.conversation_id,kind,display_name:displayName,...(participantJid?{participant_jid:participantJid}:{})});
      if(same()){notice(t('nameSaved'));await refresh();await openDialog(route);}
    };
    const conversationName=$('operatorConversationName');
    if(conversationName)conversationName.onsubmit=async event=>{event.preventDefault();const button=conversationName.querySelector('button[type=submit]');button.disabled=true;try{await saveDisplayName('conversation',$('operatorConversationNameInput').value);}catch(error){if(same())$('operatorChatError').textContent=error.message;}finally{if(button.isConnected)button.disabled=false;}};
    const clearConversationName=$('operatorClearConversationName');
    if(clearConversationName)clearConversationName.onclick=async()=>{clearConversationName.disabled=true;try{await saveDisplayName('conversation','');}catch(error){if(same())$('operatorChatError').textContent=error.message;}finally{if(clearConversationName.isConnected)clearConversationName.disabled=false;}};
    host.querySelectorAll('[data-edit-participant]').forEach(button=>button.onclick=()=>{
      const row=page.messages[Number(button.dataset.editParticipant)],participantJid=String(button.dataset.participantJid||'');if(!row||!participantJid)return;
      const message=button.closest('.operator-message');if(!message||message.querySelector('[data-participant-name-form]'))return;
      const form=document.createElement('form');form.dataset.participantNameForm='true';form.className='operator-display-name';
      form.innerHTML=`<label><small>${t('displayName')}</small><input maxlength="180" value="${esc(row.author_name||'')}" aria-label="${t('displayName')}"><small>${t('participantNameHint')}</small></label><button type="submit" class="outline">${t('saveName')}</button>`;
      form.onsubmit=async event=>{event.preventDefault();const save=form.querySelector('button[type=submit]');save.disabled=true;try{await saveDisplayName('participant',form.querySelector('input').value,participantJid);}catch(error){if(same())$('operatorChatError').textContent=error.message;save.disabled=false;}};message.append(form);
    });
    const add=(label,action,allowed=route.controls_allowed)=>{const button=document.createElement('button');button.type='button';button.className=action==='resume'?'primary':'outline';button.textContent=t(label);button.dataset.operatorAction=action;button.disabled=!allowed;button.title=t(action==='handoff'?'takeoverHelp':label);button.onclick=async()=>{button.disabled=true;try{await changeState(route,action);if(same())await openDialog(route);}catch(error){if(same())$('operatorChatError').textContent=error.message;}finally{if(button.isConnected)button.disabled=!allowed;}};$('operatorChatActions').append(button);};
    const actions=new Set(route.available_actions||[]);
    if(actions.has('pause'))add('pause','pause');
    if(actions.has('handoff'))add('takeover','handoff');
    if(actions.has('resume'))add('resume','resume');
    if(!route.controls_allowed)$('operatorActionReason').textContent=i18n.reason?i18n.reason(route.control_block_reason):t('noAccess');
    else if(['paused','human'].includes(route.state)&&!route.resume_allowed)$('operatorActionReason').textContent=t('resume')+': '+(i18n.reason?i18n.reason(route.resume_block_reason):t('noAccess'));
    else if(route.ai_availability!=='available'&&route.ai_availability!=='stopped')$('operatorActionReason').textContent=reasonText;
    if(route.provider==='connect'&&hasRight('support')){const create=document.createElement('button');create.type='button';create.className='outline';create.textContent=t('createTicket');const requestId=crypto.randomUUID();create.onclick=async()=>{create.disabled=true;try{await api('support-tickets','POST',{request_id:requestId,lead_id:route.lead_id});if(same())create.textContent=t('ticketSaved');document.dispatchEvent(new CustomEvent('tenant-support-changed'));}catch(error){if(same()){$('operatorChatError').textContent=error.message;create.disabled=false;}}};$('operatorChatActions').append(create);}
    const sendAllowed=route.can_send&&hasRight('dialogs');$('sendChat').disabled=!sendAllowed;$('chatText').disabled=!sendAllowed;
    if(sendAllowed&&window.tenantChatSend)window.tenantChatSend.bind({api:rawApi,identity:who,leadId:route.lead_id,me:member,provider:route.provider,isCurrent:same,reload:async()=>{if(same())await openDialog(route);}});
    else $('operatorSendReason').textContent=t(route.send_block_reason||'noAccess');
    $('operatorEarlier').onclick=async()=>{const button=$('operatorEarlier');button.disabled=true;try{const query=historyQuery(route);query.set('before',page.next_before);const earlier=await api('dialogs/history?'+query);if(!same())return;$('chatRows').insertAdjacentHTML('afterbegin',messageMarkup(earlier.messages));document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));page.next_before=earlier.next_before;button.hidden=!page.next_before;}catch(error){if(same())$('operatorChatError').textContent=error.message;}finally{if(button.isConnected)button.disabled=false;}};
    document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));
    $('chatText').focus();
  }
  async function changeState(route,action){
    if(action==='resume'&&!window.confirm(t('resumeConfirm')))return;
    const result=await api('conversations/'+encodeURIComponent(route.conversation_id)+'/'+action,'POST',{provider:route.provider,channel_id:route.channel_id,reason:'operator_'+action});
    await refresh();document.dispatchEvent(new CustomEvent('tenant-dialog-state',{detail:{route,result}}));if(action==='handoff')document.dispatchEvent(new CustomEvent('tenant-support-changed'));return result;
  }
  function closeDialog(){document.dispatchEvent(new CustomEvent('tenant-chat-close'));openRevision++;currentDialog=null;currentPage=null;}
  function profileChanged(data){
    const next=data.member||null,changed=identity(next)!==identity(member)||JSON.stringify(next?.permissions)!==JSON.stringify(member?.permissions)||next?.role!==member?.role||next?.active!==member?.active||JSON.stringify(data.capabilities?.modules)!==JSON.stringify(capabilities.modules);
    member=next;capabilities=data.capabilities||{};
    if(changed){epoch++;listRevision++;closeDialog();rows=[];nextOffset=null;badge(0);if($('operatorRows'))$('operatorRows').innerHTML='';if($('modalContent')?.querySelector('[data-operator-dialog]')){$('modalContent').replaceChildren();$('dealModal').classList.remove('show');}if($('operatorChannel')){setOptions('operatorChannel',[],'allChannels');setOptions('operatorAgent',[],'allAgents');}}
    if(hasRight('dialogs'))refresh();
  }
  document.addEventListener('tenant-profile',event=>profileChanged(event.detail));
  document.addEventListener('tenant-view',event=>{if(event.detail.view==='dialogs')refresh();});
  document.addEventListener('tenant-language',()=>{if($('operatorRows')){setStates();refresh();}if(currentDialog&&currentPage)renderDialog(currentPage);});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&currentDialog){$('dealModal')?.classList.remove('show');closeDialog();}});
  window.tenantOperator={openDialog,changeState,refresh,hasRight,get member(){return member}};
  if(window.tenantProfileSnapshot)profileChanged(window.tenantProfileSnapshot);else fetch(window.TENANT_AI_AGENTS_CONFIG?.profileUrl||'/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():null).then(data=>{if(!member&&data?.member)profileChanged(data);}).catch(()=>{});
})();
