from __future__ import annotations

from typing import Any

import psycopg

from worker.obs import Run

Row = dict[str, Any]
Conn = psycopg.Connection[Row]

DAYS = 3

def run_rollup(conn: Conn, run: Run, *, days: int = DAYS, dry_run: bool = False) -> int:
    with run.stage("rollup") as stage:
        if dry_run:
            stage.set("suppressed", True)
            return 0

        written = conn.execute(
            """
            WITH span AS (
                -- The day, and the half-open range of instants it covers.
                --
                -- The bounds are what the predicates below compare against,
                -- and the reason is the whole of why this job stopped being
                -- the most expensive thing in the database. `sent_at::date =
                -- span.day` is a function of the column, so no index on
                -- `sent_at` can answer it and every one of the counts under
                -- it was a sequential scan — thirty of them, every two
                -- minutes. `sent_at >= from_ts AND sent_at < to_ts` is the
                -- same question as a range, which an index can answer.
                --
                -- `day::timestamptz` reads the session timezone, exactly as
                -- the `::date` cast it replaces did, so the days are still
                -- cut where they were. That is UTC on this connection and
                -- deliberately not changed here: moving the boundary to
                -- London would silently rewrite every figure in the table.
                SELECT d::date                  AS day,
                       d::date::timestamptz     AS from_ts,
                       (d::date + 1)::timestamptz AS to_ts
                  FROM generate_series(
                      current_date - (%(days)s::int - 1), current_date, interval '1 day'
                  ) AS d
            )
            INSERT INTO daily_stats (
                day, alerts_sent, alerts_failed, alerts_withheld,
                messages_stored, messages_unread, listings_added,
                visitors, signups, revenue_pence,
                wa_messages_sent, wa_cost_micros, computed_at
            )
            SELECT
                span.day,
                (SELECT count(*) FROM notifications n
                  WHERE n.status = 'sent'
                    AND n.sent_at >= span.from_ts AND n.sent_at < span.to_ts),
                (SELECT count(*) FROM notifications n
                  WHERE n.status = 'failed'
                    AND n.created_at >= span.from_ts AND n.created_at < span.to_ts),
                (SELECT count(*) FROM notifications n
                  WHERE n.status = 'skipped' AND n.error = 'share'
                    AND n.created_at >= span.from_ts AND n.created_at < span.to_ts),
                (SELECT count(*) FROM source_messages m
                  WHERE m.stored_at >= span.from_ts AND m.stored_at < span.to_ts),
                (SELECT count(*) FROM source_messages m
                  WHERE m.status = 'unparseable'
                    AND m.stored_at >= span.from_ts AND m.stored_at < span.to_ts),
                -- Originals only. The second portal's copy of a flat is not a
                -- listing the market produced, and counting it made a quiet
                -- day look like a busy one.
                (SELECT count(*) FROM listings l
                  WHERE l.first_seen_at >= span.from_ts
                    AND l.first_seen_at < span.to_ts
                    AND l.duplicate_of IS NULL),
                (SELECT count(*) FROM site_visits v WHERE v.day = span.day),
                (SELECT count(*) FROM users u
                  WHERE u.created_at >= span.from_ts AND u.created_at < span.to_ts),
                (SELECT coalesce(sum(p.amount_pence), 0) FROM payments p
                  WHERE p.created_at >= span.from_ts AND p.created_at < span.to_ts),
                -- What WhatsApp cost that day, in messages and in money. The
                -- ledger they are counted from is purged after two months
                -- (see `purge`), so this is the only lasting record of it —
                -- and the only answer to "what did we spend in March" once
                -- March is gone. Both from the price stamped on each row, so
                -- a later price change does not rewrite a settled month.
                (SELECT count(*) FROM wa_messages w
                  WHERE w.sent_at >= span.from_ts AND w.sent_at < span.to_ts),
                (SELECT coalesce(sum(w.cost_micros), 0) FROM wa_messages w
                  WHERE w.sent_at >= span.from_ts AND w.sent_at < span.to_ts),
                now()
              FROM span
            ON CONFLICT (day) DO UPDATE SET
                alerts_sent     = EXCLUDED.alerts_sent,
                alerts_failed   = EXCLUDED.alerts_failed,
                alerts_withheld = EXCLUDED.alerts_withheld,
                messages_stored = EXCLUDED.messages_stored,
                messages_unread = EXCLUDED.messages_unread,
                listings_added  = EXCLUDED.listings_added,
                visitors        = EXCLUDED.visitors,
                signups         = EXCLUDED.signups,
                revenue_pence   = EXCLUDED.revenue_pence,
                wa_messages_sent = EXCLUDED.wa_messages_sent,
                wa_cost_micros   = EXCLUDED.wa_cost_micros,
                computed_at     = now()
            RETURNING day
            """,
            {"days": days},
        ).fetchall()

        stage.set("days", len(written))

        # The same window, one grain finer: listings per district per day. Kept
        # in the same job because it answers the same question at a different
        # zoom, and because running it here means it inherits the property that
        # matters — a missed run leaves no hole, the next one repairs it.
        #
        # London, not UTC: every date in the admin is London time, and a day
        # that ended at 01:00 in summer would file an evening on tomorrow.
        #
        # The grouping is still on the London date and the filter no longer is,
        # which is the point. `(first_seen_at AT TIME ZONE 'Europe/London')::date
        # > ...` had to be evaluated for every row in the table to find the
        # three days this writes, so the cheapest statement in the job was a
        # full scan of the largest table in the database — 720 of them a day.
        # `first_seen_at >= from_ts` asks for the same rows as a range on the
        # stored value, which `listings_added_at` answers directly.
        #
        # `from_ts` is London midnight of the oldest day included. The old
        # predicate was strictly greater than `today - days`, so the oldest day
        # in the window is `today - days + 1` and that is what is computed here
        # — the boundary is the same instant it always was.
        districts = conn.execute(
            """
            WITH cutoff AS (
                SELECT (((now() AT TIME ZONE 'Europe/London')::date
                         - make_interval(days => %(days)s::int - 1))
                        AT TIME ZONE 'Europe/London') AS from_ts
            )
            INSERT INTO district_days (day, district, listings, computed_at)
            SELECT (l.first_seen_at AT TIME ZONE 'Europe/London')::date AS day,
                   l.postcode_district,
                   count(*),
                   now()
              FROM listings l CROSS JOIN cutoff
             WHERE l.postcode_district IS NOT NULL
               -- Originals only, for the same reason: a district that appears
               -- on two portals is not a district with twice the stock.
               AND l.duplicate_of IS NULL
               AND l.first_seen_at >= cutoff.from_ts
             GROUP BY 1, 2
            ON CONFLICT (day, district) DO UPDATE
               SET listings    = EXCLUDED.listings,
                   computed_at = now()
            RETURNING day
            """,
            {"days": days},
        ).fetchall()

        stage.set("district_rows", len(districts))
        return len(written)

__all__ = ["DAYS", "run_rollup"]
