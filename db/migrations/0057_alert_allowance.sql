-- 0057_alert_allowance.sql — a WhatsApp month ends at thirty days or at nine
-- hundred alerts, whichever comes first.
--
-- Apply after 0056.
--
-- ── what this replaces ────────────────────────────────────────────────────
--
-- 0048 watched WhatsApp volume with two ops alerts: thirty a day, and five
-- hundred in total. The second was a proxy for the thing actually wanted — a
-- bound on what one subscriber can cost — and it was only a proxy, because it
-- fired once, for ever, at a number nobody had been sold.
--
-- So the bound becomes part of the plan instead: `wa_month` is thirty days or
-- nine hundred alerts. Reaching the allowance is not a cap that quietly
-- withholds listings — 0007 is unambiguous that this service exists not to do
-- that — it **ends the period**, exactly as running out of days does. The
-- person is told, in the same words, with the same button, and falls back to
-- the same lapsed share.
--
-- ── why the period needs a start ──────────────────────────────────────────
--
-- "Nine hundred a month" needs to know which month. `plan_until` says when the
-- period ends but nothing said when it began, and the end alone will not do:
-- paying stacks, so `plan_until` can be two months away. `plan_from` is set
-- wherever `plan_until` is — when a trial starts and on every payment — so
-- paying again starts a fresh allowance, which is what buying another month
-- means.
--
-- Unused alerts do not roll over. A month of silence is a month we did not
-- spend, not credit the next one inherits.
--
-- ── why a view, and not a condition repeated nine times ───────────────────
--
-- "Is this plan live" was `plan_until IS NULL OR plan_until > now()`, written
-- out in nine places across the worker, the web app and the dashboard. Adding
-- a second half to that question would have meant editing all nine and hoping
-- — and the half that gets missed is the one that keeps delivering to somebody
-- whose allowance is spent, or stops telling somebody theirs is not.
--
-- So the question is answered once, here, and every caller reads the answer.
-- `delivery_share` comes with it, because "what share do they get" has the
-- same two halves and was duplicated in the same places.

BEGIN;

-- ── the allowance ─────────────────────────────────────────────────────────

ALTER TABLE plans
    ADD COLUMN IF NOT EXISTS alert_allowance INTEGER;

ALTER TABLE plans DROP CONSTRAINT IF EXISTS plans_allowance_ck;
ALTER TABLE plans ADD CONSTRAINT plans_allowance_ck
    CHECK (alert_allowance IS NULL OR alert_allowance > 0);

COMMENT ON COLUMN plans.alert_allowance IS
    'How many alerts this plan includes in one period. NULL means unmetered, '
    'which is every Telegram plan: there is nothing to meter when delivery is '
    'free. Reaching it ends the period like running out of days does.';

-- Nine hundred on the WhatsApp month, and nothing anywhere else. Thirty days
-- at thirty a day is where the number comes from, so it is the daily cap in
-- 0055 expressed over a month — a subscriber who paces themselves never meets
-- it, and one on five busy districts meets it instead of running up a bill
-- nobody agreed to.
UPDATE plans SET alert_allowance = 900 WHERE key = 'wa_month';

-- ── when the period began ─────────────────────────────────────────────────

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS plan_from TIMESTAMPTZ;

COMMENT ON COLUMN users.plan_from IS
    'When the current plan period started, so an allowance measured over it '
    'has something to count from. Written wherever plan_until is written. NULL '
    'on a plan that cannot expire, and on rows that predate this column — see '
    'the backfill below.';

-- Everything already here began at some point nobody wrote down. The best
-- available answer is the one the period itself implies: the end, less the
-- plan's own length. Guessing it this way is honest — it is exactly the period
-- the person was sold — and the alternative, NULL, would read as "no alerts
-- counted yet" and hand a second allowance to anybody mid-month.
UPDATE users u
   SET plan_from = u.plan_until - make_interval(days => p.duration_days)
  FROM plans p
 WHERE p.key = u.plan
   AND u.plan_from IS NULL
   AND u.plan_until IS NOT NULL
   AND p.duration_days IS NOT NULL;

