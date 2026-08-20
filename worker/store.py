"""Persistence.

Deliberately thin: every decision is a pure function elsewhere — what a message
means in `worker.ingest.tg_feed`, whether a listing suits a filter in
`worker.pipeline.match`, what to do with a failed send in `worker.pipeline.outbox`
— and this module only reads and writes what they decide. That split is what makes
the subtle behaviour testable without a database.

`schema_id` survives on `insert_listing` because the column does. It is always None
now: it belonged to the schema-driven extraction the scraper used, and dropping the
column is a migration for a day when something else needs that space.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from worker.contracts.listing import Listing

Row = dict[str, Any]
Conn = psycopg.Connection[Row]


# ── sources ───────────────────────────────────────────────────────────────


def source_row(conn: Conn, source_key: str) -> Row | None:
    return conn.execute("SELECT * FROM sources WHERE key = %s", (source_key,)).fetchone()


# ── listings ──────────────────────────────────────────────────────────────

# Every column a listing is written with. A fixed tuple, and the SQL is built from
# it, so a field added to `Listing` has exactly one place to be added here — and the
# f-string interpolating it is safe because nothing outside this module can reach it.
_INSERT_COLUMNS = (
    "source_key", "external_id", "url", "price_pcm", "bedrooms", "bathrooms",
    "property_type", "furnished", "pets_allowed", "bills_included", "available_from",
    "min_tenancy_months", "deposit_pcm", "postcode", "postcode_district", "tfl_zone",
    "lat", "lng", "title", "description", "is_landlord_direct", "photo_count",
)


def insert_listing(conn: Conn, listing: Listing) -> int:
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
        {**values, "schema_id": None, "raw": Jsonb(listing.raw)},
    ).fetchone()
    assert row is not None
    return int(row["id"])


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
    "price_pcm", "bedrooms", "property_type", "postcode", "postcode_district", "tfl_zone",
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


def queue_notifications(conn: Conn, rows: list[dict[str, Any]]) -> set[tuple[int, int]]:
    """Queue a whole batch. Returns the (user_id, listing_id) pairs actually written.

    One round trip instead of one per match, and that is the difference between a
    tick that takes seconds and one that takes minutes: at 500 subscribers a busy
    hour produces thousands of matches, and each `INSERT` was a separate journey to
    Supabase — about 20ms of network for a millisecond of work.

    The returned set is what distinguishes a fresh row from one the unique index
    rejected, which the caller needs in order to count "already notified" without
    asking again.
    """
    if not rows:
        return set()
    columns = ("user_id", "subscription_id", "listing_id", "channel", "kind", "status", "error")
    # One placeholder group per row. Built rather than looped so that the whole batch
    # is one statement; `ON CONFLICT DO NOTHING` then makes a repeat harmless.
    groups = ", ".join(
        "(" + ", ".join(f"%({name}_{i})s" for name in columns)
        + f", CASE WHEN %(status_{i})s = 'skipped' THEN now() ELSE NULL END)"
        for i in range(len(rows))
    )
    params: dict[str, Any] = {}
    for i, row in enumerate(rows):
        for name in columns:
            params[f"{name}_{i}"] = row.get(name)
    written = conn.execute(
        f"""
        INSERT INTO notifications
               ({", ".join(columns)}, sent_at)
        VALUES {groups}
        ON CONFLICT (user_id, listing_id) DO NOTHING
        RETURNING user_id, listing_id
        """,  # noqa: S608 - placeholders only; column names are the fixed tuple above
        params,
    ).fetchall()
    return {(int(r["user_id"]), int(r["listing_id"])) for r in written}


def withheld_digests(conn: Conn, *, limit: int = 200) -> list[Row]:
    """People whose plan held listings back today, claimed so each is told once.

    The INSERT is the claim — the primary key on (user_id, day) means a second drain
    the same day inserts nothing and returns nothing, so no flag has to be read and
    there is no window between deciding to send and recording it.

    Counted over the last 24 hours rather than since the previous digest: the claim
    already guarantees at most one message a day, and a watermark would add a column
    to be kept correct for a number that is approximate by nature — "about fourteen"
    is the useful fact, not fourteen exactly.
    """
    return list(
        conn.execute(
            """
            WITH due AS (
                SELECT n.user_id, count(*) AS withheld
                  FROM notifications n
                  JOIN users u ON u.id = n.user_id AND u.status = 'active'
                 WHERE n.status = 'skipped' AND n.error = 'share'
                   AND n.created_at > now() - interval '24 hours'
                 GROUP BY n.user_id
                 LIMIT %s
            ),
            claimed AS (
                INSERT INTO daily_digests (user_id, day, withheld)
                SELECT user_id, current_date, withheld FROM due
                ON CONFLICT (user_id, day) DO NOTHING
                RETURNING user_id, withheld
            )
            SELECT c.user_id, c.withheld, uc.channel, uc.address
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
                   uc.address, src.display_name AS source_display, {columns},
                   -- The same CASE as `active_subscriptions`, for the same reason:
                   -- the live plan's share while it is live, the lapsed tier's once
                   -- it is not. Selected here so the renderer can name the share in
                   -- the message without a second query per notification.
                   CASE
                       WHEN u.plan_until IS NULL OR u.plan_until > now()
                           THEN p.delivery_share
                       ELSE coalesce(lapsed.delivery_share, 0)
                   END AS delivery_share
              FROM notifications n
              JOIN listings l  ON l.id = n.listing_id
              JOIN sources src ON src.key = l.source_key
              JOIN users u     ON u.id = n.user_id
              JOIN plans p     ON p.key = u.plan
              LEFT JOIN plan_settings ps ON ps.id
              LEFT JOIN plans lapsed     ON lapsed.key = ps.lapsed_plan AND lapsed.enabled
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


