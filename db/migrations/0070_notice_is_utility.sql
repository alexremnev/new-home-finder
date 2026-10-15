-- 0070_notice_is_utility.sql — Meta moved the plan notice to utility, and the
-- notice earns a button that sends something back.
--
-- Apply after 0069.
--
-- ── the rate ──────────────────────────────────────────────────────────────
--
-- 0066 found `plan_notice_paid` classified MARKETING on a template submitted
-- as utility, reasoned that a body which sells with a button that takes
-- payment is a marketing template whatever the submission said, and made
-- `marketing` the notifier's default. Meta has since reviewed it as
-- **UTILITY**. So the reasoning was wrong about this template and right about
-- the mechanism: the category is Meta's to decide, which is exactly why it was
-- left as a setting rather than hard-coded, and following it now costs one
-- environment variable instead of a migration.
--
-- Nothing in this file changes a price. `wa_template_micros` has been $0.022
-- since 0067 and `record_wa_message` picks the column off `billed_as`, so the
-- ledger started costing notices correctly the moment `WA_NOTICE_CATEGORY`
-- became `utility` — which is now the default in `WhatsAppNotifier`. What is
-- left to do is say so in the places that assert the old answer, because a
-- comment that contradicts the data is worse than no comment: 0066's is the
-- first thing anybody reads when the invoice looks wrong.
--
-- Rows already written keep `marketing` and the $0.0635 stamped on them. They
-- were billed at it. The dashboard still shows that bucket whenever its count
-- is above nought, and hides the term otherwise — a "+ 0 × $0.0635" on every
-- sum is a term that sends the reader looking for a cost nobody is paying.
--
-- Read the current answer off the template before trusting any of this:
--
--   curl -s "https://graph.facebook.com/v21.0/$WABA_ID/message_templates\
-- ?fields=name,status,category" -H "Authorization: Bearer $WA_ACCESS_TOKEN"
--
-- ── and the button, which is a delivery fault and not a price ─────────────
--
-- Worth recording here because it is the more expensive of the two things
-- this file is about. The notice reaches a shut 24-hour window by definition
-- — a plan ends after a day of silence, which is when free-form text is
-- refused — and its button is a url. Tapping a url button sends the business
-- nothing, so the window is still shut when the payment clears a minute
-- later: Meta refuses the receipt with 131047, and every alert the person has
-- just paid for stays in the queue until they happen to write in.
--
-- A quick reply is an inbound message. The same tap reopens the window, the
-- site answers it with the same checkout link the url button carried, and by
-- the time the card clears there is a route to the person again. That is
-- `WA_NOTICE_BUTTON=reply` plus a template approved with that button — the
-- shape is declared, so sending the wrong component is 132000 on every notice
-- and the two have to move together. Hence a setting, defaulting to the shape
-- that is live.

BEGIN;

COMMENT ON COLUMN wa_messages.billed_as IS
    'Which of Meta''s rates this message was charged at, in Meta''s own words '
    'so that a month here can be reconciled against a month on the invoice. '
    'service — free-form, inside the 24-hour window, which is nearly '
    'everything: the queue holds anything whose window is shut, so a listing '
    'is only ever sent as one of these. utility — a template, which is the '
    'only thing that reaches a shut window: the listing template, and the '
    'plan notice since Meta re-classified it. Both $0.022, the same as '
    'service. marketing — $0.0635, and nothing has been sent at it since the '
    'plan notice moved; the rows that carry it were billed at it and keep it, '
    'which is the whole reason this column is Meta''s vocabulary and not '
    'ours. See 0066 for how it got here and 0070 for where it went.';

COMMENT ON COLUMN plan_settings.wa_marketing_micros IS
    'What one marketing template costs us, in millionths of a US dollar. '
    '$0.0635, nearly three times the $0.022 that service and utility both '
    'cost. Nothing is currently sent at this rate — the plan notice was the '
    'only marketing template and Meta reviewed it as utility, see 0070 — so '
    'this prices history and the next marketing template, whichever comes '
    'first. Not dropped: the rows already billed at it are read on the '
    'dashboard every time an earlier month is.';

COMMIT;
