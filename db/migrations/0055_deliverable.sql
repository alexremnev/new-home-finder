-- 0055_deliverable.sql — one definition of "can this be sent right now".
--
-- Apply after 0054.
--
-- ── the tile that stopped meaning anything ────────────────────────────────
--
-- The admin's `Oldest queued` is the age of the oldest row in `notifications`
-- still `queued`, and it turns red past ten minutes. It is the one number that
-- catches a delivery that has stopped, because the count cannot: a queue that
-- nothing is draining is also a queue that nothing is being added to, so it
-- looks small.
--
-- Then WhatsApp arrived with three reasons to hold a message on purpose:
--
--  * the 24-hour window is shut, or its check-in has not been answered — up to
--    two days;
--  * the listing's photograph has not finished uploading — five minutes;
--  * a subscriber who is not paying has had thirty messages today — until
--    midnight.
--
-- All three are correct, none is a fault, and all three are indistinguishable
-- in that tile from delivery being dead. So it was red almost always, which is
-- the same as being off.
--
-- ── why a view and not a column ───────────────────────────────────────────
--
-- The obvious fix is to write the reason onto the row — but then the number is
-- only as fresh as the last worker run, and a metric that reads healthy
-- because the worker that maintains it has died is worse than no metric. A
-- view is computed when it is read, by whoever reads it, and cannot go stale.
--
-- The second reason is that `claim_queued` held these three rules privately in
-- its WHERE clause, so the dashboard could only have duplicated them in
-- TypeScript — two copies of a rule about money, drifting. Now the worker
-- claims `WHERE NOT held` and the dashboard counts `WHERE held IS NOT NULL`,
-- off one definition.

BEGIN;

-- ── the numbers themselves ────────────────────────────────────────────────

-- One row, so the three limits are quoted rather than repeated. They were
-- Python defaults on `claim_queued`, which is the wrong place now that the
-- dashboard needs the same values to explain itself.
CREATE OR REPLACE VIEW delivery_limits AS
SELECT interval '24 hours'  AS wa_window,
       interval '5 minutes' AS photo_grace,
       30                   AS wa_daily_cap;

COMMENT ON VIEW delivery_limits IS
    'What WhatsApp delivery allows, in one row: the length of the free-form '
    'window, how long an alert waits for its photograph before going without '
    'one, and how many messages a day a non-paying subscriber may cost. '
    'Quoted by queued_notifications and by the volume alert, never copied.';

-- ── the queue, with the reason it is not moving ───────────────────────────

CREATE OR REPLACE VIEW queued_notifications AS
SELECT n.id, n.user_id, n.channel, n.kind, n.listing_id, n.created_at, n.attempts,
       CASE
           -- Telegram previews the link and has no window and no per-message
           -- cost, so nothing holds it back. Said first because it is the
           -- cheap answer for most rows.
           WHEN n.channel <> 'whatsapp' THEN NULL

           -- WhatsApp is sent the picture itself rather than a link, so an
           -- alert that overtakes its own upload arrives as plain text and is
           -- never revisited. Ingest uploads a batch every two minutes, so one
           -- cycle is usually enough; the grace is the cap, not the wait.
           WHEN n.created_at > now() - lim.photo_grace
                AND EXISTS (
                    SELECT 1 FROM source_messages m
                     WHERE m.listing_id = n.listing_id
                       AND 'photo' = ANY(m.media_kinds)
                       AND m.wa_media_checked_at IS NULL
                ) THEN 'photo'

           -- Outside the window nothing can be sent at all, and there are no
           -- approved templates. Held rather than attempted: an attempt would
           -- spend one of five on a message that cannot go.
           WHEN EXISTS (
                    SELECT 1 FROM user_channels uc
                     WHERE uc.user_id = n.user_id AND uc.channel = 'whatsapp'
                       AND (uc.last_inbound_at IS NULL
                         OR uc.last_inbound_at <= now() - lim.wa_window)
                ) THEN 'window shut'

           -- Inside it, but the check-in is waiting to be answered. See 0054:
           -- the question has to be the last message in the conversation, so
           -- the minutes between asking and answering carry nothing else.
           WHEN EXISTS (
                    SELECT 1 FROM user_channels uc
                     WHERE uc.user_id = n.user_id AND uc.channel = 'whatsapp'
                       AND uc.window_asked_at >= uc.last_inbound_at
                ) THEN 'check-in unanswered'

           -- Thirty a day for somebody who is not paying for it. Held rather
           -- than dropped: the day rolls over and they go out in order. A
           -- paying subscriber is not capped — the volume alert tells us
           -- instead, because cutting off what somebody bought is worse than
           -- the bill.
           WHEN NOT EXISTS (
                    SELECT 1 FROM users u JOIN plans p ON p.key = u.plan
                     WHERE u.id = n.user_id
                       AND p.price_pence > 0
                       AND (u.plan_until IS NULL OR u.plan_until > now())
                )
                AND (
                    SELECT count(*) FROM notifications s
                     WHERE s.user_id = n.user_id
                       AND s.channel = 'whatsapp'
                       AND s.status = 'sent'
                       AND (s.sent_at AT TIME ZONE 'Europe/London')::date
                         = (now() AT TIME ZONE 'Europe/London')::date
                ) >= lim.wa_daily_cap THEN 'daily cap'

           ELSE NULL
       END AS held
  FROM notifications n
 CROSS JOIN delivery_limits lim
 WHERE n.status = 'queued';

COMMENT ON VIEW queued_notifications IS
    'Everything still queued, with `held` naming the reason it is not being '
    'sent, or NULL when nothing is stopping it. The worker claims the NULLs; '
    'the dashboard ages the NULLs and counts the rest. One definition, because '
    'a held message and a stuck one look identical from outside and only one '
    'of them is a fault.';

COMMIT;
