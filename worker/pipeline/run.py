"""Job orchestration.

Stages are separate and idempotent: each reads from the database and writes back,
so a failure loses only its own work and a repeat run is safe. The stages below
are placeholders that record their scope and counters; each subsequent step of
the plan replaces one of them.
"""

from __future__ import annotations

from typing import Any

import psycopg

from worker.config import Config
from worker.db import enabled_source_locations, get_source
from worker.obs import Run

Row = dict[str, Any]

HOT_STAGES = ("discover", "fetch", "extract", "normalize", "reconcile", "match", "notify")
SWEEP_STAGES = HOT_STAGES
DRAIN_STAGES = ("notify",)


def run_job(
    conn: psycopg.Connection[Row],
    run: Run,
    *,
    job: str,
    source_key: str | None,
    cfg: Config,
    suppress_delivery: bool = False,
    districts: frozenset[str] | None = None,
) -> str:
    """Execute one job. Returns the run status.

    `districts` narrows the run to specific postcode districts without touching
    `source_locations`, for one-off runs. It can only narrow: a district that is
    not enabled for the source is rejected rather than silently added, so an
    ad-hoc run cannot reach outside the configured coverage.
    """
    if job == "drain":
        return _run_stages(conn, run, DRAIN_STAGES, None, cfg, suppress_delivery)

    stages = SWEEP_STAGES if job == "sweep" else HOT_STAGES
    keys = [source_key] if source_key else _all_enabled_sources(conn)
    if not keys:
        run.event("warn", "no enabled sources; nothing to do")
        return "ok"

    statuses = []
    for key in keys:
        statuses.append(
            _run_stages(conn, run, stages, key, cfg, suppress_delivery, mode=job,
                        districts=districts)
        )
    return "degraded" if "degraded" in statuses else "ok"


def _all_enabled_sources(conn: psycopg.Connection[Row]) -> list[str]:
    rows = conn.execute("SELECT key FROM sources WHERE enabled ORDER BY key").fetchall()
    return [r["key"] for r in rows]


def _run_stages(
    conn: psycopg.Connection[Row],
    run: Run,
    stages: tuple[str, ...],
    source_key: str | None,
    cfg: Config,
    suppress_delivery: bool,
    mode: str = "hot",
    districts: frozenset[str] | None = None,
) -> str:
    degraded = False

    if source_key:
        source = get_source(conn, source_key)
        if source is None:
            run.event("error", f"unknown source {source_key}")
            return "degraded"

        # A source under a circuit-breaker cooldown, or with a broken extraction
        # schema, is skipped rather than retried: continuing would either extend
        # a block or produce delistings from a parser failure.
        if source["health"] in ("blocked", "broken"):
            until = source.get("health_until")
            run.event(
                "warn",
                f"{source_key} skipped: health={source['health']}",
                source_key=source_key,
                until=str(until) if until else None,
            )
            return "degraded"

        enabled = {row["code"].upper() for row in enabled_source_locations(conn, source_key)}
        if not enabled:
            run.event(
                "warn",
                f"{source_key} has no enabled locations; enable rows in source_locations",
                source_key=source_key,
            )
            return "degraded"

        scope = enabled
        if districts:
            outside = districts - enabled
            if outside:
                run.event(
                    "error",
                    f"requested districts are not enabled for {source_key}: "
                    f"{sorted(outside)}. Enable them in source_locations first.",
                    source_key=source_key,
                )
                return "degraded"
            scope = districts
            run.event(
                "info",
                f"scope narrowed for this run only: {len(scope)} of {len(enabled)}",
                source_key=source_key,
            )

        run.event(
            "info",
            f"{source_key} scope: {len(scope)} district(s)",
            source_key=source_key,
            districts=sorted(scope),
            mode=mode,
        )

    for name in stages:
        with run.stage(name, source_key=source_key) as st:
            if name == "notify" and suppress_delivery:
                st.set("suppressed", True)
                st.log("info", "quiet hours: delivery deferred, queue retained")
                continue
            if cfg.dry_run:
                st.set("dry_run", True)
            st.set("implemented", False)
            st.log("debug", f"{name} not implemented yet")
            degraded = True

    return "degraded" if degraded else "ok"
