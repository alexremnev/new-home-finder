from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
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
           SET last_seen_at = now(),
               miss_count = 0,
               -- The feed and the scrapers write one row per flat for
               -- Rightmove and Zoopla, so whichever saw it first owned every
               -- field. The feed states no type at all — its Bedrooms field
               -- says "2 Bedrooms" and stops — and the scraper reads
               -- "Apartment" off the portal minutes later, but hit this
               -- clause and threw it away. A feed-first row therefore stayed
               -- untyped for ever: no type on the alert, and it answered
               -- every property-type filter.
               --
               -- Blanks only. A reader that stated a type stated it from the
               -- page it read, and the second reader is not better informed
               -- about the same flat.
               property_type = COALESCE(listings.property_type, EXCLUDED.property_type),

               -- The same rule, for the same reason, for the two fields the
               -- filters ask about that a search card most often withholds.
               -- Without these the clause above was discarding them twice
               -- over: a Zoopla or Rightmove row the feed had written first
               -- kept the feed's silence for ever, and so did a row whose own
               -- scraper read the card on one run and the listing page — where
               -- both fields actually are — on the next. See
               -- `zoopla.furnishing_on` and `rightmove.details_on`.
               --
               -- 'unknown' is this column's blank, not NULL: it is NOT NULL
               -- DEFAULT 'unknown' and a CHECK constraint keeps it one of the
               -- four words, so NULLIF is what makes COALESCE mean the same
               -- thing here as it does above.
               furnished = COALESCE(
                   NULLIF(listings.furnished, 'unknown'),
                   NULLIF(EXCLUDED.furnished, 'unknown'),
                   'unknown'
               ),
               -- NULL is the blank here and it means "not stated". Never
               -- written back to NULL, and never to False: see
               -- `rightmove.as_listing` for why a refusal nobody made is worse
               -- than silence.
               pets_allowed = COALESCE(listings.pets_allowed, EXCLUDED.pets_allowed)
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
            # `duplicate_of` comes along as a value rather than as a WHERE
            # clause: it used to be filtered here, which made suppressing the
            # second portal's copy of a flat unconditional for everybody. It is
            # a subscriber's choice now (`send_duplicates`), so the matcher
            # decides per filter — see `_check_duplicate`.
            f"SELECT id, first_seen_at, duplicate_of, {columns} FROM listings "
            "WHERE id = ANY(%s) "
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
                   -- messages. Both are answered by `user_entitlement`; see
                   -- 0057 for why this is not written out here any more.
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

@dataclass(frozen=True)
class Sighted:
    """What the database already holds for a batch of one portal's own ids.

    Two answers from one round trip, because a sweep needs both and they are
    not the same question. `ids` is every listing already stored, so a sighting
    can be recorded against the right row. `known` is the subset this reader
    should *not* treat as news — see `sighted_by` for why those differ.
    """

    #: external_id -> listing id, for every one of them already stored.
    ids: dict[str, int]

    #: The ones this reader has nothing left to learn from. Everything else in
    #: the batch is news to it, whether or not some other reader stored it.
    known: frozenset[str]


