# Каталог каналов и «Состояние AI»: контракт интеграции v1

Ветка `codex/channel-catalog-ai-state`, база `9f3bf628`. Миграции не нужны.
Изменения только для `/app`; `/webapp`, настройки агентов и production не меняются.

## HTTP и права

Три новых GET endpoint:

- `/api/platform/ai/channels` — каталог для мастера Sol; owner или admin с существующим `ai_agents_manage`.
- `/api/platform/ai/state` — пользовательская сводка; существующее право `dialogs` (включая owner и AI-admin).
- `/api/platform/ai/state/technical` — та же сводка с `technical`; дополнительно **серверная** проверка текущего owner/admin+`ai_agents_manage` в БД.

Обязательные query-поля: `expected_tenant_id`, `expected_user_id`, точное совпадение с текущей сессией. Роль и tenant из тела/параметров не принимаются как полномочия. Несовпадение кабинета: 409; нет права: 403. Чтение не требует CSRF-mutation, не разрешает POST/PATCH/DELETE. Ответы `Cache-Control: no-store`.

Envelope: `{success:true,result:{schema_version:1,...}}`. Ошибка: `{success:false,error_code}` без исходного исключения/ответа провайдера. Поддерживаемые коды: `access_denied`, `identity_changed`, `invalid_limit`, `ai_state_unavailable`. Для состояния необязательный `limit=30`, диапазон 1–100; список событий ограничен, не является полной историей.

Старые `/api/platform/ai/diagnostics` и `/api/platform/ai/reliability` сохраняются, включая авторизацию и совместимый ответ. Новая проекция использует их общие внутренние SQL-reader функции под текущей авторизацией, не обращаясь по HTTP к самой себе. `/api/platform/diagnostics` (общая диагностика CRM) также не удаляется.

## Каталог каналов

Пример одного элемента `result.channels`:

```json
{
  "id": "connect:00000000-0000-0000-0000-000000000201",
  "channel_id": "00000000-0000-0000-0000-000000000201",
  "provider": "connect",
  "type": "whatsapp",
  "name": "Поддержка WhatsApp",
  "connection": {"state": "connected", "source": "cached", "checked_at": "2026-10-17T10:00:00+00:00"},
  "ai": {"supported": true, "available": true, "reason_codes": [], "availability_scope": "channel"},
  "capabilities": {"text_replies": true, "incoming_voice": true, "voice_replies": false, "groups": true, "drafts": true, "crm_actions": false},
  "scope": {"provider": "connect", "channel_id": "00000000-0000-0000-0000-000000000201"},
  "requirements": ["agent_policy", "group_opt_in", "voice_provider_and_decoder", "resolved_dialogue_scope"]
}
```

- `id` — составной ключ **отображения**, уникальный внутри каталога текущего tenant. Его нельзя записывать в runtime `channel_id`.
- `channel_id` и `scope` сохраняют действующие ID. Идентичность диалога остаётся `tenant + provider + channel + conversation`.
- Полный канальный ключ состояния уже используется для Connect. Существующее хранилище Kommo сохраняет прежнюю семантику состояния по chat/conversation с пустым `scope_channel_id`; этот пакет его не мигрирует. Реальный Kommo channel остаётся в маршруте, scope и строгом Test Mode binding. Каталог не обещает новый механизм разделения одного Kommo chat между несколькими каналами.
- `type=whatsapp` доказан текущим WhatsApp-only адаптером Connect. Для каналов Kommo возвращается `unknown`: нет достоверного справочника типа/названия. Не угадывать тип по UUID, имени или факту подключения CRM.
- `name` — сохранённое название подключения; резервное имя `WhatsApp` или `Канал CRM`, без UUID в основном заголовке. Провайдера показывать вторичной подписью, не как тип общения.
- `connection.state`: `connected`, `disconnected`, `connecting`, `pending_qr`, `reconnecting`, `reconnect_required`, `error`, `disabled`, `unknown`. Это **сохранённое наблюдение**, не текущий live health. `checked_at` — время локального обновления/наблюдения, не новая проверка сессии.
- `ai.supported` — runtime знает этот адаптер. `ai.available` — подключение допустимо для конфигурации scope. Оно **не зависит** от включения компании/агента, Manual/Automatic, расписания или квот. Это не обещание ответа.
- Для Connect `available` требует активного канала, подключённой интеграции, сохранённого `connected` и допустимого статуса tenant. Неизвестное состояние закрывает выбор.
- Для Kommo `available` требует подключённой CRM и active tenant. Сам канал доказан ранее принятой Kommo AI-задачей; его live-состояние неизвестно. Поэтому `connection.state=unknown` совместимо с `ai.available=true`. Эта ограниченность явно отражена в `limitations`.
- `reason_codes`: `integration_disconnected`, `channel_connection_unconfirmed`, `tenant_inactive`.
- `capabilities` описывает реализованные возможности, **не выдаёт разрешение на действие**. Runtime повторно проверяет все policies.
- Connect: текст, входящий голос с существующим AI/ffmpeg, черновики, группы только с явным `allow_group_messages=true`. Голосовые ответы и Kommo CRM-действия не реализованы.
- Kommo: текст, черновики, существующие CRM-действия при дополнительных разрешениях. Голос и группы не объявляются поддержанными. `requirements`: `pipeline_id`, `stage_id`, `agent_policy`, `crm_action_policy`, `exact_talk_route`.
- `limited` сигнализирует ограничение 500 каналов каждого provider. `multiple_scopes_supported=true`. `integrations` содержит только безопасные booleans подключённых Connect/Kommo.
- Нет фиктивных Instagram/Telegram подключений. Новая интеграция требует настоящего адаптера, а не добавления frontend option.

