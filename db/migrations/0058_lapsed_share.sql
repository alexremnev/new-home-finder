-- 0058_lapsed_share.sql — what the alerts fall back to, asked before the plan
-- ends rather than after.
--
-- Apply after 0057.
--
-- ── the message that was wrong ────────────────────────────────────────────
--
-- "Your free trial ends tomorrow. After that the alerts stop until you renew."
--
-- On Telegram that is not what happens: a finished plan falls back to the
-- lapsed tier, a fifth of what matches, and 0046 set it there deliberately
-- because a fifth of the listings is the best argument for the other four. The
-- notice was telling people the opposite of the thing meant to bring them back.
--
-- It was not a wording mistake. `claim_plan_notices` read `delivery_share` from
-- `user_entitlement`, which is the share in force *now* — and for somebody
-- whose trial has not ended yet, that is the trial's own hundred per cent. The
-- notice then read a hundred as "nothing is withheld, so there is nothing to
-- fall back to" and said the alerts stop.
--
-- So the view answers both questions instead of one, and each notice reads the
-- one it is actually asking. `delivery_share` is unchanged; `lapsed_share` is
-- new, and is the channel's fallback whether or not the plan is still running.
-- Nought on WhatsApp, where every message is billed — see 0057.

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
       END AS delivery_share,

       -- What the alerts fall back to when this plan ends, whether or not it
       -- has ended yet.
       --
       -- The same rule as the last two branches above; what differs is that it
       -- does not ask whether the plan is still live. `delivery_share` answers
       -- "what is being delivered now", and the notice sent the day before a
       -- trial ends has to answer "what next". Reading the first for the second
       -- is what told a live trial its share was a hundred — and therefore that
       -- the alerts would stop, when on Telegram they drop to a fifth.
       CASE
           WHEN uc.channel = 'whatsapp' THEN 0
           ELSE coalesce(lapsed.delivery_share, 0)
       END AS lapsed_share
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
    'Whether each account''s plan is live, why not when it is not, what share '
    'of matches it therefore receives now (delivery_share) and what that '
    'share becomes once the plan ends (lapsed_share). One row per user. '
    'Exists so that "is the plan live" has one definition: it has two halves '
    'now — days and alerts — and it was written out in nine places before '
    'this. The share is here for the same reason, and because it depends on '
    'the channel: a finished plan falls back to a fifth of the listings on '
    'Telegram and to nothing on WhatsApp, where every message is billed.';
