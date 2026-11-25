/* Composer for a new WhatsApp number and editor for provider-confirmed messages. */
(() => {
  const base = '/api/platform/crm/chat/';
  const copy = {
    ru: {
      write: 'Написать на номер', source: 'WhatsApp-источник', choose: 'Выберите источник',
      number: 'Номер с кодом страны', message: 'Сообщение', send: 'Отправить',
      close: 'Закрыть', check: 'Проверить статус', repeat: 'Повторить запрос',
      pendingSends: 'Незавершённые отправки', otherNumber: 'Другой номер',
      working: 'Проверяем…', first_sent: 'WhatsApp подтвердил отправку.',
      first_existing: 'Этот диалог уже есть. Текст сохранён как черновик.',
      first_kept: 'У диалога уже есть черновик. Новый текст сохранён в окне «Написать на номер».',
      first_blocked: 'Отправка не состоялась. Текст сохранён как черновик.',
      first_phone_invalid: 'Введите международный номер, начиная с + и кода страны.',
      first_text_invalid: 'Введите сообщение длиной до 8 000 символов.',
      first_source_unavailable: 'Этот WhatsApp-источник сейчас недоступен.',
      first_source_changed: 'Источник изменился. Откройте диалог и проверьте подключение.',
      first_recipient_self: 'Вы указали номер выбранного источника. Введите номер клиента.',
      first_recipient_not_registered: 'Этот номер не зарегистрирован в WhatsApp.',
      first_recipient_unverified: 'Не удалось подтвердить номер в WhatsApp. Проверьте номер и повторите.',
      first_identity_conflict: 'У этого номера несколько неподтверждённых адресов в WhatsApp. Проверьте диалог.',
      first_admission_paused: 'Приём новых диалогов приостановлен.',
      first_operator_offline: 'Чтобы начать переписку, установите статус «В сети».',
      first_request_conflict: 'Номер, источник или текст этого запроса изменился.',
      edit: 'Редактировать', editTitle: 'Редактировать сообщение', save: 'Сохранить правку',
      edited: 'Изменено', latest: 'Загрузить текущий текст', current: 'Текущий текст',
      edit_expired: 'Срок редактирования истёк. Текст оригинала сохранён.',
      edit_version_conflict: 'Текст уже изменился. Загрузите текущую версию перед сохранением.',
      edit_superseded: 'Правка подтверждена. В переписке показана более свежая версия.',
      edit_message_unavailable: 'Исходное сообщение сейчас недоступно для редактирования.',
      edit_source_unavailable: 'Этот источник сейчас не поддерживает редактирование.',
      edit_provider_rejected: 'WhatsApp отклонил правку. Текст оригинала сохранён.',
      edit_pending: 'Есть неподтверждённая правка этого сообщения.',
      edit_no_change: 'Измените текст перед сохранением.',
      edit_text_invalid: 'Введите текст длиной до 8 000 символов.',
      edit_request_conflict: 'У этого запроса уже есть другая версия текста.',
      delete: 'Удалить у всех', deleteTitle: 'Удалить сообщение у всех?', deleteConfirm: 'WhatsApp удалит это сообщение у участников. CRM обновится только после подтверждения шлюза.',
      deleted: 'Сообщение удалено', messageDeleted: 'Это сообщение удалено', delete_expired: 'Срок удаления истёк. Сообщение не изменено.',
      delete_version_conflict: 'Сообщение изменилось. Обновите переписку перед удалением.', delete_message_unavailable: 'Это сообщение сейчас нельзя удалить.',
      delete_source_unavailable: 'Удаление сейчас недоступно для этого источника.', delete_provider_rejected: 'WhatsApp отклонил удаление. Сообщение сохранено.',
      delete_pending: 'Для этого сообщения уже ожидается подтверждение удаления.', delete_request_conflict: 'У этого запроса уже другое сообщение.',
      message_workspace_changed: 'Компания или сотрудник изменились. Откройте диалог снова.',
      denied: 'Недостаточно прав для этого действия.',
      unknown: 'Результат пока неизвестен. Текст сохранён, проверьте статус.',
      unavailable: 'Запрос пока не найден. Его можно безопасно повторить.',
      storage: 'Не удалось сохранить черновик и номер запроса.',
      failed: 'Действие не выполнено. Проверьте источник и права.',
    },
    az: {
      write: 'Nömrəyə yaz', source: 'WhatsApp mənbəyi', choose: 'Mənbə seçin',
      number: 'Ölkə kodu ilə nömrə', message: 'Mesaj', send: 'Göndər',
      close: 'Bağla', check: 'Statusu yoxla', repeat: 'Sorğunu təkrarla',
      pendingSends: 'Tamamlanmamış göndərişlər', otherNumber: 'Başqa nömrə',
      working: 'Yoxlanılır…', first_sent: 'WhatsApp göndərişi təsdiqlədi.',
      first_existing: 'Bu dialoq artıq var. Mətn qaralama kimi saxlanıldı.',
      first_kept: 'Dialoqun artıq qaralaması var. Yeni mətn «Nömrəyə yaz» pəncərəsində saxlanıldı.',
      first_blocked: 'Mesaj göndərilmədi. Mətn qaralama kimi saxlanıldı.',
      first_phone_invalid: 'Beynəlxalq nömrəni + və ölkə kodu ilə daxil edin.',
      first_text_invalid: '8 000 simvoladək mesaj daxil edin.',
      first_source_unavailable: 'Bu WhatsApp mənbəyi hazırda əlçatan deyil.',
      first_source_changed: 'Mənbə dəyişib. Dialoqu açın və bağlantını yoxlayın.',
      first_recipient_self: 'Seçilmiş mənbənin nömrəsini daxil etmisiniz. Müştərinin nömrəsini yazın.',
      first_recipient_not_registered: 'Bu nömrə WhatsApp-da qeydiyyatdan keçməyib.',
      first_recipient_unverified: 'Nömrəni WhatsApp-da təsdiqləmək mümkün olmadı. Nömrəni yoxlayıb təkrarlayın.',
      first_identity_conflict: 'Bu nömrənin təsdiqlənməmiş WhatsApp ünvanları var. Dialoqu yoxlayın.',
      first_admission_paused: 'Yeni dialoqların qəbulu dayandırılıb.',
      first_operator_offline: 'Yazışmaya başlamaq üçün «Onlayn» statusunu seçin.',
      first_request_conflict: 'Bu sorğunun nömrəsi, mənbəyi və ya mətni dəyişib.',
      edit: 'Redaktə et', editTitle: 'Mesajı redaktə et', save: 'Dəyişikliyi saxla',
      edited: 'Redaktə edilib', latest: 'Cari mətni yüklə', current: 'Cari mətn',
      edit_expired: 'Redaktə müddəti bitib. İlkin mətn saxlanıldı.',
      edit_version_conflict: 'Mətn artıq dəyişib. Saxlamazdan əvvəl cari versiyanı yükləyin.',
      edit_superseded: 'Dəyişiklik təsdiqləndi. Yazışmada daha yeni versiya göstərilir.',
      edit_message_unavailable: 'İlkin mesajı hazırda redaktə etmək mümkün deyil.',
      edit_source_unavailable: 'Bu mənbədə redaktə hazırda əlçatan deyil.',
      edit_provider_rejected: 'WhatsApp dəyişikliyi rədd etdi. İlkin mətn saxlanıldı.',
      edit_pending: 'Bu mesajın təsdiqlənməmiş dəyişikliyi var.',
      edit_no_change: 'Saxlamazdan əvvəl mətni dəyişin.',
      edit_text_invalid: '8 000 simvoladək mətn daxil edin.',
      edit_request_conflict: 'Bu sorğuya başqa mətn versiyası bağlıdır.',
      delete: 'Hamı üçün sil', deleteTitle: 'Mesaj hamı üçün silinsin?', deleteConfirm: 'WhatsApp bu mesajı iştirakçılardan siləcək. CRM yalnız şlüz təsdiqindən sonra yenilənəcək.',
      deleted: 'Mesaj silindi', messageDeleted: 'Bu mesaj silinib', delete_expired: 'Silmə müddəti bitib. Mesaj dəyişdirilmədi.',
      delete_version_conflict: 'Mesaj dəyişib. Silməzdən əvvəl yazışmanı yeniləyin.', delete_message_unavailable: 'Bu mesajı hazırda silmək mümkün deyil.',
      delete_source_unavailable: 'Bu mənbədə silmə hazırda əlçatan deyil.', delete_provider_rejected: 'WhatsApp silinməni rədd etdi. Mesaj saxlanıldı.',
      delete_pending: 'Bu mesajın silinməsi artıq təsdiq gözləyir.', delete_request_conflict: 'Bu sorğuya başqa mesaj bağlıdır.',
      message_workspace_changed: 'Şirkət və ya əməkdaş dəyişib. Dialoqu yenidən açın.',
      denied: 'Bu əməliyyat üçün icazə yoxdur.',
      unknown: 'Nəticə hələ məlum deyil. Mətn saxlanılıb, statusu yoxlayın.',
      unavailable: 'Sorğu hələ tapılmadı. Eyni sorğunu təkrarlaya bilərsiniz.',
      storage: 'Qaralamanı və sorğu nömrəsini saxlamaq mümkün olmadı.',
      failed: 'Əməliyyat baş tutmadı. Mənbəni və icazələri yoxlayın.',
    },
    en: {
      write: 'Message a number', source: 'WhatsApp source', choose: 'Choose a source',
      number: 'Number with country code', message: 'Message', send: 'Send',
      close: 'Close', check: 'Check status', repeat: 'Retry request',
      pendingSends: 'Pending sends', otherNumber: 'Another number',
      working: 'Checking…', first_sent: 'WhatsApp confirmed the send.',
      first_existing: 'This conversation already exists. Your text was saved as a draft.',
      first_kept: 'This conversation has a draft. Your new text is saved in “Message a number”.',
      first_blocked: 'The message was not sent. Your text was saved as a draft.',
      first_phone_invalid: 'Enter an international number starting with + and the country code.',
      first_text_invalid: 'Enter a message of up to 8,000 characters.',
      first_source_unavailable: 'This WhatsApp source is currently unavailable.',
      first_source_changed: 'The source changed. Open the conversation and check the connection.',
      first_recipient_self: 'This is the selected source’s number. Enter the customer’s number.',
      first_recipient_not_registered: 'This number is not registered with WhatsApp.',
      first_recipient_unverified: 'WhatsApp could not verify the number. Check it and try again.',
      first_identity_conflict: 'This number has conflicting WhatsApp addresses. Check the conversation.',
      first_admission_paused: 'New conversations are paused.',
      first_operator_offline: 'Go online to start a conversation.',
      first_request_conflict: 'The number, source or text for this request changed.',
      edit: 'Edit', editTitle: 'Edit message', save: 'Save edit',
      edited: 'Edited', latest: 'Load current text', current: 'Current text',
      edit_expired: 'The edit window expired. The original text was kept.',
      edit_version_conflict: 'The message already changed. Load the current version before saving.',
      edit_superseded: 'The edit was confirmed. The conversation shows a newer version.',
      edit_message_unavailable: 'The original message is currently unavailable for editing.',
      edit_source_unavailable: 'Editing is currently unavailable on this source.',
      edit_provider_rejected: 'WhatsApp rejected the edit. The original text was kept.',
      edit_pending: 'This message has an unconfirmed edit.',
      edit_no_change: 'Change the text before saving.',
      edit_text_invalid: 'Enter text of up to 8,000 characters.',
      edit_request_conflict: 'This request already has a different text version.',
      delete: 'Delete for everyone', deleteTitle: 'Delete this message for everyone?', deleteConfirm: 'WhatsApp will remove this message for participants. CRM changes only after gateway confirmation.',
      deleted: 'Message deleted', messageDeleted: 'This message was deleted', delete_expired: 'The delete window expired. The message was not changed.',
      delete_version_conflict: 'The message changed. Refresh the conversation before deleting it.', delete_message_unavailable: 'This message cannot be deleted right now.',
      delete_source_unavailable: 'Delete is currently unavailable for this source.', delete_provider_rejected: 'WhatsApp rejected the deletion. The message was kept.',
      delete_pending: 'A deletion for this message is already awaiting confirmation.', delete_request_conflict: 'This request is already tied to another message.',
      message_workspace_changed: 'The company or employee changed. Reopen the conversation.',
      denied: 'You do not have permission for this action.',
      unknown: 'The outcome is still unknown. Your text is saved. Check the status.',
      unavailable: 'The request was not found yet. You can retry the same request.',
      storage: 'The draft and request ID could not be stored.',
      failed: 'The action could not be completed. Check the source and permissions.',
    }
  };
  const t = key => (copy[window.tenantI18n?.language] || copy.en)[key];
  const session = member => ({
    expected_tenant_id: String(member.tenant_id), expected_user_id: String(member.telegram_id),
  });
  const storageKey = (kind, owner, route = '') =>
    ['tenant.whatsapp', kind, owner.expected_tenant_id, owner.expected_user_id, route].join(':');
  function read(key) {
    try { return JSON.parse(sessionStorage.getItem(key) || '{}') || {}; } catch { return {}; }
  }
  function store(key, value) {
    try {
      if (value) sessionStorage.setItem(key, JSON.stringify(value));
      else sessionStorage.removeItem(key);
      return true;
    } catch { return false; }
  }
  const errorCopy = error => t(error.message) || t(error.status === 401 || error.status === 403 ? 'denied' : 'failed');
  function element(tag, text) {
    const item = document.createElement(tag);
    if (text) item.textContent = text;
    return item;
  }
  function labeled(label, control) {
    const item = element('label');
    item.append(element('span', label), control);
    return item;
  }
  const post = (api, route, data) => api(base + route, {method: 'POST', body: JSON.stringify(data)});
  const get = (api, route, data) => api(base + route + '?' + new URLSearchParams(data));

  let active = null;
  function show(title, anchor) {
    active?.close();
    const dialog = element('dialog');
    dialog.className = 'operator-whatsapp-action';
    const heading = element('h2', t(title));
    heading.id = 'operatorWhatsAppActionTitle';
    dialog.setAttribute('aria-labelledby', heading.id);
    const form = element('form');
    const status = element('p');
    status.setAttribute('role', 'status');
    const actions = element('div');
    actions.className = 'operator-whatsapp-action-buttons';
    const close = element('button', t('close'));
    close.type = 'button';
    close.className = 'outline';
    const primary = element('button');
    primary.type = 'submit';
    primary.className = 'primary';
    close.onclick = () => dialog.close();
    actions.append(close, primary);
    form.append(heading, status, actions);
    dialog.append(form);
    const viewport = window.visualViewport;
    let messageBounds = anchor()?.getBoundingClientRect();
    function place() {
      messageBounds = anchor()?.getBoundingClientRect() || messageBounds;
      if (!messageBounds) return;
      const gutter = 12, gap = 8;
      const left = (viewport?.offsetLeft ?? 0) + gutter;
      const top = (viewport?.offsetTop ?? 0) + gutter;
      const right = left + (viewport?.width ?? document.documentElement.clientWidth) - 2 * gutter;
      const bottom = top + (viewport?.height ?? document.documentElement.clientHeight) - 2 * gutter;
      dialog.style.maxWidth = Math.max(0, right - left) + 'px';
      dialog.style.maxHeight = Math.max(0, bottom - top) + 'px';
      const {width, height} = dialog.getBoundingClientRect();
      const below = messageBounds.bottom + gap;
      const above = messageBounds.top - gap - height;
      const y = below + height <= bottom || above < top ? below : above;
      dialog.style.left = Math.max(left, Math.min(right - width, messageBounds.right - width)) + 'px';
      dialog.style.top = Math.max(top, Math.min(bottom - height, y)) + 'px';
    }
    const resize = new ResizeObserver(place);
    dialog.addEventListener('close', () => {
      if (active === dialog) active = null;
      resize.disconnect();
      window.removeEventListener('resize', place);
      viewport?.removeEventListener('resize', place);
      viewport?.removeEventListener('scroll', place);
      if (document.activeElement === document.body) anchor()?.focus({preventScroll:true});
      dialog.remove();
    });
    document.body.append(dialog);
    active = dialog;
    dialog.showModal();
    resize.observe(dialog);
    window.addEventListener('resize', place);
    viewport?.addEventListener('resize', place);
    viewport?.addEventListener('scroll', place);
    return {dialog, form, status, actions, primary, place};
  }

  function editKey(member, route, message) {
    return storageKey('edit', session(member), [route.channel_id, route.conversation_id, message.id].join('/'));
  }
  function canEdit(member, route, message) {
    if (!member || !route?.wecall || route.can_send !== true || route.owner_overview_read_only ||
        route.search_overview_read_only || route.archive_read_only || message.direction !== 'outgoing' ||
        message.message_type !== 'text' || String(message.source_channel_id) !== String(route.channel_id) ||
        message.source_conversation_id !== route.conversation_id || String(message.source_lead_id) !== String(route.lead_id)) return false;
    return (typeof message.wa_edit_version === 'string' && Date.parse(message.wa_edit_until) > Date.now()) ||
      !!read(editKey(member, route, message)).request_id;
  }

  function deleteKey(member, route, message) {
    return storageKey('delete', session(member), [route.channel_id, route.conversation_id, message.id].join('/'));
  }
  function canDelete(member, route, message) {
    if (!member || !route?.wecall || route.can_send !== true || route.owner_overview_read_only ||
        route.search_overview_read_only || route.archive_read_only || message.direction !== 'outgoing' ||
        message.message_deleted === true || String(message.source_channel_id) !== String(route.channel_id) ||
        message.source_conversation_id !== route.conversation_id || String(message.source_lead_id) !== String(route.lead_id)) return false;
    return (typeof message.wa_delete_version === 'string' && Date.parse(message.wa_delete_until) > Date.now()) ||
      !!read(deleteKey(member, route, message)).request_id;
  }

  function openDelete({member, route, message, anchor, api, isCurrent, reload, onSaved}) {
    if (!isCurrent() || !canDelete(member, route, message)) return;
    const owner = session(member), key = deleteKey(member, route, message), saved = read(key);
    let draft = {version: message.wa_delete_version, request_id: '', ...saved};
    const view = show('deleteTitle', anchor);
    view.status.textContent = t('deleteConfirm');
    let busy = false;
    const alive = () => view.dialog.open && isCurrent();
    const draw = () => { view.primary.disabled = busy || (!draft.request_id && !draft.version);
      view.primary.textContent = t(busy ? 'working' : draft.request_id && !draft.retry ? 'check' : 'delete'); };
    view.form.onsubmit = async event => {
      event.preventDefault();
      if (!alive() || busy) return;
      const checking = !!draft.request_id && !draft.retry;
      if (!draft.request_id) draft.request_id = crypto.randomUUID();
      draft.retry = false;
      if (!store(key, draft)) { view.status.textContent = t('storage'); draft.request_id = ''; return; }
      busy = true; draw();
      const ids = {...owner, lead_id: String(route.lead_id), message_id: message.id, request_id: draft.request_id};
      try {
        let result = checking ? await get(api, 'delete-message', ids) :
          await post(api, 'delete-message', {...ids, version: draft.version});
        for (let attempt = 0; !checking && result.state === 'pending' && attempt < 4 && alive(); attempt++) {
          await new Promise(resolve => setTimeout(resolve, 650));
          if (!alive()) break;
          result = await get(api, 'delete-message', ids);
        }
        if (!alive()) return;
        if (result.state === 'accepted') {
          store(key, null); view.dialog.close(); await reload(); onSaved(t('deleted'));
        } else if (result.state === 'rejected') {
          delete draft.request_id; store(key, draft); view.status.textContent = t(result.code) || t('failed');
        } else view.status.textContent = t('unknown');
      } catch (error) {
        if (!alive()) return;
        if (checking && error.message === 'delete_request_unavailable') {
          draft.retry = true; store(key, draft); view.status.textContent = t('unavailable');
        } else if (!checking && error.status && error.status < 500) {
          delete draft.request_id; store(key, draft); view.status.textContent = errorCopy(error);
        } else view.status.textContent = t(error.message) || t('unknown');
      } finally { busy = false; draw(); }
    };
    if (draft.request_id) view.status.textContent = t(draft.retry ? 'unavailable' : 'unknown');
    draw();
    view.place();
  }

  function openEdit({member, route, message, anchor, api, isCurrent, reload, getMessage, onSaved}) {
    if (!isCurrent() || !canEdit(member, route, message)) return;
    const owner = session(member);
    const view = show('editTitle', anchor);
    const key = editKey(member, route, message);
    const text = element('textarea');
    text.required = true;
    text.rows = 5;
    text.maxLength = 8000;
    const original = element('p');
    original.className = 'operator-whatsapp-current-text';
    original.hidden = true;
    const latest = element('button', t('latest'));
    latest.type = 'button';
    latest.className = 'outline';
    latest.hidden = true;
    view.actions.prepend(latest);
    view.status.before(original, labeled(t('message'), text));
    const saved = read(key);
    let draft = {version: message.wa_edit_version, text: message.body, ...saved};
    if (!draft.request_id && saved.version && saved.version !== message.wa_edit_version) {
      original.textContent = t('current') + '\n' + message.body;
      original.hidden = false;
      draft.version = message.wa_edit_version;
    }
    text.value = draft.text || '';
    const alive = () => view.dialog.open && isCurrent();
    let busy = false;
    let conflict = false;
    function draw() {
      text.readOnly = busy || !!draft.request_id;
      view.primary.disabled = busy || conflict || (!draft.request_id && !draft.version);
      view.primary.textContent = t(busy ? 'working' : draft.retry ? 'repeat' : draft.request_id ? 'check' : 'save');
    }
    latest.onclick = async () => {
      latest.disabled = true;
      try {
        await reload();
        const fresh = getMessage();
        if (!alive()) return;
        if (!fresh?.wa_edit_version || !canEdit(member, route, fresh)) {
          view.status.textContent = t('edit_message_unavailable');
          return;
        }
        draft.version = fresh.wa_edit_version;
        store(key, draft);
        conflict = false;
        latest.hidden = true;
        original.hidden = false;
        original.textContent = t('current') + '\n' + fresh.body;
        view.status.textContent = '';
        draw();
      } catch (error) {
        if (alive()) view.status.textContent = errorCopy(error);
      } finally { latest.disabled = false; }
    };
    text.addEventListener('input', () => {
      draft.text = text.value;
      store(key, draft);
    });
    view.form.onsubmit = async event => {
      event.preventDefault();
      if (!alive() || busy || conflict) return;
      const checking = !!draft.request_id && !draft.retry;
      if (!draft.request_id) draft.request_id = crypto.randomUUID();
      draft.retry = false;
      if (!store(key, draft)) {
        view.status.textContent = t('storage');
        draft.request_id = '';
        return;
      }
      busy = true;
      draw();
      const ids = {...owner, lead_id: String(route.lead_id), message_id: message.id, request_id: draft.request_id};
      try {
        let result = checking ? await get(api, 'edit-message', ids) :
          await post(api, 'edit-message', {...ids, version: draft.version, text: draft.text});
        // A successful gateway HTTP response means its authenticated edit is
        // queued for projection, not that the rendered message already changed.
        for (let attempt = 0; !checking && result.state === 'pending' && attempt < 4 && alive(); attempt++) {
          await new Promise(resolve => setTimeout(resolve, 650));
          if (!alive()) break;
          result = await get(api, 'edit-message', ids);
        }
        if (!alive()) return;
        if (result.state === 'accepted') {
          store(key, null);
          view.dialog.close();
          await reload();
          onSaved(t(result.code) || t('edited'));
        } else if (result.state === 'rejected') {
          reject(result.code);
        } else view.status.textContent = t('unknown');
      } catch (error) {
        if (!alive()) return;
        if (checking && error.message === 'edit_request_unavailable') {
          draft.retry = true;
          store(key, draft);
          view.status.textContent = t('unavailable');
        } else if (!checking && error.status && error.status < 500) {
          reject(error.message, errorCopy(error));
        } else {
          view.status.textContent = t(error.message) || t('unknown');
        }
      } finally {
        busy = false;
        draw();
      }
    };
    function reject(code, description = t(code) || t('failed')) {
      delete draft.request_id;
      store(key, draft);
      conflict = code === 'edit_version_conflict';
      latest.hidden = !conflict;
      view.status.textContent = description;
    }
    if (draft.request_id) view.status.textContent = t(draft.retry ? 'unavailable' : 'unknown');
    draw();
    view.place();
    text.focus({preventScroll:true});
  }

  const style = element('style');
  style.textContent = `
    .operator-whatsapp-action { position:fixed; inset:auto; margin:0; box-sizing:border-box; width:min(480px,calc(100% - 24px));
      max-height:calc(100dvh - 24px); padding:18px; border:1px solid var(--line);
      border-radius:12px; color:var(--ink); background:var(--card); }
    .operator-whatsapp-action::backdrop { background:#10142080; }
    .operator-whatsapp-action form { display:grid; gap:12px; }
    .operator-whatsapp-action h2 { margin:0; font-size:18px; }
    .operator-whatsapp-action label { display:grid; gap:5px; margin:0; min-width:0; }
    .operator-whatsapp-action label[hidden] { display:none; }
    .operator-whatsapp-action input, .operator-whatsapp-action select, .operator-whatsapp-action textarea {
      box-sizing:border-box; width:100%; max-width:100%; margin:0; color:var(--ink); background:var(--card);
    }
    .operator-whatsapp-action p { margin:0; overflow-wrap:anywhere; white-space:pre-wrap; }
    .operator-whatsapp-action [role=status]:empty { display:none; }
    .operator-whatsapp-action-buttons { display:flex; justify-content:flex-end; gap:8px; flex-wrap:wrap; }
    .operator-whatsapp-current-text { padding:8px; border-inline-start:2px solid var(--brand); }
    @media(max-width:600px){ .operator-whatsapp-action input,.operator-whatsapp-action select,.operator-whatsapp-action textarea { font-size:16px; }
      .operator-whatsapp-action-buttons button { min-height:44px; } }
  `;
  document.head.append(style);
  const close = () => { if (active?.open) active.close(); };
  for (const event of ['tenant-auth-pending', 'tenant-auth-invalid', 'tenant-chat-close', 'tenant-profile', 'tenant-language']) {
    document.addEventListener(event, close);
  }
  document.addEventListener('tenant-view', event => {
    if (event.detail?.view !== 'dialogs') close();
  });
  window.tenantWhatsAppMessageActions = {canEdit, openEdit, canDelete, openDelete, t};
})();
