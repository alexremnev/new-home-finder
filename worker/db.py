from __future__ import annotations

import random
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

Row = dict[str, Any]

_DIRECT_HOST = re.compile(r"@db\.([a-z0-9]+)\.supabase\.co", re.IGNORECASE)

def _explain(database_url: str, failure: Exception) -> str:

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

        raise psycopg.OperationalError(_explain(database_url, failure)) from failure
    with connection as conn:
        yield conn

@contextmanager
def advisory_lock(database_url: str, key: str) -> Iterator[bool]:

    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as conn:
        got = conn.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS ok", (key,)).fetchone()
        acquired = bool(got and got["ok"])
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (key,))
