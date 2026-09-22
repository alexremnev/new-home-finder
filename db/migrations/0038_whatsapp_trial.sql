-- 0038_whatsapp_trial.sql — a shorter trial on WhatsApp, because it is the one
-- that costs money per message.
--
-- Apply after 0037.
--
-- ── why the length is per channel ─────────────────────────────────────────
--
-- A Telegram alert is free to deliver; a WhatsApp one is billed per message and
-- becomes billed per service message on 1 October 2026. Two days of unpaid
-- WhatsApp alerts on a busy set of districts is a real bill for a person who
-- may never subscribe, and the trial's job — showing that the alerts are real
-- and fast — is done within a day.
--
-- ── why a column rather than a second trial plan ──────────────────────────
--
-- A `trial_whatsapp` plan row would need its own limits, its own display name
-- and its own place in `plans_one_default`, and every query that asks "is this
-- person on trial" would have to learn a second key. The only thing that
-- differs is the length, so the only thing stored is the length.
--
-- NULL means "same as duration_days", so every other plan is untouched: a
-- month is a month on either messenger.

BEGIN;

ALTER TABLE plans
    ADD COLUMN IF NOT EXISTS duration_days_whatsapp SMALLINT;

COMMENT ON COLUMN plans.duration_days_whatsapp IS
    'How many days this plan lasts when it was started from WhatsApp. NULL '
    'means use duration_days. Set only where the channel changes the cost.';

-- One day on WhatsApp, two on Telegram. Telegram is stated as well so this file
-- reads as the whole rule rather than half of it.
UPDATE plans
   SET duration_days = 2,
       duration_days_whatsapp = 1
 WHERE key = 'trial';

COMMIT;
