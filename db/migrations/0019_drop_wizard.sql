-- 0019_drop_wizard.sql — the wizard is gone.
--
-- Apply after 0018, and only after the deploy that removed the code. Dropping a
-- table the running code still reads is an outage; dropping one nothing reads is
-- housekeeping, and the difference is entirely the order.
--
-- ── what was here and why it went ────────────────────────────────────────
--
-- 0008 built a six-step wizard inside the bot: inline keyboards, one message edited
-- in place, and this table holding the half-finished answers. It worked, and it was
-- replaced rather than fixed, because of a limit that no amount of work on it would
-- have moved: it could only ever exist in Telegram.
--
-- The state was `wizard_sessions`, the questions were Telegram keyboards, and the
-- answers arrived as `callback_query`. None of that transfers to WhatsApp or email.
-- A web form is the one setup surface every channel can link to, so the channel
-- became a delivery choice instead of a second implementation of the same six
-- questions.
--
-- Removed with it: `lib/wizard.ts` (795 lines), its tests (479), `lib/districts.ts`,
-- eight functions and four Telegram helpers from the webhook, and this table.
-- Roughly 1,500 lines for a path nothing routed to any more — and dead code is not
-- free: it is read by everybody who works on the file, and it is the first thing a
-- search for "how does setup work" finds.
--
-- Sessions in flight are lost. That is a person mid-way through a wizard that no
-- longer exists, whose next message gets the form's link, which is where they were
-- going anyway.

BEGIN;

DROP TABLE IF EXISTS wizard_sessions;

COMMIT;
