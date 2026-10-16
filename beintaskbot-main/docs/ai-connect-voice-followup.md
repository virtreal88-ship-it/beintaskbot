# Входящий голос WhatsApp через Connect

Реализация: feature/voice, база Beintaskbot 0cf99046.
Connect: отдельный feature/voice, база cb9ffcd09df53e78447d18a310b11ba5b8b9457a.
Работа не меняет production, Railway, настройки Nizam Ai или Pause/Resume.

## Контракт и путь сообщения

1. Evolution messages.upsert / MESSAGES_UPSERT содержит data.key.{id,remoteJid,fromMe}
   и data.message.audioMessage. Connect также распознаёт аудио внутри
   ephemeralMessage, viewOnceMessage, viewOnceMessageV2, viewOnceMessageV2Extension.
2. Существующий подписанный webhook сохраняет входящее сообщение в tenant-local
   CRM и идемпотентно ставит работу в существующий worker. Connect UUID сообщения
   служит opaque reference; URL и mediaKey из WhatsApp не передаются.
3. Worker использует действующие scope, расписание, Test Mode, enabled-флаги,
   Human/Pause и правила выбора ключа. Для платформенных агентов используется
   центральный ключ, отдельный клиентский OpenAI-ключ не нужен.
4. Beintaskbot проверяет локальные tenant/lead/channel/conversation и получает
   файл через GET /v1/channels/{channel_uuid}/messages/{message_uuid}/audio
   с conversationId и tenant-owned Connect API key. Connect независимо проверяет
   tenant principal, channel, conversation, направление, тип и срок доступа.
5. Connect обращается только к настроенному Evolution origin:
   POST /chat/getBase64FromMediaMessage/{stored_instance} с сохранённым
   message.key и convertToMp4:false. Evolution сам извлекает и расшифровывает
   файл из своего хранилища. Произвольные URL из webhook не используются.
6. Beintaskbot ограниченно загружает байты по public HTTPS/443 с pinned DNS,
   без redirect. ffmpeg декодирует аудио локально для проверки целостности и
   реальной длительности. OGG/Opus отправляется провайдеру без конвертации;
   raw AAC преобразуется в WAV. Сетевые протоколы/playlist-демультиплексоры
   в ffmpeg запрещены.
7. Существующая tenant_chat_ai_provider.transcribe выполняет распознавание.
   Язык не задаётся принудительно: сохраняется язык речи. Для платформенного
   ключа серверный AI_AGENTS_TRANSCRIPTION_MODEL по умолчанию равен
   gpt-4o-mini-transcribe. Выбор будущей модели возможен централизованно.
   Legacy company-key настройки Chat AI продолжают действовать.
8. Транскрипция сохраняется в saas_crm_messages.body, исходный message_type
   остаётся audio, в raw добавляется voice_transcription.source=whatsapp_audio.
   Серверная квитанция подтверждает происхождение транскрипции. Контекст получает
   [Audio transcript], инструкции агента, базу знаний и историю; ответ идёт
   обычным текстом через существующую финальную блокировку Connect send.

## Идемпотентность, ошибки и лимиты

- saas_connect_voice_receipts имеет уникальность tenant/channel/external_id.
  Claim фиксируется до платного запроса; конкурентный worker не повторяет его.
  При неопределённом результате, сбое процесса или timeout автоматического
  повторного распознавания нет. Для новой попытки клиент пересылает запись.
- Успешная транскрипция повторно используется из истории; raw webhook сам по
  себе не может выдать произвольный текст за серверную транскрипцию.
- Плохое аудио не создаёт текст и не переводит диалог в Human Mode.
  Ошибка текущего голосового завершает работу с безопасным диагностическим
  кодом voice_*, без отправки предположений клиенту.
- Позднейшее текстовое сообщение продолжает обрабатываться. Если новый текст
  вытеснил ожидающее голосовое из существующей очереди, worker восстанавливает
  до трёх последних необработанных audio из последних 30 сообщений контекста.
  Ошибка старого аудио оставляет маркер unavailable и не блокирует новый текст.
  Более старые/избыточные вложения явно остаются unavailable, без догадок.
