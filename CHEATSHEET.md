# Памятка

Всё, что нужно в повседневной работе. Обоснование решений — в
`london-rent-alerts-spec.md`, здесь только команды.

## Воркер

Две джобы, и разделены они не для порядка: у них разные причины падать. `ingest`
зависит от одного хоста, одной сессии Telegram и чужого формата сообщений; `drain`
— только от того, принимает ли Telegram отправку. Сломанный источник не должен
останавливать доставку того, что уже подошло.

```bash
uv run python -m worker ingest                 # прочитать фид, разобрать, сматчить
uv run python -m worker ingest --dry-run       # только обвязка: без сети и без TG_*
uv run python -m worker drain                  # отправить очередь notifications
uv run python -m worker drain --dry-run        # сколько ждёт, не отправляя
uv run python -m worker tick                   # то, что вызывает планировщик
uv run python -m worker schedules              # таблица расписаний
```

Для `ingest` нужны `uv sync --extra ingest` (Telethon) и переменные `TG_*` из
`.env.example`. **`TG_READER` обязателен и уникален на аккаунт** — по нему ключуются
курсор в `ingest_cursors` и уникальность по читателю, так что два хоста с одним
именем будут пропускать прочитанное друг другом.

```sql
-- дошло ли сырьё и разбирается ли оно
SELECT status, count(*) FROM source_messages GROUP BY status;

-- на чём спотыкается парсер (растущий счётчик = формат сменился)
SELECT parse_error, count(*) FROM source_messages
 WHERE status = 'unparseable' GROUP BY 1 ORDER BY 2 DESC LIMIT 10;

-- где остановился каждый читатель
SELECT reader, source_key, last_external_id, updated_at FROM ingest_cursors;

-- перепрогнать разбор после правки парсера: сообщения никуда не делись
UPDATE source_messages SET status = 'new', parse_error = NULL WHERE status = 'unparseable';
```

Скрапинга больше нет. Объявления приходят из фида и пишутся под порталом, который
их хостит (`rightmove`, `zoopla`), поэтому `UNIQUE (source_key, external_id)`
по-прежнему отсекает одно и то же объявление, пришедшее дважды.

## Тесты

```bash
uv run pytest -q                               # все
uv run pytest tests/test_normalize.py -v       # один файл, подробно
uv run pytest -q -k robots                     # по имени
uv run ruff check .                            # линтер
uv run mypy worker                             # типы
```

`tests/test_outbox_db.py` требует настоящий Postgres и без него пропускается —
там проверяется то, чего у заглушки быть не может: уникальный индекс, граница
суток, `ON CONFLICT`, поведение LEFT JOIN. **Базу он затирает**, поэтому только
одноразовый контейнер, никогда не Supabase.

```bash
docker run -d --name pg -e POSTGRES_PASSWORD=x -p 5433:5432 postgres:16-alpine
TEST_DATABASE_URL=postgresql://postgres:x@localhost:5433/postgres uv run pytest tests/test_outbox_db.py -q
docker rm -f pg
```

## Уведомления

Бот: `@BotFather` → `/newbot` → имя и username (обязательно кончается на `bot`).
Токен из ответа — в `.env` как `TELEGRAM_TOKEN`. Свой chat id: написать боту
что-нибудь и открыть `https://api.telegram.org/bot<ТОКЕН>/getUpdates`, взять
`chat.id`.

```bash
uv run python -m worker drain                  # отправить очередь
uv run python -m worker drain --dry-run        # посмотреть сколько ждёт, не отправляя
```

`hot` отправляет сам, в том же прогоне — ждать отдельного `drain` не нужно. `drain`
как отдельная джоба существует для двух случаев: повторить доставку без повторного
скрапинга и выпустить очередь, задержанную тихими часами.

```sql
-- очередь и её состояние
SELECT status, count(*) FROM notifications GROUP BY status;

-- почему не ушло
SELECT id, user_id, attempts, error FROM notifications
 WHERE status IN ('queued','failed') ORDER BY created_at DESC LIMIT 20;
```

