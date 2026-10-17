/* One embeddable AI state section. Terra owns its entry point; no auto-mount/fetch. */
(() => {
  'use strict';
  const strings = {
    ru: {title:'Состояние AI',loading:'Проверяю…',refresh:'Обновить',unavailable:'Состояние AI недоступно. Проверьте вход и права.',service:'AI-сервис',available:'Доступен',unavailableState:'Недоступен',enabled:'Включено агентов',connected:'Подключено каналов',errors:'Ошибки AI за 24 часа',sends:'Отправки без подтверждения',conditional:'Готовность зависит от канала, расписания, лимитов и состояния конкретного диалога.',limited:'Показаны последние сохранённые события. Исторические ошибки могли сохраняться не полностью; состояние подключений получено из кеша.',quarantine:'Старые диалоги с неоднозначным каналом остаются заблокированы',on:'Включён',off:'Выключен',manual:'Готовит черновики — отправляет сотрудник',automatic:'Отвечает автоматически, если разрешено политикой',ready:'Готов к новым сообщениям при выполнении условий',blocked:'Остановлен ограничением или настройкой',disabled:'Выключен',outside_schedule:'Вне расписания',test:'Только выбранный тестовый диалог, без автоотправки',noEvents:'Для агента нет событий в показанной выборке.',noAgent:'События без подтверждённого агента',open:'Открыть диалог',technical:'Технические подробности',copy:'Копировать безопасный отчёт',copied:'Отчёт скопирован',copyFailed:'Не удалось скопировать отчёт',allDay:'Без ограничения расписания',next:'Следующая смена',human:'Диалог передан сотруднику',paused:'AI в диалоге приостановлен',schedule:'AI не работает вне заданного расписания',policy:'AI остановлен проверкой политики. Обратитесь к администратору.',error:'Не удалось обработать сообщение — требуется проверка',media:'Не удалось обработать голосовое сообщение',limit:'Достигнут лимит AI. Проверьте лимиты или дождитесь их обновления.',model:'Ошибка AI-провайдера. Обратитесь к администратору.',connect:'Результат отправки не подтверждён. Не отправляйте повторно без проверки диалога.',received:'Сообщение получено; обработка пока не подтверждена',queued:'Сообщение ожидает обработки',processing:'Сообщение обрабатывается',generated:'Ответ сохранён как черновик',sent:'Ответ отправлен',skipped:'Сообщение пропущено; проверьте условия работы агента',noChannel:'Канал недоступен',worker:'Нет свежего сигнала от worker. Обратитесь к администратору.',configuration:'Проверьте настройки AI у администратора',groups:'Групповые сообщения разрешены',groupsOff:'Группы не включены',humanCount:'Диалогов у сотрудника',pausedCount:'Диалогов на паузе'},
    az: {title:'AI vəziyyəti',loading:'Yoxlanılır…',refresh:'Yenilə',unavailable:'AI vəziyyəti əlçatan deyil. Girişi və icazələri yoxlayın.',service:'AI xidməti',available:'Əlçatandır',unavailableState:'Əlçatan deyil',enabled:'Aktiv agentlər',connected:'Qoşulmuş kanallar',errors:'Son 24 saatda AI xətaları',sends:'Təsdiqlənməmiş göndərişlər',conditional:'Hazırlıq kanaldan, cədvəldən, limitlərdən və konkret dialoqun vəziyyətindən asılıdır.',limited:'Son saxlanmış hadisələr göstərilir. Köhnə xətalar tam saxlanmamış ola bilər; bağlantı vəziyyəti keşdəndir.',quarantine:'Kanalı qeyri-müəyyən olan köhnə dialoqlar bloklanmış qalır',on:'Aktiv',off:'Söndürülüb',manual:'Qaralama hazırlayır — əməkdaş göndərir',automatic:'Siyasət icazə verdikdə avtomatik cavab verir',ready:'Şərtlər ödənildikdə yeni mesajlara hazırdır',blocked:'Məhdudiyyət və ya parametr səbəbindən dayandırılıb',disabled:'Söndürülüb',outside_schedule:'İş qrafikindən kənardır',test:'Yalnız seçilmiş test dialoqu, avtomatik göndəriş yoxdur',noEvents:'Göstərilən nümunədə agent hadisəsi yoxdur.',noAgent:'Agenti təsdiqlənməmiş hadisələr',open:'Dialoqu aç',technical:'Texniki məlumat',copy:'Təhlükəsiz hesabatı köçür',copied:'Hesabat köçürüldü',copyFailed:'Hesabatı köçürmək mümkün olmadı',allDay:'Cədvəl məhdudiyyəti yoxdur',next:'Növbəti növbə',human:'Dialoq əməkdaşa ötürülüb',paused:'Dialoqda AI dayandırılıb',schedule:'AI təyin edilmiş iş qrafikindən kənarda işləmir',policy:'AI siyasət yoxlaması ilə dayandırılıb. Administratora müraciət edin.',error:'Mesaj emal edilmədi — yoxlama tələb olunur',media:'Səsli mesaj emal edilmədi',limit:'AI limiti bitib. Limitləri yoxlayın və ya yenilənməsini gözləyin.',model:'AI provayder xətası. Administratora müraciət edin.',connect:'Göndəriş təsdiqlənməyib. Dialoqu yoxlamadan təkrar göndərməyin.',received:'Mesaj alınıb; emal hələ təsdiqlənməyib',queued:'Mesaj emal növbəsindədir',processing:'Mesaj emal olunur',generated:'Cavab qaralama kimi saxlanıb',sent:'Cavab göndərilib',skipped:'Mesaj buraxılıb; agentin iş şərtlərini yoxlayın',noChannel:'Kanal əlçatan deyil',worker:'Worker-dən yeni siqnal yoxdur. Administratora müraciət edin.',configuration:'Administratorla AI parametrlərini yoxlayın',groups:'Qrup mesajlarına icazə verilib',groupsOff:'Qruplar aktiv edilməyib',humanCount:'Əməkdaşda olan dialoqlar',pausedCount:'Dayandırılmış dialoqlar'},
    en: {title:'AI state',loading:'Checking…',refresh:'Refresh',unavailable:'AI state is unavailable. Check your session and permissions.',service:'AI service',available:'Available',unavailableState:'Unavailable',enabled:'Enabled agents',connected:'Connected channels',errors:'AI errors in 24 hours',sends:'Unconfirmed sends',conditional:'Readiness depends on the channel, schedule, limits and the individual dialogue.',limited:'Showing recent recorded events. Historical errors may be incomplete; connection status is cached.',quarantine:'Legacy dialogues with ambiguous channels remain blocked',on:'Enabled',off:'Disabled',manual:'Prepares drafts — an employee sends them',automatic:'Replies automatically when policy permits',ready:'Eligible for new messages when conditions are met',blocked:'Stopped by a restriction or setting',disabled:'Disabled',outside_schedule:'Outside schedule',test:'Selected test dialogue only, no automatic sending',noEvents:'No events for this agent in the displayed sample.',noAgent:'Events without a confirmed agent',open:'Open dialogue',technical:'Technical details',copy:'Copy safe report',copied:'Report copied',copyFailed:'Could not copy report',allDay:'No schedule restriction',next:'Next shift',human:'Dialogue handed to an employee',paused:'AI is paused in this dialogue',schedule:'AI does not run outside the configured schedule',policy:'AI was stopped by a policy check. Contact an administrator.',error:'Could not process the message — investigation needed',media:'Could not process the voice message',limit:'AI limit reached. Check limits or wait for their reset.',model:'AI provider error. Contact an administrator.',connect:'Sending was not confirmed. Check the dialogue before trying again.',received:'Message received; processing is not yet confirmed',queued:'Message is waiting in the queue',processing:'Message is being processed',generated:'Reply saved as a draft',sent:'Reply sent',skipped:'Message skipped; check the agent conditions',noChannel:'Channel unavailable',worker:'No recent worker heartbeat. Contact an administrator.',configuration:'Ask an administrator to check AI settings',groups:'Group messages allowed',groupsOff:'Groups not enabled',humanCount:'Dialogues with employees',pausedCount:'Paused dialogues'}
  };
  Object.assign(strings.ru, {hold:'Диалог передан сотруднику или приостановлен',serverOff:'AI выключен на сервере. Обратитесь к администратору.'});
  Object.assign(strings.az, {hold:'Dialoq əməkdaşa ötürülüb və ya dayandırılıb',serverOff:'Serverdə AI söndürülüb. Administratora müraciət edin.'});
  Object.assign(strings.en, {hold:'Dialogue handed to an employee or paused',serverOff:'AI is disabled on the server. Contact an administrator.'});
  const describe = (code, status, t) => {
    if (['conversation_human_or_paused','conversation_paused_or_human'].includes(code)) return t('hold');
    if (['manager_replied','human_handoff','conversation_human','conversation_human_or_paused','conversation_paused_or_human','connect_human_takeover','customer_requested_manager','trigger_reply_sent_then_handoff'].includes(code)) return t('human');
    if (code === 'conversation_paused' || code === 'manager_pause') return t('paused');
    if (code === 'outside_schedule') return t('schedule');
    if (code === 'manual_mode' || code === 'auto_send_disabled') return t('manual');
    if (code === 'test_mode_draft_only') return t('test');
    if (code === 'agent_disabled' || code === 'company_ai_disabled') return t('disabled');
    if (code.includes('limit') || code.includes('quota')) return t('limit');
    if (code.startsWith('voice_')) return t('media');
    if (code.startsWith('chat_ai_http_')) return t('model');
    if (['connect_send_unknown','send_result_unknown'].includes(code)) return t('connect');
    if (code === 'worker_unavailable') return t('worker');
    if (code === 'ai_agents_server_disabled') return t('serverOff');
    if (['integration_disconnected','channel_connection_unconfirmed','channel_not_in_catalog'].includes(code)) return t('noChannel');
    if (['model_unavailable','ai_configuration_not_ready'].includes(code)) return t('configuration');
    if (code && !['queue_delay','received_not_queued','send_in_progress'].includes(code)) return t(status === 'error' ? 'error' : 'policy');
    return t(status || 'blocked');
  };
  function mount(host, options = {}) {
    let profile = options.profile, language = options.language || 'ru', epoch = 0, destroyed = false;
    const t = key => (strings[language] || strings.ru)[key] || strings.ru[key] || strings.ru.error;
    const node = (tag, text, className) => { const el = document.createElement(tag); if (text != null) el.textContent = String(text); if (className) el.className = className; return el; };
    const section = node('section', null, 'tenant-ai-state'); section.setAttribute('aria-live', 'polite'); host.replaceChildren(section);
    const current = (revision, tenant, user) => !destroyed && revision === epoch && String(profile?.tenant_id) === tenant && String(profile?.telegram_id) === user;
    async function request(resource, identity) {
      const response = await fetch('/api/platform/ai/' + resource + '?' + new URLSearchParams(identity), {credentials:'same-origin',cache:'no-store'});
      const body = await response.json();
      if (!response.ok || !body.success || body.result?.schema_version !== 1) throw Error('unavailable');
      return body.result;
    }
    const time = value => { const date = new Date(value); return Number.isNaN(date.getTime()) ? '' : date.toLocaleString(language === 'az' ? 'az-AZ' : language === 'en' ? 'en-GB' : 'ru-RU'); };
    function renderEvent(event, revision, tenant, user) {
      const row = node('li'); row.append(node('strong', describe(event.reason_code || '', event.status, t)), node('p', event.channel?.name), node('small', time(event.occurred_at)));
      if (event.dialog && typeof options.onOpenDialog === 'function') {
        const button = node('button', t('open')); button.type = 'button';
        button.onclick = () => { if (current(revision, tenant, user)) options.onOpenDialog({...event.dialog}); };
        row.append(button);
      }
      return row;
    }
    async function refresh() {
      if (destroyed) return;
      const revision = ++epoch, tenant = String(profile?.tenant_id || ''), user = String(profile?.telegram_id || '');
      section.replaceChildren(node('h2', t('title')), node('p', t('loading')));
      if (!tenant || !user) { section.replaceChildren(node('h2', t('title')), node('p', t('unavailable'))); return; }
      const identity = {expected_tenant_id:tenant,expected_user_id:user,limit:'30'};
      try {
        const data = await request('state', identity); if (!current(revision, tenant, user)) return;
        section.replaceChildren(node('h2', t('title')));
        const summary = data.summary;
        section.append(node('p', t('service') + ': ' + t(summary.service === 'available' ? 'available' : 'unavailableState')));
        const counts = node('dl', null, 'tenant-ai-state-counts');
        for (const [key, value] of [['enabled',summary.enabled_agents],['connected',summary.connected_channels],['errors',summary.ai_errors_24h],['sends',summary.unconfirmed_sends_24h]]) counts.append(node('dt', t(key)), node('dd', value));
        section.append(counts, node('p', t('conditional')), node('p', t('limited')));
        if (summary.quarantined_legacy_dialogues) section.append(node('p', t('quarantine') + ': ' + summary.quarantined_legacy_dialogues));
        const reload = node('button', t('refresh')); reload.type = 'button'; reload.onclick = refresh; section.append(reload);
        const cards = node('div', null, 'tenant-ai-state-cards');
        for (const agent of data.agents) {
          const card = node('article'); card.append(node('h3', agent.name), node('p', t(agent.enabled ? 'on' : 'off')), node('p', t(agent.mode)), node('p', t(agent.state)));
          const schedule = agent.schedule || {};
          card.append(node('p', schedule.start ? `${schedule.start}–${schedule.end} · ${schedule.timezone} · ${(schedule.days || []).map(day => new Intl.DateTimeFormat(language, {weekday:'short',timeZone:'UTC'}).format(new Date(Date.UTC(2024,0,1+day)))).join(', ')}` : t('allDay')));
          if (agent.next_shift_at) card.append(node('p', t('next') + ': ' + time(agent.next_shift_at)));
          for (const channel of agent.channels) {
            const item = node('div', null, 'tenant-ai-state-channel'); item.append(node('strong', channel.name), node('p', t(channel.state)));
            for (const label of new Set(channel.reason_codes.map(code => describe(code, channel.state, t)))) item.append(node('p', label));
            if (channel.provider === 'connect') item.append(node('small', t(channel.group_messages_allowed ? 'groups' : 'groupsOff')));
            for (const [key, label] of [['human','humanCount'],['paused','pausedCount']]) if (channel.dialogue_states?.[key]) item.append(node('p', t(label) + ': ' + channel.dialogue_states[key]));
            card.append(item);
          }
          const list = node('ul'); for (const event of agent.recent_events) list.append(renderEvent(event, revision, tenant, user));
          card.append(list); if (!agent.recent_events.length) card.append(node('p', t('noEvents'))); cards.append(card);
        }
        section.append(cards);
        const unassigned = data.events.filter(event => !event.agent_id);
        if (unassigned.length) { section.append(node('h3', t('noAgent'))); const list = node('ul'); unassigned.forEach(event => list.append(renderEvent(event, revision, tenant, user))); section.append(list); }
        if (data.can_view_technical) {
          const details = node('details'), content = node('div'); details.append(node('summary', t('technical')), content); section.append(details);
          let loaded = false;
          details.ontoggle = async () => {
            if (!details.open || loaded || !current(revision, tenant, user)) return; loaded = true; content.replaceChildren(node('p', t('loading')));
            try {
              const diagnostic = await request('state/technical', identity); if (!current(revision, tenant, user)) return;
              // Copy only the server's allowlisted technical object, never names,
              // dialogue links, message IDs, DOM text or arbitrary API errors.
              const report = JSON.stringify({schema_version:1,generated_at:diagnostic.generated_at,technical:diagnostic.technical}, null, 2);
              content.replaceChildren(node('pre', report)); const copy = node('button', t('copy')); copy.type = 'button';
              copy.onclick = async () => { if (!current(revision, tenant, user)) return; try { await navigator.clipboard.writeText(report); if (current(revision, tenant, user)) copy.textContent = t('copied'); } catch { if (current(revision, tenant, user)) copy.textContent = t('copyFailed'); } };
              content.append(copy);
            } catch { if (current(revision, tenant, user)) content.replaceChildren(node('p', t('unavailable'))); }
          };
        }
      } catch { if (current(revision, tenant, user)) section.replaceChildren(node('h2', t('title')), node('p', t('unavailable'))); }
    }
    function destroy() { destroyed = true; ++epoch; section.remove(); document.removeEventListener('tenant-profile', changed); }
    function changed() { destroy(); } // Integrator remounts only for the new identity.
    document.addEventListener('tenant-profile', changed);
    refresh();
    return {refresh,destroy,setLanguage(value) { language = ['ru','az','en'].includes(value) ? value : 'ru'; return refresh(); }};
  }
  window.tenantAIState = {mount};
})();
