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
uv run python -m worker login                  # войти аккаунтом, напечатать TG_SESSION
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

Окружение собирается с обоими экстра. `uv sync` **синхронизирует**, а не докладывает:
`uv sync --extra ingest` в одиночку уберёт `dev`, и `pytest` перестанет находиться.

```bash
uv sync --extra ingest --extra dev             # один раз, и после каждой правки зависимостей
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

`ingest` и `scrape` только складывают совпадения в очередь — отправляет их `drain`,
отдельным прогоном раз в две минуты. Поэтому `drain` полезен сам по себе: повторить
доставку, не перечитывая источники.

```sql
-- очередь и её состояние
SELECT status, count(*) FROM notifications GROUP BY status;

-- сколько придержала доля бесплатного тарифа и кому
SELECT user_id, count(*) FROM notifications
 WHERE status = 'skipped' AND error = 'share'
   AND created_at > now() - interval '24 hours'
 GROUP BY 1 ORDER BY 2 DESC;

-- кому уже отправлена дневная сводка (одна строка на человека в день)
SELECT user_id, day, withheld, sent_at FROM daily_digests ORDER BY sent_at DESC LIMIT 20;

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
-- (предупреждение одно, за час; стадия `day` в старых строках — то, что
-- рассылалось раньше)
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
schtasks /Run   /TN "home ingest"
schtasks /Query /TN "home ingest" /V /FO LIST
```

Три задачи: `ingest` каждые 5 минут, `drain` тоже каждые 5 со сдвигом на 2 минуты,
`report` раз в час. Сдвиг нужен, чтобы пачка уходила в том же цикле, в котором
попала в очередь, а не ждала следующего.

Интервал живёт в самой задаче. Таблицы расписаний больше нет — два таймера на одно
решение это один лишний, и лишним был тот, которого никто не спрашивал.

Логи: `logs\ingest-ГГГГ-ММ-ДД.log` и `logs\drain-ГГГГ-ММ-ДД.log`.

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
-- сдвиг срока сам делает предупреждение за час снова актуальным.
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

### Ссылка на оплату

Кнопка «Get full access» в листинге и ссылка из `/pay` — это одна и та же
страница `{SITE}/upgrade?t=<token>`. Диплинка `t.me/<bot>?start=pay` больше нет:
она отправляла боту `/start pay`, тот отвечал прайсом и ссылкой на эту же
страницу — три шага, чтобы сказать одно и то же дважды.

Токен на аккаунт один (`purpose = 'upgrade'`), живёт 36 часов и при каждой выдаче
продлевается, а не перевыпускается — `issueToken` в `lib/plans.ts` и
`upgrade_token` в `worker/store.py` делают это с двух сторон через `greatest`.
Поэтому новое сообщение не убивает кнопку в предыдущем, а воркер и сайт не
отбирают ссылку друг у друга. Если токен всё же умер — `/pay` выдаёт следующий.

```sql
-- живые ссылки на оплату и сколько им осталось
SELECT user_id, purpose, expires_at - now() AS ttl, used_at
  FROM user_tokens WHERE purpose = 'upgrade' ORDER BY expires_at DESC;
```

### Как доля называется в сообщениях

Вечерняя сводка говорит настоящее число отправленных — оно считается в
`daily_digests` (колонка `sent`), а не выводится из доли, иначе на единственной
строке о том, чего человек не получил, стояла бы почти верная цифра:
«🔒 12 new listings today — you are seeing only 2 of them».

Везде, где числа нет (план истёк, плашка на листинге, прайс на сайте) — процент
как он лежит в `plans.delivery_share`: «20% of what matches», остаток — «80%».
Одна и та же доля называется одинаково в боте и на сайте; это была форма
`1 in 5`, она читается проще, но доля называется ещё и в прайсе, и одна
формулировка везде лучше лучшей формулировки в одном месте.

### Две доли в `user_entitlement`

`delivery_share` — что доставляется **сейчас**: своя доля плана, пока он жив,
иначе доля по каналу. `lapsed_share` (миграция 0058) — что будет, **когда план
кончится**, независимо от того, кончился он уже или нет: 20 на телеграме, 0 на
ватсапе.

Предупреждение за час уходит, пока план ещё живёт, поэтому ему нужна вторая
колонка. С первой живой триал сообщал о себе «доля 100%», из чего текст заключал
«падать некуда» и писал `After that the alerts stop` — ровно обратное тому, ради
чего 0046 поставил бесплатный тариф на пятую часть листингов.

Предупреждение одно. За сутки («ends tomorrow») было и удалено: два сообщения об
одном и том же окончании — на одно больше, чем кто-либо просил, а час — то, что
приходит, когда решение уже перед человеком. `claim_plan_notices` больше не
выдаёт стадию `day`; в `plan_notices_stage_ck` она оставлена, потому что уже
отправленные строки — запись того, что отправлялось.

```sql
SELECT user_id, plan, live, delivery_share, lapsed_share
  FROM user_entitlement ORDER BY plan_until;