Состояния строки: `queued` (ждёт), `sent`, `failed` (насовсем — либо ошибка
неповторяемая, либо кончились 5 попыток), `skipped` (получатель недоступен,
остаток его очереди брошен).

## Веб-часть (apps/web)

```bash
cd apps/web
npm install
npm run dev          # http://localhost:3000
npm test             # парсеры формы, команд бота и визарда
npm run typecheck
```

Регистрация вебхука — только после деплоя, Telegram ходит по HTTPS:

```bash
SECRET=$(openssl rand -hex 24)     # он же в TELEGRAM_WEBHOOK_SECRET
curl -s "https://api.telegram.org/bot$TELEGRAM_TOKEN/setWebhook" \
  -d "url=https://<приложение>.vercel.app/api/tg/webhook" \
  -d "secret_token=$SECRET" \
  -d 'allowed_updates=["message","callback_query"]'
curl -s "https://api.telegram.org/bot$TELEGRAM_TOKEN/getWebhookInfo"
```

**`callback_query` в `allowed_updates` обязателен.** Без него Telegram выбрасывает
нажатия кнопок до того, как они дойдут до маршрута: визард отрисуется, кнопки
будут видны, а тап не сделает ничего — и ни в логах приложения, ни в
`getWebhookInfo` про это не будет ни слова. Если визард «не реагирует», проверять
надо это в первую очередь: `getWebhookInfo` должен показывать оба типа.

`secret_token` — вся защита маршрута: URL не секрет, он попадает в логи. Бот
держит ровно один вебхук, поэтому кто поставил последним — тот и владеет.

Меню бота (кнопка ⌘ рядом с полем ввода) ставится из самого бота: отправьте
`/menu` с чата, указанного в `TELEGRAM_ADMIN_CHAT`. Список берётся из `BOT_MENU` в
`apps/web/lib/commands.ts`, то есть из того же файла, что и парсер — меню не может
предложить команду, которой бот не понимает. Повторять безопасно.

```sql
-- незавершённые визарды: начали настройку и ушли
SELECT chat_id, step, updated_at, expires_at FROM wizard_sessions ORDER BY updated_at DESC;

-- что уже сказано про планы (по одной строке на стадию и срок)
SELECT user_id, stage, plan_until, sent_at FROM plan_notices ORDER BY sent_at DESC LIMIT 20;

-- у кого план кончается в ближайшие сутки и предупреждён ли он
SELECT u.id, u.plan, u.plan_until,
       array_agg(n.stage ORDER BY n.stage) FILTER (WHERE n.plan_until = u.plan_until) AS told
  FROM users u LEFT JOIN plan_notices n ON n.user_id = u.id
 WHERE u.plan_until BETWEEN now() AND now() + interval '1 day'
 GROUP BY u.id, u.plan, u.plan_until;
```

```sql
-- кто подписан и на что
SELECT u.id, u.status, s.label, s.backfill_from
  FROM users u LEFT JOIN subscriptions s ON s.user_id = u.id ORDER BY u.id;

-- зависшие pending: форму заполнили, но Start в боте не нажали
SELECT id, created_at, token_expires_at FROM users
 WHERE status = 'pending' AND token_expires_at < now();
```

## Запуск по расписанию (Windows)

```cmd
scripts\win\install-task.cmd          :: один раз, из админской консоли
schtasks /Run   /TN "LondonRentAlerts worker"
schtasks /Query /TN "LondonRentAlerts worker" /V /FO LIST
```

Задача вызывает `worker tick` каждые 10 минут. Что именно и как часто выполняется
— решает таблица `schedules`, поэтому окно 10:00–19:00 и часовой интервал меняются
`UPDATE`, а не правкой задачи. Если ничего не пора — тик стоит один запрос.

Логи: `D:\projects\new-home-finder\logs\worker-ГГГГ-ММ-ДД.log`.

## Планы и оплата

Лимиты — строки в `plans`, не код. Меняются без деплоя.

