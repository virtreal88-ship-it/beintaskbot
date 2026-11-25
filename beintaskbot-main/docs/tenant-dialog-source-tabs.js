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
    .dialog-note-form{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:8px 10px;background:var(--card,#fff);border-inline-start:3px solid #b37827}
    .dialog-note-form textarea{box-sizing:border-box;flex:1 1 100%;min-width:0;resize:vertical}
    .dialog-note-form small{flex:1}.dialog-note-form [role=status]:empty{display:none}
  `;
  document.head.append(style);
  let session = '';
  let last;
  function reset() { session = ''; last = undefined; }
  for (const event of ['tenant-auth-invalid','tenant-auth-pending']) document.addEventListener(event,reset);
  document.addEventListener('tenant-profile', ({detail}) => {
    const user = detail?.member;
    const next = JSON.stringify([user?.tenant_id,user?.telegram_id,user?.role,user?.permissions,detail?.capabilities?.operator_widget]);
    if (next !== session) { reset(); session = next; }
  });

  window.tenantDialogSourceTabs = function({source,key,panel,canComment,save}) {
    const labels = copy[window.tenantI18n?.language] || copy.ru;
    if (!last || last.key !== key || last.source !== source) last = {key,source,active:source,text:''};
    const state = last;
    const bar = document.createElement('div'), form = document.createElement('form');
    bar.className = 'dialog-source-tabs'; bar.setAttribute('role','group'); bar.setAttribute('aria-label',labels[3]);
    form.className = 'dialog-note-form';
    const field = document.createElement('textarea'), hint = document.createElement('small');
    const submit = document.createElement('button'), status = document.createElement('span');
    field.maxLength = 2000; field.rows = 2; field.required = true; field.value = state.text;
    field.placeholder = labels[5]; field.setAttribute('aria-label',labels[5]); hint.textContent = labels[7];
    submit.type = 'submit'; submit.textContent = labels[6]; status.setAttribute('role','status');
    form.append(field,hint,submit,status);
    panel.before(bar); panel.after(form);
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
      const comment = state.active === 'comment';
      panel.classList.toggle('dialog-channel-muted',comment); form.hidden = !comment;
      buttons[2].disabled = !allowed;
      buttons.forEach((btn,i) => btn.setAttribute('aria-pressed',String(kinds[i] === state.active)));
      field.readOnly = !allowed || !!state.busy; submit.disabled = field.readOnly;
    }
    field.oninput = () => {state.text = field.value;};
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
