# Railway staging для AI Agents

Эта инструкция создаёт отдельный staging-проект Railway из того же GitHub-репозитория и отдельную PostgreSQL. Она не подключает Kommo webhook и не авторизуется в Kommo. В текущем рабочем дереве изменения локальные; Railway увидит их только после помещения проверенной версии в выбранную GitHub-ветку. Корневой production-сервис и его переменные не трогать.

## Что запускает Railway сейчас

Корневые файлы репозитория — конфигурация, которую Railway получает при выборе корня репозитория:

- `railway.toml` задаёт `bash start.sh` и перезапуск сервиса. Приложение публикует `/health`; задайте этот путь в staging service Settings.
- `start.sh` переходит в `beintaskbot-main/` и запускает `python3 bot.py`.
- Корневой `requirements.txt` — список Python-зависимостей сборки; в нём добавлены `cryptography` и `pypdf`, необходимые текущим backend-модулям.
- `bot.py` читает Railway `PORT` и поднимает HTTP-сервер. Отдельный Railway worker-сервис не нужен: AI Agents worker запускается как фоновая задача внутри HTTP-сервиса.

В staging `TELEGRAM_POLLING_ENABLED=false` пропускает создание Telegram Application, вызовы `deleteWebhook`/`getUpdates` и Telegram job queue. HTTP-сервер и AI Agents worker продолжают работать. Для входа в `/app` всё ещё нужен отдельный staging bot token: Telegram WebApp init-data проверяется этим токеном. Откройте staging Mini App через тестового Telegram-бота; настройте URL Mini App в BotFather. Команды `/start` и Telegram-обработчики при polling off отвечать не будут.

`STAGING_MODE=true` также:

- запрещает запуск с непустым глобальным `KOMMO_TOKEN`;
- отключает legacy GitHub JSON storage, который иначе мог бы импортировать `balance.json`/`kpi.json` из production-репозитория;
- очищает глобальный `KOMMO_BASE_URL` и не регистрирует широкий legacy `/webhook/kommo`;
- требует staging-origin вместо `https://production.example.invalid`;
- принудительно считает `AI_AGENTS_AUTO_SEND_ENABLED` и `AI_AGENTS_CRM_ENABLED` выключенными даже при ошибочно выставленном `true`.

Tenant Kommo OAuth — отдельный путь: только stage credentials из staging Railway Variables и только callback на том же staging HTTPS host. Backend отклоняет callback на production host. Пока OAuth не настроен и тестовый чат не подтверждён, соединение и обработка остаются неактивны.

## Переменные Railway

Задайте переменные только в staging app service. Не копируйте production Variables целиком.

