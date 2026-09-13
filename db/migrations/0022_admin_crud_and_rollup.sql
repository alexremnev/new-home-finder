-- 0022_admin_crud_and_rollup.sql — a day's numbers, and what the admin may do.
--
-- Apply after 0021.
--
-- ── why a rollup table and NOT a job that reads log files ────────────────
--
-- The proposal was a job that parses the log files into the database every five
-- minutes. That is the wrong direction, and it matters enough to write down.
--
-- Every event is ALREADY in the database. worker/obs/log.py writes to job_events in
-- the same call that prints the JSON line; the file is a copy for somebody reading a
-- terminal, not the record. A job that parsed the files would re-derive structure
-- from text that was structured before it was printed, and it would give the console
-- a second source of truth that lags the first — so two screens would disagree and
-- neither would be wrong.
--
-- It would also fail in the one case where files are all there is: the database
-- being unreachable. A parser that writes what it read to a database that is down
-- has nowhere to put it.
--
-- What IS worth precomputing is the aggregation. "Alerts sent per day" over a year
-- is a scan of every notification row; done on every console load, at ten thousand
-- users, that is the console's slowest query and it gets slower every day. This
-- table holds one row per day, written by the `rollup` job from the real tables —
-- not from files — and the console reads days from it instead of counting.
--
-- The other thing it buys is history. `notifications` will eventually be pruned;
-- these counts are small enough to keep for ever, so the shape of last spring
-- survives the deletion of last spring's rows.

BEGIN;

CREATE TABLE IF NOT EXISTS daily_stats (
    day              DATE PRIMARY KEY,
    -- Delivery
    alerts_sent      INTEGER NOT NULL DEFAULT 0,
    alerts_failed    INTEGER NOT NULL DEFAULT 0,
    alerts_withheld  INTEGER NOT NULL DEFAULT 0,
    -- Intake
    messages_stored  INTEGER NOT NULL DEFAULT 0,
    messages_unread  INTEGER NOT NULL DEFAULT 0,
    listings_added   INTEGER NOT NULL DEFAULT 0,
    -- People and money
    visitors         INTEGER NOT NULL DEFAULT 0,
    signups          INTEGER NOT NULL DEFAULT 0,
    revenue_pence    INTEGER NOT NULL DEFAULT 0,
    -- When this row was last recomputed. Today's row is rewritten every run, so
    -- without this there is no way to tell a quiet day from a stalled rollup.
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS daily_stats_recent ON daily_stats (day DESC);

-- ── what the console may do to an account ────────────────────────────────
--
-- Every one of these is written to admin_actions. An admin panel without an audit
-- trail is a panel where "who changed this and why" has no answer, and the only
-- moment to record it is the moment it happens.
ALTER TABLE admin_actions DROP CONSTRAINT IF EXISTS admin_actions_action_ck;
ALTER TABLE admin_actions ADD CONSTRAINT admin_actions_action_ck CHECK (
    action IN (
        'extend_plan',    -- more time, for nothing
        'set_plan',       -- move somebody between tiers
        'block',          -- stop delivering and stop them coming back
        'unblock',
        'pause',          -- they asked to stop; reversible
        'resume',
        'erase'           -- remove the personal data, keep the financial record
    )
);

-- ── erasure, which is not deletion ──────────────────────────────────────
--
-- `payments.user_id` is ON DELETE CASCADE, so `DELETE FROM users` destroys the
-- payment history with the person. That is the wrong trade in both directions: a
-- financial record has to survive, and a request to be forgotten has to be honoured.
--
-- So erasing wipes what is personal — the chat id in `user_channels`, which is the
-- only identifier here — deactivates the filter, and marks the row. The payments
-- stay, attached to an account that no longer names anybody.
--
-- A hard DELETE remains available for an account with no payments, where there is
-- nothing to preserve and a row nobody will ever look at again is just clutter.
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_status_ck;
ALTER TABLE users ADD CONSTRAINT users_status_ck CHECK (
    status IN ('pending', 'active', 'stopped', 'blocked', 'erased')
);

ALTER TABLE daily_stats ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target) THEN
            EXECUTE format('REVOKE ALL ON daily_stats FROM %I', target);
        END IF;
    END LOOP;
END $$;

COMMIT;
