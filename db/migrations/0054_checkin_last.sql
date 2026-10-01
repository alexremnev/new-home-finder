-- 0054_checkin_last.sql — the check-in is the last message, not just a message.
--
-- Apply after 0053.
--
-- 0047 added `window_asked_at` to ask, half an hour before WhatsApp's 24-hour
-- window shut, whether the alerts should carry on. Asking worked. Being read
-- did not: the question went out and then half an hour of listings landed on
-- top of it, so the buttons were thirty messages up the conversation by the
-- time anybody looked. A question nobody taps costs two days of silence,
-- because with no approved templates nothing can be sent once the window is
-- shut.
--
-- Two changes, both in the worker, and neither alters this schema:
--
--  * the question is now asked five minutes before the window shuts, so it is
--    the newest thing in the conversation when the alerts stop. A reply does
--    not have to arrive inside the window; anything inbound reopens it
--    whenever it comes.
--  * `claim_queued` holds WhatsApp alerts from the moment the question is
--    asked until it is answered, so nothing can land on top of it. The tap
--    releases them by itself: it moves `last_inbound_at` past
--    `window_asked_at`, and the held two days go out on the next drain.
--
-- So this column now decides delivery and not only bookkeeping, and the
-- comment on it said otherwise. That is all this migration changes.

BEGIN;

COMMENT ON COLUMN user_channels.window_asked_at IS
    'When we last asked this number whether to carry on, five minutes before '
    'their 24-hour window shut. Compared against last_inbound_at: an earlier '
    'value means the question belongs to a window that has since been '
    'reopened. While it is the later of the two the question is unanswered, '
    'and WhatsApp alerts are held rather than sent — the question has to stay '
    'the last message in the conversation to be worth asking at all.';

COMMIT;
