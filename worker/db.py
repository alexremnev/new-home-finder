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
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

Row = dict[str, Any]


@contextmanager
def connect(database_url: str, *, autocommit: bool = True) -> Iterator[psycopg.Connection[Row]]:
    with psycopg.connect(database_url, autocommit=autocommit, row_factory=dict_row) as conn:
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