| Переменная | Значение в staging | Источник и ограничение |
|---|---|---|
| `STAGING_MODE` | `true` | Обязательно; включает staging-защиты в коде. |
| `TELEGRAM_POLLING_ENABLED` | `false` | Не вызывает `deleteWebhook` и не забирает Telegram updates. |
| `TELEGRAM_TOKEN` | токен отдельного тестового Telegram-бота | Нужен для проверки WebApp init-data. Production bot token не копировать. |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | Ссылка на PostgreSQL service, созданный в staging-проекте. Не вводить production URL. Имя `Postgres` замените на точное имя нового DB service. |
| `TENANT_ENCRYPTION_KEY` | отдельный Fernet key | Не использовать production key: им шифруются OAuth и Chat AI credentials в staging DB. Генерация: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Вставить результат непосредственно в Railway Variables, не в чат или Git. |
| `CANONICAL_WEB_ORIGIN` | `https://<staging-host>` | Origin отдельного Railway домена или staging custom domain; обязательно HTTPS. `https://production.example.invalid` здесь отклоняется. |
| `OPENAI_API_KEY` | отдельный ограниченный staging key | Требуется при импорте legacy приложения. Агент использует tenant Chat AI credentials из staging DB, но общая точка входа всё ещё требует эту переменную. Не копировать production key; задать budget/rate limits у провайдера. |
| `OPENAI_MODEL` | можно оставить текущий `gpt-4o-mini-2024-07-18` | Только название модели, не секрет; необязательная настройка legacy Chat AI. AI Agents берёт модель из tenant Chat AI settings. |
| `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY` | отдельная staging пара | Нужны существующей конфигурации приложения. В текущем статическом PWA public VAPID key зашит в `docs/index.html`, поэтому staging push не проверять: отдельная пара не совпадёт с этим клиентским значением. Push для AI Agents не требуется. |
| `KOMMO_OAUTH_CLIENT_ID`, `KOMMO_OAUTH_CLIENT_SECRET` | отдельные OAuth client credentials | Не обязательны для первого boot, но нужны для подключения tenant к Kommo. Не брать значения production. Их callback регистрировать только на staging host после отдельного согласования. |
| `KOMMO_OAUTH_REDIRECT_URI` | `https://<staging-host>/api/platform/integrations/kommo/callback` | Указать такой же точный HTTPS callback в отдельной Kommo OAuth-конфигурации. При пустой переменной backend строит этот path от `CANONICAL_WEB_ORIGIN`; явный production callback будет отвергнут. |
| `AI_AGENTS_ENABLED` | `true` | Включает tenant AI API и фоновый worker. В `STAGING_MODE` schema setup выполняется до создания worker и при ошибке БД останавливает старт сервера. |
| `AI_AGENTS_TEST_WEBHOOK_ENABLED` | `false` | В первой сборке staging endpoint не регистрируется. Включать только после отдельного разрешения и проверки точной Test Mode binding. |
| `AI_AGENTS_AUTO_SEND_ENABLED` | `false` | Не включать. Дополнительно принудительно заблокировано в `STAGING_MODE`. |
| `AI_AGENTS_CRM_ENABLED` | `false` | Не включать. Дополнительно принудительно заблокировано в `STAGING_MODE`. |
| `GH_TOKEN` | не задавать | В staging `STAGING_MODE` запрещает чтение и запись legacy GitHub data branch. Stage JSON documents хранятся только в stage PostgreSQL. |
| `KOMMO_TOKEN` | не задавать | В staging непустое значение вызывает отказ при запуске. Нельзя подставлять глобальный production access token. |

`PORT` задаёт Railway. Не задавайте вручную `PORT`, `WEBHOOK_PORT`, `KOMMO_DOMAIN` или `KOMMO_BASE_URL`. `OPENAI_API_BASE` и `WHATSAPP_*` из старых примеров текущий путь AI Agents не использует: не задавайте их и не создавайте вторую WhatsApp-интеграцию. `LINEAR_API_KEY` оставьте пустым; Telegram job queue при polling off не стартует. `AI_AGENTS_ENABLED=false` остановит API и worker, но потребует нового Railway deployment; для немедленной остановки после привязки используйте аварийный stop в Test Mode. Пока Test Mode не привязан, события не допускаются к очереди.

## Создание сервиса в Railway UI

