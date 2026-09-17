from __future__ import annotations

from dataclasses import asdict
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from worker.contracts.listing import Listing

Row = dict[str, Any]
Conn = psycopg.Connection[Row]

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
        """,
        {**values, "schema_id": None, "raw": Jsonb(listing.raw)},
    ).fetchone()
    assert row is not None
    return int(row["id"])

def _listing_values(listing: Listing) -> dict[str, Any]:
    data = asdict(listing) if hasattr(listing, "__dataclass_fields__") else listing.model_dump()
    return {name: data.get(name) for name in _INSERT_COLUMNS}

_LISTING_VIEW_COLUMNS = (
    "price_pcm", "bedrooms", "property_type", "postcode", "postcode_district", "tfl_zone",
    "available_from", "furnished", "pets_allowed", "bills_included",
    "min_tenancy_months", "is_landlord_direct", "url",

    "bathrooms", "deposit_pcm", "raw", "image_url",
)

def listings_for_matching(conn: Conn, listing_ids: list[int]) -> list[Row]:

    if not listing_ids:
        return []
    columns = ", ".join(_LISTING_VIEW_COLUMNS)
    return list(
        conn.execute(
            f"SELECT id, first_seen_at, {columns} FROM listings "
            "WHERE id = ANY(%s) ORDER BY first_seen_at, id",
            (listing_ids,),
        ).fetchall()
    )

def active_subscriptions(conn: Conn) -> list[Row]:

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
            SELECT c.user_id, c.stage, c.plan_until, c.plan, uc.channel, uc.address,
                   -- What the alerts fall back to, so the notice can say so rather
                   -- than claiming they stop. Nobody is cut off any more.
                   (SELECT lapsed.delivery_share
                      FROM plan_settings ps
                      JOIN plans lapsed
                        ON lapsed.key = ps.lapsed_plan AND lapsed.enabled
                     LIMIT 1) AS lapsed_share
              FROM claimed c
              JOIN user_channels uc ON uc.user_id = c.user_id AND uc.is_primary
            """,
            (limit,),
        ).fetchall()
    )

def queue_notifications(conn: Conn, rows: list[dict[str, Any]]) -> set[tuple[int, int]]:

    if not rows:
        return set()
    columns = ("user_id", "subscription_id", "listing_id", "channel", "kind", "status", "error")

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
        """,
        params,
    ).fetchall()
    return {(int(r["user_id"]), int(r["listing_id"])) for r in written}

def daily_digests(conn: Conn, *, limit: int = 500) -> list[Row]:

    return list(
        conn.execute(
            """
            WITH due AS (
                SELECT s.user_id,
                       count(n.id) FILTER (WHERE n.kind = 'new_listing') AS matched,
                       count(n.id) FILTER (WHERE n.status = 'sent')      AS sent,
                       count(n.id) FILTER (
                           WHERE n.status = 'skipped' AND n.error = 'share'
                       ) AS withheld,
                       round(avg(l.price_pcm))::int AS avg_price
                  FROM subscriptions s
                  JOIN users u ON u.id = s.user_id AND u.status = 'active'
                  -- LEFT, because a day with no match is still a day worth
                  -- reporting: silence is the signal that a filter is too tight.
                  LEFT JOIN notifications n
                         ON n.user_id = s.user_id
                        AND n.kind = 'new_listing'
                        AND n.created_at > now() - interval '24 hours'
                  LEFT JOIN listings l ON l.id = n.listing_id
                 WHERE s.active
                 GROUP BY s.user_id
                 ORDER BY s.user_id
                 LIMIT %s
            ),
            claimed AS (
                INSERT INTO daily_digests (user_id, day, withheld)
                SELECT user_id, current_date, withheld FROM due
                ON CONFLICT (user_id, day) DO NOTHING
                RETURNING user_id
            )
            SELECT d.user_id, d.matched, d.sent, d.withheld, d.avg_price,
                   uc.channel, uc.address, uc.last_inbound_at,
                   -- Paid means a plan that costs money and has not run out.
                   -- A live trial is not paid: it is the thing the button is
                   -- there to convert.
                   (p.price_pence > 0
                    AND (u.plan_until IS NULL OR u.plan_until > now())) AS paid,
                   CASE
                       WHEN u.plan_until IS NULL OR u.plan_until > now()
                           THEN p.delivery_share
                       ELSE coalesce(lapsed.delivery_share, 0)
                   END AS delivery_share
              FROM claimed c
              JOIN due d            ON d.user_id = c.user_id
              JOIN users u          ON u.id = c.user_id
              JOIN plans p          ON p.key = u.plan
              LEFT JOIN plan_settings ps ON ps.id
              LEFT JOIN plans lapsed     ON lapsed.key = ps.lapsed_plan AND lapsed.enabled
              JOIN user_channels uc ON uc.user_id = c.user_id AND uc.is_primary
             ORDER BY d.user_id
            """,
            (limit,),
        ).fetchall()
    )

def claim_queued(conn: Conn, *, limit: int, max_attempts: int) -> list[Row]:

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
            SELECT n.id, n.user_id, n.channel, n.kind, n.attempts, n.listing_id,
                   uc.address, uc.last_inbound_at,
                   src.display_name AS source_display, {columns},
                   u.plan AS plan_key,
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
            """,
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

    conn.execute(
        "UPDATE notifications SET status = 'skipped', error = %s WHERE id = %s",
        (reason[:500], notification_id),
    )

