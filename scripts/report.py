"""Who received what, and whether anything is quietly broken.

Two jobs in one script because they read the same tables and you want them at the
same cadence: a report you can open, and an alert you cannot miss.

    python scripts/report.py                    # write the report, check for trouble
    python scripts/report.py --days 30          # a longer window
    python scripts/report.py --no-alert         # report only, stay silent
    python scripts/report.py --out reports/     # somewhere other than ./reports

── why a file ───────────────────────────────────────────────────────────────

Because a dashboard is a decision and a file is not. A plain text table can be
opened, mailed, diffed between days and kept; when the questions it answers have
settled into the three you actually ask, those three are worth a real dashboard and
the rest can be dropped. Doing it the other way round means building charts for
questions nobody turns out to have.

── what counts as broken ────────────────────────────────────────────────────

Only silences. Every check here fires on *absence* — no messages read, none
parsed, a queue that stopped draining, a source that has not been scraped — because
a loud failure already tells you: the worker exits non-zero, the log has a
traceback, the task's Last Run Result turns 1. What no existing signal covers is a
component that stops doing anything while everything still reports success, and
that is the failure this product cannot survive: alerts simply stop, and the first
person to notice is a subscriber who did not get one.

Thresholds are arguments rather than constants so the quiet hours of a genuinely
quiet market do not have to be argued with in code.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from worker.env import load_env  # noqa: E402 - path set above for direct runs

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:  # pragma: no cover - a setup error, not a runtime one
    sys.exit("report: psycopg is not installed. Run `uv sync` first.")

Row = dict[str, Any]


# ── the report ────────────────────────────────────────────────────────────


DELIVERY_BY_DAY = """
SELECT date_trunc('day', coalesce(n.sent_at, n.created_at))::date AS day,
       count(*) FILTER (WHERE n.status = 'sent')    AS sent,
       count(*) FILTER (WHERE n.status = 'queued')  AS waiting,
       count(*) FILTER (WHERE n.status = 'failed')  AS failed,
       count(*) FILTER (WHERE n.status = 'skipped' AND n.error = 'share')
                                                    AS withheld,
       count(DISTINCT n.user_id)                    AS people
  FROM notifications n
 WHERE coalesce(n.sent_at, n.created_at) >= now() - make_interval(days => %(days)s)
 GROUP BY 1 ORDER BY 1 DESC
"""

# Per person, because "how many did this subscriber get" is the question support
# actually gets asked, and an average over everybody cannot answer it.
DELIVERY_BY_USER = """
SELECT u.id,
       u.plan,
       u.plan_until,
       coalesce(p.delivery_share, 100)              AS share,
       count(*) FILTER (WHERE n.status = 'sent')    AS sent,
       count(*) FILTER (WHERE n.status = 'skipped' AND n.error = 'share')
                                                    AS withheld,
       count(*) FILTER (WHERE n.status = 'failed')  AS failed,
       max(n.sent_at)                               AS last_sent,
       (SELECT count(*) FROM subscriptions s WHERE s.user_id = u.id AND s.active) AS filters
  FROM users u
  LEFT JOIN plans p ON p.key = u.plan
  LEFT JOIN notifications n
         ON n.user_id = u.id
        AND coalesce(n.sent_at, n.created_at) >= now() - make_interval(days => %(days)s)
 WHERE u.status = 'active'
 GROUP BY u.id, u.plan, u.plan_until, p.delivery_share
 ORDER BY sent DESC, u.id
"""

# The hour of day a person hears from us. Useful for the one complaint this kind of
# product reliably attracts, which is being messaged at four in the morning.
DELIVERY_BY_HOUR = """
SELECT extract(hour FROM n.sent_at)::int AS hour, count(*) AS sent
  FROM notifications n
 WHERE n.status = 'sent'
   AND n.sent_at >= now() - make_interval(days => %(days)s)
 GROUP BY 1 ORDER BY 1
"""

INGEST_BY_DAY = """
SELECT date_trunc('day', received_at)::date AS day,
       source_key,
       count(*)                                          AS messages,
       count(*) FILTER (WHERE status = 'parsed')          AS parsed,
       count(*) FILTER (WHERE status = 'unparseable')     AS unparseable,
       count(*) FILTER (WHERE status = 'new')             AS waiting
  FROM source_messages
 WHERE received_at >= now() - make_interval(days => %(days)s)
 GROUP BY 1, 2 ORDER BY 1 DESC, 2
"""

LISTINGS_BY_SOURCE = """
SELECT source_key,
       count(*)                                        AS total,
       count(*) FILTER (WHERE status = 'active')       AS active,
       max(first_seen_at)                              AS newest
  FROM listings GROUP BY 1 ORDER BY 1