1. Откройте Railway и создайте **новый проект** с названием вроде `beintaskbot-staging`. Не добавляйте staging service или database в production project/environment, чтобы не унаследовать shared variables и ссылки на production DB.
2. В новом проекте выберите **New → GitHub Repo** и тот же GitHub-репозиторий. Выберите отдельную staging-ветку, содержащую проверенные изменения. Не выбирайте production branch, если она не содержит этих изменений. Установите Root Directory на корень репозитория — там находятся `railway.toml`, `start.sh`, `nixpacks.toml` и `requirements.txt`.
3. Через **New → Database → PostgreSQL** создайте отдельную staging БД. Дождитесь её готовности.
4. В app service откройте **Variables** и добавьте `DATABASE_URL` как Railway Reference Variable `${{Postgres.DATABASE_URL}}`, заменив `Postgres` на точное название созданного DB service. Проверьте источник ссылки перед сохранением.
5. Создайте staging hostname во вкладке **Networking** (Generate Domain) и внесите его origin в `CANONICAL_WEB_ORIGIN`. Установите все переменные из таблицы; секреты вводить только в Railway UI. В начале оставьте Kommo OAuth credentials пустыми, если staging OAuth client ещё не подготовлен.
6. Убедитесь, что значения включают `STAGING_MODE=true`, `TELEGRAM_POLLING_ENABLED=false`, `AI_AGENTS_ENABLED=true`, `AI_AGENTS_TEST_WEBHOOK_ENABLED=false`, `AI_AGENTS_AUTO_SEND_ENABLED=false`, `AI_AGENTS_CRM_ENABLED=false`. Не задавайте `KOMMO_TOKEN`/`GH_TOKEN` и не подключайте никакой webhook.
7. В app service Settings задайте Healthcheck Path `/health` и timeout `300` секунд. Не задавайте Pre-Deploy Command. В `STAGING_MODE` startup валидирует Fernet key, применяет tenant + AI Agents + Chat AI schema к `DATABASE_URL`, и только затем запускает worker. Новая схема повторно применима. Если migration или соединение с БД завершается ошибкой, HTTP app не начинает обслуживать трафик и `/health` не станет healthy. Вне staging путь worker сохраняет прежнюю lazy-migration схему.
8. До первого deployment сверяйте в Variables, что все ссылки относятся к ресурсам staging-проекта. Первый deployment запускайте только после отдельного подтверждения владельца. После него проверьте Railway Logs и `https://<staging-host>/health`; пока не добавляйте Kommo webhook.

## Останов и подготовка к ручному тесту

- Перед установкой Test Mode binding очередь закрыта отсутствующей/неподтверждённой привязкой; `AI_AGENTS_ENABLED=true` само по себе не разрешает ни один разговор.
- После привязки используйте Test Mode emergency stop как живой выключатель очереди и внешних AI Agent действий. Для полного отключения API/worker установите `AI_AGENTS_ENABLED=false` и выполните restart/deploy staging service.
- Не устанавливайте `AI_AGENTS_TEST_WEBHOOK_ENABLED=true` и не создавайте подписку Kommo на этом этапе. После проверки staging и отдельного одобрения адресом будет `https://<staging-host>/webhook/kommo/ai-agents-test`; действующий production webhook не редактировать.
- Для ручного теста позже потребуются: подтверждённые IDs ровно одного тестового Kommo-разговора (account, tenant, company channel, contact, `talk_id`, `chat_id`, deal), отдельный Kommo OAuth client/callback и Chat AI provider credential, сохранённый в staging tenant. Не отправляйте секреты в чат.

## Что ещё нужно предоставить владельцу staging

1. Railway доступ, позволяющий создать новый project, app service и PostgreSQL, либо самостоятельное создание этих ресурсов по инструкции.
2. GitHub staging branch/commit после ревью локальных изменений. В этой сессии код не отправлялся в GitHub и ни один Railway deployment не запускался.
3. Токен нового Telegram staging bot и выбранный staging hostname. BotFather должен указывать Mini App на этот hostname.
4. После отдельного разрешения на Kommo OAuth — client ID/secret, зарегистрированный staging callback, без изменения production redirect URI. Достаточно внести значения в Railway; пересылать их в чат не нужно.

Реальная staging PostgreSQL проверка остаётся отдельным действием после создания stage DB. До этого используйте только локальный disposable сценарий из `tests/postgres/README.md`, который принимает только loopback URL с именем `ai_agents_test_*`.

Test Mode allowlist (staging only): AI_AGENTS_TEST_CLIENT_PHONE and AI_AGENTS_TEST_COMPANY_PHONE. Store the verified pair only in Railway Variables for the staging service. Leave both blank until the test dialogue is confirmed; either missing value keeps Test Mode blocked. Never place these values in Git or production variables.