```

Ссылка на оплату в этих сообщениях — кнопка (`Action(url=...)`), а не строка с
url: в телеграме inline-кнопка, в ватсапе — его единственная `cta_url`, пока
открыто окно. В тексте url не дублируется; если ссылки нет вовсе, в тексте
остаётся `/pay`.

## Команды бота

```
/start         ссылка на форму — единственный способ задать фильтр
/current       текущий фильтр и план
/update        ссылка на форму — открывается с текущими фильтрами
/pay           тарифы и оплата
/stop          удалить фильтр и прекратить отправку
/grant <ref> <plan> <days>   только из TELEGRAM_ADMIN_CHAT
```

Любая ссылка на сайт, которую бот даёт известному ему человеку, несёт токен
`?e=` (`filterUrl` в `lib/plans.ts`). По нему страница узнаёт, кто пришёл:
форма открывается на его текущих критериях, а не на дефолтах, и вместо прайса
показывает «сохранить и вернуться». Токен живёт час и продлевается при каждом
сообщении бота — ссылка из прошлого сообщения не умирает от нового. Обратное
преобразование (критерии → положения ползунков) лежит в `lib/prefill.ts`, рядом
с границами самих контролов.

## Доступность источников и фикстуры


Проба сообщает две независимые вещи: отдаёт ли сайт страницу этой машине и
разрешает ли `robots.txt` её запрашивать. Запрещённый путь не запрашивается, а
помечается пропущенным.

```bash
# Сохранённая страница -> фикстура для коммита. Сохраняет структуру, классы,
# иконки и форматы значений; убирает прозу, фотографии и ссылки.
    -o tests/fixtures/openrent_e14_search.html
```

`snapshots/` в `.gitignore` — сырые страницы остаются на той машине, где скачаны.
Коммитятся только урезанные фикстуры.

## Частота и охват — это данные, не код

Без деплоя и без коммита. Выполнять в Supabase → SQL Editor.

Как часто обращаемся к источнику — это systemd-таймеры, не база: `OnUnitInactiveSec`
в `deploy/systemd/london-home-finder-<job>.timer`. Таблицы `schedules` больше нет,
её удалила миграция 0021.

```sql
-- как быстро внутри одного прогона (запросов в секунду)
UPDATE sources SET config = jsonb_set(config, '{rate_limit_rps}', '0.15')
 WHERE key = 'openrent';

-- приостановить источник: таймер, а не база
--   sudo systemctl disable --now london-home-finder-openrent.timer

-- читать источник, но никому не отправлять (миграция 0060).
-- `enabled` — читаем ли мы его вообще; `announces` — доходит ли найденное
-- до подписчика. Это разные вопросы, и tg-фид — первый источник, которому
-- нужны разные ответы: подписка на него кончается, и единственный способ
-- узнать, покрывают ли скраперы то же самое, — продолжать его читать,
-- ничего при этом не рассылая. Сравнение лежит в listing_sightings.
UPDATE sources SET announces = false WHERE key = 'tg_feed';
UPDATE sources SET announces = true  WHERE key = 'tg_feed';   -- обратно

-- кто сейчас молчит
SELECT key, enabled, announces, health FROM sources ORDER BY key;

