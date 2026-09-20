-- 0033_live_prices.sql — a Price id per Stripe account.
--
-- Apply after 0032.
--
-- A Price id belongs to one Stripe account, so the test account's ids mean
-- nothing to the live one. With a single column, going live meant editing every
-- plan row at the same moment as the keys, and going back meant editing them
-- again — under exactly the pressure where that goes wrong.
--
-- Two columns instead: both accounts' ids sit side by side and STRIPE_MODE
-- chooses. Switching is then one environment variable, and so is reverting.

BEGIN;

ALTER TABLE plans
    ADD COLUMN IF NOT EXISTS stripe_price_id_live TEXT;

COMMENT ON COLUMN plans.stripe_price_id IS
    'Price id in the TEST Stripe account. Used when STRIPE_MODE is not live.';

COMMENT ON COLUMN plans.stripe_price_id_live IS
    'Price id in the LIVE Stripe account. Used when STRIPE_MODE=live. Starts '
    'with price_, not prod_: it is the row in the product''s Pricing table.';

COMMIT;