def sighted_by(
    conn: Conn,
    *,
    source_key: str,
    reader: str,
    external_ids: list[str],
    catch_up: timedelta,
) -> Sighted:
    """What a reader already knows about these ids.

    ── why this is not "is there a row in `listings`" ───────────────────────

    It was, and that was load-bearing in a way nobody chose. The Telegram feed
    stores a listing under the portal that hosts it — `source_key =
    'rightmove'`, the portal's own `external_id` — so a flat the feed posted a
    minute ago is already a row by the time the scraper's five-minute timer
    comes round. Asked "is there a row", the scraper answered yes, filed the
    listing as `already_known`, and never reached `_announceable`.

    That was invisible while the feed did the announcing: whichever path got
    there first sent the alert and `notifications` UNIQUE (user_id, listing_id)
    discarded the second. Switch the feed's delivery off and the same race
    becomes a hole — the feed wins it, because it is a push and the scrapers
    poll, and then nobody announces at all.

    So the question is "has *this reader* seen it", which is what
    `listing_sightings` records and what 0052 was written to make answerable.

    ── why `catch_up`, and not sightings alone ──────────────────────────────

    On sightings alone, every listing stored by any other path and never seen
    by this one becomes news again at once — for Rightmove and Zoopla that
    announces nothing, because they are dated and `_announceable` still holds
    them to `first_listed > settled_at`, but OpenRent is undated and a settled
    district would announce its whole backlog in one run. It would also be
    fetched: OpenRent spends a redirect lookup and a 300KB page per id it
    considers new, and a few hundred of those exhausts the run's budget, which
    leaves the district incomplete, which stops it being swept, which voids its
    watch after a day. A blackout, from a change meant to close a gap.

    A listing stored more than `catch_up` ago is therefore left alone. What the
    scraper can still catch up on is the last few hours, which is the only part
    where the race against the feed is actually in play. Everything older is
    somebody else's history.

    Backfilling `listing_sightings` from `listings` would have done the same
    job and was rejected: those are the rows the feed-versus-scraper comparison
    is read from, and inventing a sighting for every listing ever stored would
    make `feed_first` and `median_lead_secs` say whatever we had written into
    them. See 0052 on why nothing was backfilled there either.
    """

    if not external_ids:
        return Sighted(ids={}, known=frozenset())

    rows = conn.execute(
        """
        SELECT l.external_id,
               l.id,
               (g.listing_id IS NOT NULL) AS seen,
               (l.first_seen_at < now() - %(catch_up)s) AS stale
          FROM listings l
          LEFT JOIN listing_sightings g
                 ON g.listing_id = l.id AND g.reader = %(reader)s
         WHERE l.source_key = %(source_key)s
           AND l.external_id = ANY(%(ids)s)
        """,
        {
            "source_key": source_key,
            "reader": reader,
            "ids": external_ids,
            "catch_up": catch_up,
        },
    ).fetchall()

    return Sighted(
        ids={str(row["external_id"]): int(row["id"]) for row in rows},
        known=frozenset(
            str(row["external_id"]) for row in rows if row["seen"] or row["stale"]
        ),
    )

def announces(conn: Conn, source_key: str) -> bool:
    """Whether what this source finds may be queued for a subscriber.

    The sibling of `enabled_sources`, and deliberately not the same question.
    `enabled` is about whether we read a site at all — cost, politeness, a
    circuit breaker. This is about whether what we read reaches anybody, and
    the Telegram feed is the first source to want different answers: its
    subscription is ending, and the only way to find out whether the scrapers
    cover what it covers is to keep reading it while it sends nothing. See
    0060.

    A source key with no row answers False rather than True. Nothing should
    reach a subscriber under a key the reference table has never heard of, and
    `listings.source_key` has a foreign key to it, so this can only be a
    caller's mistake — the fail-closed answer is the one that makes it visible
    as silence rather than as mystery alerts.
    """

    row = conn.execute(
        "SELECT announces FROM sources WHERE key = %s", (source_key,)
    ).fetchone()
    return bool(row["announces"]) if row else False


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


