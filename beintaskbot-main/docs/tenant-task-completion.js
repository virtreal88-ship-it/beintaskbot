/* Tenant task completion; ambiguous writes keep their original request UUID. */
(() => {
  const $ = id => document.getElementById(id);
  const taskFlow = () => typeof window === 'undefined' ? null : window.tenantTaskFlow;
  let profile, pending, key, busy = false, profileReceived = false, generation = 0, profileKey = '', origin = null, returnFocus = null;
  const dictionary={ru:{title:'Завершить задачу',result:'Результат',hint:'Опишите результат. Согласование применяется по текущим правилам компании.',submit:'Завершить',check:'Проверить прежний запрос',close:'Закрыть',waiting:'Сохраняем…',error:'Завершение не подтверждено. Прежний запрос сохранён.',storage:'Не удалось сохранить запрос в браузере. Он не отправлен.',pending:'Завершение ожидает согласования. Текущий статус задачи показан в карточке.',done:'Завершение задачи подтверждено.'},az:{title:'Tapşırığı tamamla',result:'Nəticə',hint:'Nəticəni yazın. Təsdiq qaydaları şirkətinizin cari ayarlarına əsaslanır.',submit:'Tamamla',check:'Əvvəlki sorğunu yoxla',close:'Bağla',waiting:'Saxlanılır…',error:'Tamamlama təsdiqlənməyib. Əvvəlki sorğu saxlanılıb.',storage:'Sorğunu brauzerdə saxlamaq mümkün olmadı. Göndərilmədi.',pending:'Tamamlama təsdiq gözləyir. Tapşırığın cari vəziyyəti kartda göstərilir.',done:'Tapşırığın tamamlanması təsdiqləndi.'},en:{title:'Complete task',result:'Result',hint:'Describe the result. Current company approval rules apply.',submit:'Complete',check:'Check original request',close:'Close',waiting:'Saving…',error:'Completion is unconfirmed. The original request is retained.',storage:'Could not preserve the request in this browser. It was not sent.',pending:'Completion is awaiting approval. The card shows the current task status.',done:'Task completion confirmed.'}};
  const copy=()=>dictionary[typeof window!=='undefined'?window.tenantI18n?.language:'az']||dictionary.az;
  const modal = document.createElement('div'); modal.id = 'tenantCompleteModal'; modal.className = 'modal';
  modal.innerHTML = '<form class="modal-card" id="completeForm" style="max-height:calc(100dvh - 32px);overflow:auto"><div class="modal-head"><h2>Tapşırığı tamamla</h2><button class="close" type="button" id="completeClose" aria-label="Bağla">×</button></div><p class="sub" id="completeHint"></p><label style="display:grid;gap:8px">Nəticə<textarea id="completeText" required maxlength="3500" style="width:100%;min-height:110px;resize:none;font:inherit;padding:12px;border:1px solid var(--line);border-radius:10px"></textarea></label><div class="notice" id="completeNotice"></div><div style="position:sticky;bottom:0;background:white;display:flex;gap:12px;padding:16px 0 max(16px,env(safe-area-inset-bottom))"><button type="submit" class="primary" id="completeSubmit">Tamamla</button><button type="button" class="outline" id="completeCancel">Bağla</button></div></form>';
  document.body.append(modal);
  const identity = () => profile ? `${profile.tenant_id}:${profile.telegram_id}` : '';
  const resize = () => {const t=$('completeText');t.style.height='auto';t.style.height=`${t.scrollHeight}px`;};
  const close = () => {if(!busy){modal.classList.remove('show');const previous=origin;origin=null;if(previous)void taskFlow()?.restoreAfterCompletion(previous);else returnFocus?.isConnected&&returnFocus.focus?.();}};
  function translate(){if(!taskFlow())return;const c=copy(),canComplete=taskFlow().sourceActions('kommo').complete===true;modal.querySelector('h2').textContent=c.title;modal.querySelector('label').firstChild.textContent=c.result;$('completeHint').textContent=c.hint;$('completeClose').setAttribute('aria-label',c.close);$('completeCancel').textContent=c.close;$('completeSubmit').textContent=busy?c.waiting:pending?c.check:c.submit;$('completeSubmit').disabled=busy||!canComplete;$('completeText').disabled=busy||!!pending||!canComplete;}
  if(taskFlow()){const style=document.createElement('style');style.textContent='#tenantCompleteModal .modal-card{width:calc(100% - 24px);max-width:680px;box-sizing:border-box}#tenantCompleteModal button{min-height:44px}#tenantCompleteModal textarea{box-sizing:border-box;max-width:100%}#tenantCompleteModal form>div:last-child{flex-wrap:wrap}#tenantCompleteModal :focus-visible{outline:3px solid #087c5c;outline-offset:3px}';document.head.append(style);}
  $('completeClose').onclick = close; $('completeCancel').onclick = close;
  modal.onclick = event => {if(event.target === modal) close();};
  document.addEventListener('keydown', event => {if(event.key === 'Escape') close();});
  $('completeText').oninput = resize;
  document.addEventListener('click', event => {
    const button = event.target.closest?.('[data-task-complete]');
    if(!button || busy || !profile || taskFlow() && taskFlow().sourceActions('kommo').complete!==true) return;
    const taskId = Number(button.dataset.taskComplete);
    if(!Number.isSafeInteger(taskId) || taskId <= 0) return;
    returnFocus=document.activeElement;origin=taskFlow()?.suspendForCompletion(String(taskId));
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
    modal.classList.add('show'); resize();translate();(pending?$('completeSubmit'):$('completeText')).focus?.();
  });
  $('completeForm').onsubmit = async event => {
    event.preventDefault(); if(busy || !profile || taskFlow()&&taskFlow().sourceActions('kommo').complete!==true) return;
    const originalIdentity = identity(), originalKey = key, originalGeneration = generation, flowScope = taskFlow()?.capture();
    const current = () => identity()===originalIdentity && generation===originalGeneration && (!taskFlow() || taskFlow().current(flowScope));
    const resultText = $('completeText').value.trim();
    if(!pending && !resultText) return;
    pending ||= {request_id:crypto.randomUUID(),task_id:Number($('completeForm').dataset.taskId),result_text:resultText};
    try {sessionStorage.setItem(originalKey,JSON.stringify(pending));}
    catch { $('completeNotice').textContent=taskFlow()?copy().storage:'Brauzerdə sorğunu saxlamaq alınmadı. Əməliyyat göndərilmədi.';$('completeNotice').classList.add('show');return; }
    busy=true;$('completeText').disabled=true;$('completeSubmit').disabled=true;
    $('completeSubmit').innerHTML='<i class="spinner" style="display:inline-block"></i>Gözləyin…';
    try {
      const query = taskFlow() ? '?'+new URLSearchParams({flow:'1',source:'kommo',expected_tenant_id:flowScope.expected_tenant_id,expected_user_id:flowScope.expected_user_id}) : '';
      const response=await fetch('/api/platform/crm/tasks/complete'+query,{method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json'},body:JSON.stringify(pending)});
      const result=await response.json();
      if(!current())return;
      if(!response.ok || !result || result.success!==true) {const error=Error(result?.error||'Tamamlama alınmadı.');error.pending=response.ok||!!result?.retry_same_request||response.status>=500;error.status=response.status;throw error;}
      if(taskFlow()&&!(result.approval_pending===true&&result.request_id===pending.request_id||result.completed===true&&String(result.task_id)===String(pending.task_id))){const error=Error(copy().error);error.pending=true;throw error;}
      sessionStorage.removeItem(originalKey);
      pending=null;busy=false;close();$('tasksRefresh').click();
      if(taskFlow())taskFlow().operationMessage(copy()[result.approval_pending?'pending':'done']);else if(result.warning)alert(result.warning);
    } catch(error) {
      if(!current()) return;
      $('completeNotice').textContent=taskFlow()?copy().error:error.message;$('completeNotice').classList.add('show');
      if(error.pending===false) {pending=null;sessionStorage.removeItem(originalKey);$('completeText').disabled=false;}
      if(error.status===403||error.status===409)void taskFlow()?.proveAccess();
    } finally {
      if(current()) {busy=false;$('completeSubmit').disabled=false;$('completeSubmit').textContent=pending?'Eyni sorğunu yoxla':'Tamamla';translate();}
    }
  };
  function apply(data) {
    const previous=identity(),nextKey=JSON.stringify([data.member,data.capabilities]);profile=data.capabilities?.modules?.tasks?data.member:null;
    if(nextKey!==profileKey){generation++;profileKey=nextKey;busy=false;}
    if(identity()!==previous) {modal.classList.remove('show');pending=null;key=null;origin=null;$('completeText').value='';}
    modal.style.visibility='';
  }
  document.addEventListener('tenant-profile',event=>{profileReceived=true;apply(event.detail);});
  document.addEventListener('tenant-language',translate);
  document.addEventListener('tenant-auth-pending',()=>{generation++;busy=false;modal.style.visibility='hidden';});
  document.addEventListener('tenant-auth-invalid',()=>{generation++;busy=false;profile=null;pending=null;key=null;modal.classList.remove('show');$('completeText').value='';});
  if(typeof window!=='undefined')window.tenantTaskCompletion={render:translate,leave:()=>{generation++;busy=false;origin=null;modal.classList.remove('show');}};
  fetch('/api/platform/me',{credentials:'same-origin'}).then(r=>r.json()).then(data=>{if(data.success&&!profileReceived)apply(data);}).catch(()=>{});
})();
