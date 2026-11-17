/* Scoped, read-only related content. Actions reuse the tenant task workflow. */
(() => {
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels=()=>({ru:{tasks:'Задачи',notes:'Заметки',create:'Создать задачу',emptyTasks:'В загруженной части списка задач этой сделки нет.',emptyNotes:'Заметок нет.',loading:'Загружаем…',error:'Не удалось загрузить. Повторите открытие карточки.',limited:'Показана только загруженная часть списка.',chat:'Переписка'},az:{tasks:'Tapşırıqlar',notes:'Qeydlər',create:'Tapşırıq yarat',emptyTasks:'Yüklənmiş siyahıda bu sövdələşmənin tapşırığı yoxdur.',emptyNotes:'Qeyd yoxdur.',loading:'Yüklənir…',error:'Yükləmək mümkün olmadı. Kartı yenidən açın.',limited:'Siyahının yalnız yüklənmiş hissəsi göstərilir.',chat:'Yazışma'},en:{tasks:'Tasks',notes:'Notes',create:'Create task',emptyTasks:'No tasks for this deal in the loaded list.',emptyNotes:'No notes.',loading:'Loading…',error:'Could not load. Reopen the card to retry.',limited:'Only the loaded part of the list is shown.',chat:'Conversation'}}[window.tenantI18n?.language]||{tasks:'Tapşırıqlar',notes:'Qeydlər',create:'Tapşırıq yarat',emptyTasks:'Tapşırıq yoxdur.',emptyNotes:'Qeyd yoxdur.',loading:'Yüklənir…',error:'Yükləmə xətası',limited:'Yüklənmiş hissə',chat:'Yazışma'});
  const style=document.createElement('style');style.textContent='.kommo-deal-sections{margin-top:20px;display:grid;gap:22px}.kommo-deal-section{min-width:0}.kommo-deal-section header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}.kommo-deal-section h3{margin:0;font-size:14px;color:var(--muted);font-weight:600}.kommo-related-card{display:block;width:100%;box-sizing:border-box;text-align:left;padding:14px;margin-top:10px;border:1px solid var(--line);border-left:3px solid var(--brand);border-radius:10px;background:var(--card);color:var(--ink);font:inherit;overflow-wrap:anywhere;white-space:pre-wrap}.kommo-related-card small{display:block;color:var(--muted);margin-bottom:8px;font-size:12px}.kommo-note-card{border-left-color:#efb957}.kommo-deal-section button{min-height:44px}.kommo-deal-chat-title{font-size:14px;color:var(--muted);margin:24px 0 12px}#dealModal .modal-card{max-height:calc(100dvh - 24px);overflow:auto;box-sizing:border-box;width:calc(100% - 24px);padding-bottom:max(22px,env(safe-area-inset-bottom))}#dealModal.deal-conversation{padding:0}#dealModal.deal-conversation>.modal-card{width:100%;max-width:none;height:100dvh;max-height:100dvh;border-radius:0;padding:0;display:flex;flex-direction:column;overflow:hidden}#dealModal.deal-conversation .modal-head{position:static;flex:none;display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid var(--line);margin:0}#dealModal .deal-chat-identity{flex:1;min-width:0}#dealModal .deal-chat-identity h2{font-size:17px;margin:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}#dealModal .deal-chat-phone{font-size:14px;margin-top:4px;overflow-wrap:anywhere}#dealModal .deal-info-toggle,#dealModal #closeModal{flex:none;min-width:44px;min-height:44px}#dealModal.deal-conversation #modalContent{flex:1;min-height:0;overflow:hidden;display:flex;flex-direction:column}#dealModal.deal-conversation .chat-placeholder{flex:1;min-height:0;display:flex;flex-direction:column;margin:0;border-radius:0;padding:0;overflow:hidden}#dealModal.deal-conversation .deal-chat-history{flex:1;min-height:0;overflow:auto;overscroll-behavior:contain;padding:12px 16px}#dealModal.deal-conversation #chatRows{max-height:none;overflow:visible}#dealModal.deal-conversation .chat-placeholder>div:not(.deal-chat-history){flex:none;padding:10px 16px;margin:0!important}#dealModal.deal-conversation .deal-info-panel{min-height:0;flex:1;overflow:auto;padding:16px;padding-bottom:max(16px,env(safe-area-inset-bottom))}#dealModal.deal-conversation [hidden]{display:none!important}#dealModal.deal-conversation #chatText{min-width:0;color:var(--ink);background:var(--card)!important}#dealModal.deal-conversation .chat-placeholder{padding-bottom:env(safe-area-inset-bottom)}#dealModal.deal-conversation .chat-placeholder>.deal-chat-compose{flex:0 1 auto;min-height:0;max-height:60%;overflow:auto;overscroll-behavior:contain;box-sizing:border-box}#dealModal.deal-conversation .deal-chat-compose textarea{max-height:min(160px,20dvh);overflow-y:auto!important;min-height:44px;box-sizing:border-box}#dealModal.deal-conversation #tcAiResult{min-width:0;overflow-wrap:anywhere}#dealModal.deal-conversation .deal-chat-compose>div:first-child{min-width:0}#dealModal.deal-conversation .deal-chat-compose button{max-width:100%;white-space:normal}';document.head.append(style);
  const stageStyle=document.createElement('style');stageStyle.textContent=`
/* Reference: 1080px capture at 3x, 812x76px pill across a 10px stage line. */
#dealModal.deal-conversation>.modal-card{border:0}
#dealModal.deal-conversation .modal-head{border-bottom:0}
.deal-stage-bar{position:relative;isolation:isolate;flex:none;display:flex;align-items:center;flex-direction:column;justify-content:center;min-height:44px;padding:0;gap:0;box-sizing:border-box}
.deal-stage-bar::before{content:"";position:absolute;z-index:-1;left:0;right:0;top:20px;height:3px;background:#fade96}
.deal-stage-pill{display:flex;align-items:stretch;width:75.2%;max-width:520px;height:26px;box-sizing:border-box;border:1px solid #cdd1d4;border-radius:10px;background:#fff;color:#657986}
.deal-stage-pill>.deal-pipeline-name{display:block;box-sizing:border-box;flex:0 0 39.5%;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:2px 12px;border:0;background:none;color:#657986;font:400 15px/20px Arial,sans-serif}
#dealModal .deal-stage-trigger{position:relative;display:flex;align-items:center;gap:10px;box-sizing:border-box;flex:1;min-width:0;max-width:none;height:24px;min-height:0;padding:0 12px 0 15px;border:0;border-left:1px solid #cdd1d4;border-radius:0 9px 9px 0;background:#fade96!important;color:#363b44;font:400 15px/20px Arial,sans-serif;opacity:1}
.deal-stage-trigger::before{content:"";position:absolute;inset:-10px 0}
.deal-stage-trigger::after{content:"";flex:none;width:6px;height:6px;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:rotate(45deg);margin:0 2px 3px 0}
.deal-stage-trigger-label{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left}
.deal-stage-trigger:focus-visible{outline:2px solid #363b44;outline-offset:3px}
.deal-stage-bar>[role=status]:not(:empty){align-self:stretch;padding:6px;color:var(--ink);background:var(--card);font-size:12px;overflow-wrap:anywhere}
.deal-stage-picker{position:absolute;inset:0;z-index:60;background:#0006;display:flex;align-items:center;justify-content:center;padding:16px;box-sizing:border-box}
.deal-stage-sheet{width:100%;max-width:520px;max-height:80dvh;overflow:auto;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:14px;box-shadow:0 12px 40px #0003}
.deal-stage-sheet header{display:flex;align-items:center;gap:10px;padding:14px;border-bottom:1px solid var(--line)}
.deal-stage-sheet select{flex:1;min-width:0;max-width:100%;font:inherit;font-weight:600;color:var(--ink);background:var(--card);border:0;min-height:44px}
.deal-stage-sheet button{min-height:44px}.deal-stage-choice{display:flex;width:100%;align-items:center;gap:14px;text-align:left;padding:16px;background:none;color:var(--ink);border:0;border-bottom:1px solid var(--line);border-radius:0;font:inherit}
.deal-stage-dot{height:16px;width:16px;border-radius:50%;flex:none;background:var(--stage-color,#ffe08b)}.deal-stage-choice-name{flex:1;overflow-wrap:anywhere}
@media(max-width:600px){.deal-stage-picker{align-items:flex-end;padding:0}.deal-stage-sheet{max-width:none;border-radius:16px 16px 0 0;padding-bottom:env(safe-area-inset-bottom)}}
#dealModal.deal-desktop-sidebar #modalContent{flex-direction:row;align-items:stretch}
#dealModal.deal-desktop-sidebar .deal-info-panel{flex:0 0 clamp(300px,30vw,440px);width:clamp(300px,30vw,440px);box-sizing:border-box;border-right:1px solid var(--line);overscroll-behavior:contain}
#dealModal.deal-desktop-sidebar .chat-placeholder{min-width:0}
`;document.head.append(stageStyle);
  const date=value=>{const d=new Date(typeof value==='number'?value*1000:value);return value&&!Number.isNaN(d.getTime())?d.toLocaleString():'';};
  let cleanupLayout=()=>{};
  function mount({host,deal,api=window.tenantTaskFlow?.api,isCurrent}){
    cleanupLayout();
    const c=labels(),container=document.createElement('div');container.className='kommo-deal-sections';container.innerHTML=`<section class="kommo-deal-section"><header><h3>${esc(c.tasks)}</h3><button type="button" class="primary" data-related-create hidden>+ ${esc(c.create)}</button></header><div data-related-tasks role="status">${esc(c.loading)}</div></section><section class="kommo-deal-section"><header><h3>${esc(c.notes)}</h3></header><div data-related-notes role="status">${esc(c.loading)}</div></section>`;
    const chat=host.querySelector('.chat-placeholder');host.insertBefore(container,chat);if(chat){const heading=document.createElement('h3');heading.className='kommo-deal-chat-title';heading.textContent=c.chat;chat.before(heading);}
    // Conversation-first card: related content stays available behind Info, not above chat.
    const modal=document.getElementById('dealModal'),head=modal.querySelector('.modal-head');
    modal.querySelectorAll('.deal-stage-bar,.deal-stage-picker').forEach(node=>node.remove());
    const previousTitle=document.getElementById('modalTitle');if(previousTitle&&previousTitle.parentElement!==head)head.prepend(previousTitle);
    head.querySelectorAll('[data-deal-header-extra]').forEach(node=>node.remove());
    modal.classList.add('deal-conversation');modal.querySelector('.modal-card').setAttribute('role','dialog');modal.querySelector('.modal-card').setAttribute('aria-modal','true');modal.querySelector('.modal-card').setAttribute('aria-labelledby','modalTitle');
    const title=document.getElementById('modalTitle');title.textContent=deal.contact_name||deal.name||c.chat;
    const identity=document.createElement('div');identity.className='deal-chat-identity';identity.dataset.dealHeaderExtra='1';
    title.before(identity);identity.append(title);
    const phone=document.createElement('div');phone.className='deal-chat-phone';phone.textContent=deal.phone||'—';identity.append(phone);
    const info=document.createElement('section');info.id='dealInfoPanel';info.className='deal-info-panel';info.hidden=true;
    for(const node of [...host.children])if(node!==chat&& !node.classList.contains('kommo-deal-chat-title'))info.append(node);
    host.querySelector('.kommo-deal-chat-title')?.remove();host.prepend(info);
    const infoButton=document.createElement('button');infoButton.type='button';infoButton.className='outline deal-info-toggle';infoButton.dataset.dealHeaderExtra='1';
    const infoLabel=({ru:'Инфо',az:'Məlumat',en:'Info'}[window.tenantI18n?.language]||'Məlumat');
    infoButton.textContent='ⓘ';infoButton.title=infoLabel;infoButton.setAttribute('aria-label',infoLabel);infoButton.setAttribute('aria-controls',info.id);infoButton.setAttribute('aria-expanded','false');
    head.insertBefore(infoButton,document.getElementById('closeModal'));
    const desktop=window.matchMedia?.('(min-width:1024px)');
    let mobileInfoOpen=false;
    const setInfo=open=>{
      mobileInfoOpen=open;
      const sideBySide=desktop?.matches===true,visible=sideBySide||open;
      modal.classList.toggle('deal-desktop-sidebar',sideBySide);
      info.hidden=!visible;if(chat)chat.hidden=!sideBySide&&open;
      infoButton.hidden=sideBySide;infoButton.setAttribute('aria-expanded',String(visible));
      if(visible)loadRelated();
    };
    infoButton.onclick=()=>setInfo(info.hidden);
    modal.onkeydown=event=>{if(event.key==='Escape'&&!desktop?.matches&&!info.hidden){event.preventDefault();event.stopPropagation();setInfo(false);infoButton.focus();}};
    if(chat){const rows=chat.querySelector('#chatRows');if(rows){const viewport=document.createElement('div');viewport.className='deal-chat-history';rows.before(viewport);viewport.append(rows);const composer=document.createElement('div');composer.className='deal-chat-compose';for(const node of [...chat.children])if(node!==viewport)composer.append(node);chat.append(composer);}}
    const current=()=>container.isConnected&&isCurrent()&&document.getElementById('dealModal')?.classList.contains('show');
    const flow=window.tenantTaskFlow,scope=flow?.capture();
    // Reuse the board's policy catalog and immutable, receipt-verified move command.
    const stageBar=document.createElement('div');stageBar.className='deal-stage-bar';
    const stageLabel=({ru:'Этап сделки',az:'Sövdələşmə mərhələsi',en:'Deal stage'}[window.tenantI18n?.language]||'Deal stage');
    const pipeline=document.createElement('span'),trigger=document.createElement('button'),feedback=document.createElement('span');
    pipeline.className='deal-pipeline-name';pipeline.textContent=deal.pipeline_name||stageLabel;pipeline.title=pipeline.textContent;
    trigger.type='button';trigger.className='deal-stage-trigger';trigger.textContent=(deal.stage_name||stageLabel)+' ▾';trigger.setAttribute('aria-label',stageLabel);trigger.setAttribute('aria-haspopup','dialog');trigger.setAttribute('aria-expanded','false');trigger.disabled=true;
    const triggerLabel=document.createElement('span');triggerLabel.className='deal-stage-trigger-label';triggerLabel.textContent=deal.stage_name||stageLabel;trigger.title=triggerLabel.textContent;trigger.replaceChildren(triggerLabel);
    const pill=document.createElement('div');pill.className='deal-stage-pill';pill.append(pipeline,trigger);
    feedback.setAttribute('role','status');stageBar.append(pill,feedback);head.after(stageBar);
    const picker=document.createElement('div');picker.className='deal-stage-picker';picker.hidden=true;
    const sheet=document.createElement('section');sheet.className='deal-stage-sheet';sheet.setAttribute('role','dialog');sheet.setAttribute('aria-modal','true');sheet.setAttribute('aria-label',stageLabel);
    const pickerHead=document.createElement('header'),pipelineSelect=document.createElement('select'),dismiss=document.createElement('button'),choices=document.createElement('div');
    pipelineSelect.setAttribute('aria-label',({ru:'Сменить воронку',az:'Vərəqi dəyiş',en:'Change pipeline'}[window.tenantI18n?.language]||'Change pipeline'));pipelineSelect.dataset.dealPipelinePicker='';
    dismiss.type='button';dismiss.textContent='×';dismiss.setAttribute('aria-label',({ru:'Закрыть',az:'Bağla',en:'Close'}[window.tenantI18n?.language]||'Close'));
    pickerHead.append(pipelineSelect,dismiss);sheet.append(pickerHead,choices);picker.append(sheet);modal.querySelector('.modal-card').append(picker);
    const closePicker=()=>{picker.hidden=true;trigger.setAttribute('aria-expanded','false');if(current())trigger.focus();};
    dismiss.onclick=closePicker;picker.onclick=e=>{if(e.target===picker)closePicker();};
    picker.onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();closePicker();}else if(e.key==='Tab'){const nodes=[...sheet.querySelectorAll('button,select')].filter(n=>!n.disabled);if(!nodes.length)return;const first=nodes[0],last=nodes[nodes.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}};
    let stageRow={...deal},busy=false;
    void Promise.resolve().then(()=>api('/api/platform/crm/deal-board-preferences')).then(policy=>{
      if(!current())return;
      const sources=policy.move_stage_sources||[],baseTargets=policy.move_stage_targets||[];
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
        pipeline.textContent=catalog?.name||stageRow.pipeline_name||stageLabel;pipeline.title=pipeline.textContent;
        triggerLabel.textContent=stageRow.stage_name||stageLabel;trigger.title=triggerLabel.textContent;
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
      trigger.onclick=()=>{if(trigger.disabled||!current())return;render();picker.hidden=false;trigger.setAttribute('aria-expanded','true');(pipelineSelect.disabled?dismiss:pipelineSelect).focus();};
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
            if(result.completed){stageRow={...stageRow,source_revision:null,status_id:142,stage_name:catalog?.stages?.find(t=>Number(t.stage_id)===142)?.name||'Uğurla tamamlandı'};Object.assign(deal,stageRow);return;}
            throw Error('Completion unavailable');
          }
          const updated=await window.tenantKommoStageMove.move(stageRow,target,policy.move_context,targetPipeline);
          if(!current())return;
          stageRow={...updated,pipeline_name:catalog?.name||stageRow.pipeline_name,stage_name:catalog?.stages?.find(t=>String(t.stage_id)===target)?.name||stageRow.stage_name};
          Object.assign(deal,stageRow);
          document.dispatchEvent(new CustomEvent('tenant-crm-sync-complete',{detail:{tenant_id:policy.move_context.tenant_id,resources:['leads']}}));
        }catch(error){if(current())feedback.textContent=error.message||'Stage update failed';}
        finally{busy=false;if(current()){render();if(!trigger.disabled&&(picker.contains(document.activeElement)||document.activeElement===document.body))trigger.focus();}}
      };
      render();
    }).catch(()=>{if(current())trigger.disabled=true;});

    let relatedStarted=false;
    function loadRelated(){
    if(relatedStarted||!current())return;relatedStarted=true;
    const tasks=container.querySelector('[data-related-tasks]');
    if(!flow){tasks.textContent=c.error;}else void flow.read('kommo',{},scope).then(result=>{
      if(!current()||!flow.current(scope))return;
      if(result.source!=='kommo'||!Array.isArray(result.tasks)||result.sources?.find(item=>item.source==='kommo')?.view!==true)throw Error('invalid_tasks');
      const rows=result.tasks.filter(item=>String(item.kommo_lead_id)===String(deal.kommo_lead_id));
      tasks.innerHTML=rows.map(item=>`<button class="kommo-related-card" type="button" data-related-task="${esc(item.id)}"><small>${esc(item.task_type_name||'')} · ${esc(date(item.due_at))}</small>${esc(String(item.description||item.text||item.display_title||'').replace(/\n?\[CRM:[a-f0-9]{32}\]/g,''))}</button>`).join('')||`<p class="sub">${esc(c.emptyTasks)}</p>`;
      if(result.has_more)tasks.insertAdjacentHTML('beforeend',`<p class="sub">${esc(c.limited)}</p>`);
      tasks.querySelectorAll('[data-related-task]').forEach(button=>button.onclick=()=>{if(!current())return;document.getElementById('dealModal').classList.remove('show');void flow.openFromLink('kommo',button.dataset.relatedTask);});
      const create=container.querySelector('[data-related-create]');create.hidden=result.sources.find(item=>item.source==='kommo')?.create!==true;
      create.onclick=async()=>{if(!current())return;create.disabled=true;try{const opened=await flow.createForKommoDeal(deal.kommo_lead_id,current,()=>{if(container.isConnected&&isCurrent()){document.getElementById('dealModal').classList.add('show');create.focus();}});if(opened&&current())document.getElementById('dealModal').classList.remove('show');}catch{if(current())tasks.textContent=c.error;}finally{create.disabled=false;}};
    }).catch(()=>{if(current())tasks.textContent=c.error;});
    const notes=container.querySelector('[data-related-notes]');
    void (flow?flow.api('/api/platform/crm/deal/notes?lead_id='+encodeURIComponent(deal.kommo_lead_id),{scope}):Promise.reject(Error('scope_unavailable'))).then(result=>{if(!current())return;if(!Array.isArray(result.notes))throw Error('invalid_notes');window.tenantKommoDealNoteCreate?.mount({container,leadId:deal.kommo_lead_id,scope,current,canCreate:result.can_create===true,flow});notes.innerHTML=result.notes.map(item=>`<article class="kommo-related-card kommo-note-card"><small>${esc(date(item.created_at))}</small>${esc(item.text)}</article>`).join('')||`<p class="sub">${esc(c.emptyNotes)}</p>`;if(result.has_more)notes.insertAdjacentHTML('beforeend',`<p class="sub">${esc(c.limited)}</p>`);}).catch(()=>{if(current())notes.textContent=c.error;});
    }
    const syncLayout=()=>{if(container.isConnected&&isCurrent())setInfo(mobileInfoOpen);};
    desktop?.addEventListener?.('change',syncLayout);
    cleanupLayout=()=>desktop?.removeEventListener?.('change',syncLayout);
    // Related requests are independent of chat loading and never awaited here.
    syncLayout();
  }
  window.tenantKommoDealCard={mount};
})();
