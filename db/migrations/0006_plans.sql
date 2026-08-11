-- 0006_plans.sql — paid plans, entitlements, and one token table instead of two.
--
-- Apply after 0005. It is written to work whether or not 0005 has been applied,
-- because the token columns it supersedes were introduced there.
--
-- ── what a plan is ────────────────────────────────────────────────────────
--
-- Limits are rows, not code. The free trial is one district for a week; the paid
-- plan is three districts for a fortnight. Changing either — or adding a tier —
-- is an UPDATE, and neither the worker nor the web app needs redeploying. The
-- same reasoning as `schedules`: anything that will be tuned in response to real
-- users belongs in the database.
--
-- Delivery speed is deliberately NOT a plan attribute. Everyone gets the alert
-- from the run that found the listing. A paid tier that is merely "the same thing,
-- sooner" invites the suspicion that the free tier is being held back on purpose,
-- and it would mean building a deferral mechanism whose only purpose is to make
-- the product worse for some people.
--
-- ── how an expiry stops delivery ──────────────────────────────────────────
--
-- `plan_until` is checked in the matcher's own query, so an expired plan stops
-- producing messages without any job having to run first. A sweeper that had to
-- deactivate expired subscriptions would, if it failed to run, keep sending —
-- and that is the failure mode that costs money and trust.

BEGIN;

CREATE TABLE IF NOT EXISTS plans (
    key                TEXT PRIMARY KEY,
    display_name       TEXT     NOT NULL,
    max_districts      SMALLINT NOT NULL,
    max_alerts_per_day SMALLINT NOT NULL,
    -- NULL means it does not expire. Used for a plan that is granted rather than
    -- sold, and for whatever open-ended tier may come later.
    duration_days      SMALLINT,
    price_pence         INTEGER NOT NULL DEFAULT 0,
    is_signup_default  BOOLEAN  NOT NULL DEFAULT false,
    enabled            BOOLEAN  NOT NULL DEFAULT true,
    CONSTRAINT plans_districts_ck CHECK (max_districts BETWEEN 1 AND 50),
    CONSTRAINT plans_alerts_ck    CHECK (max_alerts_per_day BETWEEN 1 AND 200)
);

-- Exactly one plan may be what a new sign-up gets, or the web app would have to
-- choose between candidates at runtime.
CREATE UNIQUE INDEX IF NOT EXISTS plans_one_default
    ON plans ((true)) WHERE is_signup_default;

INSERT INTO plans
       (key, display_name, max_districts, max_alerts_per_day, duration_days,
        price_pence, is_signup_default)
VALUES ('trial', 'Free trial',  1, 10,    7,    0, true),
       ('paid',  'Paid',        3, 30,   14, 1000, false),
       ('comp',  'Complimentary', 3, 30, NULL,   0, false)
ON CONFLICT (key) DO NOTHING;

-- ── users ─────────────────────────────────────────────────────────────────

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS plan       TEXT REFERENCES plans(key),
    ADD COLUMN IF NOT EXISTS plan_until TIMESTAMPTZ,
    -- Shown to the person and quoted by them when they pay by bank transfer, so
    -- a received payment can be matched to an account without asking who they are.
    ADD COLUMN IF NOT EXISTS payment_ref TEXT,
    -- So the "your trial has ended" message is sent once and not on every run.
    ADD COLUMN IF NOT EXISTS expiry_notified_at TIMESTAMPTZ;

UPDATE users SET plan = 'trial' WHERE plan IS NULL;

-- NOT NULL because the matcher joins on it. A NULL plan would drop the
-- subscription out of the join and stop that person's alerts with no error
-- anywhere — the worst shape a bug can take here.
ALTER TABLE users ALTER COLUMN plan SET DEFAULT 'trial';
ALTER TABLE users ALTER COLUMN plan SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS users_payment_ref
    ON users (payment_ref) WHERE payment_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS users_expiring
    ON users (plan_until) WHERE plan_until IS NOT NULL AND expiry_notified_at IS NULL;

-- ── one token table ───────────────────────────────────────────────────────
--
-- 0005 put a start token on `users`. A second pair of columns for the edit link
-- would be the same mechanism written twice, so both live here instead: same
-- shape, different purpose and lifetime, one place to expire them.

CREATE TABLE IF NOT EXISTS user_tokens (
    token      TEXT PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose    TEXT   NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT user_tokens_purpose_ck CHECK (purpose IN ('start', 'edit', 'upgrade'))
);
CREATE INDEX IF NOT EXISTS user_tokens_user ON user_tokens (user_id, purpose);

-- Carry over any live start token, then remove the columns it lived in. Guarded
-- on the column existing so this migration applies to a database that never saw
-- 0005's version of it.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'users' AND column_name = 'start_token'
    ) THEN
        INSERT INTO user_tokens (token, user_id, purpose, expires_at)
        SELECT start_token, id, 'start',
               coalesce(token_expires_at, now() + interval '1 hour')
          FROM users
         WHERE start_token IS NOT NULL
        ON CONFLICT (token) DO NOTHING;

        ALTER TABLE users DROP COLUMN start_token;
        ALTER TABLE users DROP COLUMN token_expires_at;
    END IF;
END $$;

-- ── payments ──────────────────────────────────────────────────────────────
--
-- Every grant of a paid plan is recorded, including the ones typed in by hand.
-- Without this there is no way to answer "did this person pay, and for what",
-- which is the first question asked in any dispute.

CREATE TABLE IF NOT EXISTS payments (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan         TEXT   NOT NULL REFERENCES plans(key),
    amount_pence INTEGER NOT NULL DEFAULT 0,
    provider     TEXT   NOT NULL,
    -- The provider's own identifier, where there is one. Unique so that a
    -- repeated webhook cannot extend a plan twice for one payment.
    provider_ref TEXT,
    granted_days SMALLINT,
    granted_by   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT payments_provider_ck
        CHECK (provider IN ('stripe', 'bank_transfer', 'manual'))
);
CREATE UNIQUE INDEX IF NOT EXISTS payments_provider_ref
    ON payments (provider, provider_ref) WHERE provider_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS payments_user ON payments (user_id, created_at DESC);

-- ── row level security on the new tables ──────────────────────────────────
--
-- 0003 explains why: Supabase publishes a REST endpoint over every table in
-- `public`, reachable with the anon key that is designed to be shipped in
-- front-end code. `user_tokens` is the worst of these three to leave open — a
-- readable token table means anyone can take over any account.

ALTER TABLE plans       ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE payments    ENABLE ROW LEVEL SECURITY;

-- Same reasoning as 0003 for the guard: these roles are Supabase's, and naming a
-- missing one would abort this transaction and roll back everything above it.
DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target) THEN
            EXECUTE format(
                'REVOKE ALL ON plans, user_tokens, payments FROM %I', target);
        END IF;
    END LOOP;
END $$;

COMMIT;
