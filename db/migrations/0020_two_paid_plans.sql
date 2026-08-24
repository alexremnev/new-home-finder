-- 0020_two_paid_plans.sql — a week for £5, a month for £10.
--
-- Apply after 0019.
--
-- ── why the Stripe price lives in the table ──────────────────────────────
--
-- There was one plan and one `STRIPE_PRICE_ID` in the environment. Two plans that
-- way means two variables, a branch in the checkout route to pick between them, and
-- a third variable the day a third plan exists — a deploy for what is a pricing
-- decision.
--
-- So the price id goes beside the plan it prices. Adding a plan becomes: make the
-- price in Stripe, insert a row. No deploy, and no chance of a plan whose row says
-- thirty days while the environment charges for seven.
--
-- ── why `paid` is disabled rather than deleted ───────────────────────────
--
-- People are on it, and `payments` rows reference it. Deleting the row would break
-- the foreign key on their history; disabling it stops it being offered while
-- leaving everything that points at it intact. `active_subscriptions` resolves a
-- user's plan without checking `enabled` — deliberately, so that retiring a plan
-- never silently cuts off somebody paying for it.

BEGIN;

ALTER TABLE plans
    -- Nullable: `trial`, `free` and `comp` have no price and never will.
    ADD COLUMN IF NOT EXISTS stripe_price_id TEXT;

COMMENT ON COLUMN plans.stripe_price_id IS
    'The Stripe Price this plan is bought with. Set it after creating the price in '
    'the Stripe dashboard. NULL means the plan cannot be bought.';

INSERT INTO plans
       (key, display_name, max_districts, max_alerts_per_day, duration_days,
        price_pence, is_signup_default, enabled)
VALUES ('week',  '1 week',  7, 60,  7,  500, false, true),
       ('month', '1 month', 7, 60, 30, 1000, false, true)
ON CONFLICT (key) DO UPDATE
   SET display_name  = EXCLUDED.display_name,
       max_districts = EXCLUDED.max_districts,
       duration_days = EXCLUDED.duration_days,
       price_pence   = EXCLUDED.price_pence,
       enabled       = EXCLUDED.enabled;

-- No longer offered. Anybody currently on it keeps it until it expires.
UPDATE plans SET enabled = false WHERE key = 'paid';

-- The tier a finished plan falls back to. Unchanged in substance — stated here
-- because 0010 set it and a reader of this file would otherwise wonder what happens
-- when a week runs out.
UPDATE plan_settings SET lapsed_plan = 'free' WHERE lapsed_plan IS NULL;

COMMIT;