```sql
SELECT * FROM plans;

-- цена и длительность
UPDATE plans SET price_pence = 1500, duration_days = 30 WHERE key = 'paid';
-- сколько районов даёт бесплатный (0008 ставит 5 и там, и на платном)
UPDATE plans SET max_districts = 5 WHERE key = 'trial';
```

Выдать план вручную (оплата пришла переводом):

```sql
-- Никаких флагов сбрасывать не надо: plan_notices ключуется по plan_until, так что
-- сдвиг срока сам делает предупреждения за сутки и за час снова актуальными.
UPDATE users SET plan = 'paid',
       plan_until = greatest(coalesce(plan_until, now()), now()) + interval '14 days'
 WHERE payment_ref = 'LRA-XXXXXX';

INSERT INTO payments (user_id, plan, amount_pence, provider, granted_days, granted_by)
SELECT id, 'paid', 1000, 'bank_transfer', 14, 'manual'
  FROM users WHERE payment_ref = 'LRA-XXXXXX';
```

Или из бота, со своего chat id (`TELEGRAM_ADMIN_CHAT`): `/grant LRA-XXXXXX paid 14`.

```sql
-- кто на чём и до когда
SELECT u.id, u.plan, u.plan_until, u.payment_ref, u.status,
       (SELECT count(*) FROM subscriptions s WHERE s.user_id = u.id AND s.active) AS filters
  FROM users u ORDER BY u.id;

-- истёкшие, но ещё не уведомлённые
SELECT u.id, u.plan_until FROM users u
 WHERE u.plan_until < now()
   AND NOT EXISTS (SELECT 1 FROM plan_notices n
                    WHERE n.user_id = u.id AND n.stage = 'expired'
                      AND n.plan_until = u.plan_until);

-- выручка
SELECT provider, count(*), sum(amount_pence)/100.0 AS pounds FROM payments GROUP BY provider;
```

Истёкший план перестаёт отправлять сам: условие стоит в запросе матчера, а не в
отдельной джобе. Фильтр при этом сохраняется — продление включает алерты обратно.

## Команды бота

```
/show          текущий фильтр и план
/filter        ссылка на форму (30 минут, одноразовая)
/price 1500-2200 · /price 2000 · /price any
/beds 1-2
/areas SE16, SE8
/pets on · /bills on · /direct on
/upgrade       тарифы и ссылка на оплату
/stop          удалить фильтр и прекратить отправку
/grant <ref> <plan> <days>   только из TELEGRAM_ADMIN_CHAT
```

## Доступность источников и фикстуры


Проба сообщает две независимые вещи: отдаёт ли сайт страницу этой машине и
разрешает ли `robots.txt` её запрашивать. Запрещённый путь не запрашивается, а
помечается пропущенным.

```bash
# Сохранённая страница -> фикстура для коммита. Сохраняет структуру, классы,
# иконки и форматы значений; убирает прозу, фотографии и ссылки.
    -o tests/fixtures/openrent/detail.html
```

`snapshots/` в `.gitignore` — сырые страницы остаются на той машине, где скачаны.
Коммитятся только урезанные фикстуры.

## Частота и охват — это данные, не код

Без деплоя и без коммита. Выполнять в Supabase → SQL Editor.

```sql
-- как часто обращаемся к источнику
UPDATE schedules SET interval_seconds = 300
 WHERE job = 'hot' AND source_key = 'openrent';

-- как быстро внутри одного прогона (запросов в секунду)
UPDATE sources SET config = jsonb_set(config, '{rate_limit_rps}', '0.15')
 WHERE key = 'openrent';

-- приостановить источник, сохранив настройки
UPDATE schedules SET enabled = false WHERE source_key = 'rightmove';

-- расширить охват
UPDATE source_locations SET enabled = true
 WHERE source_key = 'openrent'
   AND location_id IN (SELECT id FROM locations WHERE code IN ('E1', 'SE1'));
```

## Осмотр базы

