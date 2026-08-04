# new-home-finder

Cross-portal London rental monitor. A background worker collects listings,
matches them against saved search criteria, and delivers alerts — currently to
Telegram, with the channel abstracted so others can be added without touching
the pipeline.

Full design: `london-rent-alerts-spec.md`.

## Status

Early scaffold. Implemented so far:

| Component | State |
|---|---|
| Reachability probe (`scripts/probe.py`, `.github/workflows/probe.yml`) | ready to run |
| Database schema (`db/migrations/0001_init.sql`) | ready to apply |
| Stage contracts (`worker/contracts/`) | complete |
| Fetch client, extraction engine, pipeline, notifiers, web app | not started |

## First step: run the probe

The probe answers the question that gates everything else — whether the target
site responds to the machine the worker will run on, and which client profile it
accepts. Run it before writing any pipeline code.

On a GitHub Actions runner (the intended POC host):

```
Actions → probe → Run workflow
```

Locally, for comparison:

```bash
pip install curl-cffi requests
python scripts/probe.py
```

Read the `detail` row first: that is the page the extractor parses. A local 200
paired with a runner 403 means the runner IP is the problem rather than the
client, which changes where the worker should be hosted — see the execution
portability section of the spec.

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                   # install dependencies
cp .env.example .env                      # then fill in the values
psql "$DATABASE_URL" -f db/migrations/0001_init.sql
```

The repository is public. Secrets belong in `.env` locally and in GitHub Secrets
in CI; never in code, and never in a log line.

## Operating the worker

Scheduling lives in the database, not in cron. The cron trigger only ticks; the
`schedules` table decides what is due, per job and per source. Changing a
frequency is an update, with no commit and no deploy:

```sql
UPDATE schedules SET interval_seconds = 300
 WHERE job = 'hot' AND source_key = 'openrent';
```

Request pacing within a run is separate, and also per source:

```sql
UPDATE sources
   SET config = jsonb_set(config, '{rate_limit_rps}', '0.15')
 WHERE key = 'openrent';
```

## Layout

```
scripts/probe.py            reachability probe
db/migrations/              schema
worker/contracts/           data passed between stages; imports nothing else
.github/workflows/          probe (scrape and keepalive to follow)
```