-- догоняет ли скрапер фид: `caught_up` — объявления, которые уже лежали
-- в базе от другого ридера, и до которых этот добрался только сейчас.
-- Это ровно те алерты, которые раньше отправлял фид.
SELECT r.job, s.source_key, s.counters->>'caught_up' AS caught_up,
       s.counters->>'new' AS new, s.counters->>'announced' AS announced,
       s.finished_at
  FROM job_stages s JOIN job_runs r ON r.id = s.run_id
 WHERE s.stage = 'scrape' AND s.finished_at > now() - interval '6 hours'
 ORDER BY s.finished_at DESC;

-- расширить охват
UPDATE source_locations SET enabled = true
 WHERE source_key = 'openrent'
   AND location_id IN (SELECT id FROM locations WHERE code IN ('E1', 'SE1'));
```

## Как работают скраперы — семь запросов по порядку

Выполнять в Supabase → SQL Editor. Только читают, ничего не меняют.
Период там, где он есть, задаётся одним `interval` внутри запроса.

Запросы идут в том порядке, в котором двигаются данные, и каждый объясняет
свой участок. Если читать их сверху вниз, получается устройство целиком:
от того, какие районы вообще запрашиваются, до того, что дошло до людей.

```sql
-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  КАК РАБОТАЮТ СКРАПЕРЫ — семь запросов в том порядке, в котором идут     ║
-- ║  данные. Каждый только читает, ничего не меняет.                        ║
-- ╚══════════════════════════════════════════════════════════════════════════╝
--
-- Путь одного объявления, целиком:
--
--   subscriptions            кто-то выбрал район E14
--        ↓                   → только выбранные районы вообще запрашиваются
--   source_sweeps            с какого момента следим за E14 на этом портале
--        ↓                   → всё, что было до этого момента, не новость
--   job_runs / job_stages    прогон: хост, статус, трафик, счётчики
--        ↓
--   listings                 сама квартира (UNIQUE source_key + external_id)
--        ↓
--   listing_sightings        кто её увидел и когда — фид или скрапер
--        ↓
--   listings.duplicate_of    та же квартира с другого портала гасится
--        ↓
--   notifications            что реально ушло людям (UNIQUE user_id + listing_id)


-- ═════════════════ 1. Что вообще запрашивается ═══════════════════════════
--
-- Скраперы читают НЕ весь Лондон. Список районов берётся из живых подписок:
-- район, который никто не выбрал, не стоит ни одного запроса. Поэтому если
-- объявлений нет — первым делом смотреть сюда, а не в логи.
--
-- Тот же запрос, что worker выполняет перед каждым обходом.
SELECT upper(area) AS district,
       count(DISTINCT s.user_id) AS subscribers
  FROM subscriptions s
  CROSS JOIN LATERAL jsonb_array_elements_text(
      coalesce(s.criteria->'areas'->'postcode_districts', '[]'::jsonb)
  ) AS area
 WHERE s.active
 GROUP BY 1
 ORDER BY 2 DESC, 1;


-- ═════════════════ 2. Состояние наблюдения по районам ════════════════════
--
-- Две даты на район, и они отвечают на РАЗНЫЕ вопросы. Это центральная идея
-- всей конструкции:
--
--   settled_at — с какого момента мы следим. Не двигается. Решает, что
--                считать новостью: объявление, появившееся раньше этой даты,
--                не анонсируется никогда. Именно поэтому первый взгляд на
--                район молчит — иначе новый подписчик получил бы весь
--                стоячий рынок сразу.
--
--   swept_at   — когда читали в последний раз. Двигается каждый прогон.
--                Решает, насколько глубоко листать. Пока это было одной
--                колонкой, район, отслеживаемый с августа, пролистывал
--                август заново на каждом прогоне.
--
-- Что смотреть: «ещё не анонсирует» — нормально для района, добавленного
-- минуту назад, и ненормально для района, который в списке неделю.
-- «Не читался давно» при работающем таймере означает, что район не попал в
-- бюджет прогона (25 районов за раз) или портал отказывает.
SELECT sw.source_key,
       sw.district,
       to_char(sw.settled_at AT TIME ZONE 'Europe/London', 'DD Mon HH24:MI')
           AS watching_since,
       to_char(sw.swept_at   AT TIME ZONE 'Europe/London', 'DD Mon HH24:MI')
           AS last_read,
       CASE WHEN sw.swept_at IS NULL THEN 'ни разу'
            ELSE age(now(), sw.swept_at)::text END          AS read_ago,
       -- Сколько объявлений этого района уже лежит от этого источника.
       (SELECT count(*) FROM listings l
         WHERE l.source_key = sw.source_key
           AND upper(l.postcode_district) = sw.district)     AS listings_stored
  FROM source_sweeps sw
 ORDER BY sw.source_key, sw.district;