### Преобразование выбора в existing scopes

Для каждого Connect-канала брать `scope.provider/channel_id`, добавлять `pipeline_id:''`, `stage_id:''`, `allow_group_messages:false` по умолчанию. Для Kommo обязательно добавить выбранные из existing `agents/options` `pipeline_id` и `stage_id`. Не использовать `id` вместо `channel_id`, не заменять scope одним названием типа WhatsApp.

Runtime уже поддерживает несколько WhatsApp-каналов у одного агента, разных агентов на непересекающихся каналах, несколько Kommo scopes и смешанные Connect+Kommo scopes. Модель/расписание общие для агента; история/состояние/задачи Connect разделяются по каналу. Проверки overlap включённых агентов сохраняются. Existing Kommo wildcard scopes продолжают работать; новый мастер не обязан создавать wildcard. Test Mode ограничен существующей точной привязкой для каждого провайдера, а не всеми отмеченными каналами сразу.

Исправлен старый `agents/options.channels`: он теперь читает только `provider='kommo'`. Ранее там могли появляться Connect UUID. У Kommo queue кандидаты и `_scope_matches` теперь явно исключают Connect scopes.

Полный прогон также воспроизвёл существующую в базе `9f3bf628` ошибку заголовка группы: старое `contact_name` перекрывало `display_name`. Одно выражение read projection в `tenant_operator_dialogs.py` теперь выбирает для группы `manual_display_name → display_name`; личные диалоги сохраняют прежний приоритет. Записи, WhatsApp subjects и ручные имена не изменяются.

## Единое состояние

`summary`: `service` (`available|unavailable` — heartbeat), `state` (`attention|conditional|not_ready`), `enabled_agents`, `eligible_agents`, `connected_channels` (только наблюдавшиеся connected), `ai_errors_24h`, `unconfirmed_sends_24h`, `quarantined_legacy_dialogues`.

Heartbeat не считается доказательством успешной работы. Проверяются настройки компании/агента, модель, подключения, расписание, Test Mode binding и фактические ошибки/очередь. Квоты, актуальность провайдера и состояние конкретного нового диалога окончательно проверяются runtime. Нет выдуманного «всё исправно». Ошибки отражают сохранённые receipts за 24 часа; историческая полнота не гарантируется и показывается пользователю.

`agents`: `id`, `name`, `enabled`, `mode`, `state`, `schedule`, `schedule_open`, `next_shift_at`, `channels`, `recent_events`. Состояния: `disabled`, `blocked`, `outside_schedule`, `manual`, `test`, `ready` (условная готовность к новым сообщениям). Настроенный Automatic отдельно от фактического разрешения автоотправки. Manual и запрет автоотправки означают черновики, а не сбой. Расписание рассчитывается существующим helper, включая переход через полночь и timezone.

В `agents[].channels`: исходный scope без инструкций; `state`, `reason_codes`, `group_messages_allowed`, `dialogue_states` с отдельными счётчиками Human/Pause по agent+provider+channel. Состояние отдельного диалога не выключает весь агент и не считается системной аварией.

