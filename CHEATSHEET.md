# Памятка

Всё, что нужно в повседневной работе. Обоснование решений — в
`london-rent-alerts-spec.md`, здесь только команды.

## Воркер

```bash
uv run python -m worker schedules              # что запланировано и когда следующий запуск
uv run python -m worker hot                    # прогнать основной цикл сейчас, минуя расписание
uv run python -m worker hot --dry-run          # то же, без записи куда-либо кроме журнала прогона
uv run python -m worker hot --source openrent  # один источник
uv run python -m worker hot --districts SE16   # один район, в пределах включённого охвата
uv run python -m worker sweep                  # полный обход; только он помечает объявления снятыми
uv run python -m worker drain                  # отправить то, что стоит в очереди notifications
uv run python -m worker tick                   # то, что вызывает планировщик: только подошедшее по сроку
```

Разница между `tick` и `hot`: `tick` смотрит в `schedules` и молча выходит, если
срок не наступил — поэтому частый триггер почти ничего не стоит. `hot` запускает
принудительно.

`--districts` умеет только сужать. Район, не включённый в `source_locations`,
отклоняется с ошибкой, а не добавляется молча — иначе разовый запуск мог бы
выйти за настроенный охват.

## Тесты

```bash
uv run pytest -q                               # все
uv run pytest tests/test_normalize.py -v       # один файл, подробно
uv run pytest -q -k robots                     # по имени
uv run ruff check .                            # линтер
uv run mypy worker                             # типы
```

## Доступность источников и фикстуры

```bash
python scripts/probe.py                                   # все сайты
python scripts/probe.py --site openrent                   # один
python scripts/probe.py --site openrent --save snapshots  # сохранить страницы
python scripts/probe.py --url https://example.com/x       # один адрес, все профили клиента
```

Проба сообщает две независимые вещи: отдаёт ли сайт страницу этой машине и
разрешает ли `robots.txt` её запрашивать. Запрещённый путь не запрашивается, а
помечается пропущенным.

```bash
# Сохранённая страница -> фикстура для коммита. Сохраняет структуру, классы,
# иконки и форматы значений; убирает прозу, фотографии и ссылки.
python scripts/reduce_fixture.py snapshots/openrent/detail.plain_honest.html \
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
uv run python -m worker hot --dry-run
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
