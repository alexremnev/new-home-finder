-- 0064_whatsapp_week.sql — a WhatsApp week for £6.99, two hundred messages.
--
-- Apply after 0063.
--
-- ── why a week exists on WhatsApp now ─────────────────────────────────────
--
-- Telegram has been sold by the week and by the month since 0020. WhatsApp
-- has only ever had the month 0039 introduced, so the cheapest way onto the
-- channel most people pick was £19.99 — four times the Telegram week, asked
-- of somebody who has had a one-day trial and is deciding in a chat window.
-- A week is the size of the decision: a flat hunt that is going well is over
-- inside one.
--
-- Nothing in the application chooses between plans, so this is the whole of
-- the change: `paidPlans` lists what is enabled and priced for the channel,
-- the landing card reads the prices off it, /pay reads the same list, and
-- `/api/checkout` refuses anything not in it. A plan is a row.
--
-- ── why £6.99 and not a quarter of the month ──────────────────────────────
--
-- A seventh of £19.99 is £4.66, and selling a week at that would make the
-- month the worse purchase of the two — the opposite of what the "Better
-- value" flag on it says. £6.99 is a third of the month for a quarter of the
-- days, so the month stays the better rate per day while the week stays the
-- smaller commitment. It is also the Telegram week plus the two pounds or so
-- that two hundred billed messages cost us.
--
-- ── why two hundred messages and not a quarter of nine hundred ────────────
--
-- The month's nine hundred came from thirty days at the thirty-a-day cap in
-- 0055. Seven days of that is two hundred and ten, so two hundred is the same
-- rule rounded to a figure somebody can hold in their head — and it leaves
-- the month the better value on messages as well as on days, which is the
-- same reason the price is not a quarter.
--
-- The allowance is what makes this safe to sell at seven pounds: every
-- WhatsApp message is billed by Meta (see 0063), so an unmetered week on a
-- wide filter could cost more than it took. Two hundred messages at the
-- $0.022 service rate in `plan_settings` is about $4.40 — the floor under
-- the margin, not an estimate of it.
--
-- ── why no Stripe ids ─────────────────────────────────────────────────────
--
-- The same reason 0039 and 0041 give: a Price has to be created in each
-- Stripe account first, and a plan with no Price id cannot be bought —
-- `/api/checkout` answers 503 and names the empty column. Visible and
-- unbuyable until the ids are pasted in is the right failure; quoting £6.99
-- and charging something else is not.
--
-- After applying, in both Stripe accounts:
--
--   UPDATE plans SET stripe_price_id      = 'price_…' WHERE key = 'wa_week';
--   UPDATE plans SET stripe_price_id_live = 'price_…' WHERE key = 'wa_week';

BEGIN;

-- `max_districts` and `delivery_share` match the month on purpose. A week is
-- a shorter month and not a smaller one: the thing being sold is time, and a
-- week that also saw fewer areas or a share of the listings would be two
-- reductions dressed as one. `delivery_share` takes its default of 100.
INSERT INTO plans
       (key, display_name, max_districts, duration_days,
        price_pence, message_allowance, is_signup_default, enabled, channel)
VALUES ('wa_week', '1 week', 5, 7, 699, 200, false, true, 'whatsapp')
ON CONFLICT (key) DO UPDATE
   SET display_name      = EXCLUDED.display_name,
       max_districts     = EXCLUDED.max_districts,
       duration_days     = EXCLUDED.duration_days,
       price_pence       = EXCLUDED.price_pence,
       message_allowance = EXCLUDED.message_allowance,
       enabled           = EXCLUDED.enabled,
       channel           = EXCLUDED.channel;

-- Nothing else to do: `user_entitlement` reads `message_allowance` off
-- whichever plan the account is on, `claim_plan_notices` reads the stage off
-- the view, and the Stripe webhook reads `duration_days` off the row it was
-- paid for. None of them names a plan.

COMMIT;
