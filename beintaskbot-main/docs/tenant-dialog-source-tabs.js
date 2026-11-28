/* A note has its own editor and never enters a customer reply payload. */
(() => {
  const copy = {
    ru: ['Чат','WhatsApp','Комментарий','Канал ответа','Нет подтверждённого диалога в этом канале','Внутренний комментарий','Сохранить комментарий','Доступен только сотрудникам'],
    az: ['Çat','WhatsApp','Şərh','Cavab kanalı','Bu kanalda təsdiqlənmiş dialoq yoxdur','Daxili şərh','Şərhi saxla','Yalnız əməkdaşlara görünür'],
    en: ['Chat','WhatsApp','Comment','Reply channel','No confirmed conversation on this channel','Internal comment','Save comment','Visible only to staff']
  };
  const style = document.createElement('style');
  style.textContent = `
    .dialog-source-tabs{display:flex;align-items:stretch;gap:clamp(8px,2vw,20px);padding:0 10px;flex:none;background:var(--card,#fff);border-top:1px solid var(--border,#ddd)}
    .dialog-source-tabs button{appearance:none;border:0;border-bottom:2px solid transparent;border-radius:0;background:transparent;color:inherit;font:inherit;font-size:14px;padding:6px 2px;min-width:0}
    .dialog-source-tabs button[aria-pressed=true]{color:var(--accent,#2676bb);border-bottom-color:currentColor;font-weight:600}
    .dialog-source-tabs button:disabled{opacity:.52;cursor:default}
    .dialog-channel-muted,.dialog-note-form[hidden]{display:none!important}
    .dialog-note-form{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:6px 10px max(6px,env(safe-area-inset-bottom));background:var(--card,#fff)}
    .dialog-note-lifecycle-actions{display:flex;align-items:center;justify-content:flex-end;flex:1 1 100%}
    .dialog-note-lifecycle-actions:not(:has(button)){display:none}
    .dialog-note-composer{box-sizing:border-box;display:flex;align-items:flex-end;gap:4px;width:100%;min-width:0;padding:4px 6px;border:1px solid var(--line,#888);border-radius:13px;background:var(--card,#fff)}
    .dialog-note-composer:focus-within{border-color:var(--brand,#2676bb)}
    .dialog-note-composer textarea.dialog-note-input,.dialog-note-composer textarea.dialog-note-input:focus,.dialog-note-composer textarea.dialog-note-input:focus-visible{box-sizing:border-box;min-width:0;width:100%;flex:1;min-height:44px;max-height:120px;margin:0;padding:8px 7px;border:0!important;outline:0!important;box-shadow:none!important;background:transparent!important;color:var(--ink);font:inherit;resize:none}
    .dialog-note-composer button.dialog-note-send{display:grid;place-items:center;flex:none;width:44px;height:44px;min-width:44px;min-height:44px;margin:0;padding:0;border:0;border-radius:50%;background:var(--ink,#132621);color:var(--card,#fff)}
    .dialog-note-send svg{width:22px;height:22px;pointer-events:none}
    .dialog-note-composer button.dialog-note-send:focus-visible{outline:2px solid var(--brand,#2676bb);outline-offset:2px}
    .dialog-note-send:disabled{opacity:.5}
    @media(max-width:900px){.dialog-note-composer textarea.dialog-note-input{font-size:16px}}
    .dialog-note-form small{flex:1}.dialog-note-form [role=status]:empty{display:none}
  `;
  document.head.append(style);
  // Shared appearance for the comment tab and its menu dialog. Each keeps its own submit handler.
  window.tenantStyleInternalCommentComposer = (composer, field, submit, label) => {
    composer.classList.add('dialog-note-composer');
    field.classList.add('dialog-note-input');
    submit.className = 'primary dialog-note-send';
    submit.title = label;
    submit.setAttribute('aria-label',label);
    submit.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="currentColor"><path d="m3.6 3.8 17.6 7.3a1 1 0 0 1 0 1.8L3.6 20.2a.9.9 0 0 1-1.2-1l1.3-5.4 9-1.8-9-1.8-1.3-5.4a.9.9 0 0 1 1.2-1Z"/></svg>';
  };
  let session = '';
  let last;
  function reset() { session = ''; last = undefined; }
  for (const event of ['tenant-auth-invalid','tenant-auth-pending']) document.addEventListener(event,reset);
  document.addEventListener('tenant-profile', ({detail}) => {
    const user = detail?.member;
    const next = JSON.stringify([user?.tenant_id,user?.telegram_id,user?.role,user?.permissions,detail?.capabilities?.operator_widget]);
    if (next !== session) { reset(); session = next; }
  });

  window.tenantDialogSourceTabs = function({source,key,panel,lifecycleActions,canComment,save}) {
    const labels = copy[window.tenantI18n?.language] || copy.ru;
    if (!last || last.key !== key || last.source !== source) last = {key,source,active:source,text:''};
    const state = last;
    const bar = document.createElement('div'), form = document.createElement('form');
    bar.className = 'dialog-source-tabs'; bar.setAttribute('role','group'); bar.setAttribute('aria-label',labels[3]);
    form.className = 'dialog-note-form';
    const editor = document.createElement('div'), field = document.createElement('textarea'), hint = document.createElement('small');
    const submit = document.createElement('button'), status = document.createElement('span');
    field.maxLength = 2000; field.rows = 2; field.required = true; field.value = state.text;
    field.placeholder = labels[5]; field.setAttribute('aria-label',labels[5]); hint.textContent = labels[7];
    submit.type = 'submit'; status.setAttribute('role','status');
    window.tenantStyleInternalCommentComposer(editor,field,submit,labels[6]);
    editor.append(field,submit); form.append(editor,hint,status);
    panel.before(bar); panel.after(form);
    const actionsHome=lifecycleActions?.parentNode, actionsNext=lifecycleActions?.nextSibling;
    const noteActions=actionsHome?document.createElement('div'):null;
    if(noteActions){noteActions.className='dialog-note-lifecycle-actions';form.append(noteActions);}
    let allowed = canComment;
    const kinds = ['widget','whatsapp','comment'];
    const buttons = kinds.map((kind,i) => {
      const btn = document.createElement('button'); btn.type = 'button'; btn.textContent = labels[i];
      if (kind !== source && kind !== 'comment') {btn.disabled = true; btn.title = labels[4];}
      btn.onclick = () => {
        if (btn.disabled) return;
        state.active = kind; paint();
        if (kind === 'comment') field.focus();
      };
      bar.append(btn);
      return btn;
    });
    function paint() {
      if(!allowed&&state.active==='comment')state.active=source;
      const comment = state.active === 'comment';
      panel.classList.toggle('dialog-channel-muted',comment); form.hidden = !comment;
      if(noteActions){
        const destination=comment?noteActions:actionsHome;
        if(lifecycleActions.parentNode!==destination)destination.insertBefore(lifecycleActions,comment?null:actionsNext);
      }
      buttons[2].disabled = !allowed;
      buttons.forEach((btn,i) => btn.setAttribute('aria-pressed',String(kinds[i] === state.active)));
      field.readOnly = !allowed || !!state.busy; submit.disabled = field.readOnly || !field.value.trim();
    }
    field.oninput = () => {state.text = field.value; paint();};
    state.sync = () => {field.value = state.text; paint();};
    form.onsubmit = async event => {
      event.preventDefault();
      const note = field.value.trim();
      if (!allowed || state.active !== 'comment' || state.busy || !note || !form.isConnected) return;
      if (state.attempt?.note !== note) state.attempt = {note,request_id:crypto.randomUUID()};
      state.busy = true; status.textContent = ''; paint();
      try {
        await save(state.attempt);
        state.text = ''; state.attempt = null;
      } catch (error) {
        if (form.isConnected) status.textContent = error.message;
      } finally {
        state.busy = false; state.sync();
      }
    };
    paint();
    return {setCommentAllowed(value) {allowed = value; paint();}};
  };
})();
