"""Nightly deletion: the data we have decided not to keep.

Every other job here adds rows. This one is the only thing that removes them,
and it runs once a day rather than on the two-minute tick because nothing it
does is urgent and everything it does is irreversible.

── what it removes, and why each is safe ────────────────────────────────────

`RULES` is the whole policy, in one place, with the retention beside each
table. Two of them are the point of the job and the rest are housekeeping:

  * **Abandoned sign-ups.** The web form creates a `pending` user with a
    one-time token and sends them to the messenger; the token lives an hour.
    Somebody who never pressed the button leaves an account that has no
    channel, no consent, no trial started and no subscriber behind it — and it
    sits in the admin list looking like a lost customer, which it is not. They
    go at the end of the day they were created, so "how many signed up
    yesterday" is still answerable until then, and `daily_stats.signups` keeps
    the count for ever either way.

  * **Listings nobody can see any more.** Not by age — by `last_seen_at`. A
    flat first advertised in June and still on the portal today must stay,
    because `listings` *is* the dedup memory: `worker.sources.sweep` asks which
    external ids it already has, and an undated portal announces anything it
    does not recognise. Deleting a still-advertised listing would therefore
    send it to everybody a second time, as news. A listing not seen for two
    months is off the market, cannot come back through a sweep, and takes its
    price log and sightings with it.

  * **Run logs.** The noisiest table in the database by a wide margin: `drain`
    and `rollup` tick every two minutes, so there are some 1,400 runs a day,
    each with stages and log lines. `job_runs` is the only rule needed —
    stages and events cascade from it.

  * **Raw feed messages.** The largest text we store, and `status` tells us it
    has already been parsed. Keyed on `stored_at` rather than `received_at`,
    because `stored_at` is what the table's growth actually follows, and a
    reader catching up on old history would otherwise have its work deleted
    the same night it arrived.

  * **Raw visits.** One row per visitor per day with referrer, city and
    browser on it. The daily totals live in `daily_stats.visitors` and the
    per-district ones in `district_days`, so what is lost after two months is
    the breakdown, not the shape.

  * **Delivery history.** The one rule with a real cost, taken deliberately:
    `notifications` carries UNIQUE (user_id, listing_id), which is what stops
    the same flat being sent twice, and the admin's "All time" column counts
    it. Two things make it bearable — a listing older than the same cutoff is
    gone from `listings` too, so there is nothing left to re-send, and the
    current paid period is excluded so a WhatsApp allowance cannot be reset by
    this job and sell somebody alerts they already had.

── what it deliberately leaves alone ────────────────────────────────────────

`payments` and `admin_actions`, for ever: one is the financial record and the
other is the audit trail, and both exist to answer a question asked months
later. `daily_stats`, `district_days` and `daily_digests`, because they are
the history the rules above are allowed to delete raw rows *because of*.
`source_seen_ids` and `source_sweeps`, because they are state rather than
history — a deleted watermark means a district read from scratch.

── how it deletes ───────────────────────────────────────────────────────────

In batches, with a cap per rule per run. A first run against a database that
has never been purged has a great deal to do, and one `DELETE` of a million
rows is a single transaction long enough to be killed by a statement timeout —
which would roll back all of it and achieve nothing, every night, for ever.
Batches commit as they go (the connection is autocommit), so a run that is cut
short keeps what it did, and the cap means the first few nights share the work
rather than one night trying to do all of it.

The cutoffs are computed in the query from London midnight, not from the
timer's own clock, so the job does the same thing whatever timezone the server
keeps and whenever it happens to fire.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg

from worker.obs import Run

Row = dict[str, Any]
Conn = psycopg.Connection[Row]

#: Rows removed per statement. Small enough that nothing else waiting on these
#: tables notices, large enough that a backlog does not take all night.
BATCH = 2_000

#: Rows per rule per run. A ceiling rather than a target: in the steady state
#: a night's work is a few hundred rows and this is never reached.
CAP = 100_000

#: Two months, as asked. One number, so "how long do we keep things" has one
#: answer for listings, messages, visits and delivery history alike.
KEEP_DAYS = 60

#: Shorter for the run log, because it is not data about the market or about a
#: person — it is this worker talking about itself, and a month of that is far
#: more than anybody reads. The admin's longest window is three months, so the
#: Jobs panel at that range shows a month of runs rather than three.
KEEP_DAYS_RUNS = 30


@dataclass(frozen=True)
class Rule:
    """One table, one predicate, one retention."""

    #: What the counter is called. Named for what was removed, not for the
    #: table, so the summary line reads as a sentence.
    name: str
    table: str
    #: The primary key. More than one column for a composite key: Postgres
    #: compares row values, so `(day, visitor_hash) IN (SELECT day,
    #: visitor_hash ...)` works exactly as the single-column form does.
    keys: tuple[str, ...]
    #: Which rows may go. `%(cutoff)s` is the retention boundary and
    #: `%(midnight)s` the start of today in London; both are always bound.
    where: str


# Deliberately in this order. Delivery history goes before the listings it
# points at, so that what the cascade would have taken anyway is counted under
# the rule that owns the decision rather than appearing as a mystery drop in
# the notifications table.
RULES: tuple[Rule, ...] = (
    Rule(
        name="signups_abandoned",
        table="users",
        keys=("id",),
        # Four guards, and each one is a way this has gone wrong elsewhere:
        # a verified channel means they did press the button; a payment or a
        # delivered alert means this is a real account whatever its status
        # says; and a priced plan means somebody was mid-upgrade. `pending`
        # alone would eventually have deleted one of those.
        where="""
            status = 'pending'
          AND created_at < %(midnight)s
          AND NOT EXISTS (
              SELECT 1 FROM user_channels c
               WHERE c.user_id = users.id AND c.verified_at IS NOT NULL)
          AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.user_id = users.id)
          AND NOT EXISTS (
              SELECT 1 FROM notifications n WHERE n.user_id = users.id)
          AND NOT EXISTS (
              SELECT 1 FROM plans pl
               WHERE pl.key = users.plan AND pl.price_pence > 0)
        """,
    ),
    Rule(
        name="alerts_forgotten",
        table="notifications",
        keys=("id",),
        # Never a queued row. One still waiting after two months means
        # delivery has been broken for two months, and deleting the evidence
        # of that is the opposite of what this job is for.
        where="""
            status <> 'queued'
          AND coalesce(sent_at, created_at) < %(cutoff)s
          AND NOT EXISTS (
              SELECT 1 FROM users u
               WHERE u.id = notifications.user_id
                 AND u.plan_from IS NOT NULL
                 AND coalesce(notifications.sent_at, notifications.created_at)
                     >= u.plan_from)
        """,
    ),
    Rule(
        name="listings_gone",
        table="listings",
        keys=("id",),
        # `last_seen_at`, never `first_seen_at` — see the header. This is the
        # line that keeps the sweeper's dedup memory intact.
        where="last_seen_at < %(cutoff)s",
    ),
    Rule(
        name="messages_parsed",
        table="source_messages",
        keys=("id",),
        where="stored_at < %(cutoff)s",
    ),
    Rule(
        name="visits_old",
        table="site_visits",
        keys=("day", "visitor_hash"),
        where="day < %(cutoff)s::date",
    ),
    Rule(
        name="runs_logged",
        table="job_runs",
        keys=("id",),
        # Not a run still going. At a month old that is a stuck row rather
        # than a live job, but the lock is what decides whether a job runs,
        # not this table, so there is nothing to gain by tidying it away.
        where="started_at < %(runs_cutoff)s AND status <> 'running'",
    ),
)


def run_purge(
    conn: Conn,
    run: Run,
    *,
    keep_days: int = KEEP_DAYS,
    keep_days_runs: int = KEEP_DAYS_RUNS,
    dry_run: bool = False,
) -> dict[str, int]:
    """Apply every rule. Returns how many rows each removed."""

    removed: dict[str, int] = {}
    with run.stage("purge") as stage:
        bounds = conn.execute(
            """
            SELECT date_trunc('day', now() AT TIME ZONE 'Europe/London')
                       AT TIME ZONE 'Europe/London'            AS midnight,
                   now() - make_interval(days => %(keep)s::int) AS cutoff,
                   now() - make_interval(days => %(runs)s::int) AS runs_cutoff
            """,
            {"keep": keep_days, "runs": keep_days_runs},
        ).fetchone()
        assert bounds is not None

        if dry_run:
            # Counted and not deleted, which is the only honest dry run for a
            # job like this: the number is the whole question.
            for rule in RULES:
                waiting = conn.execute(
                    f"SELECT count(*) AS n FROM {rule.table} WHERE {rule.where}",
                    bounds,
                ).fetchone()
                assert waiting is not None
                stage.set(f"{rule.name}_waiting", int(waiting["n"]))
            stage.set("suppressed", True)
            return {}

        for rule in RULES:
            gone = _delete_in_batches(conn, rule, bounds)
            removed[rule.name] = gone
            # Only what actually went. A summary of twelve zeroes says nothing
            # and hides the one line that does.
            if gone:
                stage.count(rule.name, gone)
            if gone >= CAP:
                stage.log(
                    "info",
                    f"{rule.name} hit the {CAP}-row cap for this run; "
                    f"the rest goes tomorrow night",
                )

        return removed


def _delete_in_batches(conn: Conn, rule: Rule, bounds: Row) -> int:
    total = 0
    while total < CAP:
        # The subselect is what makes this a batch: `LIMIT` stops the scan as
        # soon as it has found enough, so a night with nothing to do costs one
        # pass and a night with a backlog costs one pass per batch.
        deleted = conn.execute(
            f"""
            DELETE FROM {rule.table}
             WHERE ({", ".join(rule.keys)}) IN (
                   SELECT {", ".join(rule.keys)} FROM {rule.table}
                    WHERE {rule.where}
                    LIMIT {min(BATCH, CAP - total)}
             )
            """,
            bounds,
        ).rowcount
        total += deleted
        if deleted == 0:
            break
    return total


__all__ = ["BATCH", "CAP", "KEEP_DAYS", "KEEP_DAYS_RUNS", "RULES", "Rule", "run_purge"]
