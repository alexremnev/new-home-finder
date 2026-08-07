"""Job orchestration.

Stages read from the database and write back to it, so a failure loses only its
own work and a repeat run is safe. Two things shape the error handling:

  * a refusal, a challenge, or an exhausted budget stops the source immediately.
    Continuing to request from a host that has just refused is the quickest way
    from a temporary limit to a lasting block;
  * while a source is unhealthy nothing is delisted. A parser returning nothing
    looks exactly like every listing disappearing at once, and the difference
    matters more than any other distinction in this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg

from worker import store
from worker.config import Config
from worker.contracts.listing import Listing, RawListing
from worker.contracts.source import FetchResult, FetchTask, Source, SourceLocation
from worker.db import enabled_source_locations
from worker.extract.engine import ExtractionError, apply
from worker.extract.fingerprint import fingerprint
from worker.extract.health import evaluate
from worker.fetch.client import (
    Blocked,
    BudgetExhausted,
    Challenged,
    Disallowed,
    FetchError,
    HttpTransport,
    PoliteClient,
    Policy,
    Transport,
)
from worker.obs import Run, Stage
from worker.pipeline.reconcile import Summary, decide_missing, decide_seen

# Imported from the package, not from the contracts module: the registry is filled
# by importing the adapters, and taking the bare dict leaves it empty.
from worker.sources import SOURCES

Row = dict[str, Any]
Conn = psycopg.Connection[Row]

# How long a source sits out after a refusal, doubling with each consecutive one.
COOLDOWN_BASE_SECONDS = 30 * 60
COOLDOWN_MAX_SECONDS = 12 * 3600


@dataclass
class SourceOutcome:
    status: str
    counters: dict[str, int]


def run_job(
    conn: Conn,
    run: Run,
    *,
    job: str,
    source_key: str | None,
    cfg: Config,
    suppress_delivery: bool = False,
    districts: frozenset[str] | None = None,
    transport: Transport | None = None,
) -> str:
    """Execute one job. Returns the run status.

    `transport` is injectable so the whole path can be exercised against a fixture
    without touching a live site.
    """
    if job == "drain":
        with run.stage("notify") as stage:
            stage.set("implemented", False)
            stage.log("debug", "delivery is not implemented yet")
        return "degraded"

    keys = [source_key] if source_key else _all_enabled_sources(conn)
    if not keys:
        run.event("warn", "no enabled sources; nothing to do")
        return "ok"

    statuses = []
    for key in keys:
        statuses.append(
            _run_source(
                conn, run, key,
                mode="sweep" if job == "sweep" else "hot",
                cfg=cfg, districts=districts, transport=transport,
                suppress_delivery=suppress_delivery,
            )
        )
    if "failed" in statuses:
        return "failed"
    return "degraded" if "degraded" in statuses else "ok"


def _all_enabled_sources(conn: Conn) -> list[str]:
    rows = conn.execute("SELECT key FROM sources WHERE enabled ORDER BY key").fetchall()
    return [r["key"] for r in rows]


# ── one source ────────────────────────────────────────────────────────────


def _run_source(
    conn: Conn,
    run: Run,
    source_key: str,
    *,
    mode: str,
    cfg: Config,
    districts: frozenset[str] | None,
    transport: Transport | None,
    suppress_delivery: bool,
) -> str:
    source = SOURCES.get(source_key)
    if source is None:
        run.event("error", f"no adapter registered for {source_key}", source_key=source_key)
        return "degraded"

    row = store.clear_source_cooldown_if_elapsed(conn, source_key)
    if row is None:
        run.event("error", f"{source_key} is not in sources", source_key=source_key)
        return "degraded"
    if row["health"] in ("blocked", "broken"):
        run.event(
            "warn",
            f"{source_key} skipped: health={row['health']}",
            source_key=source_key,
            until=str(row.get("health_until")) if row.get("health_until") else None,
        )
        return "degraded"

    locations = enabled_source_locations(conn, source_key)
    scope = _scope(run, source_key, locations, districts)
    if scope is None:
        return "degraded"

    config = row["config"] or {}
    client = PoliteClient(
        transport or HttpTransport(),
        policy=Policy.from_config(config, mode=mode),
        validators=store.load_validators(conn, source_key),
    )

    try:
        tasks = _discover(conn, run, source, client, locations, mode=mode, scope=scope)
        listings, health = _collect(conn, run, source, client, tasks, source_key=source_key)
        if health is not None and health.verdict == "broken":
            _mark_broken(conn, run, source_key, health.notes)
            return "degraded"
        summary = _reconcile(
            conn, run, source_key, listings, mode=mode, health=row["health"]
        )
        # The source answered, so any earlier refusals stop counting towards the
        # cooldown; otherwise it would keep doubling on stale history.
        store.clear_source_failures(conn, source_key)
    except (Challenged, Blocked) as exc:
        _mark_blocked(conn, run, source_key, row, exc)
        return "degraded"
    except BudgetExhausted as exc:
        run.event("warn", f"{source_key}: {exc}", source_key=source_key)
        return "degraded"
    except FetchError as exc:
        run.event("error", f"{source_key}: {exc}", source_key=source_key)
        return "degraded"
    finally:
        store.save_validators(conn, source_key, client.validators)
        run.event(
            "info", f"{source_key} client stats", source_key=source_key,
            requests=client.stats.requests, retries=client.stats.retries,
            not_modified=client.stats.not_modified,
            skipped_by_robots=client.stats.skipped_by_robots,
            ua_escalations=client.stats.ua_escalations,
        )

    with run.stage("match", source_key=source_key) as stage:
        stage.set("candidates", len(summary.new_ids))
        stage.set("implemented", False)
        stage.log("debug", "matching is not implemented yet")
    with run.stage("notify", source_key=source_key) as stage:
        stage.set("suppressed", suppress_delivery)
        stage.set("implemented", False)
        stage.log("debug", "delivery is not implemented yet")

    return "degraded"  # match and notify are still placeholders


def _scope(
    run: Run,
    source_key: str,
    locations: list[Row],
    districts: frozenset[str] | None,
) -> frozenset[str] | None:
    enabled = frozenset(str(loc["code"]).upper() for loc in locations)
    if not enabled:
        run.event(
            "warn",
            f"{source_key} has no enabled locations; enable rows in source_locations",
            source_key=source_key,
        )
        return None
    if districts:
        outside = districts - enabled
        if outside:
            run.event(
                "error",
                f"requested districts are not enabled for {source_key}: {sorted(outside)}",
                source_key=source_key,
            )
            return None
        run.event(
            "info", f"scope narrowed for this run only: {len(districts)} of {len(enabled)}",
            source_key=source_key,
        )
        enabled = districts
    run.event(
        "info", f"{source_key} scope: {len(enabled)} district(s)",
        source_key=source_key, districts=sorted(enabled),
    )
    return enabled


# ── discover ──────────────────────────────────────────────────────────────


def _discover(
    conn: Conn,
    run: Run,
    source: Source,
    client: PoliteClient,
    locations: list[Row],
    *,
    mode: str,
    scope: frozenset[str],
) -> list[FetchTask]:
    known = store.known_external_ids(conn, source.key)
    source_locations = [
        SourceLocation(
            source_key=source.key, location_id=loc["location_id"],
            code=loc["code"], external_id=loc["external_id"],
        )
        for loc in locations
    ]

    tasks: list[FetchTask] = []
    with run.stage("discover", source_key=source.key) as stage:
        stage.set("known", len(known))
        for index_task in source.discover(source_locations, mode):  # type: ignore[arg-type]
            response = client.get(index_task.url)
            stage.count("index_requests")
            if response.from_cache:
                stage.count("index_unchanged")
                continue
            result = FetchResult(
                task=index_task, status=response.status, body=response.body,
                fingerprint="", from_cache=response.from_cache,
            )
            for task in source.expand(result, scope=scope, known_ids=known):
                if task.page_kind == "search_list":
                    # A sitemap index points at child sitemaps; follow them now.
                    child = client.get(task.url)
                    stage.count("index_requests")
                    child_result = FetchResult(
                        task=task, status=child.status, body=child.body, fingerprint="",
                    )
                    tasks.extend(
                        source.expand(child_result, scope=scope, known_ids=known)
                    )
                else:
                    tasks.append(task)
        # A sitemap can list the same listing under more than one URL, and the
        # children can overlap. Without this the duplicates cost a request each and
        # then look like an extraction fault further down.
        unique: dict[str, FetchTask] = {}
        duplicates = 0
        for task in tasks:
            key = task.external_id or task.url
            if key in unique:
                duplicates += 1
                continue
            unique[key] = task
        if duplicates:
            stage.set("duplicate_tasks_dropped", duplicates)
            stage.log("info", f"dropped {duplicates} duplicate listing urls before fetching")
        tasks = list(unique.values())

        # Order carries no meaning, and walking identifiers in sequence is one of
        # the most recognisable signatures of an automated client.
        urls = client.order([t.url for t in tasks])
        by_url = {t.url: t for t in tasks}
        tasks = [by_url[u] for u in urls]
        stage.set("to_fetch", len(tasks))
        if not tasks:
            stage.log("info", "nothing new in scope")
    return tasks


# ── fetch, extract, normalise ─────────────────────────────────────────────


def _collect(
    conn: Conn,
    run: Run,
    source: Source,
    client: PoliteClient,
    tasks: list[FetchTask],
    *,
    source_key: str,
) -> tuple[list[Listing], Any]:
    listings: list[Listing] = []
    rows: list[dict[str, Any]] = []
    schema_id: int | None = None
    schema = None

    with run.stage("fetch", source_key=source_key) as fetch_stage, \
         run.stage("extract", source_key=source_key) as extract_stage, \
         run.stage("normalize", source_key=source_key) as norm_stage:
        for task in tasks:
            try:
                response = client.get(task.url)
            except Disallowed as exc:
                fetch_stage.count("disallowed")
                fetch_stage.log("warn", str(exc))
                continue
            fetch_stage.count("fetched")
            if response.status != 200 or not response.body:
                fetch_stage.count("unusable")
                continue

            if schema is None:
                loaded = _schema_for(conn, run, source, response.body, source_key=source_key)
                if loaded is None:
                    extract_stage.degrade("no extraction schema and none could be inferred")
                    break
                schema_id, schema = loaded

            try:
                extracted = apply(
                    schema, response.body,
                    base={"external_id": task.external_id, "url": task.url},
                )
            except ExtractionError as exc:
                extract_stage.count("extract_errors")
                extract_stage.log("warn", f"{task.url}: {exc}")
                continue
            if not extracted:
                extract_stage.count("empty")
                continue
            rows.extend(extracted)
            extract_stage.count("extracted", len(extracted))

            for raw in extracted:
                try:
                    listings.append(
                        source.normalize(
                            RawListing(source_key=source_key, fields=raw, schema_id=schema_id)
                        )
                    )
                    norm_stage.count("normalized")
                except Exception as exc:  # noqa: BLE001 - one bad row must not stop the run
                    norm_stage.count("rejected")
                    norm_stage.log("warn", f"{task.url}: {type(exc).__name__}: {exc}")

        health = None
        if rows:
            # The item floor belongs to the index, not here: a run may legitimately
            # find only one new listing, and treating that as a break would rewrite
            # a working schema for nothing.
            # One listing per page, so a repeated id would mean duplicated
            # discovery rather than a bad selector; that is checked earlier.
            health = evaluate(rows, source.fields, min_items=1, check_duplicate_ids=False)
            extract_stage.set("health", health.verdict)
            extract_stage.set("fill_rate", {k: round(v, 3) for k, v in health.fill_rate.items()})
            if health.verdict != "ok":
                extract_stage.log("warn", "; ".join(health.notes))
    return listings, health


def _schema_for(
    conn: Conn, run: Run, source: Source, body: bytes, *, source_key: str
) -> tuple[int, Any] | None:
    """The schema in force, seeding the adapter's own on first use."""
    loaded = store.load_schema(conn, source_key, "detail")
    if loaded is not None:
        return loaded

    built_in = getattr(source, "detail_schema", None)
    if built_in is None:
        run.event(
            "error",
            f"{source_key} has no stored schema and no built-in one; "
            "inferring one is not implemented yet",
            source_key=source_key,
        )
        return None

    layout = fingerprint(body, strategy=built_in.strategy, item=built_in.item)
    schema_id = store.save_schema(
        conn, source_key=source_key, page_kind="detail", schema=built_in,
        layout_fingerprint=layout, status="pinned", created_by="human",
    )
    run.event(
        "info",
        f"{source_key}: stored the adapter's schema as pinned (version 1)",
        source_key=source_key, fingerprint=layout,
    )
    return schema_id, built_in


