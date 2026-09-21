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

        from worker.ingest.parse import fill_images, run_parse
        from worker.ingest.reader import collect

        source = source_key or DEFAULT_SOURCE
        asyncio.run(collect(conn, run, source_key=source, dry_run=cfg.dry_run))
        listing_ids = run_parse(conn, run, source_key=source, dry_run=cfg.dry_run)

        # Before queueing, so a listing about to be sent already has its picture.
        if not cfg.dry_run:
            fill_images(conn, run)

        if listing_ids:
            queue_matches(conn, run, source_key=source, listing_ids=listing_ids)

        return "ok"

    if job == "scrape":

        # The one portal the Telegram feed does not publish, so it is fetched
        # from the site. Everything after this is the same path a feed listing
        # takes — which is why there is nothing here but collect and queue.
        from worker.sources.openrent import collect as scrape_openrent

        sweep = scrape_openrent(conn, run, dry_run=cfg.dry_run)

        # Nothing is alerted while the scraper is still catching up.
        #
        # The sitemap has no lastmod, so a listing found for the first time
        # looks new whether it went up an hour ago or a month ago. On the first
        # runs that is the whole standing market, and a subscriber would be sent
        # hundreds of flats that have been available for weeks — which is both
        # useless and indistinguishable from spam. They are stored, so they are
        # matched from then on; they are simply not announced retrospectively.
        if sweep.stored and sweep.caught_up:
            queue_matches(
                conn, run, source_key="openrent", listing_ids=sweep.stored
            )
        elif sweep.stored:
            with run.stage("seed-openrent") as stage:
                stage.count("stored_not_announced", len(sweep.stored))
                stage.log(
                    "info",
                    f"{len(sweep.stored)} openrent listings stored without "
                    "alerting: still working through the backlog",
                )
        return "ok"

    run.event("error", f"unknown job {job!r}")
    return "failed"

__all__ = ["DEFAULT_SOURCE", "run_job"]
