-- 0074_send_it_anyway.sql — the admin may push one withheld listing out.
--
-- Apply after 0073.
--
-- ── the row you can see and cannot do anything about ──────────────────────
--
-- A free subscriber receives a tenth of what matches — 0069 — and the nine
-- tenths are not dropped, they are written as `skipped` with `error = 'share'`
-- so the account page can show them. Which it does: "What was sent" lists a
-- flat in the right district at the right rent beside a warning badge saying
-- it was never delivered.
--
-- There was no way to change that. The listing is in the database, the chat is
-- open, the filter matched — and the only remaining options were to raise the
-- person's share, which hands them every future listing too, or to paste the
-- link by hand into a chat the bot owns.
--
-- So: one button, one listing, back into the queue. The next drain sends it
-- the way it would have sent it in the first place — same renderer, same
-- `notifications` row, same `sent_at`, so it is a delivery and not a message
-- that exists only in somebody's chat history.
--
-- ── why this needs a migration at all ─────────────────────────────────────
--
-- Nothing about the queue changes: `claim_queued` already takes any `queued`
-- row that `queued_notifications.held` does not hold, and on Telegram it holds
-- nothing (0055). The requeue is an UPDATE the route can write by itself.
--
-- What it cannot write by itself is the audit row. Every action on that page
-- is recorded in `admin_actions`, and the CHECK there names the actions one by
-- one on purpose — a typo in a route becomes a failed transaction rather than
-- a journal entry nobody can group by. A new action means a new name in the
-- list.
--
-- ── Telegram only, and why that is not a limitation to fix later ──────────
--
-- The route restricts itself to Telegram, which is where a withheld share is
-- visible and free to undo. On WhatsApp the same button would be a charge per
-- press, against an allowance that is the thing deciding what gets sent, and
-- `drop_stale_whatsapp` would mark an old requeued row `skipped` again on the
-- very next drain — the backlog window is two days and these listings are
-- older than that by the time anybody looks. Pressing it there would do
-- nothing, slowly, and bill for the attempt.

BEGIN;

ALTER TABLE admin_actions DROP CONSTRAINT IF EXISTS admin_actions_action_ck;
ALTER TABLE admin_actions ADD CONSTRAINT admin_actions_action_ck CHECK (
    action IN (
        'extend_plan',    -- more time, for nothing
        'set_plan',       -- move somebody between tiers
        'block',          -- stop delivering and stop them coming back
        'unblock',
        'pause',          -- they asked to stop; reversible
        'resume',
        'erase',          -- remove the personal data, keep the financial record
        'send_listing'    -- requeue one listing the plan's share held back
    )
);

COMMIT;
