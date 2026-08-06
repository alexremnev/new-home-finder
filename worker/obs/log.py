"""Run logging.

Every event goes to two places: a JSON line on stdout, which the CI log keeps,
and a row in the database, which outlives the CI log and can be queried.

The repository is public. Nothing written here may contain a chat id, phone
number, email address, or a whole criteria object — reference a user by its
internal id instead.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Literal

import psycopg

Level = Literal["debug", "info", "warn", "error"]
Row = dict[str, Any]

_FORBIDDEN_CTX_KEYS = frozenset(
    {"chat_id", "phone", "phone_e164", "email", "address", "criteria", "token"}
)


class Run:
    """One worker invocation, with its stages and events."""

    def __init__(
        self,
        conn: psycopg.Connection[Row],
        *,
        job: str,
        trigger: str,
        run_url: str | None = None,
        dry_run: bool = False,
    ) -> None:
        self.conn = conn
        self.job = job
        self.dry_run = dry_run
        self.counters: dict[str, int] = {}
        row = conn.execute(
            """
            INSERT INTO job_runs (job, trigger, run_url) VALUES (%s, %s, %s)
            RETURNING id
            """,
            (job, trigger, run_url),
        ).fetchone()
        assert row is not None
        self.id: int = row["id"]
        self.event("info", f"run started: job={job} trigger={trigger}", dry_run=dry_run)

    # ── events ────────────────────────────────────────────────────────────

    def event(
        self,
        level: Level,
        message: str,
        *,
        stage: str | None = None,
        source_key: str | None = None,
        **ctx: Any,
    ) -> None:
        leaked = _FORBIDDEN_CTX_KEYS & ctx.keys()
        if leaked:
            raise ValueError(f"personal data must not be logged: {sorted(leaked)}")
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "level": level,
            "run_id": self.id,
            "stage": stage,
            "source": source_key,
            "msg": message,
            **ctx,
        }
        print(json.dumps({k: v for k, v in record.items() if v is not None}), file=sys.stdout)
        self.conn.execute(
            """
            INSERT INTO job_events (run_id, level, stage, source_key, message, ctx)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (self.id, level, stage, source_key, message, json.dumps(ctx)),
        )

    def count(self, name: str, delta: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + delta

    # ── stages ────────────────────────────────────────────────────────────

    @contextmanager
    def stage(self, name: str, *, source_key: str | None = None) -> Iterator[Stage]:
        row = self.conn.execute(
            "INSERT INTO job_stages (run_id, stage, source_key) VALUES (%s, %s, %s) RETURNING id",
            (self.id, name, source_key),
        ).fetchone()
        assert row is not None
        st = Stage(self, row["id"], name, source_key)
        try:
            yield st
        except Exception as exc:
            st.finish("failed")
            self.event("error", f"{name} failed: {exc}", stage=name, source_key=source_key)
            raise
        else:
            st.finish(st.status)

    # ── completion ────────────────────────────────────────────────────────

    def finish(self, status: str, error: str | None = None) -> None:
        self.conn.execute(
            """
            UPDATE job_runs
               SET finished_at = now(), status = %s, counters = %s, error = %s
             WHERE id = %s
            """,
            (status, json.dumps(self.counters), error, self.id),
        )
        # A degraded run is a warning, not an error. Logging it as an error
        # makes every scaffold run shout, and an alert that fires on every run
        # is one nobody reads.
        level: Level = (
            "info" if status in ("ok", "skipped_locked")
            else "warn" if status == "degraded"
            else "error"
        )
        self.event(level, f"run finished: {status}", **self.counters)


class Stage:
    def __init__(self, run: Run, stage_id: int, name: str, source_key: str | None) -> None:
        self.run = run
        self.id = stage_id
        self.name = name
        self.source_key = source_key
        self.status = "ok"
        self.counters: dict[str, Any] = {}

    def log(self, level: Level, message: str, **ctx: Any) -> None:
        self.run.event(level, message, stage=self.name, source_key=self.source_key, **ctx)

    def count(self, name: str, delta: int = 1) -> None:
        self.counters[name] = int(self.counters.get(name, 0)) + delta
        self.run.count(name, delta)

    def set(self, name: str, value: Any) -> None:
        self.counters[name] = value

    def degrade(self, reason: str) -> None:
        self.status = "degraded"
        self.log("warn", reason)

    def finish(self, status: str) -> None:
        self.run.conn.execute(
            "UPDATE job_stages SET finished_at = now(), status = %s, counters = %s WHERE id = %s",
            (status, json.dumps(self.counters), self.id),
        )
