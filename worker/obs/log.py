from __future__ import annotations

import json
import socket
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

# How bad each outcome is. ok is fine, degraded means something was lost,
# failed means it stopped. `skipped_locked` sits with ok: another run holds the
# lock, which is the lock doing its job.
SEVERITY = {"ok": 0, "skipped_locked": 0, "degraded": 1, "failed": 2}

class Run:

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

        self.trigger = trigger
        self.dry_run = dry_run
        self.counters: dict[str, int] = {}
        # The worst thing any stage of this run reported.
        #
        # Without it a run was recorded as whatever the job function returned,
        # and a stage could degrade itself — a portal refusing us three times
        # running, a district left unread — while the run still said "ok" and
        # the dashboard showed a green tile and 0 failed. The stage knew; the
        # run did not ask. See `finish`.
        self.worst: str = "ok"
        self._stage_note: str | None = None
        # Whether that worst thing is the kind the next run undoes by itself.
        #
        # A portal refusing every exit address for one page is a lost read and
        # nothing more: the watermark does not move, so the next run reads back
        # over the gap and the only cost is latency. A channel with no
        # implementation behind it is not like that at all, and neither is a
        # district that could not be read back to its watermark — those stay
        # broken until somebody acts.
        #
        # Carried into the run's own events so the dashboard can tell the two
        # apart without matching on message text. See `problems` in
        # apps/web/lib/admin-queries.ts, which holds the recoverable kind back
        # until it has happened four runs in a row.
        self._recoverable: bool = False
        # Which machine this is. The portal readers run from the server and
        # from a desk both — Zoopla answers the server 403 and OpenRent 405 on
        # content pages — and without this the run log cannot say which of them
        # made a request.
        host = socket.gethostname()[:120] or None
        row = conn.execute(
            """
            INSERT INTO job_runs (job, trigger, run_url, host) VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (job, trigger, run_url, host),
        ).fetchone()
        assert row is not None
        self.id: int = row["id"]
        self.event(
            "info",
            f"run started: job={job} trigger={trigger} host={host}",
            dry_run=dry_run,
        )

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
            self._note(name, "failed")
            self.event("error", f"{name} failed: {exc}", stage=name, source_key=source_key)
            raise
        else:
            st.finish(st.status)
            self._note(name, st.status, recoverable=st.recoverable)

    def _recoverable_ctx(self) -> dict[str, bool]:
        """`{"recoverable": True}` when it is, and nothing when it is not.

        Absent rather than false so that every event already in the table
        reads the same as one written now: a panel that asks for the flag gets
        no answer from either, which is the honest answer for a run that
        finished before the flag existed.
        """

        return {"recoverable": True} if self._recoverable else {}

    def _note(self, stage: str, status: str, *, recoverable: bool = False) -> None:
        if SEVERITY.get(status, 0) > SEVERITY.get(self.worst, 0):
            self.worst = status
            self._stage_note = stage
            self._recoverable = recoverable

    def finish(self, status: str, error: str | None = None) -> None:
        # A run is no better than its worst stage. The job function returns
        # "ok" when it did not itself raise, which is not the same thing as
        # nothing having gone wrong: `stage.degrade()` is the normal way a
        # portal reports that it was refused, and that used to leave the run
        # green.
        if SEVERITY.get(self.worst, 0) > SEVERITY.get(status, 0):
            self.event(
                "warn",
                f"run reported {status} but its {self._stage_note} stage was "
                f"{self.worst}; recording {self.worst}",
                **self._recoverable_ctx(),
            )
            status = self.worst
            if error is None:
                error = f"{self._stage_note} stage was {self.worst}"

        self.conn.execute(
            """
            UPDATE job_runs
               SET finished_at = now(), status = %s, counters = %s, error = %s
             WHERE id = %s
            """,
            (status, json.dumps(self.counters), error, self.id),
        )

        level: Level = (
            "info" if status in ("ok", "skipped_locked")
            else "warn" if status == "degraded"
            else "error"
        )
        # Merged rather than passed alongside: the counters are splatted, and a
        # counter that one day happens to be called `recoverable` would be a
        # duplicate keyword and a crash at the very end of a run.
        self.event(
            level,
            f"run finished: {status}",
            **{**self.counters, **self._recoverable_ctx()},
        )

        counts = " ".join(f"{name}={value}" for name, value in sorted(self.counters.items()))
        print(
            f"SUMMARY {datetime.now(UTC).astimezone():%Y-%m-%d %H:%M:%S} "
            f"run={self.id} job={self.job} trigger={self.trigger} status={status}"
            + (f" {counts}" if counts else " (nothing to do)"),
            flush=True,
        )

class Stage:
    def __init__(self, run: Run, stage_id: int, name: str, source_key: str | None) -> None:
        self.run = run
        self.id = stage_id
        self.name = name
        self.source_key = source_key
        self.status = "ok"
        self.counters: dict[str, Any] = {}
        # True only while every degradation so far has been recoverable. See
        # `degrade` and `Run._recoverable`.
        self.recoverable = False

    def log(self, level: Level, message: str, **ctx: Any) -> None:
        self.run.event(level, message, stage=self.name, source_key=self.source_key, **ctx)

    def count(self, name: str, delta: int = 1) -> None:
        self.counters[name] = int(self.counters.get(name, 0)) + delta
        self.run.count(name, delta)

    def set(self, name: str, value: Any) -> None:
        self.counters[name] = value

    def degrade(self, reason: str, *, recoverable: bool = False) -> None:
        """Record that this stage lost something, and say whether that is final.

        `recoverable` means the next run of this job undoes it with no help:
        a portal that refused every exit address for one page leaves the
        watermark where it was, so the read is late rather than lost. Default
        False, which is right for everything that needs a person — a channel
        with no implementation, a chat that does not exist, a district that
        could not be read back as far as its watermark.

        A stage that degrades twice is only recoverable if both were. One
        fault that outlives the run is enough to make the whole stage worth
        looking at, and it must not be hidden by a recoverable one beside it.
        """

        self.recoverable = (
            recoverable
            if self.status != "degraded"
            else self.recoverable and recoverable
        )
        self.status = "degraded"
        self.log("warn", reason, **({"recoverable": True} if recoverable else {}))

    def finish(self, status: str) -> None:
        self.run.conn.execute(
            "UPDATE job_stages SET finished_at = now(), status = %s, counters = %s WHERE id = %s",
            (status, json.dumps(self.counters), self.id),
        )
