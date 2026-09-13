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
        return len(written)

__all__ = ["DAYS", "run_rollup"]