-- ═════════════════ 3. Прогоны: где, чем кончились, сколько мегабайт ══════
--
-- job_runs — один прогон задачи. job_stages — стадия внутри него; у портальных
-- читателей стадия называется 'scrape' и несёт source_key, и ВСЕ счётчики
-- трафика лежат там, а не в прогоне.
--
-- Что значат счётчики:
--   districts  сколько районов взято в этот прогон (бюджет — 25)
--   seen       сколько объявлений портал показал всего
--   new        из них незнакомых нам
--   stored     сколько строк реально записано (new минус копии)
--   sent       сколько поставлено в очередь на отправку — и вот это главное:
--              stored без sent означает район, который ещё дочитывается
--   mb         ровно то, что прошло по сети в сжатом виде (счётчик libcurl),
--              а не размер распакованных страниц — разница десятикратная
--   mb_proxy   сколько из этого ушло через резидентский прокси: эту цифру
--              сверять со счётом DataImpulse
--   refused    портал отказал; смотреть address_refused рядом
--   part_read  район недочитан до конца (сработал предел на число страниц) —
--              такой район НЕ отмечается прочитанным, иначе пропущенное
--              ушло бы как новое на следующем прогоне
--
-- Период — в `interval` ниже.
SELECT coalesce(r.host, '(до 0044)')                        AS host,
       r.job,
       to_char(r.started_at AT TIME ZONE 'Europe/London', 'DD Mon HH24:MI:SS')
           AS started_london,
       r.trigger,
       r.status,
       round(extract(epoch FROM r.finished_at - r.started_at))::int AS secs,
       round((s.counters->>'bytes')::numeric / 1048576, 2)  AS mb,
       round((s.counters->>'proxy_bytes')::numeric / 1048576, 2) AS mb_proxy,
       (s.counters->>'districts')::int                      AS districts,
       (s.counters->>'seen')::int                           AS seen,
       (s.counters->>'new')::int                            AS new,
       (s.counters->>'stored')::int                         AS stored,
       (s.counters->>'announced')::int                       AS sent,
       nullif((s.counters->>'duplicate')::int, 0)           AS duplicates,
       nullif((s.counters->>'refused')::int, 0)             AS refused,
       nullif((s.counters->>'district_partial')::int, 0)    AS part_read,
       s.counters->'refused_this_address'                   AS address_refused,
       -- Прогон может кончиться degraded и не записать r.error — причина тогда
       -- только в job_events. Без второго LATERAL вы видели бы «degraded» без
       -- объяснения.
       coalesce(nullif(left(r.error, 200), ''), e.message)  AS error
  FROM job_runs r
  LEFT JOIN LATERAL (
      SELECT js.counters FROM job_stages js
       WHERE js.run_id = r.id AND js.stage = 'scrape'
         AND (r.job = 'portals' OR js.source_key = r.job)
       ORDER BY js.started_at LIMIT 1
  ) AS s ON true
  LEFT JOIN LATERAL (
      SELECT je.message FROM job_events je
       WHERE je.run_id = r.id AND je.level IN ('error', 'warn')
       ORDER BY je.id DESC LIMIT 1
  ) AS e ON true
 WHERE r.started_at > now() - interval '24 hours'
   AND r.job IN ('rightmove', 'zoopla', 'zoopla_london', 'openrent', 'portals')
 ORDER BY r.started_at DESC;


