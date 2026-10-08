/* Completion/review UI. Commands are durable; no polling or financial side effects. */
(() => {
  const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let profile,capabilities={},scope='',generation=0,busy=false,loading=false,rows=[],total=0,payload=null,selected=null;
  const nav=document.querySelector('[data-view="approvals"]');
  const modal=document.createElement('div');modal.id='hotCompletionModal';modal.className='modal';
  modal.innerHTML='<form class="modal-card" id="hotCompletionForm"><div class="modal-head"><h2 id="hotCompletionTitle"></h2><button type="button" class="close" id="hotCompletionClose">×</button></div><p id="hotCompletionResult" style="white-space:pre-wrap;overflow-wrap:anywhere"></p><label id="hotCompletionLabel">Nəticə<textarea id="hotCompletionText" maxlength="8000"></textarea></label><div class="notice" role="status" id="hotCompletionNotice"></div><div class="hot-footer"><button type="submit" class="primary" id="hotCompletionSubmit">Təsdiq et</button><button type="button" class="outline" id="hotCompletionCancel">Bağla</button></div></form>';
  document.body.append(modal);
  const style=document.createElement('style');style.textContent='#hotCompletionModal{z-index:50}#hotCompletionModal .modal-card{max-height:calc(100dvh - 24px);overflow:auto;padding-bottom:max(24px,env(safe-area-inset-bottom))}#hotCompletionModal textarea{display:block;width:100%;min-height:100px;resize:none;font:inherit;padding:12px;border:1px solid var(--line);border-radius:10px}#hotCompletionModal .hot-footer{position:sticky;bottom:-24px;background:white;padding:16px 0;display:flex;gap:10px}#hotCompletionModal .spinner{display:inline-block}#hotApprovalList{margin-top:16px}';document.head.append(style);
  const key=()=>`crm-hot-completion:${profile.tenant_id}:${profile.telegram_id}`;
  const notice=(id,text)=>{$(id).textContent=text||'';$(id).classList.toggle('show',!!text);};
  const canReview=()=>capabilities.modules?.hot_orders&&['owner','admin'].includes(profile?.role);
  async function api(url,data) {
    const response=await fetch(url,{credentials:'same-origin',cache:'no-store',...(data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{})});
    const body=await response.json();if(!response.ok||!body.success){const error=Error(body.error||'Sorğu alınmadı.');error.status=response.status;throw error;}return body;
  }
  function render() {
    $('hotApprovalList').innerHTML=rows.map(row=>`<article class="hot-card"><h2>${esc(row.client_name)}</h2><p>${esc(row.service_name)}</p><p class="hot-text">${esc(row.description)}</p><h3>Nəticə</h3><p class="hot-text">${esc(row.result_text)}</p><div class="hot-actions">${row.actions?.approve?`<button class="primary" data-review="approve" data-id="${esc(row.id)}">Təsdiq et</button>`:''}${row.actions?.reject?`<button class="outline" data-review="reject" data-id="${esc(row.id)}">Düzəlişə qaytar</button>`:''}</div></article>`).join('')||'<div class="empty">Təsdiq gözləyən sifariş yoxdur.</div>';
    $('hotApprovalMore').hidden=rows.length>=total;
  }
  async function load(append=false) {
    if(!canReview()||loading||busy)return;const version=generation;loading=true;$('hotApprovalRefresh').disabled=true;notice('hotApprovalNotice','');
    try {const body=await api(`/api/platform/hot-orders?approvals=1&limit=50&offset=${append?rows.length:0}`);if(version!==generation)return;
      rows=append?rows.concat(body.orders||[]):body.orders||[];total=body.total||0;render();
    }catch(error){if(version===generation)notice('hotApprovalNotice',error.message);}
    finally{if(version===generation){loading=false;$('hotApprovalRefresh').disabled=false;}}
  }
  function resize(){const field=$('hotCompletionText');field.style.height='auto';field.style.height=field.scrollHeight+'px';}
  function open(row,action){
    if(busy||!row?.actions?.[action])return;selected={row,action};payload=null;
    try{payload=JSON.parse(sessionStorage.getItem(key())||'null');}catch{}
    if(payload&&(payload.order_id!==row.id||payload.action!==action)){notice('hotApprovalNotice','Əvvəlki əməliyyatı tamamlayın.');return;}
    $('hotCompletionTitle').textContent={complete:'Sifarişi tamamla',approve:'Tamamlanmanı təsdiq et',reject:'Düzəlişə qaytar'}[action];
    $('hotCompletionResult').textContent=row.result_text||'';$('hotCompletionLabel').hidden=action==='approve';
    const field=$('hotCompletionText');field.value=payload?.result_text||payload?.reason||'';field.required=action!=='approve';field.disabled=!!payload;
    notice('hotCompletionNotice',payload?'Əvvəlki sorğunu yenidən yoxlayın.':'');modal.classList.add('show');resize();
  }
  function close(){if(!busy)modal.classList.remove('show');}
  $('hotCompletionForm').onsubmit=async event=>{
    event.preventDefault();if(busy||!selected||!profile)return;
    if(!payload){const text=$('hotCompletionText').value.trim();if(selected.action!=='approve'&&!text){notice('hotCompletionNotice','Nəticə və ya səbəb yazın.');return;}
      payload={action:selected.action,order_id:selected.row.id,request_id:crypto.randomUUID(),expected_updated_at:selected.row.updated_at,
        expected_tenant_id:profile.tenant_id,expected_user_id:profile.telegram_id,[selected.action==='complete'?'result_text':'reason']:text};}
    const savedKey=key(),version=generation,command=payload;
    try{sessionStorage.setItem(savedKey,JSON.stringify(command));}catch{notice('hotCompletionNotice','Brauzer yaddaşını aktiv edin.');return;}
    busy=true;$('hotCompletionSubmit').disabled=true;$('hotCompletionText').disabled=true;$('hotCompletionSubmit').innerHTML='<i class="spinner"></i>Gözləyin…';
    try{await api('/api/platform/hot-orders',command);sessionStorage.removeItem(savedKey);if(version!==generation)return;
      payload=null;busy=false;close();document.dispatchEvent(new CustomEvent('tenant-hot-order-changed'));await load();
    }catch(error){if(version!==generation)return;notice('hotCompletionNotice',error.message);if(error.status&&error.status<500){sessionStorage.removeItem(savedKey);payload=null;$('hotCompletionText').disabled=false;}}
    finally{if(version===generation){busy=false;$('hotCompletionSubmit').disabled=false;$('hotCompletionSubmit').textContent=payload?'Eyni sorğunu yoxla':'Təsdiq et';}}
  };
  function tab(hot){$('systemApprovalsHost').hidden=hot;$('hotApprovalsHost').hidden=!hot;if(hot)load();}
  $('systemApprovalTab').onclick=()=>tab(false);$('hotApprovalTab').onclick=()=>tab(true);
  $('hotApprovalRefresh').onclick=()=>load();$('hotApprovalMore').onclick=()=>load(true);
  $('hotApprovalList').onclick=event=>{const button=event.target.closest('[data-review]');if(button)open(rows.find(row=>row.id===button.dataset.id),button.dataset.review);};
  $('hotCompletionClose').onclick=close;$('hotCompletionCancel').onclick=close;$('hotCompletionText').oninput=resize;
  nav.addEventListener('click',()=>{if(!nav.hidden)tab(canReview());});
  document.addEventListener('tenant-hot-order-command',event=>open(event.detail.row,event.detail.action));
  function apply(data){const next=JSON.stringify([data.member,data.capabilities]),changed=next!==scope;
    if(next!==scope){scope=next;generation++;busy=loading=false;rows=[];total=0;payload=selected=null;modal.classList.remove('show');$('hotApprovalList').innerHTML='';$('hotCompletionResult').textContent='';$('hotCompletionText').value='';notice('hotApprovalNotice','');notice('hotCompletionNotice','');$('hotCompletionSubmit').disabled=false;$('hotApprovalRefresh').disabled=false;}
    profile=data.member;capabilities=data.capabilities||{};nav.hidden=!['owner','admin'].includes(profile.role)||!(capabilities.modules?.tasks||capabilities.modules?.hot_orders);nav.style.display=nav.hidden?'none':'';
    $('hotApprovalTab').hidden=!canReview();$('systemApprovalTab').hidden=!capabilities.modules?.tasks;
    if(changed&&capabilities.modules?.hot_orders){
      try{const saved=JSON.parse(sessionStorage.getItem(key())||'null');
        if(saved&&['complete','approve','reject'].includes(saved.action)&&String(saved.expected_tenant_id)===String(profile.tenant_id)&&String(saved.expected_user_id)===String(profile.telegram_id)&&
          (saved.action==='complete'||canReview()))open({id:saved.order_id,updated_at:saved.expected_updated_at,actions:{[saved.action]:true}},saved.action);
      }catch{}
    }
    if(new URLSearchParams(window.location?.search||'').get('view')==='approvals'&&!nav.hidden&&!nav.classList.contains('active'))nav.click();
  }
  document.addEventListener('tenant-profile',event=>apply(event.detail));
  const initial=generation;api('/api/platform/me').then(data=>{if(initial===generation)apply(data);}).catch(()=>{});
})();
