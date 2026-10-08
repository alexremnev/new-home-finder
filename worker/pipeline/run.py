from __future__ import annotations

import asyncio
from typing import Any

import psycopg

from worker import store
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


def _rightmove() -> Any:
    from worker.sources.rightmove import Rightmove

    return Rightmove()


def _zoopla() -> Any:
    from worker.sources.zoopla import Zoopla

    return Zoopla()


def _openrent_v2() -> Any:
    from worker.sources.openrent_v2 import OpenRentV2

    return OpenRentV2()


def _zoopla_london() -> Any:
    """Zoopla, read as one search of the whole city rather than district by
    district.

    A separate job from `zoopla` and not a replacement for it: they are two
    schedules with two timers, and the district reader stays the way to read a
    named district on demand — which is what onboarding a district and catching
    up after an outage both need.

    The same source key, though, so the two converge on one row per flat. See
    the class note in worker.sources.zoopla.
    """

    from worker.sources.zoopla import Zoopla

    return Zoopla.for_region()


# One job per portal, keyed by source key. Built through these thunks rather
# than imported at module level: the delivery host installs no scraping extra,
# and `import worker.pipeline.run` must not require curl_cffi there.
PORTAL_JOBS: dict[str, Any] = {
    "rightmove": _rightmove,
    "zoopla": _zoopla,
    "zoopla_london": _zoopla_london,
    "openrent_v2": _openrent_v2,
}

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

    if job in PORTAL_JOBS or job == "portals":

        # One job per portal, and the job name *is* the source key. Each has
        # its own timer so the three do not land on the server together, and
        # each gets its own row in `job_runs` — which is what makes the admin
        # Jobs panel say "zoopla has not run since Tuesday" instead of hiding
        # it inside one combined job that looks healthy because the other two
        # worked.
        #
        # `portals` still runs all of them, for a manual sweep. Nothing
        # schedules it.
        from worker.sources.sweep import collect as sweep_portal

        chosen = dict(PORTAL_JOBS) if job == "portals" else {job: PORTAL_JOBS[job]}
        # Built here rather than inside the loop, because the gate below has to
        # ask each reader what source it writes to. Building is cheap — these
        # are plain objects — and it is only reached on a portal job, which is
        # the point of the thunks: importing this module still needs no scraping
        # extra on the delivery host.
        wanted = {name: build() for name, build in chosen.items()}

        # `sources.enabled` decides whether a portal runs, so switching one off
        # is one UPDATE and no deploy. An explicit --source overrides it,
        # because asking for one reader by name is a deliberate act and having
        # it silently do nothing would be worse than an error.
        #
        # Asked of the reader's own source key, not of the job name. Two jobs
        # can read one site — `zoopla` by district and `zoopla_london` in one
        # sweep — and keyed by job name the second was switched off by a
        # `sources` row that does not exist and never will.
        if source_key is None:
            live = store.enabled_sources(conn)
            for name in sorted(wanted):
                if wanted[name].key in live:
                    continue
                run.event(
                    "info",
                    f"{name} reads {wanted[name].key}, which is disabled in "
                    f"sources; skipping",
                )
                del wanted[name]
            if not wanted:
                run.event("warn", "no enabled portal to read")
                return "ok"
        else:
            if source_key not in PORTAL_JOBS:
                run.event(
                    "error",
                    f"{source_key!r} is not a portal; "
                    f"try one of {sorted(PORTAL_JOBS)}",
                )
                return "failed"
            wanted = {source_key: PORTAL_JOBS[source_key]}

        status = "ok"
        for key, reader in wanted.items():
            try:
                sweep = sweep_portal(conn, run, reader, dry_run=cfg.dry_run)
            except Exception as exc:
                # One portal must not take the others' listings with it. The
                # stage is already marked failed by `run.stage`; this keeps the
                # job going and reports honestly at the end.
                run.event("error", f"{key} failed: {type(exc).__name__}: {exc}")
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

__all__ = ["DEFAULT_SOURCE", "PORTAL_JOBS", "run_job"]
