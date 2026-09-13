from __future__ import annotations

import asyncio
from typing import Any

import psycopg

from worker.config import Config
from worker.obs import Run
from worker.pipeline.outbox import (
    drain,
    notify_plan_changes,
    queue_matches,
    seed_new_subscriptions,
)
from worker.pipeline.rollup import run_rollup

Row = dict[str, Any]
Conn = psycopg.Connection[Row]

DEFAULT_SOURCE = "tg_feed"

def run_job(
    conn: Conn,
    run: Run,
    *,
    job: str,
    source_key: str | None = None,
    cfg: Config,
    suppress_delivery: bool = False,
    districts: frozenset[str] | None = None,
) -> str:

    if job == "rollup":
        run_rollup(conn, run, dry_run=cfg.dry_run)
        return "ok"

    if job == "drain":

        seed_new_subscriptions(conn, run, dry_run=cfg.dry_run)
        notify_plan_changes(conn, run, dry_run=cfg.dry_run)
        return drain(conn, run, suppress=suppress_delivery, dry_run=cfg.dry_run)

    if job == "ingest":

        from worker.ingest.parse import run_parse
        from worker.ingest.reader import collect

        source = source_key or DEFAULT_SOURCE
        asyncio.run(collect(conn, run, source_key=source, dry_run=cfg.dry_run))
        listing_ids = run_parse(conn, run, source_key=source, dry_run=cfg.dry_run)
        if listing_ids:
            queue_matches(conn, run, source_key=source, listing_ids=listing_ids)

        return "ok"

    run.event("error", f"unknown job {job!r}")
    return "failed"

__all__ = ["DEFAULT_SOURCE", "run_job"]