-- ═════════════════ 4. Сводка по машинам и читателям ══════════════════════
--
-- Rightmove отдаёт серверу напрямую. Zoopla отвечает адресу сервера 403, а
-- OpenRent — 405, поэтому они либо стоят в планировщике на Windows, либо идут
-- через резидентский прокси. Здесь видно, что где крутится и во что обходится.
SELECT coalesce(r.host, '(до 0044)')                        AS host,
       r.job,
       count(*)                                              AS runs,
       count(*) FILTER (WHERE r.status = 'ok')               AS ok,
       count(*) FILTER (WHERE r.status = 'degraded')         AS degraded,
       count(*) FILTER (WHERE r.status = 'failed')           AS failed,
       count(*) FILTER (WHERE r.status = 'skipped_locked')   AS skipped,
       round(100.0 * count(*) FILTER (WHERE r.status = 'ok')
                   / nullif(count(*), 0))                    AS ok_pct,
       round(sum((s.counters->>'bytes')::numeric) / 1048576, 1)       AS mb_total,
       round(sum((s.counters->>'proxy_bytes')::numeric) / 1048576, 1) AS mb_proxy,
       round(avg((s.counters->>'bytes')::numeric) / 1048576, 2)       AS mb_per_run,
       sum((s.counters->>'stored')::int)                     AS stored,
       sum((s.counters->>'announced')::int)                  AS sent,
       to_char(max(r.started_at) AT TIME ZONE 'Europe/London', 'DD Mon HH24:MI')
           AS last_run
  FROM job_runs r
  LEFT JOIN LATERAL (
      SELECT js.counters FROM job_stages js
       WHERE js.run_id = r.id AND js.stage = 'scrape'
         AND (r.job = 'portals' OR js.source_key = r.job)
       ORDER BY js.started_at LIMIT 1
  ) AS s ON true
 WHERE r.started_at > now() - interval '24 hours'
   AND r.job IN ('rightmove', 'zoopla', 'zoopla_london', 'openrent', 'portals')
 GROUP BY 1, 2
 ORDER BY 1, 2;


-- ═════════════════ 5. Жизнь одного объявления ════════════════════════════
--
-- Самый наглядный запрос: берёт последние 30 объявлений и показывает, кто их
-- увидел, кто первым, копия ли это и ушло ли кому-нибудь.
--
-- Тонкость, без которой ничего не понять: для Rightmove и Zoopla фид и скрапер
-- пишут в ОДНУ строку. У listings есть UNIQUE (source_key, external_id), а
-- Telegram-фид и скрапер извлекают один и тот же id — фид из ссылки
-- rightmove.co.uk/properties/93625473, скрапер из поля id на странице поиска.
-- Вставка идёт ON CONFLICT DO UPDATE, поэтому второй, кто пришёл, ничего не
-- создаёт. Отсюда же следует, что двойного уведомления быть не может.
--
-- Именно поэтому существует listing_sightings: строка не помнит, кто её нашёл.
SELECT l.id,
       l.source_key,
       l.postcode_district                                  AS district,
       l.price_pcm,
       l.bedrooms,
       to_char(l.first_seen_at AT TIME ZONE 'Europe/London', 'DD Mon HH24:MI')
           AS first_seen,
       -- Кто её видел, в порядке появления.
       (SELECT string_agg(g.reader || ' ' ||
                to_char(g.first_at AT TIME ZONE 'Europe/London', 'HH24:MI:SS'),
                ' → ' ORDER BY g.first_at)
          FROM listing_sightings g WHERE g.listing_id = l.id)  AS who_saw_it,
       -- Копия с другого портала? Тогда никому не отправляется.
       l.duplicate_of,
       CASE WHEN l.image_url IS NULL THEN 'нет' ELSE 'есть' END AS photo,
       (SELECT count(*) FROM notifications n WHERE n.listing_id = l.id)
           AS queued_for,
       (SELECT count(*) FROM notifications n
         WHERE n.listing_id = l.id AND n.status = 'sent')    AS actually_sent
  FROM listings l
 ORDER BY l.first_seen_at DESC
 LIMIT 30;