def seen_ids(
    conn: Conn, *, source_key: str, external_ids: list[str]
) -> dict[str, str | None]:
    """Which of these ids this source has already resolved, and to where.

    Separate from `sighted_by` because a listing we decided not to store
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


def costly_whatsapp(conn: Conn) -> list[Row]:
    """WhatsApp subscribers who have crossed a volume threshold unannounced.

    One row per alert still owed, with `kind` saying which threshold and
    `paid` saying whether delivery was stopped or merely noted. The insert into
    `whatsapp_cost_alerts` is what makes it "still owed": drain runs every five
    minutes, and without it crossing a line would be announced twelve times an
    hour.

    Two thresholds, and they are not the same kind of fact:

    `daily` — the thirty-a-day cap. Ours, applied to anybody not on a live
    paid plan, and it releases itself at midnight. Delivery is held, not lost.

    `allowance` — the messages included in a paid period are used up, so the
    period has ended with days still on the clock. Nothing releases that but
    paying again. The subscriber is told by `claim_plan_notices`; this is the
    half that reaches us, because a paying subscriber going quiet three weeks
    early is something to know before they ask. Once per period, keyed on
    `plan_until` — see 0067.
    """

    return list(
        conn.execute(
            """
            WITH tally AS (
                -- The ledger, not `notifications`: the cap is on what a
                -- subscriber costs, every message is billed, and only the
                -- listings were ever in `notifications`. See 0063.
                --
                -- `ever` went with the lifetime alert 0057 removed. A single
                -- aggregate over the whole table, computed every minute, for
                -- a column nothing read.
                SELECT w.user_id,
                       count(*) AS today
                  FROM wa_messages w
                 WHERE w.user_id IS NOT NULL
                   AND (w.sent_at AT TIME ZONE 'Europe/London')::date
                     = (now() AT TIME ZONE 'Europe/London')::date
                 GROUP BY w.user_id
            ),
            owed AS (
                -- The threshold is the cap itself: the alert says "we have
                -- stopped delivering to this person", so reading it from
                -- anywhere else would let it announce the wrong thing.
                --
                -- The lifetime figure that used to sit beside this is gone. It
                -- was a proxy for a bound on what one subscriber can cost, and
                -- 0057 made that a real one: `plans.message_allowance`, which
                -- ends the period and tells the person rather than telling us.
                SELECT t.user_id, 'daily' AS kind,
                       (now() AT TIME ZONE 'Europe/London')::date AS day,
                       t.today AS sent, NULL::integer AS allowance
                  FROM tally t
                 WHERE t.today >= (SELECT wa_daily_cap FROM delivery_limits)

                 UNION ALL

                -- The other threshold, and the one that costs a paying
                -- subscriber something: the messages included in their period
                -- are used up, so their alerts stop with days still on the
                -- clock. The daily cap is a thing we do to a non-payer and
                -- releases itself at midnight; this does not, and the first we
                -- would otherwise hear of it is them asking why it went quiet.
                --
                -- `priced` only. A trial has an allowance too and runs out the
                -- same way, but a trial ending is the product working and not
                -- news — and there are as many of those a week as there are
                -- signups.
                --
                -- `day` holds `plan_until`, which is what makes this once per
                -- period rather than once per day: the event belongs to the
                -- period. Without it a month that ran out in its first week
                -- would be announced again every day for three weeks. See
                -- 0067.
                SELECT e.user_id, 'allowance' AS kind,
                       u.plan_until::date AS day,
                       e.messages_used AS sent, e.message_allowance AS allowance
                  FROM user_entitlement e
                  JOIN users u ON u.id = e.user_id
                 WHERE e.priced
                   AND e.out_of_messages
                   AND u.plan_until IS NOT NULL
            ),
            claimed AS (
                INSERT INTO whatsapp_cost_alerts (user_id, kind, day, sent)
                SELECT user_id, kind, day, sent FROM owed
                ON CONFLICT DO NOTHING
                RETURNING user_id, kind, sent
            )
            SELECT c.user_id, c.kind, c.sent, o.allowance,
                   -- Which plan ran out, so the alert names it. "subscriber
                   -- #12 has used all 200" leaves the reader working out
                   -- whether that is the week or a misconfigured month.
                   (SELECT u.plan FROM users u WHERE u.id = c.user_id) AS plan,
                   EXISTS (
                       SELECT 1 FROM user_entitlement e
                        WHERE e.user_id = c.user_id AND e.priced AND e.live
                   ) AS paid
              FROM claimed c
              JOIN owed o ON o.user_id = c.user_id AND o.kind = c.kind
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

    And only people who will still be receiving something once the window
    reopens, which is the rest of the WHERE clause. "Tap below and the alerts
    carry on" has to be true when it is read:

      * `live` and a share above nought is "alerts are being delivered at all".
        A lapsed plan falls back to nothing on WhatsApp — see 0058 — so the
        window is worth nothing to them, and a spent allowance is the same
        answer by the other half of `live`.
      * the hour's grace is so that this never follows the notice saying the
        plan is ending, on the same conversation. The notice itself goes out
        `NOTICE_LEAD_MINUTES` before the end, and the grace is far wider than
        that on purpose: inside the last hour the plan notice is the message
        that is due, and "tap below and the alerts carry on" contradicts it
        whether it arrives before or after.

    Which is what takes the WhatsApp trial out: it runs a single day from the
    first alert, so its end always falls inside the window it was started in
    and within the hour before that window shuts. A trial-length change does
    not reopen the hole — a trial with two days left on it is somebody who has
    alerts to carry on with, and the rule says so rather than naming the plan.
    """

    return list(
        conn.execute(
            """
            SELECT uc.user_id, uc.address, uc.last_inbound_at, s.criteria
              FROM user_channels uc
              JOIN users u            ON u.id = uc.user_id AND u.status = 'active'
              JOIN subscriptions s    ON s.user_id = uc.user_id AND s.active
              JOIN user_entitlement e ON e.user_id = uc.user_id
             WHERE uc.channel = 'whatsapp'
               AND uc.is_primary
               AND uc.verified_at IS NOT NULL
               AND uc.last_inbound_at IS NOT NULL
               -- Something to carry on with, and not about to be told the
               -- opposite by the plan notice.
               AND e.live
               AND e.delivery_share > 0
               AND (u.plan_until IS NULL
                 OR u.plan_until > now() + interval '1 hour')
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