"""


def table(title: str, rows: list[Row]) -> str:
    """Fixed-width columns, because the point is to be read rather than parsed."""
    if not rows:
        return f"{title}\n  (nothing)\n"
    headers = list(rows[0].keys())
    widths = {
        h: max(len(h), *(len(fmt(r[h])) for r in rows)) for h in headers
    }
    line = "  ".join(h.ljust(widths[h]) for h in headers)
    rule = "  ".join("-" * widths[h] for h in headers)
    body = "\n".join(
        "  ".join(fmt(r[h]).ljust(widths[h]) for h in headers) for r in rows
    )
    return f"{title}\n{line}\n{rule}\n{body}\n"


def fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


# ── the checks ────────────────────────────────────────────────────────────


def problems(conn: Any, *, quiet_hours: int, stale_hours: int) -> list[str]:
    """Every silence worth waking someone for. Empty means nothing is stuck."""
    found: list[str] = []

    def scalar(sql: str, params: tuple[Any, ...] = ()) -> Any:
        row = conn.execute(sql, params).fetchone()
        return None if row is None else next(iter(row.values()))

    # 1. The reader. Its own log says "nothing waiting" whether the feed is quiet or
    #    the session was revoked, so only the gap distinguishes them.
    latest = scalar("SELECT max(received_at) FROM source_messages")
    if latest is None:
        found.append("no source messages have ever been stored — the reader has not run")
    else:
        age = (datetime.now(timezone.utc) - latest).total_seconds() / 3600
        if age > quiet_hours:
            found.append(
                f"no new source message for {age:.1f}h "
                f"(last {latest:%Y-%m-%d %H:%M}) — reader stopped, or the feed went quiet"
            )

    # 2. The parser. A format change shows up here and nowhere else: messages keep
    #    arriving, listings stop appearing, and every job still exits 0.
    stuck = scalar(
        "SELECT count(*) FROM source_messages WHERE status = 'new' "
        "AND stored_at < now() - make_interval(hours => %s)",
        (stale_hours,),
    )
    if stuck:
        found.append(f"{stuck} source message(s) unparsed for over {stale_hours}h — parse job stopped")

    recent_bad = scalar(
        "SELECT count(*) FROM source_messages WHERE status = 'unparseable' "
        "AND stored_at > now() - interval '24 hours'"
    )
    recent_all = scalar(
        "SELECT count(*) FROM source_messages WHERE stored_at > now() - interval '24 hours'"
    ) or 0
    if recent_all >= 10 and recent_bad and recent_bad / recent_all > 0.2:
        found.append(
            f"{recent_bad} of {recent_all} messages in the last 24h could not be parsed "
            "— the source has probably changed its format"
        )

    # 3. The queue. Rows piling up means delivery is down while matching is fine,
    #    which produces no error anywhere.
    waiting = scalar(
        "SELECT count(*) FROM notifications WHERE status = 'queued' "
        "AND created_at < now() - make_interval(hours => %s)",
        (stale_hours,),
    )
    if waiting:
        found.append(f"{waiting} message(s) queued for over {stale_hours}h — delivery is not draining")

    # 4. Scraped sources. Distinct from the reader: these can be blocked by the site
    #    while everything else keeps working.
    for row in conn.execute(
        "SELECT key, health, health_note FROM sources WHERE enabled AND health <> 'ok'"
    ).fetchall():
        found.append(f"source {row['key']} is {row['health']}: {row['health_note'] or 'no note'}")

    # 5. Nobody can be sent anything. Worth its own line because it looks like a
    #    quiet market from every other angle.
    reachable = scalar(
        """
        SELECT count(*) FROM subscriptions s
          JOIN users u          ON u.id = s.user_id AND u.status = 'active'
          JOIN user_channels uc ON uc.user_id = u.id AND uc.is_primary
         WHERE s.active
        """
    )
    if not reachable:
        found.append("no active subscription has a delivery channel — nothing can be sent")

    return found


def tell_ops(message: str) -> None:
    token = os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_OPS_CHAT")
    if not token or not chat:
        print("report: TELEGRAM_TOKEN or TELEGRAM_OPS_CHAT not set; not alerting", file=sys.stderr)
        return
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({
            "chat_id": chat, "text": message, "disable_web_page_preview": True,
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status >= 300:
                print(f"report: alert rejected with {response.status}", file=sys.stderr)
    except (urllib.error.URLError, OSError) as error:
        print(f"report: could not alert — {type(error).__name__}: {error}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Delivery statistics, and a check for silences.")
    parser.add_argument("--days", type=int, default=7, help="window for the report (default 7)")
    parser.add_argument("--out", default="reports", metavar="DIR",
                        help="where to write the report (default ./reports)")
    parser.add_argument("--no-alert", action="store_true",
                        help="write the report but send nothing to the ops chat")
    parser.add_argument("--quiet-hours", type=int, default=6, metavar="H",
                        help="hours without a source message before that is a fault (default 6)")
    parser.add_argument("--stale-hours", type=int, default=2, metavar="H",
                        help="hours a message may sit unparsed or unsent (default 2)")
    args = parser.parse_args()

    load_env()
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("report: DATABASE_URL is not set", file=sys.stderr)
        return 1

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    directory = pathlib.Path(args.out)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"report-{stamp}.txt"

    with psycopg.connect(url, row_factory=dict_row) as conn:
        sections = [
            f"Report {datetime.now():%Y-%m-%d %H:%M} · window {args.days} day(s)\n",
            table("DELIVERY BY DAY",
                  conn.execute(DELIVERY_BY_DAY, {"days": args.days}).fetchall()),
            table("PER SUBSCRIBER",
                  conn.execute(DELIVERY_BY_USER, {"days": args.days}).fetchall()),
            table("BY HOUR OF DAY",
                  conn.execute(DELIVERY_BY_HOUR, {"days": args.days}).fetchall()),
            table("INGEST", conn.execute(INGEST_BY_DAY, {"days": args.days}).fetchall()),
            table("LISTINGS", conn.execute(LISTINGS_BY_SOURCE).fetchall()),
        ]
        faults = problems(conn, quiet_hours=args.quiet_hours, stale_hours=args.stale_hours)

    if faults:
        sections.append("PROBLEMS\n" + "\n".join(f"  ! {f}" for f in faults) + "\n")
    else:
        sections.append("PROBLEMS\n  none\n")

    report = "\n".join(sections)
    path.write_text(report, encoding="utf-8")
    print(report)
    print(f"report: written to {path}")

    if faults and not args.no_alert:
        tell_ops("⚠️ Something is stuck:\n\n" + "\n".join(f"• {f}" for f in faults))
        # Non-zero so a scheduler's own "last run failed" column agrees with the
        # alert. Silence in two places is how a monitor stops being trusted.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