# ── reconcile ─────────────────────────────────────────────────────────────


def _reconcile(
    conn: Conn,
    run: Run,
    source_key: str,
    listings: list[Listing],
    *,
    mode: str,
    health: str,
) -> Summary:
    summary = Summary()
    with run.stage("reconcile", source_key=source_key) as stage:
        seen: set[str] = set()
        for listing in listings:
            seen.add(listing.external_id)
            existing = store.existing_listing(conn, source_key, listing.external_id)
            decision = decide_seen(existing, listing.model_dump())

            if decision.kind == "insert":
                listing_id = store.insert_listing(conn, listing)
                store.record_price(conn, listing_id, listing.price_pcm)
            else:
                assert existing is not None
                listing_id = existing.id
                if decision.kind == "unchanged":
                    store.touch_listing(conn, listing_id)
                else:
                    store.update_listing(
                        conn, listing_id, listing, revive=decision.kind == "revive"
                    )
                if decision.price_change is not None:
                    store.record_price(conn, listing_id, listing.price_pcm)
            summary.record(decision, listing_id)

        if mode == "sweep":
            for external_id, existing in store.active_listing_ids(conn, source_key).items():
                if external_id in seen:
                    continue
                decision = decide_missing(existing, mode=mode, source_health=health)
                if decision.kind == "miss":
                    store.set_miss_count(conn, existing.id, decision.miss_count)
                elif decision.kind == "delist":
                    store.delist(conn, existing.id, decision.miss_count)
                summary.record(decision, existing.id)

        for name, value in summary.as_counters().items():
            stage.set(name, value)
            run.count(name, value)
    return summary


