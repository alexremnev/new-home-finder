-- 0047_window_checkin.sql — asking before the WhatsApp window shuts.
--
-- Apply after 0046.
--
-- WhatsApp allows free-form messages only within 24 hours of the person's own
-- last message. With no approved templates, that is the only way to reach them
-- at all: outside the window nothing can be sent.
--
-- So half an hour before it shuts we ask, while we still can, whether they want
-- the alerts to carry on. Tapping either button is an inbound message, which
-- opens a fresh 24 hours; everything held back while the window was closed then
-- goes out on the next run.
--
-- ── why a column and not a table ─────────────────────────────────────────
--
-- One question per window, and the window is already identified by
-- `last_inbound_at`. So "have we asked about this window" is
-- `window_asked_at >= last_inbound_at` — no row to insert, nothing to clean up,
-- and a new window makes the old answer stale by itself.

BEGIN;

ALTER TABLE user_channels
    ADD COLUMN IF NOT EXISTS window_asked_at TIMESTAMPTZ;

COMMENT ON COLUMN user_channels.window_asked_at IS
    'When we last asked this number whether to carry on, before their 24-hour '
    'window shut. Compared against last_inbound_at: an earlier value means the '
    'question belongs to a window that has since been reopened.';

-- The sweep looks for windows about to shut, so it reads this way round.
CREATE INDEX IF NOT EXISTS user_channels_window
    ON user_channels (last_inbound_at)
 WHERE channel = 'whatsapp' AND last_inbound_at IS NOT NULL;

COMMIT;
