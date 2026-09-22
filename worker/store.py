from __future__ import annotations

import secrets
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
            # The second portal's copy of a flat somebody has already been sent.
            # Filtered here rather than in the matcher so that every path into
            # the outbox — an alert, a digest, a starter batch — inherits it.
            "WHERE id = ANY(%s) AND duplicate_of IS NULL "
            "ORDER BY first_seen_at, id",
            (listing_ids,),
        ).fetchall()
    )

def mark_duplicate(conn: Conn, listing_id: int) -> int | None:
    """Point a listing at the copy of it we already have, if there is one.

    Returns the id it was pointed at, or None when this is the one that counts.

    The comparison is against the OLDEST listing sharing the fingerprint on that
    London day, and only when that one came from a different portal. Two from
    the same portal are a block of identical flats, not one flat twice — see
    0040 for why that distinction is the whole rule.
    """

    row = conn.execute(
        """
        UPDATE listings me
           SET duplicate_of = keeper.id
          FROM listings mine
          JOIN LATERAL (
            -- The oldest listing sharing the fingerprint that day, which may
            -- well be `mine` itself — that is how an original is recognised.
            SELECT o.id, o.source_key
              FROM listings o
             WHERE o.postcode  = mine.postcode
               AND o.price_pcm = mine.price_pcm
               AND o.bedrooms  = mine.bedrooms
               -- Not stated on both portals as often as the rest, and NULL has
               -- to match NULL for the pair to be found at all.
               AND o.bathrooms IS NOT DISTINCT FROM mine.bathrooms
               AND (o.first_seen_at AT TIME ZONE 'Europe/London')::date
                 = (mine.first_seen_at AT TIME ZONE 'Europe/London')::date
             ORDER BY o.first_seen_at, o.id
             LIMIT 1
          ) AS keeper ON true
         WHERE mine.id = %(id)s
           AND me.id = mine.id
           AND mine.postcode IS NOT NULL
           AND me.duplicate_of IS NULL
           AND keeper.id <> mine.id
           AND keeper.source_key <> mine.source_key
        RETURNING me.duplicate_of
        """,
        {"id": listing_id},
    ).fetchone()
    return int(row["duplicate_of"]) if row and row["duplicate_of"] else None

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

# A checkout link the worker can push. The site issues these for somebody who
# has just asked (/pay), and an hour's life is right for that. A link inside the
# 20:00 digest is different: it may well be tapped the next morning, and an
# expired button is worse than none.
PUSHED_TOKEN_MINUTES = 36 * 60

# Reused while it has this much life left, so that the link in a message sent
# ten minutes ago still works. Issuing a fresh one every run would kill it.
KEEP_ALIVE_MINUTES = 60

def upgrade_token(conn: Conn, user_id: int) -> str:

    live = conn.execute(
        """
        SELECT token FROM user_tokens
         WHERE user_id = %s AND purpose = 'upgrade' AND used_at IS NULL
           AND expires_at > now() + make_interval(mins => %s)
         ORDER BY expires_at DESC
         LIMIT 1
        """,
        (user_id, KEEP_ALIVE_MINUTES),
    ).fetchone()
    if live:
        return str(live["token"])

    # base64url of 24 bytes, the same shape the site issues.
    token = secrets.token_urlsafe(24)
    conn.execute(
        "DELETE FROM user_tokens WHERE user_id = %s AND purpose = 'upgrade'", (user_id,)
    )
    conn.execute(
        """
        INSERT INTO user_tokens (token, user_id, purpose, expires_at)
        VALUES (%s, %s, 'upgrade', now() + make_interval(mins => %s))
        """,
        (token, user_id, PUSHED_TOKEN_MINUTES),
    )
    return token