# ── health transitions ────────────────────────────────────────────────────


def _mark_blocked(conn: Conn, run: Run, source_key: str, row: Row, exc: Exception) -> None:
    """A refusal or a challenge stops the source and holds it out for a while.

    Delisting is suspended by the health value itself, so a block cannot be
    mistaken for every listing disappearing.
    """
    fails = int(row.get("consecutive_fails") or 0)
    cooldown = min(COOLDOWN_BASE_SECONDS * (2**fails), COOLDOWN_MAX_SECONDS)
    kind = "challenge" if isinstance(exc, Challenged) else "refusal"
    store.set_source_health(
        conn, source_key, "blocked", note=f"{kind}: {exc}",
        cooldown_seconds=cooldown, count_failure=True,
    )
    run.event(
        "error",
        f"{source_key} blocked ({kind}); run stopped, cooldown {cooldown // 60} min, "
        "delisting suspended",
        source_key=source_key,
    )


def _mark_broken(conn: Conn, run: Run, source_key: str, notes: list[str]) -> None:
    store.set_source_health(
        conn, source_key, "broken", note="; ".join(notes) or "extraction health failed"
    )
    run.event(
        "error",
        f"{source_key} extraction is broken; schema left in place and delisting suspended",
        source_key=source_key, notes=notes,
    )


__all__ = ["Stage", "run_job"]
