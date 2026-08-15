"""What a run does.

Two jobs, and the split between them is the guarantee the pipeline rests on.

`ingest` brings listings in: read a Telegram feed into `source_messages`, turn what
is stored into `listings`, queue the matches. `drain` sends what is queued, and
notices plan expiries while it is there.

They are separate because their failure modes are. Ingest depends on one host, one
Telegram session and somebody else's message format; delivery depends on Telegram
accepting a send. A broken source must not stop delivery of what already matched,
and a rate limit must not stop new listings being recorded.

Nothing here fetches a web page. The scraper this project began as is gone: the feed
supplies the listings, and a second feed is another reader rather than another
extraction engine.
"""

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

Row = dict[str, Any]
Conn = psycopg.Connection[Row]

# The feed. A row in `sources`, so it can be disabled without a deploy, and the
# default here rather than a constant elsewhere because every job takes one.
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
    """Execute one job. Returns the run status."""
    if job == "drain":
        # A new subscription is seeded here for the same reason plan expiries are
        # noticed here: this job runs on a short interval whether or not anything was
        # ingested, so somebody who just finished the wizard gets their first
        # listings within minutes rather than at the next read.
        seed_new_subscriptions(conn, run, dry_run=cfg.dry_run)
        notify_plan_changes(conn, run, dry_run=cfg.dry_run)
        return drain(conn, run, suppress=suppress_delivery, dry_run=cfg.dry_run)

    if job == "ingest":
        # Imported here, not at module scope, so that `drain` needs neither the
        # reader nor its optional Telethon dependency. A host that only delivers
        # should be able to run without an MTProto client installed.
        from worker.ingest.parse import run_parse
        from worker.ingest.reader import collect

        source = source_key or DEFAULT_SOURCE
        asyncio.run(collect(conn, run, source_key=source, dry_run=cfg.dry_run))
        listing_ids = run_parse(conn, run, source_key=source, dry_run=cfg.dry_run)
        if listing_ids:
            queue_matches(conn, run, source_key=source, listing_ids=listing_ids)
        # Delivery is deliberately not called here. `drain` is its own job so that a
        # failed send can be retried without reading anything again, and so quiet
        # hours can hold messages for a later tick to release.
        return "ok"

    run.event("error", f"unknown job {job!r}")
    return "failed"


__all__ = ["DEFAULT_SOURCE", "run_job"]
