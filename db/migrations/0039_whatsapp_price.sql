-- 0039_whatsapp_price.sql — a plan belongs to a messenger, and WhatsApp has its
-- own price.
--
-- Apply after 0038.
--
-- ── why a plan is now scoped to a channel ─────────────────────────────────
--
-- `paidPlans()` offered every enabled priced plan to everybody, which was right
-- while delivery was free on both messengers. It is not: a WhatsApp alert is
-- billed per message by Meta, so the same £10 month costs us nothing on
-- Telegram and a real amount on WhatsApp.
--
-- With one price list, a WhatsApp subscriber could buy the Telegram month and
-- we would deliver at a loss on a busy filter. So a plan states which messenger
-- it is for, and NULL keeps meaning "either" — nothing has to be restated to
-- stay as it was.
--
-- ── why the Telegram plans become telegram-only ───────────────────────────
--
-- Not to hide them, but because £5 a week was priced for a channel that costs
-- nothing to deliver on. Anybody already on `week` or `month` keeps it until it
-- expires: `active_subscriptions` resolves a plan without consulting this
-- column, exactly as it ignores `enabled`.

BEGIN;

ALTER TABLE plans
    ADD COLUMN IF NOT EXISTS channel TEXT;

ALTER TABLE plans DROP CONSTRAINT IF EXISTS plans_channel_ck;
ALTER TABLE plans ADD CONSTRAINT plans_channel_ck
    CHECK (channel IS NULL OR channel IN ('telegram', 'whatsapp'));

COMMENT ON COLUMN plans.channel IS
    'Which messenger this plan may be bought from. NULL means either. Set where '
    'the cost of delivery differs by channel.';

-- Priced for a channel that is free to deliver on.
UPDATE plans SET channel = 'telegram' WHERE key IN ('week', 'month');

-- Twice the Telegram month, because every alert on it is a message Meta bills
-- for. `stripe_price_id` is deliberately left NULL: the Price has to be made in
-- each Stripe account first, and a plan with no Price cannot be bought — which
-- is the right failure, rather than a checkout that 500s.
INSERT INTO plans
       (key, display_name, max_districts, duration_days,
        price_pence, is_signup_default, enabled, channel)
VALUES ('wa_month', '1 month', 5, 30, 2000, false, true, 'whatsapp')
ON CONFLICT (key) DO UPDATE
   SET display_name  = EXCLUDED.display_name,
       max_districts = EXCLUDED.max_districts,
       duration_days = EXCLUDED.duration_days,
       price_pence   = EXCLUDED.price_pence,
       enabled       = EXCLUDED.enabled,
       channel       = EXCLUDED.channel;

COMMIT;
