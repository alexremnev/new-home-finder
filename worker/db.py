"""Database access.

Two things beyond opening a connection:

`advisory_lock` guards a job against concurrent runs. The scheduler may fire more
than once for the same slot, and a lock held in the database is the only level
that works regardless of which host produced the duplicate.

`fetch_due_schedules` and `mark_schedule_ran` implement the compare-and-swap that
keeps two ticks from claiming the same schedule row.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

Row = dict[str, Any]


# What Supabase's direct database host looks like. Matched only to explain a
# failure, never to rewrite a connection string: guessing at somebody's credentials
# is not a repair.
_DIRECT_HOST = re.compile(r"@db\.([a-z0-9]+)\.supabase\.co", re.IGNORECASE)


def _explain(database_url: str, failure: Exception) -> str:
    """Turn a resolver failure into the sentence that fixes it.

    `getaddrinfo failed` on `db.<ref>.supabase.co` is not a network problem and not
    a typo. That host publishes an IPv6 address and no IPv4 one, so it resolves on a
    connection with working IPv6 and fails outright on one without — which is why it
    can work for weeks and then stop when a router reboots or an ISP changes
    something. Nothing in this project changed on the day it breaks.

    The fix is the pooler host, and specifically the SESSION pooler on port 5432,
    not the transaction pooler on 6543. `advisory_lock` in this module takes a
    session-scoped lock with `pg_try_advisory_lock`; through a transaction pooler
    that lock is taken on whichever backend served the statement and released at a
    moment nobody controls, so the guard against two concurrent runs silently stops
    guarding. A lock that reports success and does nothing is worse than no lock.
    """
    match = _DIRECT_HOST.search(database_url)
    if not match:
        return str(failure)
    return (
        f"cannot resolve Supabase's direct host: {failure}\n\n"
        "That host is IPv6-only, so it resolves only where IPv6 works — which is "
        "why this can break with nothing changed on our side.\n\n"
        "Use the SESSION pooler instead. In Supabase: Project Settings → Database → "
        "Connection string → Session pooler. It looks like\n"
        f"  postgresql://postgres.{match.group(1)}:<password>"
        "@aws-0-<region>.pooler.supabase.com:5432/postgres\n\n"
        "Session pooler (5432), not transaction pooler (6543): this worker takes a "
        "session-scoped advisory lock to stop two runs overlapping, and a "
        "transaction pooler cannot hold one — the lock would report success and "
        "guard nothing."
    )


@contextmanager
def connect(database_url: str, *, autocommit: bool = True) -> Iterator[psycopg.Connection[Row]]:
    try:
        connection = psycopg.connect(database_url, autocommit=autocommit, row_factory=dict_row)
    except psycopg.OperationalError as failure:
        # Re-raised with an explanation rather than logged and swallowed: the run has
        # to fail, and the traceback it fails with should say what to do.
        raise psycopg.OperationalError(_explain(database_url, failure)) from failure
    with connection as conn:
        yield conn


@contextmanager
def advisory_lock(database_url: str, key: str) -> Iterator[bool]:
    """Hold a session-scoped lock for the duration of the block.

    Yields False if another session holds it, in which case the caller should
    record the run as skipped rather than proceed.
    """
    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as conn:
        got = conn.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS ok", (key,)).fetchone()
        acquired = bool(got and got["ok"])
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (key,))


def fetch_due_schedules(conn: psycopg.Connection[Row], *, job: str | None = None) -> list[Row]:
    sql = """
        SELECT * FROM schedules
         WHERE enabled AND now() >= next_run_at
           AND (%(job)s IS NULL OR job = %(job)s)
         ORDER BY next_run_at
    """
    return list(conn.execute(sql, {"job": job}).fetchall())


def claim_schedule(conn: psycopg.Connection[Row], schedule: Row) -> bool:
    """Compare-and-swap on next_run_at so only one tick claims a row."""
    interval = schedule["interval_seconds"]
    jitter = schedule["jitter_pct"] / 100.0
    delay = interval * (1.0 + random.uniform(-jitter, jitter))
    updated = conn.execute(
        """
        UPDATE schedules
           SET next_run_at = now() + make_interval(secs => %(delay)s),
               last_run_at = now()
         WHERE id = %(id)s AND next_run_at = %(expected)s
        """,
        {"id": schedule["id"], "expected": schedule["next_run_at"], "delay": delay},
    ).rowcount
    return updated == 1


def finish_schedule(conn: psycopg.Connection[Row], schedule_id: int, status: str) -> None:
    conn.execute(
        """
        UPDATE schedules
           SET last_status = %(status)s,
               consecutive_fails = CASE WHEN %(status)s = 'ok' THEN 0
                                        ELSE consecutive_fails + 1 END
         WHERE id = %(id)s
        """,
        {"id": schedule_id, "status": status},
    )


def enabled_source_locations(conn: psycopg.Connection[Row], source_key: str) -> list[Row]:
    """The geographic scope of a run.

    Narrowing or widening coverage is an update to source_locations.enabled, not
    a code change, so the first stage can watch three districts and later watch
    a hundred without a deploy.
    """
    return list(
        conn.execute(
            """
            SELECT sl.source_key, sl.location_id, sl.external_id,
                   l.code, l.kind, l.tfl_zone_min, l.tfl_zone_max, l.lat, l.lng
              FROM source_locations sl
              JOIN locations l ON l.id = sl.location_id
             WHERE sl.source_key = %s AND sl.enabled
             ORDER BY l.code
            """,
            (source_key,),
        ).fetchall()
    )


def get_source(conn: psycopg.Connection[Row], source_key: str) -> Row | None:
    return conn.execute("SELECT * FROM sources WHERE key = %s", (source_key,)).fetchone()