```sql
-- сколько чего лежит
SELECT 'sources' AS t, count(*) FROM sources
UNION ALL SELECT 'locations',        count(*) FROM locations
UNION ALL SELECT 'source_locations', count(*) FROM source_locations
UNION ALL SELECT 'schedules',        count(*) FROM schedules
UNION ALL SELECT 'listings',         count(*) FROM listings
UNION ALL SELECT 'notifications',    count(*) FROM notifications
UNION ALL SELECT 'job_runs',         count(*) FROM job_runs;

-- текущий охват
SELECT sl.source_key, l.code, l.tfl_zone_min, l.tfl_zone_max
  FROM source_locations sl JOIN locations l ON l.id = sl.location_id
 WHERE sl.enabled ORDER BY sl.source_key, l.code;

-- последние прогоны
SELECT id, job, trigger, status,
       to_char(started_at, 'YYYY-MM-DD HH24:MI') AS started,
       round(extract(epoch FROM finished_at - started_at)::numeric, 1) AS seconds,
       counters
  FROM job_runs ORDER BY id DESC LIMIT 10;

-- что происходило в последнем прогоне
SELECT stage, source_key, status, counters FROM job_stages
 WHERE run_id = (SELECT max(id) FROM job_runs) ORDER BY id;

SELECT level, stage, source_key, message, ctx FROM job_events
 WHERE run_id = (SELECT max(id) FROM job_runs) ORDER BY ts;

-- проблемы по всем прогонам
SELECT ts, level, source_key, message FROM job_events
 WHERE level IN ('warn', 'error') ORDER BY ts DESC LIMIT 20;

-- состояние источников и какая схема извлечения действует
SELECT s.key, s.health, s.health_note, p.version, p.strategy, p.created_by
  FROM sources s
  LEFT JOIN parse_schemas p
    ON p.source_key = s.key AND p.status IN ('active', 'pinned')
 ORDER BY s.key;

-- свежие объявления (пусто, пока стадии пайплайна не реализованы)
SELECT source_key, external_id, price_pcm, bedrooms, postcode_district,
       available_from, first_seen_at
  FROM listings WHERE status = 'active'
 ORDER BY first_seen_at DESC LIMIT 20;
```

## Настройка на новой машине

```bash
git clone <repo> && cd new-home-finder
uv sync --extra dev
cp .env.example .env          # затем вписать в .env строку подключения
uv run pytest -q
```

Миграции по порядку, через Supabase → SQL Editor: `db/migrations/0001_init.sql`,
`0002_rightmove.sql`, `0003_rls.sql`. Затем:

```bash
uv run python scripts/seed_locations.py --enable SE16,SE8,E14
```

## Секреты

`.env.example` — шаблон, лежит в git, значения всегда пустые. `.env` — настоящие
значения, в git не попадает. В CI значения приходят из GitHub Secrets, а не из
файла.

Значение из `.env` применяется только если переменная ещё не задана в окружении —
поэтому устаревший файл не может подменить рабочий секрет.

Если секрет всё же попал в коммит — меняйте его. Удалить строку недостаточно:
история git постоянна, и значение уже покинуло машину.

## Разбор неполадок

| Симптом | Причина |
|---|---|
| `DATABASE_URL is not set` | нет `.env` рядом с `pyproject.toml`, либо переменная пустая |
| `password authentication failed` | пароль не закодирован; `@ # / ? :` в пароле нужно кодировать процентами |
| `permission denied for table ...` | подключение не под владельцем таблиц; нужна строка подключения, а не anon-ключ |
| воркер пишет `nothing due` | это норма для `tick`; для принудительного запуска нужен `hot` |
| статус прогона `skipped_locked` | другой прогон держит advisory lock по этой задаче и источнику |
| статус прогона `degraded` | стадия сообщила о проблеме, либо стадии ещё заглушки |
| источник пропущен, `health=blocked` | размыкатель в паузе; срок в `sources.health_until` |
| источник пропущен, `health=broken` | схема извлечения сломалась и не починилась; пометка «снято» намеренно приостановлена |
