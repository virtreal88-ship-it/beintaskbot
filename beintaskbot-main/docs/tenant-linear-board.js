/* Tenant task board. Server capabilities are authoritative; no drag/drop for staff. */
(() => {
  const $=id=>document.getElementById(id);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let profile,epoch=0,loadEpoch=0,rows=[],statuses=[],cursor=null,version='',rights={},loading=false,sending=false,pending=null,loadedSearch='',dialogEpoch=0,selectedState=null;
  document.head.insertAdjacentHTML('beforeend',`<style>
    .tl-toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:18px}.tl-toolbar .search{min-width:140px}
    .tl-column-trigger{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:42px;padding:8px 12px;border:1px solid var(--app-line,var(--line,#d4d4d8));border-radius:9px;background:var(--app-surface,var(--card,#fff));color:var(--app-ink,var(--ink,#18181b));font:inherit;font-weight:700;cursor:pointer}
    .tl-column-trigger:hover{background:var(--app-hover,var(--app-raised,#f4f4f5))}
    .tl-column-trigger:focus-visible,.tl-column-dialog :focus-visible{outline:3px solid var(--app-accent,var(--brand,#0284c7));outline-offset:2px}
    .tl-column-dialog{width:min(440px,calc(100vw - 24px));max-width:calc(100vw - 24px);max-height:min(82dvh,640px);margin:auto;padding:0;overflow:hidden;border:1px solid var(--app-line,var(--line,#d4d4d8));border-radius:16px;background:var(--app-surface,var(--card,#fff));color:var(--app-ink,var(--ink,#18181b));box-shadow:0 24px 70px rgba(0,0,0,.32);color-scheme:inherit}
    .tl-column-dialog::backdrop{background:rgba(2,6,23,.58)}
    .tl-column-dialog__surface{max-height:min(82dvh,640px);overflow:auto;padding:18px}
    .tl-column-dialog__header{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;margin-bottom:14px}
    .tl-column-dialog__header h2{margin:0;color:var(--app-ink,var(--ink,#18181b));font-size:16px;font-weight:800}
    .tl-column-dialog__header p{margin:4px 0 0;color:var(--app-muted,var(--muted,#71717a));font-size:12px;line-height:1.45}
    .tl-column-dialog__close,.tl-column-dialog__reset{border:1px solid var(--app-line,var(--line,#d4d4d8));border-radius:10px;background:var(--app-raised,var(--app-surface,var(--card,#fff)));color:var(--app-ink,var(--ink,#18181b));cursor:pointer}
    .tl-column-dialog__close{display:inline-flex;align-items:center;justify-content:center;flex:none;width:38px;height:38px;font-size:22px}
    .tl-column-options{display:grid;gap:4px;max-height:min(55dvh,420px);overflow:auto;padding:2px}
    .tl-column-option{display:flex;align-items:center;justify-content:space-between;gap:14px;min-height:44px;padding:8px 10px;border:1px solid transparent;border-radius:10px;color:var(--app-ink,var(--ink,#18181b));cursor:pointer}
    .tl-column-option:hover{border-color:var(--app-line,var(--line,#d4d4d8));background:var(--app-hover,var(--app-raised,#f4f4f5))}
    .tl-column-option:focus-within{outline:3px solid var(--app-accent,var(--brand,#0284c7));outline-offset:2px}
    .tl-column-option__name{display:flex;align-items:center;gap:9px;min-width:0;font-size:13px;font-weight:700}
    .tl-column-option__dot{width:9px;height:9px;flex:none;border-radius:50%;outline:1px solid rgba(100,116,139,.4)}
    .tl-column-option input[type=checkbox]{width:20px;height:20px;flex:none;margin:0;accent-color:var(--app-accent,var(--brand,#0284c7))}
    .tl-column-dialog__reset{display:block;width:100%;min-height:44px;margin-top:12px;padding:8px 12px;font:inherit;font-size:12px;font-weight:700}
    @media(max-width:480px){.tl-column-dialog{width:calc(100vw - 16px);max-width:calc(100vw - 16px);max-height:calc(100dvh - 20px)}.tl-column-dialog__surface{max-height:calc(100dvh - 20px);padding:16px}}
    .tl-board{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(min(290px,100%),1fr);gap:16px;align-items:start;min-width:0;max-width:100%;overflow-x:auto;padding-bottom:12px;overscroll-behavior-x:contain}
    .tl-column{min-width:0}.tl-card{padding:15px;margin:12px 0;border:1px solid var(--line);border-radius:13px;background:#fff;overflow-wrap:anywhere}
    .tl-card h3{margin:6px 0;font-size:16px}.tl-card .badge{margin:4px 4px 4px 0}.tl-actions{display:flex;gap:7px;flex-wrap:wrap;margin-top:12px}
    #tlTaskModal .modal-card{max-height:85dvh;overflow:auto;padding-bottom:calc(22px + env(safe-area-inset-bottom))}
    #tlTaskModal textarea{width:100%;font:inherit;min-height:110px;resize:vertical;border:1px solid var(--line);border-radius:9px;padding:10px}
    #tlTaskModal .tl-footer{position:sticky;bottom:0;background:#fff;padding:12px 0 0;display:flex;gap:8px;flex-wrap:wrap}
    @media(max-width:760px){.tl-toolbar input{flex-basis:100%}.tl-card{margin:10px 0}}
  </style>`);
  document.querySelector('.nav').insertAdjacentHTML('beforeend','<button data-view="linear" id="tlNav" hidden style="display:none"><span>▦</span> Linear</button>');
  document.querySelector('main').insertAdjacentHTML('beforeend',`<section class="view" id="view-linear"><div class="top"><div><h1>Linear</h1><p id="tlCount"></p></div></div>
    <div class="tl-toolbar"><input class="search" id="tlSearch" placeholder="Linear-da axtarış"><button class="outline" id="tlFind">Axtar</button><select class="search" id="tlStateFilter"><option value="">Bütün statuslar</option></select><button class="tl-column-trigger" type="button" id="tlColumnsOpen" aria-haspopup="dialog" aria-controls="tlColumnDialog" aria-expanded="false">Sütunlar <span aria-hidden="true">▾</span></button><dialog class="tl-column-dialog" id="tlColumnDialog" aria-labelledby="tlColumnDialogTitle"><div class="tl-column-dialog__surface"><div class="tl-column-dialog__header"><div><h2 id="tlColumnDialogTitle">Sütunlar</h2><p>Status sütunlarının görünüşünü seçin.</p></div><button class="tl-column-dialog__close" type="button" id="tlColumnsClose" aria-label="Bağla">×</button></div><div class="tl-column-options" id="tlColumnOptions" role="group" aria-label="Status sütunları"></div><button class="tl-column-dialog__reset" type="button" id="tlColumnsAll">Bütün sütunları göstər</button></div></dialog><button class="refresh" id="tlRefresh">Yenilə</button><button class="primary" id="tlCreate" hidden>+ Tapşırıq</button><button class="outline" id="tlNext" hidden>Daha çox</button></div>
    <div id="tlBoardNotice" class="notice" role="status"></div><button id="tlRecover" class="outline" hidden>Yarımçıq sorğunu yoxla</button><div class="tl-board" id="tlBoard" tabindex="0" role="region" aria-label="Linear tapşırıq lövhəsi"></div>
    </section>
    <div class="modal" id="tlTaskModal"><div class="modal-card panel"><div class="modal-head"><h2 id="tlTaskTitle"></h2><button class="close" id="tlTaskClose" aria-label="Bağla">×</button></div><div id="tlTaskFields"></div><p id="tlTaskMessage" role="status"></p><div class="tl-footer"><button class="primary" id="tlTaskSave">Saxla</button><button class="outline" id="tlTaskCancel">Bağla</button></div></div></div>`);
  function storageKey(person=profile){return 'tenant-linear-command:'+person.tenant_id+':'+person.telegram_id;}
  function columnStorageKey(person=profile){return 'tenant-linear-columns:'+person.tenant_id+':'+person.telegram_id;}
  function hiddenColumns(){try{return new Set(JSON.parse(localStorage.getItem(columnStorageKey())||'[]').map(String));}catch{return new Set();}}
  function renderColumnOptions(catalog,hidden){const container=$('tlColumnOptions');if(!container)return;const dialog=$('tlColumnDialog');const activeId=dialog?.open?document.activeElement?.dataset?.linearColumnId:null;
    container.innerHTML=catalog.map(state=>{const id=String(state.id);const name=String(state.name||'Status');const raw=String(state.color||'');const color=/^#[0-9a-f]{3,8}$/i.test(raw)?raw:'#64748b';return `<label class="tl-column-option" for="tl-column-${esc(id)}"><span class="tl-column-option__name"><span class="tl-column-option__dot" style="background:${color}"></span><span>${esc(name)}</span></span><input id="tl-column-${esc(id)}" type="checkbox" data-linear-column-toggle data-linear-column-id="${esc(id)}" value="${esc(id)}" ${hidden.has(id)?'':'checked'} aria-label="Sütunu göstər: ${esc(name)}"></label>`;}).join('');
    if(activeId&&dialog?.open)[...container.querySelectorAll('input[data-linear-column-toggle]')].find(input=>input.dataset.linearColumnId===activeId)?.focus({preventScroll:true});}
  function saveHiddenColumns(){const hidden=[...$('tlColumnOptions').querySelectorAll('input[data-linear-column-toggle]')].filter(input=>!input.checked).map(input=>String(input.value));try{localStorage.setItem(columnStorageKey(),JSON.stringify(hidden));}catch{}}
  function openColumnDialog(){const dialog=$('tlColumnDialog');if(!dialog)return;if(!dialog.open){if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','');}$('tlColumnsOpen').setAttribute('aria-expanded','true');dialog.querySelector('input[type="checkbox"]')?.focus({preventScroll:true});}
  function closeColumnDialog(){const dialog=$('tlColumnDialog');if(dialog?.open)dialog.close();else dialog?.removeAttribute('open');}
  function hasAccount(row){const value=String(row.account||'').trim().toLocaleLowerCase('az');return !!value&&!['göstərilməyib','gosterilmeyib','account göstərilməyib','account gosterilmeyib','—','-'].includes(value);}
  function notice(text){$('tlBoardNotice').textContent=text||'';$('tlBoardNotice').classList.toggle('show',!!text);}
  async function api(params='',data){const response=await fetch('/api/platform/linear/tasks'+params,{credentials:'same-origin',cache:'no-store',
    ...(data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{})});const result=await response.json();
    if(!response.ok||!result.success){const error=new Error(result.error||'Linear sorğusu alınmadı.');error.status=response.status;throw error;}return result;}
  function date(value){const d=new Date(value);if(Number.isNaN(d.getTime()))return '—';return d.toLocaleDateString('en-GB',{timeZone:'Asia/Baku'})+' '+d.toLocaleTimeString('en-GB',{timeZone:'Asia/Baku',hour12:false});}
  function render(){
    const filter=selectedState?.id||'';
    const ordered=new Map();
    [...statuses,...rows.map(row=>row.state).filter(Boolean)].forEach(state=>{if(state?.id&&!ordered.has(String(state.id)))ordered.set(String(state.id),state);});
    if(selectedState&&!ordered.has(String(filter)))ordered.set(String(filter),selectedState);
    const catalog=[...ordered.values()];
    $('tlStateFilter').innerHTML='<option value="">Bütün statuslar</option>'+catalog.map(state=>`<option value="${esc(state.id)}">${esc(state.name)} (${rows.filter(row=>row.state?.id===state.id&&hasAccount(row)).length})</option>`).join('');
    if(ordered.has(String(filter)))$('tlStateFilter').value=filter;
    const hidden=hiddenColumns();
    renderColumnOptions(catalog,hidden);
    const selected=rows.filter(row=>hasAccount(row)&&(!$('tlStateFilter').value||row.state?.id===$('tlStateFilter').value));
    const visibleStatuses=catalog.filter(state=>!hidden.has(String(state.id))&&($('tlStateFilter').value===''||state.id===$('tlStateFilter').value));
    $('tlCount').textContent=`Yüklənib: ${rows.filter(hasAccount).length} · Görünür: ${selected.length}`;
    $('tlCreate').hidden=!rights.can_create;$('tlNext').hidden=!cursor;$('tlRecover').hidden=!pending;
    const priorities=['Yoxdur','Təcili','Yüksək','Normal','Aşağı'];
    $('tlBoard').innerHTML=visibleStatuses.map(state=>{const items=selected.filter(row=>String(row.state?.id)===String(state.id));return `<section class="tl-column" data-state-id="${esc(state.id)}"><h2 style="font-size:17px">${esc(state.name||'Status')} · ${items.length}</h2>`+
      items.map(row=>`<article class="tl-card" data-issue="${esc(row.id)}"><div class="sub">${/^https:\/\/linear\.app\//i.test(String(row.url||''))?`<a href="${esc(row.url)}" target="_blank" rel="noopener">${esc(row.identifier)}</a>`:esc(row.identifier)}</div><div class="badge" style="font-size:15px">${esc(row.account)}</div>
        <h3>${esc(row.title)}</h3><span class="badge" style="background:${row.priority===1?'#ffe5e5':row.priority===2?'#fff0d1':'#e8f7ef'}">${priorities[row.priority]||'—'}</span><span class="badge">${esc(row.state?.name)}</span>
        <div class="sub">Layihə: ${esc(row.project?.name||'—')}</div><div class="sub">Operator: ${esc(row.operator||'—')}</div><div class="sub">İcraçı: ${esc(row.assignee?.name||'—')}</div><div class="sub">${date(row.createdAt)}</div>
        <details style="margin-top:8px"><summary>Açıqlama</summary><div style="white-space:pre-wrap">${esc(row.body)}</div></details><div class="tl-actions">
        ${row.capabilities?.can_edit?'<button class="outline" data-edit>Redaktə</button>':''}${row.capabilities?.can_change_status?'<button class="outline" data-status>Status</button>':''}
        ${(row.capabilities?.buttons||[]).map(button=>`<button class="outline" data-button-id="${esc(button.id)}">${esc(button.label)}</button>`).join('')}</div></article>`).join('')+'</section>';}).join('')||'<div class="empty">Bu səhifədə uyğun tapşırıq yoxdur. Daha çox düyməsi varsa növbəti səhifəni açın.</div>';
  }
  async function load(more=false){if(!profile||(more&&loading))return;const current=epoch,request=++loadEpoch;const search=$('tlSearch').value.trim();
    if(more&&search!==loadedSearch){notice('Axtarış dəyişib. Əvvəlcə Axtar düyməsini basın.');return;}
    const active=()=>current===epoch&&request===loadEpoch;let append=more;const seen=new Set();
    loading=true;$('tlNext').disabled=true;notice('Yüklənir…');
    if(!more){rows=[];cursor=null;rights={};render();}
    try{for(let page=0;page<100;page++){
      const after=append?cursor:null;if(after)seen.add(after);
      const result=await api('?search='+encodeURIComponent(search)+(after?'&after='+encodeURIComponent(after):''));
      if(!active())return;if(String(result.tenant_id)!==String(profile.tenant_id)||String(result.user_id)!==String(profile.telegram_id)){const error=new Error('Kabinet dəyişib. Yeniləyin.');error.status=403;throw error;}
      if(append&&version!==result.config_version){const error=new Error('Qaydalar dəyişib. Siyahını yeniləyin.');error.status=409;throw error;}
      rows=append?[...new Map([...rows,...result.issues].map(row=>[row.id,row])).values()]:result.issues;if(!append&&Array.isArray(result.statuses))statuses=result.statuses;version=result.config_version;rights=result.capabilities;loadedSearch=search;
      const next=result.pageInfo.hasNextPage?result.pageInfo.endCursor:null;
      cursor=next;render();append=true;
      if(result.pageInfo.hasNextPage&&(!next||seen.has(next))){cursor=null;render();throw new Error('Səhifələmə dayandı. Siyahını yeniləyin.');}
      if(!cursor){notice('');return;}
      notice(`Yüklənir… ${rows.length} tapşırıq`);
    }
    notice('Yükləmə limiti çatdı. Davam etmək üçün Daha çox düyməsini basın.');
    }catch(error){if(active()){if(!append||[403,409].includes(error.status)){rows=[];cursor=null;rights={};render();}notice(error.message);}}
    finally{if(active()){loading=false;$('tlRefresh').disabled=false;$('tlNext').disabled=false;}}}
  function close(){if(!sending){dialogEpoch++;$('tlTaskModal').classList.remove('show');}}
  function frozen(){if(!pending)return;const current=epoch;$('tlTaskTitle').textContent='Yarımçıq sorğu';$('tlTaskFields').innerHTML='<p>Sorğunun məlumatları saxlanılıb. Yenidən yoxlama eyni ID və eyni məlumatla aparılır; yeni tapşırıq yaradılmır.</p>';
    $('tlTaskMessage').textContent='';$('tlTaskSave').textContent='Eyni sorğunu yoxla';$('tlTaskSave').onclick=()=>send(pending,current);$('tlTaskModal').classList.add('show');}
  async function open(action,row,button){if(pending){frozen();return;}const current=epoch,dialog=++dialogEpoch;let choices;
    try{if(action==='create'||action==='edit'||action==='status')choices=await api('?catalog=1');}catch(error){if(current===epoch&&dialog===dialogEpoch)notice(error.message);return;}if(current!==epoch||dialog!==dialogEpoch)return;
    const binding=choices?.binding||{};const options=(items,value)=>items.map(item=>`<option value="${esc(item.id)}" ${item.id===value?'selected':''}>${esc(item.name)}</option>`).join('');
    $('tlTaskTitle').textContent=action==='create'?'Yeni Linear tapşırığı':action==='edit'?'Tapşırığı redaktə et':button?.label||'Status';$('tlTaskMessage').textContent='';$('tlTaskSave').textContent='Təsdiqlə';
    if(action==='button')$('tlTaskFields').innerHTML=button.require_reason?'<label>Səbəb (məcburi)<textarea id="tlReason" maxlength="2000"></textarea></label>':'<p>Tapşırığın statusunu dəyişmək istəyirsiniz?</p>';
    else if(action==='status')$('tlTaskFields').innerHTML=`<label>Status<select id="tlNewState">${options(choices.states,row.state?.id)}</select></label>`;
    else $('tlTaskFields').innerHTML=`<label>Başlıq<input id="tlTitleInput" maxlength="255" value="${esc(row?.title||'')}"></label>
      ${choices.owner?`<label>Account<input id="tlAccountInput" maxlength="160" value="${esc(row?.account||'')}"></label><label>Operator<input id="tlOperatorInput" maxlength="160" value="${esc(row?.operator||'')}"></label>`:`<p>Account və Operator administratorun təyin etdiyi qaydaya görə saxlanılır.</p>`}
      <label>Layihə<select id="tlProjectInput"><option value="">Seçin</option>${options(choices.projects,row?.project?.id)}</select></label>
      <label>Prioritet<select id="tlPriorityInput">${['Yoxdur','Təcili','Yüksək','Normal','Aşağı'].map((name,value)=>`<option value="${value}" ${value===(row?.priority??0)?'selected':''}>${name}</option>`).join('')}</select></label>
      <label>İcraçı<select id="tlAssigneeInput"><option value="">Təyin edilməyib</option>${options(choices.assignees,row?.assignee?.id||binding.assignee_id)}</select></label>
      <label>Açıqlama<textarea id="tlBodyInput" maxlength="10000">${esc(row?.body||'')}</textarea></label>`;
    $('tlTaskModal').classList.add('show');
    $('tlTaskSave').onclick=()=>{if(current!==epoch)return;let fields={};
      if(action==='button'){fields={button_id:button.id,reason:$('tlReason')?.value.trim()||''};if(button.require_reason&&!fields.reason){$('tlTaskMessage').textContent='Səbəbi yazın.';return;}}
      else if(action==='status')fields={state_id:$('tlNewState').value};
      else {fields={title:$('tlTitleInput').value.trim(),body:$('tlBodyInput').value.trim(),project_id:$('tlProjectInput').value,
        priority:Number($('tlPriorityInput').value),assignee_id:$('tlAssigneeInput').value,...(choices.owner?{account:$('tlAccountInput').value.trim(),operator:$('tlOperatorInput').value.trim()}:{})};
        const required=choices.required_fields||['account','operator','project','body'];
        if(!fields.title||required.some(name=>name==='project'?!fields.project_id:['account','operator'].includes(name)?choices.owner&&!fields[name]:!fields[name])){$('tlTaskMessage').textContent='Bütün məcburi sahələri doldurun.';return;}}
      send({action,...fields,request_id:crypto.randomUUID(),issue_id:row?.id,expected_updated_at:row?.updatedAt,
        config_version:choices?.config_version||version,expected_tenant_id:profile.tenant_id,expected_user_id:profile.telegram_id},current);
    };
    const textarea=$('tlBodyInput')||$('tlReason');if(textarea){const resize=()=>{textarea.style.height='auto';textarea.style.height=Math.min(textarea.scrollHeight,320)+'px';};textarea.oninput=resize;resize();}
  }
  async function send(data,current){if(sending||current!==epoch)return;const key=storageKey();pending=data;
    try{sessionStorage.setItem(key,JSON.stringify(data));}catch{pending=null;$('tlTaskMessage').textContent='Sorğunu təhlükəsiz saxlamaq alınmadı.';return;}
    sending=true;$('tlTaskSave').disabled=true;$('tlTaskMessage').textContent='Göndərilir…';$('tlTaskFields').querySelectorAll('input,textarea,select').forEach(input=>input.disabled=true);
    try{await api('',data);sessionStorage.removeItem(key);if(current!==epoch)return;pending=null;sending=false;close();await load();}
    catch(error){if(current!==epoch)return;if(error.status===400){sessionStorage.removeItem(key);pending=null;$('tlTaskFields').querySelectorAll('input,textarea,select').forEach(input=>input.disabled=false);}
      else {$('tlTaskSave').onclick=()=>send(pending,current);$('tlTaskSave').textContent='Eyni sorğunu yoxla';} $('tlTaskMessage').textContent=error.message;render();}
    finally{if(current===epoch){sending=false;$('tlTaskSave').disabled=false;}}
  }
  function apply(data){const next=data.member;const identity=JSON.stringify([next.tenant_id,next.telegram_id,next.role,next.permissions,next.workflow,data.capabilities]);
    const changed=!profile||identity!==profile.identity;profile={...next,identity};const allowed=!!data.capabilities?.modules?.linear;
    $('tlNav').hidden=!allowed;$('tlNav').style.display=allowed?'':'none';
    if(changed||!allowed){epoch++;selectedState=null;rows=[];statuses=[];cursor=null;rights={};version='';loading=false;sending=false;pending=null;$('tlSearch').value='';$('tlTaskModal').classList.remove('show');$('tlRefresh').disabled=false;$('tlNext').disabled=false;render();
      try{const saved=JSON.parse(sessionStorage.getItem(storageKey())||'null');if(saved&&String(saved.expected_tenant_id)===String(next.tenant_id)&&String(saved.expected_user_id)===String(next.telegram_id))pending=saved;}catch{}
      $('tlRecover').hidden=!pending;if(!allowed){$('view-linear').classList.remove('active');return;}}
    return changed&&allowed;
  }
  $('tlNav').onclick=()=>{if(window.tenantAppNavigate?.('linear',true))return;document.querySelectorAll('.nav button').forEach(b=>b.classList.toggle('active',b===$('tlNav')));document.querySelectorAll('.view').forEach(view=>view.classList.toggle('active',view.id==='view-linear'));load();};
  $('tlRefresh').onclick=()=>load();$('tlFind').onclick=()=>load();$('tlNext').onclick=()=>load(true);$('tlStateFilter').onchange=()=>{selectedState=statuses.find(state=>state.id===$('tlStateFilter').value)||rows.find(row=>row.state?.id===$('tlStateFilter').value)?.state||null;render();};
  $('tlColumnsOpen').onclick=openColumnDialog;$('tlColumnsClose').onclick=closeColumnDialog;$('tlColumnOptions').onchange=event=>{if(event.target?.matches?.('input[data-linear-column-toggle]')){saveHiddenColumns();render();}};
  $('tlColumnsAll').onclick=()=>{try{localStorage.setItem(columnStorageKey(),'[]');}catch{}render();$('tlColumnsAll').focus({preventScroll:true});};
  $('tlColumnDialog').addEventListener('close',()=>{$('tlColumnsOpen').setAttribute('aria-expanded','false');$('tlColumnsOpen').focus({preventScroll:true});});
  $('tlColumnDialog').addEventListener('cancel',event=>{event.preventDefault();closeColumnDialog();});
  $('tlColumnDialog').addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();closeColumnDialog();}});
  $('tlColumnDialog').addEventListener('click',event=>{if(event.target===$('tlColumnDialog'))closeColumnDialog();});
  $('tlSearch').onkeydown=event=>{if(event.key==='Enter')load();};$('tlCreate').onclick=()=>open('create');$('tlRecover').onclick=frozen;$('tlTaskClose').onclick=close;$('tlTaskCancel').onclick=close;
  $('tlBoard').onclick=event=>{const card=event.target.closest('[data-issue]');if(!card)return;const row=rows.find(item=>item.id===card.dataset.issue);if(!row)return;
    if(event.target.closest('[data-edit]'))open('edit',row);else if(event.target.closest('[data-status]'))open('status',row);else{const action=event.target.closest('[data-button-id]');if(action){const button=row.capabilities.buttons.find(b=>b.id===action.dataset.buttonId);if(button)open('button',row,button);}}};
  function restoreView(){
    if($('tlNav').hidden)return;
    const intended=new URLSearchParams(window.location?.search||'').get('view')||sessionStorage.getItem('tenant.app.view');
    if(intended==='linear'&&!$('view-linear').classList.contains('active'))window.tenantAppNavigate?.('linear',false);
    if($('view-linear').classList.contains('active')&&!loading)load();
  }
  document.addEventListener('tenant-view',event=>{if(event.detail?.view==='linear')load();});
  document.addEventListener('tenant-profile',event=>{const changed=apply(event.detail);if(changed)restoreView();});const initial=epoch;
  fetch('/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():null).then(data=>{if(initial===epoch&&data?.member){apply(data);restoreView();}}).catch(()=>{});
})();
