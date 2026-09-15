-- 0023_ignore_and_pause.sql — dismissing one listing, and pausing all of them.
--
-- Apply after 0022.
--
-- ── why an "Ignore" button needs a column ─────────────────────────────────
--
-- Deleting the Telegram message is enough for the person: it disappears. It is
-- not enough for us. Without a record, the admin's "what was sent" page shows a
-- message that no longer exists in the chat and cannot say why, and there is no
-- way to learn which listings people dismiss on sight — which is the cheapest
-- signal there is about whether the filter is doing its job.
--
-- The column belongs on `notifications` rather than a table of its own: the fact
-- is about one delivery to one person, which is exactly what a notification row
-- already is. `(user_id, listing_id)` is already unique there, so there is no
-- second row to reconcile and nothing to join.
--
-- ── pausing needs no column at all ────────────────────────────────────────
--
-- `subscriptions.active` already exists and `active_subscriptions` already
-- requires it, so clearing it stops the queueing at source. That is the whole
-- of pause, and it differs from /stop in the way that matters: /stop deletes the
-- filter, pause keeps it, so resuming asks nothing of the person.

BEGIN;

ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS ignored_at TIMESTAMPTZ;

COMMENT ON COLUMN notifications.ignored_at IS
    'When the recipient pressed Ignore. The message is deleted from the chat; '
    'this is the only remaining evidence that it was ever sent.';

-- For "how many alerts get dismissed?", which is a question about recent
-- behaviour rather than the whole history.
CREATE INDEX IF NOT EXISTS notifications_ignored
    ON notifications (ignored_at DESC) WHERE ignored_at IS NOT NULL;

COMMIT;
