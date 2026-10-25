-- 0071_one_free_day.sql — one more day of the WhatsApp trial, free, for
-- somebody the trial barely reached.
--
-- Apply after 0070.
--
-- ── who it is for ─────────────────────────────────────────────────────────
--
-- The WhatsApp trial runs a single day — 0038 — and a day is enough to show
-- what the alerts are only if the alerts arrive. Somebody whose filter matched
-- ten messages or fewer in that day has not seen the product; they have seen a
-- quiet chat window, and the sentence saying their trial has ended is the
-- first thing that happens to them. Offering that person another day is
-- cheaper than losing them, and it is bounded: thirty messages at $0.022 is
-- the worst a trial day has ever been able to cost.
--
-- Ten counts every WhatsApp message in the period, because that is what
-- `user_entitlement.messages_used` counts and what the thirty is an allowance
-- of — see 0068. Deliberately: a trial that was spent on replies and a
-- check-in rather than on listings is the same failure from the subscriber's
-- side, and a second definition of "a message" is the thing that eventually
-- disagrees with the allowance.
--
-- ── why a column and not a plan, a payment or a notice stage ──────────────
--
-- The gift is the same trial, run again: same key, same share, same thirty
-- messages, same one day. So nothing about the plan changes and there is
-- nothing for a `trial_extended` row in `plans` to hold — 0038 made the same
-- argument against a second trial plan when only the length differed.
--
-- What has to be remembered is that it has happened, once, for ever. That
-- cannot live in `plan_notices`: its rows are keyed by `plan_until`, and the
-- gift moves `plan_until` by a day, so the extended trial is a new period
-- there and would be offered its own gift at the end of it. It cannot live in
-- `payments` either — nothing was paid, and a row of nought pounds in the
-- ledger is a refund waiting to be reconciled.
--
-- So: one timestamp on the account. NULL means "never had it", which is every
-- account that exists today, and it is the whole guard — the grant is a single
-- UPDATE whose WHERE includes `trial_gift_at IS NULL`, so two taps on the
-- button race each other and exactly one wins. The button outlives the message
-- it is drawn on, which is why that matters: a tap on last week's offer is
-- answered by the second tap's wording, not by another day.
--
-- A timestamp and not a boolean, for what it costs to ask "how many of these
-- did we give away last week, and how many of them paid afterwards".

BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS trial_gift_at TIMESTAMPTZ;

COMMENT ON COLUMN users.trial_gift_at IS
    'When this account was given one more free day of the WhatsApp trial, '
    'offered to somebody whose trial reached them with ten messages or fewer. '
    'NULL means never, and it is the guard as well as the record: the grant '
    'is one UPDATE that requires NULL here, so the offer can only be taken '
    'once however many times its button is tapped. The gift is the trial run '
    'again — same plan key, same day, same allowance, plan_from reset — so '
    'nothing else distinguishes it and nothing else needs to.';

COMMIT;