def subscribed_districts(conn: Conn) -> list[str]:

    # The districts somebody is actually waiting to hear about: taken from live
    # subscriptions rather than from an operator's list of what a source covers.
    #
    # This is what the scraper filters the sitemap against, and the reason is
    # that the two lists disagreed in both directions. A district on the list
    # that nobody had chosen cost requests for nothing; a district somebody had
    # chosen but which was missing from the list was never fetched at all, and
    # that subscriber heard nothing from this source with no error anywhere.
    #
    # Following the subscriptions cannot drift: there is one list, and it is the
    # one that decides who gets sent what.
    # The conditions are `active_subscriptions` own, deliberately: that query
    # decides who is sent anything, so anything it excludes is a district worth
    # no requests. A finished trial is *not* excluded — it drops the person to
    # the lapsed share rather than stopping them, and a share of nothing is
    # nothing. A lapsed tier set to zero is excluded, because then they really
    # do receive nothing.
    return [
        str(row["code"])
        for row in conn.execute(
            """
            SELECT DISTINCT upper(area) AS code
              FROM subscriptions s
              JOIN users u          ON u.id = s.user_id AND u.status = 'active'
              JOIN plans p          ON p.key = u.plan
              JOIN user_channels uc ON uc.user_id = s.user_id AND uc.is_primary
              JOIN channels c       ON c.key = uc.channel AND c.enabled
              LEFT JOIN plan_settings ps ON ps.id
              LEFT JOIN plans lapsed     ON lapsed.key = ps.lapsed_plan AND lapsed.enabled
              CROSS JOIN LATERAL jsonb_array_elements_text(
                  coalesce(s.criteria->'areas'->'postcode_districts', '[]'::jsonb)
              ) AS area
             WHERE s.active
               AND CASE
                       WHEN u.plan_until IS NULL OR u.plan_until > now()
                           THEN p.delivery_share
                       ELSE coalesce(lapsed.delivery_share, 0)
                   END > 0
             ORDER BY code
            """
        ).fetchall()
    ]

def settled_districts(conn: Conn, source_key: str) -> set[str]:

    # Districts this source has been read through at least once. Anything new
    # in one of these genuinely appeared after we last looked; anything new in
    # a district that is not here may have been on the market for months.
    return {
        str(row["district"])
        for row in conn.execute(
            "SELECT district FROM source_sweeps WHERE source_key = %s", (source_key,)
        ).fetchall()
    }

def settle_district(conn: Conn, source_key: str, district: str) -> None:

    conn.execute(
        """
        INSERT INTO source_sweeps (source_key, district)
        VALUES (%s, %s)
        ON CONFLICT (source_key, district) DO NOTHING
        """,
        (source_key, district.upper()),
    )

def known_external_ids(
    conn: Conn, *, source_key: str, external_ids: list[str]
) -> set[str]:

    # Asked in one query rather than one per id: the sitemap has no lastmod, so
    # every run compares its whole list against what is already stored.
    if not external_ids:
        return set()
    return {
        str(row["external_id"])
        for row in conn.execute(
            "SELECT external_id FROM listings "
            "WHERE source_key = %s AND external_id = ANY(%s)",
            (source_key, external_ids),
        ).fetchall()
    }

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
                       -- Rooms and whole homes are never averaged together: a
                       -- £900 room beside a £2,100 flat produces a figure that
                       -- describes neither, and the digest quoted it as though
                       -- it described the market.
                       --
                       -- So a filter asking only for rooms gets the average of
                       -- rooms, and every other filter gets the average with
                       -- rooms left out. A mixed filter therefore reports on the
                       -- homes it matched; that is a partial answer, but it is
                       -- a true one.
                       round(avg(l.price_pcm) FILTER (
                           WHERE CASE
                               WHEN jsonb_typeof(s.criteria->'property_types') = 'array'
                                AND jsonb_array_length(s.criteria->'property_types') = 1
                                AND s.criteria->'property_types'->>0 = 'room'
                               THEN l.property_type = 'room'
                               -- NULL is a home: the feed leaves the type unset
                               -- for a plain bedroom count, and only ever writes
                               -- 'room' when it means one.
                               ELSE l.property_type IS DISTINCT FROM 'room'
                           END
                       ))::int AS avg_price,
                       -- Which of the two the figure is, so the digest can say
                       -- so rather than leaving it to be guessed.
                       coalesce(
                           jsonb_typeof(s.criteria->'property_types') = 'array'
                           AND jsonb_array_length(s.criteria->'property_types') = 1
                           AND s.criteria->'property_types'->>0 = 'room',
                           false
                       ) AS rooms_only
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
                   d.rooms_only,
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

