from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from worker.contracts.listing import Listing

Row = dict[str, Any]
Conn = psycopg.Connection[Row]


_INSERT_COLUMNS = (
    "source_key", "external_id", "url", "price_pcm", "bedrooms", "bathrooms",
    "property_type", "furnished", "pets_allowed", "bills_included", "available_from",
    "min_tenancy_months", "deposit_pcm", "postcode", "postcode_source",
    "postcode_district", "tfl_zone",
    "lat", "lng", "title", "description", "is_landlord_direct", "photo_count",
    "floor_area_sqft",
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

    "bathrooms", "deposit_pcm", "raw", "image_url", "floor_area_sqft",
    # For the map link on a listing whose portal never stated a full postcode,
    # which is most of Rightmove and all of Zoopla.
    "lat", "lng",
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

    Only postcodes a portal stated take part. A derived one is the nearest
    centroid to a coordinate the portal rounded, so it collapses the several
    unit postcodes around a block into one value — and two different flats
    merging is not a duplicate somebody can ignore, it is a flat the subscriber
    is never told about. See 0056: filling those postcodes is for the alert to
    name a street, and dedupe on them needs a distance test as well.
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
               AND o.postcode_source = 'portal'
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
           AND mine.postcode_source = 'portal'
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
                   -- it is not — and "live" is two questions now, days and
                   -- alerts. Both are answered by `user_entitlement`; see 0057
                   -- for why this is not written out here any more.
                   e.delivery_share
              FROM subscriptions s
              JOIN users u            ON u.id = s.user_id
              JOIN user_entitlement e ON e.user_id = u.id
              JOIN user_channels uc   ON uc.user_id = s.user_id AND uc.is_primary
              JOIN channels c         ON c.key = uc.channel AND c.enabled
             WHERE s.active AND u.status = 'active'
             ORDER BY s.id
            """
        ).fetchall()
    )

# A checkout link the worker can push. The site issues these for somebody who
# has just asked (/pay), and an hour's life is right for that. A link inside a
# listing alert or the 20:00 digest is different: it may well be tapped the next
# morning, and an expired button is worse than none.
PUSHED_TOKEN_MINUTES = 36 * 60

def upgrade_token(conn: Conn, user_id: int) -> str:

    # Kept and extended, never replaced while it is live.
    #
    # One account has one upgrade token, and every link it has ever appeared in
    # is the same string — so pushing a new message cannot kill the button in
    # the last one. Extending rather than overwriting is also what stops the
    # site and the worker taking the link away from each other: an hour from
    # /pay and thirty-six from here, and `greatest` means whichever is longer
    # wins rather than whichever ran last.
    live = conn.execute(
        """
        UPDATE user_tokens
           SET expires_at = greatest(
                   expires_at, now() + make_interval(mins => %s)
               )
         WHERE user_id = %s AND purpose = 'upgrade'
           AND used_at IS NULL AND expires_at > now()
        RETURNING token
        """,
        (PUSHED_TOKEN_MINUTES, user_id),
    ).fetchone()
    if live:
        return str(live["token"])

    # base64url of 24 bytes, the same shape the site issues.
    token = secrets.token_urlsafe(24)
    # Only dead rows are left to clear: anything live was returned above.
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
              JOIN users u            ON u.id = s.user_id AND u.status = 'active'
              JOIN user_entitlement e ON e.user_id = u.id
              JOIN user_channels uc   ON uc.user_id = s.user_id AND uc.is_primary
              JOIN channels c         ON c.key = uc.channel AND c.enabled
              CROSS JOIN LATERAL jsonb_array_elements_text(
                  coalesce(s.criteria->'areas'->'postcode_districts', '[]'::jsonb)
              ) AS area
             WHERE s.active
               AND e.delivery_share > 0
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

@dataclass(frozen=True)
class Watch:
    """What we know about our own reading of one district on one source."""

    #: When watching began. Fixed. Decides what counts as news.
    settled_at: datetime
    #: When it was last read. Moves. Decides how far back to page.
    swept_at: datetime | None


def district_watch(conn: Conn, source_key: str) -> dict[str, Watch]:
    """Per district: since when we have watched it, and when we last read it.

    Two timestamps because they answer two different questions, and conflating
    them made the scrapers pay for their own history — see 0050.
    """

    return {
        str(row["district"]): Watch(
            settled_at=row["settled_at"], swept_at=row["swept_at"]
        )
        for row in conn.execute(
            "SELECT district, settled_at, swept_at FROM source_sweeps "
            "WHERE source_key = %s",
            (source_key,),
        ).fetchall()
    }


def void_watch(conn: Conn, source_key: str, districts: list[str]) -> None:
    """Forget that we were ever watching these districts.

    `settled_at` is a claim that everything after it was seen, and that claim
    is only true while we kept looking. A district that fell out of every
    subscription, or that a sleeping machine or a refusing portal left unread,
    has a gap — and the next run cannot tell what appeared during the gap from
    what appeared a minute ago. Announcing the difference means a week of
    listings arriving at once.

    Deleting the row puts the district back to never-watched, so the next run
    reads it silently and starts again from now. One quiet pass is the whole
    cost, and the alternative is a flood.
    """

    if not districts:
        return
    conn.execute(
        "DELETE FROM source_sweeps WHERE source_key = %s AND district = ANY(%s)",
        (source_key, [one.upper() for one in districts]),
    )


def mark_swept(conn: Conn, source_key: str, district: str) -> None:
    """Record that this district has just been read."""

    conn.execute(
        "UPDATE source_sweeps SET swept_at = now() "
        "WHERE source_key = %s AND district = %s",
        (source_key, district.upper()),
    )


def watching_since(conn: Conn, source_key: str) -> dict[str, datetime]:
    """When each district started being watched on this source.

    Kept as its own function because the undated sources only ever need this
    half of it. See `district_watch` for both timestamps.
    """

    return {
        district: watch.settled_at
        for district, watch in district_watch(conn, source_key).items()
    }


def settle_district(conn: Conn, source_key: str, district: str) -> None:
    """Start watching a district: from now on, what appears here is news.

    `swept_at` is set at the same time, and that is not decoration. Settling a
    district happens because we have just read it, so "when did we last read
    it" is now. Leaving it NULL meant a freshly settled district looked to
    `sweep.stale_watches` exactly like one with a gap — so it was voided on the
    very next run, settled again, voided again, once per run for ever, and
    nothing was ever announced from it. NULL now means only what it was
    supposed to mean: a row written before 0050.
    """

    conn.execute(
        """
        INSERT INTO source_sweeps (source_key, district, swept_at)
        VALUES (%s, %s, now())
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

def enabled_sources(conn: Conn) -> set[str]:
    """Which source keys are switched on.

    Read so that turning a portal off is one UPDATE and no deploy — the same
    principle as the coverage and rate settings in `sources.config`. Until
    this existed the column was decoration: the job built every reader whatever
    the row said.
    """

    return {
        str(row["key"])
        for row in conn.execute("SELECT key FROM sources WHERE enabled").fetchall()
    }


def listing_ids_for(
    conn: Conn, *, source_key: str, external_ids: list[str]
) -> dict[str, int]:
    """external_id -> listing id, for the ones already stored.

    The id-carrying form of `known_external_ids`. A scraper needs both answers
    at once: which of these are new to us, and — for the ones that are not —
    which row to record the sighting against. Asking twice would be a second
    round trip for a question the first one already answered.
    """

    if not external_ids:
        return {}
    return {
        str(row["external_id"]): int(row["id"])
        for row in conn.execute(
            "SELECT id, external_id FROM listings "
            "WHERE source_key = %s AND external_id = ANY(%s)",
            (source_key, external_ids),
        ).fetchall()
    }


def seen_ids(
    conn: Conn, *, source_key: str, external_ids: list[str]
) -> dict[str, str | None]:
    """Which of these ids this source has already resolved, and to where.

    Separate from `listing_ids_for` because a listing we decided not to store
    is still a listing we resolved. OpenRent's district search is a
    two-kilometre radius, so a third of what it returns belongs to a
    neighbouring district: those are correctly absent from `listings`, and
    without this table every run would spend a redirect lookup rediscovering
    that they are somebody else's. See 0053.

    The value is the district the id turned out to be in, or None when its slug
    could not be read.
    """

    if not external_ids:
        return {}
    return {
        str(row["external_id"]): (
            None if row["district"] is None else str(row["district"])
        )
        for row in conn.execute(
            "SELECT external_id, district FROM source_seen_ids "
            "WHERE source_key = %s AND external_id = ANY(%s)",
            (source_key, external_ids),
        ).fetchall()
    }


def remember_seen(
    conn: Conn, source_key: str, resolved: dict[str, str | None]
) -> None:
    """Note which district each of these ids turned out to be in.

    One statement for the batch: a district resolves hundreds of ids on its
    first pass, and a round trip each would cost more than the fetching does.
    """

    if not resolved:
        return
    ids = list(resolved)
    conn.execute(
        """
        INSERT INTO source_seen_ids (source_key, external_id, district)
        SELECT %s, one.id, one.district
          FROM unnest(%s::text[], %s::text[]) AS one(id, district)
        ON CONFLICT (source_key, external_id) DO NOTHING
        """,
        (source_key, ids, [resolved[one] for one in ids]),
    )


def record_sightings(conn: Conn, listing_ids: list[int], reader: str) -> None:
    """Note that this reader has seen these listings.

    One statement for the whole district rather than one per listing: a sweep
    sees a few hundred, almost all of them already known, and three portals
    times twenty districts times a round trip each would cost more than the
    fetching does. See 0052 for why this is recorded at all.
    """

    if not listing_ids:
        return
    conn.execute(
        """
        INSERT INTO listing_sightings (listing_id, reader)
        SELECT unnest(%s::bigint[]), %s
        ON CONFLICT (listing_id, reader) DO NOTHING
        """,
        (list(listing_ids), reader),
    )


def biggest_sitemap(conn: Conn, source_key: str, *, days: int = 7) -> int | None:
    """The most listings one child sitemap has held lately.

    OpenRent sometimes answers the same sitemap url with a complete but stunted
    file — 123 listings where there are normally 25,000, closing tag and all, so
    nothing about the response says it is short. Only its size does, and only
    compared against what the same url usually gives.

    Per child sitemap, not per run, because the number of children varies: the
    index lists one file some runs and two others. Compared per run, a perfectly
    good single-file run of 24,920 sat just under half of a two-file run's
    49,872 and was called stunted by 32 listings.

    None when there is no history to compare against, which is the first run and
    is not a reason to distrust anything.
    """

    row = conn.execute(
        """
        SELECT max(
                 (counters->>'in_sitemap')::int
                 / greatest(1, (counters->>'sitemaps')::int)
               ) AS most
          FROM job_stages
         WHERE stage = 'scrape' AND source_key = %s
           AND jsonb_typeof(counters->'in_sitemap') = 'number'
           AND jsonb_typeof(counters->'sitemaps') = 'number'
           AND (counters->>'sitemaps')::int > 0
           AND started_at > now() - make_interval(days => %s)
        """,
        (source_key, days),
    ).fetchone()
    most = None if row is None else row["most"]
    return None if most is None else int(most)

def costly_whatsapp(conn: Conn) -> list[Row]:
    """WhatsApp subscribers who have crossed a volume threshold unannounced.

    One row per alert still owed, with `kind` saying which threshold and
    `paid` saying whether delivery was stopped or merely noted. The insert into
    `whatsapp_cost_alerts` is what makes it "still owed": drain runs every two
    minutes, and without it crossing a line would be announced thirty times an
    hour.
    """

    return list(
        conn.execute(
            """
            WITH tally AS (
                SELECT n.user_id,
                       count(*) FILTER (
                           WHERE (n.sent_at AT TIME ZONE 'Europe/London')::date
                               = (now() AT TIME ZONE 'Europe/London')::date
                       ) AS today,
                       count(*) AS ever
                  FROM notifications n
                 WHERE n.channel = 'whatsapp' AND n.status = 'sent'
                 GROUP BY n.user_id
            ),
            owed AS (
                -- The threshold is the cap itself: the alert says "we have
                -- stopped delivering to this person", so reading it from
                -- anywhere else would let it announce the wrong thing.
                --
                -- The lifetime figure that used to sit beside this is gone. It
                -- was a proxy for a bound on what one subscriber can cost, and
                -- 0057 made that a real one: `plans.alert_allowance`, which
                -- ends the period and tells the person rather than telling us.
                SELECT t.user_id, 'daily' AS kind, t.today AS sent
                  FROM tally t
                 WHERE t.today >= (SELECT wa_daily_cap FROM delivery_limits)
            ),
            claimed AS (
                INSERT INTO whatsapp_cost_alerts (user_id, kind, day, sent)
                SELECT user_id, kind, (now() AT TIME ZONE 'Europe/London')::date, sent
                  FROM owed
                ON CONFLICT DO NOTHING
                RETURNING user_id, kind, sent
            )
            SELECT c.user_id, c.kind, c.sent,
                   EXISTS (
                       SELECT 1 FROM user_entitlement e
                        WHERE e.user_id = c.user_id AND e.priced AND e.live
                   ) AS paid
              FROM claimed c
             ORDER BY c.user_id, c.kind
            """
        ).fetchall()
    )

def drop_stale_whatsapp(conn: Conn, *, days: int = 2) -> int:
    """Keep the WhatsApp backlog to the last `days`, whenever it is collected.

    A rolling window, measured from now rather than from whenever the person
    replies. Run on every drain, so the queue never holds more than two days of
    listings at any moment: somebody who answers the check-in after ten hours
    gets those ten hours, and somebody who answers after three weeks gets the
    last two days — not the three weeks.

    That is the point. A flat listed a week ago has been let, and sending it
    anyway is a wall of noise where every line is a message Meta charges for.
    """

    gone = conn.execute(
        """
        UPDATE notifications
           SET status = 'skipped', error = 'stale: held longer than the backlog window'
         WHERE channel = 'whatsapp' AND status = 'queued'
           AND created_at < now() - make_interval(days => %s)
        RETURNING id
        """,
        (days,),
    ).fetchall()
    return len(gone)

def closing_windows(conn: Conn, *, minutes: int = 5, limit: int = 200) -> list[Row]:
    """WhatsApp numbers whose 24-hour window shuts within `minutes`.

    Asked late on purpose — see `CHECKIN_MINUTES`. Late means the question is
    the newest thing in the conversation, and `claim_queued` keeps it that way
    by holding alerts from this moment until it is answered.

    One question per window: `window_asked_at` is compared against
    `last_inbound_at`, so a window that has since been reopened makes the old
    answer stale by itself and nothing has to be cleaned up.

    Only people with an active subscription, because the question is whether to
    carry on receiving — there is nothing to carry on with otherwise.
    """

    return list(
        conn.execute(
            """
            SELECT uc.user_id, uc.address, uc.last_inbound_at, s.criteria
              FROM user_channels uc
              JOIN users u         ON u.id = uc.user_id AND u.status = 'active'
              JOIN subscriptions s ON s.user_id = uc.user_id AND s.active
             WHERE uc.channel = 'whatsapp'
               AND uc.is_primary
               AND uc.verified_at IS NOT NULL
               AND uc.last_inbound_at IS NOT NULL
               -- Inside the window, but with less than `minutes` of it left.
               AND uc.last_inbound_at <=
                   now() - interval '24 hours' + make_interval(mins => %(minutes)s)
               AND uc.last_inbound_at > now() - interval '24 hours'
               AND (uc.window_asked_at IS NULL
                 OR uc.window_asked_at < uc.last_inbound_at)
             ORDER BY uc.last_inbound_at
             LIMIT %(limit)s
            """,
            {"minutes": minutes, "limit": limit},
        ).fetchall()
    )

def mark_window_asked(conn: Conn, user_id: int) -> None:
    """Record that this window's question has been asked.

    Also what holds the alerts back for the last minutes of the window — see
    `claim_queued`. So it is written immediately before the send and undone by
    `unmark_window_asked` if that send fails, rather than left standing: a
    question that was never delivered must not be the reason nothing else is.
    """

    conn.execute(
        """
        UPDATE user_channels SET window_asked_at = now()
         WHERE user_id = %s AND channel = 'whatsapp'
        """,
        (user_id,),
    )

def unmark_window_asked(conn: Conn, user_id: int) -> None:
    """Undo `mark_window_asked` after a send that failed.

    Without this a failed check-in is indistinguishable from an answered one:
    the question is never asked again, the alerts stay held, and with no
    templates the person cannot be reached at all until they write in
    themselves. Clearing it lets the next run try again while the window is
    still open, and releases the queue if it does not.
    """

    conn.execute(
        """
        UPDATE user_channels SET window_asked_at = NULL
         WHERE user_id = %s AND channel = 'whatsapp'
        """,
        (user_id,),
    )

def claim_plan_notices(conn: Conn, *, limit: int = 200) -> list[Row]:

    return list(
        conn.execute(
            """
            WITH due AS (
                SELECT u.id AS user_id, u.plan, u.plan_until,
                       e.alert_allowance, e.alerts_used,
                       -- Out of days outranks out of alerts: when both are
                       -- true the period is over either way, and "you have
                       -- used all 900" would be answering the smaller
                       -- question. Otherwise a spent allowance is its own
                       -- stage, and it can fire with weeks still on the clock
                       -- — which is why the window below is not only "within
                       -- a day of the end".
                       CASE
                           WHEN u.plan_until <= now()                      THEN 'expired'
                           WHEN e.out_of_alerts                            THEN 'spent'
                           WHEN u.plan_until <= now() + interval '1 hour'  THEN 'hour'
                           ELSE 'day'
                       END AS stage
                  FROM users u
                  JOIN user_entitlement e ON e.user_id = u.id
                 WHERE u.status = 'active'
                   AND u.plan_until IS NOT NULL
                   AND (u.plan_until <= now() + interval '1 day'
                     OR e.out_of_alerts)
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
                   d.alert_allowance, d.alerts_used,
                   -- What the alerts fall back to, so the notice can say so
                   -- rather than guessing. Read from the entitlement view and
                   -- not from the lapsed plan directly: the fallback depends on
                   -- the channel — a fifth of the listings on Telegram, nothing
                   -- on WhatsApp — and a notice promising a share that is not
                   -- delivered is worse than one that promises nothing.
                   e.delivery_share AS lapsed_share
              FROM claimed c
              JOIN due d              ON d.user_id = c.user_id
              JOIN user_entitlement e ON e.user_id = c.user_id
              JOIN user_channels uc   ON uc.user_id = c.user_id AND uc.is_primary
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
                       ) AS withheld
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
            SELECT d.user_id, d.matched, d.sent, d.withheld,
                   uc.channel, uc.address, uc.last_inbound_at,
                   -- Paid means a plan that costs money and has not run out —
                   -- of days or of alerts. A live trial is not paid: it is the
                   -- thing the button is there to convert.
                   (e.priced AND e.live) AS paid,
                   e.delivery_share
              FROM claimed c
              JOIN due d              ON d.user_id = c.user_id
              JOIN user_entitlement e ON e.user_id = c.user_id
              JOIN user_channels uc   ON uc.user_id = c.user_id AND uc.is_primary
             ORDER BY d.user_id
            """,
            (limit,),
        ).fetchall()
    )

def claim_queued(conn: Conn, *, limit: int, max_attempts: int) -> list[Row]:
    """The next batch of messages that can actually be sent, oldest first.

    What "can be sent" means is `queued_notifications.held` and lives in the
    database — see 0055. It used to live here, in three `AND NOT (...)` blocks
    with their limits as Python defaults, which left the admin dashboard no way
    to tell a message held on purpose from a delivery that had died.

    Held rows are not claimed, only skipped: claiming spends one of five
    attempts, and a message waiting for a photograph, for a window to reopen or
    for midnight has not failed at anything.
    """

    claimed = conn.execute(
        """
        WITH due AS (
            SELECT n.id FROM notifications n
             WHERE n.status = 'queued' AND n.attempts < %(max_attempts)s
               -- Correlated, not `id IN (SELECT ...)`: this way the reason is
               -- worked out for the rows being considered rather than for the
               -- whole backlog, and `held` stops at the first rule that bites.
               AND NOT EXISTS (
                   SELECT 1 FROM queued_notifications q
                    WHERE q.id = n.id AND q.held IS NOT NULL
               )
             ORDER BY n.created_at
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
                   (SELECT m.wa_media_id FROM source_messages m
                     WHERE m.listing_id = n.listing_id AND m.wa_media_id IS NOT NULL
                     LIMIT 1) AS wa_media_id,
                   -- The same answer `active_subscriptions` reads, from the same
                   -- view, so the share named in the message cannot disagree
                   -- with the share that decided what was queued.
                   e.delivery_share
              FROM notifications n
              JOIN listings l         ON l.id = n.listing_id
              JOIN sources src        ON src.key = l.source_key
              JOIN users u            ON u.id = n.user_id
              JOIN user_entitlement e ON e.user_id = u.id
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
    """New filters that have not had their starter batch considered yet.

    `seeded_at` is the latch, so every new subscription passes through here
    exactly once — which is also what makes this the one place that can tell
    the ops chat somebody has signed up. No address is selected: the chat is
    told who and what, never how to reach them.
    """

    return list(
        conn.execute(
            """
            SELECT s.id, s.user_id, s.criteria, uc.channel,
                   u.plan, u.plan_until,
                   -- How many filters this account has ever had, so the ops
                   -- line can say "signed up" rather than guess. The sign-up
                   -- form writes a new subscription every time it is used, so
                   -- somebody coming back to change their search arrives here
                   -- looking exactly like a new customer.
                   (SELECT count(*) FROM subscriptions s2
                     WHERE s2.user_id = s.user_id) AS filters
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
