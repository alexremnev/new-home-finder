-- 0041_prices_under_round.sql — £4.99 and £9.99 instead of £5 and £10.
--
-- Apply after 0040.
--
-- ── why the Stripe ids are cleared here ──────────────────────────────────
--
-- A Stripe Price is immutable, so changing what something costs means creating
-- a new Price and pointing the row at it. Between those two acts the row would
-- say £4.99 while Stripe charged £5.00 — and that is the worst failure this
-- system can have, because nothing errors and nothing logs. It is simply
-- untrue, on the page and in the bot, until somebody notices.
--
-- So this migration removes the old ids in the same transaction as the new
-- price. A plan with no Price id cannot be bought: `/api/checkout` returns 503
-- and names the empty column. Refusing to sell for a minute is a good failure;
-- quoting one number and taking another is not.
--
-- After applying: create the new Prices in both Stripe accounts and paste the
-- ids in. Until then the plans are visible and unbuyable, which is intended.
--
-- `wa_month` is not here — 0039 introduces it at £19.99 with no ids at all.

BEGIN;

UPDATE plans
   SET price_pence          = 499,
       stripe_price_id      = NULL,
       stripe_price_id_live = NULL
 WHERE key = 'week';

UPDATE plans
   SET price_pence          = 999,
       stripe_price_id      = NULL,
       stripe_price_id_live = NULL
 WHERE key = 'month';

COMMIT;
