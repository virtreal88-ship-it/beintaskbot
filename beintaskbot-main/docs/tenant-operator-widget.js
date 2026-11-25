/* The private website channel is a live view of Operator's own message history. */
(() => {
  const root = document.getElementById('operatorWidget');
  if (!root) return;
  const labels = {
    ru: ['Чат сайта BeinSystems', 'К списку', 'Диалогов пока нет', 'Сообщений пока нет', 'Предыдущие сообщения',
      'Ответ клиенту', 'Отправить', 'Открыть в Operator', 'Вложение', 'Клиент', 'Оператор', 'Система',
      'Загрузка…', 'Ответ сохранён в Operator.', 'Operator отклонил ответ.',
      'Результат отправки пока неизвестен. Проверьте историю в Operator перед повтором.', 'Обновить', 'Только просмотр', 'Ответить в Operator', 'непрочитанных'],
    az: ['BeinSystems sayt çatı', 'Siyahıya', 'Hələ dialoq yoxdur', 'Hələ mesaj yoxdur', 'Əvvəlki mesajlar',
      'Müştəriyə cavab', 'Göndər', 'Operator-da aç', 'Qoşma', 'Müştəri', 'Operator', 'Sistem',
      'Yüklənir…', 'Cavab Operator-da saxlanıldı.', 'Operator cavabı qəbul etmədi.',
      'Göndəriş təsdiqlənməyib. Təkrarlamadan əvvəl Operator tarixçəsini yoxlayın.', 'Yenilə', 'Yalnız baxış', 'Operator-da cavab ver', 'oxunmamış'],
    en: ['BeinSystems website chat', 'Back to list', 'No conversations yet', 'No messages yet', 'Earlier messages',
      'Reply to customer', 'Send', 'Open in Operator', 'Attachment', 'Customer', 'Operator', 'System',
      'Loading…', 'Reply saved in Operator.', 'Operator rejected the reply.',
      'Delivery is uncertain. Check the conversation in Operator before resending.', 'Refresh', 'Read only', 'Reply in Operator', 'unread']
  };
  const t = i => (labels[window.tenantI18n?.language] || labels.ru)[i];
  const element = (tag, text) => {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    return node;
  };
  const button = (label, click) => {
    const node = element('button', label);
    node.type = 'button';
    node.onclick = click;
    return node;
  };
  let member, scope = '', epoch = 0, version = 0, busy = false, sending = false, timer;
  let details, heading, list, panel, status, transcript, composer, draft, send, older, original;
  let selected, more = false, messages = new Map(), request;
  const relevant = () => member && !document.hidden && document.getElementById('view-dialogs')?.classList.contains('active');
  function reset() {
    ++epoch; ++version; clearTimeout(timer); member = null; scope = ''; selected = null;
    messages.clear(); busy = false; sending = false; request = null; details = null;
    root.replaceChildren(); root.hidden = true;
  }
  function clearChat() {
    ++version; busy = false; selected = null; messages.clear(); request = null; sending = false;
    panel.replaceChildren(); panel.hidden = true; list.hidden = false; list.replaceChildren(); status.textContent = '';
  }
  async function api(fields) {
    const response = await fetch('/api/platform/operator/widget', {method:'POST', credentials:'same-origin',
      cache:'no-store', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
        ...fields, expected_tenant_id:member.tenant_id, expected_user_id:member.telegram_id
      })});
    const result = await response.json();
    if (!response.ok || result.success !== true) {
      const error = Error(result.error || String(response.status));
      error.status = response.status;
      throw error;
    }
    return result;
  }
  function schedule() {
    clearTimeout(timer);
    if (relevant()) timer = setTimeout(refresh, details?.open ? 20000 : 60000);
  }
  function paintMessages() {
    const bottom = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 48;
    const height = transcript.scrollHeight, top = transcript.scrollTop;
    transcript.replaceChildren();
    const sorted = [...messages.values()].sort((a,b) => a.id.length-b.id.length || a.id.localeCompare(b.id));
    for (const m of sorted) {
      const line = element('article');
      line.className = 'operator-message';
      const time = new Date(m.created_at);
      const who = m.name || t({client:9, operator:10, system:11}[m.sender]);
      line.append(element('small', [who, Number.isNaN(time.valueOf()) ? '' : time.toLocaleString()].filter(Boolean).join(' · ')));
      line.append(element('p', m.text));
      if (m.attachment) line.append(element('small', t(8)));
      transcript.append(line);
    }
    if (!sorted.length) transcript.append(element('p',t(3)));
    return {bottom, height, top};
  }
  function renderList(chats) {
    const unread = chats.reduce((sum, chat) => sum + (chat.unread || 0), 0);
    const unreadLabel = t(19);
    heading.textContent = t(0) + (unread ? ' · ' + unread + ' ' + unreadLabel : '');
    if (!details.open) return;
    list.replaceChildren();
    const sorted = [...chats].sort((a,b) => Number(b.status === 'open') - Number(a.status === 'open') ||
      (b.updated_at || '').localeCompare(a.updated_at || ''));
    for (const chat of sorted) {
      list.append(button([chat.name, chat.operator, chat.unread ? chat.unread + ' ' + unreadLabel : '']
        .filter(Boolean).join(' · '), () => openChat(chat)));
    }
    if (!chats.length) list.append(element('p', t(2)));
  }
  async function refresh(olderPage = false) {
    clearTimeout(timer);
    if (!relevant() || busy) return;
    const current = version, session = epoch, chat = selected;
    busy = true;
    try {
      if (chat && details.open) {
        const fields = {action:'history', chat_id:chat.id, revision:chat.revision};
        if (olderPage && messages.size) {
          fields.action = 'older';
          fields.before_id = [...messages.keys()].sort((a,b)=>a.length-b.length || a.localeCompare(b))[0];
        }
        const result = await api(fields);
        if (current !== version || session !== epoch) return;
        selected = result.chat;
        const oldSize = messages.size;
        for (const m of result.messages) messages.set(m.id, m);
        if (olderPage || oldSize === 0) more = result.has_older;
        older.hidden = !more;
        const scroll = paintMessages();
        if (olderPage) transcript.scrollTop = scroll.top + transcript.scrollHeight - scroll.height;
        else if (scroll.bottom || !oldSize) transcript.scrollTop = transcript.scrollHeight;
        composer.hidden = !selected.can_reply;
        original.textContent = selected.reply_in_operator ? t(18) : selected.can_reply ? t(7) : t(17) + ' · ' + t(7);
      } else {
        const result = await api({action:'list'});
        if (current !== version || session !== epoch) return;
        renderList(result.chats);
      }
      if (status.dataset.feedback !== 'send') status.textContent = '';
    } catch (err) {
      if (current !== version || session !== epoch) return;
      if ([401,403,404,409].includes(err.status)) {
        clearChat();
        heading.textContent = t(0);
      }
      status.textContent = err.message;
    } finally {
      if (session === epoch && current === version) busy = false;
      if (session === epoch) schedule();
    }
  }
  async function submit() {
    const text = draft.value.trim();
    if (!selected?.can_reply || !text || sending) return;
    const current = version, session = epoch;
    const route = selected;
    const storageKey = ['operator-widget',member.tenant_id,member.telegram_id,route.revision].join('.');
    sending = true; send.disabled = true; draft.disabled = true;
    try {
      const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
      const hash = Array.from(new Uint8Array(bytes), v => v.toString(16).padStart(2,'0')).join('');
      if (current !== version || session !== epoch) return;
      let saved;
      try {saved = JSON.parse(sessionStorage.getItem(storageKey));} catch {}
      if (request?.hash !== hash) request = saved?.hash === hash ? saved : {hash, request_id:crypto.randomUUID()};
      try {sessionStorage.setItem(storageKey,JSON.stringify(request));} catch {}
      const result = await api({action:'reply', chat_id:route.id, revision:route.revision, text, request_id:request.request_id});
      if (result.request_id && result.state === 'unknown') {
        try {sessionStorage.setItem(storageKey,JSON.stringify({hash,request_id:result.request_id}));} catch {}
      }
      if (['saved','rejected'].includes(result.state)) {
        try {sessionStorage.removeItem(storageKey);} catch {}
      }
      if (current !== version || session !== epoch) return;
      if (result.request_id && result.state === 'unknown') request = {hash, request_id:result.request_id};
      if (result.state === 'saved') {draft.value = ''; request = null;}
      if (result.state === 'rejected') request = null;
      status.dataset.feedback = 'send';
      status.textContent = t(result.state === 'saved' ? 13 : result.state === 'rejected' ? 14 : 15);
      void refresh();
    } catch (err) {
      if (current !== version || session !== epoch) return;
      if ([401,403,404].includes(err.status)) clearChat();
      status.dataset.feedback = 'send'; status.textContent = err.status ? err.message : t(15);
    } finally {
      if (current === version && session === epoch) {sending = false; send.disabled = false; draft.disabled = false;}
    }
  }
  function openChat(chat) {
    clearChat(); selected = chat; list.hidden = true; panel.hidden = false;
    status.dataset.feedback = ''; status.textContent = t(12);
    panel.append(button(t(1), () => {clearChat(); void refresh();}), element('strong',chat.name));
    original = element('a', t(7)); original.href = chat.operator_url; original.target = '_blank';
    original.rel = 'noopener noreferrer'; panel.append(original);
    older = button(t(4), () => refresh(true)); older.hidden = true;
    transcript = element('div'); transcript.className = 'widget-history';
    composer = element('form'); composer.hidden = true;
    draft = element('textarea'); draft.maxLength = 4000; draft.setAttribute('aria-label',t(5)); draft.placeholder = t(5);
    send = element('button',t(6)); send.type = 'submit';
    composer.append(draft,send); composer.onsubmit = event => {event.preventDefault(); void submit();};
    panel.append(older, transcript, composer); void refresh();
  }
  function mount(profile) {
    member = profile; root.hidden = false;
    const style = element('style');
    style.textContent = '#operatorWidget {margin-bottom:8px} #operatorWidget summary{cursor:pointer;padding:8px;font-weight:600} #operatorWidget .widget-list,#operatorWidget .widget-panel{display:grid;gap:8px;padding:8px} #operatorWidget [hidden]{display:none!important} #operatorWidget .widget-list{max-height:60dvh;overflow:auto} #operatorWidget .widget-history{max-height:48dvh;overflow:auto} #operatorWidget article p{white-space:pre-wrap;overflow-wrap:anywhere;margin:4px 0} #operatorWidget form{display:flex;gap:8px;align-items:center} #operatorWidget textarea{min-width:0;flex:1} #operatorWidget:has(details[open]) + #operatorDialogs{display:none}';
    details = element('details'); details.className = 'card'; heading = element('summary',t(0));
    list = element('div'); list.className = 'widget-list'; panel = element('div'); panel.className = 'widget-panel'; panel.hidden = true;
    status = element('p'); status.setAttribute('role','status');
    details.append(heading,list,panel,status,button(t(16),()=>refresh())); root.append(style,details);
    details.ontoggle = () => {clearChat(); void refresh();};
    if (relevant()) void refresh();
  }
  document.addEventListener('tenant-profile', event => {
    const data = event.detail, next = data?.member;
    if (!next || !data.capabilities?.operator_widget) {reset(); return;}
    const key = JSON.stringify([next.tenant_id,next.telegram_id,next.role,next.permissions,data.capabilities]);
    if (key === scope) return;
    reset(); scope = key; mount(next);
  });
  for (const event of ['tenant-auth-invalid','tenant-auth-pending']) document.addEventListener(event, reset);
  document.addEventListener('tenant-view', event => {
    if (event.detail.view === 'dialogs' && member) void refresh();
    else if (details) {details.open = false; clearChat(); clearTimeout(timer);}
  });
  document.addEventListener('visibilitychange', () => {if (relevant()) void refresh(); else clearTimeout(timer);});
  document.addEventListener('tenant-language', () => {if (member) {const user = member, key = scope; reset(); scope = key; mount(user);}});
})();