-- ── the one answer ────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW user_entitlement AS
SELECT u.id AS user_id,
       u.plan,
       u.plan_from,
       u.plan_until,
       p.display_name AS plan_display,
       -- A plan that costs money. "Paid" has to mean priced AND live, and
       -- conflating the two is how somebody whose month ended stayed exempt
       -- from the daily cap.
       p.price_pence > 0 AS priced,
       p.alert_allowance,

       -- Alerts sent on WhatsApp in this period.
       --
       -- Only `new_listing`: the allowance is what was sold, and what was sold
       -- is listings. The evening digest and the window check-in are messages
       -- we chose to send, so charging them to the person's allowance would
       -- bill them for our own habits.
       --
       -- Counted from `notifications` rather than kept as a counter column,
       -- for the reason 0055 gives: a number maintained by a worker is only as
       -- fresh as the worker, and this one decides whether somebody is served.
       coalesce(used.sent, 0) AS alerts_used,
       CASE WHEN p.alert_allowance IS NULL THEN NULL
            ELSE greatest(p.alert_allowance - coalesce(used.sent, 0), 0)
       END AS alerts_left,

       -- Out of days, out of alerts, or neither.
       (u.plan_until IS NOT NULL AND u.plan_until <= now()) AS out_of_days,
       (
           p.alert_allowance IS NOT NULL
           AND coalesce(used.sent, 0) >= p.alert_allowance
       ) AS out_of_alerts,

       (u.plan_until IS NULL OR u.plan_until > now())
       AND (
           p.alert_allowance IS NULL
           OR coalesce(used.sent, 0) < p.alert_allowance
       ) AS live,

       -- The share that follows from all of the above: the plan's own while it
       -- is live, and once it is not —
       --
       --   * nothing at all on WhatsApp. The lapsed tier is a fifth of what
       --     matches, and on a channel Meta bills per message that is a
       --     standing bill for somebody who has stopped paying. The free tier
       --     exists to show what the service does; it cannot be shown at our
       --     expense for ever.
       --   * the lapsed tier's share on Telegram, where delivery is free and a
       --     fifth of the listings is the best argument for the other four.
       CASE
           WHEN (u.plan_until IS NULL OR u.plan_until > now())
                AND (p.alert_allowance IS NULL
                  OR coalesce(used.sent, 0) < p.alert_allowance)
               THEN p.delivery_share
           WHEN uc.channel = 'whatsapp' THEN 0
           ELSE coalesce(lapsed.delivery_share, 0)
       END AS delivery_share
  FROM users u
  JOIN plans p ON p.key = u.plan
  -- Which messenger they are on, because what a finished plan falls back to
  -- depends on what it costs to deliver. Exactly one channel per account is
  -- primary, so this does not multiply the rows.
  LEFT JOIN user_channels uc ON uc.user_id = u.id AND uc.is_primary
  LEFT JOIN plan_settings ps ON ps.id
  LEFT JOIN plans lapsed ON lapsed.key = ps.lapsed_plan AND lapsed.enabled
  LEFT JOIN LATERAL (
      SELECT count(*) AS sent
        FROM notifications n
       WHERE n.user_id = u.id
         AND n.channel = 'whatsapp'
         AND n.kind = 'new_listing'
         AND n.status = 'sent'
         AND (u.plan_from IS NULL OR n.sent_at >= u.plan_from)
  ) AS used ON p.alert_allowance IS NOT NULL;

COMMENT ON VIEW user_entitlement IS
    'Whether each account''s plan is live, why not when it is not, and what '
    'share of matches it therefore receives. One row per user. Exists so that '
    '"is the plan live" has one definition: it has two halves now — days and '
    'alerts — and it was written out in nine places before this. The share '
    'is here for the same reason, and because it depends on the channel: a '
    'finished plan falls back to a fifth of the listings on Telegram and to '
    'nothing on WhatsApp, where every message is billed.';

-- ── telling somebody their allowance is gone ──────────────────────────────

-- The same three stages plus one. A spent allowance is an ended period, so it
-- is announced through the same table and the same job; `plan_until` stays in
-- the unique key, so it is said once per period and a renewal re-arms it by
-- itself, exactly as it does for the other three.
ALTER TABLE plan_notices DROP CONSTRAINT IF EXISTS plan_notices_stage_ck;
ALTER TABLE plan_notices ADD CONSTRAINT plan_notices_stage_ck
    CHECK (stage IN ('day', 'hour', 'expired', 'spent'));

-- ── the queue asks the same question ──────────────────────────────────────

-- 0055's `queued_notifications` tested "is this person paying" with the half of
-- the question that existed then. Left alone, somebody whose allowance is spent
-- would stay exempt from the thirty-a-day cap — paying by a test that no longer
-- means paying — and the period would end without the one consequence that
-- limits what it costs. Replaced here so that the two views agree, which is the
-- whole point of there being views.
CREATE OR REPLACE VIEW queued_notifications AS
SELECT n.id, n.user_id, n.channel, n.kind, n.listing_id, n.created_at, n.attempts,
       CASE
           WHEN n.channel <> 'whatsapp' THEN NULL

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

           -- Thirty a day for anybody not on a live paid plan. "Live" now
           -- includes having alerts left — see user_entitlement.
           WHEN NOT EXISTS (
                    SELECT 1 FROM user_entitlement e
                     WHERE e.user_id = n.user_id AND e.priced AND e.live
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

COMMIT;
