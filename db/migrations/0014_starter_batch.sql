-- 0014_starter_batch.sql — the first five listings, so signing up is not silence.
--
-- Apply after 0013.
--
-- ── the problem this fixes ───────────────────────────────────────────────
--
-- `subscriptions.backfill_from` is set to `now()` at sign-up, deliberately: without
-- it a new subscriber would be sent the entire standing market at once, which is
-- both useless and indistinguishable from spam.
--
-- The cost is that the first thing anybody experiences is nothing at all. With a
-- narrow filter and a feed of a hundred listings a day, the first alert can be hours
-- away, and the wizard's promise — "I'll message you when a new listing matches" —
-- looks like it did not work.
--
-- A handful of recent matches, once, fixes that without replaying the market: five
-- is enough to show what the alerts look like and to be useful, and few enough that
-- nobody mistakes it for a backlog dump.
--
-- ── why a column and not just a wider backfill_from ──────────────────────
--
-- Widening `backfill_from` would let *every* future run match those old listings
-- too, and the count would be whatever the market happened to hold. This is a
-- one-off with a cap, so it needs its own mark: NULL means "owed a starter batch",
-- and it is set once the batch has been queued whether or not anything matched.
--
-- Set for existing subscriptions rather than left NULL: they have been receiving
-- alerts for days, and a starter batch now would be five listings they have already
-- seen or already declined.

BEGIN;

ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS seeded_at TIMESTAMPTZ;

UPDATE subscriptions SET seeded_at = now() WHERE seeded_at IS NULL;

-- Partial, because the only query that reads this wants the ones still owed a batch,
-- and after the first week that is a handful of rows out of all of them.
CREATE INDEX IF NOT EXISTS subscriptions_unseeded
    ON subscriptions (created_at) WHERE seeded_at IS NULL AND active;

COMMIT;
