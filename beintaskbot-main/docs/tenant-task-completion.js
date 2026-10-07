/* Tenant task completion; ambiguous writes keep their original request UUID. */
(() => {
  const $ = id => document.getElementById(id);
  let profile, pending, key, busy = false, profileReceived = false;
  const modal = document.createElement('div'); modal.id = 'tenantCompleteModal'; modal.className = 'modal';
  modal.innerHTML = '<form class="modal-card" id="completeForm" style="max-height:calc(100dvh - 32px);overflow:auto"><div class="modal-head"><h2>Tapşırığı tamamla</h2><button class="close" type="button" id="completeClose" aria-label="Bağla">×</button></div><p class="sub" id="completeHint"></p><label style="display:grid;gap:8px">Nəticə<textarea id="completeText" required maxlength="3500" style="width:100%;min-height:110px;resize:none;font:inherit;padding:12px;border:1px solid var(--line);border-radius:10px"></textarea></label><div class="notice" id="completeNotice"></div><div style="position:sticky;bottom:0;background:white;display:flex;gap:12px;padding:16px 0 max(16px,env(safe-area-inset-bottom))"><button type="submit" class="primary" id="completeSubmit">Tamamla</button><button type="button" class="outline" id="completeCancel">Bağla</button></div></form>';
  document.body.append(modal);
  const identity = () => profile ? `${profile.tenant_id}:${profile.telegram_id}` : '';
  const resize = () => {const t=$('completeText');t.style.height='auto';t.style.height=`${t.scrollHeight}px`;};
  const close = () => {if(!busy) modal.classList.remove('show');};
  $('completeClose').onclick = close; $('completeCancel').onclick = close;
  modal.onclick = event => {if(event.target === modal) close();};
  document.addEventListener('keydown', event => {if(event.key === 'Escape') close();});
  $('completeText').oninput = resize;
  document.addEventListener('click', event => {
    const button = event.target.closest?.('[data-task-complete]');
    if(!button || busy || !profile) return;
    const taskId = Number(button.dataset.taskComplete);
    if(!Number.isSafeInteger(taskId) || taskId <= 0) return;
    key = `crm-task-complete:${identity()}:${taskId}`;
    try {pending=JSON.parse(sessionStorage.getItem(key)||'null');} catch {pending=null;}
    if(pending && pending.task_id !== taskId) pending=null;
    $('completeForm').dataset.taskId = String(taskId);
    $('completeText').value = pending?.result_text || '';
    $('completeText').disabled = !!pending;
    $('completeSubmit').disabled = false;
    $('completeSubmit').textContent = pending ? 'Eyni sorğunu yoxla' : 'Tamamla';
    $('completeNotice').classList.remove('show');
    $('completeHint').textContent = 'Tapşırıq #' + taskId + '. Nəticəni yazın. Təsdiq qaydaları şirkətinizin ayarlarına əsaslanır.';
    modal.classList.add('show'); resize();
  });
  $('completeForm').onsubmit = async event => {
    event.preventDefault(); if(busy || !profile) return;
    const originalIdentity = identity(), originalKey = key;
    const resultText = $('completeText').value.trim();
    if(!pending && !resultText) return;
    pending ||= {request_id:crypto.randomUUID(),task_id:Number($('completeForm').dataset.taskId),result_text:resultText};
    try {sessionStorage.setItem(originalKey,JSON.stringify(pending));}
    catch { $('completeNotice').textContent='Brauzerdə sorğunu saxlamaq alınmadı. Əməliyyat göndərilmədi.';$('completeNotice').classList.add('show');return; }
    busy=true;$('completeText').disabled=true;$('completeSubmit').disabled=true;
    $('completeSubmit').innerHTML='<i class="spinner" style="display:inline-block"></i>Gözləyin…';
    try {
      const response=await fetch('/api/platform/crm/tasks/complete',{method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json'},body:JSON.stringify(pending)});
      const result=await response.json();
      if(!response.ok || !result.success) {const error=Error(result.error||'Tamamlama alınmadı.');error.pending=!!result.retry_same_request||response.status>=500;throw error;}
      sessionStorage.removeItem(originalKey);
      if(identity()!==originalIdentity) return;
      pending=null;modal.classList.remove('show');$('tasksRefresh').click();
      if(result.warning) alert(result.warning);
    } catch(error) {
      if(identity()!==originalIdentity) return;
      $('completeNotice').textContent=error.message;$('completeNotice').classList.add('show');
      if(error.pending===false) {pending=null;sessionStorage.removeItem(originalKey);$('completeText').disabled=false;}
    } finally {
      busy=false;
      if(identity()===originalIdentity) {$('completeSubmit').disabled=false;$('completeSubmit').textContent=pending?'Eyni sorğunu yoxla':'Tamamla';}
    }
  };
  function apply(data) {
    const previous=identity();profile=data.capabilities?.modules?.tasks?data.member:null;
    if(identity()!==previous) {modal.classList.remove('show');pending=null;key=null;}
  }
  document.addEventListener('tenant-profile',event=>{profileReceived=true;apply(event.detail);});
  fetch('/api/platform/me',{credentials:'same-origin'}).then(r=>r.json()).then(data=>{if(data.success&&!profileReceived)apply(data);}).catch(()=>{});
})();