# How long a WhatsApp alert waits for its photograph before going without one.
#
# WhatsApp is sent the picture itself rather than a link, so an alert that
# overtakes its own upload arrives as plain text and is never revisited. Ingest
# runs every two minutes and uploads a batch each time, so one cycle is usually
# enough; this is the cap, not the wait.
PHOTO_GRACE_MINUTES = 5

def claim_queued(
    conn: Conn, *, limit: int, max_attempts: int, photo_grace: int = PHOTO_GRACE_MINUTES
) -> list[Row]:

    claimed = conn.execute(
        """
        WITH due AS (
            SELECT n.id FROM notifications n
             WHERE n.status = 'queued' AND n.attempts < %(max_attempts)s
               -- Held back, not claimed: claiming spends an attempt, and a
               -- message waiting for a picture has not failed at anything.
               AND NOT (
                   n.channel = 'whatsapp'
                   AND n.created_at > now() - make_interval(mins => %(photo_grace)s)
                   AND EXISTS (
                       SELECT 1 FROM source_messages m
                        WHERE m.listing_id = n.listing_id
                          AND 'photo' = ANY(m.media_kinds)
                          AND m.wa_media_checked_at IS NULL
                   )
               )
             ORDER BY n.created_at
             LIMIT %(limit)s
             FOR UPDATE SKIP LOCKED
        )
        UPDATE notifications n SET attempts = n.attempts + 1
          FROM due WHERE n.id = due.id
        RETURNING n.id
        """,
        {"limit": limit, "max_attempts": max_attempts, "photo_grace": photo_grace},
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
                   (SELECT m.wa_media_id FROM source_messages m
                     WHERE m.listing_id = n.listing_id AND m.wa_media_id IS NOT NULL
                     LIMIT 1) AS wa_media_id,
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
        "source_key", "reader", "chat", "external_id", "received_at", "body", "links",
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

def messages_missing_photo(
    conn: Conn, *, source_key: str, chat: str, reader: str, limit: int = 10
) -> list[Row]:

    return list(
        conn.execute(
            """
            SELECT id, external_id FROM source_messages
             WHERE source_key = %s
               AND wa_media_checked_at IS NULL
               AND 'photo' = ANY(media_kinds)
               -- Only what can still be sent. The starter batch looks back
               -- three days and an alert goes out in minutes.
               AND received_at > now() - interval '7 days'
               -- Anybody reading this chat can fetch it: the id means the same
               -- thing to all of them. A row from before 0032 has no chat, and
               -- for those only the reader that stored it knows what the id
               -- refers to.
               AND (chat = %s OR (chat IS NULL AND reader = %s))
             ORDER BY received_at DESC
             LIMIT %s
            """,
            (source_key, chat, reader, limit),
        ).fetchall()
    )

def set_message_photo(conn: Conn, message_id: int, media_id: str | None) -> None:

    conn.execute(
        "UPDATE source_messages SET wa_media_id = %s, wa_media_checked_at = now() "
        "WHERE id = %s",
        (media_id, message_id),
    )

def listings_missing_image(conn: Conn, *, limit: int = 40) -> list[Row]:

    return list(
        conn.execute(
            """
            SELECT l.id, l.url FROM listings l
             WHERE l.image_checked_at IS NULL
               AND l.status = 'active'
               -- Only listings that can still be sent. A picture for a flat
               -- from August is a request to a portal for nobody's benefit,
               -- and the starter batch looks back three days.
               AND l.first_seen_at > now() - interval '7 days'
               -- And only those with no photograph of their own: the message
               -- that made this listing usually carried one, and asking a
               -- portal for a picture we already have is rude twice over.
               AND NOT EXISTS (
                 SELECT 1 FROM source_messages m
                  WHERE m.listing_id = l.id AND m.wa_media_id IS NOT NULL
               )
             ORDER BY l.first_seen_at DESC
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