def leave_queued(conn: Conn, notification_id: int, error: str) -> None:

    conn.execute(
        "UPDATE notifications SET error = %s WHERE id = %s",
        (error[:500], notification_id),
    )

def stop_user(conn: Conn, user_id: int, *, reason: str) -> None:

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


def store_source_messages(conn: Conn, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    columns = (
        "source_key", "reader", "external_id", "received_at", "body", "links",
        "media_kinds", "content_hash",
    )
    groups = ", ".join(
        "(" + ", ".join(f"%({name}_{i})s" for name in columns) + ")"
        for i in range(len(rows))
    )
    params: dict[str, Any] = {}
    for i, row in enumerate(rows):
        for name in columns:
            params[f"{name}_{i}"] = row.get(name)

    written = conn.execute(
        f"""
        INSERT INTO source_messages ({", ".join(columns)})
        VALUES {groups}
        ON CONFLICT DO NOTHING
        RETURNING id
        """,  # noqa: S608 - placeholders only; column names are the fixed tuple above
        params,
    ).fetchall()
    return len(written)


def unparsed_messages(conn: Conn, *, source_key: str, limit: int = 500) -> list[Row]:

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

def listings_missing_image(conn: Conn, *, limit: int = 40) -> list[Row]:

    return list(
        conn.execute(
            """
            SELECT id, url FROM listings
             WHERE image_checked_at IS NULL AND status = 'active'
             ORDER BY first_seen_at DESC
             LIMIT %s
            """,
            (limit,),
        ).fetchall()
    )

def set_listing_image(conn: Conn, listing_id: int, image_url: str | None) -> None:

    # checked_at is written either way: "looked and found nothing" has to be
    # distinguishable from "not looked at", or every pictureless listing is
    # fetched again on every run, forever.
    conn.execute(
        "UPDATE listings SET image_url = %s, image_checked_at = now() WHERE id = %s",
        (image_url, listing_id),
    )

def mark_parsed(conn: Conn, message_id: int, listing_id: int) -> None:
    conn.execute(
        "UPDATE source_messages SET status = 'parsed', parsed_at = now(), "
        "listing_id = %s, parse_error = NULL WHERE id = %s",
        (listing_id, message_id),
    )

def mark_unparseable(conn: Conn, message_id: int, reason: str) -> None:

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

def unseeded_subscriptions(conn: Conn) -> list[Row]:

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

    columns = ", ".join(_LISTING_VIEW_COLUMNS)
    return list(
        conn.execute(
            f"""
            SELECT id, first_seen_at, {columns} FROM listings
             WHERE status = 'active'
               AND first_seen_at > now() - make_interval(days => %s)
             ORDER BY first_seen_at DESC
             LIMIT %s
            """,
            (days, limit),
        ).fetchall()
    )

def mark_seeded(conn: Conn, subscription_id: int) -> None:

    conn.execute(
        "UPDATE subscriptions SET seeded_at = now() WHERE id = %s", (subscription_id,)
    )