-- ═════════════════ 6. Фид против скраперов ═══════════════════════════════
--
-- Можно ли выключить Telegram-источник. Группировка по id объявления НА
-- ПОРТАЛЕ, а не по строке в базе: у Rightmove и Zoopla это одна строка, а у
-- фид и скрапер одного портала пишут в одну строку, поэтому сама строка не
-- говорит, кто увидел объявление первым — это знает только listing_sightings.
--
-- Решает дело колонка missed_in_our_districts. «Только фид» само по себе
-- ничего не значит: объявление в районе, который никто не выбрал, скрапер не
-- смотрит по замыслу. Промах — это то, что фид нашёл, а скрапер нет, в районе,
-- который скрапер читает. Устойчивый ноль там — основание выключать фид.
--
-- Задним числом ничего не восстановлено: до появления listing_sightings никто
-- не записывал, кто увидел объявление, поэтому период раньше начала учёта
-- покажет ноль не потому, что промахов не было.
WITH covered AS (
    SELECT DISTINCT upper(area) AS code
      FROM subscriptions s
      CROSS JOIN LATERAL jsonb_array_elements_text(
          coalesce(s.criteria->'areas'->'postcode_districts', '[]'::jsonb)
      ) AS area
     WHERE s.active
),
fam AS (
    SELECT l.id, l.external_id,
           l.source_key                                     AS portal,
           upper(l.postcode_district)                       AS district
      FROM listings l
     WHERE l.first_seen_at > now() - interval '24 hours'
),
saw AS (
    SELECT f.portal, f.external_id,
           bool_or(f.district IN (SELECT code FROM covered)) AS in_covered,
           min(g.first_at) FILTER (WHERE g.reader =  'tg_feed') AS feed_at,
           min(g.first_at) FILTER (WHERE g.reader <> 'tg_feed') AS scraper_at
      FROM fam f
      LEFT JOIN listing_sightings g ON g.listing_id = f.id
     GROUP BY f.portal, f.external_id
)
SELECT portal,
       count(*)                                              AS listings,
       count(*) FILTER (WHERE feed_at IS NOT NULL AND scraper_at IS NOT NULL)
           AS both_saw,
       count(*) FILTER (WHERE feed_at IS NOT NULL AND scraper_at IS NULL)
           AS feed_only,
       count(*) FILTER (WHERE feed_at IS NOT NULL AND scraper_at IS NULL
                          AND in_covered)                    AS missed_in_our_districts,
       count(*) FILTER (WHERE feed_at IS NULL AND scraper_at IS NOT NULL)
           AS scraper_only,
       count(*) FILTER (WHERE scraper_at < feed_at)          AS scraper_was_first,
       count(*) FILTER (WHERE feed_at < scraper_at)           AS feed_was_first,
       -- Положительное значение — скрапер позже. Читатель, который находит
       -- всё, но на пять минут позже, для уведомлений заменой не является.
       round(percentile_cont(0.5) WITHIN GROUP (
           ORDER BY extract(epoch FROM scraper_at - feed_at)
       ))::int                                               AS median_lead_secs
  FROM saw
 WHERE feed_at IS NOT NULL OR scraper_at IS NOT NULL
 GROUP BY portal
 ORDER BY portal;


-- ═════════════════ 7. Что дошло до людей, и от кого ══════════════════════
--
-- Конец пути. notifications имеет UNIQUE (user_id, listing_id) и вставку с
-- ON CONFLICT DO NOTHING — это и есть гарантия, что одна квартира не уйдёт
-- человеку дважды, даже если её нашли и фид, и скрапер.
--
-- Копии между порталами сюда не попадают вовсе: listings_for_matching
-- отсекает duplicate_of IS NOT NULL ещё до постановки в очередь.
SELECT l.source_key,
       n.channel,
       n.status,
       count(*)                                              AS notifications,
       count(DISTINCT n.user_id)                             AS people,
       count(DISTINCT n.listing_id)                          AS listings,
       to_char(max(n.sent_at) AT TIME ZONE 'Europe/London', 'DD Mon HH24:MI')
           AS last_sent
  FROM notifications n
  JOIN listings l ON l.id = n.listing_id
 WHERE n.created_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3;
