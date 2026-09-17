-- 0027_why_it_stopped.sql — why a filter went quiet.
--
-- Apply after 0026.
--
-- ── why this is worth a column ────────────────────────────────────────────
--
-- `subscriptions.active = false` records that the alerts stopped. It does not
-- record the only thing about it that matters: whether the service worked.
--
-- Somebody who taps "I found a place" is a success and somebody who taps "pause"
-- is not, and today both leave exactly the same row. That is the difference
-- between knowing the product works and guessing.

BEGIN;

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS stopped_reason TEXT;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS stopped_at TIMESTAMPTZ;

COMMENT ON COLUMN subscriptions.stopped_reason IS
    'Said by the person, not inferred: found_a_place | paused | replaced | '
    'stopped. NULL while the filter is running.';

ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS subscriptions_stopped_reason_ck;
ALTER TABLE subscriptions ADD CONSTRAINT subscriptions_stopped_reason_ck CHECK (
    stopped_reason IS NULL
    OR stopped_reason IN ('found_a_place', 'paused', 'replaced', 'stopped')
);

CREATE INDEX IF NOT EXISTS subscriptions_outcomes
    ON subscriptions (stopped_reason, stopped_at DESC)
    WHERE stopped_reason IS NOT NULL;

COMMIT;
