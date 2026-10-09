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
from worker.pipeline.purge import run_purge
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


def _openrent() -> Any:
    from worker.sources.openrent import OpenRent

    return OpenRent()


def _spareroom() -> Any:
    """SpareRoom, read as a rotating window over the whole London feed.

    A region reader like `zoopla_london`, and for a different reason: Zoopla
    reads the city in one search because it is cheaper than reading twenty
    districts, while SpareRoom has no district page to read at all — an outcode
    url redirects into the one place its robots.txt forbids. See the module.
    """

    from worker.sources.spareroom import SpareRoom

    return SpareRoom()


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
    "openrent": _openrent,
    "spareroom": _spareroom,
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

    if job == "purge":
        # Once a night, and the only job that removes rows. Deliberately not
        # folded into `rollup`, which runs every two minutes: a mistake in a
        # retention rule would then be applied seven hundred times before
        # anybody noticed, and the two jobs want opposite things from a
        # failure — a missed rollup repairs itself on the next tick, a missed
        # purge simply waits a day.
        run_purge(conn, run, dry_run=cfg.dry_run)
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

    if job in PORTAL_JOBS or job == "portals":

        # One job per portal, each with its own timer so they do not land on
        # the server together, and each with its own row in `job_runs` — which
        # is what makes the admin Jobs panel say "zoopla has not run since
        # Tuesday" instead of hiding it inside one combined job that looks
        # healthy because the others worked.
        #
        # The job name is NOT the source key, though it reads like one for
        # three of the five: `zoopla_london` and `zoopla` are two jobs reading
        # one site under one key. Everything below that asks the database about
        # a source therefore asks the reader for its key and never uses the
        # name of the job.
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
            # Built, like the ones above. This used to assign the thunk
            # itself, so `--source` on a portal job handed `sweep.collect` a
            # function where a reader belongs and the run died on the first
            # attribute it asked for — reported as "degraded" by the handler
            # below, which looks like the portal refused us.
            wanted = {source_key: PORTAL_JOBS[source_key]()}

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
                # Keyed to the source the reader WRITES to, not to the job
                # name — the same distinction the `enabled` gate above makes,
                # and for a worse consequence. `zoopla_london` writes `zoopla`
                # and has no `sources` row of its own (0061 says so, and it
                # should not have one), while `store.announces` answers False
                # for a key the table has never heard of. So keyed by job name
                # the whole-city sweep stored everything it found and queued
                # none of it, every five minutes, silently.
                #
                # Which job did the reading is not lost by this: `job_runs.job`
                # is the job name, and the stage is recorded against that run.
                queue_matches(
                    conn, run, source_key=reader.key, listing_ids=sweep.announce
                )
        return status

    run.event("error", f"unknown job {job!r}")
    return "failed"

__all__ = ["DEFAULT_SOURCE", "PORTAL_JOBS", "run_job"]