```

## Чистка (`purge`)

Единственная джоба, которая **удаляет** строки. Таймер один раз в сутки, в 01:00
(`deploy/systemd/london-home-finder-purge.timer`). Вся политика — в одном месте,
в `RULES` в `worker/pipeline/purge.py`; менять срок хранения значит менять там
константу, а не запрос.

```bash
uv run python -m worker purge --dry-run   # сколько бы удалилось, ничего не удаляя
uv run python -m worker purge             # удалить
```

| Что | Срок | Почему так |
| --- | --- | --- |
| `users` со статусом `pending` | до конца дня регистрации | заполнил форму, в мессенджер не нажал. Не тронет того, у кого подтверждён канал, есть платёж, есть отправленный алерт или платный тариф |
| `listings` | 60 дней **по `last_seen_at`** | не по дате появления: `listings` — это и есть память дедупа у скраперов, и удаление объявления, которое ещё висит на портале, разошлёт его всем второй раз как новое |
| `notifications` | 60 дней, кроме текущего оплаченного периода | исключение по `plan_from` — иначе обнулился бы лимит алертов в WhatsApp |
| `source_messages` | 60 дней по `stored_at` | самый тяжёлый текст в базе |
| `site_visits` | 60 дней | суточные итоги остаются в `daily_stats.visitors` |
| `job_runs` (+ `job_stages`, `job_events` каскадом) | 30 дней | самая быстрорастущая таблица: ~1400 прогонов в сутки |

Не чистится никогда: `payments` и `admin_actions` (деньги и аудит),
`daily_stats` / `district_days` / `daily_digests` (ради них и можно удалять
сырьё), `source_seen_ids` и `source_sweeps` (это состояние, а не история —
удалённый вотермарк означает перечитывание района с нуля).

Удаляет пачками по 2000 строк с потолком 100 000 на правило за прогон: первый
прогон на непочищенной базе разложится на несколько ночей и не упадёт в
statement timeout. Границы считаются в запросе от полуночи по Лондону, так что
часовой пояс сервера решает только когда работать, а не что считать вчерашним.

```sql
-- что джоба удалила за последние ночи
SELECT started_at::date AS night, counters
  FROM job_runs WHERE job = 'purge' ORDER BY id DESC LIMIT 7;
```

## Осмотр базы

```sql
-- сколько чего лежит
SELECT 'sources' AS t, count(*) FROM sources
UNION ALL SELECT 'locations',        count(*) FROM locations
UNION ALL SELECT 'source_locations', count(*) FROM source_locations
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
`0002_rightmove.sql`, `0003_rls.sql`, и так до последней — на сегодня
`0058_lapsed_share.sql`. Затем:

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
| `failed to resolve host 'db.<ref>.supabase.co'` | прямой хост Supabase отдаёт только IPv6. Работает там, где IPv6 есть, и перестаёт, когда его не стало — без единого изменения у нас. Нужен **session pooler** |
| `password authentication failed` | пароль не закодирован; `@ # / ? :` в пароле нужно кодировать процентами |
| `permission denied for table ...` | подключение не под владельцем таблиц; нужна строка подключения, а не anon-ключ |
| джоба не запускалась сама | расписание держит systemd: `systemctl list-timers "london-home-finder*"` |
| статус прогона `skipped_locked` | другой прогон держит advisory lock по этой задаче и источнику |
| статус прогона `degraded` | стадия сообщила о проблеме, либо стадии ещё заглушки |
| источник пропущен, `health=blocked` | размыкатель в паузе; срок в `sources.health_until` |
| источник пропущен, `health=broken` | схема извлечения сломалась и не починилась; пометка «снято» намеренно приостановлена |

## Строка подключения к Supabase

Берите **session pooler**, а не прямой хост и не transaction pooler:

```
postgresql://postgres.<ref>:<пароль>@aws-0-<регион>.pooler.supabase.com:5432/postgres
```

Supabase → Project Settings → Database → Connection string → **Session pooler**.
Скопируйте строку целиком: регион в имени хоста угадать нельзя.

Три варианта, и разница между ними не косметическая:

