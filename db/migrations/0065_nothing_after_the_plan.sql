-- 0065_nothing_after_the_plan.sql — a finished WhatsApp plan stops the
-- listings, including the ones already queued.
--
-- Apply after 0064.
--
-- ── the hole ──────────────────────────────────────────────────────────────
--
-- 0058 settled what an ended plan receives: a fifth of the matches on
-- Telegram, where delivery is free, and nothing at all on WhatsApp, where
-- Meta bills per message. `user_entitlement.delivery_share` says so, and
-- `queue_matches` reads it — so from the moment a trial or a month ends, a
-- WhatsApp subscriber's new matches are written as `skipped` with the reason
-- `share` and never queued.
--
-- What was never asked again is the queue that already existed. A listing is
-- queued while the plan is live and then waits: for its photograph, for
-- midnight, and above all for a shut 24-hour window to reopen — which is the
-- normal case, since a window shuts after a day of silence and the whole
-- backlog sits behind it. `queued_notifications.held` names those three
-- reasons and asks nothing about the plan, and `claim_queued` claims whatever
-- it does not name. So:
--
--   1. alerts queue up behind a shut window while the trial runs,
--   2. the trial ends — new matches now stop at `queue_matches`,
--   3. the person writes in, or taps "Keep searching", which reopens the
--      window,
--   4. and up to a day's listings go out to somebody with no plan, each one
--      billed to us, and each one carrying the footer a restricted alert
--      gets — which at a share of nought reads "you are currently receiving
--      only 0% of available properties". See `restriction_text`.
--
-- The daily cap was the only thing bounding it, at thirty messages a day, and
-- the cap exists to bound what a non-payer costs per day — not to decide
-- whether they are owed anything at all.
--
-- ── the rule, in the one place that defines delivery ──────────────────────
--
-- So `held` asks the question first, before the photograph and before the
-- window: on WhatsApp, is the plan live? `user_entitlement.live` is both
-- halves of that — days and messages — so a spent allowance holds the queue
-- exactly as a finished month does, and a trial is no different from a
-- subscription, which is the point.
--
-- Held, not skipped, like every other reason here: somebody who renews inside
-- the backlog window gets those listings, and the rolling two-day sweep in
-- `drop_stale_whatsapp` clears the rest without this file having to decide
-- when a backlog is dead. The reason reads on the dashboard beside the other
-- four, which is how "nothing is being delivered to this person" stops
-- looking like a stalled queue.
--
-- Telegram is untouched, deliberately: the branch is inside the WhatsApp arm
-- of the CASE. A lapsed Telegram account keeps receiving its fifth of the
-- matches with the limited-access line at the foot of each one, because that
-- fifth costs nothing to send and is the best argument for the other four.

CREATE OR REPLACE VIEW queued_notifications AS
SELECT n.id, n.user_id, n.channel, n.kind, n.listing_id, n.created_at, n.attempts,
       CASE
           WHEN n.channel <> 'whatsapp' THEN NULL

           -- First, because it is the only one of these that is not about
           -- timing. The other four say "not yet"; this one says there is
           -- nobody to deliver to. Reported ahead of them for the same
           -- reason: "window shut" about an ended trial sends whoever reads
           -- the dashboard looking for a conversation to reopen.
           WHEN NOT EXISTS (
                    SELECT 1 FROM user_entitlement e
                     WHERE e.user_id = n.user_id AND e.live
                ) THEN 'plan ended'

           WHEN n.created_at > now() - lim.photo_grace
                AND EXISTS (
                    SELECT 1 FROM source_messages m
                     WHERE m.listing_id = n.listing_id
                       AND 'photo' = ANY(m.media_kinds)
                       AND m.wa_media_checked_at IS NULL
                ) THEN 'photo'

           WHEN EXISTS (
                    SELECT 1 FROM user_channels uc
                     WHERE uc.user_id = n.user_id AND uc.channel = 'whatsapp'
                       AND (uc.last_inbound_at IS NULL
                         OR uc.last_inbound_at <= now() - lim.wa_window)
                ) THEN 'window shut'

           WHEN EXISTS (
                    SELECT 1 FROM user_channels uc
                     WHERE uc.user_id = n.user_id AND uc.channel = 'whatsapp'
                       AND uc.window_asked_at >= uc.last_inbound_at
                ) THEN 'check-in unanswered'

           -- Thirty messages a day for anybody not on a live paid plan.
           -- "Live" includes having messages left — see user_entitlement.
           WHEN NOT EXISTS (
                    SELECT 1 FROM user_entitlement e
                     WHERE e.user_id = n.user_id AND e.priced AND e.live
                )
                AND (
                    SELECT count(*) FROM wa_messages w
                     WHERE w.user_id = n.user_id
                       AND (w.sent_at AT TIME ZONE 'Europe/London')::date
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
    'the dashboard ages the NULLs and counts the rest. One definition, '
    'because a held message and a stuck one look identical from outside and '
    'only one of them is a fault. On WhatsApp the first question is whether '
    'the plan is live at all: a finished trial or month receives nothing '
    'there, backlog included, however open its 24-hour window is — see 0058 '
    'for why nothing rather than the lapsed fifth.';
