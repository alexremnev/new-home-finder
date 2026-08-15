# new-home-finder

Cross-portal London rental monitor. A background worker collects listings,
matches them against saved search criteria, and delivers alerts — currently to
Telegram, with the channel abstracted so others can be added without touching
the pipeline.

Full design: `london-rent-alerts-spec.md`.

## Status

| Component | State |
|---|---|
| Database schema (`db/migrations/0001_init.sql`) | ready — **not yet applied** |
| Stage contracts (`worker/contracts/`) | complete |
| Run logging, scheduling, CLI (`worker/obs`, `worker/db`, `worker/__main__`) | complete |
| District scope + postcode parsing (`worker/normalize/geo.py`) | complete, tested |
| robots.txt matching (`worker/fetch/robots.py`) | complete, tested |
| Location seed (`scripts/seed_locations.py`) | complete |
| Pipeline stages | placeholders — each is filled by one plan step |
| Fetch client, extraction, notifiers, web app | not started |

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                    # install dependencies
cp .env.example .env                       # then fill in DATABASE_URL
psql "$DATABASE_URL" -f db/migrations/0001_init.sql
python scripts/seed_locations.py --enable SE16,SE8,E14
```

The repository is public. Secrets belong in `.env` locally and in GitHub Secrets
in CI; never in code, and never in a log line.

## Running the worker

There are three ways to trigger a job, and they exist for different purposes.

**Check a source first.** The probe reports two independent things per site —
whether the page is served to this machine, and whether robots.txt permits
fetching it. A disallowed path is skipped rather than fetched.


Reachability is not permission, and neither is terms of service: see the legal
section of the spec before adopting a source.

**Locally, during development.** Fastest loop, and `--dry-run` writes nothing
beyond the run log:

```bash
python -m worker schedules              # show what is due and when
```

**On a runner, manually.** This is what verifies the real network path and
environment, which a local run cannot:

```bash
gh run watch
```


### Frequency is data, not cron

The cron trigger only ticks. The `schedules` table decides what is due, per job
and per source, so a frequency change needs no commit and no deploy:

```sql
UPDATE schedules SET interval_seconds = 300
 WHERE job = 'hot' AND source_key = 'openrent';
```

Request pacing within a single run is a separate knob, also per source:

```sql
UPDATE sources
   SET config = jsonb_set(config, '{rate_limit_rps}', '0.15')
 WHERE key = 'openrent';
```

### Coverage scope is data too

`locations` holds all zone 1–3 districts as reference data. Which of them a
source actually watches is `source_locations.enabled`. The seed enables three
and leaves the rest in place but disabled.

Widen coverage without a deploy:

```sql
UPDATE source_locations SET enabled = true
 WHERE source_key = 'openrent'
   AND location_id IN (SELECT id FROM locations WHERE code IN ('E1', 'SE1', 'SE17'));
```

Check the current scope:

```sql
SELECT l.code, l.tfl_zone_min, l.tfl_zone_max, sl.enabled
  FROM source_locations sl JOIN locations l ON l.id = sl.location_id
 WHERE sl.source_key = 'openrent' AND sl.enabled
 ORDER BY l.code;
```

The scope is applied before any listing is fetched: a listing URL carries its
outward code, so out-of-scope listings are discarded during discovery rather
than downloaded and filtered afterwards. Matching is anchored, so a scope of
`E1` does not admit `E14` or `E17`. A URL whose slug carries no outward code is
kept rather than dropped, so a slug format change costs extra fetches instead of
silently losing listings — and the postcode on the page, not the URL, is treated
as authoritative once extracted.

## Layout

```
scripts/seed_locations.py   districts and coverage scope
db/migrations/              schema
worker/contracts/           data passed between stages; imports nothing else
worker/obs/                 run, stage, and event logging
worker/normalize/           units, dates, postcodes
worker/fetch/               robots.txt matching (RFC 9309)
worker/pipeline/            stage orchestration
worker/__main__.py          CLI entry point
```
