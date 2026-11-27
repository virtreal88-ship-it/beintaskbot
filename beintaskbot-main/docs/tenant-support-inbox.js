/* The existing support queue in its own workspace section; no duplicate inbox or data. */
(()=>{
  const $=id=>document?.getElementById(id),i18n=window.tenantI18n,t=key=>i18n.t(key);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const statuses=['new','waiting','in_progress','resolved'];
  const workspaceText=key=>({
    ru:{additional:'Дополнительно',mine:'Мои',attention:'Внимание',case:'Обращение',shown:'Показано',search:'Поиск обращений',context:'Сведения и AI-анализ',more:'Ещё',work:'Работа с обращением',settings:'Настройки сроков',empty:'Обращений по этим условиям нет',info:'Инфо'},
    az:{additional:'Əlavə',mine:'Mənim',attention:'Diqqət',case:'Müraciət',shown:'Göstərilir',search:'Müraciət axtar',context:'Məlumat və AI təhlili',more:'Digər',work:'Müraciətlə iş',settings:'Müddət ayarları',empty:'Bu şərtlərə uyğun müraciət yoxdur',info:'Məlumat'},
    en:{additional:'Additional details',mine:'Mine',attention:'Attention',case:'Ticket',shown:'Showing',search:'Search tickets',context:'Details and AI analysis',more:'More',work:'Ticket actions',settings:'Response targets',empty:'No tickets match these filters',info:'Info'}
  }[i18n.language]||{})[key]||key;
  function syncQueueToolbar(){
    const button=$('supportFiltersToggle');if(!button)return;
    const count=Number($('supportStatus').value!=='unresolved')+Number(!!$('supportAssignee').value)+Number(!!$('supportChannel').value);
    button.textContent=t('filters')+(count?' · '+count:'');
    $('supportQueueLabel').textContent=$('supportStatus').selectedOptions[0]?.textContent||t('unresolved');
  }
  function organizeTicketWorkspace(){
    const header=$('supportDetailContent').querySelector('.support-card-header');
    const primary=document.createElement('div');primary.className='support-primary-actions';primary.setAttribute('aria-label',workspaceText('work'));
    primary.append($('supportTake'),$('supportOpenChat'),$('supportResolve'),$('supportCreateTask'));header.append(primary);
    const chat=$('supportOpenChat');chat.textContent=({ru:'Переписка',az:'Yazışma',en:'Conversation'}[i18n.language]||'Conversation');
    const close=$('supportClose');close.textContent='×';close.title=t('close');close.setAttribute('aria-label',t('close'));
  }
  function installWorkspaceStyle(){
    $('supportWorkspaceStyle')?.remove();const style=document.createElement('style');style.id='supportWorkspaceStyle';style.textContent=`
      #supportInbox .top{display:flex;flex-wrap:wrap;margin-bottom:20px;align-items:center}#supportInbox .top>div:first-child{flex:1;min-width:0}
      #supportInbox .support-count-line{display:flex;gap:10px;align-items:center;margin-top:8px;color:var(--muted);font-size:13px}
      #supportInbox .support-count-line .crm-count-badge{margin:0}
      #supportInbox .support-queue{padding:0;background:transparent;border:0}
      #supportInbox .support-filters{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0 0 16px}
      #supportInbox .support-search{flex:1 1 260px;max-width:540px}
      #supportInbox .support-search input{height:42px;border-radius:9px;font:inherit}
      #supportInbox .support-visually-hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
      #supportInbox .support-quick-filters{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
      #supportInbox .support-quick-filters button,#supportInbox .support-filter-toggle{min-height:40px;padding:8px 12px;font-size:13px;background:var(--card);border:1px solid var(--line);border-radius:9px;color:var(--ink)}
      #supportInbox .support-quick-filters [aria-pressed=true],#supportInbox #supportFiltersToggle[aria-expanded=true]{color:var(--brand);border-color:var(--brand);background:color-mix(in srgb,var(--brand) 6%,var(--card))}
      #supportInbox .support-filter-toggle{display:block}
      #supportInbox #supportAdvanced{display:none}
      #supportInbox #supportAdvanced[data-open=true]{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;flex-basis:100%;padding:14px;border:1px solid var(--line);border-radius:10px;background:var(--card);box-sizing:border-box}
      #supportInbox #supportAdvanced label{font-size:12px;color:var(--muted)}
      #supportInbox #supportNotice:empty{display:none}
      #supportInbox .support-table-head,#supportInbox .support-row{display:grid;grid-template-columns:minmax(220px,2.6fr) minmax(120px,1fr) minmax(130px,1fr) minmax(130px,1fr);gap:20px;padding:14px 18px;align-items:center}
      #supportInbox .support-table-head{border:1px solid var(--line);border-radius:12px 12px 0 0;background:var(--paper);font-size:12px;font-weight:600;color:var(--muted)}
      #supportInbox #supportRows{border:1px solid var(--line);border-top:0;border-radius:0 0 12px 12px;background:var(--card);overflow:hidden}
      #supportInbox .support-row{border-bottom:1px solid var(--line);cursor:pointer;transition:background .12s}
      #supportInbox .support-row:last-child{border-bottom:0}
      #supportInbox .support-row:hover{background:color-mix(in srgb,var(--brand) 3%,var(--card))}
      #supportInbox .support-row>div{min-width:0}
      #supportInbox .support-case-link{display:block;max-width:100%;padding:0;border:0;background:none;color:var(--ink);font:inherit;font-weight:650;text-align:left;white-space:normal;overflow-wrap:anywhere}
      #supportInbox .support-case-link:hover{color:var(--brand)}
      #supportInbox .support-case-link:focus-visible{outline:2px solid var(--brand);outline-offset:4px;border-radius:3px}
      #supportInbox .support-excerpt{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;margin:5px 0;color:var(--muted);font-size:13px;line-height:1.45}
      #supportInbox .support-source{font-size:11px;line-height:1.4}
      #supportInbox .support-status{display:inline-flex;align-items:center;max-width:100%;padding:4px 9px;border-radius:6px;background:var(--paper);font-size:12px;line-height:1.4;font-weight:600}
      #supportInbox .support-status[data-status=waiting]{background:var(--app-warning-bg,#fff7e6);color:var(--app-warning-ink,#996300)}
      #supportInbox .support-status[data-status=in_progress]{background:color-mix(in srgb,var(--brand) 10%,var(--card));color:var(--brand)}
      #supportInbox .support-status[data-status=resolved]{background:#e8f5ee;color:#236641}
      #supportInbox .support-priority,#supportInbox .support-due{font-size:11px;margin-top:5px}
      #supportInbox .support-priority[data-priority=urgent]{color:var(--danger,#b42318);font-weight:650}
      #supportInbox .support-owner-cell,#supportInbox .support-date-cell{font-size:12px;line-height:1.5}
      #supportInbox .support-date-cell{color:var(--muted)}
      #supportInbox .support-cell-label{display:none}
      #supportInbox .support-empty{padding:40px 20px;text-align:center;color:var(--muted)}
      #supportInbox #supportMore{margin-top:14px}
      #supportInbox #supportSlaSettings{position:relative}
      #supportInbox #supportSlaSettings>summary{list-style:none;display:grid;place-items:center;cursor:pointer;width:40px;height:40px;border:1px solid var(--line);border-radius:9px;background:var(--card);font-size:18px}
      #supportInbox #supportSlaSettings>summary::-webkit-details-marker{display:none}
      #supportInbox .support-sla-explanation{grid-column:1/-1;margin:0 0 6px;font-size:12px;color:var(--muted);line-height:1.5}
      #supportInbox #supportSlaSettings .support-sla-form{position:absolute;right:0;top:44px;z-index:8;width:min(360px,calc(100vw - 48px));box-sizing:border-box;padding:16px;border:1px solid var(--line);border-radius:12px;background:var(--card);box-shadow:0 12px 32px rgb(0 0 0 / 12%);grid-template-columns:1fr 1fr}
      #supportDetail .support-modal-card{padding:20px;border-radius:14px}
      #supportDetail .support-card-header{top:-20px;padding-top:4px}
      #supportDetail .support-card-header h2{font-size:19px;font-weight:650}
      #supportDetail #supportClose{width:44px;min-height:44px!important;padding:0;font-size:26px;font-weight:400;border-radius:50%;border:0;background:var(--paper);color:var(--muted)}
      #supportDetail .support-case-tabs button{min-height:44px}
      #supportDetail .support-primary-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:10px 0;border-bottom:1px solid var(--line)}
      #supportDetail .support-primary-actions button{min-height:38px;padding:8px 12px;white-space:normal;font:inherit;font-size:13px}
      #supportDetail #supportResolve{width:auto}
      #supportDetail #supportMoreActions{position:relative;margin-left:auto}
      #supportDetail #supportMoreActions>summary{display:grid;place-items:center;width:38px;height:38px;cursor:pointer;list-style:none;border:1px solid var(--line);border-radius:9px;font-weight:700}
      #supportDetail #supportMoreActions>summary::-webkit-details-marker{display:none}
      #supportDetail .support-extra-actions{position:absolute;right:0;top:42px;width:min(280px,calc(100vw - 48px));padding:10px;background:var(--card);border:1px solid var(--line);border-radius:10px;box-shadow:0 12px 32px rgb(0 0 0 / 12%);display:grid;gap:8px}
      #supportDetail .support-work-fields{grid-template-columns:repeat(4,minmax(0,1fr));margin:0 0 6px}
      #supportDetail .support-edit label{font-size:13px;color:var(--muted)}
      #supportDetail .support-edit textarea{color:var(--ink);font:inherit;font-size:14px;line-height:1.5;resize:vertical}
      #supportDetail .support-save-actions{position:sticky;bottom:-20px;z-index:3;background:var(--card);padding:12px 0;border-top:1px solid var(--line);justify-content:flex-end}
      #supportHistory{display:flex;flex-direction:column;gap:8px;padding:12px 0}#supportHistory .support-message{max-width:85%;margin:0;align-self:flex-start;min-width:140px;box-sizing:border-box}#supportHistory .support-message[data-direction=outgoing]{align-self:flex-end;margin:0}#supportHistory .support-message small{font-size:11px;margin-top:5px}#supportDetail .support-history-actions{justify-content:flex-end}
      #supportDetail .support-history-actions+.sub{display:none}
      @media(max-width:1000px){#supportInbox .support-table-head,#supportInbox .support-row{grid-template-columns:minmax(180px,2fr) minmax(105px,1fr) minmax(110px,1fr);gap:12px}#supportInbox .support-table-head>span:last-child{display:none}#supportInbox .support-date-cell{grid-column:1/-1;font-size:11px}#supportDetail .support-work-fields{grid-template-columns:repeat(2,minmax(0,1fr))}}
      @media(max-width:600px){
        #supportInbox .top{gap:8px}#supportInbox #supportRefresh{width:40px;min-height:40px;padding:0;font-size:0}#supportInbox #supportRefresh:before{content:'↻';font-size:22px}#supportInbox .support-count-line{font-size:12px;flex-wrap:wrap}
        #supportInbox .support-search{flex-basis:100%;max-width:none}#supportInbox .support-quick-filters{flex:1}#supportInbox .support-quick-filters button{padding:8px;font-size:12px}
        #supportInbox #supportAdvanced[data-open=true]{grid-template-columns:1fr}#supportInbox #supportSlaSettings .support-sla-form{position:fixed;left:16px;right:16px;top:144px;width:auto;z-index:30}
        #supportInbox .support-table-head{display:none}#supportInbox #supportRows{border-top:1px solid var(--line);border-radius:12px}
        #supportInbox .support-row{grid-template-columns:minmax(0,1fr) auto;gap:10px;padding:16px}
        #supportInbox .support-case-cell{grid-column:1/-1}#supportInbox .support-state-cell{grid-row:2;grid-column:1}#supportInbox .support-owner-cell{grid-row:2;grid-column:2;text-align:right;max-width:145px}
        #supportInbox .support-date-cell{grid-column:1/-1}#supportInbox .support-date-cell .support-cell-label{display:inline;margin-right:5px}
        #supportDetail .support-modal-card{padding:16px;border-radius:0}#supportDetail .support-card-header{top:-16px}
        #supportDetail .support-primary-actions{gap:6px}#supportDetail .support-primary-actions button{min-height:44px;padding:8px;font-size:12px}
        #supportDetail #supportResolve{width:auto;padding:8px}#supportDetail #supportMoreActions>summary{width:44px;height:44px}#supportDetail .support-save-actions{bottom:-16px}
      }`;document.head.append(style);
  }
  const caseTabs=new Map();
  const mediaTitle=kind=>t(({image:'photo',audio:'voiceMessage',voice:'voiceMessage',video:'video',document:'file',text:'caseTextMessage'})[kind]||'attachment');
  const endpoint=(window.TENANT_AI_AGENTS_CONFIG?.endpoint||'/api/platform/ai/')+'support-tickets';
  let member=null,operatorWorkspaceAvailable=false,epoch=0,listRevision=0,detailRevision=0,members=[],channels=[],selected=null,nextOffset=null,nextBefore=null,rows=[],timer,directTicketKey='',returnFocus=null,modalOpen=false,routeChange=false,bodyOverflow='',drafts=new Map(),ticketMedia=new Map(),detailData=null;
  const identity=person=>person?String(person.tenant_id)+':'+String(person.telegram_id):'';
  let accessCapabilities={};
  const allowed=()=>window.tenantOperator?.hasRight('support')??(!!member&&member.active!==false&&accessCapabilities.modules?.support===true);
  let manualCaseUILoad=null;
  function ensureManualCaseUI(){
    if(window.tenantOperator?.openManualCaseCreate)return Promise.resolve(window.tenantOperator);
    if(manualCaseUILoad)return manualCaseUILoad;
    manualCaseUILoad=new Promise((resolve,reject)=>{const script=document.createElement('script');script.src='/assets/tenant-operator-dialogs.js';script.async=true;script.dataset.supportManualCaseLoader='true';const fail=()=>{script.remove();manualCaseUILoad=null;reject(Error(t('error')));};script.onerror=fail;script.onload=()=>{if(window.tenantOperator?.openManualCaseCreate)resolve(window.tenantOperator);else fail();};document.head.appendChild(script);});return manualCaseUILoad;
  }
  function notice(text){const node=(modalOpen?$('supportDetailNotice'):null)||$('supportNotice');if(node)node.textContent=text||'';}
  const materialMessages=()=>detailData?[...detailData.messages.values()].sort((a,b)=>{const left=String(a.happened_at||a.created_at||''),right=String(b.happened_at||b.created_at||'');return left&&right?left.localeCompare(right)||String(a.id).localeCompare(String(b.id)):0;}):[];
  function rememberMessages(items){if(!detailData)return;for(const item of items||[])if(item.id)detailData.messages.set(String(item.id),{...detailData.messages.get(String(item.id)),...item});}
  function appendMaterialMessages(){const host=$('supportHistory');if(!host||!detailData)return;const items=materialMessages(),known=new Map([...host.querySelectorAll('[data-message-id]')].map(node=>[node.dataset.messageId,node]));if(!known.size&&items.length)host.replaceChildren();for(let index=0;index<items.length;index++){const item=items[index],key=String(item.id);if(known.has(key)){const body=known.get(key).querySelector('[data-message-body]');if(body)body.textContent=item.body||mediaTitle(item.message_type);continue;}const template=document.createElement('template');template.innerHTML=messages([item],detailData.ticket);const node=template.content.firstElementChild;const next=items.slice(index+1).map(row=>known.get(String(row.id))).find(Boolean);host.insertBefore(node,next||null);known.set(key,node);}}
  function refreshMaterialControls(){if(detailData&&selected===detailData.ticket.id)renderConsultations(detailData.ticket);}
  const tabText=key=>({ru:{history:'История диалога',details:'Данные обращения',developer:'Разработчики',tabs:'Обращение',materials:'Материалы обращения',historyHint:'История от принятия диалога до создания обращения. Для старых обращений показывается сохранённый фрагмент.'},az:{history:'Dialoq tarixçəsi',details:'Müraciət məlumatları',developer:'Tərtibatçılar',tabs:'Müraciət',materials:'Müraciət materialları',historyHint:'Dialoqun qəbulundan müraciətin yaradılmasına qədər tarixçə. Köhnə müraciətlərdə saxlanmış fraqment göstərilir.'},en:{history:'Dialogue history',details:'Case details',developer:'Developers',tabs:'Case',materials:'Case materials',historyHint:'History from accepting the dialogue to creating the case. Older cases show the saved fragment.'}}[i18n.language]||{})[key]||key;
  function selectCaseTab(name,{focus=false,persist=true,clearReply=false}={}){
    if(!detailData||!['history','details','developer'].includes(name))return;
    $('supportMoreActions')?.removeAttribute('open');
    const host=$('supportDetailContent'),previous=detailData.activeTab;
    if(previous)detailData.tabScroll[previous]=host.scrollTop;
    detailData.activeTab=name;
    for(const tab of host.querySelectorAll('[data-support-tab]')){const active=tab.dataset.supportTab===name;tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;if(active&&focus)tab.focus({preventScroll:true});}
    for(const panel of host.querySelectorAll('[data-support-panel]')){panel.hidden=panel.dataset.supportPanel!==name;panel.querySelectorAll('audio,video').forEach(media=>{if(panel.hidden)media.pause?.();});}
    host.scrollTop=detailData.tabScroll[name]||0;
    caseTabs.set(identity(member)+':'+selected,name);
    if(persist&&new URLSearchParams(location.search).get('support_ticket_id')===selected){const url=new URL(location.href);url.searchParams.set('support_ticket_tab',name);if(clearReply)url.searchParams.delete('consultation_reply_id');history.replaceState(history.state,'',url.pathname+url.search+url.hash);}
    if(name==='history')void loadDialogueHistory();
  }
  function installCaseTabs(ticket){
    const host=$('supportDetailContent'),notice=$('supportDetailNotice'),tabs=document.createElement('div');tabs.className='support-case-tabs';tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label',tabText('tabs'));
    const panels={};for(const name of ['history','details','developer']){const tab=document.createElement('button');tab.type='button';tab.id='supportTab-'+name;tab.dataset.supportTab=name;tab.setAttribute('role','tab');tab.setAttribute('aria-controls','supportPanel-'+name);tab.textContent=tabText(name);tab.onclick=()=>selectCaseTab(name,{clearReply:true});tabs.append(tab);const panel=document.createElement('section');panel.id='supportPanel-'+name;panel.dataset.supportPanel=name;panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby',tab.id);panel.tabIndex=0;panels[name]=panel;}
    while(notice.nextSibling)panels.details.append(notice.nextSibling);
    const header=document.createElement('header');header.className='support-card-header';host.prepend(header);header.append(host.querySelector('.support-heading'),tabs);host.append(panels.history,panels.details,panels.developer);
    const earlier=$('supportEarlier'),history=$('supportHistory');
    panels.history.innerHTML=`<p class="sub">${esc(detailData.historyContext?.boundary_status==='accepted_interval'?({ru:'История от принятия диалога до создания обращения.',az:'Dialoqun qəbulundan müraciətin yaradılmasına qədər tarixçə.',en:'History from accepting the dialogue to creating the case.'}[i18n.language]||tabText('historyHint')):({ru:'Время принятия не сохранено. Показаны сохранённые сообщения обращения.',az:'Qəbul vaxtı saxlanmayıb. Müraciətin saxlanmış mesajları göstərilir.',en:'Acceptance time was not saved. Showing saved case messages.'}[i18n.language]||tabText('historyHint')))}</p><p id="supportDialogueNotice" role="status" aria-live="polite"></p>`;
    panels.history.append(earlier,history);
    tabs.onkeydown=event=>{const names=['history','details','developer'],index=names.indexOf(event.target.dataset.supportTab);if(index<0)return;const next=event.key==='Home'?0:event.key==='End'?2:event.key==='ArrowRight'?(index+1)%3:event.key==='ArrowLeft'?(index+2)%3:-1;if(next>=0){event.preventDefault();selectCaseTab(names[next],{focus:true,clearReply:true});}};
  }
  async function loadDialogueHistory(earlier=false,refresh=false){
    const state=detailData;if(!state||!modalOpen||state.historyLoading)return;
    if(!earlier&&!refresh)return; // Initial page is the server-pinned case history.
    if(earlier&&!nextBefore)return;
    const revision=detailRevision,version=epoch,owner=identity(member),ticket=state.ticket;
    const current=()=>state===detailData&&revision===detailRevision&&version===epoch&&owner===identity(member)&&modalOpen&&selected===ticket.id&&allowed();
    state.historyLoading=true;const button=$('supportEarlier'),status=$('supportDialogueNotice');button.disabled=true;
    try{
      const page=await api('/'+encodeURIComponent(ticket.id)+(earlier?'?before='+encodeURIComponent(nextBefore):''));
      if(!current())return;
      if(!earlier){clearTicketMedia();state.messages.clear();$('supportHistory').replaceChildren();}
      rememberMessages(page.messages);appendMaterialMessages();nextBefore=page.next_before;state.scope=page.scope;button.hidden=!nextBefore;status.textContent='';refreshMaterialControls();
    }catch(error){if(current())status.textContent=error.message||t('error');}
    finally{state.historyLoading=false;if(current())button.disabled=false;}
  }
  async function apiAt(base,path='',method='GET',data){
    const current=member,version=epoch;
    const response=await fetch(base+path,{method,credentials:'same-origin',cache:'no-store',headers:data?{'Content-Type':'application/json'}:{},...(data?{body:JSON.stringify({...data,expected_tenant_id:current?.tenant_id,expected_user_id:current?.telegram_id})}:{})});
    const payload=await response.json();
    if(!document?.body||version!==epoch||identity(current)!==identity(member))throw new Error(t('companyChanged'));
    if(!response.ok||!payload.success){const error=new Error(payload.error||t('error'));error.status=response.status;throw error;}
    return payload.result;
  }
  const api=(path='',method='GET',data)=>apiAt(endpoint,path,method,data);
  const slaEndpoint=endpoint.replace(/support-tickets$/,'support-sla-policy');
  function installSlaControls(panel){
    const actions=panel.querySelector('.support-actions'),status=$('supportStatus'),filters=$('supportRows');if(!actions||!status||!filters)return;
    if(!status.querySelector('option[value="attention"]')){const option=document.createElement('option');option.value='attention';option.textContent=t('attentionQueue');status.append(option);}
    const attention=document.createElement('button');attention.type='button';attention.className='outline';attention.id='supportAttention';updateAttentionButton();attention.setAttribute('aria-pressed','false');attention.onclick=()=>{status.value=status.value==='attention'?'unresolved':'attention';load();};actions.insertBefore(attention,$('supportRefresh'));
    const settings=document.createElement('details');settings.id='supportSlaSettings';settings.hidden=true;const summary=document.createElement('summary');summary.textContent=t('slaConfigure');const help=document.createElement('p');help.textContent=t('slaHelp');const form=document.createElement('form');form.className='support-sla-form';
    for(const level of ['urgent','high','normal','low']){const label=document.createElement('label'),title=document.createElement('span'),input=document.createElement('input');title.textContent=priorityText(level);input.type='number';input.min='1';input.max='720';input.step='1';input.required=true;input.dataset.slaHours=level;label.append(title,input);form.append(label);}
    const submit=document.createElement('button'),result=document.createElement('p');submit.type='submit';submit.className='primary';submit.textContent=t('slaSave');result.setAttribute('role','status');result.setAttribute('aria-live','polite');form.append(submit,result);form.onsubmit=async event=>{event.preventDefault();if(submit.disabled)return;const response_hours={};for(const level of ['urgent','high','normal','low'])response_hours[level]=Number(form.querySelector(`[data-sla-hours="${level}"]`).value);if(Object.values(response_hours).some(value=>!Number.isInteger(value)||value<1||value>720)){result.textContent=t('error');return;}submit.disabled=true;try{await apiAt(slaEndpoint,'','PUT',{response_hours});result.textContent=t('slaSaved');await load();}catch(error){result.textContent=error.message||t('error');}finally{submit.disabled=false;}};settings.append(summary,help,form);filters.before(settings);
    const style=document.createElement('style');style.textContent='#supportInbox .support-sla-form{display:grid;grid-template-columns:repeat(4,minmax(90px,1fr));gap:10px;align-items:end;margin:10px 0}#supportInbox .support-sla-form label{margin:0}@media(max-width:600px){#supportInbox .support-sla-form{grid-template-columns:1fr 1fr}}#supportDetail .support-recommendation-results article{padding:10px;margin:8px 0;border:1px solid var(--line);border-radius:10px;background:var(--paper)}#supportDetail .support-recommendation-results a{color:var(--brand);font-weight:700}';panel.append(style);
  }
  function install(){
    const page=$('operatorSupport');if(!page||$('supportInbox'))return;
    const panel=document.createElement('article');panel.id='supportInbox';
    panel.innerHTML=`<header class="top"><div><h1 data-i18n="support">${t('support')}</h1><div class="support-count-line"><span id="supportCount" class="crm-count-badge"></span><span id="supportQueueLabel"></span></div></div><div class="support-actions"><button type="button" class="outline" id="supportMine" aria-pressed="false">${t('mine')}</button><button type="button" class="refresh" id="supportRefresh">${t('refresh')}</button></div></header><section class="support-queue"><form id="supportSearchForm" autocomplete="off"></form><div class="support-filters"><label class="support-search"><span class="support-visually-hidden">${t('searchTickets')}</span><input type="search" id="supportSearch" form="supportSearchForm" name="support_query" autocomplete="off" placeholder="${workspaceText('search')}"></label><div id="supportQuickFilters" class="support-quick-filters"></div><button type="button" class="outline support-filter-toggle" id="supportFiltersToggle" aria-expanded="false" aria-controls="supportAdvanced">${t('filters')}</button><div class="support-advanced" id="supportAdvanced"><label><span>${t('status')}</span><select id="supportStatus"><option value="unresolved">${t('unresolved')}</option>${statuses.map(key=>`<option value="${key}">${t(key)}</option>`).join('')}<option value="all">${t('allTickets')}</option></select></label><label><span>${t('assignee')}</span><select id="supportAssignee"></select></label><label><span>${t('channel')}</span><select id="supportChannel"></select></label></div></div><p id="supportNotice" role="status" aria-live="polite"></p><div class="support-table-head" aria-hidden="true"><span>${workspaceText('case')}</span><span>${t('status')}</span><span>${t('assignee')}</span><span>${t('lastMessage')}</span></div><div id="supportRows"></div><button type="button" id="supportMore" class="outline" hidden>${t('more')}</button><div id="supportDetail" role="dialog" aria-modal="true" aria-labelledby="supportTicketTitle" hidden><div id="supportDetailContent" class="support-modal-card" tabindex="-1"></div></div></section>`;
    page.appendChild(panel);installSlaControls(panel);
    $('supportQuickFilters').append($('supportMine'),$('supportAttention'));$('supportMine').textContent=workspaceText('mine');$('supportMine').title=t('mine');$('supportMine').setAttribute('aria-label',t('mine'));$('supportRefresh').title=t('refresh');$('supportRefresh').setAttribute('aria-label',t('refresh'));
    const settings=$('supportSlaSettings'),settingsLabel=settings.querySelector('summary');settingsLabel.textContent='⚙';settingsLabel.title=workspaceText('settings');settingsLabel.setAttribute('aria-label',workspaceText('settings'));const settingsHelp=settings.querySelector('p');settingsHelp.className='support-sla-explanation';settings.querySelector('form').prepend(settingsHelp);panel.querySelector('.top .support-actions').prepend(settings);
    $('supportSearchForm').onsubmit=event=>event.preventDefault();
    document.body.appendChild($('supportDetail'));syncSupportViewport();
    $('supportDetailContent').addEventListener('click',event=>{const button=event.target.closest?.('[data-ticket-media-open]');if(button&&$('supportDetailContent').contains(button))void openTicketMedia(button);});
    const detailContent=$('supportDetailContent'),rememberDraft=()=>{if(modalOpen&&selected)saveDraft(selected);};detailContent.addEventListener('input',rememberDraft);detailContent.addEventListener('change',rememberDraft);
    const style=document.createElement('style');style.textContent=`#supportInbox h1{margin:0}#supportInbox .panel{background:var(--card);color:var(--ink)}#supportInbox .support-heading,#supportInbox .support-actions{display:flex;gap:10px;justify-content:space-between;align-items:center;flex-wrap:wrap}#supportInbox .support-filters{display:grid;grid-template-columns:minmax(190px,2fr) repeat(3,minmax(120px,1fr));gap:12px;margin:0 0 18px}#supportInbox .support-filters label{min-width:0;margin:0}#supportInbox input,#supportInbox select,#supportInbox textarea,#supportDetail input,#supportDetail select,#supportDetail textarea{background:var(--card);color:var(--ink);width:100%;box-sizing:border-box;border:1px solid var(--line);border-radius:10px;padding:10px}#supportInbox .support-row{display:grid;grid-template-columns:minmax(150px,1.1fr) minmax(170px,1.5fr) minmax(145px,1fr);gap:16px;padding:17px 0;border-bottom:1px solid var(--line);overflow-wrap:anywhere}#supportInbox small{display:block;color:var(--muted)}#supportDetail{position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;padding:24px;background:rgb(0 0 0 / 58%);box-sizing:border-box}#supportDetail[hidden]{display:none}#supportDetail .support-modal-card{width:min(1120px,100%);max-height:min(92dvh,980px);overflow:auto;overscroll-behavior:contain;background:var(--card);color:var(--ink);border-radius:18px;padding:24px;box-shadow:0 24px 80px rgb(0 0 0 / 35%)}#supportDetail h2{margin:4px 0}#supportDetail small{display:block;color:var(--muted)}#supportDetail .support-heading,#supportDetail .support-actions{display:flex;gap:10px;justify-content:space-between;align-items:center;flex-wrap:wrap}#supportDetailContent>.support-heading{position:sticky;top:-24px;z-index:5;padding:8px 0;background:var(--card);border-bottom:1px solid var(--line)}#supportDetail .support-meta b{display:block}#supportDetail p,#supportHistory{white-space:pre-wrap;overflow-wrap:anywhere}#supportDetail .support-message{padding:11px;margin:8px 0;border-radius:10px;background:var(--paper);border:1px solid var(--line)}#supportDetail .support-message[data-direction=outgoing]{margin-left:10%;background:color-mix(in srgb,var(--brand) 10%,var(--card))}#supportDetail .support-media-preview{display:grid;gap:8px;margin-top:8px}#supportDetail .support-media-preview img,#supportDetail .support-media-preview video{max-width:min(680px,100%);max-height:55vh;object-fit:contain;background:#111;border-radius:8px}#supportDetail .support-media-preview audio{max-width:100%}#supportDetail .support-photo{cursor:zoom-in}#supportDetail .support-photo.is-zoomed{max-width:100%;max-height:none;width:auto;height:auto;cursor:zoom-out}#supportDetail .support-edit{display:grid;gap:12px;margin:16px 0}#supportDetail .support-meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:16px 0}#supportDetail .support-ai-state{padding:12px;border-radius:10px;background:var(--app-warning-bg,var(--paper));color:var(--app-warning-ink,var(--ink));margin:14px 0}#supportDetail .support-actions button:disabled{opacity:.5;cursor:not-allowed}#supportMine[aria-pressed=true]{border-color:var(--brand);background:var(--paper)}#supportInbox .support-link{font:inherit;color:var(--brand);background:transparent;padding:0;margin-top:5px;text-align:left}@media(max-width:850px){#supportInbox .support-filters{grid-template-columns:1fr 1fr}#supportInbox .support-search{grid-column:1/-1}}@media(max-width:600px){#supportInbox .support-row{grid-template-columns:1fr}#supportInbox .support-filters{grid-template-columns:1fr}#supportInbox .support-search{grid-column:auto}#supportDetail{padding:0}#supportDetail .support-modal-card{width:100%;height:100dvh;max-height:none;border-radius:0;padding:16px;box-sizing:border-box}#supportDetailContent>.support-heading{top:-16px}#supportDetail .support-meta{grid-template-columns:1fr}#supportDetail .support-actions{justify-content:flex-start}#supportDetail .support-actions button{flex:1;white-space:normal}}`;
    style.textContent+=`#supportInbox .support-filter-toggle{display:none}#supportInbox .support-advanced{display:contents}#supportDetail .support-developer-assignee{display:grid;grid-template-columns:1fr;gap:3px;min-width:min(240px,100%);margin:0;font-size:12px;font-weight:650}#supportDetail .support-developer-assignee select{padding:8px 10px}@media(max-width:600px){#supportInbox .support-filter-toggle{display:block}#supportInbox .support-advanced{display:none}#supportInbox .support-advanced[data-open=true]{display:grid;grid-template-columns:1fr;gap:12px;grid-column:1/-1}#supportDetail .support-developer-assignee{flex:1 1 100%}}`+`#supportDetailContent>.support-heading{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px 12px;align-items:center}#supportDetailContent>.support-heading>div{min-width:0;overflow-wrap:anywhere}#supportDetailContent>.support-heading>#supportClose{grid-column:2;grid-row:1;min-height:44px;align-self:start}#supportDetail .support-developer-assignee{grid-column:1/-1;grid-row:2;min-width:0;width:100%}#supportDetail .support-developer-assignee select{min-height:44px;font-size:16px}#supportDetail button:focus-visible,#supportDetail select:focus-visible{outline:2px solid var(--brand);outline-offset:2px}@media(max-width:600px){#supportDetail{top:var(--support-visual-top,0px);bottom:auto;height:var(--support-visual-height,100dvh)}#supportDetail .support-modal-card{height:100%;max-height:100%;padding-bottom:max(16px,env(safe-area-inset-bottom))}}#supportDetail .support-case-tabs{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px;position:sticky;top:0;z-index:6;background:var(--card);padding:8px 0;border-bottom:1px solid var(--line)}#supportDetail .support-case-tabs button{min-width:0;min-height:48px;padding:8px 6px;white-space:normal;overflow-wrap:anywhere;font-size:13px;line-height:1.25;background:transparent;color:var(--muted);border:1px solid transparent;border-radius:9px}#supportDetail .support-case-tabs [aria-selected=true]{background:var(--paper);color:var(--brand);border-color:var(--line);font-weight:700}#supportDetail [data-support-panel]{min-width:0;padding-top:12px}#supportDetail [data-support-panel][hidden]{display:none!important}#supportDetailContent>.support-heading{position:static}#supportDetailNotice:empty{display:none}#supportDetail .support-history-actions{display:flex;gap:8px;flex-wrap:wrap}#supportDetail .support-history-actions button{min-height:44px;white-space:normal}#supportDialogueHistory{min-width:0;min-height:180px}#supportDialogueHistory .operator-message{box-sizing:border-box}#supportDetail #supportCaseMaterials{margin-top:24px;border-top:1px solid var(--line);padding-top:16px}#supportDetail #supportCaseMaterials>summary{cursor:pointer;font-weight:650;min-height:32px}#supportDetail .support-case-consultations{margin:0;padding:0;border:0}#supportDetail .support-case-consultations button{white-space:normal;overflow-wrap:anywhere;max-width:100%}#supportDetail .support-case-consultations .support-edit select{min-width:0}#supportDetail [data-support-dialog-history] [data-media-open][hidden]{display:none}#supportDetail .support-case-tabs button:focus-visible{outline:2px solid var(--brand);outline-offset:-2px}#supportDetail .support-card-header{position:sticky;top:-24px;z-index:6;background:var(--card);padding:8px 0 0}#supportDetail .support-card-header>.support-heading{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px 12px;position:static}#supportDetail .support-card-header .support-heading>div{min-width:0;overflow-wrap:anywhere}#supportDetail .support-card-header #supportClose{grid-column:2;grid-row:1;align-self:start;min-height:44px}#supportDetail .support-card-header h2{font-size:18px;line-height:1.3}#supportDetail .support-card-header .support-case-tabs{position:static}#supportDetail .support-card-header .support-developer-assignee{grid-row:2}@media(max-width:600px){#supportDetail .support-card-header{top:-16px}}#supportDetail [data-support-dialog-history] [data-native-audio=true]>button[data-media-open]:not([hidden]){display:block!important}#supportDetail .support-modal-card{height:min(92dvh,980px);box-sizing:border-box}@media(max-width:600px){#supportDetail .support-modal-card{height:100%}}@media(max-width:600px){#supportDetail .support-case-tabs{grid-template-columns:minmax(0,1fr) minmax(0,1.15fr) max-content}#supportDetail .support-case-tabs button{font-size:12px;padding:8px 3px;word-break:normal;overflow-wrap:normal}#supportDetail .support-case-tabs [data-support-tab=developer]{white-space:nowrap}}`;$('supportInboxBaseStyle')?.remove();style.id='supportInboxBaseStyle';document.head.appendChild(style);
    installWorkspaceStyle();
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
    $('supportMine').setAttribute('aria-pressed',String(assignee.value==='me'));syncQueueToolbar();
  }
  function badge(count){const node=$('supportUnresolvedCount');if(node){node.textContent=String(count);node.hidden=count===0;node.setAttribute('aria-label',t('unresolved')+': '+count);}}
  const safeIdentityName=value=>{const name=String(value||'').trim();return /@(?:s\.whatsapp\.net|g\.us|lid)$/i.test(name)?'':name;};
  const verifiedCasePhone=ticket=>{const value=String(ticket.customer_verified_phone||'').trim();return /^\+?[0-9 ()-]+$/.test(value)&&value.replace(/\D/g,'').length>=7&&value.replace(/\D/g,'').length<=15?value:'';};
  function displayName(ticket){return safeIdentityName(ticket.contact_name)||t('customer');}
  const ticketStatus=ticket=>ticket.completion_outcome==='test_stopped'?t('testStoppedUnresolved'):t(ticket.status);
  const priorityText=value=>t(({low:'priorityLow',normal:'priorityNormal',high:'priorityHigh',urgent:'priorityUrgent'})[value]||'priorityNormal');
  let activeAttentionCount=0;
  function updateAttentionButton(){const button=$('supportAttention');if(button){button.textContent=workspaceText('attention')+(activeAttentionCount?` · ${activeAttentionCount}`:'');button.title=t('attentionQueue');button.setAttribute('aria-label',t('attentionQueue')+(activeAttentionCount?' · '+activeAttentionCount:''));button.setAttribute('aria-pressed',String($('supportStatus')?.value==='attention'));}}
  function renderSlaSettings(policy){
    const host=$('supportSlaSettings');if(!host)return;const permitted=policy&&typeof policy==='object'&&!Array.isArray(policy);host.hidden=!permitted;if(!permitted)return;
    for(const level of ['urgent','high','normal','low']){const input=host.querySelector(`[data-sla-hours="${level}"]`);if(input&&document.activeElement!==input)input.value=Number.isInteger(policy[level])?String(policy[level]):'';}
  }
  async function load(append=false){
    if(!member||!allowed())return;install();const revision=++listRevision,version=epoch;
    try{const result=await api('?'+new URLSearchParams({status:$('supportStatus').value,assignee:$('supportAssignee').value,search:$('supportSearch').value,channel_id:$('supportChannel').value,offset:append?nextOffset||0:0}));if(revision!==listRevision)return;
      members=result.members;channels=result.channels||[];rows=append?rows.concat(result.tickets):result.tickets;nextOffset=result.next_offset;activeAttentionCount=Number(result.attention_count)||0;badge(result.unresolved_count||0);updateAttentionButton();renderSlaSettings(result.sla_policy);filterOptions();
      const focusedTicket=$('supportRows').contains(document.activeElement)?document.activeElement.closest('[data-support-id]')?.dataset.supportId:null;
      $('supportCount').textContent=workspaceText('shown')+': '+rows.length+(nextOffset!==null?'+':'');
      $('supportRows').innerHTML=rows.map(ticket=>`<article class="support-row"><div class="support-case-cell"><button type="button" class="support-case-link" data-support-id="${esc(ticket.id)}" title="#${esc(ticket.id.slice(0,8).toUpperCase())}" aria-label="${esc(t('open')+': '+displayName(ticket))}" data-user-content>${esc(displayName(ticket))}</button><p class="support-excerpt" data-user-content>${esc(ticket.description)}</p><small class="support-source" data-user-content>${esc(ticket.channel_name||ticket.channel_id)}${ticket.source==='ai_handoff'?' · '+t('human'):''}</small></div><div class="support-state-cell"><span class="support-status" data-status="${esc(statuses.includes(ticket.status)?ticket.status:'new')}">${ticketStatus(ticket)}</span>${['high','urgent'].includes(ticket.priority)?`<small class="support-priority" data-priority="${esc(ticket.priority)}">${priorityText(ticket.priority)}</small>`:''}${ticket.response_due_at_effective?`<small class="support-due">${t('responseDue')}: ${esc(i18n.date(ticket.response_due_at_effective))}</small>`:''}</div><div class="support-owner-cell"><span class="support-cell-label">${t('assignee')}</span><span data-user-content>${esc(ticket.assignee_name||t('unassigned'))}</span></div><div class="support-date-cell"><span class="support-cell-label">${t('lastMessage')}</span><span>${esc(i18n.date(ticket.last_message_at))}</span></div></article>`).join('')||`<div class="support-empty">${workspaceText('empty')}</div>`;
      $('supportMore').hidden=nextOffset===null;$('supportRows').querySelectorAll('[data-support-id]').forEach(button=>button.onclick=()=>open(button.dataset.supportId,'',false,true));$('supportRows').querySelectorAll('.support-row').forEach(row=>row.onclick=event=>{if(event.target.closest('button,a,input,select')||window.getSelection?.()?.toString())return;row.querySelector('[data-support-id]')?.click();});if(focusedTicket&&!modalOpen)[...$('supportRows').querySelectorAll('[data-support-id]')].find(button=>button.dataset.supportId===focusedTicket)?.focus({preventScroll:true});if(!modalOpen)notice('');
    }catch(error){if(version===epoch&&revision===listRevision)notice(error.message);}
  }
  function messages(items,ticket){return items.map(m=>{const kind=String(m.message_type||'text').toLowerCase(),isMedia=['image','video','audio','voice','document'].includes(kind),meta=[m.media_filename||m.file_name||'',m.media_size_bytes?`${m.media_size_bytes} B`: ''].filter(Boolean).join(' · ');return `<article class="support-message" data-message-id="${esc(m.id)}" data-direction="${esc(m.direction)}"><small>${esc(safeIdentityName(m.author_name||m.sender_name)||(m.direction==='incoming'?t('customer'):t('employeeAI')))} · ${esc(i18n.date(m.happened_at||m.created_at))}</small><div data-message-body data-user-content>${esc(m.body||mediaTitle(kind))}</div><small>${esc(mediaTitle(kind))}${meta?' · '+esc(meta):''}</small>${isMedia&&ticket?.lead_id&&m.external_id?`<button type="button" class="outline" data-ticket-media-open="${esc(m.id)}" data-external-id="${esc(m.external_id)}" data-media-kind="${esc(kind)}" data-lead-id="${esc(ticket.lead_id)}" data-channel-id="${esc(ticket.channel_id)}" data-conversation-id="${esc(ticket.conversation_id)}">${t('caseMediaOpen')}</button><small data-media-status role="status">${esc(t('caseMediaLazy'))}</small>`:''}</article>`;}).join('');}
  function clearTicketMedia(){for(const entry of ticketMedia.values()){entry.media?.pause?.();if(entry.media)entry.media.removeAttribute('src');URL.revokeObjectURL(entry.url);}ticketMedia.clear();}
  async function openTicketMedia(button){const id=button.dataset.ticketMediaOpen,kind=button.dataset.mediaKind,article=button.closest('.support-message'),status=article?.querySelector('[data-media-status]');if(!id||!status)return;const existing=ticketMedia.get(id);if(existing){existing.node.hidden=!existing.node.hidden;return;}button.disabled=true;status.textContent=t('loading');try{const query=new URLSearchParams({lead_id:button.dataset.leadId,message_id:button.dataset.externalId,channel_id:button.dataset.channelId,conversation_id:button.dataset.conversationId,expected_tenant_id:String(member?.tenant_id||''),expected_user_id:String(member?.telegram_id||'')});const response=await fetch('/api/platform/crm/chat/attachment?'+query,{credentials:'same-origin',cache:'no-store'});if(!response.ok)throw new Error(t('error'));const blob=await response.blob();if(!button.isConnected||!modalOpen||!selected) return;const url=URL.createObjectURL(blob),wrap=document.createElement('div');wrap.className='support-media-preview';let media;if(kind==='image'){media=document.createElement('img');media.alt=t('photo');media.className='support-photo';const zoom=document.createElement('button');zoom.type='button';zoom.className='outline';zoom.textContent=t('openOriginal');zoom.onclick=()=>media.classList.toggle('is-zoomed');wrap.append(media,zoom);media.src=url;}else if(kind==='video'){media=document.createElement('video');media.controls=true;media.preload='metadata';media.src=url;wrap.append(media);}else if(['audio','voice'].includes(kind)){media=document.createElement('audio');media.controls=true;media.preload='none';media.src=url;wrap.append(media);}else{const open=document.createElement('a'),download=document.createElement('a');open.href=url;open.target='_blank';open.rel='noopener noreferrer';open.textContent=t('caseMediaOpen');download.href=url;download.download='attachment';download.textContent=t('downloadAttachment');wrap.append(open,download);}article.append(wrap);ticketMedia.set(id,{url,node:wrap,media});status.textContent='';}catch(error){status.textContent=error.message||t('error');}finally{button.disabled=false;}}

  function renderDetail(result){
    const ticket=result.ticket;ticket.support_update_allowed=result.capabilities?.update!==false;
    detailData?.consultationUI?.dispose();clearTicketMedia();selected=ticket.id;nextBefore=result.next_before;
    detailData={ticket,messages:new Map(),attachedMedia:[...(result.attached_media||[])],scope:result.scope,historyContext:result.history_context,tabScroll:{},activeTab:null};rememberMessages(result.messages);
    const host=$('supportDetailContent'),canOpen=window.tenantOperator?.hasRight('dialogs');
    host.innerHTML=`<div class="support-heading"><div><small title="${esc(ticket.id)}">#${esc(ticket.id.slice(0,8).toUpperCase())}</small><h2 id="supportTicketTitle" data-user-content>${esc(displayName(ticket))}</h2>${verifiedCasePhone(ticket)?`<small id="supportVerifiedPhone" data-user-content>${esc(verifiedCasePhone(ticket))}</small>`:''}</div><button type="button" class="outline" id="supportClose">${t('close')}</button></div><p id="supportDetailNotice" role="alert" aria-live="polite"></p><div class="support-edit"><label>${t('description')}<textarea id="supportDescription" maxlength="4000" rows="3">${esc(ticket.description)}</textarea></label><label>${t('nextSteps')}<textarea id="supportNextSteps" maxlength="4000" rows="3">${esc(ticket.next_steps)}</textarea></label><div class="support-meta"><label>${t('status')}<select id="supportEditStatus">${statuses.map(key=>`<option value="${key}" ${key===ticket.status?'selected':''}>${t(key)}</option>`).join('')}</select></label><label>${t('assignee')}<select id="supportEditAssignee">${assigneeOptions(ticket.assigned_to)}</select></label></div><button type="button" class="primary" id="supportSave">${t('save')}</button></div><div class="support-actions"><button type="button" class="primary" id="supportTake">${t('take')}</button><button type="button" class="outline" id="supportOpenChat" ${canOpen?'':'disabled'}>${t('openChat')}</button><button type="button" class="outline" id="supportResolve">${t('resolve')}</button><button type="button" class="outline" id="supportCreateTask">${t('caseCreateTask')}</button></div><button type="button" class="outline" id="supportEarlier" ${nextBefore?'':'hidden'}>${t('earlier')}</button><div id="supportHistory">${messages(result.messages,ticket)||t('emptyHistory')}</div>`;
    installCaseTabs(ticket);
    if(operatorWorkspaceAvailable){const createTask=$('supportCreateTask');createTask.disabled=true;createTask.title='Тикет не отправлен. Создание через Operator отключено до подтверждения ответа, идемпотентности и публичной ссылки.';const hint=document.createElement('p');hint.className='sub';hint.textContent='Создание тикета через Operator ожидает подтверждения API-контракта';$('supportPanel-details').append(hint);}
    $('supportClose').onclick=()=>closeTicketModal();
    $('supportSave').onclick=()=>{drafts.delete(ticket.id);mutate(ticket.id,{status:$('supportEditStatus').value,assigned_to:$('supportEditAssignee').value||null,description:$('supportDescription').value,next_steps:$('supportNextSteps').value});};
    $('supportTake').onclick=()=>mutate(ticket.id,{status:'in_progress',assigned_to:member.telegram_id});
    $('supportResolve').onclick=()=>mutate(ticket.id,{status:'resolved'});
    $('supportOpenChat').onclick=()=>openCustomerChat(ticket);
    $('supportCreateTask').onclick=async()=>{const button=$('supportCreateTask');if(operatorWorkspaceAvailable){button.disabled=true;return;}button.disabled=true;try{const tasks=window.tenantExternalTasks;if(!tasks?.open)throw Error('unavailable');const issue=String(ticket.description||ticket.summary||'').replace(/\s+/g,' ').trim().slice(0,900),prefix=({ru:'Поддержка',az:'Dəstək',en:'Support'}[i18n.language]||'Support');const taskText=`${prefix} · #${ticket.id.slice(0,8)}${issue?` · ${issue}`:''}`.slice(0,3500);const opened=await tasks.open({initial:{text:taskText,lead_id:ticket.lead_id}});if(!opened)throw Error('unavailable');}catch{notice(t('caseTaskUnavailable'));}finally{if(button.isConnected)button.disabled=false;}};
    $('supportEarlier').onclick=()=>void loadDialogueHistory(true);
    renderConsultations(ticket);organizeTicketWorkspace(ticket);
    if(result.capabilities?.update===false){for(const id of ['supportSave','supportTake','supportResolve','supportDescription','supportNextSteps','supportEditStatus','supportEditAssignee']){const control=$(id);if(control)control.disabled=true;}}
    const tab=new URLSearchParams(location.search).get('support_ticket_tab')||caseTabs.get(identity(member)+':'+ticket.id)||'history';selectCaseTab(['history','details','developer'].includes(tab)?tab:'history');
  }

  function renderConsultations(ticket){
    if(!detailData||detailData.ticket.id!==ticket.id)return;
    if(detailData.consultationUI){detailData.consultationUI.refreshMaterials();return;}
    if(!window.tenantCaseConsultations)return;
    const revision=detailRevision;
    detailData.consultationUI=window.tenantCaseConsultations.mount({host:$('supportPanel-developer'),ticket,member,getMessages:materialMessages,isCurrent:()=>modalOpen&&selected===ticket.id&&revision===detailRevision,notice});
  }
  function openCustomerChat(ticket,composeDraft=''){
    const route={provider:'connect',lead_id:ticket.lead_id,channel_id:ticket.channel_id,conversation_id:ticket.conversation_id,...(composeDraft?{compose_draft:composeDraft}:{})};
    closeTicketModal(true);if(new URLSearchParams(location.search).has('support_ticket_id'))writeTicketRoute('','',true);window.tenantAppNavigate?.('dialogs',false);document.dispatchEvent(new CustomEvent('tenant-support-open',{detail:route}));
  }
  function renderFocusedAttachment(result){
    const item=result.focus_message,ticket=result.ticket,host=$('supportPanel-details');if(!item||!host)return;
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
    section.append(status);host.prepend(section);selectCaseTab('details');
  }
  const caseNotificationCopy={
    ru:{source:'Событие уведомления',ai_handoff:'Требуется ответ сотрудника',ai_consultation:'Требуется консультация разработчика',customer_requested_manager:'Клиент попросил сотрудника.',customer_dissatisfied:'Клиент выразил недовольство.',unsupported_evidence:'Для ответа недостаточно проверенных подтверждений. Сотруднику нужно проверить обращение.',unknown:'Не удалось завершить действие. Откройте обращение для проверки.',original:'Исходный текст',technical:'Технические подробности',unavailable:'Связанное обращение или событие недоступно. Возможно, ссылка устарела или доступ изменился.',forbidden:'Нет доступа к этому обращению. Проверьте текущие права.',changed:'Связь уведомления с обращением изменилась. Обновите уведомления и откройте его ещё раз.',failure:'Не удалось загрузить обращение и исходное событие. Попробуйте ещё раз.'},
    az:{source:'Bildiriş hadisəsi',ai_handoff:'Əməkdaşın cavabı tələb olunur',ai_consultation:'Tərtibatçı məsləhəti tələb olunur',customer_requested_manager:'Müştəri əməkdaş istədi.',customer_dissatisfied:'Müştəri narazılıq bildirdi.',unsupported_evidence:'Cavab üçün yoxlanmış təsdiqlər kifayət deyil. Əməkdaş müraciəti yoxlamalıdır.',unknown:'Əməliyyatı tamamlamaq olmadı. Yoxlamaq üçün müraciəti açın.',original:'İlkin mətn',technical:'Texniki təfərrüatlar',unavailable:'Əlaqəli müraciət və ya hadisə əlçatan deyil. Link köhnəlmiş və ya giriş dəyişmiş ola bilər.',forbidden:'Bu müraciətə giriş yoxdur. Cari hüquqları yoxlayın.',changed:'Bildirişin müraciətlə əlaqəsi dəyişib. Bildirişləri yeniləyin və yenidən açın.',failure:'Müraciəti və ilkin hadisəni yükləmək olmadı. Yenidən cəhd edin.'},
    en:{source:'Notification event',ai_handoff:'An employee needs to reply',ai_consultation:'A developer consultation is needed',customer_requested_manager:'The customer asked for an employee.',customer_dissatisfied:'The customer expressed dissatisfaction.',unsupported_evidence:'There is not enough verified evidence for an answer. An employee needs to review the case.',unknown:'Could not complete the action. Open the case to review it.',original:'Original text',technical:'Technical details',unavailable:'The linked case or event is unavailable. The link may be outdated or access may have changed.',forbidden:'You do not have access to this case. Check your current permissions.',changed:'The notification’s case link has changed. Refresh notifications and open it again.',failure:'Could not load the case and its source event. Please try again.'}
  };
  const caseNotificationText=key=>(caseNotificationCopy[i18n.language]||caseNotificationCopy.en)[key];
  const notificationUuid=value=>/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(String(value||''));
  function caseNotificationReason(code){for(const known of ['customer_requested_manager','customer_dissatisfied','unsupported_evidence'])if([known,'ai_no_answer_handoff:'+known,'ai_developer_consultation:'+known].includes(code))return caseNotificationText(known);return caseNotificationText('unknown')}
  const caseNotificationError=error=>caseNotificationText(error?.status===403?'forbidden':error?.status===409?'changed':error?.status===404||error?.message==='notification_context_invalid'?'unavailable':'failure');
  function validNotificationContext(context,ticketId,notificationId){return context&&context.notification_id===notificationId&&context.case_id===ticketId&&['ai_handoff','ai_consultation'].includes(context.event_kind)&&typeof context.event_key==='string'&&context.event_key.length>0&&context.event_key.length<=1000&&typeof context.reason_code==='string'&&typeof context.title==='string'&&typeof context.body==='string'&&typeof context.created_at==='string'&&Number.isFinite(Date.parse(context.created_at))}
  function renderNotificationContext(context){
    const card=document.createElement('section');card.id='supportNotificationContext';card.tabIndex=-1;card.className='support-message';card.setAttribute('aria-label',caseNotificationText('source'));card.dataset.notificationId=context.notification_id;
    const heading=document.createElement('h3'),time=document.createElement('small'),reason=document.createElement('p');heading.textContent=caseNotificationText(context.event_kind);time.textContent=i18n.date(context.created_at);reason.textContent=caseNotificationReason(context.reason_code);card.append(heading,time,reason);if(context.event_kind==='ai_consultation'){const body=document.createElement('p');body.dataset.notificationBody='';body.setAttribute('data-user-content','');body.textContent=context.body;card.append(body)}
    const original=document.createElement('details'),originalLabel=document.createElement('summary');originalLabel.textContent=caseNotificationText('original');original.append(originalLabel);for(const value of [context.title,context.body]){const text=document.createElement('p');text.textContent=value;text.setAttribute('data-user-content','');original.append(text)}card.append(original);
    const technical=document.createElement('details'),label=document.createElement('summary'),code=document.createElement('p');label.textContent=caseNotificationText('technical');code.textContent=context.reason_code;technical.append(label,code);card.append(technical);$('supportDetailNotice').after(card);return card;
  }
  function ticketUrl(id='',focus='',notificationId=''){const url=new URL(location.href);if(id){if(url.searchParams.get('support_ticket_id')!==id){url.searchParams.delete('support_ticket_tab');url.searchParams.delete('consultation_reply_id');}url.searchParams.set('view','support');url.searchParams.set('support_ticket_id',id);if(focus)url.searchParams.set('support_ticket_message_id',focus);else url.searchParams.delete('support_ticket_message_id');if(notificationId){url.searchParams.set('notification_id',notificationId);url.searchParams.delete('consultation_reply_id')}else url.searchParams.delete('notification_id');}else{url.searchParams.delete('support_ticket_id');url.searchParams.delete('support_ticket_tab');url.searchParams.delete('consultation_reply_id');url.searchParams.delete('support_ticket_message_id');url.searchParams.delete('notification_id');}return url;}
  function writeTicketRoute(id,focus='',replace=false,notificationId=''){const url=ticketUrl(id,focus,notificationId),state={...history.state,...(id?{view:'support'}:{}),supportTicketModal:!!id,supportTicketId:id||null,supportNotificationId:notificationId||null};history[replace?'replaceState':'pushState'](state,'',url.pathname+url.search+url.hash);}
  function showTicketModal(){const modal=$('supportDetail');if(!modal)return;if(!modalOpen){returnFocus=document.activeElement;bodyOverflow=document.body.style.overflow;document.body.style.overflow='hidden';$('supportInbox')?.setAttribute('inert','');modalOpen=true;}modal.hidden=false;}
  function saveDraft(id){const content=$('supportDetailContent');if(!id||!content)return;const values={};content.querySelectorAll('input,textarea,select').forEach((node,index)=>{const key=node.id||node.dataset.consultAnswer||node.dataset.groupDraft||node.dataset.caseMediaCheck||(node.dataset.shareMessage?'share:'+node.value:null)||('field-'+index);const serverField=({supportDescription:'description',supportNextSteps:'next_steps',supportEditStatus:'status',supportEditAssignee:'assigned_to'})[key];if(serverField&&detailData?.ticket?.id===id&&String(node.value)===String(detailData.ticket[serverField]??''))return;values[key]={value:node.value,checked:!!node.checked};});if(Object.keys(values).length)drafts.set(id,values);}
  function restoreDraft(id){const values=drafts.get(id),content=$('supportDetailContent');if(!values||!content)return;content.querySelectorAll('input,textarea,select').forEach((node,index)=>{const key=node.id||node.dataset.consultAnswer||node.dataset.groupDraft||node.dataset.caseMediaCheck||(node.dataset.shareMessage?'share:'+node.value:null)||('field-'+index),value=values[key];if(value){node.value=value.value;if('checked'in node)node.checked=value.checked;}});}
  function closeTicketModal(fromHistory=false){if(!modalOpen)return;const closedId=selected,returnBell=returnFocus?.id==='dialogConsultationBell'?returnFocus:null;saveDraft(closedId);detailData?.consultationUI?.dispose();clearTicketMedia();detailData=null;detailRevision++;selected=null;modalOpen=false;$('supportDialogueHistory')?.replaceChildren();document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));const modal=$('supportDetail');if(modal)modal.hidden=true;document.body.style.overflow=bodyOverflow;$('supportInbox')?.removeAttribute('inert');if(!fromHistory){if(history.state?.supportTicketModal){
      if(returnBell){
        const version=epoch,revision=detailRevision,frame=window.requestAnimationFrame||function(callback){setTimeout(callback,0)};
        window.addEventListener('popstate',()=>frame(()=>frame(()=>{
          // Mobile navigation reparents the top bar. Retry only when that move lost focus.
          if(version!==epoch||revision!==detailRevision||modalOpen||document.activeElement!==document.body||!returnBell.isConnected||!returnBell.getClientRects().length)return;
          returnBell.focus({preventScroll:true});
        })),{once:true});
      }
      history.back();
    }else{routeChange=true;const url=ticketUrl();history.replaceState({...history.state,supportTicketModal:false,supportTicketId:null},'',url.pathname+url.search+url.hash);}}const fallback=[...document.querySelectorAll('[data-support-id]')].find(node=>node.dataset.supportId===closedId);if(returnFocus?.isConnected&&returnFocus!==document.body){const target=returnFocus.closest?.('.app-user-menu[hidden]')?document.querySelector('.app-avatar-button'):returnFocus;target?.focus();}else fallback?.focus();returnFocus=null;}
  function trapTicketFocus(event){
    if(!modalOpen)return;
    if(event.key==='Escape'){event.preventDefault();closeTicketModal();return;}
    if(event.key!=='Tab')return;
    const container=$('supportDetailContent'),nodes=[...container.querySelectorAll('button,a[href],input,select,textarea,summary,[tabindex]')].filter(node=>{
      if(node.tabIndex<0||node.matches(':disabled')||node.closest('[hidden],[inert]'))return false;
      // A closed details keeps only its first summary (and that summary's controls) in the tab order.
      for(let parent=node.parentElement;parent&&parent!==container;parent=parent.parentElement){
        if(parent.tagName==='DETAILS'&&!parent.open&&![...parent.children].find(child=>child.tagName==='SUMMARY')?.contains(node))return false;
      }
      return node.checkVisibility?.({visibilityProperty:true})!==false;
    });
    if(!nodes.length){event.preventDefault();container.focus();return;}
    const first=nodes[0],last=nodes[nodes.length-1];
    if(event.shiftKey&&(document.activeElement===first||document.activeElement===container)){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&(document.activeElement===last||document.activeElement===container)){event.preventDefault();first.focus();}
  }
  async function open(id,focus='',fromHistory=false,pushRoute=false,options={}){
    if(selected)saveDraft(selected);if(selected!==id)clearTicketMedia();const revision=++detailRevision,version=epoch;selected=id;showTicketModal();
    if(pushRoute){writeTicketRoute(id,focus);directTicketKey=epoch+':'+id+':'+focus;}
    const title=$('supportDetailContent');title.innerHTML=`<div class="support-heading"><h2>${esc(t('loading'))}</h2><button type="button" class="outline" id="supportClose">${t('close')}</button></div>`;$('supportClose').onclick=()=>closeTicketModal();
    const query=new URLSearchParams(focus?{message_id:focus}:{});if(options.notificationId){query.set('notification_id',options.notificationId);query.set('expected_tenant_id',String(member?.tenant_id||''));query.set('expected_user_id',String(member?.telegram_id||''));}
    try{const result=await api('/'+encodeURIComponent(id)+(query.size?'?'+query:''));if(revision!==detailRevision||selected!==id)return false;
      if(String(result.ticket?.id).toLowerCase()!==String(id).toLowerCase())throw new Error(t('error'));
      if(options.notificationId&&!validNotificationContext(result.notification_context,id,options.notificationId))throw new Error('notification_context_invalid');
      renderDetail(result);renderFocusedAttachment(result);restoreDraft(id);if(options.notificationId){selectCaseTab('details');detailData.notificationContext=result.notification_context;renderNotificationContext(result.notification_context);$('supportNotificationContext').focus({preventScroll:true});}else ($('supportClose')||$('supportDetailContent')).focus();return true;
    }catch(error){if(version===epoch&&revision===detailRevision){if(options.keepError){const message=document.createElement('p');message.id='supportDetailNotice';message.setAttribute('role','alert');message.setAttribute('aria-live','polite');message.textContent=options.notificationId?caseNotificationError(error):error.message;title.append(message);if(options.notificationId){message.tabIndex=-1;message.focus({preventScroll:true});const details=document.createElement('details'),label=document.createElement('summary'),code=document.createElement('p');label.textContent=caseNotificationText('technical');code.textContent=error.message;details.append(label,code);title.append(details);}}else{notice(error.message);closeTicketModal();}}return false;}
  }
  async function openNotification({ticketId,replyId}={}){
    if(!member||!allowed()||typeof ticketId!=='string'||!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(ticketId)||typeof replyId!=='string'||!replyId||replyId.length>200)return false;
    ticketId=ticketId.toLowerCase();const version=epoch,owner=identity(member);install();if(!$('supportDetailContent'))return false;
    directTicketKey=epoch+':'+ticketId+':';writeTicketRoute(ticketId,'',new URLSearchParams(location.search).get('support_ticket_id')===ticketId);
    window.tenantAppNavigate?.('support',false);
    if(version!==epoch||owner!==identity(member)||!allowed())return false;
    if(!await open(ticketId,'',true,false,{keepError:true}))return false;
    selectCaseTab('developer');const replyUrl=new URL(location.href);replyUrl.searchParams.set('consultation_reply_id',replyId);history.replaceState(history.state,'',replyUrl.pathname+replyUrl.search+replyUrl.hash);
    const revision=detailRevision,controller=detailData?.consultationUI;
    const current=()=>version===epoch&&owner===identity(member)&&revision===detailRevision&&modalOpen&&selected===ticketId&&allowed();
    try{const shown=await controller?.focusReply?.(replyId);if(!current())return false;if(shown)return true;
      if(!$('supportDetailNotice')?.textContent)notice(({ru:'Ответ разработчика недоступен в этом обращении.',az:'Tərtibatçı cavabı bu müraciətdə əlçatan deyil.',en:'The developer reply is unavailable in this case.'}[i18n.language]||'The developer reply is unavailable in this case.'));
    }catch(error){if(current())notice(error.message||t('error'));}return false;
  }
  async function openCaseNotification({ticketId,notificationId}={}){
    if(!member||!allowed()||typeof ticketId!=='string'||!notificationUuid(ticketId)||typeof notificationId!=='string'||!notificationUuid(notificationId))return false;
    ticketId=ticketId.toLowerCase();notificationId=notificationId.toLowerCase();const version=epoch,owner=identity(member);install();if(!$('supportDetailContent'))return false;
    directTicketKey=epoch+':'+ticketId+':notification:'+notificationId;writeTicketRoute(ticketId,'',new URLSearchParams(location.search).get('support_ticket_id')===ticketId&&new URLSearchParams(location.search).get('notification_id')===notificationId,notificationId);
    window.tenantAppNavigate?.('support',false);if(version!==epoch||owner!==identity(member)||!allowed())return false;
    if(!await open(ticketId,'',true,false,{keepError:true,notificationId}))return false;
    const revision=detailRevision,context=detailData?.notificationContext,card=$('supportNotificationContext');
    const current=()=>version===epoch&&owner===identity(member)&&revision===detailRevision&&modalOpen&&selected===ticketId&&allowed()&&$('view-support')?.classList.contains('active')&&new URLSearchParams(location.search).get('support_ticket_id')===ticketId&&new URLSearchParams(location.search).get('notification_id')===notificationId&&card?.isConnected&&card.dataset.notificationId===notificationId&&!$('supportDetail').hidden;
    if(!returnFocus?.isConnected||returnFocus===document.body||returnFocus?.closest?.('#dialogConsultationMenu'))returnFocus=$('dialogConsultationBell')||returnFocus;
    await new Promise(resolve=>(window.requestAnimationFrame||function(callback){setTimeout(callback,0)})(resolve));if(!current())return false;
    card.focus({preventScroll:true});card.scrollIntoView?.({block:'center'});if(!current()||document.activeElement!==card)return false;
    return Object.freeze({notification_id:context.notification_id,case_id:context.case_id,event_key:context.event_key});
  }
  window.tenantSupportInbox={openNotification,openCaseNotification};
  function openDirectTicket(){
    if(!member||!allowed())return;
    const query=new URLSearchParams(location.search),id=(query.get('support_ticket_id')||'').toLowerCase(),focus=query.get('support_ticket_message_id')||'',notificationId=(query.get('notification_id')||'').toLowerCase();
    if(query.get('view')!=='support'||!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id))return;
    if(focus&&!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(focus))return;
    const key=notificationId?epoch+':'+id+':notification:'+notificationId:epoch+':'+id+':'+focus;if(directTicketKey===key)return;if(notificationId&&!notificationUuid(notificationId)){directTicketKey=key;install();notice(caseNotificationText('unavailable'));return;}
    install();if(!$('supportDetail'))return;
    directTicketKey=key;const replyId=query.get('consultation_reply_id');if(replyId&&!notificationId){void openNotification({ticketId:id,replyId});return;}void open(id,focus,true,false,notificationId?{keepError:true,notificationId}:{});
  }
  async function action(id,path,method,data){const revision=++detailRevision,version=epoch,host=$('supportDetail');host?.querySelectorAll('button').forEach(button=>button.disabled=true);try{const result=await api('/'+encodeURIComponent(id)+path,method,data);if(revision!==detailRevision)return;if(result.readable===false){closeTicketModal();await load();return;}renderDetail(result);await load();}catch(error){if(version===epoch){await open(id);notice(error.message);}}}
  const mutate=(id,data)=>action(id,'','PATCH',data);
  document.addEventListener('tenant-chat-open',event=>{
    const lead=event.detail.lead_id,version=epoch;if(!member||!allowed())return;
    const host=$('modalContent');if(!host||$('supportCreateFromChat')||event.detail.surface==='operator'||event.detail.surface==='native-deal')return;
    const button=document.createElement('button');button.type='button';button.className='outline';button.id='supportCreateFromChat';
    button.textContent=t('createTicket');button.title=t('createTicket');button.setAttribute('aria-label',t('createTicket'));
    (host.querySelector('.deal-chat-header')||host.querySelector('.deal-card-data-actions')||host).append(button);
    button.onclick=async()=>{if(button.disabled)return;button.disabled=true;try{const operator=await ensureManualCaseUI();if(version!==epoch||!button.isConnected)return;if(!operator.hasRight('support')||!operator.hasRight('dialogs'))throw Error(t('noAccess'));button.disabled=false;await operator.openManualCaseCreate({provider:'connect',lead_id:lead,...(event.detail.channel_id?{channel_id:event.detail.channel_id}:{}),...(event.detail.conversation_id?{conversation_id:event.detail.conversation_id}:{})},button,()=>version===epoch&&button.isConnected);}catch(error){if(version===epoch&&button.isConnected){button.disabled=false;notice(error.message);}}};

  });
  function profileChanged(data){const rightsChanged=JSON.stringify(accessCapabilities)!==JSON.stringify(data.capabilities||{});accessCapabilities=data.capabilities||{};operatorWorkspaceAvailable=Array.isArray(data.integrations)&&data.integrations.some(row=>row.provider==='beinsystems_operator');const person=data.member;if(rightsChanged||!allowed()||identity(person)!==identity(member)||JSON.stringify(person?.permissions)!==JSON.stringify(member?.permissions)||person?.active!==member?.active){epoch++;listRevision++;detailRevision++;directTicketKey='';selected=null;detailData?.consultationUI?.dispose();clearTicketMedia();detailData=null;drafts.clear();rows=[];members=[];channels=[];nextOffset=null;nextBefore=null;if($('supportRows'))$('supportRows').replaceChildren();if($('supportCount'))$('supportCount').textContent='';if($('supportDetail')){$('supportDetail').hidden=true;$('supportDetailContent')?.replaceChildren();modalOpen=false;document.body.style.overflow=bodyOverflow;$('supportInbox')?.removeAttribute('inert');returnFocus=null;}badge(0);notice('');}member=person;if(allowed()){load();openDirectTicket();}}
  const syncSupportViewport=()=>{const host=$('supportDetail');if(!host)return;host.style.setProperty('--support-visual-top',(window.visualViewport?.offsetTop||0)+'px');host.style.setProperty('--support-visual-height',(window.visualViewport?.height||window.innerHeight)+'px');};window.addEventListener('resize',syncSupportViewport);window.visualViewport?.addEventListener('resize',syncSupportViewport);window.visualViewport?.addEventListener('scroll',syncSupportViewport);
  document.addEventListener('keydown',trapTicketFocus);
  window.addEventListener('popstate',()=>{const query=new URLSearchParams(location.search),id=query.get('support_ticket_id');if(id===selected&&modalOpen&&document.querySelector('.operator-photo-viewer'))return;if(id&&query.get('view')==='support'){directTicketKey='';openDirectTicket();}else if(modalOpen)closeTicketModal(true);});
  document.addEventListener('tenant-profile',event=>profileChanged(event.detail));
  document.addEventListener('tenant-view',event=>{if(event.detail.view==='support'){load();openDirectTicket();}else if(modalOpen){closeTicketModal(true);if(new URLSearchParams(location.search).has('support_ticket_id'))writeTicketRoute('','',true);}});
  document.addEventListener('tenant-support-changed',()=>load());
  document.addEventListener('tenant-dialog-state',()=>{load();if(selected)open(selected);});
  document.addEventListener('tenant-operator-error',event=>notice(event.detail.message));
  document.addEventListener('tenant-language',()=>{if(!$('supportInbox'))return;const status=$('supportStatus').value,assignee=$('supportAssignee').value,channel=$('supportChannel').value,search=$('supportSearch').value,id=selected,opener=returnFocus;saveDraft(id);modalOpen=false;document.body.style.overflow=bodyOverflow;$('supportInbox')?.removeAttribute('inert');$('supportDetail')?.remove();$('supportInbox').remove();install();$('supportStatus').value=status;$('supportAssignee').value=assignee;$('supportChannel').value=channel;$('supportSearch').value=search;load();if(id){const notificationId=new URLSearchParams(location.search).get('notification_id')?.toLowerCase();open(id,'',true,false,notificationId?{keepError:true,notificationId}:{});}if(opener?.isConnected)returnFocus=opener;});
  if(window.tenantProfileSnapshot)profileChanged(window.tenantProfileSnapshot);else fetch(window.TENANT_AI_AGENTS_CONFIG?.profileUrl||'/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():null).then(data=>{if(!member&&data?.member)profileChanged(data);}).catch(()=>{});
})();
