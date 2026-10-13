-- 0067_nothing_is_free.sql — the utility rate is the service rate, and a
-- spent allowance is worth telling ourselves about.
--
-- Apply after 0066.
--
-- ── the utility rate is known after all ───────────────────────────────────
--
-- 0063 left `wa_template_micros` at nought with "read it off the bill", and
-- 0066 repeated that. The bill says it is the same as free-form text inside
-- the window: $0.022. Nothing on WhatsApp is free and nothing is cheaper than
-- that — the free service tier the first version of this was built around is
-- gone, and the only rate that stands apart is marketing, which 0066 set.
--
-- So two prices do the work of three. The three *names* stay, because the
-- invoice itemises by category and a ledger that cannot be matched to it line
-- for line is a ledger nobody will trust in a dispute. And the listing
-- template keeps its own column rather than borrowing the service one: these
-- are two rates that happen to be equal today, not one rate with two names,
-- and Meta is free to move either.
--
-- Nothing already in `wa_messages` changes. The price is stamped at send time
-- precisely so that setting it now does not rewrite a month that has already
-- been billed — those rows carry 0 and the dashboard says how many.
--
-- ── and an allowance that runs out should reach the ops chat ──────────────
--
-- `whatsapp_cost_alerts` already stops the daily cap being announced sixty
-- times an hour. A spent allowance is the other threshold worth hearing about
-- and the more important one: the daily cap is a thing we do to a non-payer,
-- while an allowance running out is a paying subscriber's period ending early
-- — they stop receiving alerts with days still on the clock, and the first we
-- would otherwise know of it is them asking why.
--
-- Keyed on `plan_until` in the `day` column rather than on today's date. The
-- event belongs to the period, not to the day it happened on, so this is what
-- makes it once per period: a drain every five minutes for the remaining three
-- weeks of that month finds the row already there.

BEGIN;

-- ── the second price ──────────────────────────────────────────────────────

UPDATE plan_settings SET wa_template_micros = 22000;

COMMENT ON COLUMN plan_settings.wa_template_micros IS
    'What one utility template costs us, in millionths of a US dollar — the '
    'listing template, which is the shape an alert would take outside the '
    '24-hour window. $0.022 on this number, the same as a free-form message '
    'inside the window; marketing is the only rate that stands apart. Its own '
    'column and not the service one because these are two rates that are '
    'equal today, not one rate with two names.';

-- 0066 called marketing "the only one of the two template rates anybody has a
-- figure for", which this file is the end of.
COMMENT ON COLUMN plan_settings.wa_marketing_micros IS
    'What one marketing template costs us, in millionths of a US dollar — the '
    'plan notice that reaches a shut window with a payment button on it. '
    '$0.0635, nearly three times either of the other two, which are both '
    '$0.022. The only rate on this number that stands apart, and the reason '
    'wa_messages.billed_as exists.';

-- ── the allowance alert ───────────────────────────────────────────────────

ALTER TABLE whatsapp_cost_alerts
    DROP CONSTRAINT IF EXISTS whatsapp_cost_alerts_kind_ck;
ALTER TABLE whatsapp_cost_alerts ADD CONSTRAINT whatsapp_cost_alerts_kind_ck
    CHECK (kind IN ('daily', 'lifetime', 'allowance'));

COMMENT ON TABLE whatsapp_cost_alerts IS
    'One row per alert already sent about a WhatsApp subscriber''s volume. '
    'Exists so that drain, which runs every five minutes, says it once. '
    'daily — they hit the daily cap, which for a non-payer is where delivery '
    'stops. allowance — a paid plan used up the messages included in its '
    'period, so their alerts stop with days still on the clock; `day` holds '
    'plan_until there, because that event belongs to the period and not to '
    'the date it fell on. lifetime — gone since 0057, and left in the check '
    'because rows already written are the record of what was sent.';

COMMIT;
