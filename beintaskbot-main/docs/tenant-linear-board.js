/* Tenant task board. Server capabilities are authoritative; no drag/drop for staff. */
(() => {
  const $=id=>document.getElementById(id);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let profile,epoch=0,rows=[],cursor=null,version='',rights={},loading=false,sending=false,pending=null,loadedSearch='',dialogEpoch=0;
  document.head.insertAdjacentHTML('beforeend',`<style>
    .tl-toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:18px}.tl-toolbar .search{min-width:140px}
    .tl-board{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(290px,100%),1fr));gap:16px;align-items:start}
    .tl-column{min-width:0}.tl-card{padding:15px;margin:12px 0;border:1px solid var(--line);border-radius:13px;background:#fff;overflow-wrap:anywhere}
    .tl-card h3{margin:6px 0;font-size:16px}.tl-card .badge{margin:4px 4px 4px 0}.tl-actions{display:flex;gap:7px;flex-wrap:wrap;margin-top:12px}
    #tlTaskModal .modal-card{max-height:85dvh;overflow:auto;padding-bottom:calc(22px + env(safe-area-inset-bottom))}
    #tlTaskModal textarea{width:100%;font:inherit;min-height:110px;resize:vertical;border:1px solid var(--line);border-radius:9px;padding:10px}
    #tlTaskModal .tl-footer{position:sticky;bottom:0;background:#fff;padding:12px 0 0;display:flex;gap:8px;flex-wrap:wrap}
    @media(max-width:760px){.tl-board{grid-template-columns:1fr}.tl-toolbar input{flex-basis:100%}.tl-card{margin:10px 0}}
  </style>`);
  document.querySelector('.nav').insertAdjacentHTML('beforeend','<button data-view="linear" id="tlNav" hidden style="display:none"><span>▦</span> Linear</button>');
  document.querySelector('main').insertAdjacentHTML('beforeend',`<section class="view" id="view-linear"><div class="top"><div><h1>Linear</h1><p id="tlCount"></p></div></div>
    <div class="tl-toolbar"><input class="search" id="tlSearch" placeholder="Linear-da axtarış"><button class="outline" id="tlFind">Axtar</button><select class="search" id="tlStateFilter"><option value="">Bütün statuslar</option></select><button class="refresh" id="tlRefresh">Yenilə</button><button class="primary" id="tlCreate" hidden>+ Tapşırıq</button></div>
    <div id="tlBoardNotice" class="notice" role="status"></div><button id="tlRecover" class="outline" hidden>Yarımçıq sorğunu yoxla</button><div class="tl-board" id="tlBoard"></div><button class="outline" id="tlNext" hidden>Daha çox</button></section>
    <div class="modal" id="tlTaskModal"><div class="modal-card panel"><div class="modal-head"><h2 id="tlTaskTitle"></h2><button class="close" id="tlTaskClose" aria-label="Bağla">×</button></div><div id="tlTaskFields"></div><p id="tlTaskMessage" role="status"></p><div class="tl-footer"><button class="primary" id="tlTaskSave">Saxla</button><button class="outline" id="tlTaskCancel">Bağla</button></div></div></div>`);
  function storageKey(person=profile){return 'tenant-linear-command:'+person.tenant_id+':'+person.telegram_id;}
  function notice(text){$('tlBoardNotice').textContent=text||'';$('tlBoardNotice').classList.toggle('show',!!text);}
  async function api(params='',data){const response=await fetch('/api/platform/linear/tasks'+params,{credentials:'same-origin',cache:'no-store',
    ...(data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{})});const result=await response.json();
    if(!response.ok||!result.success){const error=new Error(result.error||'Linear sorğusu alınmadı.');error.status=response.status;throw error;}return result;}
  function date(value){const d=new Date(value);if(Number.isNaN(d.getTime()))return '—';return d.toLocaleDateString('en-GB',{timeZone:'Asia/Baku'})+' '+d.toLocaleTimeString('en-GB',{timeZone:'Asia/Baku',hour12:false});}
  function render(){const filter=$('tlStateFilter').value;const statuses=new Map(rows.map(row=>[row.state?.id,row.state]));
    $('tlStateFilter').innerHTML='<option value="">Bütün statuslar</option>'+[...statuses.values()].filter(Boolean).map(s=>`<option value="${esc(s.id)}">${esc(s.name)} (${rows.filter(r=>r.state?.id===s.id).length})</option>`).join('');
    if(statuses.has(filter))$('tlStateFilter').value=filter;const selected=rows.filter(row=>!$('tlStateFilter').value||row.state?.id===$('tlStateFilter').value);
    $('tlCount').textContent=`Yüklənib: ${rows.length} · Görünür: ${selected.length}`;
    $('tlCreate').hidden=!rights.can_create;$('tlNext').hidden=!cursor;$('tlRecover').hidden=!pending;
    const priorities=['Yoxdur','Təcili','Yüksək','Normal','Aşağı'];
    $('tlBoard').innerHTML=[...new Set(selected.map(row=>row.state?.id))].map(id=>`<section class="tl-column"><h2 style="font-size:17px">${esc(statuses.get(id)?.name||'Status')} · ${selected.filter(row=>row.state?.id===id).length}</h2>`+
      selected.filter(row=>row.state?.id===id).map(row=>`<article class="tl-card" data-issue="${esc(row.id)}"><div class="sub">${esc(row.identifier)}</div><div class="badge" style="font-size:15px">${esc(row.account||'Account göstərilməyib')}</div>
        <h3>${esc(row.title)}</h3><span class="badge" style="background:${row.priority===1?'#ffe5e5':row.priority===2?'#fff0d1':'#e8f7ef'}">${priorities[row.priority]||'—'}</span><span class="badge">${esc(row.state?.name)}</span>
        <div class="sub">Layihə: ${esc(row.project?.name||'—')}</div><div class="sub">Operator: ${esc(row.operator||'—')}</div><div class="sub">İcraçı: ${esc(row.assignee?.name||'—')}</div><div class="sub">${date(row.createdAt)}</div>
        <details style="margin-top:8px"><summary>Açıqlama</summary><div style="white-space:pre-wrap">${esc(row.body)}</div></details><div class="tl-actions">
        ${row.capabilities?.can_edit?'<button class="outline" data-edit>Redaktə</button>':''}${row.capabilities?.can_change_status?'<button class="outline" data-status>Status</button>':''}
        ${(row.capabilities?.buttons||[]).map(button=>`<button class="outline" data-button-id="${esc(button.id)}">${esc(button.label)}</button>`).join('')}</div></article>`).join('')+'</section>').join('')||'<div class="empty">Bu səhifədə uyğun tapşırıq yoxdur. Daha çox düyməsi varsa növbəti səhifəni açın.</div>';
  }
  async function load(more=false){if(loading||!profile)return;const current=epoch;const search=$('tlSearch').value.trim();if(more&&search!==loadedSearch){notice('Axtarış dəyişib. Əvvəlcə Axtar düyməsini basın.');return;}loading=true;$('tlRefresh').disabled=true;$('tlNext').disabled=true;notice('Yüklənir…');
    try{const result=await api('?search='+encodeURIComponent($('tlSearch').value.trim())+(more&&cursor?'&after='+encodeURIComponent(cursor):''));
      if(current!==epoch)return;if(String(result.tenant_id)!==String(profile.tenant_id)||String(result.user_id)!==String(profile.telegram_id))throw new Error('Kabinet dəyişib. Yeniləyin.');
      if(more&&version!==result.config_version){rows=[];cursor=null;rights={};render();throw new Error('Qaydalar dəyişib. Siyahını yeniləyin.');}
      rows=more?[...new Map([...rows,...result.issues].map(row=>[row.id,row])).values()]:result.issues;version=result.config_version;rights=result.capabilities;loadedSearch=search;
      cursor=result.pageInfo.hasNextPage?result.pageInfo.endCursor:null;notice('');render();
    }catch(error){if(current===epoch){if(!more||[403,409].includes(error.status)){rows=[];cursor=null;rights={};render();}notice(error.message);}}
    finally{if(current===epoch){loading=false;$('tlRefresh').disabled=false;$('tlNext').disabled=false;}}}
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
    if(changed||!allowed){epoch++;rows=[];cursor=null;rights={};version='';loading=false;sending=false;pending=null;$('tlSearch').value='';$('tlTaskModal').classList.remove('show');$('tlRefresh').disabled=false;$('tlNext').disabled=false;render();
      try{const saved=JSON.parse(sessionStorage.getItem(storageKey())||'null');if(saved&&String(saved.expected_tenant_id)===String(next.tenant_id)&&String(saved.expected_user_id)===String(next.telegram_id))pending=saved;}catch{}
      $('tlRecover').hidden=!pending;if(!allowed){$('view-linear').classList.remove('active');return;}}
  }
  $('tlNav').onclick=()=>{document.querySelectorAll('.nav button').forEach(b=>b.classList.toggle('active',b===$('tlNav')));document.querySelectorAll('.view').forEach(view=>view.classList.toggle('active',view.id==='view-linear'));load();};
  $('tlRefresh').onclick=()=>load();$('tlFind').onclick=()=>load();$('tlNext').onclick=()=>load(true);$('tlStateFilter').onchange=render;
  $('tlSearch').onkeydown=event=>{if(event.key==='Enter')load();};$('tlCreate').onclick=()=>open('create');$('tlRecover').onclick=frozen;$('tlTaskClose').onclick=close;$('tlTaskCancel').onclick=close;
  $('tlBoard').onclick=event=>{const card=event.target.closest('[data-issue]');if(!card)return;const row=rows.find(item=>item.id===card.dataset.issue);if(!row)return;
    if(event.target.closest('[data-edit]'))open('edit',row);else if(event.target.closest('[data-status]'))open('status',row);else{const action=event.target.closest('[data-button-id]');if(action){const button=row.capabilities.buttons.find(b=>b.id===action.dataset.buttonId);if(button)open('button',row,button);}}};
  document.addEventListener('tenant-profile',event=>apply(event.detail));const initial=epoch;
  fetch('/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():null).then(data=>{if(initial===epoch&&data?.member)apply(data);}).catch(()=>{});
})();
