"""Persistence.

Deliberately thin. Every rule about a listing's life lives in
`worker.pipeline.reconcile` as a pure function; this module only reads and writes
what those functions decide. That split is what makes the subtle behaviour
testable without a database, and keeps the SQL simple enough to read.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from worker.contracts.extraction import ExtractionSchema, Health
from worker.contracts.listing import Listing
from worker.pipeline.reconcile import TRACKED, Existing

Row = dict[str, Any]
Conn = psycopg.Connection[Row]


# ── sources ───────────────────────────────────────────────────────────────


def source_row(conn: Conn, source_key: str) -> Row | None:
    return conn.execute("SELECT * FROM sources WHERE key = %s", (source_key,)).fetchone()


def set_source_health(
    conn: Conn,
    source_key: str,
    health: str,
    *,
    note: str | None = None,
    cooldown_seconds: float | None = None,
    count_failure: bool = False,
) -> None:
    conn.execute(
        """
        UPDATE sources
           SET health = %(health)s,
               health_note = %(note)s,
               -- Casts are required: a NULL parameter compared with IS NULL
               -- gives the planner nothing to infer a type from, and the
               -- statement is rejected before it runs.
               health_until = CASE
                   WHEN %(cooldown)s::double precision IS NULL THEN NULL
                   ELSE now() + make_interval(secs => %(cooldown)s::double precision)
               END,
               consecutive_fails = CASE
                   WHEN %(count_failure)s::boolean THEN consecutive_fails + 1
                   ELSE consecutive_fails
               END
         WHERE key = %(key)s
        """,
        {
            "key": source_key, "health": health, "note": note,
            "cooldown": cooldown_seconds, "count_failure": count_failure,
        },
    )


def clear_source_failures(conn: Conn, source_key: str) -> None:
    """A run that reached the source resets the escalation.

    Without this the cooldown would keep doubling on the strength of refusals that
    happened days ago and have since stopped.
    """
    conn.execute(
        """
        UPDATE sources SET consecutive_fails = 0
         WHERE key = %s AND consecutive_fails > 0
        """,
        (source_key,),
    )


def clear_source_cooldown_if_elapsed(conn: Conn, source_key: str) -> Row | None:
    """Return a source to service once its cooldown has passed.

    Without this a circuit breaker would latch permanently, and the source would
    stay skipped long after the reason had gone.
    """
    conn.execute(
        """
        UPDATE sources
           SET health = 'ok', health_note = NULL, health_until = NULL
         WHERE key = %s AND health_until IS NOT NULL AND health_until <= now()
        """,
        (source_key,),
    )
    return source_row(conn, source_key)


# ── extraction schemas ────────────────────────────────────────────────────


def load_schema(conn: Conn, source_key: str, page_kind: str) -> tuple[int, ExtractionSchema] | None:
    row = conn.execute(
        """
        SELECT id, schema FROM parse_schemas
         WHERE source_key = %s AND page_kind = %s AND status IN ('active', 'pinned')
        """,
        (source_key, page_kind),
    ).fetchone()
    if row is None:
        return None
    return row["id"], ExtractionSchema.model_validate(row["schema"])


def save_schema(
    conn: Conn,
    *,
    source_key: str,
    page_kind: str,
    schema: ExtractionSchema,
    layout_fingerprint: str,
    status: str,
    created_by: str,
    validation: Health | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
) -> int:
    """Store a schema and make it the one in force.

    The previous one is retired rather than deleted: it is keyed by the layout it
    was written for, so if the site reverts an experiment it can be reactivated
    without asking a model anything.
    """
    conn.execute(
        """
        UPDATE parse_schemas SET status = 'retired'
         WHERE source_key = %s AND page_kind = %s AND status IN ('active', 'pinned')
        """,
        (source_key, page_kind),
    )
    row = conn.execute(
        """
        INSERT INTO parse_schemas
               (source_key, page_kind, version, layout_fingerprint, strategy, schema,
                status, created_by, validation, tokens_in, tokens_out, activated_at)
        SELECT %(source)s, %(kind)s,
               coalesce(max(version), 0) + 1,
               %(fingerprint)s, %(strategy)s, %(schema)s, %(status)s, %(by)s,
               %(validation)s, %(tin)s, %(tout)s, now()
          FROM parse_schemas WHERE source_key = %(source)s AND page_kind = %(kind)s
        RETURNING id
        """,
        {
            "source": source_key,
            "kind": page_kind,
            "fingerprint": layout_fingerprint,
            "strategy": schema.strategy,
            "schema": Jsonb(schema.model_dump(exclude_none=True)),
            "status": status,
            "by": created_by,
            "validation": Jsonb(validation.model_dump()) if validation else None,
            "tin": tokens_in,
            "tout": tokens_out,
        },
    ).fetchone()
    assert row is not None
    return int(row["id"])


def schema_for_fingerprint(
    conn: Conn, source_key: str, page_kind: str, layout_fingerprint: str
) -> tuple[int, ExtractionSchema] | None:
    """A retired schema written for this exact layout, if there is one."""
    row = conn.execute(
        """
        SELECT id, schema FROM parse_schemas
         WHERE source_key = %s AND page_kind = %s AND layout_fingerprint = %s
           AND status = 'retired'
         ORDER BY version DESC LIMIT 1
        """,
        (source_key, page_kind, layout_fingerprint),
    ).fetchone()
    if row is None:
        return None
    return row["id"], ExtractionSchema.model_validate(row["schema"])


# ── listings ──────────────────────────────────────────────────────────────


def known_external_ids(conn: Conn, source_key: str) -> set[str]:
    """Every id already stored, so a listing costs no request to re-discover."""
    rows = conn.execute(
        "SELECT external_id FROM listings WHERE source_key = %s", (source_key,)
    ).fetchall()
    return {r["external_id"] for r in rows}


def existing_listing(conn: Conn, source_key: str, external_id: str) -> Existing | None:
    columns = ", ".join(TRACKED)
    row = conn.execute(
        f"SELECT id, status, miss_count, {columns} FROM listings "  # noqa: S608 - fixed names
        "WHERE source_key = %s AND external_id = %s",
        (source_key, external_id),
    ).fetchone()
    if row is None:
        return None
    return Existing(
        id=row["id"],
        status=row["status"],
        miss_count=row["miss_count"],
        values={name: row[name] for name in TRACKED},
    )


def active_listing_ids(conn: Conn, source_key: str) -> dict[str, Existing]:
    """Everything currently active, keyed by external id, for a full pass."""
    columns = ", ".join(TRACKED)
    rows = conn.execute(
        f"SELECT external_id, id, status, miss_count, {columns} FROM listings "  # noqa: S608
        "WHERE source_key = %s AND status = 'active'",
        (source_key,),
    ).fetchall()
    return {
        r["external_id"]: Existing(
            id=r["id"], status=r["status"], miss_count=r["miss_count"],
            values={name: r[name] for name in TRACKED},
        )
        for r in rows
    }


_INSERT_COLUMNS = (
    "source_key", "external_id", "url", "price_pcm", "bedrooms", "bathrooms",
    "property_type", "furnished", "pets_allowed", "bills_included", "available_from",
    "min_tenancy_months", "deposit_pcm", "postcode", "postcode_district", "tfl_zone",
    "lat", "lng", "title", "description", "is_landlord_direct", "photo_count",
)


def insert_listing(conn: Conn, listing: Listing, *, schema_id: int | None = None) -> int:
    values = _listing_values(listing)
    columns = ", ".join(_INSERT_COLUMNS)
    placeholders = ", ".join(f"%({name})s" for name in _INSERT_COLUMNS)
    row = conn.execute(
        f"""
        INSERT INTO listings ({columns}, schema_id, raw)
        VALUES ({placeholders}, %(schema_id)s, %(raw)s)
        ON CONFLICT (source_key, external_id) DO UPDATE
           SET last_seen_at = now(), miss_count = 0
        RETURNING id
        """,  # noqa: S608 - column names are a fixed tuple in this module
        {**values, "schema_id": schema_id, "raw": Jsonb(listing.raw)},
    ).fetchone()
    assert row is not None
    return int(row["id"])


def update_listing(conn: Conn, listing_id: int, listing: Listing, *, revive: bool) -> None:
    values = _listing_values(listing)
    assignments = ", ".join(
        f"{name} = %({name})s" for name in _INSERT_COLUMNS if name not in ("source_key",
                                                                          "external_id")
    )
    conn.execute(
        f"""
        UPDATE listings
           SET {assignments},
               raw = %(raw)s,
               last_seen_at = now(),
               miss_count = 0,
               status = 'active',
               delisted_at = CASE WHEN %(revive)s THEN NULL ELSE delisted_at END
         WHERE id = %(listing_id)s
        """,  # noqa: S608 - assignments are built from a fixed tuple
        {**values, "raw": Jsonb(listing.raw), "revive": revive, "listing_id": listing_id},
    )


def touch_listing(conn: Conn, listing_id: int) -> None:
    """An unchanged listing: record that it is still there and nothing else."""
    conn.execute(
        "UPDATE listings SET last_seen_at = now(), miss_count = 0 WHERE id = %s",
        (listing_id,),
    )


def record_price(conn: Conn, listing_id: int, price_pcm: int) -> None:
    conn.execute(
        """
        INSERT INTO listing_price_log (listing_id, price_pcm) VALUES (%s, %s)
        ON CONFLICT (listing_id, seen_at) DO NOTHING
        """,
        (listing_id, price_pcm),
    )


def set_miss_count(conn: Conn, listing_id: int, miss_count: int) -> None:
    conn.execute("UPDATE listings SET miss_count = %s WHERE id = %s", (miss_count, listing_id))


def delist(conn: Conn, listing_id: int, miss_count: int) -> None:
    """Soft delete only. History is needed for analytics and for future dedup."""
    conn.execute(
        """
        UPDATE listings
           SET status = 'delisted', delisted_at = now(), miss_count = %s
         WHERE id = %s
        """,
        (miss_count, listing_id),
    )


def _listing_values(listing: Listing) -> dict[str, Any]:
    data = asdict(listing) if hasattr(listing, "__dataclass_fields__") else listing.model_dump()
    return {name: data.get(name) for name in _INSERT_COLUMNS}


# ── matching and the outbox ───────────────────────────────────────────────
#
# Delivery goes through a table rather than straight out of the matcher. The
# reason is the one property that matters most here: a listing must reach the
# recipient exactly once. A crash between deciding to send and sending would,
# without a row to point at, either lose the message or repeat it, and
# `UNIQUE (user_id, listing_id)` makes the repeat impossible even if the whole
# stage runs twice.

_LISTING_VIEW_COLUMNS = (
    "price_pcm", "bedrooms", "property_type", "postcode_district", "tfl_zone",
    "available_from", "furnished", "pets_allowed", "bills_included",
    "min_tenancy_months", "is_landlord_direct", "url",
    # Shown in the alert too. `raw` carries what has no column of its own — the
    # neighbourhood name, the street, the floor area as the source worded it — and
    # the renderer decides what to do with them.
    "bathrooms", "deposit_pcm", "raw",
)


def listings_for_matching(conn: Conn, listing_ids: list[int]) -> list[Row]:
    """The listings a run just found, oldest first.

    Read back from the database rather than reused from memory because matching
    needs `first_seen_at`, which only exists once the row is written.
    """
    if not listing_ids:
        return []
    columns = ", ".join(_LISTING_VIEW_COLUMNS)
    return list(
        conn.execute(
            f"SELECT id, first_seen_at, {columns} FROM listings "  # noqa: S608 - fixed names
            "WHERE id = ANY(%s) ORDER BY first_seen_at, id",
            (listing_ids,),
        ).fetchall()
    )


def active_subscriptions(conn: Conn) -> list[Row]:
    """Every filter that should currently receive alerts.

    The plan is resolved here, in the query the matcher already runs, rather than
    by a job that rewrites entitlements. A job that fails to run leaves people on a
    tier they are not paying for; a CASE in this query cannot.

    An expired plan no longer removes the row. It resolves `delivery_share` to the
    lapsed tier's instead, so a finished trial keeps receiving a share of its
    matches. A share of 0 — which happens only if the lapsed tier is missing or
    disabled — means nothing is delivered, which is the old behaviour and the way to
    restore it deliberately.

    The address is deliberately not selected: matching does not need it, and a
    value that is never loaded cannot be logged by accident.
    """
    return list(
        conn.execute(
            """
            SELECT s.id, s.user_id, s.criteria, s.backfill_from, uc.channel,
                   -- The plan's own share while it is live, the lapsed tier's once
                   -- it is not. Resolved here so an expired plan steps down instead
                   -- of dropping out, and so no job has to rewrite `users.plan`.
                   CASE
                       WHEN u.plan_until IS NULL OR u.plan_until > now()
                           THEN p.delivery_share
                       ELSE coalesce(lapsed.delivery_share, 0)
                   END AS delivery_share
              FROM subscriptions s
              JOIN users u          ON u.id = s.user_id
              JOIN plans p          ON p.key = u.plan
              JOIN user_channels uc ON uc.user_id = s.user_id AND uc.is_primary
              JOIN channels c       ON c.key = uc.channel AND c.enabled
              LEFT JOIN plan_settings ps ON ps.id
              LEFT JOIN plans lapsed     ON lapsed.key = ps.lapsed_plan AND lapsed.enabled
             WHERE s.active AND u.status = 'active'
             ORDER BY s.id
            """
        ).fetchall()
    )


def claim_plan_notices(conn: Conn, *, limit: int = 200) -> list[Row]:
    """Plan warnings that are due, claimed so each is sent exactly once.

    Three stages: a day before the plan ends, an hour before, and once it has. The
    point of warning beforehand is that "your alerts stopped an hour ago" is a
    message about a decision the person no longer gets to make.

    Two properties are worth stating, because both are enforced by the schema
    rather than by this function remembering.

    Once each. The INSERT into `plan_notices` *is* the claim: the unique index on
    (user_id, stage, plan_until) means a second worker, or a second tick, inserts
    nothing and therefore returns nothing. There is no flag to read, and no window
    between deciding and recording.

    Re-armed by a renewal, with no reset anywhere. `plan_until` is part of that key,
    so paying moves the expiry, which makes a different key, which makes the day and
    hour warnings due again for the new period. This is what makes "if they pay
    before the trial ends, warn them again before the paid period ends" fall out of
    the data model instead of being a special case in code.

    The most urgent unsent stage per user, one row each: someone whose plan ended
    while the worker was down is told it ended, not warned it is about to.
    """
    return list(
        conn.execute(
            """
            WITH due AS (
                SELECT u.id AS user_id, u.plan, u.plan_until,
                       CASE
                           WHEN u.plan_until <= now()                      THEN 'expired'
                           WHEN u.plan_until <= now() + interval '1 hour'  THEN 'hour'
                           ELSE 'day'
                       END AS stage
                  FROM users u
                 WHERE u.status = 'active'
                   AND u.plan_until IS NOT NULL
                   AND u.plan_until <= now() + interval '1 day'
                 ORDER BY u.plan_until
                 LIMIT %s
            ),
            claimed AS (
                INSERT INTO plan_notices (user_id, stage, plan_until, plan)
                SELECT user_id, stage, plan_until, plan FROM due
                ON CONFLICT (user_id, stage, plan_until) DO NOTHING
                RETURNING user_id, stage, plan_until, plan
            )
            SELECT c.user_id, c.stage, c.plan_until, c.plan, uc.channel, uc.address
              FROM claimed c
              JOIN user_channels uc ON uc.user_id = c.user_id AND uc.is_primary
            """,
            (limit,),
        ).fetchall()
    )


def queue_notification(
    conn: Conn, *, user_id: int, subscription_id: int, listing_id: int, channel: str,
    kind: str = "new_listing", status: str = "queued", error: str | None = None,
) -> bool:
    """Add one row to the outbox. False means this user already had this listing.

    `status='skipped'` writes the row without queuing a send, which is how a match
    withheld by the plan's share is still recorded once and only once.
    """
    row = conn.execute(
        """
        INSERT INTO notifications
               (user_id, subscription_id, listing_id, channel, kind, status, error,
                sent_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s,
                CASE WHEN %s = 'skipped' THEN now() ELSE NULL END)
        ON CONFLICT (user_id, listing_id) DO NOTHING
        RETURNING id
        """,
        (user_id, subscription_id, listing_id, channel, kind, status, error, status),
    ).fetchone()
    return row is not None


def claim_queued(conn: Conn, *, limit: int, max_attempts: int) -> list[Row]:
    """Take a batch of queued messages and count the attempt before sending.

    Counting first is deliberate. If the process dies mid-send the attempt is
    already recorded, so a message that kills the worker is retried a bounded
    number of times instead of forever.
    """
    claimed = conn.execute(
        """
        WITH due AS (
            SELECT id FROM notifications
             WHERE status = 'queued' AND attempts < %(max_attempts)s
             ORDER BY created_at
             LIMIT %(limit)s
             FOR UPDATE SKIP LOCKED
        )
        UPDATE notifications n SET attempts = n.attempts + 1
          FROM due WHERE n.id = due.id
        RETURNING n.id
        """,
        {"limit": limit, "max_attempts": max_attempts},
    ).fetchall()
    ids = [int(r["id"]) for r in claimed]
    if not ids:
        return []
    columns = ", ".join(f"l.{name}" for name in _LISTING_VIEW_COLUMNS)
    return list(
        conn.execute(
            f"""
            SELECT n.id, n.user_id, n.channel, n.kind, n.attempts,
                   uc.address, src.display_name AS source_display, {columns}
              FROM notifications n
              JOIN listings l  ON l.id = n.listing_id
              JOIN sources src ON src.key = l.source_key
              LEFT JOIN user_channels uc
                     ON uc.user_id = n.user_id AND uc.channel = n.channel
             WHERE n.id = ANY(%s)
             ORDER BY n.created_at
            """,  # noqa: S608 - column names are a fixed tuple in this module
            (ids,),
        ).fetchall()
    )


def queued_count(conn: Conn) -> int:
    row = conn.execute(
        "SELECT count(*) AS n FROM notifications WHERE status = 'queued'"
    ).fetchone()
    return int((row or {}).get("n") or 0)


def mark_sent(
    conn: Conn, notification_id: int, *, provider_msg_id: str | None, cost_micros: int = 0
) -> None:
    conn.execute(
        """
        UPDATE notifications
           SET status = 'sent', sent_at = now(), provider_msg_id = %s,
               cost_micros = %s, error = NULL
         WHERE id = %s
        """,
        (provider_msg_id, cost_micros, notification_id),
    )


def mark_failed(conn: Conn, notification_id: int, error: str) -> None:
    conn.execute(
        "UPDATE notifications SET status = 'failed', error = %s WHERE id = %s",
        (error[:500], notification_id),
    )


def mark_skipped(conn: Conn, notification_id: int, reason: str) -> None:
    """Abandoned rather than attempted: the recipient is gone, or delivery was
    called off for a reason that has nothing to do with this message."""
    conn.execute(
        "UPDATE notifications SET status = 'skipped', error = %s WHERE id = %s",
        (reason[:500], notification_id),
    )


def leave_queued(conn: Conn, notification_id: int, error: str) -> None:
    """A failure worth retrying: the reason is recorded, the row stays in the queue."""
    conn.execute(
        "UPDATE notifications SET error = %s WHERE id = %s",
        (error[:500], notification_id),
    )


def stop_user(conn: Conn, user_id: int, *, reason: str) -> None:
    """The recipient is unreachable: stop trying, and stop collecting for them.

    Subscriptions are deactivated rather than deleted. Deleting is what `/stop`
    means — an explicit request — and this is not that: someone who blocked the
    bot and later unblocks it should find their filter intact.
    """
    conn.execute(
        """
        UPDATE users SET status = 'blocked', stopped_at = now()
         WHERE id = %s AND status <> 'blocked'
        """,
        (user_id,),
    )
    conn.execute("UPDATE subscriptions SET active = false WHERE user_id = %s", (user_id,))
    conn.execute(
        """
        UPDATE notifications SET status = 'skipped', error = %s
         WHERE user_id = %s AND status = 'queued'
        """,
        (reason[:500], user_id),
    )


# ── conditional request validators ────────────────────────────────────────


def load_validators(conn: Conn, source_key: str) -> dict[str, tuple[str | None, str | None]]:
    """ETag and Last-Modified per URL, so an unchanged page costs almost nothing."""
    row = conn.execute(
        "SELECT config -> 'validators' AS v FROM sources WHERE key = %s", (source_key,)
    ).fetchone()
    stored = (row or {}).get("v") or {}
    return {url: (pair.get("etag"), pair.get("modified")) for url, pair in stored.items()}


def save_validators(
    conn: Conn, source_key: str, validators: dict[str, tuple[str | None, str | None]], *,
    keep: int = 500,
) -> None:
    trimmed = dict(list(validators.items())[-keep:])
    payload = {
        url: {"etag": etag, "modified": modified} for url, (etag, modified) in trimmed.items()
    }
    conn.execute(
        "UPDATE sources SET config = jsonb_set(config, '{validators}', %s::jsonb) WHERE key = %s",
        (json.dumps(payload), source_key),
    )

# ── inbound messages from a feed ──────────────────────────────────────────


def store_source_message(
    conn: Conn, *, source_key: str, reader: str, external_id: str, received_at: Any,
    body: str | None, links: list[str], media_kinds: list[str], content_hash: str,
) -> bool:
    """Keep one raw message. False means it was already known.

    Two unique indexes can reject it, and they answer different questions: the
    per-reader one means this reader has read it before, the content one means
    another reader already has. `ON CONFLICT DO NOTHING` without naming a column
    covers both, which is what makes a second account safe to add.
    """
    row = conn.execute(
        """
        INSERT INTO source_messages
               (source_key, reader, external_id, received_at, body, links,
                media_kinds, content_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        RETURNING id
        """,
        (source_key, reader, external_id, received_at, body, links,
         media_kinds, content_hash),
    ).fetchone()
    return row is not None


def unparsed_messages(conn: Conn, *, source_key: str, limit: int = 500) -> list[Row]:
    """The parser's queue, oldest first.

    Oldest first so a backlog is worked through in the order it arrived, and capped
    so a first run over months of history does not load all of it into memory. What
    is left is simply picked up by the next run.
    """
    return list(
        conn.execute(
            """
            SELECT id, external_id, received_at, body, links
              FROM source_messages
             WHERE source_key = %s AND status = 'new'
             ORDER BY received_at, id
             LIMIT %s
            """,
            (source_key, limit),
        ).fetchall()
    )


def mark_parsed(conn: Conn, message_id: int, listing_id: int) -> None:
    conn.execute(
        "UPDATE source_messages SET status = 'parsed', parsed_at = now(), "
        "listing_id = %s, parse_error = NULL WHERE id = %s",
        (listing_id, message_id),
    )


def mark_unparseable(conn: Conn, message_id: int, reason: str) -> None:
    """Recorded, not deleted, and not retried.

    The reason is stored because a rising count of one particular reason is how a
    format change announces itself — and because the message is still here, a fixed
    parser can be run over the backlog by setting these rows back to 'new'.
    """
    conn.execute(
        "UPDATE source_messages SET status = 'unparseable', parsed_at = now(), "
        "parse_error = %s WHERE id = %s",
        (reason[:500], message_id),
    )


def ingest_cursor(conn: Conn, *, reader: str, source_key: str) -> int:
    row = conn.execute(
        "SELECT last_external_id FROM ingest_cursors WHERE reader = %s AND source_key = %s",
        (reader, source_key),
    ).fetchone()
    return 0 if row is None else int(row["last_external_id"])


def set_ingest_cursor(conn: Conn, *, reader: str, source_key: str, last_external_id: int) -> None:
    """Move a reader's cursor forward, never back.

    `greatest` rather than assignment: two runs of the same reader can overlap, and
    the later-finishing one may hold the older value. Rewinding would re-read
    messages that are already stored — harmless, because the unique indexes reject
    them, but it would also make the cursor useless as a progress signal.
    """
    conn.execute(
        """
        INSERT INTO ingest_cursors (reader, source_key, last_external_id)
        VALUES (%s, %s, %s)
        ON CONFLICT (reader, source_key) DO UPDATE
           SET last_external_id = greatest(ingest_cursors.last_external_id,
                                           EXCLUDED.last_external_id),
               updated_at = now()
        """,
        (reader, source_key, last_external_id),
    )
