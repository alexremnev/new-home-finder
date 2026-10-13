-- 0066_marketing_rate.sql — the plan notice is a third rate, not a second one.
--
-- Apply after 0065.
--
-- ── what changed outside this repository ──────────────────────────────────
--
-- 0063 wrote the ledger against two of Meta's rates: `service` for free-form
-- text inside the 24-hour window, `utility` for the approved template that is
-- the only thing which reaches a shut window. One template existed and it was
-- a utility one, so two names covered everything that could be sent.
--
-- The template that carries the plan notice now has a payment button on it,
-- and a template whose purpose is to sell is a **marketing** one. Meta
-- classifies templates itself and re-classifies them after the fact — a
-- template submitted as utility is moved if its wording and its button say
-- otherwise — so this is not a choice the submission settles. Marketing is
-- also the dearest of the three: $0.0635 a message on this number against
-- $0.022 for service, which is the whole reason a mislabelled row matters.
--
-- ── why a third name rather than reusing `utility` ────────────────────────
--
-- Because the ledger exists to be reconciled against the invoice, and the
-- invoice will itemise marketing separately. A row that says `utility` while
-- Meta charged marketing makes the two unreconcilable at exactly the line
-- where the money is, and the discrepancy would read as a billing fault
-- rather than as our own label.
--
-- The listing template is still a utility one, so `utility` does not become
-- dead: `mark_sent` writes it whenever an alert is ever sent as a template.
--
-- ── why the rate is a setting and not an inference ────────────────────────
--
-- Nothing in the code can know a template's category: it is Meta's to decide
-- and Meta's to change, and the only honest source is the template itself —
--
--   curl -s "https://graph.facebook.com/v21.0/$WABA_ID/message_templates\
-- ?fields=name,status,category" -H "Authorization: Bearer $WA_ACCESS_TOKEN"
--
-- So the notifier is told, by `WA_NOTICE_CATEGORY`. Meta's review came back
-- `MARKETING` on a template submitted as utility, exactly as above, and that
-- is the notifier's default — the variable exists to follow Meta if it moves
-- the template again, not to be remembered on every deploy. Read the category
-- off the call above whenever the bill looks wrong; the switch is one
-- environment variable and a restart rather than a migration.
--
-- `wa_template_micros` stays at nought here, as 0063 left it, and 0067 sets
-- it: the utility rate turned out to be the service rate. Until then the
-- dashboard reports messages that carry no money rather than counting them as
-- free. The marketing rate is known, so it is set.

BEGIN;

-- ── the ledger learns the third name ──────────────────────────────────────

ALTER TABLE wa_messages DROP CONSTRAINT IF EXISTS wa_messages_billed_as_ck;
ALTER TABLE wa_messages ADD CONSTRAINT wa_messages_billed_as_ck
    CHECK (billed_as IN ('service', 'utility', 'marketing'));

COMMENT ON COLUMN wa_messages.billed_as IS
    'Which of Meta''s rates this message was charged at, in Meta''s own words '
    'so that a month here can be reconciled against a month on the invoice. '
    'service — free-form, inside the 24-hour window, which is nearly '
    'everything: the queue holds anything whose window is shut, so a listing '
    'is only ever sent as one of these. utility — the listing template, which '
    'is the shape an alert would take outside the window if one were ever '
    'sent there. marketing — the plan notice, which carries a payment button '
    'and is therefore classified as selling; the dearest of the three, and '
    'the reason this column exists.';

-- ── the third price ───────────────────────────────────────────────────────

ALTER TABLE plan_settings
    ADD COLUMN IF NOT EXISTS wa_marketing_micros INTEGER NOT NULL DEFAULT 0;

ALTER TABLE plan_settings DROP CONSTRAINT IF EXISTS plan_settings_price_ck;
ALTER TABLE plan_settings ADD CONSTRAINT plan_settings_price_ck
    CHECK (wa_service_micros >= 0 AND wa_template_micros >= 0
       AND wa_marketing_micros >= 0);

COMMENT ON COLUMN plan_settings.wa_marketing_micros IS
    'What one marketing template costs us, in millionths of a US dollar — the '
    'plan notice that reaches a shut window with a payment button on it. The '
    'dearest of the three rates and the only one of the two template rates '
    'anybody has a figure for.';

-- $0.0635 a message, the marketing rate on this number as of October 2026.
-- Priced per category and per country, so this figure is about the United
-- Kingdom and nowhere else.
UPDATE plan_settings SET wa_marketing_micros = 63500;

-- ── one account of a period having ended, not two ─────────────────────────
--
-- `spent` and `expired` are two names for one event. A subscriber who used
-- their allowance early was claimed under both and told twice: "the alerts
-- have stopped" when the messages ran out, and the same sentence again when
-- the days did, weeks later. `claim_plan_notices` now treats the two as one
-- claim per `plan_until`, which is where that duplicate is removed.
--
-- But a row in this table is a *claim*, not a delivery. The INSERT is what
-- stops a drain every five minutes repeating itself, and it stands even when
-- the send failed for good — `release_plan_notice` only undoes the failures
-- worth retrying. So deduplicating on the claim alone would have cost the
-- thing the two stages were accidentally providing: a notice that could not
-- be delivered at `spent` got a second, independent go at `expired`.
--
-- Hence this column. The cross-stage rule asks whether the earlier notice was
-- actually *heard*, so one message per period is one message that arrived,
-- and a period whose first attempt failed is still announced when the days
-- run out. The same-stage rule does not ask: that one is the per-drain guard,
-- and it has to hold whatever the send did.
--
-- Every row that already exists is treated as delivered. The alternative —
-- NULL meaning "nobody knows" — would re-announce, on the first drain after
-- this migration, every period that ever ended for anybody still in the
-- table. A duplicate sent once is the one outcome worth ruling out.

ALTER TABLE plan_notices
    ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ;

COMMENT ON COLUMN plan_notices.delivered_at IS
    'When the provider accepted this notice, or NULL if it was claimed and '
    'never got through. The row itself is only a claim — it is inserted '
    'before the send and kept even when the send fails for good — so this is '
    'the one column that says somebody was actually told. Read by '
    'claim_plan_notices to decide whether an ended period has already been '
    'announced.';

UPDATE plan_notices SET delivered_at = sent_at WHERE delivered_at IS NULL;

COMMIT;
