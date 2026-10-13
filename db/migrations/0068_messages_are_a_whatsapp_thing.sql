-- 0068_messages_are_a_whatsapp_thing.sql — a message allowance is a property
-- of the channel, not of the plan, and the trial has one.
--
-- Apply after 0067.
--
-- ── the hole ──────────────────────────────────────────────────────────────
--
-- `plans.message_allowance` was set on `wa_week` and `wa_month` and nowhere
-- else, and both of those carry `channel = 'whatsapp'` — so in practice only
-- WhatsApp was ever metered, and nothing said so. The rule lived in which
-- rows happened to have a number in them.
--
-- The trial is where that stops working. It is one row shared by both
-- messengers — 0038 hit the same wall over length and answered it with
-- `duration_days_whatsapp` — so giving the trial an allowance would have
-- given it to Telegram as well, where delivery is free and a message limit is
-- meaningless.
--
-- ── why the view and not a second column ──────────────────────────────────
--
-- 0038's answer was a per-channel column, and copying it here would mean
-- `message_allowance_whatsapp` beside `message_allowance`, every plan setting
-- both, and a Telegram limit still expressible by leaving the wrong one
-- filled in. The length of a trial genuinely differs by messenger — it is a
-- product decision, twice. An allowance does not: there is exactly one rule,
-- "metered where Meta charges us", and WhatsApp is the only channel that
-- does.
--
-- So the gate goes in `user_entitlement`, once, and `plans.message_allowance`
-- becomes "how many, if this account is on WhatsApp". A Telegram account
-- reads NULL — unmetered — whatever its plan row says, which makes "no
-- message limits on Telegram" true by construction rather than by remembering
-- to leave a column empty.
--
-- It is also what the counter already did. `messages_used` counts
-- `wa_messages`, which holds nothing but WhatsApp messages, so on Telegram it
-- was structurally always nought and the allowance could never be reached.
-- This says that out loud instead of relying on it.
--
-- ── and the trial gets thirty ─────────────────────────────────────────────
--
-- Thirty is not a new number: it is `delivery_limits.wa_daily_cap`, which has
-- bounded every non-paying account since 0055, and the WhatsApp trial lasts
-- one day — so thirty messages is what a trial could already cost us.
--
-- What changes is which of the two bounds does the work, and that is worth
-- the swap. The cap *withholds*: delivery stops, nothing is said, and
-- midnight releases it. The allowance *ends the period*: the subscriber is
-- told what happened and given the one tap that undoes it — see
-- `claim_plan_notices`, whose `spent` stage exists for this — and the
-- dashboard can show 5/30 beside 55/200 and 350/900 as the same kind of fact.
-- 0057 drew that distinction when it introduced the allowance; the trial was
-- simply never moved across.
--
-- No ops alert for it. `costly_whatsapp` raises the allowance alert for
-- `priced` plans only, and the trial is free — a trial ending is the product
-- working, and there are as many of those a week as there are signups. See
-- 0067.
--
-- ── CREATE OR REPLACE, so the dependents live ─────────────────────────────
--
-- 0063 had to drop and recreate this view, and `queued_notifications` with
-- it, because it renamed output columns and that is the one thing REPLACE
-- cannot do. Nothing is renamed here — same columns, same order, same types,
-- different expressions behind three of them — so the dependent view is left
-- alone.