# How long before a plan ends the one warning goes out.
#
# Five minutes, not the hour it was. The warning is the message that carries
# the payment button, and a trial is decided on when it is actually running
# out: an hour ahead it arrives next to alerts that are still arriving, which
# is a reason to come back to it later and not a reason to tap. `drain` runs
# every minute, so five minutes is four or five chances to claim the stage
# rather than one.
#
# On WhatsApp this is also the last message that can be delivered at all. The
# 24-hour window and a trial's `plan_until` run out together — the trial starts
# when the messenger is connected, which is an inbound message — so the
# `expired` notice that follows needs the approved template in
# `WA_NOTICE_TEMPLATE` to leave the building, and without it is dropped. Five
# minutes still sits inside the window; much less would not.
NOTICE_LEAD_MINUTES = 5

def claim_plan_notices(
    conn: Conn, *, limit: int = 200, lead_minutes: int = NOTICE_LEAD_MINUTES
) -> list[Row]:

    return list(
        conn.execute(
            """
            WITH candidate AS (
                SELECT u.id AS user_id, u.plan, u.plan_until,
                       e.message_allowance, e.messages_used,
                       -- Whether this account has already had its one free
                       -- extra day, which `owed_a_free_day` reads together
                       -- with `messages_used` to decide whether the warning
                       -- below is followed by the offer of another. Here and
                       -- not asked for separately because the notice is the
                       -- only thing that offers it, and a second query would
                       -- be a second answer to "is this person eligible".
                       u.trial_gift_at,
                       -- Out of days outranks out of messages: when both are
                       -- true the period is over either way, and "you have
                       -- used all 900" would be answering the smaller
                       -- question. Otherwise a spent allowance is its own
                       -- stage, and it can fire with weeks still on the clock
                       -- — which is why the WHERE below is not only "close to
                       -- the end"
                       --
                       -- One warning before the end, `NOTICE_LEAD_MINUTES`
                       -- out. There was a second a day out as well, and the
                       -- one that remains used to be an hour out; two messages
                       -- about one ending is one more than anybody asked for,
                       -- and the one worth keeping is the one that arrives
                       -- while the decision is in front of them.
                       --
                       -- Still keyed 'hour'. It is a claim key in a table whose
                       -- rows go back to when that was the lead, and a counter
                       -- name in the stage log; renaming it would buy a truer
                       -- word and cost the record. 'day' is left in the stage
                       -- constraint for the same reason.
                       CASE
                           WHEN u.plan_until <= now() THEN 'expired'
                           WHEN e.out_of_messages     THEN 'spent'
                           ELSE 'hour'
                       END AS stage
                  FROM users u
                  JOIN user_entitlement e ON e.user_id = u.id
                  -- For the window below. LEFT so that somebody with no
                  -- primary channel is still a candidate on the strength of
                  -- their plan alone, exactly as before this join existed.
                  LEFT JOIN user_channels uc
                         ON uc.user_id = u.id AND uc.is_primary
                 WHERE u.status = 'active'
                   AND u.plan_until IS NOT NULL
                   -- The window and the CASE are one rule: every row here has
                   -- `plan_until` either behind it or within two leads of now,
                   -- so the ELSE above can only be the warning before the end.
                   AND (u.plan_until <= now() + make_interval(mins => %(lead)s)
                     OR e.out_of_messages
                     -- Or because on WhatsApp the deadline is the window
                     -- shutting and not the plan ending. Outside the 24 hours
                     -- there is no route at all without an approved template,
                     -- which is why the `expired` notice is never delivered
                     -- there — so a plan whose end lands with the window, or
                     -- within a lead of it, is warned while somebody is still
                     -- able to hear it. The one-day WhatsApp trial is exactly
                     -- that shape: it starts on the message that connected the
                     -- number, so its last minutes and the window's are the
                     -- same minutes, and the notice sent now is the last one
                     -- that will arrive.
                     --
                     -- Bounded by `plan_until` so this is not a daily "your
                     -- plan is ending" to a subscriber with three weeks left.
                     -- A window shutting on somebody who has alerts to carry
                     -- on with is the check-in's business — `closing_windows`
                     -- — and not this one's.
                     OR (uc.channel = 'whatsapp'
                         AND uc.last_inbound_at > now() - interval '24 hours'
                         AND uc.last_inbound_at <= now() - interval '24 hours'
                                                 + make_interval(mins => %(lead)s)
                         AND u.plan_until <= uc.last_inbound_at
                                           + interval '24 hours'
                                           + make_interval(mins => %(lead)s)))
            ),
            due AS (
                -- The limit has to count people who still need telling, which
                -- is why this is a second step rather than one more line in
                -- the WHERE above.
                --
                -- `plan_until` ascending puts the oldest expiry first, and an
                -- account that has lapsed keeps matching the WHERE for ever —
                -- so every account that ever expired sat at the front of the
                -- batch, claimed, returning nothing. Past two hundred of them
                -- the INSERT below had nothing left to claim and the notice
                -- for somebody whose plan ends today was never produced at
                -- all. The ON CONFLICT is still the race guard; this is what
                -- keeps the batch about the people it is for.
                SELECT c.* FROM candidate c
                 WHERE NOT EXISTS (
                     SELECT 1 FROM plan_notices pn
                      WHERE pn.user_id = c.user_id
                        AND pn.plan_until = c.plan_until
                        -- One notice per period about that period having
                        -- ended, whichever of the two ways it ended.
                        --
                        -- `spent` and `expired` were separate claims, and a
                        -- subscriber who used their allowance early got both:
                        -- "the alerts have stopped" when the messages ran out,
                        -- and the same sentence again when the days did, weeks
                        -- later. Two names for one event, and the second of
                        -- them is the marketing template — so the duplicate
                        -- was also the expensive one.
                        --
                        -- Which of the two arrives is whichever came first,
                        -- and in practice that is `spent`: an allowance can
                        -- only be used up by a message, a message can only be
                        -- sent inside the 24-hour window, and the drain is
                        -- five minutes behind — so the window is still open
                        -- and the notice goes out as free text with a button
                        -- on it. The template is what is left for the ordinary
                        -- case of a plan that simply ran out of days.
                        --
                        -- `delivered_at`, and not the row's existence, because
                        -- a row here is a claim: it is inserted before the
                        -- send and kept even when the send failed for good.
                        -- Deduplicating on the claim would have thrown away
                        -- what the two stages were accidentally providing — a
                        -- notice nobody could be told at `spent` got a second,
                        -- independent go when the days ran out. So the
                        -- question is whether they were told, not whether we
                        -- tried. See 0066.
                        --
                        -- `hour` keeps its own claim. It is the warning before
                        -- the end and not a second account of it, so it is not
                        -- a duplicate of anything. And the same-stage rule
                        -- asks nothing about delivery: that one is the guard
                        -- against a drain every five minutes repeating itself,
                        -- and it has to hold whatever the send did.
                        AND (pn.stage = c.stage
                          OR (pn.stage IN ('expired', 'spent')
                         AND c.stage IN ('expired', 'spent')
                         AND pn.delivered_at IS NOT NULL))
                 )
                 ORDER BY c.plan_until
                 LIMIT %(limit)s
            ),
            claimed AS (
                INSERT INTO plan_notices (user_id, stage, plan_until, plan)
                SELECT user_id, stage, plan_until, plan FROM due
                ON CONFLICT (user_id, stage, plan_until) DO NOTHING
                RETURNING user_id, stage, plan_until, plan
            )
            SELECT c.user_id, c.stage, c.plan_until, c.plan, uc.channel, uc.address,
                   -- When they last wrote in, because on WhatsApp that is what
                   -- decides whether this notice can be a sentence with a
                   -- button on it or cannot be sent at all. Every other send
                   -- in the outbox carries it; this one did not, so
                   -- `window_open(None)` was false for everybody and the whole
                   -- job took the closed-window path — the hour warning,
                   -- which has no template, was dropped even for the trial
                   -- whose window was still open.
                   uc.last_inbound_at,
                   d.message_allowance, d.messages_used, d.trial_gift_at,
                   -- What the alerts fall back to, so the notice can say so
                   -- rather than guessing. Read from the entitlement view and
                   -- not from the lapsed plan directly: the fallback depends on
                   -- the channel — the lapsed tier's share of the listings on
                   -- Telegram, nothing on WhatsApp — and a notice promising a
                   -- share that is not delivered is worse than one that
                   -- promises nothing.
                   --
                   -- `lapsed_share`, not `delivery_share`: three of these four
                   -- stages fire while the plan is still running, and the share
                   -- in force then is the plan's own. Reading it told a live
                   -- trial it was on a hundred per cent, from which the notice
                   -- concluded there was nothing to fall back to and said the
                   -- alerts would stop. See 0058.
                   e.lapsed_share
              FROM claimed c
              JOIN due d              ON d.user_id = c.user_id
              JOIN user_entitlement e ON e.user_id = c.user_id
              JOIN user_channels uc   ON uc.user_id = c.user_id AND uc.is_primary
            """,
            {"lead": lead_minutes, "limit": limit},
        ).fetchall()
    )