`events`: безопасный непрозрачный `id`, подтверждённые `agent_id/agent_name` (или null), `channel`, `category` (`error|human|policy|info`), `status`, `reason_code`, `occurred_at`, `checkpoints`, `dialog`, `action`. Не приписывать событие агенту по совпадению имени или contact/JID. При смене coalescing job история использует run receipts; run/draft/sent evidence теперь связаны с тем же agent_id, не с соседним агентом того же события.

`dialog` либо null, либо `{provider,channel_id,lead_id}`. Переход создаётся только по существующему маршруту своего tenant, для Connect — совпадение channel+conversation, для Kommo — существующая сделка и её права. Получатель перехода снова применяет обычную авторизацию истории. Номер клиента/JID и тело сообщения не нужны для ссылки. Недоступные Kommo события не передаются оператору с отсутствующим deal scope.

`technical` появляется **только** на `/state/technical`: tenant ID, валидный UUID deployment, heartbeat, известные boolean flags, числовые метрики очереди, allowlisted reason codes. Нет API-ключей, environment dump, provider URL, тела сообщений, prompts, персональных данных или произвольных исключений. Копирование берёт только этот объект и время генерации, не DOM/весь snapshot.

## UI для Terra

Новые самостоятельные ассеты `/assets/tenant-ai-state.js` и `.css` зарегистрированы. Существующие общие UI-файлы не изменены. Компонент не вставляет кнопки и не выполняет fetch до mount:

```javascript
const panel = window.tenantAIState.mount(host, {
  profile: currentMember,
  language: 'ru', // ru | az | en
  onOpenDialog: route => window.tenantOperator.openDialog(route)
});
// panel.refresh(); panel.setLanguage('az'); panel.destroy();
```

Terra заменяет две точки «Диагностика»/«Статус AI» одной «Состояние AI» и подключает компонент в выбранном месте. Убрать старое автоматическое создание кнопки из `tenant-ai-reliability.js` при замене точки входа; старые API остаются. Саму существующую общую CRM-диагностику не удалять без отдельного решения.

После `tenant-profile` компонент уничтожается и отбрасывает незавершённые ответы. Интегратор повторно монтирует его для новой подтверждённой identity. Язык менять через `setLanguage`. Технические данные загружаются только при раскрытии блока. Автоматического polling, загрузки медиа или запусков AI нет. RU/AZ/EN, безопасный `textContent`, адаптивные карточки и кнопки >=44px включены.

## Границы релиза

Legacy quarantine и канальные ключи не меняются; нет миграции, Resume, массового сброса Human Mode или удаления записей. Нет новых сервисов/интеграций, изменений настройки Nizam Ai/второго агента, WhatsApp-сессий, `/webapp` или production. Каталог предназначен для выбора, но не подменяет серверную валидацию `save_agent`/runtime.

Нужна интеграция UI Terra + мастера Sol в общей ветке. Production-проверку после будущего согласованного релиза проводить на собственном тестовом контакте; этот пакет не выполняет отправок.

## Проверки 2026-10-17

- Полный Python-набор: **1571 passed, 4 skipped, 240 subtests passed**, 316.58 s. Использован настоящий psycopg (предварительно импортирован, чтобы старые unit-тесты не заменили его заглушкой), `--runxfail` и отдельная localhost PostgreSQL на порту 55441 для многопроцессных гонок.
- В полном наборе — 9 реальных PostgreSQL concurrency cases: leases, повторные webhook, гонки отправки/takeover и cold startup. Провайдеры замоканы.
- Новый контракт/права/состояние: **22 теста**, включая отсутствие изменений данных при чтении, provider namespace, два канала, tenant/deal isolation, чужой agent run/draft, Human/Pause/Resume, Test Mode и карантин.
- Все **57 JavaScript-наборов** прошли. После уточнения пользовательских формулировок новый UI-набор повторно прошёл.
- 4 пропуска — прежние Windows asyncio loopback проверки; это не пропуски новых SQL/channel/permission тестов.
- `git diff --check` чист; ассеты/entrypoints `/webapp` не изменены. Новые миграции отсутствуют.
- Live-проверка WhatsApp не выполнялась. Визуальный preview Chrome не подтверждён: Browser Use вернул CDP timeout; RU/AZ/EN, DOM, XSS, lazy technical loading, копирование и смена tenant проверены в JSDOM. Финальное размещение и мобильный просмотр общего интерфейса — на интеграции Terra.
