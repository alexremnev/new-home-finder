"""Worker entry point.

    python -m worker tick                        what the scheduler calls
    python -m worker hot   [--source] [--districts]  run now, ignoring the schedule
    python -m worker sweep [--source]
    python -m worker drain
    python -m worker schedules                   show the schedule table

`tick` is the only command a scheduler needs. It keeps frequency in the database
rather than in a cron expression, so intervals are per source and changing one is
an update rather than a commit. Add --dry-run to any job to avoid writes outside
the run log.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from worker.config import Config, ConfigError
from worker.db import claim_schedule, connect, fetch_due_schedules, finish_schedule
from worker.db import advisory_lock as db_advisory_lock
from worker.obs import Run
from worker.pipeline.run import run_job

Row = dict[str, Any]
JOBS = ("hot", "sweep", "drain")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="worker")
    parser.add_argument("command", choices=(*JOBS, "tick", "schedules"))
    parser.add_argument("--source", help="limit to one source key")
    parser.add_argument("--job", help="with tick: consider only this job")
    parser.add_argument(
        "--districts",
        help="narrow this run to these postcode districts, e.g. SE16 or SE16,E14. "
        "Affects one run only; it cannot reach outside what source_locations enables",
    )
    parser.add_argument("--dry-run", action="store_true", help="no writes outside the run log")
    parser.add_argument(
        "--trigger", default="manual", choices=("schedule", "manual", "retry"),
        help="recorded on the run for auditing",
    )
    args = parser.parse_args(argv)

    try:
        cfg = Config.load(dry_run=args.dry_run)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    if args.command == "schedules":
        return _show_schedules(cfg)
    if args.command == "tick":
        return _tick(cfg, only_job=args.job)

    districts = (
        frozenset(d.strip().upper() for d in args.districts.split(",") if d.strip())
        if args.districts
        else None
    )
    return _run_one(
        cfg,
        job=args.command,
        source_key=args.source,
        trigger=args.trigger,
        districts=districts,
    )


# ── tick ──────────────────────────────────────────────────────────────────


def _tick(cfg: Config, *, only_job: str | None) -> int:
    with connect(cfg.database_url) as conn:
        due = fetch_due_schedules(conn, job=only_job)
        if not due:
            # The common case. Kept cheap on purpose: a frequent tick costs
            # almost nothing, and fine granularity is what makes any interval
            # in the database achievable.
            print('{"level":"debug","msg":"nothing due"}')
            return 0

        worst = 0
        for schedule in due:
            if not claim_schedule(conn, schedule):
                print('{"level":"debug","msg":"schedule claimed by another tick"}')
                continue
            worst = max(worst, _execute(cfg, conn, schedule))
        return worst


def _execute(cfg: Config, conn: Any, schedule: Row) -> int:
    job = schedule["job"]
    source_key = schedule["source_key"]
    suppress, quiet_reason = _quiet_state(schedule["quiet_hours"])

    if suppress == "pause":
        Run(conn, job=job, trigger="schedule", run_url=cfg.run_url).finish("skipped_locked")
        print(f'{{"level":"info","msg":"quiet hours: {job} paused ({quiet_reason})"}}')
        finish_schedule(conn, schedule["id"], "ok")
        return 0

    lock_key = f"job:{job}:{source_key or '*'}"
    with db_advisory_lock(cfg.database_url, lock_key) as acquired:
        run = Run(
            conn, job=job, trigger="schedule", run_url=cfg.run_url, dry_run=cfg.dry_run
        )
        if not acquired:
            run.event("warn", f"another run holds {lock_key}")
            run.finish("skipped_locked")
            return 0
        status = _dispatch(conn, run, job, source_key, cfg, suppress == "queue")
        finish_schedule(conn, schedule["id"], status)
        return 0 if status == "ok" else 1


# ── one-off ───────────────────────────────────────────────────────────────


def _run_one(
    cfg: Config,
    *,
    job: str,
    source_key: str | None,
    trigger: str,
    districts: frozenset[str] | None = None,
) -> int:
    lock_key = f"job:{job}:{source_key or '*'}"
    with connect(cfg.database_url) as conn, db_advisory_lock(cfg.database_url, lock_key) as ok:
        run = Run(conn, job=job, trigger=trigger, run_url=cfg.run_url, dry_run=cfg.dry_run)
        if not ok:
            run.event("warn", f"another run holds {lock_key}")
            run.finish("skipped_locked")
            return 0
        status = _dispatch(
            conn, run, job, source_key, cfg, suppress_delivery=False, districts=districts
        )
        return 0 if status == "ok" else 1


def _dispatch(
    conn: Any,
    run: Run,
    job: str,
    source_key: str | None,
    cfg: Config,
    suppress_delivery: bool,
    districts: frozenset[str] | None = None,
) -> str:
    try:
        status = run_job(
            conn, run, job=job, source_key=source_key, cfg=cfg,
            suppress_delivery=suppress_delivery, districts=districts,
        )
    except Exception as exc:  # noqa: BLE001 - the run record must survive any failure
        run.finish("failed", error=f"{type(exc).__name__}: {exc}")
        raise
    run.finish(status)
    return status


# ── quiet hours ───────────────────────────────────────────────────────────


def _quiet_state(quiet: dict[str, Any] | None) -> tuple[str | None, str]:
    """Return ('pause' | 'queue' | None, reason).

    'queue' keeps collecting but defers delivery until the window closes, which
    is the behaviour wanted for overnight hours: listings are not lost, and
    nobody is messaged at 03:00.
    """
    if not quiet or "from" not in quiet or "to" not in quiet:
        return None, ""
    tz = ZoneInfo(quiet.get("tz", "Europe/London"))
    now = datetime.now(tz).time()
    start = _parse_time(quiet["from"])
    end = _parse_time(quiet["to"])
    inside = start <= now or now < end if start > end else start <= now < end
    if not inside:
        return None, ""
    mode = quiet.get("mode", "queue")
    return ("pause" if mode == "pause" else "queue"), f"{quiet['from']}–{quiet['to']} {tz}"


def _parse_time(value: str) -> Any:
    hour, _, minute = value.partition(":")
    return datetime.min.replace(hour=int(hour), minute=int(minute or 0)).time()


# ── inspection ────────────────────────────────────────────────────────────


def _show_schedules(cfg: Config) -> int:
    with connect(cfg.database_url) as conn:
        rows = conn.execute(
            """
            SELECT job, coalesce(source_key, '*') AS source, enabled,
                   interval_seconds, jitter_pct,
                   to_char(next_run_at, 'YYYY-MM-DD HH24:MI:SS') AS next_run,
                   coalesce(last_status, '-') AS last_status, consecutive_fails
              FROM schedules ORDER BY job, source
            """
        ).fetchall()
    if not rows:
        print("no schedules; apply db/migrations/0001_init.sql")
        return 1
    header = f"{'job':7} {'source':10} {'on':3} {'every':>7} {'jit':>4}  {'next run':19} {'last':9} {'fails':>5}"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['job']:7} {r['source']:10} {'yes' if r['enabled'] else 'no':3} "
            f"{r['interval_seconds']:>7} {r['jitter_pct']:>3}%  {r['next_run']:19} "
            f"{r['last_status']:9} {r['consecutive_fails']:>5}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
