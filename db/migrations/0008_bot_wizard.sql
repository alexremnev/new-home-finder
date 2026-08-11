-- 0008_bot_wizard.sql — setting a filter up inside the bot, and warning before a
-- plan ends rather than after.
--
-- Apply after 0007.
--
-- ── why the wizard needs a table ──────────────────────────────────────────
--
-- The webhook is a serverless function. Between the tap that chooses SE16 and the
-- tap that chooses a price there is no process, no memory, and no guarantee the
-- same instance answers. A conversation spread over eight taps therefore has to
-- keep its half-finished state somewhere durable, and `callback_data` cannot hold
-- it: Telegram caps that field at 64 bytes, which is one district, not a draft.
--
-- ── why warnings need a table and not two more columns ────────────────────
--
-- 0006 put `expiry_notified_at` on `users` so the "your plan ended" message was
-- sent once. Two warnings before the end cannot live in one timestamp, and adding
-- `warned_1d_at` and `warned_1h_at` beside it would still be wrong, because of
-- what happens when someone pays: the flags would have to be reset, by hand, in
-- every place a plan is extended — and a missed reset means the person silently
-- never hears that their *paid* period is ending either.
--
-- `plan_notices` puts `plan_until` in the unique key instead. Paying moves
-- `plan_until`, which makes the key different, which makes the warnings due again
-- for the new period. Nothing resets anything. That is the whole mechanism, and it
-- is why `expiry_notified_at` can go.

BEGIN;

-- ── the wizard ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS wizard_sessions (
    -- Keyed by chat, not by user: the wizard starts before there is a user row.
    -- Someone who finds the bot directly taps /start with no account behind them,
    -- and the account is created when they press Finish.
    chat_id       TEXT PRIMARY KEY,
    -- Set when the wizard belongs to somebody already known, so /update can put
    -- the result back on the right account. NULL for a brand-new sign-up.
    user_id       BIGINT REFERENCES users(id) ON DELETE CASCADE,
    step          TEXT   NOT NULL,
    -- The half-built criteria object, in the same vocabulary the finished one
    -- uses. Not a bag of form fields: it goes through the same validation as the
    -- web form, so there is one definition of what a filter is.
    draft         JSONB  NOT NULL DEFAULT '{}'::jsonb,
    -- The message the keyboard is attached to, so each step edits it instead of
    -- sending a new one. Without this a five-step wizard leaves five dead
    -- keyboards in the chat, all of them still tappable.
    prompt_msg_id TEXT,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- An abandoned wizard must not live for ever: someone who wanders off
    -- mid-setup and returns next week should get a clean start, not a stale
    -- half-draft they have forgotten the shape of.
    expires_at    TIMESTAMPTZ NOT NULL,
    CONSTRAINT wizard_step_ck CHECK (step IN (
        'overwrite', 'districts', 'bedrooms', 'price', 'pets', 'furnished', 'confirm'
    ))
);

CREATE INDEX IF NOT EXISTS wizard_sessions_stale ON wizard_sessions (expires_at);

-- ── warnings before a plan ends ───────────────────────────────────────────

CREATE TABLE IF NOT EXISTS plan_notices (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stage      TEXT   NOT NULL,
    -- The expiry this notice was about. In the unique key on purpose — see the
    -- header. This is what makes a renewal re-arm the warnings by itself.
    plan_until TIMESTAMPTZ NOT NULL,
    plan       TEXT NOT NULL,
    sent_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT plan_notices_stage_ck CHECK (stage IN ('day', 'hour', 'expired'))
);

-- The idempotency guarantee itself, enforced by the database rather than by the
-- worker remembering: one notice per stage per expiry, however many times the
-- worker ticks.
CREATE UNIQUE INDEX IF NOT EXISTS plan_notices_once
    ON plan_notices (user_id, stage, plan_until);
CREATE INDEX IF NOT EXISTS plan_notices_user ON plan_notices (user_id, sent_at DESC);

-- Carry the one flag that existed into the new table, so anybody already told
-- their plan ended is not told a second time by the new code path.
INSERT INTO plan_notices (user_id, stage, plan_until, plan, sent_at)
SELECT id, 'expired', plan_until, plan, expiry_notified_at
  FROM users
 WHERE expiry_notified_at IS NOT NULL AND plan_until IS NOT NULL
ON CONFLICT DO NOTHING;

-- Dropped rather than left in place. A column nothing reads is a flag the next
-- person will reasonably assume still governs something; the two places that
-- null it (the Stripe webhook and /grant) no longer need to.
ALTER TABLE users DROP COLUMN IF EXISTS expiry_notified_at;
DROP INDEX IF EXISTS users_expiring;

-- ── plan limits ───────────────────────────────────────────────────────────
--
-- Rows, not code, which is why this is an UPDATE and not a deploy. Five districts
-- on both tiers: the trial is now a full-strength week rather than a narrower
-- product, and what the paid plan sells is time.

UPDATE plans SET max_districts = 5, duration_days = 7  WHERE key = 'trial';
UPDATE plans SET max_districts = 5, duration_days = 14 WHERE key = 'paid';
UPDATE plans SET max_districts = 5                     WHERE key = 'comp';

-- ── row level security ────────────────────────────────────────────────────
--
-- Same reasoning as 0003 and 0006: Supabase publishes a REST endpoint over every
-- table in `public`, reachable with the anon key that ships in front-end code.
-- `wizard_sessions` holds other people's half-written filters and, in `user_id`,
-- a mapping from chat to account.

ALTER TABLE wizard_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE plan_notices    ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target) THEN
            EXECUTE format(
                'REVOKE ALL ON wizard_sessions, plan_notices FROM %I', target);
        END IF;
    END LOOP;
END $$;

COMMIT;
