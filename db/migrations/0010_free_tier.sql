-- 0010_free_tier.sql — a free tier that receives a share of its matches.
--
-- Apply after 0009.
--
-- ── what changes, and why it is not the cap 0007 removed ──────────────────
--
-- 0007 removed a daily cap, and the reason it gave still stands: "a cap silently
-- withholds listings that matched — which is the one thing this service exists not
-- to do, and the person has no way to know it happened."
--
-- The objection is to the silence, not to the arithmetic. A share that the person
-- is told about — on the plan they chose, with the number withheld named in the
-- chat — answers it: nothing is hidden, and what they are missing is the reason to
-- pay rather than a defect they cannot see. 0007 also said where it would belong if
-- it came back: "on the subscription, not the plan: fewer messages is a
-- preference, not something to sell." That was right about a *preference*. This is
-- the opposite — it is what is being sold — so it goes on the plan, and a
-- per-subscription "send me fewer" remains a separate, later thing.
--
-- ── why expiry stops excluding people ────────────────────────────────────
--
-- Until now `plan_until <= now()` removed a subscription from the matcher's query
-- entirely. A trial ending was a cliff: full service, then nothing.
--
-- It becomes a step down instead. The query resolves an *effective* share — the
-- plan's own while it is live, the free tier's once it is not — so an expired trial
-- keeps receiving a tenth of its matches and a reason to upgrade. Deliberately
-- still in the query rather than in a job that rewrites `users.plan`: a job that
-- fails to run leaves people on a tier they are not paying for, and the whole point
-- of 0006's design was that entitlement is evaluated, not swept.

BEGIN;

-- Percentage of matching listings actually delivered. 100 means everything.
--
-- Not a boolean and not a daily count: a count is unfair between a narrow filter
-- and a wide one — three a day is everything to one person and a rounding error to
-- another — while a share is proportional to what each subscription would have
-- received.
ALTER TABLE plans
    ADD COLUMN IF NOT EXISTS delivery_share SMALLINT NOT NULL DEFAULT 100;

ALTER TABLE plans DROP CONSTRAINT IF EXISTS plans_share_ck;
ALTER TABLE plans ADD CONSTRAINT plans_share_ck
    CHECK (delivery_share BETWEEN 1 AND 100);

-- Never zero, by constraint. A tier that receives nothing is an unsubscribe with
-- extra steps: the person keeps a filter, keeps expecting alerts, and hears
-- nothing — which is exactly the silence this design is trying to avoid.

-- The tier an expired plan falls back to. No duration: it does not end, so nobody
-- is ever cut off, and there is no second expiry to warn about.
INSERT INTO plans (key, display_name, max_districts, duration_days, price_pence,
                   is_signup_default, enabled, delivery_share)
VALUES ('free', 'Free', 5, NULL, 0, false, true, 10)
ON CONFLICT (key) DO UPDATE
    SET delivery_share = EXCLUDED.delivery_share,
        duration_days  = EXCLUDED.duration_days,
        max_districts  = EXCLUDED.max_districts;

-- The trial is a full-strength week: its job is to show what the product does, and
-- a throttled trial demonstrates the throttle instead.
UPDATE plans SET delivery_share = 100 WHERE key IN ('trial', 'paid', 'comp');

-- Which plan an expired one becomes. A row rather than a constant in code, so the
-- fallback can be changed — or turned back into a hard stop by pointing it at a
-- disabled plan — without a deploy.
CREATE TABLE IF NOT EXISTS plan_settings (
    id            BOOLEAN PRIMARY KEY DEFAULT true,
    lapsed_plan   TEXT NOT NULL REFERENCES plans(key),
    CONSTRAINT plan_settings_single CHECK (id)
);
INSERT INTO plan_settings (id, lapsed_plan) VALUES (true, 'free')
ON CONFLICT (id) DO UPDATE SET lapsed_plan = EXCLUDED.lapsed_plan;

-- ── counting what was withheld ────────────────────────────────────────────
--
-- `notifications` already has a `skipped` status and a `UNIQUE (user_id,
-- listing_id)`, so a withheld match is recorded as a row rather than merely not
-- happening. Three things follow: "why did I not get this one" has an answer, the
-- daily "14 more matched" count is a query rather than a tally kept somewhere, and
-- the same listing cannot be counted twice across runs.
--
-- The trade is that upgrading does not retroactively deliver what was withheld —
-- the row exists, so it is not reconsidered. That is the right way round: by the
-- time somebody upgrades, yesterday's listings are gone from the market anyway, and
-- a burst of stale alerts on payment would be a poor first impression of the paid
-- tier.

COMMENT ON COLUMN notifications.error IS
    'Why a row is failed or skipped. "share" means withheld by the plan''s delivery_share.';

CREATE INDEX IF NOT EXISTS notifications_withheld
    ON notifications (user_id, created_at DESC)
    WHERE status = 'skipped';

ALTER TABLE plan_settings ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target) THEN
            EXECUTE format('REVOKE ALL ON plan_settings FROM %I', target);
        END IF;
    END LOOP;
END $$;

COMMIT;
