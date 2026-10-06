/* Task creation for the tenant cabinet, independent of legacy /webapp. */
(() => {
  const $ = id => document.getElementById(id);
  let profile, options, pending, busy = false;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const storageKey = () => `crm-task:${profile.tenant_id}:${profile.telegram_id}`;
  async function api(url, data) {
    const response = await fetch(url, {credentials:'same-origin', method:data ? 'POST' : 'GET',
      headers:{'Content-Type':'application/json'}, ...(data ? {body:JSON.stringify(data)} : {})});
    const body = await response.json();
    if (!response.ok || !body.success) {
      const error = new Error(body.error || 'Sorğu alınmadı.');
      error.pending = !!body.retry_same_request || response.status >= 500;
      throw error;
    }
    return body;
  }
  const button = document.createElement('button');
  button.className = 'primary'; button.textContent = '+ Tapşırıq'; button.hidden = true;
  $('tasksRefresh').before(button);
  const style = document.createElement('style');
  style.textContent = '#tenantTaskModal .modal-card{max-height:calc(100dvh - 32px);overflow:auto;padding-bottom:max(22px,env(safe-area-inset-bottom))}#tenantTaskModal label{display:grid;gap:6px;margin:12px 0}#tenantTaskModal input,#tenantTaskModal select,#tenantTaskModal textarea{width:100%;font:inherit;border:1px solid var(--line);border-radius:9px;padding:10px}#tenantTaskModal textarea{resize:none;min-height:100px}#tenantTaskModal .task-actions{display:flex;gap:10px;position:sticky;bottom:-22px;background:#fff;padding:14px 0}#tenantTaskModal .spinner{display:inline-block}#view-tasks .top{flex-wrap:wrap}';
  document.head.append(style);
  const modal = document.createElement('div'); modal.id = 'tenantTaskModal'; modal.className = 'modal';
  modal.innerHTML = `<form class="modal-card" id="tenantTaskForm"><div class="modal-head"><h2>Yeni tapşırıq</h2><button type="button" class="close" id="taskClose" aria-label="Bağla">×</button></div>
    <label>İcraçı<select id="taskExecutor" required></select></label>
    <label>Vərəq<select id="taskPipeline"></select></label>
    <label>Sövdələşmə<select id="taskLead"><option value="">Sövdələşməsiz</option></select></label>
    <p class="sub" id="taskRouteHint" style="white-space:normal"></p>
    <label>Tapşırıq<textarea id="taskBody" required maxlength="3500" placeholder="Nə etmək lazımdır?"></textarea></label>
    <label>Son tarix və saat<input id="taskDue" type="datetime-local" required></label>
    <div id="taskCreateNotice" class="notice"></div><div class="task-actions"><button class="primary" type="submit" id="taskSubmit">Yarat</button><button class="outline" type="button" id="taskCancel">Bağla</button></div></form>`;
  document.body.append(modal);
  function resize() { const t = $('taskBody'); t.style.height = 'auto'; t.style.height = `${t.scrollHeight}px`; }
  function configure() {
    const executor = options.executors.find(e => String(e.id) === $('taskExecutor').value);
    const pipes = executor?.pipelines || [];
    const selected = $('taskPipeline').value;
    $('taskPipeline').innerHTML = pipes.map(p => `<option value="${p.pipeline_id}">${esc(p.name || 'Vərəq #'+p.pipeline_id)}</option>`).join('');
    if (pipes.some(p => String(p.pipeline_id) === selected)) $('taskPipeline').value = selected;
    $('taskPipeline').closest('label').hidden = pipes.length === 0;
    $('taskRouteHint').textContent = pipes.length ? 'Sövdələşmə seçilməsə, əvvəl icraçının vərəqində yeni sövdələşmə yaradılacaq. Administrator təyin edərkən seçilmiş sövdələşmə icraçının ilk mərhələsinə köçürüləcək.' : 'Tapşırıq icraçının daxili nişanı ilə saxlanacaq; sövdələşmə köçürülməyəcək.';
  }
  function lock(on) {
    ['taskExecutor','taskPipeline','taskLead','taskBody','taskDue'].forEach(id => $(id).disabled = on);
    $('taskSubmit').disabled = busy;
  }
  async function open() {
    button.disabled = true;
    try {
      options = await api('/api/platform/crm/tasks/options');
      try { pending = JSON.parse(sessionStorage.getItem(storageKey()) || 'null'); } catch { pending = null; }
      $('tenantTaskForm').reset();
      $('taskExecutor').innerHTML = options.executors.map(e => `<option value="${e.id}">${esc(e.name)}</option>`).join('');
      $('taskExecutor').value = String(pending?.executor_id || profile.telegram_id);
      if (!$('taskExecutor').value && options.executors[0]) $('taskExecutor').value = String(options.executors[0].id);
      $('taskLead').innerHTML = '<option value="">Sövdələşməsiz</option>' + options.deals.map(d => `<option value="${d.id}">${esc(d.name)} · #${d.id}</option>`).join('');
      configure();
      if (pending) {
        $('taskPipeline').value = String(pending.pipeline_id || ''); $('taskLead').value = String(pending.lead_id || '');
        $('taskBody').value = pending.text;
        const due = new Date(pending.due_at); $('taskDue').value = new Date(due.getTime() - due.getTimezoneOffset()*60000).toISOString().slice(0,16);
      }
      lock(!!pending); $('taskSubmit').textContent = pending ? 'Eyni sorğunu yoxla' : 'Yarat';
      $('taskCreateNotice').classList.remove('show'); modal.classList.add('show'); resize();
    } catch (error) { alert(error.message); } finally { button.disabled = false; }
  }
  function close() { if (!busy) modal.classList.remove('show'); }
  button.onclick = open; $('taskClose').onclick = close; $('taskCancel').onclick = close;
  modal.onclick = event => { if (event.target === modal) close(); };
  document.addEventListener('keydown', event => { if (event.key === 'Escape') close(); });
  $('taskBody').oninput = resize; $('taskExecutor').onchange = configure;
  $('tenantTaskForm').onsubmit = async event => {
    event.preventDefault(); if (busy) return;
    if (!pending) {
      pending = {request_id:crypto.randomUUID(), executor_id:Number($('taskExecutor').value),
        pipeline_id:Number($('taskPipeline').value), lead_id:Number($('taskLead').value),
        text:$('taskBody').value.trim(), due_at:new Date($('taskDue').value).toISOString()};
    }
    // Keep the exact request on network loss or browser reload.
    sessionStorage.setItem(storageKey(), JSON.stringify(pending));
    busy = true; lock(true); $('taskSubmit').innerHTML = '<i class="spinner"></i>Yaradılır…';
    try {
      const result = await api('/api/platform/crm/tasks', pending);
      sessionStorage.removeItem(storageKey()); pending = null; busy = false; close();
      $('tasksRefresh').click(); if (result.warning) alert(result.warning);
    } catch (error) {
      $('taskCreateNotice').textContent = error.message; $('taskCreateNotice').classList.add('show');
      if (error.pending !== false) lock(true);
      else { pending = null; sessionStorage.removeItem(storageKey()); lock(false); }
    } finally { busy = false; $('taskSubmit').disabled = false; $('taskSubmit').textContent = pending ? 'Eyni sorğunu yoxla' : 'Yarat'; }
  };
  function apply(data) { profile = data.member; button.hidden = !data.capabilities?.modules?.tasks; }
  document.addEventListener('tenant-profile', event => apply(event.detail));
  api('/api/platform/me').then(apply).catch(() => {});
})();
