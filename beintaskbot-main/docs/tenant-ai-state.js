/* Read-only, embeddable AI state. It never starts AI work or changes a dialogue. */
(() => {
  'use strict';
  const strings = {
    ru: {title:'Состояние AI',loading:'Проверяю…',refresh:'Обновить',unavailable:'Состояние AI недоступно. Проверьте вход и права.',available:'Доступен',unavailableState:'Недоступен',working:'Система работает',limited:'Есть ограничения',attention:'Требуется внимание',unknown:'Состояние неизвестно',service:'AI-сервис',enabled:'Включённые агенты',connected:'Подключённые каналы',errors:'Ошибки AI за 24 часа',stuck:'Зависшие задачи',noData:'Нет данных',agentsTitle:'Состояние агентов',eventsTitle:'Последние события',on:'Включён',off:'Выключен',manual:'Manual — готовит черновики',automatic:'Automatic — отвечает при разрешениях',ready:'Готов к новым сообщениям при выполнении условий',blocked:'Остановлен ограничением или настройкой',disabled:'Выключен',outside_schedule:'Вне расписания',test:'Только тестовый диалог, без автоотправки',allDay:'Без ограничения расписания',next:'Следующая смена',groups:'Групповые сообщения разрешены',groupsOff:'Группы не включены',humanCount:'Диалогов у сотрудника',pausedCount:'Диалогов на паузе',technical:'Технические подробности',copy:'Копировать безопасный отчёт',copied:'Отчёт скопирован',copyFailed:'Не удалось скопировать отчёт',worker:'Worker heartbeat',deployment:'Deployment',flags:'Ограничения сервера',queue:'Очередь AI',codes:'Коды событий',eventNormal:'AI не ответил — Диалог передан сотруднику',eventSkipped:'Сообщение пропущено согласно условиям агента',eventError:'Сообщение не обработано',eventReceived:'Сообщение получено',eventQueued:'Сообщение ожидает обработки',eventProcessing:'Сообщение обрабатывается',eventGenerated:'Ответ сохранён как черновик',eventSent:'Ответ отправлен',normalHelp:'Это нормальное поведение. AI не вмешивается, пока диалог у сотрудника или на паузе.',eventCheck:'Требуется проверка администратора.',eventPolicy:'Проверьте канал и условия работы агента.',open:'Открыть диалог',schedule:'Расписание',channel:'Канал',agent:'Агент',timeUnknown:'Время не указано',summaryNote:'Готовность всегда зависит от канала, расписания, лимитов и конкретного диалога.',limitedNote:'Показаны последние сохранённые события. Исторические ошибки могут быть неполными.',unconfirmed:'Отправки без подтверждения',quarantine:'Старые диалоги с неоднозначным каналом остаются заблокированы'},
    az: {title:'AI vəziyyəti',loading:'Yoxlanılır…',refresh:'Yenilə',unavailable:'AI vəziyyəti əlçatan deyil. Girişi və icazələri yoxlayın.',available:'Əlçatandır',unavailableState:'Əlçatan deyil',working:'Sistem işləyir',limited:'Məhdudiyyətlər var',attention:'Diqqət tələb olunur',unknown:'Vəziyyət məlum deyil',service:'AI xidməti',enabled:'Aktiv agentlər',connected:'Qoşulmuş kanallar',errors:'Son 24 saatda AI xətaları',stuck:'İlişmiş tapşırıqlar',noData:'Məlumat yoxdur',agentsTitle:'Agentlərin vəziyyəti',eventsTitle:'Son hadisələr',on:'Aktiv',off:'Söndürülüb',manual:'Manual — qaralama hazırlayır',automatic:'Automatic — icazə olduqda cavab verir',ready:'Şərtlər ödənildikdə yeni mesajlara hazırdır',blocked:'Məhdudiyyət və ya parametr səbəbindən dayandırılıb',disabled:'Söndürülüb',outside_schedule:'İş qrafikindən kənardır',test:'Yalnız test dialoqu, avtomatik göndəriş yoxdur',allDay:'Cədvəl məhdudiyyəti yoxdur',next:'Növbəti növbə',groups:'Qrup mesajlarına icazə verilib',groupsOff:'Qruplar aktiv edilməyib',humanCount:'Əməkdaşda olan dialoqlar',pausedCount:'Dayandırılmış dialoqlar',technical:'Texniki məlumat',copy:'Təhlükəsiz hesabatı köçür',copied:'Hesabat köçürüldü',copyFailed:'Hesabatı köçürmək mümkün olmadı',worker:'Worker heartbeat',deployment:'Deployment',flags:'Server məhdudiyyətləri',queue:'AI növbəsi',codes:'Hadisə kodları',eventNormal:'AI cavab vermədi — Dialoq əməkdaşa ötürülüb',eventSkipped:'Mesaj agentin şərtlərinə əsasən buraxıldı',eventError:'Mesaj emal edilmədi',eventReceived:'Mesaj alınıb',eventQueued:'Mesaj emal növbəsindədir',eventProcessing:'Mesaj emal olunur',eventGenerated:'Cavab qaralama kimi saxlanıb',eventSent:'Cavab göndərilib',normalHelp:'Bu normal davranışdır. Dialoq əməkdaşda və ya fasilədə olduqda AI müdaxilə etmir.',eventCheck:'Administrator yoxlaması tələb olunur.',eventPolicy:'Kanalı və agentin iş şərtlərini yoxlayın.',open:'Dialoqu aç',schedule:'İş qrafiki',channel:'Kanal',agent:'Agent',timeUnknown:'Vaxt göstərilməyib',summaryNote:'Hazırlıq həmişə kanaldan, cədvəldən, limitlərdən və konkret dialoqdan asılıdır.',limitedNote:'Son saxlanmış hadisələr göstərilir. Köhnə xətalar tam olmaya bilər.',unconfirmed:'Təsdiqlənməmiş göndərişlər',quarantine:'Kanalı qeyri-müəyyən olan köhnə dialoqlar bloklanmış qalır'},
    en: {title:'AI state',loading:'Checking…',refresh:'Refresh',unavailable:'AI state is unavailable. Check your session and permissions.',available:'Available',unavailableState:'Unavailable',working:'System is operating',limited:'Restrictions apply',attention:'Attention required',unknown:'State unknown',service:'AI service',enabled:'Enabled agents',connected:'Connected channels',errors:'AI errors in 24 hours',stuck:'Stuck jobs',noData:'No data',agentsTitle:'Agent state',eventsTitle:'Recent events',on:'Enabled',off:'Disabled',manual:'Manual — prepares drafts',automatic:'Automatic — replies when permitted',ready:'Eligible for new messages when conditions are met',blocked:'Stopped by a restriction or setting',disabled:'Disabled',outside_schedule:'Outside schedule',test:'Test dialogue only, no automatic sending',allDay:'No schedule restriction',next:'Next shift',groups:'Group messages allowed',groupsOff:'Groups not enabled',humanCount:'Dialogues with employees',pausedCount:'Paused dialogues',technical:'Technical details',copy:'Copy safe report',copied:'Report copied',copyFailed:'Could not copy report',worker:'Worker heartbeat',deployment:'Deployment',flags:'Server restrictions',queue:'AI queue',codes:'Event codes',eventNormal:'AI did not reply — Dialogue handed to an employee',eventSkipped:'Message skipped under the agent conditions',eventError:'Message was not processed',eventReceived:'Message received',eventQueued:'Message is waiting in the queue',eventProcessing:'Message is being processed',eventGenerated:'Reply saved as a draft',eventSent:'Reply sent',normalHelp:'This is expected. AI does not intervene while an employee owns the dialogue or it is paused.',eventCheck:'Administrator review is required.',eventPolicy:'Check the channel and agent conditions.',open:'Open dialogue',schedule:'Schedule',channel:'Channel',agent:'Agent',timeUnknown:'Time is not available',summaryNote:'Readiness always depends on the channel, schedule, limits and the individual dialogue.',limitedNote:'Recent recorded events are shown. Historical errors may be incomplete.',unconfirmed:'Unconfirmed sends',quarantine:'Legacy dialogues with ambiguous channels remain blocked'}
  };
  Object.assign(strings.ru,{tenant:'Tenant ID'});Object.assign(strings.az,{tenant:'Tenant ID'});Object.assign(strings.en,{tenant:'Tenant ID'});
  const humanCodes = new Set(['human_handoff','conversation_human','connect_human_takeover','customer_requested_manager','trigger_reply_sent_then_handoff']);
  const eventKey = event => {
    if (humanCodes.has(event.reason_code)) return 'eventNormal';
    if (event.status === 'error') return 'eventError';
    return ({received:'eventReceived',queued:'eventQueued',processing:'eventProcessing',generated:'eventGenerated',sent:'eventSent',skipped:'eventSkipped'})[event.status] || 'eventSkipped';
  };
  const eventTone = event => humanCodes.has(event.reason_code) ? 'normal' : event.status === 'error' ? 'error' : event.status === 'skipped' ? 'warning' : 'info';
  const value = (item, fallback) => typeof item === 'number' && Number.isFinite(item) && item >= 0 ? String(item) : fallback;
  const safeZone = zone => { try { new Intl.DateTimeFormat('en',{timeZone:zone}).format(); return zone; } catch { return undefined; } };

  function mount(host, options = {}) {
    let profile = options.profile, language = options.language || 'ru', epoch = 0, destroyed = false;
    const t = key => (strings[language] || strings.ru)[key] || strings.ru[key] || key;
    const node = (tag, text, className) => { const el = document.createElement(tag); if (text != null) el.textContent = String(text); if (className) el.className = className; return el; };
    const section = node('section', null, 'tenant-ai-state'); section.setAttribute('aria-live', 'polite'); host.replaceChildren(section);
    const current = (revision, tenant, user) => !destroyed && revision === epoch && String(profile?.tenant_id) === tenant && String(profile?.telegram_id) === user;
    const time = (stamp, zone) => { const date = new Date(stamp); if (Number.isNaN(date.getTime())) return t('timeUnknown'); return date.toLocaleString(language === 'az' ? 'az-AZ' : language === 'en' ? 'en-GB' : 'ru-RU', safeZone(zone) ? {timeZone:zone} : undefined); };
    async function request(resource, identity) {
      const response = await fetch('/api/platform/ai/' + resource + '?' + new URLSearchParams(identity), {credentials:'same-origin',cache:'no-store'});
      const body = await response.json().catch(() => ({}));
      if (!response.ok || !body.success || body.result?.schema_version !== 1) throw Error('unavailable');
      return body.result;
    }
    function stateLabel(summary) {
      if (summary.service === 'unavailable' || summary.state === 'attention') return ['attention','attention'];
      if (summary.state === 'conditional' || summary.state === 'not_ready') return ['limited','limited'];
      if (summary.service === 'available' && summary.state === 'ready') return ['working','working'];
      return ['unknown','unknown'];
    }
    function statusLine(agent, channel) {
      const rows = []; if (agent.state !== agent.mode) rows.push(t(agent.state));
      for (const code of channel.reason_codes || []) { const message = ({agent_disabled:'disabled',outside_schedule:'outside_schedule',manual_mode:'manual',auto_send_disabled:'manual',test_mode_draft_only:'test',group_messages_not_allowed:'groupsOff'})[code]; if (message && !(message==='manual'&&agent.mode==='manual')) rows.push(t(message)); }
      return [...new Set(rows)];
    }
    function appendTechnical(container, technical) {
      const groups = [['tenant',technical.tenant_id || null],['worker',technical.worker_last_seen_at || null],['deployment',technical.deployment_id || null],['flags',technical.server_flags || null],['queue',technical.queue || null],['codes',Array.isArray(technical.reason_codes) ? technical.reason_codes : null]];
      for (const [label, raw] of groups) {
        const row = node('section', null, 'tenant-ai-technical-group'); row.append(node('h4', t(label)));
        if (raw == null || (Array.isArray(raw) && !raw.length)) { row.append(node('p', t('noData'))); container.append(row); continue; }
        if (typeof raw === 'object' && !Array.isArray(raw)) { const list = node('dl'); for (const [key, item] of Object.entries(raw)) { const itemValue = typeof item === 'boolean' ? (item ? t('on') : t('off')) : typeof item === 'number' ? String(item) : t('noData'); list.append(node('dt', key), node('dd', itemValue)); } row.append(list); }
        else row.append(node('p', Array.isArray(raw) ? raw.join(', ') : String(raw)));
        container.append(row);
      }
    }
    function renderEvent(event, revision, tenant, user, zones) {
      const row = node('li', null, 'tenant-ai-event'); row.dataset.tone = eventTone(event);
      const details = node('div', null, 'tenant-ai-event-copy'), help = node('p', humanCodes.has(event.reason_code) ? t('normalHelp') : event.status === 'error' ? t('eventCheck') : event.status === 'skipped' ? t('eventPolicy') : '', 'tenant-ai-event-help'); details.append(node('strong', t(eventKey(event))), help);
      const meta = [event.agent_name, event.channel?.name, time(event.occurred_at, zones.get(event.agent_id))].filter(Boolean).join(' · '); row.append(details, node('small', meta));
      if (event.dialog && ['connect','kommo'].includes(event.dialog.provider) && Number(event.dialog.lead_id) > 0 && (event.dialog.provider !== 'connect' || event.dialog.channel_id) && typeof options.onOpenDialog === 'function') { const button = node('button', t('open')); button.type = 'button'; button.onclick = () => { if (current(revision, tenant, user)) options.onOpenDialog({...event.dialog}); }; row.append(button); }
      return row;
    }
    async function refresh() {
      if (destroyed) return;
      const revision = ++epoch, tenant = String(profile?.tenant_id || ''), user = String(profile?.telegram_id || '');
      const title = options.showTitle === false ? [] : [node('h2', t('title'))]; section.replaceChildren(...title, node('p', t('loading')));
      if (!tenant || !user) { section.replaceChildren(...title, node('p', t('unavailable'))); return; }
      const identity = {expected_tenant_id:tenant,expected_user_id:user,limit:'30'};
      try {
        const data = await request('state', identity); if (!current(revision, tenant, user)) return;
        const summary = data.summary && typeof data.summary === 'object' ? data.summary : {}, [summaryKey, tone] = stateLabel(summary), header = node('header', null, 'tenant-ai-state-header');
        header.append(...title, node('span', t(summaryKey), 'tenant-ai-overall-status')); header.lastElementChild.dataset.tone = tone; section.replaceChildren(header);
        const service = node('p', t('service') + ': ' + (summary.service === 'available' ? t('available') : summary.service === 'unavailable' ? t('unavailableState') : t('noData')), 'tenant-ai-service'); service.dataset.state = summary.service || 'unknown'; section.append(service);
        const metrics = node('dl', null, 'tenant-ai-state-counts');
        for (const [key, count] of [['enabled',summary.enabled_agents],['connected',summary.connected_channels],['errors',summary.ai_errors_24h],['stuck',summary.stuck_jobs]]) { const card = node('div', null, 'tenant-ai-metric'); card.dataset.metric = key; card.append(node('dt', t(key)), node('dd', value(count, t('noData')))); metrics.append(card); }
        section.append(metrics, node('p', t('summaryNote'), 'tenant-ai-note'));
        if (summary.unconfirmed_sends_24h != null) section.append(node('p', t('unconfirmed') + ': ' + value(summary.unconfirmed_sends_24h, t('noData')), 'tenant-ai-note'));
        if (summary.quarantined_legacy_dialogues) section.append(node('p', t('quarantine') + ': ' + summary.quarantined_legacy_dialogues, 'tenant-ai-note'));
        const reload = node('button', t('refresh')); reload.type = 'button'; reload.className = 'outline'; reload.onclick = refresh; section.append(reload);
        const agents = Array.isArray(data.agents) ? data.agents : [], zones = new Map(agents.map(agent => [agent.id, agent.schedule?.timezone]));
        section.append(node('h3', t('agentsTitle'))); const cards = node('div', null, 'tenant-ai-state-cards');
        for (const agent of agents) {
          const card = node('article'), heading = node('header'), badge = node('span', agent.enabled === true ? t('on') : agent.enabled === false ? t('off') : t('noData'), 'tenant-ai-badge'); badge.dataset.enabled = String(agent.enabled); heading.append(node('h4', agent.name || t('agent')), badge); card.append(heading);
          const chips = node('div', null, 'tenant-ai-state-chips'); chips.append(node('span', t(agent.mode || 'manual'))); if (agent.state && agent.state !== agent.mode) chips.append(node('span', t(agent.state))); card.append(chips);
          const schedule = agent.schedule || {}; card.append(node('p', schedule.start ? `${t('schedule')}: ${schedule.start}–${schedule.end} · ${schedule.timezone || t('noData')}` : t('allDay'), 'tenant-ai-agent-schedule'));
          if (agent.next_shift_at) card.append(node('p', t('next') + ': ' + time(agent.next_shift_at, schedule.timezone), 'tenant-ai-agent-schedule'));
          for (const channel of Array.isArray(agent.channels) ? agent.channels : []) { const item = node('section', null, 'tenant-ai-state-channel'); item.append(node('strong', channel.name || t('channel'))); for (const label of statusLine(agent, channel)) item.append(node('p', label)); if (channel.provider === 'connect') item.append(node('small', t(channel.group_messages_allowed ? 'groups' : 'groupsOff'))); for (const [key,label] of [['human','humanCount'],['paused','pausedCount']]) if (Number(channel.dialogue_states?.[key]) > 0) item.append(node('p', t(label) + ': ' + channel.dialogue_states[key])); card.append(item); }
          cards.append(card);
        }
        section.append(cards); if (!agents.length) section.append(node('p', t('noData')));
        section.append(node('h3', t('eventsTitle'))); const events = node('ul', null, 'tenant-ai-events'); for (const event of Array.isArray(data.events) ? data.events : []) events.append(renderEvent(event, revision, tenant, user, zones)); section.append(events); if (!Array.isArray(data.events) || !data.events.length) section.append(node('p', t('noData')));
        section.append(node('p', t('limitedNote'), 'tenant-ai-note'));
        if (data.can_view_technical === true) {
          const details = node('details', null, 'tenant-ai-technical'), content = node('div'); details.append(node('summary', t('technical')), content); section.append(details); let loaded = false;
          details.ontoggle = async () => { if (!details.open || loaded || !current(revision, tenant, user)) return; loaded = true; content.replaceChildren(node('p', t('loading'))); try { const diagnostic = await request('state/technical', identity); if (!current(revision, tenant, user)) return; const technical = diagnostic.technical && typeof diagnostic.technical === 'object' ? diagnostic.technical : {}; content.replaceChildren(); appendTechnical(content, technical); const report = JSON.stringify({schema_version:1,generated_at:diagnostic.generated_at,technical}, null, 2), copy = node('button', t('copy')); copy.type = 'button'; copy.onclick = async () => { if (!current(revision, tenant, user)) return; try { await navigator.clipboard.writeText(report); if (current(revision, tenant, user)) copy.textContent = t('copied'); } catch { if (current(revision, tenant, user)) copy.textContent = t('copyFailed'); } }; content.append(copy); } catch { if (current(revision, tenant, user)) content.replaceChildren(node('p', t('unavailable'))); } };
        }
      } catch { if (current(revision, tenant, user)) section.replaceChildren(...title, node('p', t('unavailable'))); }
    }
    function destroy() { destroyed = true; ++epoch; section.remove(); document.removeEventListener('tenant-profile', changed); }
    function changed() { destroy(); }
    document.addEventListener('tenant-profile', changed); refresh();
    return {refresh, destroy, setLanguage(value) { language = ['ru','az','en'].includes(value) ? value : 'ru'; return refresh(); }};
  }
  window.tenantAIState = {mount};
})();
