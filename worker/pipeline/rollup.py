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
                SELECT generate_series(
                    current_date - (%(days)s::int - 1), current_date, interval '1 day'
                )::date AS day
            )
            INSERT INTO daily_stats (
                day, alerts_sent, alerts_failed, alerts_withheld,
                messages_stored, messages_unread, listings_added,
                visitors, signups, revenue_pence, computed_at
            )
            SELECT
                span.day,
                (SELECT count(*) FROM notifications n
                  WHERE n.status = 'sent' AND n.sent_at::date = span.day),
                (SELECT count(*) FROM notifications n
                  WHERE n.status = 'failed' AND n.created_at::date = span.day),
                (SELECT count(*) FROM notifications n
                  WHERE n.status = 'skipped' AND n.error = 'share'
                    AND n.created_at::date = span.day),
                (SELECT count(*) FROM source_messages m
                  WHERE m.stored_at::date = span.day),
                (SELECT count(*) FROM source_messages m
                  WHERE m.status = 'unparseable' AND m.stored_at::date = span.day),
                (SELECT count(*) FROM listings l
                  WHERE l.first_seen_at::date = span.day),
                (SELECT count(*) FROM site_visits v WHERE v.day = span.day),
                (SELECT count(*) FROM users u WHERE u.created_at::date = span.day),
                (SELECT coalesce(sum(p.amount_pence), 0) FROM payments p
                  WHERE p.created_at::date = span.day),
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
        districts = conn.execute(
            """
            INSERT INTO district_days (day, district, listings, computed_at)
            SELECT (l.first_seen_at AT TIME ZONE 'Europe/London')::date AS day,
                   l.postcode_district,
                   count(*),
                   now()
              FROM listings l
             WHERE l.postcode_district IS NOT NULL
               AND (l.first_seen_at AT TIME ZONE 'Europe/London')::date
                   > (now() AT TIME ZONE 'Europe/London')::date
                     - make_interval(days => %(days)s::int)
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
