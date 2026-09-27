from __future__ import annotations

import asyncio
from typing import Any

import psycopg

from worker.config import Config
from worker.obs import Run
from worker.pipeline.outbox import (
    ask_before_the_window_shuts,
    drain,
    notify_plan_changes,
    queue_matches,
    seed_new_subscriptions,
    watch_whatsapp_cost,
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
        # Before the digest and before delivery: this is what reopens a shut
        # WhatsApp window, and everything held back goes out once it is open.
        ask_before_the_window_shuts(conn, run, dry_run=cfg.dry_run)
        notify_plan_changes(conn, run, dry_run=cfg.dry_run)
        # After the digest, so today's count includes it, and before nothing:
        # the caps themselves are enforced when the queue is claimed.
        watch_whatsapp_cost(conn, run, dry_run=cfg.dry_run)
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

        # The original OpenRent reader, which discovers from the nationwide
        # sitemap. Left exactly as it was: `portals` below reads the same site
        # from its search pages for about a fiftieth of the traffic, and the
        # two can be compared before this one is switched off.
        from worker.sources.openrent import collect as scrape_openrent

        sweep = scrape_openrent(conn, run, dry_run=cfg.dry_run)

        # Only what the scraper is willing to call new. The rest is stored and
        # will be matched from now on, but is not announced retrospectively —
        # see Sweep.
        if sweep.announce:
            queue_matches(
                conn, run, source_key="openrent", listing_ids=sweep.announce
            )
        return "ok"

    if job == "portals":

        # Every portal read from its own search pages, through one engine.
        # Everything after this is the same path a feed listing takes, which is
        # why there is nothing here but collect and queue.
        from worker.sources.openrent_v2 import OpenRentV2
        from worker.sources.rightmove import Rightmove
        from worker.sources.sweep import collect as sweep_portal
        from worker.sources.zoopla import Zoopla

        portals = {
            one.key: one for one in (Rightmove(), Zoopla(), OpenRentV2())
        }
        if source_key is not None:
            if source_key not in portals:
                run.event(
                    "error",
                    f"{source_key!r} is not a portal this job reads; "
                    f"try one of {sorted(portals)}",
                )
                return "failed"
            portals = {source_key: portals[source_key]}

        status = "ok"
        for key, portal in portals.items():
            try:
                sweep = sweep_portal(conn, run, portal, dry_run=cfg.dry_run)
            except Exception as exc:
                # One portal must not take the others' listings with it. The
                # stage is already marked failed by `run.stage`; this keeps the
                # job going and reports honestly at the end.
                run.event(
                    "error", f"{key} failed: {type(exc).__name__}: {exc}"
                )
                status = "degraded"
                continue
            # Only what the engine is willing to call new — see the note on
            # the first run in worker.sources.sweep.
            if sweep.announce:
                queue_matches(
                    conn, run, source_key=key, listing_ids=sweep.announce
                )
        return status

    run.event("error", f"unknown job {job!r}")
    return "failed"

__all__ = ["DEFAULT_SOURCE", "run_job"]
