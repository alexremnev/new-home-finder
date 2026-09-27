from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from worker.config import Config, ConfigError
from worker.db import connect
from worker.db import advisory_lock as db_advisory_lock
from worker.obs import Run
from worker.pipeline.run import PORTAL_JOBS, run_job

Row = dict[str, Any]

# `scrape` is the original OpenRent reader, which discovers from the sitemap.
#
# The three portal readers are one job each, named after their source key, so
# that each has its own timer and its own row in `job_runs`. Combined into one
# job they shared a schedule — three sweeps landing on the server at the same
# minute — and shared a status, so a portal that had stopped was invisible
# behind the two that had not.
#
# `portals` runs all three in one go. Nothing schedules it; it is for a manual
# sweep.
JOBS = (
    "ingest", "scrape", "portals",
    *PORTAL_JOBS,
    "drain", "rollup",
)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="worker")
    parser.add_argument("command", choices=(*JOBS, "login"))
    parser.add_argument("--source", help="limit to one source key")
    parser.add_argument(
        "--districts",
        help="narrow this run to these postcode districts, e.g. SE16 or SE16,E14. "
        "Affects one run only; it cannot reach outside what source_locations enables",
    )
    parser.add_argument("--dry-run", action="store_true", help="no writes outside the run log")
    parser.add_argument(
        "--save",
        help="with login: write TG_SESSION straight into this env file instead of "
        "printing it, and read TG_API_ID/TG_API_HASH from it",
    )
    parser.add_argument(
        "--trigger", default="manual", choices=("schedule", "manual", "retry"),
        help="recorded on the run for auditing",
    )
    args = parser.parse_args(argv)

    if args.command == "login":

        from worker.ingest.reader import login

        return asyncio.run(login(args.save))

    try:
        cfg = Config.load(dry_run=args.dry_run)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

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
    except Exception as exc:
        run.finish("failed", error=f"{type(exc).__name__}: {exc}")
        raise
    run.finish(status)
    return status

if __name__ == "__main__":
    sys.exit(main())