- После загрузки, после распознавания и перед генерацией перепроверяются
  lease, revision, режим диалога, agent/company/Test Mode. Финальный send-lock
  сохраняет защиту от ручного перехвата во время генерации.
- Резервируется один вызов генерации плюс число pending audio (до четырёх).
  Голосовая lease пропорционально продлевается. Резерв консервативный:
  не возвращается после ошибок или takeover.
- Provider-reported аудиотокены записываются сразу, до генерации. Итоговый
  cumulative usage содержит audio + generation и обновляется идемпотентно,
  не суммируя повторные записи. Перед генерацией повторно проверяется
  центральный token cap. Модель транскрибации хранится в voice receipt.
- Размер: максимум 10 MiB; фактическая длительность: максимум 300 секунд.
  OGG/Opus, MP3, MP4/M4A, WAV, WebM, FLAC, AAC допускаются с проверкой декодера.
- Доступ через resolver истекает через 24 часа от сохранённого timestamp.
  Ответы имеют Cache-Control: no-store; endpoint ограничен 20 запросами/минуту/IP.
- Новые аудиобайты не сохраняются в БД. Временный файл удаляется после декодера.
  Транскрипция живёт ровно столько, сколько CRM-сообщение; receipt удаляется
  каскадно с сообщением/tenant. Политика хранения Evolution не изменяется.
  Аудит Connect хранит только идентификаторы, без аудио, текста и ключей.

## Проверка и безопасное развёртывание

Никакого merge, push или production deploy в рамках этой работы.

1. Проверить оба diff, выполнить тесты в изолированном окружении.
2. Сначала развернуть Connect: обратно совместимый endpoint, без Prisma-миграций.
   Проверить, что используемая версия Evolution поддерживает получение по
   сохранённому message key и сохраняет сообщения
   (DATABASE_SAVE_DATA_NEW_MESSAGE=true, уже присутствует в примере проекта).
   Использовать только собственный тестовый канал/номер.
3. Затем развернуть Beintaskbot: штатный additive ensure_schema добавляет
   voice receipts и saas_ai_agent_runs.reserved_calls. В существующем
   nixpacks уже есть ffmpeg; проверить его доступность в worker.
   Старый worker совместим с добавочными колонками/таблицей.
4. В изолированной тестовой компании проверить AZ/RU, быстрые text→voice→text,
   ошибки, duplicate webhook, ручной ответ во время распознавания и Pause.
   Test Mode сохраняет draft-only поведение.
5. После проверки отдельно согласовать production-релиз. Новые сервисы,
   изменения настроек Nizam Ai, Kommo и клиентские OpenAI-ключи не требуются.
   Rollback: сначала Beintaskbot; добавочные таблицы можно оставить.

Автотесты используют mock OpenAI/Evolution/Connect и синтетический OGG tone.
Это проверка механики и формата, а не качества распознавания AZ/RU.
В текущем окружении центральный ключ не предоставлен, настоящий запрос
распознавания и проверка deployed Evolution не выполнялись.
Полноценная PostgreSQL-интеграция также требует изолированной тестовой БД.

Известные проверки вне голосового кода: Settings.test_key_bound_to_company_and_chat_provider_only
падает также на файле базового коммита 0cf99046; Connect npm run lint
не запускается из-за отсутствия ESLint 9 flat config.

Источники контракта:
- [Evolution 2.3.7: getBase64FromMediaMessage](https://github.com/EvolutionAPI/evolution-api/blob/2.3.7/src/api/integrations/channel/whatsapp/whatsapp.baileys.service.ts)
- [OpenAI: Audio transcriptions](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create)
- [OpenAI: deprecations](https://developers.openai.com/api/docs/deprecations):
  для текущего default объявлено удаление 26 февраля 2027;
  модель перед этой датой потребуется обновить централизованно.