def ensure_district(conn: Conn, code: str, *, source_key: str) -> bool:
    """Register a district a feed has actually delivered. True if it was new.

    The reference data covers zones 1-3, because that is the scope a scraper was
    given. A feed answers to nobody's scope: it sent HA1 on the first day, and a
    listing in a district nobody can name is a listing that matches nobody — stored,
    counted, and invisible.

    So coverage follows the data rather than a hand-kept list. The zone is left null
    because it is genuinely unknown here and guessing it would put a wrong number
    where a filter can read it; `approx` says so out loud.

    Safe to call per listing: both statements are upserts, and the common case is
    two index probes that change nothing.
    """
    row = conn.execute(
        """
        INSERT INTO locations (kind, code, city, approx)
        VALUES ('postcode_district', %s, 'London', true)
        ON CONFLICT (kind, code) DO NOTHING
        RETURNING id
        """,
        (code.upper(),),
    ).fetchone()
    created = row is not None
    if row is None:
        row = conn.execute(
            "SELECT id FROM locations WHERE kind = 'postcode_district' AND code = %s",
            (code.upper(),),
        ).fetchone()
    if row is None:
        return False
    conn.execute(
        """
        INSERT INTO source_locations (source_key, location_id, external_id, enabled)
        VALUES (%s, %s, %s, true)
        ON CONFLICT (source_key, location_id) DO UPDATE SET enabled = true
        """,
        (source_key, int(row["id"]), code.upper()),
    )
    return created


# ── the starter batch ─────────────────────────────────────────────────────


def unseeded_subscriptions(conn: Conn) -> list[Row]:
    """Subscriptions owed their first handful of listings.

    The same joins as `active_subscriptions`, so a subscription with no delivery
    channel or a disabled one is not offered a batch it could never receive. The
    share is *not* selected: the starter batch is somebody's first impression of the
    product, and throttling it to a tenth would be an odd way to sell the rest.
    """
    return list(
        conn.execute(
            """
            SELECT s.id, s.user_id, s.criteria, uc.channel
              FROM subscriptions s
              JOIN users u          ON u.id = s.user_id AND u.status = 'active'
              JOIN user_channels uc ON uc.user_id = s.user_id AND uc.is_primary
              JOIN channels c       ON c.key = uc.channel AND c.enabled
             WHERE s.active AND s.seeded_at IS NULL
             ORDER BY s.created_at
            """
        ).fetchall()
    )


def recent_listings(conn: Conn, *, days: int, limit: int) -> list[Row]:
    """Listings from the last few days, newest first, for seeding.

    Newest first because that is the order a starter batch should arrive in, and
    because rental listings go stale in days — a fortnight-old flat is usually gone,
    and offering it as a first impression is worse than offering nothing.
    """
    columns = ", ".join(_LISTING_VIEW_COLUMNS)
    return list(
        conn.execute(
            f"""
            SELECT id, first_seen_at, {columns} FROM listings
             WHERE status = 'active'
               AND first_seen_at > now() - make_interval(days => %s)
             ORDER BY first_seen_at DESC
             LIMIT %s
            """,  # noqa: S608 - column names are a fixed tuple in this module
            (days, limit),
        ).fetchall()
    )


def mark_seeded(conn: Conn, subscription_id: int) -> None:
    """Stamped whether or not anything matched.

    Retrying an empty batch every few minutes for ever would mean a subscription in
    a quiet district is re-examined against the same listings until one appears —
    and then sent five at once days later, as if they were new.
    """
    conn.execute(
        "UPDATE subscriptions SET seeded_at = now() WHERE id = %s", (subscription_id,)
    )