def release_plan_notice(
    conn: Conn, user_id: int, stage: str, plan_until: datetime | None
) -> None:
    """Give a claimed notice back after a send that could still succeed.

    `claim_plan_notices` claims by inserting, so the row is what stops a drain
    every minute saying the same thing sixty times an hour. The cost is that a
    send which failed is a notice nobody ever hears: the claim stands and the
    stage never comes round again.

    Which is worth undoing for a failure of the moment — a timeout, a 5xx, a
    rate limit — and not for a shut WhatsApp window with nothing that may be
    sent into it. There a refusal is not a delay, it is the absence of a
    route, and handing the row back would ask Meta the same impossible
    question every five minutes until the account is deleted.
    `reachable_later` in the outbox draws that line; this only does what it is
    told.

    A claim kept is not a message lost. The hour warning is the one that
    carries the payment button, and with `last_inbound_at` now reaching the
    notifier it goes out on the first attempt whenever the window is open.
    When it is not, `plan_until` passes and the stage becomes `expired` by
    itself — a different stage, a fresh claim, and another look at the window.
    """

    conn.execute(
        """
        DELETE FROM plan_notices
         WHERE user_id = %s AND stage = %s AND plan_until = %s
        """,
        (user_id, stage, plan_until),
    )

def mark_plan_notice_delivered(
    conn: Conn, user_id: int, stage: str, plan_until: datetime | None
) -> None:
    """Record that a claimed notice actually reached somebody.

    The row was written before the send, as the claim that keeps a drain every
    five minutes from repeating itself, and it is kept even when the send
    failed for good. So its existence says "this was attempted" and nothing
    more — which is enough for the stage's own idempotency and not enough for
    the one question `claim_plan_notices` now asks across stages: has this
    period already been announced?

    `spent` and `expired` are two names for one event, and only one message
    about it should go out. Deciding that on the claim would mean a notice
    that could not be delivered at `spent` silently cancelled the `expired`
    one as well, so a period that ended would be announced to nobody. See
    0066.
    """

    conn.execute(
        """
        UPDATE plan_notices SET delivered_at = now()
         WHERE user_id = %s AND stage = %s AND plan_until = %s
           AND delivered_at IS NULL
        """,
        (user_id, stage, plan_until),
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
                   -- of days or of messages. A live trial is not paid: it is
                   -- the thing the button is there to convert.
                   (e.priced AND e.live) AS paid,
                   e.delivery_share
              FROM claimed c
              JOIN due d              ON d.user_id = c.user_id
              JOIN user_entitlement e ON e.user_id = c.user_id
              JOIN user_channels uc   ON uc.user_id = c.user_id AND uc.is_primary
             -- Not on WhatsApp to somebody who is receiving nothing. Every
             -- message there is billed (0063), a finished plan falls back to
             -- no listings at all on that channel (0058), and the digest is
             -- the one message in the system nobody asked for — so this was a
             -- nightly charge, for ever, to tell a lapsed subscriber that
             -- nothing had been sent to them. The same test `closing_windows`
             -- uses, for the same reason.
             --
             -- The claim above is still written, so this is "not sent" and not
             -- "tried again every minute until midnight".
             WHERE uc.channel <> 'whatsapp' OR (e.live AND e.delivery_share > 0)
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
    conn: Conn,
    notification_id: int,
    *,
    provider_msg_id: str | None,
    cost_micros: int = 0,
    billed_as: str = "service",
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

    # Meta bills every message, so every message goes in the ledger — see
    # 0063. Done here rather than in the caller because this is the one place
    # a delivered alert is recorded, and a meter that has to be remembered
    # separately is a meter that will one day be forgotten on a new path.
    #
    # The channel is read from the row rather than passed in: the caller is
    # `_apply`, which is channel-blind by design, and the alternative is a
    # parameter that exists only to be threaded through it.
    conn.execute(
        """
        INSERT INTO wa_messages
               (user_id, kind, notification_id, provider_msg_id, billed_as,
                cost_micros, sent_at)
        SELECT n.user_id, 'listing', n.id, n.provider_msg_id, %(billed_as)s,
               (SELECT CASE
                            WHEN %(billed_as)s = 'utility'
                                 THEN ps.wa_template_micros
                            WHEN %(billed_as)s = 'marketing'
                                 THEN ps.wa_marketing_micros
                            ELSE ps.wa_service_micros
                       END
                  FROM plan_settings ps),
               n.sent_at
          FROM notifications n
         WHERE n.id = %(id)s AND n.channel = 'whatsapp'
        ON CONFLICT (notification_id) DO NOTHING
        """,
        {"id": notification_id, "billed_as": billed_as},
    )


def record_wa_message(
    conn: Conn,
    user_id: int | None,
    kind: str,
    *,
    provider_msg_id: str | None = None,
    billed_as: str = "service",
) -> None:
    """One WhatsApp message we sent and will be charged for.

    For everything that is not a listing alert: the evening digest, the
    check-in before the window shuts, the plan notices. Listings arrive
    through `mark_sent`, which has a `notifications` row to hang the ledger
    entry on; these have nothing of the sort, which is exactly why they went
    uncounted until 0063.

    Called only after Meta accepted the message. A refusal is not billed and
    must not look like spend — and it must not eat the subscriber's allowance
    either, which is the worse of the two mistakes.

    `billed_as` is which of Meta's three rates applied, and it comes from the
    notifier rather than from a second look at the window: the plan notice is
    the one message here that can go either way, and it is also the only one
    charged at a template rate — the marketing one, since it carries a payment
    button. Defaulted to `service` because that is what every other path can
    produce. See 0066.

    The price is read inside the statement so that it is the price now, with
    no round trip and nothing cached in a process that runs for days.
    """

    conn.execute(
        """
        INSERT INTO wa_messages
               (user_id, kind, provider_msg_id, billed_as, cost_micros)
        SELECT %(user_id)s, %(kind)s, %(msg_id)s, %(billed_as)s,
               (SELECT CASE
                            WHEN %(billed_as)s = 'utility'
                                 THEN ps.wa_template_micros
                            WHEN %(billed_as)s = 'marketing'
                                 THEN ps.wa_marketing_micros
                            ELSE ps.wa_service_micros
                       END
                  FROM plan_settings ps)
        """,
        {
            "user_id": user_id,
            "kind": kind,
            "msg_id": provider_msg_id,
            "billed_as": billed_as,
        },
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

def listings_missing_type(conn: Conn, *, limit: int = 12) -> list[Row]:
    """Listings still worth asking a portal what they are.

    The feed states no property type, and an untyped listing both reads worse
    in an alert and answers every property-type filter. Same window and same
    ordering as the image queue, for the same reasons — a flat from August is
    nobody's alert — and `type_checked_at` is what stops a page being fetched
    twice.

    Unlike the image queue this does not skip listings that came with a
    picture: whether the feed message carried a photograph says nothing about
    whether it stated a type, and it never does.
    """

    return list(
        conn.execute(
            """
            SELECT l.id, l.url FROM listings l
             WHERE l.property_type IS NULL
               AND l.type_checked_at IS NULL
               AND l.status = 'active'
               AND l.first_seen_at > now() - interval '7 days'
             ORDER BY l.first_seen_at DESC
             LIMIT %s
            """,
            (limit,),
        ).fetchall()
    )

def set_listing_type(conn: Conn, listing_id: int, property_type: str | None) -> None:

    # checked_at either way, as with the picture: "looked and the page named
    # nothing" has to be distinguishable from "not looked at", or every
    # untyped listing is refetched on every run forever. Zoopla would be the
    # whole of that cost, since it answers a plain client with a 403.
    #
    # COALESCE on the value itself because the scrapers write the same row:
    # one may have filled the type between this listing being picked up and
    # this statement running, and a page that named nothing must not blank it.
    conn.execute(
        "UPDATE listings SET property_type = COALESCE(property_type, %s), "
        "type_checked_at = now() WHERE id = %s",
        (property_type, listing_id),
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
    """The pool a new subscriber's starter batch is chosen from.

    Only listings some announcing reader has actually seen. `queue_matches`
    gates a live alert on `sources.announces` at the moment a reader hands its
    catch over (see 0060), and the starter batch is the other way a listing
    reaches somebody — it reads `listings` directly, long after whoever stored
    the row has gone, so the same rule has to be asked of the row itself.

    Without this, muting the feed muted the alerts and not the welcome: every
    new subscriber's first five flats were drawn from everything stored,
    including what only the feed ever found. Which also quietly spoiled the
    measurement the muting exists for — a listing no scraper saw was still
    being sent, so "would this have reached them without the feed" had an
    answer the Coverage panel could not see.

    `listing_sightings` is the record of who saw what (0052) and nothing before
    it was backfilled, so this filter can only be used over a window shorter
    than that table's history. `SEED_DAYS` is three; the table is months old.
    """

    columns = ", ".join(_LISTING_VIEW_COLUMNS)
    return list(
        conn.execute(
            f"""
            SELECT id, first_seen_at, duplicate_of, {columns} FROM listings l
             WHERE status = 'active'
               AND first_seen_at > now() - make_interval(days => %s)
               AND EXISTS (
                     SELECT 1
                       FROM listing_sightings s
                       JOIN sources src ON src.key = s.reader
                      WHERE s.listing_id = l.id
                        AND src.announces
                   )
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