| Что | Порт | Годится |
|---|---|---|
| `db.<ref>.supabase.co` | 5432 | нет — только IPv6, отваливается без предупреждения |
| `...pooler.supabase.com` **session** | **5432** | **да** |
| `...pooler.supabase.com` transaction | 6543 | нет для воркера — см. ниже |

Transaction pooler ломает `advisory_lock`. Воркер берёт **сессионную** блокировку
`pg_try_advisory_lock`, чтобы два прогона не наложились; через transaction pooler
она берётся на том бэкенде, который обслужил запрос, и отпускается в момент,
которым никто не управляет. Блокировка отвечает «взял» и не охраняет ничего —
это хуже, чем её отсутствие.

Для вебхука на Vercel transaction pooler как раз правильный: там нет сессионных
блокировок, а соединений много и они короткие. То есть у воркера и у веба
`DATABASE_URL` может и должен различаться портом.

## Оповещения о поломках

Два независимых канала, и это не дублирование — они ловят разное.

**`scripts/report.py`** ищет проблемы **в базе**: тишину в фиде, рост
`unparseable`, неразгребаемую очередь. Ловит то, что сломалось *внутри*
работающей системы.

**`scripts/win/run-job.cmd`** шлёт в ops-чат при любом ненулевом коде выхода,
через `curl`, не касаясь базы. Ловит то, из-за чего не работает сам воркер:
недоступная база, отозванная сессия Telegram, сломанный venv.

Второй появился потому, что первый в такой ситуации молчит по той же причине,
по которой всё остальное не работает: **тревога о хранилище не может жить в
хранилище**. Один день простоя не был виден нигде, кроме кода возврата, за
которым никто не следил.

Одно сообщение на джобу в час — задача бежит каждые 5 минут, и без ограничения
за вечер пришло бы пятьдесят одинаковых. Метка лежит в `%TEMP%` и в её имени
записан час, поэтому арифметики с датами нет и чистить нечего.

Проверить, что работает — сломайте нарочно:

```cmd
:: временно испортить строку подключения
scripts\win\run-job.cmd drain
```

Должно прийти сообщение с последними строками ошибки. Второй запуск в тот же час
ничего не пришлёт — так и задумано. Сбросить ограничение:

```cmd
del %TEMP%\home-alert-*.flag
```

## Цены в Stripe

`plans.stripe_price_id` — это **идентификатор** Price, который генерирует Stripe, а
не сумма. Выглядит как `price_1QxYzAbCdEfGhIjKlMnOpQrS`; придумать нельзя, только
скопировать.

Dashboard → Product catalogue → **+ Add product**:

1. Название, например `1 week of alerts`
2. **One-off**, не Recurring — продаём фиксированный период, который покупают
   заново. Подписка Stripe положила бы график продлений в два места, их и наше.
3. Сумма, валюта GBP
4. Сохранить → в блоке **Pricing** у строки с ценой своя кнопка копирования
   (или ⋯ → Copy price ID) → оттуда `price_1QxYz…`

**Не `prod_…`** — это идентификатор продукта, а не цены. Продукт это папка, цена
это то, что в ней лежит и что продаётся. Дашборд показывает id продукта на более
заметном месте, поэтому его и копируют. Checkout на него ответит «No such price»;
у нас теперь проверка формы, которая скажет это словами.

```sql
UPDATE plans SET stripe_price_id = 'price_…' WHERE key = 'week';
UPDATE plans SET stripe_price_id = 'price_…' WHERE key = 'month';
```

Без этого страница честно скажет «not set up for card payment yet» — не отдаст
ошибку Stripe, которая винит покупателя.

### Две ловушки

**Test и live — разные идентификаторы.** Цена из тестового режима в живом не
существует. При переходе на живой меняются оба `stripe_price_id`, плюс
`STRIPE_SECRET_KEY`, плюс вебхук заводится заново. Забыть половину легко.

**Сумма в Stripe и `price_pence` никем не сверяются.** Страница показывает нашу
колонку, списывает Stripe по своей цене. Разойдутся — человеку покажут £5 и спишут
£10. Сверить один раз:

```sql
SELECT key, price_pence / 100.0 AS "показываем £", stripe_price_id
  FROM plans WHERE stripe_price_id IS NOT NULL;
```