BEGIN;

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
       -- The allowance this account actually has, which is the plan's only
       -- where messages are billed. NULL means unmetered, and on Telegram it
       -- always is. See the header.
       cap.allowance AS message_allowance,

       -- Every WhatsApp message in this period, whoever caused it: the
       -- listings, the digest, the check-in, the notices and the replies.
       -- Meta charges for all of them, so all of them are what the allowance
       -- is an allowance of.
       --
       -- Counted from `wa_messages` rather than kept as a counter column, for
       -- the reason 0055 gives: a number maintained by a worker is only as
       -- fresh as the worker, and this one decides whether somebody is
       -- served.
       coalesce(used.sent, 0) AS messages_used,
       CASE WHEN cap.allowance IS NULL THEN NULL
            ELSE greatest(cap.allowance - coalesce(used.sent, 0), 0)
       END AS messages_left,

       -- Out of days, out of messages, or neither.
       (u.plan_until IS NOT NULL AND u.plan_until <= now()) AS out_of_days,
       (
           cap.allowance IS NOT NULL
           AND coalesce(used.sent, 0) >= cap.allowance
       ) AS out_of_messages,

       (u.plan_until IS NULL OR u.plan_until > now())
       AND (
           cap.allowance IS NULL
           OR coalesce(used.sent, 0) < cap.allowance
       ) AS live,

       -- The share that follows from all of the above: the plan's own while
       -- it is live, and once it is not —
       --
       --   * nothing at all on WhatsApp. The lapsed tier is a fifth of what
       --     matches, and on a channel Meta bills per message that is a
       --     standing bill for somebody who has stopped paying. The free tier
       --     exists to show what the service does; it cannot be shown at our
       --     expense for ever.
       --   * the lapsed tier's share on Telegram, where delivery is free and
       --     a fifth of the listings is the best argument for the other four.
       CASE
           WHEN (u.plan_until IS NULL OR u.plan_until > now())
                AND (cap.allowance IS NULL
                  OR coalesce(used.sent, 0) < cap.allowance)
               THEN p.delivery_share
           WHEN uc.channel = 'whatsapp' THEN 0
           ELSE coalesce(lapsed.delivery_share, 0)
       END AS delivery_share,

       -- What the alerts fall back to when this plan ends, whether or not it
       -- has ended yet. The same rule as the last two branches above; what
       -- differs is that it does not ask whether the plan is still live. See
       -- 0058 for the notice this exists for.
       CASE
           WHEN uc.channel = 'whatsapp' THEN 0
           ELSE coalesce(lapsed.delivery_share, 0)
       END AS lapsed_share
  FROM users u
  JOIN plans p ON p.key = u.plan
  -- Which messenger they are on. It decides what a finished plan falls back
  -- to, and now also whether there is an allowance at all. Exactly one
  -- channel per account is primary, so this does not multiply the rows.
  LEFT JOIN user_channels uc ON uc.user_id = u.id AND uc.is_primary
  LEFT JOIN plan_settings ps ON ps.id
  LEFT JOIN plans lapsed ON lapsed.key = ps.lapsed_plan AND lapsed.enabled
  -- The gate, named once. Written out at each of the five places that ask
  -- would be five chances for one of them to drift into metering Telegram.
  --
  -- An account with no primary channel yet reads NULL here, which is
  -- unmetered — the same answer it got before this file, since nothing has
  -- been sent to somebody with nowhere to send it.
  CROSS JOIN LATERAL (
      SELECT CASE WHEN uc.channel = 'whatsapp' THEN p.message_allowance END
               AS allowance
  ) AS cap
  LEFT JOIN LATERAL (
      SELECT count(*) AS sent
        FROM wa_messages w
       WHERE w.user_id = u.id
         AND (u.plan_from IS NULL OR w.sent_at >= u.plan_from)
  ) AS used ON cap.allowance IS NOT NULL;

COMMENT ON COLUMN plans.message_allowance IS
    'How many WhatsApp messages this plan includes in one period, counting '
    'every message we or the subscriber cause. Read only for an account whose '
    'primary channel is WhatsApp: a message allowance is a property of the '
    'channel that charges per message, and user_entitlement returns NULL — '
    'unmetered — for every other one, whatever this says. NULL here means '
    'unmetered on WhatsApp too. Reaching it ends the period like running out '
    'of days does.';

-- Thirty, which is what the daily cap already allowed a one-day trial. See
-- the header for why the allowance is the better of the two bounds.
UPDATE plans SET message_allowance = 30 WHERE key = 'trial';

COMMIT;
